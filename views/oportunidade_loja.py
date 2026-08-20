"""Análise de Oportunidade · Por Loja — 1 linha por loja, economia = soma da
economia de todos os genéricos comprados por ela no período (core/queries.py
faz a agregação inteira dentro do banco, nunca em Python)."""
from __future__ import annotations

import streamlit as st

from core import theme, ui
from core.config import settings
from core.db import get_session
from core.queries import (
    Filtros,
    detalhe_produtos_da_loja,
    lista_atendentes,
    lista_grupos_economicos,
    lista_lojas,
    lista_ufs,
    oportunidade_por_loja,
    soma_economia_conjunto,
)

_KEY = "op_loja"
_TODAS = "Todas"
_TODOS = "Todos"


def _rotulo_periodo(qtd_meses: int) -> str:
    return f"{qtd_meses} mês" if qtd_meses == 1 else f"{qtd_meses} meses"


def _badge_economia(economia: float) -> str:
    """Cores corrigidas: economia real em R$ sempre em verde (par 'sucesso')
    — "já otimizada" usa o par cinza dedicado ('neutro'), deliberadamente
    mais apagado, pra nunca competir visualmente com uma economia de
    verdade (ver core/theme.py)."""
    if economia and economia > 0:
        return theme.badge(ui.formatar_moeda(economia), "sucesso")
    return theme.badge("Já otimizada", "neutro")


@st.dialog("Detalhes da loja", width="large")
def _dialog_detalhes(loja: dict, filtros: Filtros) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**CNPJ**  \n{ui.formatar_cnpj(loja['cnpj'])}")
        st.markdown(f"**Razão Social**  \n{loja['razao_social']}")
        st.markdown(f"**UF / Cidade**  \n{loja['uf']} · {loja['cidade']}")
    with c2:
        st.markdown(f"**Atendente comercial**  \n{loja.get('atendente_comercial') or '—'}")
        st.markdown(f"**Consultor Farma**  \n{loja.get('consultor_farma') or '—'}")
        st.markdown(f"**Consultor interno**  \n{loja.get('consultor_interno') or '—'}")
        st.markdown(f"**Grupo econômico**  \n{loja.get('grupo_economico') or '—'}")

    theme.card_destaque("Economia perdida nesse período", ui.formatar_moeda(loja["economia"]))

    st.markdown("#### Genéricos comprados por essa loja no período")
    with get_session() as session:
        produtos = detalhe_produtos_da_loja(session, loja["loja_id"], filtros)

    if not produtos:
        st.info("Nenhum genérico com compra reconciliada nesse período.")
        return

    header = st.columns([3, 1.2, 1.2, 1.4, 1])
    for col, titulo in zip(header, ["Genérico", "Qtd. comprada", "Custo médio", "Estoque atual", "Economia"]):
        col.markdown(f"**{titulo}**")
    for p in produtos:
        c = st.columns([3, 1.2, 1.2, 1.4, 1])
        c[0].write(p["nome_canonico"])
        c[1].write(ui.formatar_numero(p["quantidade_total"]))
        c[2].write(ui.formatar_moeda(p["custo_medio_ponderado"]))
        c[3].write(ui.formatar_numero(p["estoque_total"]))
        c[4].markdown(_badge_economia(p["economia"]), unsafe_allow_html=True)


