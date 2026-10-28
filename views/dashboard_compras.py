"""Dashboard · Compras (27/10/2026; design em docs/specs/2026-10-27-dashboard-compras-design.md e protótipo aprovado no
mesmo dia). A página diz se a loja compra bem: quanto da dotação do mês já foi usado, se compra mais ou menos do que
vende, quanto do que comprou foi sem demanda ou acima do necessário, e quais produtos causaram isso.

De cima para baixo, em largura inteira:
1. pílulas de período (Mês atual, Mês passado, Últimos 3 meses) e, à direita, de quando são as notas;
2. 4 cards: Dotação (% consumido, saldo e a barra), Compra × Venda, Compras não otimizadas e Eficiência em compras;
3. Compras por categoria: as 5 categorias com a setinha IQVIA › FEBRAFAR › CMED;
4. lado a lado: Distribuição das compras por categoria (barras 100%) e Dotação × Consumido;
5. Produtos com compra não otimizada (Excesso / Sem demanda) e o detalhe com diagnóstico, sugestão e as notas.

As contas estão em `core/dashboard_compras.py`; os dados em `views/dashboard_comum.carregar_compras`. Filtro da página:
o período e a categoria (clique na tabela ou numa barra filtra, Ctrl/⌘+clique soma; a tabela e o gráfico de
distribuição não se filtram por si mesmos). Nenhum filtro acima da página.
"""
from __future__ import annotations

import datetime as dt
import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import analise, auth, dicas, theme, ui
from core import dashboard as dash
from core import dashboard_compras as dc
from core.theme import GRAFICO
from pedido import linhas as lc
from views import barras_filtro, popups
from views import dashboard_comum as comum_dash
from views import tabela as tb

_KEY = "dashboard_compras"
_ORDEM_CAT = f"{_KEY}_ordem_categoria"
_ORDEM_PROD = f"{_KEY}_ordem_produtos"
_PAGINA = f"{_KEY}_pagina"
_ASSINATURA = f"{_KEY}_assinatura"
_BUSCA = f"{_KEY}_busca"
_PILULA_PERIODO = f"{_KEY}_pilula_periodo"
_PILULA_CLASSE = f"{_KEY}_pilula_classe"
_ORDEM_PADRAO_CAT = ("consumido", False)
_CLASSES = {"Todos": "todos", "Excesso": dc.EXCESSO, "Sem demanda": dc.SEM_DEMANDA}
_SELO_CLASSE = {dc.EXCESSO: "selo-sugestao", dc.SEM_DEMANDA: "selo-perigo"}
_BADGE_CLASSE = {dc.EXCESSO: "rmc-badge-sugestao", dc.SEM_DEMANDA: "rmc-badge-perigo"}
# Cor da eficiência na tabela (a do protótipo aprovado): vermelho abaixo de 80%, verde a partir de 90%.
_EFI_VERMELHO, _EFI_VERDE = 80.0, 90.0
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro")


# ---------------------------------------------------------------------------
# Textos (puros: testados em tests/test_dashboard_compras.py)
# ---------------------------------------------------------------------------

def pct(valor) -> str:
    return "—" if valor is None else f"{valor:.1f}%".replace(".", ",")


def _ddmm(d: dt.date) -> str:
    return d.strftime("%d/%m")


