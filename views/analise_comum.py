"""Peças compartilhadas pelas telas Por Loja e Por Produto: faixa do filtro
de laboratório (obrigatório), linha de filtros, cabeçalho ordenável, cache
do resultado calculado e formatação das células.

Cache: o resultado de core/analise.py é guardado por (laboratório, período,
versão dos dados). Ordenar pelo cabeçalho, trocar de página, buscar ou abrir
o detalhe só reaproveitam esse resultado em memória — o banco só é
consultado quando o laboratório ou o período mudam, ou quando os dados
mudam (novo envio, EAN resolvido, tabela Gruppy nova…). A versão dos dados é
conferida no banco no máximo a cada 30 s; ações da aba Dados que mudam dados
chamam `invalidar()` pra valer na hora.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core import analise, theme, ui
from core.config import settings
from core.db import get_session
from core.queries import PaginaResultado, lista_atendentes, lista_grupos_economicos, lista_lojas, lista_ufs

_TODAS = "Todas"
_TODOS = "Todos"
# Guardado fora da key do widget: o Streamlit descarta o estado de um
# widget que deixa de ser desenhado, e as duas telas nunca estão abertas
# juntas — sem isso, a escolha se perderia ao trocar de tela.
_LABORATORIO_ESCOLHIDO = "analise_laboratorio_escolhido"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def _versao_dados() -> tuple:
    with get_session() as session:
        return analise.versao_dados(session)


@st.cache_data(max_entries=settings.analise.cache_combinacoes, show_spinner="Calculando a análise...")
def _resultado_versionado(laboratorio: str, periodo_meses: int, versao: tuple) -> pd.DataFrame:
    # `versao` não é usada no corpo: ela existe só pra entrar na chave do
    # cache — dados mudaram, versão mudou, resultado guardado não serve mais.
    with get_session() as session:
        return analise.carregar(session, laboratorio, periodo_meses)


def resultado(laboratorio: str, periodo_meses: int) -> pd.DataFrame:
    return _resultado_versionado(laboratorio, periodo_meses, _versao_dados())


@st.cache_data(ttl=30, show_spinner=False)
def _laboratorios() -> list[str]:
    with get_session() as session:
        return analise.laboratorios_disponiveis(session)


@st.cache_data(ttl=300, show_spinner=False)
def _opcoes_filtros() -> dict:
    with get_session() as session:
        return {
            "ufs": lista_ufs(session), "atendentes": lista_atendentes(session),
            "grupos": lista_grupos_economicos(session), "lojas": lista_lojas(session),
        }


def invalidar() -> None:
    """Descarta tudo que está guardado — chamar depois de qualquer ação que
    muda os dados da análise (envio GPS/Gruppy/Base, EAN ou CNPJ resolvido)."""
    _versao_dados.clear()
    _resultado_versionado.clear()
    _laboratorios.clear()
    _opcoes_filtros.clear()


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def _rotulo_periodo(qtd_meses: int) -> str:
    return f"{qtd_meses} mês" if qtd_meses == 1 else f"{qtd_meses} meses"


def faixa_laboratorio(key: str) -> str | None:
    """Filtro principal: sem laboratório escolhido, não há análise."""
    laboratorios = _laboratorios()
    escolhido = st.session_state.get(_LABORATORIO_ESCOLHIDO)
    if escolhido not in laboratorios:
        escolhido = None

    def _guardar() -> None:
        st.session_state[_LABORATORIO_ESCOLHIDO] = st.session_state[f"{key}_laboratorio"]

    with st.container(key="faixa-laboratorio"):
        col_titulo, col_campo = st.columns([1, 2.4], vertical_alignment="center")
        col_titulo.markdown(
            '<div class="rmc-faixa-titulo">Laboratório</div>'
            '<div class="rmc-faixa-sub">Escolha a tabela de preços para liberar a análise</div>',
            unsafe_allow_html=True,
        )
        with col_campo:
            if not laboratorios:
                st.caption("Nenhuma tabela Gruppy ativa — suba uma na aba Dados.")
                return None
            st.selectbox(
                "Laboratório", options=laboratorios,
                index=laboratorios.index(escolhido) if escolhido else None,
                placeholder="Selecione o laboratório", key=f"{key}_laboratorio",
                on_change=_guardar, label_visibility="collapsed",
            )
    return st.session_state.get(f"{key}_laboratorio")


def filtros(key: str, laboratorio: str | None, rotulo_busca: str) -> analise.Filtros:
    """Linha de filtros de sempre; desabilitada (mas visível) enquanto não há
    laboratório, pra deixar claro que dependem dele."""
    opcoes = _opcoes_filtros()
    bloqueado = laboratorio is None
    f1, f2, f3, f4, f5 = st.columns([2.2, 1, 1.4, 1.6, 1.2])
    with f1:
        busca = st.text_input(rotulo_busca, key=f"{key}_busca", disabled=bloqueado)
    with f2:
        uf = st.selectbox("UF", options=[_TODAS] + opcoes["ufs"], key=f"{key}_uf", disabled=bloqueado)
    with f3:
        atendente = st.selectbox(
            "Atendente comercial", options=[_TODOS] + opcoes["atendentes"], key=f"{key}_atendente", disabled=bloqueado,
        )
    with f4:
        grupo = st.selectbox(
            "Grupo econômico", options=[_TODOS] + opcoes["grupos"], key=f"{key}_grupo", disabled=bloqueado,
        )
    with f5:
        periodo = st.selectbox(
            "Período", options=settings.periodos_meses_opcoes, format_func=_rotulo_periodo,
            key=f"{key}_periodo", disabled=bloqueado,
        )
    loja_ids = None
    if not bloqueado:
        with st.expander("Selecionar lojas específicas (opcional)"):
            mapa = {f"{l['razao_social']} · {ui.formatar_cnpj(l['cnpj'])}": l["id"] for l in opcoes["lojas"]}
            selecionadas = st.multiselect("Lojas", options=list(mapa.keys()), key=f"{key}_lojas")
            loja_ids = [mapa[s] for s in selecionadas] or None
    return analise.Filtros(
        laboratorio=laboratorio, periodo_meses=periodo, uf=None if uf == _TODAS else uf, busca=busca or None,
        atendente_comercial=None if atendente == _TODOS else atendente,
        grupo_economico=None if grupo == _TODOS else grupo, loja_ids=loja_ids,
    )


def card_conjunto(df: pd.DataFrame, filtros_: analise.Filtros) -> None:
    """Economia somada do conjunto filtrado (grupo econômico ou 2+ lojas)."""
    if filtros_.grupo_economico or (filtros_.loja_ids and len(filtros_.loja_ids) >= 2):
        rotulo = filtros_.grupo_economico or f"{len(filtros_.loja_ids)} lojas selecionadas"
        theme.card_destaque(f"Economia perdida total — {rotulo}", ui.formatar_moeda(float(df["economia"].sum())))


# ---------------------------------------------------------------------------
# Ordenação e paginação
# ---------------------------------------------------------------------------

@dataclass
class Coluna:
    rotulo: str
    campo: str | None  # None = coluna não ordenável (ex: botões)
    numerica: bool = False


def cabecalho_ordenavel(colunas: list[Coluna], larguras: list[float], key: str) -> tuple[str, bool]:
    """Cabeçalho em que cada título é clicável: o primeiro clique numa coluna
    ordena (números do maior pro menor, texto de A a Z), o seguinte inverte.
    Padrão: Economia, maior primeiro. O clique volta pra página 1. Devolve
    (campo, crescente) já atualizado — a troca acontece no callback, antes
    da tela ser redesenhada, então a seta e as linhas saem na ordem nova."""
    chave_estado = f"{key}_ordem"
    campo_atual, crescente_atual = st.session_state.get(chave_estado, ("economia", False))

    def _alternar(coluna: Coluna) -> None:
        campo, crescente = st.session_state.get(chave_estado, ("economia", False))
        if coluna.campo == campo:
            novo = (campo, not crescente)
        else:
            novo = (coluna.campo, not coluna.numerica)
        st.session_state[chave_estado] = novo
        st.session_state[f"{key}_pagina"] = 1

    with st.container(key=f"cabecalho-{key}"):
        for coluna, celula in zip(colunas, st.columns(larguras)):
            if coluna.campo is None:
                if coluna.rotulo:
                    celula.markdown(f"**{coluna.rotulo}**")
                continue
            seta = (" ▲" if crescente_atual else " ▼") if coluna.campo == campo_atual else ""
            celula.button(f"{coluna.rotulo}{seta}", key=f"{key}_ord_{coluna.campo}", on_click=_alternar, args=(coluna,))
    return st.session_state.get(chave_estado, ("economia", False))


def pagina(df: pd.DataFrame, key: str) -> tuple[pd.DataFrame, PaginaResultado]:
    tamanho = ui.tamanho_pagina_atual(key)
    total = len(df)
    total_paginas = max(1, -(-total // tamanho))
    numero = min(max(1, ui.pagina_atual(key)), total_paginas)
    inicio = (numero - 1) * tamanho
    fatia = df.iloc[inicio:inicio + tamanho]
    return fatia, PaginaResultado(linhas=[], total_linhas=total, pagina=numero, tamanho_pagina=tamanho)


# ---------------------------------------------------------------------------
# Células
# ---------------------------------------------------------------------------

def _sem_matematica(texto: str) -> str:
    """O Markdown do Streamlit lê o que fica entre dois "$" como fórmula
    (LaTeX): "R$ 5,15 … R$ 25,59" virava matemática e o HTML aparecia cru.
    A entidade HTML do cifrão mostra o mesmo caractere sem disparar isso."""
    return texto.replace("$", "&#36;")


def _vazio(valor) -> bool:
    return valor is None or (isinstance(valor, float) and pd.isna(valor))


def moeda(valor) -> str:
    return "—" if _vazio(valor) else ui.formatar_moeda(float(valor))


def diferenca(valor) -> str:
    """Com sinal; negativo em vermelho (a loja já paga menos que o laboratório)."""
    if _vazio(valor):
        return "—"
    texto = _sem_matematica(ui.formatar_moeda(abs(float(valor))))
    if float(valor) < 0:
        return f'<span class="rmc-negativo">−{texto}</span>'
    return texto


def preco_pago(linha: dict) -> str:
    """Preço + marcador quando ele não veio direto do VlrUnitario. A dica
    (title) mostra o VlrUnitario original, pra ninguém estranhar o valor
    diferente da planilha."""
    texto = _sem_matematica(moeda(linha["preco_pago"]))
    fonte = linha["fonte"]
    original = _sem_matematica(moeda(linha["vlr_unitario_original"]))
    if fonte == analise.FONTE_CMV:
        dica = f"VlrUnitario {original} fora do padrão — usado o custo CMV (Fat × %CMV ÷ QTD)"
        return f'{texto}<br><span title="{dica}">{theme.badge("ajustado", "sugestao")}</span>'
    if fonte == analise.FONTE_CUSTO_MEDIO:
        dica = f"VlrUnitario {original} fora do padrão — usado o R$ Custo médio da planilha"
        return f'{texto}<br><span title="{dica}">{theme.badge("ajustado", "sugestao")}</span>'
    if fonte == analise.FONTE_FORA:
        dica = "Nenhum preço da planilha ficou a ±50% do laboratório — provável erro de cadastro; economia não contabilizada"
        return f'{texto}<br><span title="{dica}">{theme.badge("a revisar", "neutro")}</span>'
    return texto


def preco_medio(linha: dict) -> str:
    if _vazio(linha["preco_medio"]):
        return "—"
    texto = _sem_matematica(moeda(linha["preco_medio"]))
    meses = int(linha["meses_preco_medio"] or 0)
    if meses and meses < settings.analise.meses_preco_medio:
        texto += f' <span class="rmc-muted">({meses}m)</span>'
    return texto


def economia(linha: dict) -> str:
    if linha.get("fonte") == analise.FONTE_FORA:
        return theme.badge("Não contabilizada", "neutro")
    valor = linha["economia"]
    if valor and valor > 0:
        return theme.badge(_sem_matematica(ui.formatar_moeda(float(valor))), "sucesso")
    return theme.badge("Já otimizada", "neutro")


def texto_markdown(texto: str) -> str:
    """Pra montar frases com vários valores em R$ numa mesma linha de markdown."""
    return _sem_matematica(texto)


def aviso_sem_laboratorio() -> None:
    st.info("Selecione um laboratório para ver a análise.")
