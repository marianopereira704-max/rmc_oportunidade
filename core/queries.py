"""Consultas de apoio das telas de Análise de Oportunidade: meses carregados,
histórico mensal de um genérico numa loja, preços por laboratório e listas
dos filtros. O cálculo da oportunidade em si (menor preço pago, recuo de
preço, vencedor, economia) mora em core/analise.py."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)


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


def listar_ultimos_ano_meses(session: Session, quantidade: int) -> list[str]:
    """Os `quantidade` ano_mes DISTINTOS mais recentes já carregados via GPS
    — "1 mês" é o mês calendário mais recente com GPS carregado, não uma
    janela de 30 dias corridos; "6 meses" são os 6 ano_mes mais recentes que
    existirem, mesmo que não sejam contíguos."""
    linhas = session.execute(
        select(RegistroCompraGPS.ano_mes)
        .distinct()
        .order_by(RegistroCompraGPS.ano_mes.desc())
        .limit(max(1, quantidade))
    ).all()
    return [r[0] for r in linhas]


def historico_compras(
    session: Session, loja_id: int, base_generico_id: int, ano_mes: str | None = None
) -> list[dict]:
    """Histórico mês a mês (todos os EAN do genérico já consolidados) de uma
    loja pra um genérico específico — usado no gráfico de linha do popup de
    Detalhes. ano_mes=None devolve todos os meses disponíveis."""
    peso = RegistroCompraGPS.custo_unitario * RegistroCompraGPS.quantidade
    stmt = (
        select(
            RegistroCompraGPS.ano_mes,
            func.sum(RegistroCompraGPS.quantidade).label("quantidade"),
            # Mesma guarda de _compras_periodo_subquery (ver comentário lá).
            (func.sum(peso) / func.nullif(func.sum(RegistroCompraGPS.quantidade), 0)).label("custo_medio_ponderado"),
            func.sum(peso).label("valor_total"),
        )
        .select_from(RegistroCompraGPS)
        .join(EanGenerico, EanGenerico.ean == RegistroCompraGPS.ean)
        .where(RegistroCompraGPS.loja_id == loja_id, EanGenerico.base_generico_id == base_generico_id)
        .group_by(RegistroCompraGPS.ano_mes)
        .order_by(RegistroCompraGPS.ano_mes)
    )
    if ano_mes:
        stmt = stmt.where(RegistroCompraGPS.ano_mes == ano_mes)
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def precos_por_laboratorio(session: Session, uf: str, base_generico_ids: list[int]) -> dict[tuple[str, int], float]:
    """Preço vigente de cada laboratório pra um conjunto pequeno de
    genéricos — pensado pra ser calculado só pra página atual (os
    `base_generico_ids` visíveis na tela), nunca pro dataset inteiro."""
    if not base_generico_ids:
        return {}
    stmt = (
        select(
            TabelaGruppy.laboratorio,
            EanGenerico.base_generico_id,
            func.min(ItemTabelaGruppy.custo_liquido).label("preco"),
        )
        .select_from(ItemTabelaGruppy)
        .join(EanGenerico, EanGenerico.ean == ItemTabelaGruppy.ean)
        .join(TabelaGruppy, TabelaGruppy.id == ItemTabelaGruppy.tabela_gruppy_id)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(
            TabelaGruppyCobertura.status == StatusCobertura.ATIVA,
            TabelaGruppyCobertura.uf == uf,
            EanGenerico.base_generico_id.in_(base_generico_ids),
        )
        .group_by(TabelaGruppy.laboratorio, EanGenerico.base_generico_id)
    )
    return {(lab, bg_id): float(preco) for lab, bg_id, preco in session.execute(stmt).all()}


def _lista_distinct(session: Session, coluna) -> list[str]:
    """Valores distintos pra alimentar dropdown de filtro — exclui NULL e
    string vazia (não só NULL): uma fonte externa pode mandar "" em vez de
    omitir o campo pra "sem grupo/sem atendente", e isso nunca pode aparecer
    como opção fantasma no dropdown nem virar critério de agrupamento (duas
    lojas com grupo_economico="" não são "do mesmo grupo")."""
    linhas = session.execute(
        select(coluna).distinct().where(coluna.is_not(None), coluna != "").order_by(coluna)
    ).all()
    return [r[0] for r in linhas]


def lista_ufs(session: Session) -> list[str]:
    return [r[0] for r in session.execute(select(Loja.uf).distinct().order_by(Loja.uf)).all()]


def lista_atendentes(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.atendente_comercial)


def lista_consultores_farma(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.consultor_farma)


def lista_consultores_internos(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.consultor_interno)


def lista_grupos_economicos(session: Session) -> list[str]:
    return _lista_distinct(session, Loja.grupo_economico)


def lista_lojas(session: Session) -> list[dict]:
    """Pra multi-seleção de loja em chips (Bloco 4) — id, razão social, CNPJ, UF."""
    stmt = select(Loja.id, Loja.razao_social, Loja.cnpj, Loja.uf).order_by(Loja.razao_social)
    return [dict(r) for r in session.execute(stmt).mappings().all()]