def legenda_periodo(periodo: dc.Periodo) -> str:
    """"Outubro/2026 até 20/10", "Setembro/2026", "Agosto a outubro/2026 (até 20/10)"."""
    ini, fim = periodo.inicio, periodo.fim
    fechado = fim == (fim.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    ate = "" if fechado else f" até {_ddmm(fim)}"
    if (ini.year, ini.month) == (fim.year, fim.month):
        texto = f"{_MESES[ini.month - 1].capitalize()}/{ini.year}{ate}"
    else:
        texto = (f"{_MESES[ini.month - 1].capitalize()} a {_MESES[fim.month - 1]}/{fim.year}"
                 + (f" (até {_ddmm(fim)})" if ate else ""))
    return texto + ", pela data de emissão da nota fiscal"


def classe_eficiencia(valor) -> str | None:
    if valor is None:
        return None
    if valor < _EFI_VERMELHO:
        return "txt-vermelho"
    return "txt-verde" if valor >= _EFI_VERDE else "txt-ambar"


def cards(m: dict, regras: dc.Regras) -> list[dict]:
    """Os 4 cards (R15: textos de apoio curtos)."""
    saldo = m["saldo"]
    passou = saldo < 0
    cv = m["compra_venda"]
    return [
        {"icone": "carteira", "tom": "azul", "label": "Dotação", "valor": f"{pct(m['pct_consumido'])} consumido",
         "sub": (f"Passou {barras_filtro.moeda_curta(-saldo)}" if passou else f"Saldo de {barras_filtro.moeda_curta(saldo)}"),
         "progresso": m["pct_consumido"] or 0,
         "dica": dicas.dica(f"Dotação: {ui.formatar_moeda(m['dotacao'])}.\nConsumido: {ui.formatar_moeda(m['consumido'])}.",
                            "A dotação de cada categoria são as compras dela no mês anterior, corrigidas pela variação "
                            "da venda semana a semana contra as mesmas semanas daquele mês.")},
        {"icone": "trocas", "tom": "roxo", "label": "Compra × Venda", "valor": pct(cv),
         "sub": "" if cv is None else ("Comprou mais do que vendeu" if cv > 100 else "Comprou menos do que vendeu"),
         "dica": dicas.dica(f"Comprado: {ui.formatar_moeda(m['consumido'])}.\nVendido a custo: "
                            f"{ui.formatar_moeda(m['venda_custo'])}.",
                            "Com 100% a loja repôs exatamente o que vendeu. Acima disso, o estoque cresceu.")},
        {"icone": "alerta", "tom": "ambar", "label": "Compras não otimizadas", "valor": ui.formatar_moeda(m["nao_otimizado"]),
         "sub": f"{pct(m['pct_nao_otimizado'])} do comprado",
         "dica": dicas.dica(f"Excesso: {ui.formatar_moeda(m['excesso'])}.\nSem demanda: {ui.formatar_moeda(m['sem_demanda'])}.",
                            "Excesso é o que passou da venda do período mais o estoque ideal. Sem demanda é a compra de "
                            f"produto que não vendeu nos {regras.sem_demanda_dias} dias antes dela nem depois.")},
        {"icone": "visto", "tom": "verde", "label": "Eficiência em compras", "valor": pct(m["eficiencia"]),
         "sub": "Do comprado foi compra certa",
         "dica": dicas.dica("Compra certa é o comprado menos o excesso e a compra sem demanda.")},
    ]


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

def render() -> None:
    with theme.tela("dashboard-compras"):
        _render()


def _render() -> None:
    usuario = auth.usuario_atual()
    dados = comum_dash.carregar_compras(usuario)
    if isinstance(dados, str):
        st.info(dados)
        return
    periodo_escolhido, categorias, _ = comum_dash.filtros_compras(dados.loja["id"])
    chave = _periodo(dados, periodo_escolhido)
    periodo = dados.periodos[chave]
    ctx = dc.Contexto(periodo, dados.regras)
    d = dados.quadros[chave]
    filtrado = dc.filtrar(d, categorias)

    theme.kpis(cards(dc.metricas(filtrado, ctx), dados.regras))
    with st.container(key="dash-bloco-categoria"):
        _bloco_titulo("Compras por categoria")
        _tabela_categorias(d, categorias, ctx)
    esq, dir_ = st.columns(2)
    with esq:
        with st.container(key="dash-bloco-distribuicao"):
            _bloco_titulo("Distribuição das compras por categoria",
                          "Quanto de cada R$ comprado foi compra certa, excesso ou sem demanda.")
            _grafico_distribuicao(d, ctx)
    with dir_:
        with st.container(key="dash-bloco-dotacao"):
            _grafico_dotacao(dados, filtrado, ctx)
    with st.container(key="dash-bloco-produtos"):
        _bloco_titulo("Produtos com compra não otimizada", "Do maior valor não otimizado para o menor.")
        _produtos(filtrado, (dados.loja["id"], chave, tuple(categorias)), dados, periodo)


def _periodo(dados: comum_dash.DadosCompras, escolhido: str) -> str:
    """As pílulas de período no topo, com a legenda de quando são as notas à direita."""
    rotulos = {dc.ROTULO_PERIODO[k]: k for k in (dc.ATUAL, dc.PASSADO, dc.TRES) if k in dados.periodos}
    if escolhido not in rotulos.values():
        escolhido = next(iter(rotulos.values()))
    if st.session_state.get(_PILULA_PERIODO) not in rotulos:
        st.session_state[_PILULA_PERIODO] = dc.ROTULO_PERIODO[escolhido]
    with st.container(horizontal=True, vertical_alignment="center", key="dash-compras-periodo"):
        escolha = st.pills("Período", list(rotulos), selection_mode="single", key=_PILULA_PERIODO,
                           label_visibility="collapsed")
        chave = rotulos.get(escolha or "", escolhido)
        st.session_state[comum_dash.COM_PERIODO] = chave
        st.caption(legenda_periodo(dados.periodos[chave]))
    return chave


def _bloco_titulo(titulo: str, sub: str = "") -> None:
    legenda = f'<div class="rmc-dash-sub">{html.escape(sub)}</div>' if sub else ""
    st.markdown(f'<div class="rmc-dash-titulo">{titulo}</div>{legenda}', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Compras por categoria (com a setinha IQVIA › FEBRAFAR › CMED)
# ---------------------------------------------------------------------------

def _colunas_categoria() -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("categoria", "Categoria", largura="fit-content(340px)"),
        tb.ColunaTabela("dotacao", "Dotação", direita=True, numerica=True,
                        dica=dicas.dica("As compras da categoria no mês anterior, corrigidas pela variação da venda "
                                        "semana a semana contra as mesmas semanas daquele mês.")),
        tb.ColunaTabela("consumido", "Consumido", direita=True, numerica=True),
        tb.ColunaTabela("compra_venda", "Compra × Venda", direita=True, numerica=True,
                        dica=dicas.dica("O comprado dividido pelo vendido a custo.")),
        tb.ColunaTabela("sem_demanda", "Sem demanda", direita=True, numerica=True),
        tb.ColunaTabela("excesso", "Excesso", direita=True, numerica=True),
        tb.ColunaTabela("eficiencia", "Eficiência", direita=True, numerica=True,
                        dica=dicas.dica("A parte do comprado que foi compra certa.",
                                        "Vermelho abaixo de 80%, verde a partir de 90%.")),
    ]


def _celulas(m: dict, mostrar_variacao: bool) -> dict:
    dotacao = [tb.pedaco(ui.formatar_moeda(m["dotacao"]))]
    v = m.get("variacao")
    if mostrar_variacao and v is not None:
        sinal = "+" if v > 0 else ""
        dotacao.append(tb.pedaco(f"venda {sinal}{pct(v * 100)}", "aux"))
    return {
        "dotacao": tb.celula(*dotacao),
        "consumido": tb.celula(tb.pedaco(ui.formatar_moeda(m["consumido"]))),
        "compra_venda": tb.celula(tb.pedaco(pct(m["compra_venda"]))),
        "sem_demanda": tb.celula(tb.pedaco(ui.formatar_moeda(m["sem_demanda"])), tb.pedaco(pct(m["pct_sem_demanda"]), "aux")),
        "excesso": tb.celula(tb.pedaco(ui.formatar_moeda(m["excesso"])), tb.pedaco(pct(m["pct_excesso"]), "aux")),
        "eficiencia": tb.celula(tb.pedaco(pct(m["eficiencia"]), classe_eficiencia(m["eficiencia"]))),
    }


def linha_categoria(codigo: str, m: dict, mostrar_variacao: bool, tem_detalhe: bool = False) -> dict:
    return {"id": str(codigo), "filhos": bool(tem_detalhe),
            "celulas": {"categoria": tb.celula(tb.pedaco(lc.ROTULOS.get(codigo, codigo) or "—", "forte")),
                        **_celulas(m, mostrar_variacao)}}


def linhas_detalhe(no: dict, pai: str, grupo: str, mostrar_variacao: bool, nivel: int = 1) -> list[dict]:
    """As linhas de dentro (setor da IQVIA, FEBRAFAR, CMED): só informam, não clicam."""
    id_ = f"{pai}/{no['fonte']}:{no['chave']}"
    rotulo = lc.SETORES.get(no["chave"], no["chave"]) if no["fonte"] == "iqvia" else no["chave"]
    linha = {"id": id_, "pai": pai, "grupo": grupo, "nivel": nivel, "selecionavel": False, "filhos": bool(no["filhos"]),
             "celulas": {"categoria": tb.celula(tb.pedaco(rotulo)), **_celulas(no["metricas"], mostrar_variacao)}}
    return [linha] + [x for f in no["filhos"] for x in linhas_detalhe(f, id_, grupo, mostrar_variacao, nivel + 1)]


def _ordenar_categoria(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    st.session_state[_ORDEM_CAT] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)


def _clicar_categoria(id_: str, soma: bool) -> None:
    st.session_state[comum_dash.COM_CATEGORIAS] = barras_filtro.nova_selecao(
        st.session_state.get(comum_dash.COM_CATEGORIAS, []), id_, soma)


def _tabela_categorias(d: pd.DataFrame, selecionadas: list[str], ctx: dc.Contexto) -> None:
    variacao = ctx.periodo.chave == dc.ATUAL
    cats = dc.por_categoria(d, ctx)
    quadro = pd.DataFrame([{"codigo": c, "categoria": lc.ROTULOS.get(c, c), **m} for c, m in cats])
    ordem = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    if not quadro.empty:
        quadro = analise.ordenar(quadro, *ordem)
    detalhe = dc.detalhe_por_categoria(d, ctx)
    metricas = dict(cats)
    linhas = []
    for codigo in (quadro["codigo"] if not quadro.empty else []):
        nos = detalhe.get(codigo, [])
        linhas.append(linha_categoria(codigo, metricas[codigo], variacao, bool(nos)))
        linhas += [x for no in nos for x in linhas_detalhe(no, str(codigo), str(codigo), variacao)]
    if linhas:
        linhas.append({"id": "__total", "classe": "total", "selecionavel": False,
                       "celulas": {"categoria": tb.celula(tb.pedaco("Total")), **_celulas(dc.metricas(d, ctx), False)}})
    tb.tabela(f"{_KEY}_categorias", _colunas_categoria(), linhas, ordem, ao_ordenar=_ordenar_categoria, densa=True,
              selecao_linhas=selecionadas, ao_clicar_linha=_clicar_categoria,
              vazio=("Nenhuma compra", "Não há compra nem venda neste período."))


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

_LEGENDA_DIST = ((GRAFICO["aproveitada"], "Compra certa"), (GRAFICO["excesso"], "Excesso"),
                 (GRAFICO["sem_demanda"], "Sem demanda"))


def itens_distribuicao(dist: list[dict]) -> list[dict]:
    """As barras 100% de cada categoria (puro, para testar)."""
    itens = []
    for r in dist:
        total = r["consumido"]
        partes = [(r["certa"], *_LEGENDA_DIST[0]), (r["excesso"], *_LEGENDA_DIST[1]), (r["sem_demanda"], *_LEGENDA_DIST[2])]
        rotulo = lc.ROTULOS.get(r["categoria"], r["categoria"]) or "—"
        itens.append({
            "chave": r["categoria"], "rotulo": rotulo,
            "partes": [{"valor": v / total * 100, "cor": cor, "nome": nome} for v, cor, nome in partes],
            "texto": barras_filtro.moeda_curta(total),
            "dica": dicas.dica(f"{rotulo}: {ui.formatar_moeda(total)} comprados.",
                               "\n".join(f"{nome}: {pct(v / total * 100)}." for v, _, nome in partes)),
        })
    return itens


def _grafico_distribuicao(d: pd.DataFrame, ctx: dc.Contexto) -> None:
    itens = itens_distribuicao(dc.distribuicao(d, ctx))
    if not itens:
        st.caption("Nenhuma compra neste período.")
        return
    barras_filtro.barras(f"{_KEY}_barras_distribuicao", comum_dash.COM_CATEGORIAS, itens,
                         formato=lambda v: f"{v:.0f}%", legenda=list(_LEGENDA_DIST))


def _layout(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10), height=300, plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        legend=dict(orientation="h", y=1.12, x=0), xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor=GRAFICO["grade"], tickprefix="R$ ", separatethousands=True, rangemode="tozero"),
        separators=",.", font=dict(color=GRAFICO["texto_suporte"]), hovermode="x unified",
    )
    return fig


