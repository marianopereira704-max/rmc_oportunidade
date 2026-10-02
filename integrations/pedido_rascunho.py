"""O que o usuário muda no Pedido de uma loja: quantidades (rascunho),
estoque negativo corrigido e o registro das exportações. Cada gravação leva
o usuário e a data/hora (decisão de 27/09/2026).

Uma consulta por ação (o banco está a ~138 ms daqui): ler o rascunho e as
correções da loja são 1 consulta cada; gravar é 1 upsert.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.models import CorrecaoEstoque, ExportacaoPedido, RascunhoPedido
from core.sql import insert_com_atualizacao


# ---------------------------------------------------------------------------
# Rascunho
# ---------------------------------------------------------------------------

@dataclass
class ItemRascunho:
    quantidade: int
    sugestao_calculada: int | None
    alterado_por: str
    alterado_em: dt.datetime


def rascunho(session: Session, loja_id: int) -> dict[str, ItemRascunho]:
    return {
        r.linha: ItemRascunho(r.quantidade, r.sugestao_calculada, r.alterado_por, r.alterado_em)
        for r in session.scalars(select(RascunhoPedido).where(RascunhoPedido.loja_id == loja_id))
    }


def alterar_quantidade(session: Session, loja_id: int, linha: str, quantidade: int, sugestao: int | None,
                       usuario: str) -> None:
    """Quantidade igual à sugestão calculada = volta a seguir a sugestão (sai
    do rascunho): assim "desfazer" é só digitar o número sugerido de novo."""
    if quantidade < 0:
        raise ValueError("A quantidade não pode ser negativa.")
    if sugestao is not None and quantidade == sugestao:
        session.execute(delete(RascunhoPedido).where(RascunhoPedido.loja_id == loja_id, RascunhoPedido.linha == linha))
        return
    tabela = RascunhoPedido.__table__
    session.execute(
        insert_com_atualizacao(session, tabela, ["loja_id", "linha"],
                               ["quantidade", "sugestao_calculada", "alterado_por", "alterado_em"]),
        [{"loja_id": loja_id, "linha": linha, "quantidade": int(quantidade), "sugestao_calculada": sugestao,
          "alterado_por": usuario, "alterado_em": dt.datetime.utcnow()}],
    )


def descartar_rascunho(session: Session, loja_id: int) -> int:
    return session.execute(delete(RascunhoPedido).where(RascunhoPedido.loja_id == loja_id)).rowcount or 0


# ---------------------------------------------------------------------------
# Estoque negativo
# ---------------------------------------------------------------------------

@dataclass
class Correcao:
    estoque: float
    estoque_gps: float | None
    data_foto: str | None
    corrigido_por: str
    corrigido_em: dt.datetime


def correcoes(session: Session, loja_id: int) -> dict[str, Correcao]:
    return {
        c.linha: Correcao(float(c.estoque_corrigido), None if c.estoque_gps is None else float(c.estoque_gps),
                          c.data_foto, c.corrigido_por, c.corrigido_em)
        for c in session.scalars(select(CorrecaoEstoque).where(CorrecaoEstoque.loja_id == loja_id))
    }


def salvar_correcoes(session: Session, loja_id: int, valores: dict[str, float | None],
                     estoque_gps: dict[str, float], data_foto: str | None, usuario: str) -> tuple[int, int]:
    """`valores`: linha → estoque correto (None = tirar a correção).
    Devolve (gravadas, removidas)."""
    gravar = [
        {"loja_id": loja_id, "linha": linha, "estoque_corrigido": float(v), "estoque_gps": estoque_gps.get(linha),
         "data_foto": data_foto, "corrigido_por": usuario, "corrigido_em": dt.datetime.utcnow()}
        for linha, v in valores.items() if v is not None
    ]
    if any(g["estoque_corrigido"] < 0 for g in gravar):
        raise ValueError("O estoque corrigido não pode ser negativo.")
    remover = [linha for linha, v in valores.items() if v is None]
    if gravar:
        session.execute(
            insert_com_atualizacao(session, CorrecaoEstoque.__table__, ["loja_id", "linha"],
                                   ["estoque_corrigido", "estoque_gps", "data_foto", "corrigido_por", "corrigido_em"]),
            gravar,
        )
    removidas = 0
    if remover:
        removidas = session.execute(delete(CorrecaoEstoque).where(
            CorrecaoEstoque.loja_id == loja_id, CorrecaoEstoque.linha.in_(remover))).rowcount or 0
    return len(gravar), removidas


def versao_correcoes(session: Session, loja_id: int) -> tuple:
    """Entra na chave do cache do cálculo da loja."""
    total, ultima = session.execute(
        select(func.count(), func.max(CorrecaoEstoque.corrigido_em)).where(CorrecaoEstoque.loja_id == loja_id)
    ).one()
    return int(total or 0), str(ultima or "")


# ---------------------------------------------------------------------------
# Exportações
# ---------------------------------------------------------------------------

def registrar_exportacao(session: Session, loja_id: int, formato: str, itens: int, unidades: int, valor: float,
                         usuario: str) -> None:
    session.add(ExportacaoPedido(loja_id=loja_id, formato=formato, itens=itens, unidades=unidades,
                                 valor=round(float(valor), 2), exportado_por=usuario,
                                 exportado_em=dt.datetime.utcnow()))


def ultima_exportacao(session: Session, loja_id: int) -> ExportacaoPedido | None:
    e = session.scalars(select(ExportacaoPedido).where(ExportacaoPedido.loja_id == loja_id)
                        .order_by(ExportacaoPedido.id.desc()).limit(1)).first()
    if e is not None:
        session.expunge(e)
    return e
