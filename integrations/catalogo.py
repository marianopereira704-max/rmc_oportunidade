"""Catálogo IQVIA no banco (`catalogo_iqvia`, 07/10/2026 — etapa 5 do login
individual). Entra como a base inicial de categorias: `python -m
pedido.carga_catalogo` monta o arquivo e guarda no Spaces; o botão em
Dados → Rotinas carrega no banco em que o app roda.

Duas coisas na mesma carga:
- EAN → SETOR_NEC/GMRS (a linha de compra do Comprador — pedido/linhas.py);
- Q38: EAN com GMRS = G que a base de categorias NÃO conhece entra nela
  como "Prescrição genérico", origem IQVIA. Só preenche vazio: categoria
  que já existe (FEBRAFAR, CMED, MANUAL) nunca muda — o catálogo
  complementa, não substitui (Q37).
Nada se apaga: carregar de novo atualiza o setor de quem já estava.
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.models import CatalogoIqvia, CategoriaEan, OrigemCategoria
from core.sql import _insert_do_dialeto
from pedido import categorias as cat
from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves

_LOTE_SQLITE = 5_000


@dataclass
class ResultadoCarga:
    eans: int
    genericos_novos: int      # EANs que entraram na base de categorias como genérico (Q38)
    segundos: float


def carregar(session: Session, catalogo: pd.DataFrame, usuario: str) -> ResultadoCarga:
    """Grava o catálogo (`pedido.linhas.montar_catalogo`). No Postgres, COPY
    numa tabela temporária + um INSERT…SELECT: são 253 mil linhas, e em
    lotes seriam centenas de idas e voltas até NY (mesma razão da carga de
    categorias)."""
    comeco = dt.datetime.now()
    agora = dt.datetime.utcnow()
    dados = catalogo[["ean", "setor_nec", "gmrs"]].drop_duplicates("ean")
    if session.get_bind().dialect.name == "postgresql":
        _copiar_postgres(session, dados, agora)
    else:
        tabela = CatalogoIqvia.__table__
        registros = dados.assign(atualizado_em=agora).astype(object)
        registros = registros.where(registros.notna(), None).to_dict("records")
        for i in range(0, len(registros), _LOTE_SQLITE):
            stmt = _insert_do_dialeto(session, tabela)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ean"],
                set_={c: getattr(stmt.excluded, c) for c in ("setor_nec", "gmrs", "atualizado_em")},
            )
            session.execute(stmt, registros[i:i + _LOTE_SQLITE])
    novos = _genericos_novos(session, usuario, agora)
    session.flush()
    return ResultadoCarga(len(dados), novos, (dt.datetime.now() - comeco).total_seconds())


def _copiar_postgres(session: Session, dados: pd.DataFrame, agora: dt.datetime) -> None:
    buffer = io.StringIO()
    dados.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    conexao = session.connection()
    conexao.execute(text("CREATE TEMP TABLE _carga_catalogo (ean varchar(20), setor_nec varchar(40), "
                         "gmrs varchar(2)) ON COMMIT DROP"))
    cursor = conexao.connection.dbapi_connection.cursor()
    try:
        cursor.copy_expert("COPY _carga_catalogo FROM STDIN WITH (FORMAT csv)", buffer)
    finally:
        cursor.close()
    conexao.execute(text(
        "INSERT INTO catalogo_iqvia (ean, setor_nec, gmrs, atualizado_em) "
        "SELECT ean, setor_nec, NULLIF(gmrs, ''), :agora FROM _carga_catalogo "
        "ON CONFLICT (ean) DO UPDATE SET setor_nec = EXCLUDED.setor_nec, gmrs = EXCLUDED.gmrs, "
        "atualizado_em = EXCLUDED.atualizado_em"
    ), {"agora": agora})
    conexao.execute(text("DROP TABLE IF EXISTS _carga_catalogo"))


def _genericos_novos(session: Session, usuario: str, agora: dt.datetime) -> int:
    """Q38, num comando só nos dois bancos (o `WHERE` antes do ON CONFLICT é
    exigência do SQLite pra INSERT…SELECT). `IQVIA` é o NOME do enum — é o
    que o SQLAlchemy grava."""
    cast = "CAST('IQVIA' AS origemcategoria)" if session.get_bind().dialect.name == "postgresql" else "'IQVIA'"
    resultado = session.execute(text(
        "INSERT INTO categorias_ean (ean, categoria, origem, descricao, atualizado_por, atualizado_em) "
        f"SELECT ean, :categoria, {cast}, NULL, :usuario, :agora FROM catalogo_iqvia "
        "WHERE gmrs = 'G' ON CONFLICT (ean) DO NOTHING"
    ), {"categoria": cat.GENERICO, "usuario": usuario, "agora": agora})
    return max(int(resultado.rowcount or 0), 0)


def disponivel(armaz: Armazenamento, chaves: Chaves) -> dict | None:
    """Metadados do arquivo no Spaces, ou None se ainda não foi gerado."""
    if not armaz.existe(chaves.catalogo_iqvia()):
        return None
    try:
        return armaz.ler_json(chaves.catalogo_iqvia_info())
    except Exception:  # noqa: BLE001
        return {}


def versao(session: Session) -> tuple[int, str]:
    total, ultima = session.execute(select(func.count(), func.max(CatalogoIqvia.atualizado_em))).one()
    return int(total or 0), str(ultima or "")


def setores(session: Session) -> pd.Series:
    """EAN → SETOR_NEC do catálogo inteiro (uma consulta; o Assistente guarda
    em cache pela `versao`). Setor como categoria do pandas: 10 valores em
    253 mil linhas."""
    linhas = session.execute(select(CatalogoIqvia.ean, CatalogoIqvia.setor_nec)).all()
    df = pd.DataFrame(linhas, columns=["ean", "setor_nec"])
    return pd.Series(df["setor_nec"].astype("category").to_numpy(), index=df["ean"].to_numpy(), name="setor_nec")


def contagem_por_setor(session: Session) -> dict[str, int]:
    return dict(session.execute(select(CatalogoIqvia.setor_nec, func.count()).group_by(CatalogoIqvia.setor_nec)).all())


def categorias_iqvia(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(CategoriaEan)
                              .where(CategoriaEan.origem == OrigemCategoria.IQVIA)) or 0)