def _grafico_dotacao(dados: comum_dash.DadosCompras, filtrado: pd.DataFrame, ctx: dc.Contexto) -> None:
    periodo = ctx.periodo
    if periodo.chave == dc.TRES:
        _bloco_titulo("Dotação × Consumido", "Dotação final de cada mês contra o que foi comprado.")
        meses = dc.por_mes(filtrado, ctx)
        rotulos = [f"{_MESES[m['primeiro'].month - 1][:3]}/{m['primeiro'].strftime('%y')}" for m in meses]
        fig = go.Figure([
            go.Bar(x=rotulos, y=[m["dotacao"] for m in meses], name="Dotação", marker_color=theme.PALETA["verde"],
                   hovertemplate="R$ %{y:,.2f}<extra>Dotação</extra>"),
            go.Bar(x=rotulos, y=[m["consumido"] for m in meses], name="Comprado", marker_color=theme.PALETA["navy"],
                   hovertemplate="R$ %{y:,.2f}<extra>Comprado</extra>"),
        ])
        fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.08)
    else:
        sub = ("Consumido acumulado dia a dia e a dotação, recalculada a cada semana completa."
               if periodo.chave == dc.ATUAL else "Como o mês fechou: consumido acumulado e a dotação de cada dia.")
        _bloco_titulo("Dotação × Consumido", sub)
        primeiro, ultimo = periodo.meses[0]
        curva = dc.curva_do_mes(dados.itens, dados.vendas, filtrado, primeiro, ultimo, dados.regras)
        fig = go.Figure([
            go.Scatter(x=curva["dia"], y=curva["consumido"], name="Consumido acumulado", mode="lines",
                       line=dict(color=theme.PALETA["navy"], width=2),
                       hovertemplate="R$ %{y:,.2f}<extra>Consumido</extra>"),
            go.Scatter(x=curva["dia"], y=curva["dotacao"], name="Dotação", mode="lines",
                       line=dict(color=theme.PALETA["verde"], width=2, dash="dash"),
                       hovertemplate="R$ %{y:,.2f}<extra>Dotação</extra>"),
        ])
        fig.update_xaxes(title=None, tickprefix="dia ", dtick=7, tick0=1)
    st.plotly_chart(_layout(fig), use_container_width=True, config={"displayModeBar": False},
                    key=f"{_KEY}_grafico_dotacao")


