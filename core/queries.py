"""Motor de cálculo de oportunidade (Análise de Oportunidade: Por Loja / Por
Produto) — o "coração" do sistema: cruza o que cada loja pagou de fato (GPS)
contra o menor preço RMC vigente pra UF dela (Gruppy) e calcula quanto foi
"economia perdida" por não ter comprado da RMC.

Ponto mais sensível a performance do sistema: a base de compras pode ter
100k+ linhas (loja x produto x mês). Regra usada em todo lugar aqui: NUNCA
carregar a tabela inteira em memória / DataFrame só pra paginar em Python.
Toda filtragem, agregação (SUM/AVG ponderada) e paginação (LIMIT/OFFSET)
roda dentro do banco via SQLAlchemy, e só a página atual chega ao Streamlit.

Peças centrais (nomes batem com o plano de construção):
- `_menor_preco_rmc_subquery`: MIN(custo_liquido) por (UF de cobertura ativa,
  base_generico_id) — o cruzamento com a compra da loja é sempre INNER JOIN
  por (UF da loja, genérico): se não existe cobertura ativa da Gruppy pra
  aquela UF+genérico, a linha simplesmente não aparece (nunca um placeholder
  "sem comparação").
- `_compras_periodo_subquery`: consolida quantidade e custo médio PONDERADO
  (por quantidade/valor, não média simples) por (loja, genérico) no período —
  múltiplos EAN do mesmo genérico caem numa linha só.
- `_estoque_atual_subquery`: sempre o snapshot do mês mais recente por
  (loja, EAN), nunca histórico — decisão já confirmada, sem aviso de idade
  do dado. Restrita aos `ano_meses` já calculados em `_query_base` (índice
  em `ano_mes`), nunca um scan sem filtro da tabela inteira.
- `_economia_expr`: CASE portátil (funciona igual em SQLite e Postgres, ao
  contrário de `func.greatest` que é Postgres-only) para
  max(0, custo_pago - menor_preco) * quantidade — nunca negativa.
- período ("1/2/6 meses"): sempre os N `ano_mes` DISTINTOS mais recentes já
  carregados via GPS — nunca uma janela de dias corridos.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from core.models import (
    BaseGenerico,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)


@dataclass
class Filtros:
    uf: str | None = None
    busca: str | None = None  # razão social, CNPJ da loja ou nome do genérico
    periodo_meses: int = 1  # quantos ano_mes distintos mais recentes considerar
    atendente_comercial: str | None = None
    consultor_farma: str | None = None
    consultor_interno: str | None = None
    grupo_economico: str | None = None
    loja_ids: list[int] | None = None  # multi-seleção de loja em chips


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


def _menor_preco_rmc_subquery():
    """MIN(custo_liquido) agrupado por (UF de cobertura, base_generico_id),
    olhando só `TabelaGruppyCobertura.status == ATIVA`. Cada linha aqui
    representa "o melhor preço RMC disponível pra esse genérico, naquela UF,
    agora"."""
    stmt = (
        select(
            TabelaGruppyCobertura.uf.label("uf"),
            EanGenerico.base_generico_id.label("base_generico_id"),
            func.min(ItemTabelaGruppy.custo_liquido).label("menor_preco"),
        )
        .select_from(ItemTabelaGruppy)
        .join(EanGenerico, EanGenerico.ean == ItemTabelaGruppy.ean)
        .join(TabelaGruppy, TabelaGruppy.id == ItemTabelaGruppy.tabela_gruppy_id)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA)
        .group_by(TabelaGruppyCobertura.uf, EanGenerico.base_generico_id)
    )
    return stmt.subquery()


def _compras_periodo_subquery(ano_meses: list[str]):
    """Consolida compras GPS do período por (loja_id, base_generico_id):
    quantidade total e custo médio PONDERADO por quantidade/valor —
    SUM(custo_unitario*quantidade)/SUM(quantidade), nunca a média simples
    entre linhas de EAN diferentes. Isso também junta múltiplos EAN do mesmo
    genérico numa linha só (ex: duas apresentações do mesmo remédio que já
    foram resolvidas pro mesmo BaseGenerico)."""
    peso = RegistroCompraGPS.custo_unitario * RegistroCompraGPS.quantidade
    stmt = (
        select(
            RegistroCompraGPS.loja_id.label("loja_id"),
            EanGenerico.base_generico_id.label("base_generico_id"),
            func.sum(RegistroCompraGPS.quantidade).label("quantidade_total"),
            # NULLIF evita divisão por zero se a soma de quantidade der 0 num
            # grupo (ex: linhas com quantidade zerada que passaram do filtro
            # de lixo) — sem isso o Postgres derruba a query com
            # "division by zero" (SQLite silenciosamente vira NULL, então o
            # bug só aparecia em produção).
            (func.sum(peso) / func.nullif(func.sum(RegistroCompraGPS.quantidade), 0)).label("custo_medio_ponderado"),
        )
        .select_from(RegistroCompraGPS)
        .join(EanGenerico, EanGenerico.ean == RegistroCompraGPS.ean)
        .where(RegistroCompraGPS.ano_mes.in_(ano_meses))
        .group_by(RegistroCompraGPS.loja_id, EanGenerico.base_generico_id)
    )
    return stmt.subquery()


