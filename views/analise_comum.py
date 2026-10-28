"""Peças compartilhadas pelas telas Por Loja e Por Produto: faixa do filtro
de laboratório (obrigatório), linha de filtros, estado da ordenação, cache
do resultado calculado e as colunas/células da tabela de produtos (usada no
Por Produto e no Detalhes da loja — views/tabela.py).

Cache: o resultado de core/analise.py é guardado por (laboratório, período,
versão dos dados). O da Por Loja nova (core/analise_api.py, compras da API —
05/10/2026) também, com os arquivos da rede no lugar das compras do banco. Ordenar pelo cabeçalho, trocar de página, buscar ou abrir
o detalhe só reaproveitam esse resultado em memória — o banco só é
consultado quando o laboratório ou o período mudam, ou quando os dados
mudam (novo envio, EAN resolvido, tabela Gruppy nova…). A versão dos dados é
conferida no banco no máximo a cada 30 s; ações da aba Dados que mudam dados
chamam `invalidar()` pra valer na hora.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import analise, analise_api, dicas, revisar, theme, ui
from core.config import settings
from core.db import get_session
from core.queries import (PaginaResultado, lista_atendentes, lista_grupos_economicos, lista_lojas, lista_ufs,
                          rotulos_grupos)
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
def _laboratorios() -> list[str]:
    with get_session() as session:
        return analise.laboratorios_disponiveis(session)


@st.cache_data(ttl=300, show_spinner=False)
def _opcoes_filtros() -> dict:
    with get_session() as session:
        return {
            "ufs": lista_ufs(session), "atendentes": lista_atendentes(session),
            "grupos": lista_grupos_economicos(session), "lojas": lista_lojas(session),
            "rotulos_grupos": rotulos_grupos(session),
        }


# --- Compras da API (Por Loja, 05/10/2026) ------------------------------------

def _hoje() -> dt.date:
    """Hoje em Brasília. `RMC_HOJE` (AAAA-MM-DD) fixa a data — só o teste
    visual usa, pra "Último mês" não mudar com o calendário."""
    import os
    from zoneinfo import ZoneInfo

    if os.environ.get("RMC_HOJE"):
        return dt.date.fromisoformat(os.environ["RMC_HOJE"])
    return dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()


@st.cache_data(ttl=300, show_spinner=False)
def _meses_rede(hoje) -> tuple:
    """((mês, quando foi montado), …) dos meses fechados com arquivo da rede
    — conferido no Spaces no máximo a cada 5 min (a rotina monta de
    madrugada)."""
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    return tuple(analise_api.meses_disponiveis(do_ambiente(), Chaves(settings.pedido.prefixo), hoje).items())


@st.cache_data(ttl=30, show_spinner=False)
def _versao_banco_api() -> tuple:
    with get_session() as session:
        return analise_api.versao_banco(session)


@st.cache_data(max_entries=settings.analise.cache_combinacoes, show_spinner="Calculando a análise...")
def _calculo_api(laboratorio: str, meses: tuple, versao: tuple, meses_rede: tuple,
                 params: analise_api.Parametros) -> tuple[pd.DataFrame, pd.Series]:
    """`versao`, `meses_rede` e `params` só entram na chave do cache."""
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves
    from integrations import pedido_tela

    with get_session() as session:
        itens = analise_api.itens_gruppy(session, laboratorio)
        genericos = pedido_tela.genericos(session)
        eans = analise_api.eans_de_interesse(itens, genericos)
        vinc = analise_api.vinculos(session)
        lojas = analise.buscar_lojas(session, vinc["loja_id"].unique()) if not vinc.empty else \
            pd.DataFrame(columns=analise.COLUNAS_LOJA)
        categorias = analise_api.categorias_dos_eans(session, eans)
    # Só os meses do período: a mediana das regras de preço olha as compras
    # do mesmo período que a tela mostra.
    compras = analise_api.ler_compras(do_ambiente(), Chaves(settings.pedido.prefixo), list(meses), eans)
    precos = analise_api.precos_por_chave(itens, genericos)
    return analise_api.calcular(compras, vinc, lojas, precos, genericos, categorias, list(meses), params)


def meses_api(periodo_meses: int) -> list[str]:
    """Os meses do período (os N fechados mais recentes com arquivo)."""
    return analise_api.meses_do_periodo([m for m, _ in _meses_rede(_hoje())], periodo_meses)


def resultado_api(laboratorio: str, periodo_meses: int) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """(linhas por loja × produto × nota, bonificações por loja, meses)."""
    meses = meses_api(periodo_meses)
    if not meses:
        return pd.DataFrame(columns=analise_api.COLUNAS), pd.Series(dtype="int64"), []
    linhas, bonif = _calculo_api(laboratorio, tuple(meses), _versao_banco_api(), _meses_rede(_hoje()),
                                 _parametros_api())
    return linhas, bonif, meses


def resultado_api_anterior(laboratorio: str, periodo_meses: int):
    """(linhas, meses) dos N meses de calendário antes do período, se todos
    tiverem arquivo; senão None — só pra seta dos indicadores."""
    disponiveis = [m for m, _ in _meses_rede(_hoje())]
    anteriores = analise.meses_periodo_anterior(meses_api(periodo_meses), set(disponiveis))
    if anteriores is None:
        return None
    linhas, _ = _calculo_api(laboratorio, tuple(anteriores), _versao_banco_api(), _meses_rede(_hoje()),
                             _parametros_api())
    return linhas, anteriores


def historico_api(laboratorio: str) -> pd.DataFrame:
    """As notas de TODOS os meses com arquivo da rede (até 6) — pro gráfico
    de preço por mês do detalhe da Por Produto. Mesmo cache do cálculo."""
    meses = tuple(m for m, _ in _meses_rede(_hoje()))
    if not meses:
        return pd.DataFrame(columns=analise_api.COLUNAS)
    linhas, _ = _calculo_api(laboratorio, meses, _versao_banco_api(), _meses_rede(_hoje()), _parametros_api())
    return linhas


@st.cache_data(ttl=30, show_spinner=False)
def _parametros_api() -> analise_api.Parametros:
    return analise_api.parametros_da_configuracao()


def invalidar() -> None:
    """Descarta tudo que está guardado — chamar depois de qualquer ação que
    muda os dados da análise (envio Gruppy/Base/categorias, EAN resolvido, vínculo de loja)."""
    _laboratorios.clear()
    _opcoes_filtros.clear()
    _versao_banco_api.clear()
    _calculo_api.clear()


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def rotulo_periodo(qtd_meses: int) -> str:
    """Texto que se explica sozinho — o campo Período não tem mais rótulo.
    "Últimos N meses" = os N últimos meses FECHADOS com dado (na Por Loja,
    com arquivo de compras da rede — core/analise_api.py)."""
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


def filtros(key: str, laboratorio: str | None, exemplo_busca: str, escolha=None,
            categorias: list[str] | None = None) -> analise.Filtros:
    """Linha de filtros; desabilitada (mas visível) enquanto não há
    laboratório, pra deixar claro que dependem dele.

    Busca e Período sem rótulo à vista (o texto de exemplo e os valores
    "Último mês"/"Últimos 3 meses" já dizem o que são). O rótulo continua lá,
    escondido (`hidden`, não `collapsed`): ocupa a mesma altura dos rótulos
    de UF/Atendente/Grupo, então os cinco campos ficam na mesma linha, e
    leitor de tela ainda lê o nome do campo.

    `escolha` (views/loja_barra.py, 02/10/2026): com UMA loja escolhida na
    barra, ficam só busca e período (UF, atendente, grupo e "lojas
    específicas" não fazem sentido pra uma loja); com "Todas", as opções
    se limitam às lojas que o usuário pode ver.

    `categorias` (Por Produto, 05/10/2026): as categorias que existem no
    resultado — com elas, o campo Categoria entra na linha."""
    opcoes = _opcoes_filtros()
    bloqueado = laboratorio is None
    permitidas = escolha.permitidas if escolha is not None else None
    uma = escolha.loja if escolha is not None else None
    if uma is not None:
        # "collapsed": sem UF/Atendente/Grupo (de rótulo visível) ao lado, o
        # rótulo escondido só deixava um vão acima dos dois campos.
        if categorias is not None:
            f1, fc, f5 = st.columns([3.6, 1.6, 1.2])
        else:
            (f1, f5), fc = st.columns([5.2, 1.2]), None
        with f1:
            busca = st.text_input("Buscar", placeholder=exemplo_busca, key=f"{key}_busca", disabled=bloqueado,
                                  label_visibility="collapsed")
        escolhidas = _campo_categoria(fc, key, categorias, bloqueado, "collapsed")
        with f5:
            periodo = st.selectbox(
                "Período", options=settings.periodos_meses_opcoes, format_func=rotulo_periodo,
                key=f"{key}_periodo", disabled=bloqueado, label_visibility="collapsed",
            )
        return analise.Filtros(laboratorio=laboratorio, periodo_meses=periodo, busca=busca or None,
                               loja_ids=[uma["id"]], lojas_permitidas=permitidas, categorias=escolhidas)
    lojas_opcoes = opcoes["lojas"]
    if permitidas is not None:
        # Só o que existe nas lojas do usuário (Proprietário com "Todas").
        lojas_opcoes = [l for l in lojas_opcoes if l["id"] in set(permitidas)]
        opcoes = {
            "ufs": sorted({l["uf"] for l in lojas_opcoes if l.get("uf")}),
            "atendentes": sorted({l["atendente_comercial"] for l in lojas_opcoes if l.get("atendente_comercial")}),
            "grupos": sorted({l["grupo_economico"] for l in lojas_opcoes if l.get("grupo_economico")}),
        }
    if categorias is not None:
        f1, f2, f3, f4, fc, f5 = st.columns([1.9, 0.8, 1.3, 1.3, 1.4, 1.1])
    else:
        (f1, f2, f3, f4, f5), fc = st.columns([2.2, 1, 1.4, 1.6, 1.2]), None
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
            format_func=rotulo_grupo,
        )
    escolhidas = _campo_categoria(fc, key, categorias, bloqueado, "visible")
    with f5:
        periodo = st.selectbox(
            "Período", options=settings.periodos_meses_opcoes, format_func=rotulo_periodo,
            key=f"{key}_periodo", disabled=bloqueado, label_visibility="hidden",
        )
    loja_ids = None
    if not bloqueado:
        with st.expander("Selecionar lojas específicas (opcional)"):
            mapa = {f"{l['razao_social']} · {ui.formatar_cnpj(l['cnpj'])}": l["id"] for l in lojas_opcoes}
            selecionadas = st.multiselect("Lojas", options=list(mapa.keys()), key=f"{key}_lojas")
            loja_ids = [mapa[s] for s in selecionadas] or None
    return analise.Filtros(
        laboratorio=laboratorio, periodo_meses=periodo, uf=None if uf == _TODAS else uf, busca=busca or None,
        atendente_comercial=None if atendente == _TODOS else atendente,
        grupo_economico=None if grupo == _TODOS else grupo, loja_ids=loja_ids, lojas_permitidas=permitidas,
        categorias=escolhidas,
    )


def rotulo_grupo(sigla: str | None) -> str:
    """"GE787 · DROGARIA NOVA NACIONAL LTDA" (core/queries.py::rotulos_grupos)."""
    if not sigla or sigla in (_TODOS, _TODAS):
        return sigla or "—"
    return _opcoes_filtros().get("rotulos_grupos", {}).get(sigla, sigla)


def _campo_categoria(coluna, key: str, categorias: list[str] | None, bloqueado: bool, rotulo: str):
    """Multiselect de Categoria (Por Produto). Opção escolhida que sumiu das
    opções (outro laboratório) continua na lista: o multiselect descarta
    calado o valor que não está nas opções."""
    if categorias is None or coluna is None:
        return None
    ja = st.session_state.get(f"{key}_categoria", [])
    opcoes = sorted(set(categorias) | set(ja))
    with coluna:
        # Sem rótulo à vista (uma loja), o texto do campo diz o que ele é.
        escolhidas = st.multiselect("Categoria", opcoes, placeholder="Categoria" if rotulo == "collapsed" else "Todas",
                                    key=f"{key}_categoria",
                                    disabled=bloqueado, label_visibility=rotulo)
    return tuple(escolhidas) or None


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


def indicadores(visao: str, linhas: pd.DataFrame, laboratorio: str, filtros_: analise.Filtros,
                uma_loja: bool = False) -> None:
    """Cards do topo, sobre o resultado JÁ filtrado (busca, UF, lojas…). A
    seta compara com o período anterior de mesmo tamanho, com os mesmos
    filtros; sem os meses anteriores carregados, não há seta.

    `visao` = "loja" ou "produto": muda a ordem e a média (por loja × por
    produto) — cada tela abre pelo número do seu próprio assunto.

    `uma_loja` (Por Produto com uma loja na barra, Q9 de 02/10/2026): o card
    de lojas ("1 de 1") dá lugar a "Preço acima do laboratório".

    Resultado de core/analise_api.py (compras da API, 05/10/2026): "produto"
    = produto distinto (genérico ou EAN)."""
    calc = analise_api.indicadores
    atual = calc(linhas)
    anterior, meses_ant = None, None
    pacote = resultado_api_anterior(laboratorio, filtros_.periodo_meses)
    if pacote is not None:
        df_ant, meses_ant = pacote
        if visao == "produto":
            df_ant = analise_api.por_produto(df_ant)
            pacote = (df_ant, meses_ant)
        anterior = calc(analise.filtrar(df_ant, filtros_))
    dica = dicas.dica(f"Comparado com {rotulo_meses(meses_ant)}, com os mesmos filtros") if meses_ant else ""

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
    if uma_loja:
        def acima(df: pd.DataFrame) -> int:
            if df.empty:
                return 0
            mascara = pd.to_numeric(df["diferenca"], errors="coerce") > 0
            if "so_revisar" in df.columns:  # só notas "a revisar": o preço não é confiável (05/10/2026)
                mascara &= ~df["so_revisar"].astype(bool)
            return int(mascara.sum())
        n_acima = acima(linhas)
        ant_acima = acima(analise.filtrar(pacote[0], filtros_)) if pacote is not None else None
        lojas = {
            "icone": "alerta", "tom": "azul", "valor": ui.formatar_numero(n_acima),
            "label": "Preço acima do laboratório", "sub": f"de {ui.formatar_numero(len(linhas))} comprados",
            "variacao": analise.variacao(n_acima, ant_acima) if ant_acima is not None else None, "dica_variacao": dica,
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



def _celula_diferenca(valor) -> list:
    """Com sinal; negativo em vermelho (a loja já paga menos que o laboratório)."""
    if _vazio(valor):
        return tb.celula(tb.pedaco("—"))
    texto = ui.formatar_moeda(abs(float(valor)))
    if float(valor) < 0:
        return tb.celula(tb.pedaco(f"−{texto}", "negativo"))
    return tb.celula(tb.pedaco(texto))





# --- Notas fiscais (compras da API, 05/10/2026) ---------------------------

def _data(valor) -> str:
    texto = str(valor or "")[:10]
    return f"{texto[8:10]}/{texto[5:7]}/{texto[2:4]}" if len(texto) == 10 else "—"


def _numero(valor) -> str:
    return ui.formatar_numero(float(valor)) if valor is not None and not pd.isna(valor) else "—"


def _dica_compra(r: dict) -> str:
    """A nota por trás da linha, organizada (Q9 de 05/10/2026: só na dica)."""
    partes = [
        f"Nota fiscal {r['nro_nf'] or '—'}, de {_data(r['data'])}.",
        f"Fornecedor: {r['fornecedor'] or '—'}.",
        f"Quantidade: {dicas.qtd(r['quantidade'], 'unidade')}, a {moeda(r['preco_nota'])} por unidade na nota.",
    ]
    if r.get("ajustado"):
        partes.append(
            f"Preço usado: {moeda(r['preco_pago'])}, o custo de cadastro do sistema de vendas. O valor da "
            f"nota não combina com o preço de venda ({moeda(r['preco_venda'])}): provavelmente a nota foi "
            "lançada sem a fração.")
    return dicas.dica(*partes)


_DICA_REVISAR = {
    analise_api.MOTIVO_DIVERGENCIA: (
        "O custo de cadastro do sistema de vendas ({cadastro}) e a nota ({nota}) estão muito diferentes, e o preço "
        "de venda ({venda}) não mostra qual está certo."),
    analise_api.MOTIVO_ACIMA_VENDA: (
        "A nota ({nota} por unidade) passa do preço de venda ({venda}), e o cadastro ({cadastro}) também não "
        "combina com ele."),
    analise_api.MOTIVO_MEDIANA: (
        "A nota ({nota} por unidade) está muito diferente das outras compras deste produto na loja."),
}


def _dica_revisar(r: dict) -> str:
    texto = _DICA_REVISAR.get(r.get("revisar_motivo"), "O preço desta compra parece errado.").format(
        cadastro=moeda(r["custo_cadastro"]), nota=moeda(r["preco_nota"]), venda=moeda(r["preco_venda"]))
    return dicas.dica("Preço a conferir.", f"{texto} Esta compra não soma economia.",
                      revisar.acao(r.get("revisar_motivo")))


def colunas_notas(laboratorio: str, com_produto: bool = True) -> list[tb.ColunaTabela]:
    """Tabela de notas fiscais (Q16 de 05/10/2026): uma linha por produto e
    nota — detalhe da Por Loja e da Por Produto (as mesmas colunas e dicas).
    `com_produto=False` (detalhe da Por Produto): todas as linhas são do
    mesmo produto, que já está no topo do pop-up — a coluna só repetiria o
    nome (e o nome longo, quebrado, deixava o vão da coluna desigual)."""
    produto = [tb.ColunaTabela("nome_canonico", "Produto", "fit-content(230px)")] if com_produto else []
    return [
        *produto,
        tb.ColunaTabela("laboratorio", "Laboratório", "fit-content(150px)"),
        tb.ColunaTabela("data", "Compra", direita=True,
                        dica="Preço pago na nota e a data da nota.\n\nPasse o mouse no preço para ver a nota fiscal, "
                             "o fornecedor e a quantidade."),
        tb.ColunaTabela("preco_laboratorio", laboratorio, direita=True, numerica=True),
        tb.ColunaTabela("diferenca", "Diferença un.", direita=True, numerica=True),
        tb.ColunaTabela("quantidade", "Qtd.", direita=True, numerica=True),
        tb.ColunaTabela("economia", "Economia", direita=True, numerica=True),
    ]


def linha_nota(r: dict) -> dict:
    produto = [tb.pedaco(r["nome_canonico"], "forte quebra")]
    if r.get("categoria"):
        categoria = r["categoria"]
        produto.append(tb.pedaco(categoria.capitalize() if categoria.isupper() else categoria, "aux"))
    dica = _dica_compra(r)
    if r.get("a_revisar"):
        economia = tb.pedaco("A revisar", "selo-revisar", _dica_revisar(r))
    elif r["economia"] and r["economia"] > 0:
        economia = tb.pedaco(ui.formatar_moeda(float(r["economia"])), "selo-sucesso")
    else:
        economia = tb.pedaco("Já otimizada", "selo-neutro")
    return {
        "id": f"{r['loja_id']}-{r['produto_id']}-{r['nro_nf']}-{r['data']}-{r['fornecedor']}",
        "classe": "revisar" if r.get("a_revisar") else None,
        "celulas": {
            "nome_canonico": tb.celula(*produto),
            "laboratorio": tb.celula(tb.pedaco(r.get("laboratorio") or "—")),
            "data": tb.celula(tb.pedaco(moeda(r["preco_pago"]), dica=dica), tb.pedaco(_data(r["data"]), "aux", dica)),
            "preco_laboratorio": tb.celula(tb.pedaco(moeda(r["preco_laboratorio"]))),
            "diferenca": _celula_diferenca(r["diferenca"]),
            "quantidade": tb.celula(tb.pedaco(_numero(r["quantidade"]))),
            "economia": tb.celula(economia),
        },
    }


def texto_markdown(texto: str) -> str:
    """Pra montar frases com vários valores em R$ numa mesma linha de markdown."""
    return _sem_matematica(texto)


def aviso_sem_laboratorio() -> None:
    st.info("Selecione um laboratório para ver a análise.")
