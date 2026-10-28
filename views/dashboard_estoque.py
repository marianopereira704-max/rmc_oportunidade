"""Dashboard · Estoque (22/10/2026) — a página nova.

Spec: docs/specs/2026-10-22-dashboard-estoque-design.md. As contas estão em `core/dashboard_estoque.py` (puras, com
testes); os dados vêm do modelo de madrugada (`pedido/estoque_modelo.py`) pelo `views/dashboard_comum.py`. Aqui só se
monta a tela, de cima para baixo, em largura inteira:

1. 4 cards: unidades, estoque a preço de compra, valor em excesso, valor parado;
2. análise por categoria (tabela com total; clicar numa linha filtra a página, Ctrl/⌘+clique soma);
3. estoque por linha de compra (barras clicáveis, o mesmo filtro);
4. produtos críticos (excesso e parado) com busca, situação, ordenação, 25 por página e o detalhe de cada SKU.

NÃO há nenhum filtro nem texto acima da página (decisão do Mariano, 22/10/2026): quem filtrou limpa onde filtrou —
clicando de novo no que está selecionado — e o que não está filtrado fica escurecido, como no BI. O filtro é DESTA
página (a Ruptura e a Compras têm os seus) e trocar de loja limpa tudo.
"""
from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd
import streamlit as st

from core import analise, auth, dicas, revisar, theme, ui
from core import dashboard_estoque as de
from core.theme import GRAFICO
from pedido import linhas as lc
from views import analise_comum as comum
from views import barras_filtro, popups
from views import dashboard_comum as comum_dash
from views import tabela as tb

_KEY = "dashboard_estoque"
_ORDEM_CAT = f"{_KEY}_ordem_categoria"
_ORDEM_CRIT = f"{_KEY}_ordem_criticos"          # (campo, crescente); sem clique, vale a ordem de `de.criticos`
_PAGINA = f"{_KEY}_pagina"
_ASSINATURA = f"{_KEY}_assinatura"
_LOJA_ORDEM = f"{_KEY}_loja_da_ordem"
_BUSCA = f"{_KEY}_busca"
_SITUACAO = f"{_KEY}_situacao"
_AVISADOS = f"{_KEY}_avisados"
_ORDEM_PADRAO_CAT = ("estoque_compra", False)
_ORDEM_PADRAO_CRIT = ("valor_critico", False)
_SITUACOES = {"Todos": "todos", "Excesso": de.EXCESSO, "Parado": de.PARADO}
_SELO = {de.PARADO: "selo-alterado", de.EXCESSO: "selo-sugestao", de.EFICIENTE: "selo-sucesso"}   # as cores do gráfico
_ORIGEM_CUSTO = {"compra": "último preço de compra", "cadastro": "custo do cadastro", "sem": "sem custo"}


# ---------------------------------------------------------------------------
# Textos e formatos (puros: testados em tests/test_dashboard_estoque_tela.py)
# ---------------------------------------------------------------------------

def pct(valor: float) -> str:
    return f"{valor:.1f}%".replace(".", ",")


def cobertura_texto(dias) -> str:
    """"12 dias", "menos de 1 dia" ou "—" (sem venda: não há cobertura a calcular)."""
    if dias is None or pd.isna(dias) or not np.isfinite(dias):
        return "—"
    if dias < 1:
        return "menos de 1 dia"
    n = int(round(dias))
    return f"{ui.formatar_numero(n)} dia" + ("" if n == 1 else "s")


def data_curta(iso) -> str:
    """"2026-10-05" → "05/10/2026"; vazio → "—"."""
    if iso is None or (not isinstance(iso, str) and pd.isna(iso)) or str(iso) in ("", "NaT", "<NA>"):
        return "—"
    a, m, d = str(iso)[:10].split("-")
    return f"{d}/{m}/{a}"


