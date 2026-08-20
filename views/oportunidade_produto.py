"""Análise de Oportunidade · Por Produto — visão ungrouped por (loja,
genérico); pode chegar a dezenas de milhares de linhas quando a base de
compras é grande, por isso é a tela com a paginação mais pesada (sempre
resolvida no banco, nunca carregando tudo em memória)."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import theme, ui
from core.config import settings
from core.db import get_session
from core.queries import (
    Filtros,
    historico_compras,
    lista_atendentes,
    lista_grupos_economicos,
    lista_lojas,
    lista_ufs,
    oportunidade_por_produto,
    soma_economia_conjunto,
)

_KEY = "op_produto"
_TODAS = "Todas"
_TODOS = "Todos"


def _rotulo_periodo(qtd_meses: int) -> str:
    return f"{qtd_meses} mês" if qtd_meses == 1 else f"{qtd_meses} meses"


def _badge_economia(economia: float) -> str:
    if economia and economia > 0:
        return theme.badge(ui.formatar_moeda(economia), "sucesso")
    return theme.badge("Já otimizada", "neutro")


def _grafico_historico(historico: list[dict]) -> go.Figure:
    """Série principal em navy, marcador em verde — mesma regra de cor da
    identidade visual pra gráficos (no máx. 2-3 cores, sempre a partir da
    paleta central de core/theme.py, nunca hex duplicado aqui)."""
    meses = [h["ano_mes"] for h in historico]
    valores = [float(h["custo_medio_ponderado"]) for h in historico]
    fig = go.Figure(
        go.Scatter(
            x=meses,
            y=valores,
            mode="lines+markers",
            line=dict(color=theme.PALETA["navy"], width=3),
            marker=dict(color=theme.PALETA["verde"], size=9, line=dict(color=theme.PALETA["navy"], width=1)),
            hovertemplate="%{x}<br>R$ %{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=320,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        xaxis=dict(title="Mês", showgrid=False),
        yaxis=dict(title="Custo médio pago (R$)", showgrid=True, gridcolor=theme.PALETA["borda"]),
    )
    return fig


@st.dialog("Detalhes do produto", width="large")
def _dialog_detalhes(linha: dict) -> None:
    aba_resumo, aba_historico = st.tabs(["Resumo", "Histórico de compras"])

    with aba_resumo:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**CNPJ**  \n{ui.formatar_cnpj(linha['cnpj'])}")
            st.markdown(f"**Razão Social**  \n{linha['razao_social']}")
            st.markdown(f"**UF / Cidade**  \n{linha['uf']} · {linha['cidade']}")
        with c2:
            st.markdown(f"**Atendente comercial**  \n{linha.get('atendente_comercial') or '—'}")
            st.markdown(f"**Genérico**  \n{linha['nome_canonico']}")
            st.markdown(f"**Estoque atual**  \n{ui.formatar_numero(linha['estoque_total'])}")
        theme.card_destaque("Economia perdida nesse produto/período", ui.formatar_moeda(linha["economia"]))

    with aba_historico:
        with get_session() as session:
            meses_disponiveis = [
                h["ano_mes"]
                for h in historico_compras(session, linha["loja_id"], linha["base_generico_id"], None)
            ]
        chave_mes = f"{_KEY}_dialog_mes_{linha['loja_id']}_{linha['base_generico_id']}"
        mes_sel = st.selectbox("Mês", options=[_TODOS] + meses_disponiveis, key=chave_mes)

        with get_session() as session:
            historico = historico_compras(
                session, linha["loja_id"], linha["base_generico_id"],
                None if mes_sel == _TODOS else mes_sel,
            )

        if not historico:
            st.info("Sem histórico de compras pra essa combinação de loja e genérico.")
        else:
            st.plotly_chart(_grafico_historico(historico), use_container_width=True, config={"displayModeBar": False})


def render() -> None:
    theme.cabecalho(
        "Análise de Oportunidade · Por Produto",
        "Comparativo genérico a genérico entre o que a loja pagou e o menor preço RMC vigente.",
    )

    with get_session() as session:
        ufs = lista_ufs(session)
        atendentes = lista_atendentes(session)
        grupos = lista_grupos_economicos(session)
        lojas_disponiveis = lista_lojas(session)

    f1, f2, f3, f4, f5 = st.columns([2.2, 1, 1.4, 1.6, 1.2])
    with f1:
        busca = st.text_input("Buscar por genérico, razão social ou CNPJ", key=f"{_KEY}_busca")
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
        resultado = oportunidade_por_produto(session, filtros, pagina, tamanho)

    if not resultado.linhas:
        st.info(
            "Nenhum produto encontrado com os filtros atuais. Verifique se já existem compras GPS e "
            "tabela de preços RMC (Gruppy) cadastradas na aba Dados."
        )
        return

    header = st.columns([3, 1.3, 1.3, 1.3, 1.1])
    for col, titulo in zip(header, ["Genérico / Loja", "Custo médio pago", "Menor preço RMC", "Economia", ""]):
        if titulo:
            col.markdown(f"**{titulo}**")

    for linha in resultado.linhas:
        c = st.columns([3, 1.3, 1.3, 1.3, 1.1])
        c[0].write(f"{linha['nome_canonico']}  \n:gray[{linha['razao_social']} · {ui.formatar_cnpj(linha['cnpj'])}]")
        c[1].write(ui.formatar_moeda(linha["custo_medio_ponderado"]))
        c[2].write(ui.formatar_moeda(linha["menor_preco"]))
        c[3].markdown(_badge_economia(linha["economia"]), unsafe_allow_html=True)
        with c[4]:
            st.markdown('<div class="btn-secundario"></div>', unsafe_allow_html=True)
            if st.button("Detalhes", key=f"{_KEY}_detalhe_{linha['loja_id']}_{linha['base_generico_id']}"):
                _dialog_detalhes(linha)

    st.divider()
    ui.controles_paginacao(resultado, _KEY)
