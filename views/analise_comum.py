"""Peças compartilhadas pelas telas Por Loja e Por Produto: faixa do filtro
de laboratório (obrigatório), linha de filtros, estado da ordenação, cache
do resultado calculado e as colunas/células da tabela de produtos (usada no
Por Produto e no Detalhes da loja — views/tabela.py).

Cache: o resultado de core/analise.py é guardado por (laboratório, período,
versão dos dados). Ordenar pelo cabeçalho, trocar de página, buscar ou abrir
o detalhe só reaproveitam esse resultado em memória — o banco só é
consultado quando o laboratório ou o período mudam, ou quando os dados
mudam (novo envio, EAN resolvido, tabela Gruppy nova…). A versão dos dados é
conferida no banco no máximo a cada 30 s; ações da aba Dados que mudam dados
chamam `invalidar()` pra valer na hora.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import analise, theme, ui
from core.config import settings
from core.db import get_session
from core.queries import PaginaResultado, lista_atendentes, lista_grupos_economicos, lista_lojas, lista_ufs
from views import tabela as tb

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


@st.cache_data(max_entries=settings.analise.cache_combinacoes, show_spinner=False)
def _anterior_versionado(laboratorio: str, periodo_meses: int, versao: tuple):
    with get_session() as session:
        return analise.carregar_anterior(session, laboratorio, periodo_meses)


def resultado_anterior(laboratorio: str, periodo_meses: int):
    """(resultado, meses) do período anterior de mesmo tamanho, ou None —
    só pra seta dos indicadores. Mesmo cache por versão dos dados."""
    return _anterior_versionado(laboratorio, periodo_meses, _versao_dados())


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
    _anterior_versionado.clear()
    _laboratorios.clear()
    _opcoes_filtros.clear()


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def rotulo_periodo(qtd_meses: int) -> str:
    """Texto que se explica sozinho — o campo Período não tem mais rótulo.
    "Últimos N meses" = os N últimos meses CARREGADOS (ano_mes com envio
    GPS), não os N meses do calendário (core/analise.py)."""
    return "Último mês" if qtd_meses == 1 else f"Últimos {qtd_meses} meses"


# Frasco de laboratório (Erlenmeyer), traço em currentColor.
_FRASCO = (
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 3h6M10 3v6.2L4.6 18.4A1.7 1.7 0 0 0 '
    '6.1 21h11.8a1.7 1.7 0 0 0 1.5-2.6L14 9.2V3M7.2 15h9.6" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def faixa_laboratorio(key: str) -> str | None:
    """Filtro principal: sem laboratório escolhido, não há análise."""
    laboratorios = _laboratorios()
    escolhido = st.session_state.get(_LABORATORIO_ESCOLHIDO)
    if escolhido not in laboratorios:
        escolhido = None

    def _guardar() -> None:
        st.session_state[_LABORATORIO_ESCOLHIDO] = st.session_state[f"{key}_laboratorio"]

    with st.container(key="faixa-laboratorio"):
        # Coluna do título larga o bastante pro subtítulo caber numa linha em
        # 1280px — com [1, 2.4] ele quebrava e a faixa ficava com 84px.
        col_titulo, col_campo = st.columns([1.45, 2], vertical_alignment="center")
        col_titulo.markdown(
            f'<div class="rmc-faixa-cab"><span class="rmc-faixa-icone">{_FRASCO}</span><div>'
            '<div class="rmc-faixa-titulo">Laboratório</div>'
            '<div class="rmc-faixa-sub">Escolha a tabela de preços para liberar a análise</div>'
            '</div></div>',
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


def filtros(key: str, laboratorio: str | None, exemplo_busca: str) -> analise.Filtros:
    """Linha de filtros; desabilitada (mas visível) enquanto não há
    laboratório, pra deixar claro que dependem dele.

    Busca e Período sem rótulo à vista (o texto de exemplo e os valores
    "Último mês"/"Últimos 3 meses" já dizem o que são). O rótulo continua lá,
    escondido (`hidden`, não `collapsed`): ocupa a mesma altura dos rótulos
    de UF/Atendente/Grupo, então os cinco campos ficam na mesma linha, e
    leitor de tela ainda lê o nome do campo."""
    opcoes = _opcoes_filtros()
    bloqueado = laboratorio is None
    f1, f2, f3, f4, f5 = st.columns([2.2, 1, 1.4, 1.6, 1.2])
    with f1:
        busca = st.text_input(
            "Buscar", placeholder=exemplo_busca, key=f"{key}_busca", disabled=bloqueado, label_visibility="hidden",
        )
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
            "Período", options=settings.periodos_meses_opcoes, format_func=rotulo_periodo,
            key=f"{key}_periodo", disabled=bloqueado, label_visibility="hidden",
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


_MESES_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def rotulo_meses(meses: list[str]) -> str:
    """["2026-07"] → "jul/2026"; ["2026-05", …, "2026-07"] → "mai–jul/2026";
    virada de ano → "nov/2025–jan/2026"."""
    def fmt(ano_mes: str, com_ano: bool = True) -> str:
        ano, mes = ano_mes.split("-")
        return f"{_MESES_ABREV[int(mes) - 1]}/{ano}" if com_ano else _MESES_ABREV[int(mes) - 1]
    inicio, fim = min(meses), max(meses)
    if inicio == fim:
        return fmt(fim)
    return f"{fmt(inicio, inicio[:4] != fim[:4])}–{fmt(fim)}"


def indicadores(visao: str, linhas: pd.DataFrame, laboratorio: str, filtros_: analise.Filtros) -> None:
    """Cards do topo, sobre o resultado JÁ filtrado (busca, UF, lojas…). A
    seta compara com o período anterior de mesmo tamanho, com os mesmos
    filtros; sem os meses anteriores carregados, não há seta.

    `visao` = "loja" ou "produto": muda a ordem e a média (por loja × por
    produto) — cada tela abre pelo número do seu próprio assunto."""
    atual = analise.indicadores(linhas)
    anterior, meses_ant = None, None
    pacote = resultado_anterior(laboratorio, filtros_.periodo_meses)
    if pacote is not None:
        df_ant, meses_ant = pacote
        anterior = analise.indicadores(analise.filtrar(df_ant, filtros_))
    dica = f"Comparado com {rotulo_meses(meses_ant)}, com os mesmos filtros" if meses_ant else ""

    def var(campo: str) -> float | None:
        return analise.variacao(getattr(atual, campo), getattr(anterior, campo)) if anterior else None

    def moeda_ou_traco(valor: float | None) -> str:
        return "—" if valor is None else ui.formatar_moeda(valor)

    lojas = {
        "icone": "loja", "tom": "azul", "valor": ui.formatar_numero(atual.lojas_com_oportunidade),
        "label": "Lojas com oportunidade", "sub": f"de {ui.formatar_numero(atual.lojas_analisadas)} analisadas",
        "variacao": var("lojas_com_oportunidade"), "dica_variacao": dica,
    }
    economia = {
        "icone": "moeda", "tom": "verde", "valor": ui.formatar_moeda(atual.economia),
        "label": "Economia potencial", "sub": f"contra {laboratorio}",
        "variacao": var("economia"), "dica_variacao": dica,
    }
    produtos = {
        "icone": "produto", "tom": "roxo", "valor": ui.formatar_numero(atual.produtos_com_oportunidade),
        "label": "Produtos com oportunidade", "sub": f"de {ui.formatar_numero(atual.produtos_analisados)} analisados",
        "variacao": var("produtos_com_oportunidade"), "dica_variacao": dica,
    }
    if visao == "loja":
        media = {
            "icone": "media", "tom": "azul", "valor": moeda_ou_traco(atual.economia_media_loja),
            "label": "Economia média por loja",
            "sub": f"nas {ui.formatar_numero(atual.lojas_com_oportunidade)} com oportunidade",
            "variacao": var("economia_media_loja"), "dica_variacao": dica,
        }
        theme.kpis([lojas, economia, produtos, media])
    else:
        media = {
            "icone": "media", "tom": "azul", "valor": moeda_ou_traco(atual.economia_media_produto),
            "label": "Economia média por produto",
            "sub": f"nos {ui.formatar_numero(atual.produtos_com_oportunidade)} com oportunidade",
            "variacao": var("economia_media_produto"), "dica_variacao": dica,
        }
        theme.kpis([produtos, economia, lojas, media])


# ---------------------------------------------------------------------------
# Ordenação e paginação
# ---------------------------------------------------------------------------

def ordem_atual(key: str) -> tuple[str, bool]:
    """(campo, crescente) da tabela `key`. Padrão: Economia, maior primeiro."""
    return st.session_state.get(f"{key}_ordem", ("economia", False))


def alternar_ordem(key: str, campo: str, numerica: bool) -> None:
    """Clique num título: o primeiro clique numa coluna ordena (números do
    maior pro menor, texto de A a Z), o seguinte inverte. Volta pra página 1.
    Chamar em callback — a troca acontece antes da tela ser redesenhada,
    então a seta e as linhas já saem na ordem nova."""
    atual, crescente = ordem_atual(key)
    st.session_state[f"{key}_ordem"] = (campo, not crescente) if campo == atual else (campo, not numerica)
    st.session_state[f"{key}_pagina"] = 1


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


def colunas_produto(laboratorio: str) -> list[tb.ColunaTabela]:
    """As 8 colunas da tabela de produtos. O título da coluna de preço é o
    nome do laboratório escolhido (como foi digitado no upload da Gruppy).
    Pesos acertados em 1280 px (conteúdo ~870 px): "Preço médio (3m)" e
    "Diferença un." cabem numa linha; o nome do produto quebra se precisar."""
    return [
        tb.ColunaTabela("nome_canonico", "Produto", 2.1),
        tb.ColunaTabela("laboratorio", "Laboratório", 1.1),
        tb.ColunaTabela("preco_pago", "Preço pago", 0.95, direita=True, numerica=True),
        tb.ColunaTabela("preco_laboratorio", laboratorio, 0.95, direita=True, numerica=True),
        tb.ColunaTabela("diferenca", "Diferença un.", 1.05, direita=True, numerica=True),
        tb.ColunaTabela("quantidade", "Qtd.", 0.55, direita=True, numerica=True),
        tb.ColunaTabela(
            "preco_medio", f"Preço médio ({settings.analise.meses_preco_medio}m)", 1.4, direita=True, numerica=True,
        ),
        tb.ColunaTabela("economia", "Economia", 1.2, direita=True, numerica=True),
    ]


def _celula_preco_pago(linha: dict) -> list:
    """Preço + marcador quando ele não veio direto do VlrUnitario. A dica
    mostra o VlrUnitario original, pra ninguém estranhar o valor diferente
    da planilha."""
    preco = tb.pedaco(moeda(linha["preco_pago"]))
    original = moeda(linha["vlr_unitario_original"])
    fonte = linha["fonte"]
    if fonte == analise.FONTE_CMV:
        dica = f"VlrUnitario {original} fora do padrão — usado o custo CMV (Fat × %CMV ÷ QTD)"
        return tb.celula(preco, tb.pedaco("ajustado", "selo-sugestao", dica))
    if fonte == analise.FONTE_CUSTO_MEDIO:
        dica = f"VlrUnitario {original} fora do padrão — usado o R$ Custo médio da planilha"
        return tb.celula(preco, tb.pedaco("ajustado", "selo-sugestao", dica))
    if fonte == analise.FONTE_FORA:
        dica = "Nenhum preço da planilha ficou a ±50% do laboratório — provável erro de cadastro; economia não contabilizada"
        return tb.celula(preco, tb.pedaco("a revisar", "selo-neutro", dica))
    return tb.celula(preco)


def _celula_diferenca(valor) -> list:
    """Com sinal; negativo em vermelho (a loja já paga menos que o laboratório)."""
    if _vazio(valor):
        return tb.celula(tb.pedaco("—"))
    texto = ui.formatar_moeda(abs(float(valor)))
    if float(valor) < 0:
        return tb.celula(tb.pedaco(f"−{texto}", "negativo"))
    return tb.celula(tb.pedaco(texto))


def _celula_preco_medio(linha: dict) -> list:
    if _vazio(linha["preco_medio"]):
        return tb.celula(tb.pedaco("—"))
    pedacos = [tb.pedaco(moeda(linha["preco_medio"]))]
    meses = int(linha["meses_preco_medio"] or 0)
    if meses and meses < settings.analise.meses_preco_medio:
        pedacos.append(tb.pedaco(f"({meses}m)", "aux"))
    return tb.celula(pedacos)


def _celula_economia(linha: dict) -> list:
    if linha.get("fonte") == analise.FONTE_FORA:
        return tb.celula(tb.pedaco("Não contabilizada", "selo-neutro"))
    valor = linha["economia"]
    if valor and valor > 0:
        return tb.celula(tb.pedaco(ui.formatar_moeda(float(valor)), "selo-sucesso"))
    return tb.celula(tb.pedaco("Já otimizada", "selo-neutro"))


def linha_produto(linha: dict, com_loja: bool) -> dict:
    """Uma linha da tabela de produtos. `com_loja`: no Por Produto, a loja
    (razão social · CNPJ) vai embaixo do produto; no Detalhes da loja, não —
    a loja já é o assunto do popup."""
    produto = [tb.pedaco(linha["nome_canonico"], "forte")]
    if com_loja:
        produto.append([
            tb.pedaco(f"{(linha.get('razao_social') or '').strip()} ·", "aux"),
            tb.pedaco(ui.formatar_cnpj(linha["cnpj"]), "aux inteiro"),
        ])
    return {
        "id": f"{linha['loja_id']}-{linha['base_generico_id']}",
        "celulas": {
            "nome_canonico": tb.celula(*produto),
            "laboratorio": tb.celula(tb.pedaco(linha.get("laboratorio") or "—")),
            "preco_pago": _celula_preco_pago(linha),
            "preco_laboratorio": tb.celula(tb.pedaco(moeda(linha["preco_laboratorio"]))),
            "diferenca": _celula_diferenca(linha["diferenca"]),
            "quantidade": tb.celula(tb.pedaco(ui.formatar_numero(linha["quantidade"]))),
            "preco_medio": _celula_preco_medio(linha),
            "economia": _celula_economia(linha),
        },
    }


def tabela_produtos(
    key: str, key_ordem: str, registros: list[dict], laboratorio: str, com_loja: bool,
    acao: str | None = None,
    vazio: tuple[str, str] = ("Nenhuma oportunidade encontrada", "Tente alterar os filtros"),
) -> None:
    """Tabela de produtos (8 colunas) — já ordenada/paginada por quem chama."""
    tb.tabela(
        key, colunas_produto(laboratorio), [linha_produto(r, com_loja) for r in registros], ordem_atual(key_ordem),
        ao_ordenar=lambda coluna: alternar_ordem(key_ordem, coluna.campo, coluna.numerica),
        acao=acao, vazio=vazio, densa=True,
    )


def texto_markdown(texto: str) -> str:
    """Pra montar frases com vários valores em R$ numa mesma linha de markdown."""
    return _sem_matematica(texto)


def aviso_sem_laboratorio() -> None:
    st.info("Selecione um laboratório para ver a análise.")
