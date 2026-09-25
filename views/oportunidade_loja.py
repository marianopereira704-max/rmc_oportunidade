"""Análise de Oportunidade · Por Loja — uma linha por loja, economia = soma
da economia dos genéricos dela no período, contra o laboratório escolhido no
filtro principal (regras em core/analise.py). O detalhe abre a tabela de
produtos da loja com as mesmas colunas da visão Por Produto. Ordenação,
paginação e detalhe rodam sobre o resultado guardado em memória
(views/analise_comum.py); a tabela é o componente de views/tabela.py."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import analise, theme, ui
from core.config import settings
from views import analise_comum as comum
from views import tabela as tb

_KEY = "op_loja"

# Ordem fixa dos responsáveis, uma linha cada; posição vazia = "—". Sem
# prefixo ("CI:", "CF:"): quem é quem vem da posição — por isso a dica no "?".
_RESPONSAVEIS = ("consultor_interno", "consultor_farma", "atendente_comercial")
_DICA_RESPONSAVEL = (
    "Em ordem, uma linha cada: 1. Consultor interno · 2. Consultor farma · "
    "3. Atendente comercial. \"—\" = posição sem responsável."
)

_COLUNAS = [
    tb.ColunaTabela("razao_social", "Loja", "fit-content(340px)"),
    tb.ColunaTabela("localizacao", "Localização", "fit-content(220px)", icone="local"),
    tb.ColunaTabela("consultor_interno", "Responsável", dica=_DICA_RESPONSAVEL, icone="pessoa"),
    tb.ColunaTabela("qtd_produtos", "Produtos", direita=True, numerica=True),
    tb.ColunaTabela("economia", "Economia", direita=True, numerica=True),
]


def _texto(valor) -> str | None:
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


# Conectivos que não contam como sobrenome: "ANA DE SOUZA" → "ANA SOUZA".
_CONECTIVOS = {"DE", "DA", "DO", "DAS", "DOS", "E", "D'"}


def nome_curto(nome: str) -> str:
    """Nome + primeiro sobrenome ("AMANDA EMANUELI DE SOUZA LIMA" →
    "AMANDA EMANUELI"): pedido de 25/09/2026 — o nome completo deixava a
    coluna Responsável cortada em "…" na maioria das lojas."""
    partes = nome.split()
    sobrenome = next((p for p in partes[1:] if p.upper() not in _CONECTIVOS), None)
    return f"{partes[0]} {sobrenome}" if sobrenome else partes[0]


def _localizacao(loja: dict) -> str | None:
    partes = [p for p in (_texto(loja.get("cidade")), _texto(loja.get("uf"))) if p]
    return "/".join(partes) or None


def _linha(loja: dict) -> dict:
    economia = float(loja["economia"] or 0)
    selo = (
        tb.pedaco(ui.formatar_moeda(economia), "selo-sucesso") if economia > 0
        else tb.pedaco("Já otimizada", "selo-neutro")
    )
    responsaveis = []
    for campo in _RESPONSAVEIS:
        nome = _texto(loja.get(campo))
        if not nome:
            responsaveis.append(tb.pedaco("—", "aux"))
            continue
        # Nome + sobrenome, uma linha por pessoa; o nome inteiro fica na dica.
        # "unica" continua como garantia: se ainda não couber, termina em "…".
        curto = nome_curto(nome)
        responsaveis.append(tb.pedaco(curto, "aux unica", dica=nome if curto != nome else None))
    return {
        "id": str(loja["loja_id"]),
        "celulas": {
            "razao_social": tb.celula(
                tb.pedaco(loja["razao_social"], "forte"),
                tb.pedaco(ui.formatar_cnpj(loja["cnpj"]), "aux"),
            ),
            "localizacao": tb.celula(tb.pedaco(loja["localizacao"] or "—")),
            "consultor_interno": tb.celula(*responsaveis),
            "qtd_produtos": tb.celula(tb.pedaco(ui.formatar_numero(loja["qtd_produtos"]))),
            "economia": tb.celula(selo),
        },
    }


@st.dialog("Detalhes da loja", width="large")
def _dialog_detalhes(loja: dict, produtos: pd.DataFrame, laboratorio: str) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**CNPJ**  \n{ui.formatar_cnpj(loja['cnpj'])}")
        st.markdown(f"**Razão Social**  \n{loja['razao_social']}")
        st.markdown(f"**UF / Cidade**  \n{loja['uf']} · {loja['cidade']}")
        st.markdown(f"**Grupo econômico**  \n{loja.get('grupo_economico') or '—'}")
    with c2:
        # Mesma ordem da coluna Responsável da tabela, aqui com o nome inteiro.
        st.markdown(f"**Consultor interno**  \n{loja.get('consultor_interno') or '—'}")
        st.markdown(f"**Consultor farma**  \n{loja.get('consultor_farma') or '—'}")
        st.markdown(f"**Atendente comercial**  \n{loja.get('atendente_comercial') or '—'}")

    theme.card_destaque(f"Economia perdida nesse período — {laboratorio}", ui.formatar_moeda(float(loja["economia"])))

    theme.titulo_secao("Produtos comprados por essa loja no período")
    chave = f"{_KEY}_detalhe_{loja['loja_id']}"
    comum.tabela_produtos(
        f"{chave}_tabela", chave, analise.ordenar(produtos, *comum.ordem_atual(chave)).to_dict("records"),
        laboratorio, com_loja=False,
        vazio=("Nenhum produto comparável", "Nenhuma compra desta loja no período tem preço do laboratório"),
    )

    bonificadas = int(produtos["bonificacoes"].sum()) if not produtos.empty else 0
    fora = int((produtos["fonte"] == analise.FONTE_FORA).sum()) if not produtos.empty else 0
    notas = []
    if bonificadas:
        notas.append(
            f"{bonificadas} compra(s) bonificada(s) (abaixo de {ui.formatar_moeda(settings.analise.limite_bonificacao)}) "
            "ficaram fora da conta"
        )
    if fora:
        notas.append(f"{fora} produto(s) com preço \"a revisar\" (provável erro de cadastro) não somam economia")
    if notas:
        st.caption(comum.texto_markdown(" · ".join(notas) + "."))


def render() -> None:
    with theme.tela("por-loja"):
        _render()


def _render() -> None:
    # Sem cabeçalho de página (pedido de 25/09/2026): a tela abre direto na
    # faixa Laboratório — o botão ativo na sidebar já diz em que tela se está.
    laboratorio = comum.faixa_laboratorio(_KEY)
    # Cards entre a faixa e os filtros, preenchidos depois de ler os filtros.
    area_indicadores = st.container()
    filtros = comum.filtros(_KEY, laboratorio, "Buscar loja, CNPJ ou produto")
    if laboratorio is None:
        comum.aviso_sem_laboratorio()
        return

    todas = comum.resultado(laboratorio, filtros.periodo_meses)
    linhas = analise.filtrar(todas, filtros)
    with area_indicadores:
        comum.indicadores("loja", linhas, laboratorio, filtros)

    ui.resetar_pagina_se_filtro_mudou(_KEY, f"{laboratorio}|{filtros}")

    lojas = analise.por_loja(linhas)
    lojas["localizacao"] = [_localizacao(l) for l in lojas.to_dict("records")]
    ordem = comum.ordem_atual(_KEY)
    fatia, pagina = comum.pagina(analise.ordenar(lojas, *ordem), _KEY)

    tb.tabela(
        f"{_KEY}_tabela", _COLUNAS, [_linha(l) for l in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        acao="Ver detalhes",
    )

    clicada = tb.acao_clicada(f"{_KEY}_tabela")
    if clicada is not None:
        escolhida = lojas[lojas["loja_id"].astype(str) == clicada]
        if not escolhida.empty:
            loja = escolhida.iloc[0].to_dict()
            _dialog_detalhes(loja, linhas[linhas["loja_id"] == loja["loja_id"]], laboratorio)

    if not lojas.empty:
        ui.controles_paginacao(pagina, _KEY)
