"""'Explorador de Arquivos' virtual sobre o DigitalOcean Spaces.

Inspirado no sistema de pastas do Windows, mas com uma regra fixa: nada é
excluído de verdade. "Inativar" move o item para dentro de uma pasta especial
(_Inativos) e marca status=inativo; "Reativar" devolve pro lugar original.

Os metadados (pastas, nomes, quem criou, status) ficam sempre no banco
(FSNode). O conteúdo binário dos arquivos fica no DigitalOcean Spaces via
boto3 quando as credenciais estiverem configuradas; enquanto isso, cai para
um fallback em disco local (data/local_storage) — a árvore de pastas e as
regras de mover/inativar são idênticas nos dois casos, só troca onde o byte
do arquivo é gravado.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import FSNode, StatusNode, TipoNode

_PASTA_INATIVOS = "_Inativos"


# ---------------------------------------------------------------------------
# Backend de bytes: DigitalOcean Spaces (S3) ou disco local
# ---------------------------------------------------------------------------

class _BackendArmazenamento:
    def salvar(self, storage_key: str, conteudo: bytes) -> None:
        raise NotImplementedError

    def ler(self, storage_key: str) -> bytes:
        raise NotImplementedError

    def excluir_fisicamente(self, storage_key: str) -> None:
        """Só usado internamente se algum dia for preciso purgar de fato —
        NÃO é chamado pelo fluxo normal de inativação."""
        raise NotImplementedError

    def url_assinada(self, storage_key: str, expira_em: int = 300) -> str | None:
        """URL temporária pra download direto do backend, sem o conteúdo
        passar pela memória do processo Streamlit. `None` quando o backend
        não suporta (disco local não tem endpoint HTTP) — quem chama trata
        isso como "sem URL disponível, cai pro fallback de leitura sob
        demanda", nunca como erro."""
        return None


class _BackendLocal(_BackendArmazenamento):
    def __init__(self) -> None:
        self.base_dir = settings.local_storage_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str):
        return self.base_dir / storage_key

    def salvar(self, storage_key: str, conteudo: bytes) -> None:
        caminho = self._path(storage_key)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)

    def ler(self, storage_key: str) -> bytes:
        return self._path(storage_key).read_bytes()

    def excluir_fisicamente(self, storage_key: str) -> None:
        caminho = self._path(storage_key)
        if caminho.exists():
            caminho.unlink()


