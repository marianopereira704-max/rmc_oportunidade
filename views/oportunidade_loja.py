"""Análise de Oportunidade · Por Loja (só ADM) — uma linha por loja, economia
= soma das compras acima da tabela Gruppy do laboratório escolhido, no
período. Desde 05/10/2026 pelas compras da API do GPS e com todo produto da
tabela, não só genérico (regras em core/analise_api.py). O detalhe abre as
compras da loja, uma linha por produto e nota fiscal. Ordenação, paginação
e detalhe rodam sobre o resultado guardado em memória
(views/analise_comum.py); a tabela é o componente de views/tabela.py."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import analise, analise_api, auth, dicas, theme, ui
from core.config import settings
from views import analise_comum as comum
from views import loja_barra
from views import popups
from views import tabela as tb

_KEY = "op_loja"

# Ordem fixa dos responsáveis, uma linha cada; posição vazia = "—". Sem
# prefixo ("CI:", "CF:"): quem é quem vem da posição — por isso a dica no "?".
_RESPONSAVEIS = ("consultor_interno", "consultor_farma", "atendente_comercial")
_DICA_RESPONSAVEL = dicas.dica(
    "Em ordem, uma linha cada:",
    "1. Consultor interno\n2. Consultor farma\n3. Atendente comercial",
    "O traço \"—\" indica posição sem responsável.",
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


def _linha(loja: dict, destaque_id: int | None = None) -> dict:
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
        # A loja escolhida na barra lateral, destacada na visão da rede.
        "classe": "destaque" if destaque_id is not None and loja["loja_id"] == destaque_id else None,
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


@popups.popup("Detalhes da loja", "detalhe")
def _dialog_detalhes(loja: dict, produtos: pd.DataFrame, laboratorio: str, bonificadas: int) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**CNPJ**  \n{ui.formatar_cnpj(loja['cnpj'])}")
        st.markdown(f"**Razão Social**  \n{loja['razao_social']}")
        st.markdown(f"**UF / Cidade**  \n{loja['uf']} · {loja['cidade']}")
        st.markdown(f"**Grupo econômico**  \n{comum.rotulo_grupo(loja.get('grupo_economico'))}")
    with c2:
        # Mesma ordem da coluna Responsável da tabela, aqui com o nome inteiro.
        st.markdown(f"**Consultor interno**  \n{loja.get('consultor_interno') or '—'}")
        st.markdown(f"**Consultor farma**  \n{loja.get('consultor_farma') or '—'}")
        st.markdown(f"**Atendente comercial**  \n{loja.get('atendente_comercial') or '—'}")

    theme.card_destaque(f"Economia perdida nesse período — {laboratorio}", ui.formatar_moeda(float(loja["economia"])))

    theme.titulo_secao("Compras desta loja no período")
    chave = f"{_KEY}_detalhe_{loja['loja_id']}"
    ordenadas = analise.ordenar(produtos, *comum.ordem_atual(chave))
    tb.tabela(
        f"{chave}_tabela", comum.colunas_notas(laboratorio), [comum.linha_nota(r) for r in ordenadas.to_dict("records")],
        comum.ordem_atual(chave), ao_ordenar=lambda coluna: comum.alternar_ordem(chave, coluna.campo, coluna.numerica),
        vazio=("Nenhuma compra comparável", "Nenhuma compra desta loja no período tem preço do laboratório"),
        densa=True,
    )

    revisar = int(produtos["a_revisar"].sum()) if not produtos.empty else 0
    notas = []
    if bonificadas:
        notas.append(
            f"{bonificadas} compra(s) bonificada(s) (abaixo de {ui.formatar_moeda(settings.analise.limite_bonificacao)}) "
            "ficaram fora da conta"
        )
    if revisar:
        notas.append(f"{revisar} compra(s) com preço \"a revisar\" não somam economia")
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

    todas, bonificacoes, meses = comum.resultado_api(laboratorio, filtros.periodo_meses)
    if not meses:
        st.info("As compras da rede ainda não foram montadas. A rotina da madrugada junta as compras de todas as "
                "lojas por mês fechado; cada mês aparece a partir do dia 6 do mês seguinte.")
        return
    linhas = analise.filtrar(todas, filtros)
    with area_indicadores:
        comum.indicadores("loja", linhas, laboratorio, filtros)
    st.caption(f"Compras de {comum.rotulo_meses(meses)}, pela data da nota fiscal.")

    ui.resetar_pagina_se_filtro_mudou(_KEY, f"{laboratorio}|{filtros}")
    # Por Loja é a visão da REDE (só ADM): a loja da barra lateral não filtra,
    # fica destacada (Q1 de 02/10/2026).
    escolhida = loja_barra.escolha(auth.usuario_atual()).loja
    if escolhida is not None:
        st.caption(f"Visão da rede: esta tela mostra todas as lojas. A loja escolhida na barra lateral "
                   f"({loja_barra.rotulo(escolhida)}) aparece destacada.")

    lojas = analise_api.por_loja(linhas)
    lojas["localizacao"] = [_localizacao(l) for l in lojas.to_dict("records")]
    ordem = comum.ordem_atual(_KEY)
    fatia, pagina = comum.pagina(analise.ordenar(lojas, *ordem), _KEY)

    tb.tabela(
        f"{_KEY}_tabela", _COLUNAS, [_linha(l, escolhida["id"] if escolhida else None) for l in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        acao="Ver detalhes",
    )

    clicada = tb.acao_clicada(f"{_KEY}_tabela")
    if clicada is not None:
        escolhida = lojas[lojas["loja_id"].astype(str) == clicada]
        if not escolhida.empty:
            loja = escolhida.iloc[0].to_dict()
            _dialog_detalhes(loja, linhas[linhas["loja_id"] == loja["loja_id"]], laboratorio,
                             int(bonificacoes.get(loja["loja_id"], 0)))

    if not lojas.empty:
        ui.controles_paginacao(pagina, _KEY)
