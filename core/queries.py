"""Consultas de apoio das telas de Análise de Oportunidade: as listas dos
filtros (UF, atendentes, grupos, lojas) e a paginação. O cálculo da
oportunidade mora em core/analise_api.py (compras da API, 05/10/2026)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Loja, loja_da_rede


@dataclass
class PaginaResultado:
    linhas: list[dict]
    total_linhas: int
    pagina: int
    tamanho_pagina: int

    @property
    def total_paginas(self) -> int:
        if self.tamanho_pagina <= 0:
            return 1
        return max(1, -(-self.total_linhas // self.tamanho_pagina))


def _lista_distinct(session: Session, coluna) -> list[str]:
    """Valores distintos pra alimentar dropdown de filtro — exclui NULL e
    string vazia (não só NULL): uma fonte externa pode mandar "" em vez de
    omitir o campo pra "sem grupo/sem atendente", e isso nunca pode aparecer
    como opção fantasma no dropdown nem virar critério de agrupamento (duas
    lojas com grupo_economico="" não são "do mesmo grupo")."""
    linhas = session.execute(
        select(coluna).distinct().where(coluna.is_not(None), coluna != "", loja_da_rede()).order_by(coluna)
    ).all()
    return [r[0] for r in linhas]


def lista_ufs(session: Session) -> list[str]:
    return [r[0] for r in session.execute(select(Loja.uf).distinct().where(loja_da_rede()).order_by(Loja.uf)).all()]


def lista_atendentes(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.atendente_comercial)


def lista_grupos_economicos(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.grupo_economico)


def lista_lojas(session: Session) -> list[dict]:
    """Pra multi-seleção de loja em chips (Bloco 4) — id, razão social, CNPJ, UF,
    atendente e grupo (estes dois montam as opções dos filtros de quem só vê
    algumas lojas — 02/10/2026)."""
    stmt = select(Loja.id, Loja.razao_social, Loja.cnpj, Loja.uf, Loja.atendente_comercial,
                  Loja.grupo_economico).where(loja_da_rede()).order_by(Loja.razao_social)
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def rotulos_grupos(session: Session) -> dict[str, str]:
    """Sigla → "GE787 · DROGARIA NOVA NACIONAL LTDA" (Q30/Q34 de 06/10/2026:
    a sigla sozinha não diz quem é o grupo). A sigla é "GE" + o código da
    loja principal do grupo — é o nome dela que acompanha; se essa loja não
    estiver na base, o da loja de menor código do grupo."""
    linhas = session.execute(
        select(Loja.grupo_economico, Loja.legacy_id, Loja.razao_social)
        .where(Loja.grupo_economico.is_not(None), Loja.grupo_economico != "", loja_da_rede())
        .order_by(Loja.grupo_economico, Loja.legacy_id)
    ).all()
    saida: dict[str, str] = {}
    for sigla, codigo, razao in linhas:
        nome = (razao or "").strip()
        if sigla not in saida or sigla == f"GE{codigo}":
            saida[sigla] = f"{sigla} · {nome}" if nome else sigla
    return saida