def render() -> None:
    theme.cabecalho(
        "Análise de Oportunidade · Por Loja",
        "Quanto cada loja deixou de economizar comprando de outro fornecedor em vez da RMC.",
    )

    with get_session() as session:
        ufs = lista_ufs(session)
        atendentes = lista_atendentes(session)
        grupos = lista_grupos_economicos(session)
        lojas_disponiveis = lista_lojas(session)

    f1, f2, f3, f4, f5 = st.columns([2.2, 1, 1.4, 1.6, 1.2])
    with f1:
        busca = st.text_input("Buscar por razão social ou CNPJ", key=f"{_KEY}_busca")
    with f2:
        uf = st.selectbox("UF", options=[_TODAS] + ufs, key=f"{_KEY}_uf")
    with f3:
        atendente = st.selectbox("Atendente comercial", options=[_TODOS] + atendentes, key=f"{_KEY}_atendente")
    with f4:
        grupo = st.selectbox("Grupo econômico", options=[_TODOS] + grupos, key=f"{_KEY}_grupo")
    with f5:
        periodo = st.selectbox(
            "Período", options=settings.periodos_meses_opcoes, format_func=_rotulo_periodo, key=f"{_KEY}_periodo",
        )

    with st.expander("Selecionar lojas específicas (opcional)"):
        mapa_lojas = {f"{l['razao_social']} · {ui.formatar_cnpj(l['cnpj'])}": l["id"] for l in lojas_disponiveis}
        selecionadas = st.multiselect("Lojas", options=list(mapa_lojas.keys()), key=f"{_KEY}_lojas")
        loja_ids = [mapa_lojas[s] for s in selecionadas] or None

    filtros = Filtros(
        uf=None if uf == _TODAS else uf,
        busca=busca or None,
        periodo_meses=periodo,
        atendente_comercial=None if atendente == _TODOS else atendente,
        grupo_economico=None if grupo == _TODOS else grupo,
        loja_ids=loja_ids,
    )

    # Card de destaque: soma sobre TODO o conjunto filtrado (não só a página
    # visível) -- só faz sentido mostrar quando o filtro já reduziu a um
    # conjunto específico (grupo econômico ativo ou 2+ lojas selecionadas).
    if filtros.grupo_economico or (loja_ids and len(loja_ids) >= 2):
        with get_session() as session:
            total_conjunto = soma_economia_conjunto(session, filtros)
        rotulo = filtros.grupo_economico or f"{len(loja_ids)} lojas selecionadas"
        theme.card_destaque(f"Economia perdida total — {rotulo}", ui.formatar_moeda(total_conjunto))

    assinatura = f"{busca}|{uf}|{atendente}|{grupo}|{periodo}|{loja_ids}"
    ui.resetar_pagina_se_filtro_mudou(_KEY, assinatura)

    tamanho = ui.tamanho_pagina_atual(_KEY)
    pagina = ui.pagina_atual(_KEY)

    with get_session() as session:
        resultado = oportunidade_por_loja(session, filtros, pagina, tamanho)

    if not resultado.linhas:
        st.info(
            "Nenhuma loja encontrada com os filtros atuais. Verifique se já existem compras GPS e "
            "tabela de preços RMC (Gruppy) cadastradas na aba Dados."
        )
        return

    header = st.columns([2.6, 2.4, 0.6, 1.4, 1, 1.4, 0.9])
    for col, titulo in zip(header, ["Razão Social", "Time de atendimento", "UF", "Cidade", "Genéricos", "Economia", ""]):
        if titulo:
            col.markdown(f"**{titulo}**")

    for loja in resultado.linhas:
        c = st.columns([2.6, 2.4, 0.6, 1.4, 1, 1.4, 0.9])
        c[0].write(f"{loja['razao_social']}  \n:gray[{ui.formatar_cnpj(loja['cnpj'])}]")
        c[1].markdown(
            f"<span class='rmc-muted'>Comercial: {loja.get('atendente_comercial') or '—'}<br>"
            f"CI: {loja.get('consultor_interno') or '—'} · CF: {loja.get('consultor_farma') or '—'}</span>",
            unsafe_allow_html=True,
        )
        c[2].write(loja["uf"])
        c[3].write(loja["cidade"])
        c[4].write(ui.formatar_numero(loja["qtd_produtos"]))
        c[5].markdown(_badge_economia(loja["economia"]), unsafe_allow_html=True)
        with c[6]:
            st.markdown('<div class="btn-secundario"></div>', unsafe_allow_html=True)
            if st.button("Detalhes", key=f"{_KEY}_detalhe_{loja['loja_id']}"):
                _dialog_detalhes(loja, filtros)

    st.divider()
    ui.controles_paginacao(resultado, _KEY)
