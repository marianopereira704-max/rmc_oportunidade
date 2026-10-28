"""Classificação de produtos em cascata no banco (`classificacao_ean`, 25/10/2026 — design em
docs/specs/2026-10-25-classificacao-produtos-design.md).

Guarda o que a FEBRAFAR e a CMED dizem de cada EAN (categoria e tipo bruto). Junto com o catálogo IQVIA
(`catalogo_iqvia`) e as categorias MANUAIS de `categorias_ean`, forma o "universo" que `pedido.classificacao`
classifica. Nada se apaga: carregar de novo atualiza quem já estava.
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.models import CatalogoIqvia, CategoriaEan, ClassificacaoEan, OrigemCategoria
from core.sql import _insert_do_dialeto
from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves

_LOTE_SQLITE = 5_000
COLUNAS = ["ean", "febrafar", "cmed_tipo"]


@dataclass
class ResultadoCarga:
    eans: int
    com_febrafar: int
    com_cmed: int
    segundos: float


def carregar(session: Session, dados: pd.DataFrame) -> ResultadoCarga:
    """Grava `ean`, `febrafar` e `cmed_tipo` (um EAN por linha; vazio = a fonte não conhece). No Postgres, COPY numa
    tabela temporária e um INSERT…SELECT: são ~300 mil linhas, e em lotes seriam centenas de idas e voltas até NY (a
    mesma razão da carga de categorias e do catálogo IQVIA)."""
    comeco = dt.datetime.now()
    agora = dt.datetime.utcnow()
    d = dados[COLUNAS].drop_duplicates("ean")
    d = d[d["ean"].notna()]
    if session.get_bind().dialect.name == "postgresql":
        _copiar_postgres(session, d, agora)
    else:
        tabela = ClassificacaoEan.__table__
        registros = d.assign(atualizado_em=agora).astype(object)
        registros = registros.where(registros.notna(), None).to_dict("records")
        for i in range(0, len(registros), _LOTE_SQLITE):
            stmt = _insert_do_dialeto(session, tabela)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ean"],
                set_={c: getattr(stmt.excluded, c) for c in ("febrafar", "cmed_tipo", "atualizado_em")},
            )
            session.execute(stmt, registros[i:i + _LOTE_SQLITE])
    session.flush()
    return ResultadoCarga(len(d), int(d["febrafar"].notna().sum()), int(d["cmed_tipo"].notna().sum()),
                          (dt.datetime.now() - comeco).total_seconds())


def _copiar_postgres(session: Session, dados: pd.DataFrame, agora: dt.datetime) -> None:
    buffer = io.StringIO()
    dados.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    conexao = session.connection()
    conexao.execute(text("CREATE TEMP TABLE _carga_classificacao (ean varchar(20), febrafar varchar(60), "
                         "cmed_tipo varchar(60)) ON COMMIT DROP"))
    cursor = conexao.connection.dbapi_connection.cursor()
    try:
        cursor.copy_expert("COPY _carga_classificacao FROM STDIN WITH (FORMAT csv)", buffer)
    finally:
        cursor.close()
    conexao.execute(text(
        "INSERT INTO classificacao_ean (ean, febrafar, cmed_tipo, atualizado_em) "
        "SELECT ean, febrafar, cmed_tipo, :agora FROM _carga_classificacao "
        "ON CONFLICT (ean) DO UPDATE SET febrafar = EXCLUDED.febrafar, cmed_tipo = EXCLUDED.cmed_tipo, "
        "atualizado_em = EXCLUDED.atualizado_em"
    ), {"agora": agora})
    conexao.execute(text("DROP TABLE IF EXISTS _carga_classificacao"))


def disponivel(armaz: Armazenamento, chaves: Chaves) -> dict | None:
    """Metadados do arquivo no Spaces, ou None se ainda não foi gerado (`python -m pedido.carga_categorias
    --executar`)."""
    if not armaz.existe(chaves.classificacao_ean()):
        return None
    try:
        return armaz.ler_json(chaves.classificacao_ean_info())
    except Exception:  # noqa: BLE001
        return {}


def versao(session: Session) -> tuple[int, str]:
    """(total de EANs, última atualização): a chave de cache de quem lê a tabela."""
    total, ultima = session.execute(select(func.count(), func.max(ClassificacaoEan.atualizado_em))).one()
    return int(total or 0), str(ultima or "")


def contagem_por_cmed(session: Session) -> dict[str, int]:
    """Quantos EANs há por tipo da CMED (a prévia da carga mostra isto antes de gravar)."""
    return dict(session.execute(select(ClassificacaoEan.cmed_tipo, func.count())
                                .where(ClassificacaoEan.cmed_tipo.is_not(None))
                                .group_by(ClassificacaoEan.cmed_tipo)).all())


def universo(session: Session) -> pd.DataFrame:
    """Todos os EANs que alguma fonte conhece, com o que cada uma diz: `ean`, `setor_nec` (IQVIA), `febrafar`,
    `cmed_tipo` e `manual` (categoria da planilha manual do admin, em `categorias_ean`). É a entrada de
    `pedido.classificacao.classificar`. Três consultas (uma por fonte) e a junção no pandas: um FULL JOIN em SQL
    não existe no SQLite antigo, e a conta é de ~550 mil linhas."""
    iqvia = pd.DataFrame(session.execute(select(CatalogoIqvia.ean, CatalogoIqvia.setor_nec)).all(),
                         columns=["ean", "setor_nec"])
    fontes = pd.DataFrame(session.execute(select(ClassificacaoEan.ean, ClassificacaoEan.febrafar,
                                                 ClassificacaoEan.cmed_tipo)).all(),
                          columns=["ean", "febrafar", "cmed_tipo"])
    manual = pd.DataFrame(session.execute(select(CategoriaEan.ean, CategoriaEan.categoria)
                                          .where(CategoriaEan.origem == OrigemCategoria.MANUAL)).all(),
                          columns=["ean", "manual"])
    juntos = iqvia.merge(fontes, on="ean", how="outer").merge(manual, on="ean", how="outer")
    return juntos[["ean", "setor_nec", "febrafar", "cmed_tipo", "manual"]]