# ---------------------------------------------------------------------------
# Produtos com compra não otimizada
# ---------------------------------------------------------------------------

def _colunas_produtos() -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("nome", "Produto", largura="fit-content(300px)"),
        tb.ColunaTabela("comprado_un", "Comprado", direita=True, numerica=True),
        tb.ColunaTabela("vendido_un", "Vendido", direita=True, numerica=True),
        tb.ColunaTabela("limite", "Limite", direita=True, numerica=True,
                        dica=dicas.dica("A venda do período mais o estoque ideal do Assistente de pedido.")),
        tb.ColunaTabela("nao_otimizado", "Não otimizado", direita=True, numerica=True,
                        dica=dicas.dica("Excesso: as unidades pagas acima do limite × o custo médio delas. As "
                                        "bonificadas contam primeiro e valem zero.",
                                        "Sem demanda: tudo o que foi pago.")),
        tb.ColunaTabela("classe", "Classificação", centro=True),
    ]


def _un(n) -> str:
    return f"{ui.formatar_numero(int(round(float(n or 0))))} un."


def linha_produto(r: dict) -> dict:
    sub = " · ".join(x for x in (r.get("ean_principal") if isinstance(r.get("ean_principal"), str) else None,
                                 lc.ROTULOS.get(r.get("categoria"), "")) if x)
    return {"id": str(r["linha"]), "celulas": {
        "nome": tb.celula(tb.pedaco(r["nome"], "forte"), tb.pedaco(sub, "aux")),
        "comprado_un": tb.celula(tb.pedaco(_un(r["comprado_un"]))),
        "vendido_un": tb.celula(tb.pedaco(_un(r["vendido_un"]))),
        "limite": tb.celula(tb.pedaco(_un(r["limite"]))),
        "nao_otimizado": tb.celula(tb.pedaco(ui.formatar_moeda(float(r["nao_otimizado"])))),
        "classe": tb.celula(tb.pedaco(dc.ROTULO_CLASSE[r["classe"]], _SELO_CLASSE[r["classe"]])),
    }}