def _estoque_atual_subquery(ano_meses: list[str]):
    """Snapshot de estoque do mês mais recente por (loja_id, ean), depois
    somado por genérico. Sempre o mês mais recente, NUNCA histórico (decisão
    já confirmada — sem aviso de idade do dado).

    Restrito a `max(ano_meses)` — os mesmos `ano_meses` já calculados em
    `_query_base` pra `_compras_periodo_subquery` (a N-ésima janela de meses
    mais recentes já carregados via GPS). Antes disso a query rodava um
    row_number() OVER (PARTITION BY loja_id, ean ORDER BY ano_mes DESC) SEM
    NENHUM filtro de WHERE — ou seja, escaneava e ordenava a tabela
    `registros_compra_gps` INTEIRA (todo mês já carregado desde o início) só
    pra descartar quase tudo no rn==1 depois. Com o filtro por ano_mes e a
    UniqueConstraint(loja_id, ean, ano_mes) garantindo no máximo 1 linha por
    (loja_id, ean) dentro de um único ano_mes, o row_number()/partição
    inteira deixam de ser necessários — group by direto já basta (o SUM
    continua precisando existir porque vários EAN do mesmo genérico, cada um
    com sua própria linha nesse ano_mes, ainda precisam ser consolidados).

    Índice usado: `registros_compra_gps.ano_mes` tem índice próprio
    (`index=True` em core/models.py) — é ele que atende o filtro abaixo. A
    UniqueConstraint(loja_id, ean, ano_mes) NÃO ajuda aqui: é uma unique
    composta com ano_mes por último na ordem das colunas, e pela regra de
    prefixo à esquerda um índice assim não serve pra filtrar só por
    ano_mes."""
    ultimo_ano_mes = max(ano_meses)
    stmt = (
        select(
            RegistroCompraGPS.loja_id.label("loja_id"),
            EanGenerico.base_generico_id.label("base_generico_id"),
            func.sum(RegistroCompraGPS.estoque).label("estoque_total"),
        )
        .select_from(RegistroCompraGPS)
        .join(EanGenerico, EanGenerico.ean == RegistroCompraGPS.ean)
        .where(RegistroCompraGPS.ano_mes == ultimo_ano_mes)
        .group_by(RegistroCompraGPS.loja_id, EanGenerico.base_generico_id)
    )
    return stmt.subquery()


def _economia_expr(custo_pago_col, menor_preco_col, quantidade_col):
    """CASE portátil (SQLite/Postgres — `func.greatest` não existe no
    SQLite) para max(0, custo_pago - menor_preco) * quantidade: o valor total
    (em R$) que a loja deixou de economizar comprando fora da RMC nesse
    período. Nunca negativa — quando a loja já compra no menor preço (ou
    abaixo), a linha é "já otimizada", não uma economia negativa."""
    return case(
        (custo_pago_col > menor_preco_col, (custo_pago_col - menor_preco_col) * quantidade_col),
        else_=0,
    )


def _query_base(session: Session, filtros: Filtros):
    """Núcleo comum de `oportunidade_por_loja`/`oportunidade_por_produto`/
    `detalhe_produtos_da_loja`/`soma_economia_conjunto`: uma linha por
    (loja, genérico) já com quantidade, custo pago, menor preço RMC (mesma
    UF), estoque atual e economia calculados. Devolve None quando ainda não
    há nenhum mês de GPS carregado (nada a calcular)."""
    ano_meses = listar_ultimos_ano_meses(session, filtros.periodo_meses)
    if not ano_meses:
        return None

    compras = _compras_periodo_subquery(ano_meses)
    menor_preco = _menor_preco_rmc_subquery()
    estoque = _estoque_atual_subquery(ano_meses)

    economia = _economia_expr(compras.c.custo_medio_ponderado, menor_preco.c.menor_preco, compras.c.quantidade_total)

    query = (
        select(
            Loja.id.label("loja_id"),
            Loja.cnpj,
            Loja.razao_social,
            Loja.uf,
            Loja.cidade,
            Loja.atendente_comercial,
            Loja.consultor_farma,
            Loja.consultor_interno,
            Loja.grupo_economico,
            BaseGenerico.id.label("base_generico_id"),
            BaseGenerico.nome_canonico,
            compras.c.quantidade_total,
            compras.c.custo_medio_ponderado,
            menor_preco.c.menor_preco,
            func.coalesce(estoque.c.estoque_total, 0).label("estoque_total"),
            economia.label("economia"),
        )
        .select_from(compras)
        .join(Loja, Loja.id == compras.c.loja_id)
        .join(
            menor_preco,
            (menor_preco.c.uf == Loja.uf) & (menor_preco.c.base_generico_id == compras.c.base_generico_id),
        )
        .join(BaseGenerico, BaseGenerico.id == compras.c.base_generico_id)
        .outerjoin(
            estoque,
            (estoque.c.loja_id == compras.c.loja_id) & (estoque.c.base_generico_id == compras.c.base_generico_id),
        )
    )

    if filtros.uf:
        query = query.where(Loja.uf == filtros.uf)
    if filtros.loja_ids:
        query = query.where(Loja.id.in_(filtros.loja_ids))
    if filtros.atendente_comercial:
        query = query.where(Loja.atendente_comercial == filtros.atendente_comercial)
    if filtros.consultor_farma:
        query = query.where(Loja.consultor_farma == filtros.consultor_farma)
    if filtros.consultor_interno:
        query = query.where(Loja.consultor_interno == filtros.consultor_interno)
    if filtros.grupo_economico:
        query = query.where(Loja.grupo_economico == filtros.grupo_economico)
    if filtros.busca:
        termo = f"%{filtros.busca.lower()}%"
        query = query.where(
            func.lower(Loja.razao_social).like(termo)
            | func.lower(Loja.cnpj).like(termo)
            | func.lower(BaseGenerico.nome_canonico).like(termo)
        )

    return query