def md(texto) -> str:
    """Nome de produto dentro de st.markdown: "*", "$", "_" e afins não podem virar formatação."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!$|<>~])", r"\\\1", str(texto))


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

def render() -> None:
    with theme.tela("dashboard-estoque"):
        _render()


def _render() -> None:
    usuario = auth.usuario_atual()
    dados = comum_dash.carregar_estoque(usuario)
    if isinstance(dados, str):
        st.info(dados)
        return
    # Um filtro só para a tabela e o gráfico (26/10/2026): os dois mostram as 5 categorias da classificação de
    # produtos. O filtro por categoria da base (FEBRAFAR), que a tabela usava, saiu junto com ela.
    _, linhas = comum_dash.filtros_estoque(dados.loja["id"])
    categorias: list[str] = []
    _avisar_janela(dados)
    d = dados.df

    _cards(de.cards(de.filtrar(d, categorias, linhas)), dados)
    with st.container(key="dash-bloco-categoria"):
        _bloco_titulo("Análise por categoria")
        _tabela_categorias(de.sem_filtro_de(d, "linha", categorias, linhas), linhas)
    with st.container(key="dash-bloco-linha"):
        _bloco_titulo("Estoque por linha de compra", "Em R$ a preço de custo.")
        _grafico_linha(de.sem_filtro_de(d, "linha", categorias, linhas))
    with st.container(key="dash-bloco-criticos"):
        _bloco_titulo("Produtos críticos", "SKUs em excesso ou parados.")
        _criticos(de.filtrar(d, categorias, linhas), (dados.loja["id"], tuple(categorias), tuple(linhas)), dados)


def _bloco_titulo(titulo: str, sub: str = "") -> None:
    legenda = f'<div class="rmc-dash-sub">{sub}</div>' if sub else ""
    st.markdown(f'<div class="rmc-dash-titulo">{titulo}</div>{legenda}', unsafe_allow_html=True)


def _avisar_janela(dados: comum_dash.DadosEstoque) -> None:
    """O parâmetro pede mais dias de venda do que a rotina guarda (hoje cerca de 3 meses): avisa uma vez por loja."""
    guardados = de.dias_de_venda_guardados(dados.meta)
    pedidos = int(dados.config.dias_parado)
    chave = (dados.loja["id"], pedidos, guardados)
    avisados = st.session_state.setdefault(_AVISADOS, set())
    if pedidos > guardados and chave not in avisados:
        avisados.add(chave)
        st.toast(f"O parâmetro pede {ui.formatar_numero(pedidos)} dias sem venda, mas só há {ui.formatar_numero(guardados)} dias "
                 "de vendas guardados: o parado e a cobertura usam os dias que existem.", icon="⚠️")


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _cards(c: de.CardsEstoque, dados: comum_dash.DadosEstoque) -> None:
    n, config = ui.formatar_numero, dados.config
    negativos = int(dados.meta.get("skus_negativos", 0))
    theme.kpis([
        {"icone": "produto", "tom": "azul", "label": "Unid. em estoque", "valor": n(c.unidades), "sub": f"{n(c.skus)} SKUs",
         "dica": dicas.dica("Soma das unidades dos SKUs com saldo positivo no sistema de vendas.")},
        {"icone": "moeda", "tom": "roxo", "label": "Estoque a preço de compra", "valor": ui.formatar_moeda(c.estoque_compra),
         **({"dica": dicas.dica(f"Ficam de fora {dicas.qtd(negativos, 'SKU')} com estoque negativo no sistema de vendas.")}
            if negativos else {})},
        {"icone": "alerta", "tom": "ambar", "label": "Valor em excesso", "valor": ui.formatar_moeda(c.excesso_valor),
         "sub": f"{pct(c.excesso_pct)} do estoque · {n(c.excesso_skus)} SKUs",
         "dica": dicas.dica(f"A parte do estoque que passa de {dicas.qtd(config.dias_cobertura_excesso, 'dia')} de venda, "
                            "a preço de compra.", "Não conta o que já é parado.")},
        {"icone": "pausa", "tom": "azul", "label": "Valor parado", "valor": ui.formatar_moeda(c.parado_valor),
         "sub": f"{pct(c.parado_pct)} do estoque · {n(c.parado_skus)} SKUs",
         "dica": dicas.dica("SKUs com estoque que não venderam nenhuma unidade nos últimos "
                            f"{dicas.qtd(config.dias_parado, 'dia')}.", "O valor é a preço de compra.")},
    ])


# ---------------------------------------------------------------------------
# Análise por categoria
# ---------------------------------------------------------------------------

def _colunas_categoria() -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("categoria", "Categoria", largura="fit-content(340px)"),
        tb.ColunaTabela("skus", "SKUs", direita=True, numerica=True),
        tb.ColunaTabela("unidades", "Unid. em estoque", direita=True, numerica=True),
        tb.ColunaTabela("estoque_compra", "Estoque a preço de compra", direita=True, numerica=True),
        tb.ColunaTabela("estoque_venda", "Estoque a preço de venda", direita=True, numerica=True,
                        dica=dicas.dica("Quanto o estoque vale se for todo vendido pelo preço de venda.",
                                        "É só uma informação complementar.")),
        tb.ColunaTabela("cobertura", "Cobertura (dias)", direita=True, numerica=True,
                        dica=dicas.dica("Unidades em estoque ÷ unidades vendidas por dia.",
                                        "Quem não vendeu continua nas unidades e não nas vendas, por isso a "
                                        "cobertura pode ficar alta.")),
    ]


def _celulas_categoria(skus, unidades, compra, pct_compra, venda, pct_venda, cobertura) -> dict:
    return {
        "skus": tb.celula(tb.pedaco(ui.formatar_numero(skus))),
        "unidades": tb.celula(tb.pedaco(ui.formatar_numero(unidades))),
        "estoque_compra": tb.celula([tb.pedaco(ui.formatar_moeda(compra)), tb.pedaco(f"({pct(pct_compra)})", "aux")]),
        "estoque_venda": tb.celula([tb.pedaco(ui.formatar_moeda(venda)), tb.pedaco(f"({pct(pct_venda)})", "aux")]),
        "cobertura": tb.celula(tb.pedaco(cobertura_texto(cobertura))),
    }


def linha_categoria(r: dict, tem_detalhe: bool = False) -> dict:
    """Uma das 5 categorias (`codigo` = o código da linha de compra; `categoria` = o rótulo). Clica para filtrar a
    página; com detalhe, ganha a setinha no fim da linha."""
    return {"id": str(r["codigo"]), "filhos": bool(tem_detalhe), "celulas": {
        "categoria": tb.celula(tb.pedaco(r["categoria"], "forte")),
        **_celulas_categoria(r["skus"], r["unidades"], r["estoque_compra"], r["pct_compra"], r["estoque_venda"],
                             r["pct_venda"], r["cobertura"])}}


def linhas_detalhe(no: dict, pai: str, grupo: str, nivel: int = 1) -> list[dict]:
    """O nó do detalhamento (setor da IQVIA, categoria da FEBRAFAR ou tipo da CMED) e, logo abaixo, os de dentro dele.
    Só informam: não clicam (decisão de 26/10/2026 — quem filtra são as 5 categorias)."""
    id_ = f"{pai}/{no['fonte']}:{no['chave']}"
    rotulo = lc.SETORES.get(no["chave"], no["chave"]) if no["fonte"] == "iqvia" else no["chave"]
    m = no["metricas"]
    linha = {"id": id_, "pai": pai, "grupo": grupo, "nivel": nivel, "selecionavel": False, "filhos": bool(no["filhos"]),
             # Sem etiqueta da fonte (pedido de 26/10/2026): o recuo já diz o nível (IQVIA, FEBRAFAR, CMED).
             "celulas": {"categoria": tb.celula(tb.pedaco(rotulo)),
                         **_celulas_categoria(m["skus"], m["unidades"], m["estoque_compra"], m["pct_compra"],
                                              m["estoque_venda"], m["pct_venda"], m["cobertura"])}}
    return [linha] + [x for f in no["filhos"] for x in linhas_detalhe(f, id_, grupo, nivel + 1)]


def linha_total(t: dict) -> dict:
    """A linha de total: em negrito, sem clique e sem apagar."""
    return {"id": "__total", "classe": "total", "selecionavel": False, "celulas": {
        "categoria": tb.celula(tb.pedaco("Total")),
        **_celulas_categoria(t["skus"], t["unidades"], t["estoque_compra"], 100.0, t["estoque_venda"], 100.0, t["cobertura"])}}


def _ordenar_categoria(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    st.session_state[_ORDEM_CAT] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)


def _clicar_categoria(id_: str, soma: bool) -> None:
    # O mesmo filtro das barras do gráfico (as duas mostram as 5 categorias desde 26/10/2026).
    st.session_state[comum_dash.EST_LINHAS] = barras_filtro.nova_selecao(
        st.session_state.get(comum_dash.EST_LINHAS, []), id_, soma)


def _tabela_categorias(base: pd.DataFrame, selecionadas: list[str]) -> None:
    quadro = de.por_categoria(base).reset_index().rename(columns={"categoria": "codigo"})
    # A ordem pelo título "Categoria" é pelo nome que aparece, não pelo código.
    quadro["categoria"] = quadro["codigo"].map(lambda c: lc.ROTULOS.get(c, c))
    ordem = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    if not quadro.empty:
        quadro = analise.ordenar(quadro, *ordem)
    detalhe = de.detalhe_por_categoria(base)
    linhas = []
    for r in quadro.to_dict("records"):
        nos = detalhe.get(r["codigo"], [])
        linhas.append(linha_categoria(r, bool(nos)))
        linhas += [x for no in nos for x in linhas_detalhe(no, str(r["codigo"]), str(r["codigo"]))]
    if linhas:
        linhas.append(linha_total(de.total_por_categoria(base)))
    tb.tabela(f"{_KEY}_categorias", _colunas_categoria(), linhas, ordem, ao_ordenar=_ordenar_categoria, densa=True,
              selecao_linhas=selecionadas, ao_clicar_linha=_clicar_categoria,
              vazio=("Nenhuma categoria", "Com estes filtros não há SKU com estoque."))


# ---------------------------------------------------------------------------
# Estoque por linha de compra
# ---------------------------------------------------------------------------

def _grafico_linha(base: pd.DataFrame) -> None:
    por_linha = de.estoque_por_linha(base)
    itens = [{
        "chave": chave, "rotulo": lc.ROTULOS.get(chave, chave),
        "partes": [{"valor": float(r.eficiente), "cor": GRAFICO["eficiente"], "nome": "Eficiente"},
                   {"valor": float(r.excesso), "cor": GRAFICO["excesso"], "nome": "Excesso"},
                   {"valor": float(r.parado), "cor": GRAFICO["parado"], "nome": "Parado"}],
        "texto": barras_filtro.moeda_curta(r.total),
        "dica": dicas.dica(f"{lc.ROTULOS.get(chave, chave)}: {comum.moeda(r.total)} em estoque a preço de custo.",
                           f"Eficiente: {comum.moeda(r.eficiente)}\nExcesso: {comum.moeda(r.excesso)}\n"
                           f"Parado: {comum.moeda(r.parado)}"),
    } for chave, r in por_linha.iterrows()]
    # Poucas barras em largura inteira: mais largas que as 88 px dos gráficos de meia página.
    barras_filtro.barras(f"{_KEY}_barras_linha", comum_dash.EST_LINHAS, itens, formato=barras_filtro.moeda_curta,
                         barra_max=140, legenda=[(GRAFICO["eficiente"], "Eficiente"), (GRAFICO["excesso"], "Excesso"),
                                                 (GRAFICO["parado"], "Parado")])


# ---------------------------------------------------------------------------
# Produtos críticos
# ---------------------------------------------------------------------------

def _colunas_criticos(dias: int) -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("nome", "Produto", largura="fit-content(300px)"),
        tb.ColunaTabela("situacao", "Status", centro=True),
        tb.ColunaTabela("curva", "Curva", centro=True),
        tb.ColunaTabela("estoque", "Estoque", direita=True, numerica=True),
        tb.ColunaTabela("excesso_un", "Excesso (un.)", direita=True, numerica=True),
        tb.ColunaTabela("valor_estoque", "Estoque a preço de compra", direita=True, numerica=True,
                        dica=dicas.dica("Unidades × último preço de compra.",
                                        'Em "a revisar", o custo não é confiável (embalagem, cadastro ou compra fora do '
                                        'padrão) e o SKU não entra no valor em R$.')),
        tb.ColunaTabela("vendidas_n", f"Vendidas em {ui.formatar_numero(dias)} dias", direita=True, numerica=True),
        tb.ColunaTabela("cobertura", "Cobertura", direita=True, numerica=True),
    ]


def categoria_do_sku(r: dict) -> str:
    """A categoria que aparece embaixo do nome do produto (críticos e detalhe): uma das 5 da classificação de produtos
    (Propagado, Genérico, Similar, Perfumaria, Em classificação — 26/10/2026), a mesma do gráfico por linha de
    compra. Antes era a categoria da base (FEBRAFAR), e o produto sem ela ficava sem nada embaixo do nome."""
    return lc.ROTULOS.get(r.get("linha_compra"), lc.ROTULOS[lc.SEM_LINHA])


def linha_critico(r: dict) -> dict:
    """Uma linha da tabela de críticos a partir de um SKU do quadro da página."""
    sub = categoria_do_sku(r)
    if r["compra_recente"]:
        sub += f" · compra recente {data_curta(r['ultima_compra'])[:5]}"
    custo = (tb.pedaco(ui.formatar_moeda(float(r["valor_estoque"]))) if r["custo_ok"]
             else tb.pedaco("a revisar", "selo-revisar", dica=dica_custo(r["custo_motivo"])))
    excesso = float(r["excesso_un"] or 0)
    return {"id": str(r["codigo_produto"]), "celulas": {
        "nome": tb.celula(tb.pedaco(r["nome"], "forte"), tb.pedaco(sub, "aux")),
        "situacao": tb.celula(tb.pedaco(de.ROTULO_SITUACAO[r["situacao"]], _SELO[r["situacao"]])),
        "curva": tb.celula(tb.pedaco(r["curva"])),
        "estoque": tb.celula(tb.pedaco(ui.formatar_numero(r["estoque"]))),
        "excesso_un": tb.celula(tb.pedaco(ui.formatar_numero(excesso) if excesso > 0 else "—")),
        "valor_estoque": tb.celula(custo),
        "vendidas_n": tb.celula(tb.pedaco(ui.formatar_numero(r["vendidas_n"]))),
        "cobertura": tb.celula(tb.pedaco(cobertura_texto(r["cobertura"]))),
    }}


def dica_custo(motivo) -> str:
    """A dica do selo "a revisar": o motivo do custo em frase (o modelo guarda um código ou um texto curto)."""
    return dicas.dica(de.motivo_custo(motivo), "O SKU conta nas unidades, mas não entra nos valores em R$.",
                      revisar.acao(motivo))


def _ordenar_criticos(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_CRIT, _ORDEM_PADRAO_CRIT)
    st.session_state[_ORDEM_CRIT] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)
    st.session_state[_PAGINA] = 1


def _mudar_pagina(delta: int) -> None:
    st.session_state[_PAGINA] = st.session_state.get(_PAGINA, 1) + delta


def _criticos(filtrado: pd.DataFrame, assinatura_filtros: tuple, dados: comum_dash.DadosEstoque) -> None:
    esq, dir_ = st.columns([3, 2])
    busca = esq.text_input("Buscar", key=_BUSCA, placeholder="Buscar produto ou EAN", label_visibility="collapsed")
    escolha = dir_.pills("Situação", list(_SITUACOES), selection_mode="single", default="Todos", key=_SITUACAO,
                         label_visibility="collapsed")
    situacao = _SITUACOES.get(escolha or "Todos", "todos")

    # Filtro, busca ou situação novos (ou outra loja): volta à página 1; outra loja também zera a ordem.
    assinatura = (assinatura_filtros, busca.strip().lower(), situacao)
    if st.session_state.get(_ASSINATURA) != assinatura:
        st.session_state[_ASSINATURA] = assinatura
        st.session_state[_PAGINA] = 1
    if st.session_state.get(_LOJA_ORDEM) != dados.loja["id"]:
        st.session_state[_LOJA_ORDEM] = dados.loja["id"]
        st.session_state.pop(_ORDEM_CRIT, None)

    criticos = de.criticos(filtrado, busca, situacao)
    ordem = st.session_state.get(_ORDEM_CRIT, _ORDEM_PADRAO_CRIT)
    # Sem clique em título, vale a ordem de `de.criticos` (valor, depois unidades).
    ordenado = analise.ordenar(criticos, *ordem) if _ORDEM_CRIT in st.session_state and not criticos.empty else criticos
    fatia, pagina, total_paginas = de.dash.fatiar(ordenado, st.session_state.get(_PAGINA, 1))
    st.session_state[_PAGINA] = pagina

    dias = int(dados.config.dias_parado)
    tb.tabela(f"{_KEY}_criticos", _colunas_criticos(dias), [linha_critico(r) for r in fatia.to_dict("records")], ordem,
              ao_ordenar=_ordenar_criticos, acao="Ver detalhes", densa=True,
              vazio=("Nenhum SKU crítico", "Com estes filtros não há SKU em excesso nem parado."))
    total = len(ordenado)
    if total:
        inicio = (pagina - 1) * de.dash.TAMANHO_PAGINA + 1
        with st.container(horizontal=True, key="dash-pag"):
            st.caption(f"{ui.formatar_numero(inicio)}–{ui.formatar_numero(inicio + len(fatia) - 1)} de "
                       f"{ui.formatar_numero(total)} · no máximo {de.dash.TAMANHO_PAGINA} por página")
            with st.container(horizontal=True):
                st.button("Anterior", key=f"{_KEY}_anterior", disabled=pagina <= 1, on_click=_mudar_pagina, args=(-1,))
                st.button("Próxima", key=f"{_KEY}_proxima", disabled=pagina >= total_paginas, on_click=_mudar_pagina, args=(1,))

    clicada = tb.acao_clicada(f"{_KEY}_criticos")
    if clicada is not None:
        escolhida = ordenado[ordenado["codigo_produto"].astype(str) == clicada]
        if not escolhida.empty:
            _dialog_detalhes(escolhida.iloc[0].to_dict(), dados.config, dados.meta["fim"])


# ---------------------------------------------------------------------------
# Detalhe do SKU (22/10/2026): cabeçalho, 6 cards, diagnóstico e sugestão de ação
# ---------------------------------------------------------------------------

_BADGE_SITUACAO = {de.PARADO: "rmc-badge-alterado", de.EXCESSO: "rmc-badge-sugestao", de.EFICIENTE: "rmc-badge-sucesso"}


def _esc(texto) -> str:
    return html.escape(str(texto))


def cards_detalhe(r: dict, config, hoje) -> list[dict]:
    """Os 6 cards do pop-up: {rotulo, valor, aux, alerta (valor em vermelho), aux_alerta}. Puro, para testar."""
    n, moeda = ui.formatar_numero, ui.formatar_moeda
    estoque = float(r["estoque"])
    parado = r["situacao"] == de.PARADO
    vendidas = float(r["vendidas_n"])
    baixo_giro = vendidas <= float(config.giro_baixo_max_unidades)
    dias_compra = de.dias_desde(r["ultima_compra"], hoje)
    cobertura = r["cobertura"]
    com_cobertura = cobertura is not None and pd.notna(cobertura) and np.isfinite(cobertura)

    if parado:
        segundo = {"rotulo": "Valor parado", "valor": moeda(float(r["valor_parado"])) if r["custo_ok"] else "a revisar",
                   "aux": "100% do estoque", "alerta": True}
    else:
        excesso = float(r["excesso_un"] or 0)
        segundo = {"rotulo": "Excesso", "valor": f"{n(excesso)} un.", "alerta": excesso > 0,
                   "aux": f"{pct(100 * excesso / estoque) if estoque else '0,0%'} do estoque"}
    custo = (f"{moeda(float(r['custo']))}/un. · " + _ORIGEM_CUSTO.get(r["custo_origem"], str(r["custo_origem"]))
             if r["custo_ok"] else "custo não confiável")
    return [
        {"rotulo": "Estoque atual", "valor": f"{n(estoque)} un.", "aux": "", "alerta": False},
        segundo,
        {"rotulo": "Valor de compra", "valor": moeda(float(r["valor_estoque"])) if r["custo_ok"] else "a revisar",
         "aux": custo, "alerta": False},
        {"rotulo": f"Vendas {n(config.dias_parado)} dias", "valor": f"{n(vendidas)} un.", "alerta": baixo_giro,
         "aux": "sem venda" if vendidas == 0 else ("baixo giro" if baixo_giro else ""), "aux_alerta": baixo_giro},
        {"rotulo": "Cobertura", "valor": cobertura_texto(cobertura),
         "alerta": bool(com_cobertura and cobertura > config.dias_cobertura_excesso),
         "aux": "" if com_cobertura else "sem venda"},
        {"rotulo": "Última compra", "valor": data_curta(r["ultima_compra"]) if dias_compra is not None else "Sem compra",
         "aux": ("hoje" if dias_compra == 0 else f"há {n(dias_compra)} dia" + ("" if dias_compra == 1 else "s"))
                if dias_compra is not None else "na janela guardada", "alerta": False},
    ]


def _html_detalhe(r: dict, config, hoje) -> str:
    """O corpo do pop-up em HTML (uma peça só: sem recuo nem linha em branco, o Markdown não vira código)."""
    pilula_curva = "rmc-badge-sucesso" if str(r["curva"]).startswith("A") else "rmc-badge-neutro"
    cab = (f'<div class="rmc-sku-cab"><div class="nome">{_esc(r["nome"])}</div>'
           f'<div class="sub">{_esc(categoria_do_sku(r))} | EAN: {_esc(r["ean"] if pd.notna(r["ean"]) else "—")}</div>'
           f'<div class="pilulas"><span class="rmc-badge {pilula_curva}">Curva {_esc(r["curva"])}</span>'
           f'<span class="rmc-badge {_BADGE_SITUACAO[r["situacao"]]}">{de.ROTULO_SITUACAO[r["situacao"]]}</span></div></div>')
    cards = "".join(
        f'<div class="rmc-sku-card"><div class="rotulo">{_esc(c["rotulo"])}</div>'
        f'<div class="valor{" alerta" if c["alerta"] else ""}">{_esc(c["valor"])}</div>'
        + (f'<div class="aux{" alerta" if c.get("aux_alerta") else ""}">{_esc(c["aux"])}</div>' if c["aux"] else "")
        + "</div>" for c in cards_detalhe(r, config, hoje))
    diag = (f'<div class="rmc-sku-bloco diagnostico"><div class="icone">!</div><div><div class="titulo">Diagnóstico</div>'
            f'<div class="texto">{_esc(de.diagnostico(r, config, hoje))}</div></div></div>')
    itens = "".join(f"<li><strong>{_esc(t)}:</strong> {_esc(x)}</li>" for t, x in de.sugestoes(r, config, hoje))
    sug = (f'<div class="rmc-sku-bloco sugestao"><div class="icone">i</div><div><div class="titulo">Sugestão de ação</div>'
           f'<ul>{itens}</ul></div></div>') if itens else ""
    # "$" dentro de st.markdown vira fórmula: entidade HTML no lugar.
    corpo = cab + f'<div class="rmc-sku-grade">{cards}</div>' + diag + sug
    return f'<div class="rmc-sku">{corpo}</div>'.replace("$", "&#36;")


@popups.popup("Detalhes do produto", "curto")
def _dialog_detalhes(r: dict, config, hoje) -> None:
    st.markdown(_html_detalhe(r, config, hoje), unsafe_allow_html=True)