def _ordenar_produtos(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_PROD, ("nao_otimizado", False))
    st.session_state[_ORDEM_PROD] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)
    st.session_state[_PAGINA] = 1


def _mudar_pagina(delta: int) -> None:
    st.session_state[_PAGINA] = st.session_state.get(_PAGINA, 1) + delta


def _produtos(filtrado: pd.DataFrame, assinatura_filtros: tuple, dados: comum_dash.DadosCompras,
              periodo: dc.Periodo) -> None:
    esq, dir_ = st.columns([3, 2])
    busca = esq.text_input("Buscar", key=_BUSCA, placeholder="Buscar produto ou EAN", label_visibility="collapsed")
    escolha = dir_.pills("Classificação", list(_CLASSES), selection_mode="single", default="Todos", key=_PILULA_CLASSE,
                         label_visibility="collapsed")
    classe = _CLASSES.get(escolha or "Todos", "todos")

    assinatura = (assinatura_filtros, busca.strip().lower(), classe)
    if st.session_state.get(_ASSINATURA) != assinatura:
        st.session_state[_ASSINATURA] = assinatura
        st.session_state[_PAGINA] = 1
    lista = dc.produtos(filtrado, busca, classe).reset_index()
    ordem = st.session_state.get(_ORDEM_PROD, ("nao_otimizado", False))
    ordenado = analise.ordenar(lista, *ordem) if _ORDEM_PROD in st.session_state and not lista.empty else lista
    fatia, pagina, total_paginas = dash.fatiar(ordenado, st.session_state.get(_PAGINA, 1))
    st.session_state[_PAGINA] = pagina

    tb.tabela(f"{_KEY}_produtos", _colunas_produtos(), [linha_produto(r) for r in fatia.to_dict("records")],
              ordem, ao_ordenar=_ordenar_produtos, acao="Ver detalhes", densa=True,
              vazio=("Nenhum produto", "Com estes filtros não há compra em excesso nem sem demanda."))
    total = len(ordenado)
    if total:
        inicio = (pagina - 1) * dash.TAMANHO_PAGINA + 1
        with st.container(horizontal=True, key="dash-pag"):
            st.caption(f"{ui.formatar_numero(inicio)}–{ui.formatar_numero(inicio + len(fatia) - 1)} de "
                       f"{ui.formatar_numero(total)} · no máximo {dash.TAMANHO_PAGINA} por página")
            with st.container(horizontal=True):
                st.button("Anterior", key=f"{_KEY}_anterior", disabled=pagina <= 1, on_click=_mudar_pagina, args=(-1,))
                st.button("Próxima", key=f"{_KEY}_proxima", disabled=pagina >= total_paginas, on_click=_mudar_pagina, args=(1,))

    clicada = tb.acao_clicada(f"{_KEY}_produtos")
    if clicada is not None:
        escolhida = ordenado[ordenado["linha"].astype(str) == clicada]
        if not escolhida.empty:
            r = escolhida.iloc[0].to_dict()
            _dialog_detalhes(r, dc.notas(dados.itens, r["linha"], periodo), dados.regras)


