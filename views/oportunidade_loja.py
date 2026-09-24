"""Análise de Oportunidade · Por Loja — uma linha por loja, economia = soma
da economia dos genéricos dela no período, contra o laboratório escolhido no
filtro principal (regras em core/analise.py). O detalhe abre a tabela de
produtos da loja com as mesmas colunas da visão Por Produto. Ordenação,
paginação e detalhe rodam sobre o resultado guardado em memória
(views/analise_comum.py)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import analise, theme, ui
from core.config import settings
from views import analise_comum as comum

_KEY = "op_loja"
_LARGURAS = [2.6, 2.4, 0.6, 1.4, 1, 1.4, 0.9]
_LARGURAS_DETALHE = [2.4, 1.3, 1.1, 1.0, 1.0, 0.6, 1.1, 1.2]

_COLUNAS = [
    comum.Coluna("Loja", "razao_social"),
    comum.Coluna("Time de atendimento", "atendente_comercial"),
    comum.Coluna("UF", "uf"),
    comum.Coluna("Cidade", "cidade"),
    comum.Coluna("Genéricos", "qtd_produtos", numerica=True),
    comum.Coluna("Economia", "economia", numerica=True),
    comum.Coluna("", None),
]


def _badge_economia_loja(valor: float) -> str:
    if valor and valor > 0:
        return theme.badge(ui.formatar_moeda(float(valor)), "sucesso")
    return theme.badge("Já otimizada", "neutro")


def _colunas_detalhe(laboratorio: str) -> list[comum.Coluna]:
    return [
        comum.Coluna("Produto", "nome_canonico"),
        comum.Coluna("Laboratório", "laboratorio"),
        comum.Coluna("Preço pago", "preco_pago", numerica=True),
        comum.Coluna(laboratorio, "preco_laboratorio", numerica=True),
        comum.Coluna("Dif. un.", "diferenca", numerica=True),
        comum.Coluna("Qtd.", "quantidade", numerica=True),
        comum.Coluna(f"Preço médio ({settings.analise.meses_preco_medio}m)", "preco_medio", numerica=True),
        comum.Coluna("Economia", "economia", numerica=True),
    ]


@st.dialog("Detalhes da loja", width="large")
def _dialog_detalhes(loja: dict, produtos: pd.DataFrame, laboratorio: str) -> None:
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

    theme.card_destaque(f"Economia perdida nesse período — {laboratorio}", ui.formatar_moeda(float(loja["economia"])))

    st.markdown("#### Produtos comprados por essa loja no período")
    if produtos.empty:
        st.info("Nenhum produto com compra comparável nesse período.")
        return

    chave = f"{_KEY}_detalhe_{loja['loja_id']}"
    campo, crescente = comum.cabecalho_ordenavel(_colunas_detalhe(laboratorio), _LARGURAS_DETALHE, chave)
    for p in analise.ordenar(produtos, campo, crescente).to_dict("records"):
        c = st.columns(_LARGURAS_DETALHE)
        c[0].markdown(f"**{p['nome_canonico']}**")
        c[1].write(p.get("laboratorio") or "—")
        c[2].markdown(comum.preco_pago(p), unsafe_allow_html=True)
        c[3].write(comum.moeda(p["preco_laboratorio"]))
        c[4].markdown(comum.diferenca(p["diferenca"]), unsafe_allow_html=True)
        c[5].write(ui.formatar_numero(p["quantidade"]))
        c[6].markdown(comum.preco_medio(p), unsafe_allow_html=True)
        c[7].markdown(comum.economia(p), unsafe_allow_html=True)

    bonificadas = int(produtos["bonificacoes"].sum())
    fora = int((produtos["fonte"] == analise.FONTE_FORA).sum())
    notas = []
    if bonificadas:
        notas.append(
            f"{bonificadas} compra(s) bonificada(s) (abaixo de {ui.formatar_moeda(settings.analise.limite_bonificacao)}) "
            "ficaram fora da conta"
        )
    if fora:
        notas.append(f"{fora} produto(s) com preço \"a revisar\" (provável erro de cadastro) não somam economia")
    if notas:
        st.caption(" · ".join(notas) + ".")


def render() -> None:
    theme.cabecalho(
        "Análise de Oportunidade · Por Loja",
        "Quanto cada loja deixou de economizar comprando de outro fornecedor em vez da RMC.",
    )

    laboratorio = comum.faixa_laboratorio(_KEY)
    filtros = comum.filtros(_KEY, laboratorio, "Buscar por razão social, CNPJ ou produto")
    if laboratorio is None:
        comum.aviso_sem_laboratorio()
        return

    todas = comum.resultado(laboratorio, filtros.periodo_meses)
    linhas = analise.filtrar(todas, filtros)
    comum.card_conjunto(linhas, filtros)

    ui.resetar_pagina_se_filtro_mudou(_KEY, f"{laboratorio}|{filtros}")

    lojas = analise.por_loja(linhas)
    if lojas.empty:
        st.info(
            f"Nenhuma loja com compra comparável ao {laboratorio} com os filtros atuais. Confira se as compras GPS "
            "do período já foram enviadas e se o laboratório tem tabela ativa na UF das lojas."
        )
        return

    campo, crescente = comum.cabecalho_ordenavel(_COLUNAS, _LARGURAS, _KEY)
    fatia, pagina = comum.pagina(analise.ordenar(lojas, campo, crescente), _KEY)

    for loja in fatia.to_dict("records"):
        c = st.columns(_LARGURAS)
        c[0].write(f"{loja['razao_social']}  \n:gray[{ui.formatar_cnpj(loja['cnpj'])}]")
        c[1].markdown(
            f"<span class='rmc-muted'>Comercial: {loja.get('atendente_comercial') or '—'}<br>"
            f"CI: {loja.get('consultor_interno') or '—'} · CF: {loja.get('consultor_farma') or '—'}</span>",
            unsafe_allow_html=True,
        )
        c[2].write(loja["uf"])
        c[3].write(loja["cidade"])
        c[4].write(ui.formatar_numero(loja["qtd_produtos"]))
        c[5].markdown(_badge_economia_loja(loja["economia"]), unsafe_allow_html=True)
        with c[6]:
            st.markdown('<div class="btn-secundario"></div>', unsafe_allow_html=True)
            if st.button("Detalhes", key=f"{_KEY}_detalhe_{loja['loja_id']}"):
                _dialog_detalhes(loja, linhas[linhas["loja_id"] == loja["loja_id"]], laboratorio)

    st.divider()
    ui.controles_paginacao(pagina, _KEY)
