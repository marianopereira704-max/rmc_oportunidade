"""Correções de estoque negativo e o registro das exportações do Assistente
de pedido — cada gravação leva o usuário e a data/hora (decisão de
27/09/2026).

O nome do módulo é da Fase 5, quando ele guardava também o "rascunho da
loja" (quantidades digitadas). O rascunho foi substituído pela área de
trabalho por usuário + loja (integrations/pedido_area.py) em 29/09/2026 e
as funções dele foram apagadas em 02/10/2026; a tabela `rascunhos_pedido`
fica no banco sem uso (migração não apaga tabela).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.models import CorrecaoEstoque, ExportacaoPedido
from core.sql import insert_com_atualizacao


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


# ---------------------------------------------------------------------------
# Exportações
# ---------------------------------------------------------------------------

def registrar_exportacao(session: Session, loja_id: int, formato: str, itens: int, unidades: int, valor: float,
                         usuario: str) -> None:
    session.add(ExportacaoPedido(loja_id=loja_id, formato=formato, itens=itens, unidades=unidades,
                                 valor=round(float(valor), 2), exportado_por=usuario,
                                 exportado_em=dt.datetime.utcnow()))