class _BackendDigitalOceanSpaces(_BackendArmazenamento):
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.spaces.bucket
        self.client = boto3.client(
            "s3",
            region_name=settings.spaces.region,
            endpoint_url=settings.spaces.endpoint_url,
            aws_access_key_id=settings.spaces.access_key,
            aws_secret_access_key=settings.spaces.secret_key,
        )

    def salvar(self, storage_key: str, conteudo: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=storage_key, Body=conteudo)

    def ler(self, storage_key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        return obj["Body"].read()

    def excluir_fisicamente(self, storage_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

    def url_assinada(self, storage_key: str, expira_em: int = 300) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=expira_em,
        )


_backend_instancia: _BackendArmazenamento | None = None


def backend() -> _BackendArmazenamento:
    """Singleton de módulo: antes, cada chamada criava um `_BackendDigitalOceanSpaces`
    novo (e portanto um client boto3 novo) — no Explorador de Arquivos isso
    significava recriar o client uma vez por ARQUIVO LISTADO, a cada rerun.
    Client boto3 é thread-safe e caro de montar, então cachear no processo
    (não em session_state — várias sessões Streamlit compartilham o mesmo
    processo/worker) é seguro e é o que se espera aqui: a config de storage
    (Spaces configurado ou não) não muda em runtime."""
    global _backend_instancia
    if _backend_instancia is None:
        _backend_instancia = _BackendDigitalOceanSpaces() if settings.spaces.configured else _BackendLocal()
    return _backend_instancia


def modo_storage() -> str:
    return "DigitalOcean Spaces" if settings.spaces.configured else "Local (dev) — configure o DigitalOcean Spaces em produção"


# ---------------------------------------------------------------------------
# Árvore de pastas (metadados)
# ---------------------------------------------------------------------------

def garantir_raiz(session: Session) -> FSNode:
    raiz = session.execute(
        select(FSNode).where(FSNode.parent_id.is_(None), FSNode.nome == "Raiz")
    ).scalar_one_or_none()
    if raiz is None:
        raiz = FSNode(parent_id=None, nome="Raiz", tipo=TipoNode.PASTA, status=StatusNode.ATIVO)
        session.add(raiz)
        session.flush()
    return raiz


def garantir_pasta_inativos(session: Session) -> FSNode:
    raiz = garantir_raiz(session)
    pasta = session.execute(
        select(FSNode).where(FSNode.parent_id == raiz.id, FSNode.nome == _PASTA_INATIVOS)
    ).scalar_one_or_none()
    if pasta is None:
        pasta = FSNode(parent_id=raiz.id, nome=_PASTA_INATIVOS, tipo=TipoNode.PASTA, status=StatusNode.ATIVO)
        session.add(pasta)
        session.flush()
    return pasta


def obter_ou_criar_subpasta(session: Session, parent_id: int, nome: str, criado_por: str) -> FSNode:
    """Idempotente: usada pra arquivamento automático (ex: Compras/GPS/2026-07)
    onde a mesma pasta pode ser 'necessária' em vários uploads seguidos —
    ao contrário de `criar_pasta`, que sempre cria (e duplicaria nesse caso)."""
    existente = session.execute(
        select(FSNode).where(
            FSNode.parent_id == parent_id, FSNode.nome == nome, FSNode.status == StatusNode.ATIVO
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente
    return criar_pasta(session, parent_id, nome, criado_por)


def listar_conteudo(session: Session, parent_id: int, incluir_inativos: bool = False) -> list[FSNode]:
    stmt = select(FSNode).where(FSNode.parent_id == parent_id)
    if not incluir_inativos:
        stmt = stmt.where(FSNode.status == StatusNode.ATIVO)
    stmt = stmt.order_by(FSNode.tipo.desc(), FSNode.nome)  # pastas antes de arquivos
    return list(session.execute(stmt).scalars().all())


def caminho_completo(session: Session, node: FSNode) -> list[FSNode]:
    """Breadcrumb da raiz até o node (inclusive)."""
    caminho = [node]
    atual = node
    while atual.parent_id is not None:
        atual = session.get(FSNode, atual.parent_id)
        if atual is None:
            break
        caminho.append(atual)
    return list(reversed(caminho))


def listar_todas_pastas(session: Session, incluir_inativos: bool = False) -> list[FSNode]:
    stmt = select(FSNode).where(FSNode.tipo == TipoNode.PASTA)
    if not incluir_inativos:
        stmt = stmt.where(FSNode.status == StatusNode.ATIVO)
    stmt = stmt.order_by(FSNode.nome)
    return list(session.execute(stmt).scalars().all())


def caminho_texto(session: Session, node: FSNode) -> str:
    return " / ".join(n.nome for n in caminho_completo(session, node))


def criar_pasta(session: Session, parent_id: int, nome: str, criado_por: str) -> FSNode:
    pasta = FSNode(
        parent_id=parent_id, nome=nome, tipo=TipoNode.PASTA, status=StatusNode.ATIVO, criado_por=criado_por
    )
    session.add(pasta)
    session.flush()
    return pasta


def salvar_arquivo(
    session: Session, parent_id: int, nome_arquivo: str, conteudo: bytes, criado_por: str, mime_type: str | None = None
) -> FSNode:
    storage_key = f"{uuid.uuid4().hex}_{nome_arquivo}"
    backend().salvar(storage_key, conteudo)
    node = FSNode(
        parent_id=parent_id,
        nome=nome_arquivo,
        tipo=TipoNode.ARQUIVO,
        status=StatusNode.ATIVO,
        storage_key=storage_key,
        tamanho_bytes=len(conteudo),
        mime_type=mime_type,
        criado_por=criado_por,
    )
    session.add(node)
    session.flush()
    return node


def ler_arquivo(node: FSNode) -> bytes:
    if node.tipo != TipoNode.ARQUIVO or not node.storage_key:
        raise ValueError("Node não é um arquivo com conteúdo.")
    return backend().ler(node.storage_key)


def mover(session: Session, node_id: int, novo_parent_id: int) -> None:
    node = session.get(FSNode, node_id)
    if node is None:
        raise ValueError("Item não encontrado.")
    node.parent_id = novo_parent_id
    node.atualizado_em = dt.datetime.utcnow()


def renomear(session: Session, node_id: int, novo_nome: str) -> None:
    node = session.get(FSNode, node_id)
    if node is None:
        raise ValueError("Item não encontrado.")
    node.nome = novo_nome
    node.atualizado_em = dt.datetime.utcnow()


def inativar(session: Session, node_id: int) -> None:
    """Nunca exclui. Move para _Inativos e marca status, guardando de onde
    veio para permitir reativar depois no lugar certo."""
    node = session.get(FSNode, node_id)
    if node is None:
        raise ValueError("Item não encontrado.")
    pasta_inativos = garantir_pasta_inativos(session)
    node.origem_parent_id = node.parent_id
    node.parent_id = pasta_inativos.id
    node.status = StatusNode.INATIVO
    node.atualizado_em = dt.datetime.utcnow()


def reativar(session: Session, node_id: int) -> None:
    node = session.get(FSNode, node_id)
    if node is None:
        raise ValueError("Item não encontrado.")
    destino = node.origem_parent_id or garantir_raiz(session).id
    node.parent_id = destino
    node.origem_parent_id = None
    node.status = StatusNode.ATIVO
    node.atualizado_em = dt.datetime.utcnow()