def _paginar(session: Session, stmt, pagina: int, tamanho_pagina: int) -> PaginaResultado:
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    pagina = max(1, pagina)
    linhas = session.execute(
        stmt.limit(tamanho_pagina).offset((pagina - 1) * tamanho_pagina)
    ).mappings().all()
    return PaginaResultado(
        linhas=[dict(r) for r in linhas], total_linhas=total, pagina=pagina, tamanho_pagina=tamanho_pagina
    )


def oportunidade_por_produto(
    session: Session, filtros: Filtros, pagina: int, tamanho_pagina: int
) -> PaginaResultado:
    """Visão ungrouped por (loja, genérico) — é a tela que pode chegar a
    100k+ linhas quando a base de compras é grande, por isso a paginação
    mais pesada."""
    base = _query_base(session, filtros)
    if base is None:
        return PaginaResultado(linhas=[], total_linhas=0, pagina=1, tamanho_pagina=tamanho_pagina)

    stmt = base.order_by(base.selected_columns.economia.desc())
    return _paginar(session, stmt, pagina, tamanho_pagina)


def oportunidade_por_loja(
    session: Session, filtros: Filtros, pagina: int, tamanho_pagina: int
) -> PaginaResultado:
    """Agrupado por loja (1 linha por loja) — economia = soma da economia de
    todos os genéricos daquela loja no período."""
    base = _query_base(session, filtros)
    if base is None:
        return PaginaResultado(linhas=[], total_linhas=0, pagina=1, tamanho_pagina=tamanho_pagina)

    detalhe = base.subquery()
    campos_loja = (
        detalhe.c.loja_id, detalhe.c.cnpj, detalhe.c.razao_social, detalhe.c.uf, detalhe.c.cidade,
        detalhe.c.atendente_comercial, detalhe.c.consultor_farma, detalhe.c.consultor_interno,
        detalhe.c.grupo_economico,
    )
    stmt = (
        select(
            *campos_loja,
            func.sum(detalhe.c.economia).label("economia"),
            func.count(func.distinct(detalhe.c.base_generico_id)).label("qtd_produtos"),
        )
        .group_by(*campos_loja)
        .order_by(func.sum(detalhe.c.economia).desc())
    )
    return _paginar(session, stmt, pagina, tamanho_pagina)


def detalhe_produtos_da_loja(session: Session, loja_id: int, filtros: Filtros) -> list[dict]:
    """Breakdown por genérico de uma loja específica (popup de Detalhes da
    visão 'Por Loja') — sem paginação própria, é sempre uma lista pequena o
    suficiente (o catálogo de genéricos de uma única loja)."""
    filtros_loja = replace(filtros, loja_ids=[loja_id])
    base = _query_base(session, filtros_loja)
    if base is None:
        return []
    stmt = base.order_by(base.selected_columns.economia.desc())
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def soma_economia_conjunto(session: Session, filtros: Filtros) -> float:
    """Soma da economia sobre TODO o conjunto filtrado, não só a página
    visível — usado no card de destaque (grupo econômico ativo ou 2+ lojas
    selecionadas)."""
    base = _query_base(session, filtros)
    if base is None:
        return 0.0
    sub = base.subquery()
    total = session.execute(select(func.coalesce(func.sum(sub.c.economia), 0))).scalar_one()
    return float(total)


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


def listar_laboratorios_ativos(session: Session) -> list[str]:
    """Laboratórios com ao menos uma cobertura ATIVA em alguma UF — a lista
    (pequena e global) que alimenta as colunas dinâmicas de comparação por
    laboratório."""
    linhas = session.execute(
        select(TabelaGruppy.laboratorio)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA)
        .distinct()
        .order_by(TabelaGruppy.laboratorio)
    ).all()
    return [r[0] for r in linhas]


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
    como opção fantasma no dropdown nem virar critério de agrupamento (ver
    soma_economia_conjunto — duas lojas com grupo_economico="" não são "do
    mesmo grupo")."""
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