# ---------------------------------------------------------------------------
# Detalhe do produto (o padrão do Estoque e da Ruptura: cabeçalho, cards, diagnóstico, sugestão; + as notas)
# ---------------------------------------------------------------------------

def _esc(texto) -> str:
    return html.escape(str(texto))


def cards_detalhe(r: dict) -> list[dict]:
    """Os 4 cards do pop-up: {rotulo, valor, aux, alerta}. Puro, para testar."""
    bonif = float(r.get("bonif_un") or 0)
    pagas_aux = (f"{_un(r['excesso_pagas'])} pagas" if r["classe"] == dc.EXCESSO and bonif > 0 else "")
    return [
        {"rotulo": "Comprado", "valor": _un(r["comprado_un"]), "aux": f"{int(bonif)} bonificadas" if bonif else "",
         "alerta": False},
        {"rotulo": "Vendido", "valor": _un(r["vendido_un"]), "aux": "", "alerta": False},
        {"rotulo": "Limite", "valor": _un(r["limite"]), "aux": "", "alerta": False},
        {"rotulo": "Não otimizado", "valor": ui.formatar_moeda(float(r["nao_otimizado"])), "aux": pagas_aux,
         "alerta": True},
    ]


def html_notas(notas: pd.DataFrame) -> str:
    linhas = "".join(
        f'<tr><td>{n.data:%d/%m/%Y}</td><td>{_esc(" ".join(str(n.fornecedor or "—").split()))}'
        + (' <span class="rmc-badge rmc-badge-neutro">Bonificação</span>' if n.bonificado else "")
        + f'</td><td class="num">{ui.formatar_numero(int(round(n.unidades)))}</td>'
        f'<td class="num">{ui.formatar_moeda(float(n.preco))}</td><td class="num">{ui.formatar_moeda(float(n.valor))}</td></tr>'
        for n in notas.itertuples())
    return ('<div class="rmc-popup-secao">NOTAS FISCAIS DO PERÍODO</div><div class="rmc-sku-notas"><table><thead><tr>'
            '<th>Data</th><th>Fornecedor</th><th class="num">Qtd.</th><th class="num">Preço un.</th>'
            f'<th class="num">Total</th></tr></thead><tbody>{linhas}</tbody></table></div>')


