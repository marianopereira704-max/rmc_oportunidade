"""Onde a rotina do Pedido guarda o que coletou: Spaces (produção) ou uma
pasta local (teste), sempre por CHAVE — o mesmo caminho nos dois.

Não reaproveita `storage/filesystem.py` de propósito: aquele é o Explorador
de Arquivos, preso ao banco (FSNode) e sem listar/conferir existência — e a
retomada da coleta depende justamente de "esse arquivo já existe?".

Tudo fica sob `settings.pedido.prefixo` ("pedido/…"). O bucket é
compartilhado com outros sistemas: esta classe só escreve e lê; não apaga.
"""
from __future__ import annotations

import functools
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd


class Armazenamento:
    def salvar(self, chave: str, conteudo: bytes) -> None:
        raise NotImplementedError

    def ler(self, chave: str) -> bytes:
        raise NotImplementedError

    def existe(self, chave: str) -> bool:
        raise NotImplementedError

    def listar(self, prefixo: str) -> list[str]:
        """Todas as chaves que começam com `prefixo`, em ordem."""
        raise NotImplementedError

    def apagar(self, chave: str) -> None:
        """Apaga UMA chave (27/10/2026: só a retenção de 13 meses do histórico de estoque usa, com a chave montada
        e conferida por ela — o bucket é compartilhado com outros sistemas, nunca apagar por padrão de nome)."""
        raise NotImplementedError

    # -- formatos -------------------------------------------------------------

    def salvar_df(self, chave: str, df: pd.DataFrame, linhas_por_grupo: int | None = None) -> None:
        """`linhas_por_grupo`: blocos menores deixam quem lê com filtro pular
        os blocos que não interessam (pedido/rede.py)."""
        buffer = io.BytesIO()
        extra = {"row_group_size": linhas_por_grupo} if linhas_por_grupo else {}
        df.to_parquet(buffer, index=False, compression="zstd", **extra)
        self.salvar(chave, buffer.getvalue())

    def ler_df(self, chave: str, colunas: list[str] | None = None, filtros: list | None = None) -> pd.DataFrame:
        """`colunas`/`filtros`: lidos do arquivo só o que interessa (ex.: a
        Análise lê das compras da rede só os EANs do laboratório escolhido)."""
        return pd.read_parquet(io.BytesIO(self.ler(chave)), columns=colunas, filters=filtros)

    def salvar_json(self, chave: str, dado: Any) -> None:
        self.salvar(chave, json.dumps(dado, ensure_ascii=False, indent=1, default=str).encode("utf-8"))

    def ler_json(self, chave: str) -> Any:
        return json.loads(self.ler(chave).decode("utf-8"))

    def ler_varios(self, chaves: list[str], ler=None, simultaneas: int = 16) -> list:
        """Lê muitos arquivos AO MESMO TEMPO, na ordem de `chaves` (`ler` =
        `self.ler_df`/`self.ler_json`; padrão: bytes). Um arquivo por empresa
        (catálogos, lojas do GPS) lido em sequência custa ~140 ms cada até
        NY: 388 empresas ≈ 1 min — em 16 de cada vez, ~4 s (07/10/2026)."""
        ler = ler or self.ler
        if len(chaves) <= 1:
            return [ler(c) for c in chaves]
        with ThreadPoolExecutor(max_workers=min(simultaneas, len(chaves))) as executor:
            return list(executor.map(ler, chaves))


class ArmazenamentoLocal(Armazenamento):
    """Pasta no disco — testes e execução manual sem tocar no bucket real."""

    def __init__(self, raiz: str | Path) -> None:
        self.raiz = Path(raiz).resolve()

    def _caminho(self, chave: str) -> Path:
        caminho = self.raiz / Path(*chave.split("/"))
        # Windows limita o caminho a 260 caracteres, e a chave de um dia
        # ("pedido/bruto/<empresa>/<CNPJ>/compras/2026-09/2026-09-01.parquet")
        # já tem ~95 — dentro de uma pasta funda, estourava com
        # FileNotFoundError (visto no teste real de 27/09/2026). O prefixo
        # \\?\ libera caminhos longos.
        if os.name == "nt" and not str(caminho).startswith("\\\\?\\"):
            caminho = Path("\\\\?\\" + str(caminho))
        return caminho

    def salvar(self, chave: str, conteudo: bytes) -> None:
        caminho = self._caminho(chave)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        # Grava num temporário e troca: um arquivo pela metade nunca fica com
        # o nome final (e a retomada acharia que aquela unidade terminou).
        temporario = caminho.with_suffix(caminho.suffix + ".tmp")
        temporario.write_bytes(conteudo)
        temporario.replace(caminho)

    def ler(self, chave: str) -> bytes:
        return self._caminho(chave).read_bytes()

    def existe(self, chave: str) -> bool:
        return self._caminho(chave).is_file()

    def listar(self, prefixo: str) -> list[str]:
        base = self._caminho("")  # já com o prefixo de caminho longo no Windows
        if not base.exists():
            return []
        chaves = [p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.name.endswith(".tmp")]
        return sorted(c for c in chaves if c.startswith(prefixo))

    def apagar(self, chave: str) -> None:
        self._caminho(chave).unlink(missing_ok=True)


class ArmazenamentoSpaces(Armazenamento):
    """DigitalOcean Spaces (API S3). O `put_object` é atômico: ou a chave
    passa a existir com o conteúdo inteiro, ou não existe."""

    def __init__(self, endpoint: str, regiao: str, bucket: str, chave_acesso: str, chave_secreta: str) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.cliente = boto3.client(
            "s3", endpoint_url=endpoint, region_name=regiao,
            aws_access_key_id=chave_acesso, aws_secret_access_key=chave_secreta,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=20, read_timeout=120),
        )

    def salvar(self, chave: str, conteudo: bytes) -> None:
        self.cliente.put_object(Bucket=self.bucket, Key=chave, Body=conteudo)

    def ler(self, chave: str) -> bytes:
        return self.cliente.get_object(Bucket=self.bucket, Key=chave)["Body"].read()

    def existe(self, chave: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.cliente.head_object(Bucket=self.bucket, Key=chave)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def listar(self, prefixo: str) -> list[str]:
        chaves: list[str] = []
        for pagina in self.cliente.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefixo):
            chaves.extend(o["Key"] for o in pagina.get("Contents", []))
        return sorted(chaves)

    def apagar(self, chave: str) -> None:
        self.cliente.delete_object(Bucket=self.bucket, Key=chave)


@functools.lru_cache(maxsize=4)
def _spaces(endpoint: str, regiao: str, bucket: str, chave_acesso: str, chave_secreta: str) -> "ArmazenamentoSpaces":
    return ArmazenamentoSpaces(endpoint, regiao, bucket, chave_acesso, chave_secreta)


def do_ambiente() -> Armazenamento:
    """Spaces se estiver configurado; senão, a pasta local de dev.

    O cliente do Spaces é UM por processo (07/10/2026): montar um boto3 novo
    a cada chamada custava ~440 ms, mais uma conexão TLS nova até NY na 1ª
    consulta — medido: cliente novo + consulta ~720 ms; o mesmo cliente
    reaproveitado, 136 ms. O app chama isto em várias telas a cada clique.
    Cliente boto3 é thread-safe (sessões do Streamlit podem dividir); a
    chave do cache são as credenciais, então trocar o secrets troca o cliente."""
    from core.config import settings

    s = settings.spaces
    if s.configured:
        return _spaces(s.endpoint_url, s.region, s.bucket, s.access_key, s.secret_key)
    return ArmazenamentoLocal(settings.local_storage_dir)
