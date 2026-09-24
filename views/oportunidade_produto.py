"""Análise de Oportunidade · Por Produto — uma linha por (loja, genérico),
comparando o MENOR preço que a loja pagou com o preço do laboratório
escolhido no filtro principal (regras em core/analise.py). Ordenação pelo
cabeçalho e paginação rodam sobre o resultado guardado em memória
(views/analise_comum.py) — só mudar laboratório ou período vai ao banco."""
from __future__ import annotations

import html

import plotly.graph_objects as go
import streamlit as st

from core import analise, theme, ui
from core.config import settings
from core.db import get_session
from core.queries import historico_compras
from views import analise_comum as comum

_KEY = "op_produto"
_TODOS = "Todos"
_LARGURAS = [2.4, 1.2, 1.1, 1.0, 1.05, 0.65, 1.15, 1.25, 1.1]


def _colunas(laboratorio: str) -> list[comum.Coluna]:
    return [
        comum.Coluna("Produto", "nome_canonico"),
        comum.Coluna("Laboratório", "laboratorio"),
        comum.Coluna("Preço pago", "preco_pago", numerica=True),
        comum.Coluna(laboratorio, "preco_laboratorio", numerica=True),
        comum.Coluna("Diferença un.", "diferenca", numerica=True),
        comum.Coluna("Qtd.", "quantidade", numerica=True),
        comum.Coluna(f"Preço médio ({settings.analise.meses_preco_medio}m)", "preco_medio", numerica=True),
        comum.Coluna("Economia", "economia", numerica=True),
        comum.Coluna("", None),
    ]


def _grafico_historico(historico: list[dict]) -> go.Figure:
    """Série principal em navy, marcador em verde — mesma regra de cor da
    identidade visual pra gráficos (paleta central de core/theme.py)."""
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
        yaxis=dict(title="Valor unitário médio pago (R$)", showgrid=True, gridcolor=theme.PALETA["borda"]),
    )
    return fig


@st.dialog("Detalhes do produto", width="large")
def _dialog_detalhes(linha: dict, laboratorio: str) -> None:
    aba_resumo, aba_historico = st.tabs(["Resumo", "Histórico de compras"])

    with aba_resumo:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**CNPJ**  \n{ui.formatar_cnpj(linha['cnpj'])}")
            st.markdown(f"**Razão Social**  \n{linha['razao_social']}")
            st.markdown(f"**UF / Cidade**  \n{linha['uf']} · {linha['cidade']}")
            st.markdown(f"**Atendente comercial**  \n{linha.get('atendente_comercial') or '—'}")
        with c2:
            st.markdown(f"**Genérico**  \n{linha['nome_canonico']}")
            st.markdown(f"**Produto comprado (GPS)**  \n{linha.get('descricao') or '—'}")
            st.markdown(f"**Laboratório da compra**  \n{linha.get('laboratorio') or '—'}")
            st.markdown(
                f"**Preço pago − {laboratorio}**  \n"
                + comum.texto_markdown(f"{comum.moeda(linha['preco_pago'])} − {comum.moeda(linha['preco_laboratorio'])} = ")
                + comum.diferenca(linha["diferenca"])
                + f" por unidade × {ui.formatar_numero(linha['quantidade'])} un.",
                unsafe_allow_html=True,
            )
        if linha.get("bonificacoes"):
            st.caption(
                f"{linha['bonificacoes']} compra(s) bonificada(s) (preço abaixo de "
                f"{ui.formatar_moeda(settings.analise.limite_bonificacao)}) ficaram fora da conta."
            )
        if linha["fonte"] == analise.FONTE_FORA:
            st.warning(comum.texto_markdown(
                f"Nenhum preço da planilha (VlrUnitario {comum.moeda(linha['vlr_unitario_original'])}, custo CMV e "
                f"custo médio) ficou a até ±50% do preço {laboratorio}. Provável erro de cadastro da loja — "
                "a economia desta linha não é contabilizada."
            ))
        theme.card_destaque("Economia perdida nesse produto/período", ui.formatar_moeda(float(linha["economia"])))

    with aba_historico:
        with get_session() as session:
            meses_disponiveis = [
                h["ano_mes"] for h in historico_compras(session, linha["loja_id"], linha["base_generico_id"], None)
            ]
        chave_mes = f"{_KEY}_dialog_mes_{linha['loja_id']}_{linha['base_generico_id']}"
        mes_sel = st.selectbox("Mês", options=[_TODOS] + meses_disponiveis, key=chave_mes)

        with get_session() as session:
            historico = historico_compras(
                session, linha["loja_id"], linha["base_generico_id"], None if mes_sel == _TODOS else mes_sel,
            )

        if not historico:
            st.info("Sem histórico de compras pra essa combinação de loja e genérico.")
        else:
            st.plotly_chart(_grafico_historico(historico), use_container_width=True, config={"displayModeBar": False})


def render() -> None:
    theme.cabecalho(
        "Análise de Oportunidade · Por Produto",
        "Comparativo genérico a genérico entre o menor preço que a loja pagou e o preço do laboratório.",
    )

    laboratorio = comum.faixa_laboratorio(_KEY)
    filtros = comum.filtros(_KEY, laboratorio, "Buscar por produto, laboratório, razão social ou CNPJ")
    if laboratorio is None:
        comum.aviso_sem_laboratorio()
        return

    todas = comum.resultado(laboratorio, filtros.periodo_meses)
    linhas = analise.filtrar(todas, filtros)
    comum.card_conjunto(linhas, filtros)

    assinatura = f"{laboratorio}|{filtros}"
    ui.resetar_pagina_se_filtro_mudou(_KEY, assinatura)

    if linhas.empty:
        st.info(
            f"Nenhum produto com compra comparável ao {laboratorio} com os filtros atuais. Confira se as compras GPS "
            "do período já foram enviadas e se os EANs estão resolvidos na Base Genéricos."
        )
        return

    colunas = _colunas(laboratorio)
    campo, crescente = comum.cabecalho_ordenavel(colunas, _LARGURAS, _KEY)
    fatia, pagina = comum.pagina(analise.ordenar(linhas, campo, crescente), _KEY)

    for linha in fatia.to_dict("records"):
        c = st.columns(_LARGURAS)
        c[0].markdown(
            f"**{linha['nome_canonico']}**<br>"
            f"<span class='rmc-sub-loja'>{html.escape(linha['razao_social'] or '')} · "
            f"{ui.formatar_cnpj(linha['cnpj'])}</span>",
            unsafe_allow_html=True,
        )
        c[1].write(linha.get("laboratorio") or "—")
        c[2].markdown(comum.preco_pago(linha), unsafe_allow_html=True)
        c[3].write(comum.moeda(linha["preco_laboratorio"]))
        c[4].markdown(comum.diferenca(linha["diferenca"]), unsafe_allow_html=True)
        c[5].write(ui.formatar_numero(linha["quantidade"]))
        c[6].markdown(comum.preco_medio(linha), unsafe_allow_html=True)
        c[7].markdown(comum.economia(linha), unsafe_allow_html=True)
        with c[8]:
            st.markdown('<div class="btn-secundario"></div>', unsafe_allow_html=True)
            if st.button("Detalhes", key=f"{_KEY}_detalhe_{linha['loja_id']}_{linha['base_generico_id']}"):
                _dialog_detalhes(linha, laboratorio)

    st.divider()
    ui.controles_paginacao(pagina, _KEY)