def html_detalhe(r: dict, notas: pd.DataFrame, regras: dc.Regras) -> str:
    ean = r.get("ean_principal") if isinstance(r.get("ean_principal"), str) else "—"
    cab = (f'<div class="rmc-sku-cab"><div class="nome">{_esc(r["nome"])}</div>'
           f'<div class="sub">{_esc(lc.ROTULOS.get(r.get("categoria"), ""))} | EAN: {_esc(ean)}</div>'
           f'<div class="pilulas"><span class="rmc-badge {_BADGE_CLASSE[r["classe"]]}">'
           f'{dc.ROTULO_CLASSE[r["classe"]]}</span></div></div>')
    cards_ = "".join(
        f'<div class="rmc-sku-card"><div class="rotulo">{_esc(c["rotulo"])}</div>'
        f'<div class="valor{" alerta" if c["alerta"] else ""}">{_esc(c["valor"])}</div>'
        + (f'<div class="aux">{_esc(c["aux"])}</div>' if c["aux"] else "") + "</div>" for c in cards_detalhe(r))
    diag = (f'<div class="rmc-sku-bloco diagnostico"><div class="icone">!</div><div><div class="titulo">Diagnóstico</div>'
            f'<div class="texto">{_esc(dc.diagnostico(r, regras))}</div></div></div>')
    itens = "".join(f"<li><strong>{_esc(t)}:</strong> {_esc(x)}</li>" for t, x in dc.sugestoes(r))
    sug = (f'<div class="rmc-sku-bloco sugestao"><div class="icone">i</div><div><div class="titulo">Sugestão de ação</div>'
           f'<ul>{itens}</ul></div></div>')
    corpo = cab + f'<div class="rmc-sku-grade">{cards_}</div>' + diag + sug + (html_notas(notas) if not notas.empty else "")
    return f'<div class="rmc-sku">{corpo}</div>'.replace("$", "&#36;")


@popups.popup("Detalhes do produto", "curto")
def _dialog_detalhes(r: dict, notas: pd.DataFrame, regras: dc.Regras) -> None:
    st.markdown(html_detalhe(r, notas, regras), unsafe_allow_html=True)
