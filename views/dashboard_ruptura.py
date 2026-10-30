"""Dashboard · Ruptura (27/10/2026, redesenho; design em docs/specs/2026-10-26-dashboard-ruptura-design.md e protótipo
aprovado em 26/10/2026). A página fala só do que FALTA e de quanto isso custa; excesso e parado ficaram no Estoque.

De cima para baixo, em largura inteira:
1. 4 cards: Ruptura, Venda em risco por dia, Perda (projeção na transição; medida depois — R1b) e Investimento;
2. Ruptura por categoria: as 5 categorias com a setinha IQVIA › FEBRAFAR › CMED (como no Estoque);
3. lado a lado: Ruptura por curva e Perda por risco (barras clicáveis);
4. Produtos críticos (em ruptura e ruptura próxima), com busca, situação e risco; detalhe com diagnóstico e sugestão.

As contas estão em `core/dashboard_ruptura.py`; os dados (cálculo do Assistente + histórico do estoque +
classificação) em `views/dashboard_comum.carregar_ruptura`. Filtro da página: categoria, curva e risco (clique filtra,
Ctrl/⌘+clique soma, clique de novo tira; cada bloco não se filtra por si mesmo). Nenhum filtro acima da página.
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core import analise, auth, dicas, theme, ui
from core import dashboard as dash
from core import dashboard_ruptura as dr
from core.theme import GRAFICO
from pedido import linhas as lc
from views import barras_filtro, popups
from views import dashboard_comum as comum_dash
from views import tabela as tb

_KEY = "dashboard_ruptura"
_ORDEM_CAT = f"{_KEY}_ordem_categoria"
_ORDEM_CRIT = f"{_KEY}_ordem_criticos"
_PAGINA = f"{_KEY}_pagina"
_ASSINATURA = f"{_KEY}_assinatura"
_BUSCA = f"{_KEY}_busca"
_SITUACAO = f"{_KEY}_situacao"
_ORDEM_PADRAO_CAT = ("em_ruptura", False)
_ORDEM_PADRAO_CRIT = ("perda_produto", False)
_SITUACOES = {"Todos": "todos", "Em ruptura": dr.RUPTURA, "Ruptura próxima": dr.PROXIMA}
_SELO_SITUACAO = {dr.RUPTURA: ("Em ruptura", "selo-perigo"), dr.PROXIMA: ("Ruptura próxima", "selo-ruptura")}
_SELO_RISCO = {dr.ALTO: "selo-perigo", dr.MEDIO: "selo-sugestao", dr.BAIXO: "selo-neutro"}
_COR_RISCO = {dr.ALTO: GRAFICO["ruptura"], dr.MEDIO: GRAFICO["excesso"], dr.BAIXO: GRAFICO["curva_c"]}
_FONTE_ROTULO = {"iqvia": "IQVIA", "febrafar": "FEBRAFAR", "cmed": "CMED"}


# ---------------------------------------------------------------------------
# Textos (puros: testados em tests/test_dashboard_ruptura.py)
# ---------------------------------------------------------------------------

def pct(valor: float) -> str:
    return f"{valor:.1f}%".replace(".", ",")


def data_curta(valor) -> str:
    return dr._data_curta(valor)


def classe_nivel(nivel_pct: float, regras: dr.Regras) -> str:
    """A cor do nível de serviço (R6): vermelho abaixo do limite, verde a partir do outro, âmbar no meio."""
    if nivel_pct < regras.sla_vermelho * 100:
        return "txt-vermelho"
    if nivel_pct >= regras.sla_verde * 100:
        return "txt-verde"
    return "txt-ambar"


def textos_perda(resumo: dict, c: dr.Cards) -> tuple[str, str, str]:
    """(rótulo, subtítulo, legenda dos blocos) da perda, pelo modo da página (R1b)."""
    n = ui.formatar_numero
    if resumo["modo"] == dr.MODO_MEDIDA:
        return ("Perda", f"últimos {n(resumo['periodo'])} dias · se não repor: {ui.formatar_moeda(c.projecao)} em 30",
                f"Perda medida nos últimos {n(resumo['periodo'])} dias.")
    if resumo["primeira_foto"] is None:
        medida = "Medindo a partir da primeira foto do estoque"
        legenda = "Perda: projeção de 30 dias. A medição começa com a primeira foto do estoque."
    else:
        medida = (f"Medida até agora: {ui.formatar_moeda(c.perda_medida)} em {dicas.qtd(resumo['dias_medidos'], 'dia')} "
                  f"(desde {data_curta(resumo['primeira_foto'])})")
        legenda = f"Perda: projeção de 30 dias; a perda medida entra em {data_curta(resumo['completo_em'])}."
    return "Perda em 30 dias se não repor", medida, legenda


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

def render() -> None:
    with theme.tela("dashboard-ruptura"):
        _render()


def _render() -> None:
    usuario = auth.usuario_atual()
    dados = comum_dash.carregar_ruptura(usuario)
    if isinstance(dados, str):
        st.info(dados)
        return
    categorias, curvas, riscos = comum_dash.filtros_ruptura(dados.loja["id"])
    regras = dr.Regras.da_config(dados.config)
    d = dados.df
    filtrado = dr.filtrar(d, categorias, curvas, riscos)
    c = dr.cards(filtrado)
    rotulo_perda, sub_perda, legenda_perda = textos_perda(dados.resumo, c)

    _cards(c, rotulo_perda, sub_perda, regras)
    with st.container(key="dash-bloco-categoria"):
        _bloco_titulo("Ruptura por categoria", legenda_perda)
        _tabela_categorias(dr.sem_filtro_de(d, "categoria", categorias, curvas, riscos), categorias, regras)
    esq, dir_ = st.columns(2)
    with esq:
        with st.container(key="dash-bloco-curva"):
            _bloco_titulo("Ruptura por curva", "Produtos em ruptura e % da curva (a 1ª letra, a do valor vendido).")
            _grafico_curva(dr.sem_filtro_de(d, "curva", categorias, curvas, riscos))
    with dir_:
        with st.container(key="dash-bloco-risco"):
            _bloco_titulo("Perda por risco", "Pela frequência de venda do produto em falta.")
            _grafico_risco(dr.sem_filtro_de(d, "risco", categorias, curvas, riscos), regras)
    with st.container(key="dash-bloco-criticos"):
        _bloco_titulo("Produtos críticos", "Em ruptura ou com ruptura próxima, da maior perda para a menor.")
        _criticos(filtrado, (dados.loja["id"], tuple(categorias), tuple(curvas), tuple(riscos)), dados, regras)


def _bloco_titulo(titulo: str, sub: str = "") -> None:
    legenda = f'<div class="rmc-dash-sub">{html.escape(sub)}</div>' if sub else ""
    st.markdown(f'<div class="rmc-dash-titulo">{titulo}</div>{legenda}', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _cards(c: dr.Cards, rotulo_perda: str, sub_perda: str, regras: dr.Regras) -> None:
    n = ui.formatar_numero
    theme.kpis([
        {"icone": "alerta", "tom": "vermelho", "label": "Ruptura", "valor": f"{n(c.ruptura)} itens",
         "sub": f"{pct(c.ruptura_pct)} do mix · {n(c.ruptura_curva_a)} de curva A",
         "dica": dicas.dica(f"Produtos com estoque de até {dicas.qtd(regras.ruptura_unidades, 'unidade')}, vendendo "
                            "muito ou pouco.", "O mix são os produtos que venderam na janela do Assistente de pedido.")},
        {"icone": "moeda", "tom": "vermelho", "label": "Venda em risco por dia", "valor": ui.formatar_moeda(c.venda_risco_dia),
         "sub": "dos itens em ruptura hoje",
         "dica": dicas.dica("Venda esperada por dia × preço de venda dos produtos em ruptura.",
                            "A venda esperada é a média dos dias em que o produto tinha estoque.")},
        {"icone": "media", "tom": "ambar", "label": rotulo_perda, "valor": ui.formatar_moeda(c.perda), "sub": sub_perda,
         "dica": dicas.dica("Venda esperada por dia × preço de venda × dias em falta.",
                            "Os dias em falta são medidos pelo histórico do estoque. Enquanto ele não tem os dias do "
                            "período, a página mostra o que se perde em 30 dias se não repor.")},
        {"icone": "carrinho", "tom": "verde", "label": "Investimento para repor", "valor": ui.formatar_moeda(c.investimento),
         "sub": f"{dicas.qtd(c.investimento_un, 'unidade')} · sugestão do Assistente",
         "dica": dicas.dica("A sugestão do Assistente de pedido (até o estoque ideal) × o custo da última compra, dos "
                            "produtos em ruptura.", "Custo a revisar conta nas unidades e fica fora do valor.")},
    ])


# ---------------------------------------------------------------------------
# Ruptura por categoria (com a setinha IQVIA › FEBRAFAR › CMED)
# ---------------------------------------------------------------------------

def _colunas_categoria() -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("categoria", "Categoria", largura="fit-content(340px)"),
        tb.ColunaTabela("produtos", "Produtos", direita=True, numerica=True, celular=None),
        tb.ColunaTabela("em_ruptura", "Em ruptura", direita=True, numerica=True),
        tb.ColunaTabela("nivel_servico", "Nível de serviço", direita=True, numerica=True,
                        dica=dicas.dica("A parte do mix que tem estoque.",
                                        "Vermelho, âmbar e verde pelos limites das Configurações de Pedidos.")),
        tb.ColunaTabela("perda", "Perda", direita=True, numerica=True, celular=None),
        tb.ColunaTabela("investimento", "Investimento necessário", direita=True, numerica=True, celular=None),
    ]


def _celulas(m: dict, regras: dr.Regras) -> dict:
    return {
        "produtos": tb.celula(tb.pedaco(ui.formatar_numero(m["produtos"]))),
        "em_ruptura": tb.celula(tb.pedaco(ui.formatar_numero(m["em_ruptura"]))),
        "nivel_servico": tb.celula(tb.pedaco(pct(m["nivel_servico"]), classe_nivel(m["nivel_servico"], regras))),
        "perda": tb.celula(tb.pedaco(ui.formatar_moeda(m["perda"]))),
        "investimento": tb.celula(tb.pedaco(ui.formatar_moeda(m["investimento"]))),
    }


def linha_categoria(r: dict, regras: dr.Regras, tem_detalhe: bool = False) -> dict:
    return {"id": str(r["codigo"]), "filhos": bool(tem_detalhe),
            "celulas": {"categoria": tb.celula(tb.pedaco(r["categoria"], "forte")), **_celulas(r, regras)}}


def linhas_detalhe(no: dict, pai: str, grupo: str, regras: dr.Regras, nivel: int = 1) -> list[dict]:
    """As linhas de dentro (setor da IQVIA, FEBRAFAR, CMED): só informam, não clicam. Sem etiqueta da fonte (pedido
    de 26/10/2026, como no Estoque): o recuo já diz o nível."""
    id_ = f"{pai}/{no['fonte']}:{no['chave']}"
    rotulo = lc.SETORES.get(no["chave"], no["chave"]) if no["fonte"] == "iqvia" else no["chave"]
    linha = {"id": id_, "pai": pai, "grupo": grupo, "nivel": nivel, "selecionavel": False, "filhos": bool(no["filhos"]),
             "celulas": {"categoria": tb.celula(tb.pedaco(rotulo)), **_celulas(no["metricas"], regras)}}
    return [linha] + [x for f in no["filhos"] for x in linhas_detalhe(f, id_, grupo, regras, nivel + 1)]


def linha_total(t: dict, regras: dr.Regras) -> dict:
    return {"id": "__total", "classe": "total", "selecionavel": False,
            "celulas": {"categoria": tb.celula(tb.pedaco("Total")), **_celulas(t, regras)}}


def _ordenar_categoria(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    st.session_state[_ORDEM_CAT] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)


def _clicar_categoria(id_: str, soma: bool) -> None:
    st.session_state[comum_dash.RUP_CATEGORIAS] = barras_filtro.nova_selecao(
        st.session_state.get(comum_dash.RUP_CATEGORIAS, []), id_, soma)


def _tabela_categorias(base: pd.DataFrame, selecionadas: list[str], regras: dr.Regras) -> None:
    quadro = dr.por_categoria(base).reset_index().rename(columns={"categoria": "codigo"})
    quadro["categoria"] = quadro["codigo"].map(lambda x: lc.ROTULOS.get(x, x))
    ordem = st.session_state.get(_ORDEM_CAT, _ORDEM_PADRAO_CAT)
    if not quadro.empty:
        quadro = analise.ordenar(quadro, *ordem)
    detalhe = dr.detalhe_por_categoria(base)
    linhas = []
    for r in quadro.to_dict("records"):
        nos = detalhe.get(r["codigo"], [])
        linhas.append(linha_categoria(r, regras, bool(nos)))
        linhas += [x for no in nos for x in linhas_detalhe(no, str(r["codigo"]), str(r["codigo"]), regras)]
    if linhas:
        linhas.append(linha_total(dr.total(base), regras))
    tb.tabela(f"{_KEY}_categorias", _colunas_categoria(), linhas, ordem, ao_ordenar=_ordenar_categoria, densa=True,
              selecao_linhas=selecionadas, ao_clicar_linha=_clicar_categoria, celular="tabela",
              vazio=("Nenhuma categoria", "Com estes filtros não há produto no mix."))


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

def _grafico_curva(base: pd.DataFrame) -> None:
    curva = dr.por_curva(base)
    itens = [{
        "chave": k, "rotulo": f"Curva {k}",
        "partes": [{"valor": int(r.em_ruptura), "cor": GRAFICO[f"curva_{k.lower()}"], "nome": f"Curva {k}"}],
        "texto": f"{ui.formatar_numero(r.em_ruptura)} · {pct(r.pct)}",
        "dica": dicas.dica(f"Curva {k}: {ui.formatar_numero(r.em_ruptura)} de {dicas.qtd(r.produtos, 'produto')} em "
                           f"ruptura ({pct(r.pct)}).", f"Perda: {ui.formatar_moeda(r.perda)}"),
    } for k, r in curva.iterrows()]
    barras_filtro.barras(f"{_KEY}_barras_curva", comum_dash.RUP_CURVAS, itens, inteiro=True,
                         legenda=[(GRAFICO["curva_a"], "Produtos em ruptura e % da curva")])


def _grafico_risco(base: pd.DataFrame, regras: dr.Regras) -> None:
    risco = dr.por_risco(base)
    explica = {dr.ALTO: f"vende em {pct(regras.risco_alto * 100)} dos dias ou mais",
               dr.MEDIO: f"vende em {pct(regras.risco_medio * 100)} a {pct(regras.risco_alto * 100)} dos dias",
               dr.BAIXO: f"vende em menos de {pct(regras.risco_medio * 100)} dos dias"}
    itens = [{
        "chave": k, "rotulo": dr.ROTULO_RISCO[k],
        "partes": [{"valor": float(r.perda), "cor": _COR_RISCO[k], "nome": dr.ROTULO_RISCO[k]}],
        "texto": f"{barras_filtro.moeda_curta(r.perda)} · {pct(r.pct)}",
        "dica": dicas.dica(f"{dr.ROTULO_RISCO[k]}: {explica[k]}.",
                           f"Perda: {ui.formatar_moeda(r.perda)}\nEm ruptura: {ui.formatar_numero(r.em_ruptura)}"),
    } for k, r in risco.iterrows()]
    barras_filtro.barras(f"{_KEY}_barras_risco", comum_dash.RUP_RISCOS, itens, formato=barras_filtro.moeda_curta,
                         legenda=[(_COR_RISCO[k], dr.ROTULO_RISCO[k]) for k in dr.RISCOS])


# ---------------------------------------------------------------------------
# Produtos críticos
# ---------------------------------------------------------------------------

def _colunas_criticos() -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("nome", "Produto", largura="fit-content(260px)", celular="titulo"),
        tb.ColunaTabela("situacao", "Situação", centro=True, celular="etiquetas"),
        tb.ColunaTabela("curva", "Curva", centro=True, celular=None),
        tb.ColunaTabela("ultima_venda", "Última venda", direita=True, celular=None),
        tb.ColunaTabela("compra_data", "Última compra", direita=True, celular=None),
        tb.ColunaTabela("venda_90d", "Vendidas 90 dias", direita=True, numerica=True, celular=None),
        tb.ColunaTabela("perda_produto", "Perda", direita=True, numerica=True,
                        dica=dicas.dica("Em ruptura: a perda da falta aberta, ou a projeção de 30 dias enquanto o "
                                        "histórico do estoque não tem o período.", "Ruptura próxima ainda não tem perda.")),
        tb.ColunaTabela("risco", "Risco", centro=True, celular="etiquetas"),
        tb.ColunaTabela("repor_un", "Repor", direita=True, numerica=True,
                        dica=dicas.dica("A sugestão do Assistente de pedido, até o estoque ideal, e quanto custa.")),
    ]


def linha_critico(r: dict) -> dict:
    eans = r.get("eans") if isinstance(r.get("eans"), list) else []
    sub = " · ".join(x for x in (eans[0] if eans else None, lc.ROTULOS.get(r.get("linha_compra"), "")) if x)
    rotulo, classe = _SELO_SITUACAO[r["situacao"]]
    if r["situacao"] == dr.PROXIMA:
        perda = tb.pedaco("—", "aux")
    elif r.get("sem_base"):
        perda = tb.pedaco("sem base", "aux", dica=dicas.dica("O produto faltou em todos os dias da base de 90 dias.",
                                                              "Sem venda esperada, não há como medir a perda."))
    else:
        perda = tb.pedaco(ui.formatar_moeda(float(r.get("perda_produto") or 0)))
    un = int(r.get("repor_un") or 0)
    valor = ui.formatar_moeda(float(r["repor_valor"])) if r.get("custo_ok") else "custo a revisar"
    return {"id": str(r["linha"]), "celulas": {
        "nome": tb.celula(tb.pedaco(r["nome"], "forte"), tb.pedaco(sub, "aux")),
        "situacao": tb.celula(tb.pedaco(rotulo, classe)),
        "curva": tb.celula(tb.pedaco(r["curva"])),
        "ultima_venda": tb.celula(tb.pedaco(data_curta(r.get("ultima_venda")))),
        "compra_data": tb.celula(tb.pedaco(data_curta(r.get("compra_data")))),
        "venda_90d": tb.celula(tb.pedaco(ui.formatar_numero(r.get("venda_90d") or 0))),
        "perda_produto": tb.celula(perda),
        "risco": tb.celula(tb.pedaco(dr.ROTULO_RISCO[r["risco"]].replace(" risco", ""), _SELO_RISCO[r["risco"]])),
        "repor_un": tb.celula(tb.pedaco(f"{ui.formatar_numero(un)} un."), tb.pedaco(valor, "aux")) if un
        else tb.celula(tb.pedaco("—", "aux")),
    }}


def _ordenar_criticos(coluna: tb.ColunaTabela) -> None:
    atual, crescente = st.session_state.get(_ORDEM_CRIT, _ORDEM_PADRAO_CRIT)
    st.session_state[_ORDEM_CRIT] = (coluna.campo, not crescente) if coluna.campo == atual else (coluna.campo, not coluna.numerica)
    st.session_state[_PAGINA] = 1


def _mudar_pagina(delta: int) -> None:
    st.session_state[_PAGINA] = st.session_state.get(_PAGINA, 1) + delta


def _criticos(filtrado: pd.DataFrame, assinatura_filtros: tuple, dados: comum_dash.DadosRuptura, regras: dr.Regras) -> None:
    esq, dir_ = st.columns([3, 2])
    busca = esq.text_input("Buscar", key=_BUSCA, placeholder="Buscar produto ou EAN", label_visibility="collapsed")
    escolha = dir_.pills("Situação", list(_SITUACOES), selection_mode="single", default="Todos", key=_SITUACAO,
                         label_visibility="collapsed")
    situacao = _SITUACOES.get(escolha or "Todos", "todos")

    assinatura = (assinatura_filtros, busca.strip().lower(), situacao)
    if st.session_state.get(_ASSINATURA) != assinatura:
        st.session_state[_ASSINATURA] = assinatura
        st.session_state[_PAGINA] = 1
    criticos = dr.criticos(filtrado, busca, situacao)
    ordem = st.session_state.get(_ORDEM_CRIT, _ORDEM_PADRAO_CRIT)
    # Sem clique em título, vale a ordem padrão de `dr.criticos` (R12).
    ordenado = analise.ordenar(criticos, *ordem) if _ORDEM_CRIT in st.session_state and not criticos.empty else criticos
    fatia, pagina, total_paginas = dash.fatiar(ordenado, st.session_state.get(_PAGINA, 1))
    st.session_state[_PAGINA] = pagina

    tb.tabela(f"{_KEY}_criticos", _colunas_criticos(), [linha_critico(r) for r in fatia.reset_index(drop=True).to_dict("records")],
              ordem, ao_ordenar=_ordenar_criticos, acao="Ver detalhes", densa=True, celular="cartao",
              vazio=("Nenhum produto crítico", "Com estes filtros não há produto em ruptura nem com ruptura próxima."))
    total = len(ordenado)
    if total:
        inicio = (pagina - 1) * dash.TAMANHO_PAGINA + 1
        with st.container(horizontal=True, key="dash-pag"):
            st.caption(f"{ui.formatar_numero(inicio)}–{ui.formatar_numero(inicio + len(fatia) - 1)} de "
                       f"{ui.formatar_numero(total)} · no máximo {dash.TAMANHO_PAGINA} por página")
            with st.container(horizontal=True):
                st.button("Anterior", key=f"{_KEY}_anterior", disabled=pagina <= 1, on_click=_mudar_pagina, args=(-1,))
                st.button("Próxima", key=f"{_KEY}_proxima", disabled=pagina >= total_paginas, on_click=_mudar_pagina, args=(1,))

    clicada = tb.acao_clicada(f"{_KEY}_criticos")
    if clicada is not None:
        escolhida = ordenado[ordenado["linha"].astype(str) == clicada]
        if not escolhida.empty:
            _dialog_detalhes(escolhida.iloc[0].to_dict(), regras)


# ---------------------------------------------------------------------------
# Detalhe do produto (o padrão do Estoque: cabeçalho, 6 cards, diagnóstico e sugestão)
# ---------------------------------------------------------------------------

def _esc(texto) -> str:
    return html.escape(str(texto))


def cards_detalhe(r: dict, regras: dr.Regras) -> list[dict]:
    """Os 6 cards do pop-up: {rotulo, valor, aux, alerta}. Puro, para testar."""
    n = ui.formatar_numero
    em_falta = r["situacao"] == dr.RUPTURA
    dias = int(r.get("dias_falta") or 0)
    if em_falta and dias:
        dias_txt = (f"≈ {dicas.qtd(dias, 'dia')}" if int(r.get("dias_sem_foto") or 0) else dicas.qtd(dias, "dia"))
        dias_aux = f"medido desde {data_curta(r.get('inicio_falta'))}"
    else:
        dias_txt, dias_aux = ("medindo", "o histórico ainda não tem a falta") if em_falta else ("—", "ainda com estoque")
    if r["situacao"] == dr.PROXIMA:
        perda, perda_aux = "—", "ainda sem falta"
    elif r.get("sem_base"):
        perda, perda_aux = "sem base", "faltou em toda a base"
    elif not dias:
        # Sem a falta medida ainda: o card mostra o que se perde se não repor (pedido de 27/10/2026), não R$ 0,00.
        perda, perda_aux = ui.formatar_moeda(float(r.get("projecao") or 0)), "em 30 dias se não repor"
    else:
        perda = ui.formatar_moeda(float(r.get("perda_aberta") or 0))
        perda_aux = f"se não repor: {ui.formatar_moeda(float(r.get('projecao') or 0))} em 30 dias"
    fornecedor = r.get("compra_fornecedor")
    compra_aux = " · ".join(x for x in (
        " ".join(fornecedor.split()) if isinstance(fornecedor, str) and fornecedor.strip() else None,
        f"{ui.formatar_moeda(float(r['preco']))}/un." if r.get("custo_ok") else None) if x)
    return [
        {"rotulo": "Estoque atual", "valor": f"{n(r.get('estoque') or 0)} un.", "aux": "", "alerta": em_falta},
        {"rotulo": "Vendas em 90 dias", "valor": f"{n(r.get('venda_90d') or 0)} un.", "aux": "", "alerta": False},
        {"rotulo": "Dias sem estoque", "valor": dias_txt, "aux": dias_aux, "alerta": em_falta and dias > 0},
        {"rotulo": "Perda", "valor": perda, "aux": perda_aux, "alerta": em_falta},
        {"rotulo": "Última venda", "valor": data_curta(r.get("ultima_venda")), "aux": "", "alerta": False},
        {"rotulo": "Última compra", **({"valor": data_curta(r.get("compra_data")), "aux": compra_aux}
                                         if data_curta(r.get("compra_data")) != "—" else
                                         {"valor": "Sem compra", "aux": (f"custo do cadastro: {ui.formatar_moeda(float(r['preco']))}/un."
                                                                         if r.get("custo_ok") else "na janela de vendas")}),
         "alerta": False},
    ]


def html_detalhe(r: dict, regras: dr.Regras) -> str:
    eans = r.get("eans") if isinstance(r.get("eans"), list) else []
    rotulo, _ = _SELO_SITUACAO[r["situacao"]]
    classe_sit = "rmc-badge-sugestao" if r["situacao"] == dr.PROXIMA else "rmc-badge-perigo"
    classe_risco = {dr.ALTO: "rmc-badge-perigo", dr.MEDIO: "rmc-badge-sugestao", dr.BAIXO: "rmc-badge-neutro"}[r["risco"]]
    cab = (f'<div class="rmc-sku-cab"><div class="nome">{_esc(r["nome"])}</div>'
           f'<div class="sub">{_esc(lc.ROTULOS.get(r.get("linha_compra"), ""))} | EAN: {_esc(eans[0] if eans else "—")}</div>'
           f'<div class="pilulas"><span class="rmc-badge rmc-badge-neutro">Curva {_esc(r["curva"])}</span>'
           f'<span class="rmc-badge {classe_sit}">{rotulo}</span>'
           f'<span class="rmc-badge {classe_risco}">{dr.ROTULO_RISCO[r["risco"]]}</span></div></div>')
    cards = "".join(
        f'<div class="rmc-sku-card"><div class="rotulo">{_esc(c["rotulo"])}</div>'
        f'<div class="valor{" alerta" if c["alerta"] else ""}">{_esc(c["valor"])}</div>'
        + (f'<div class="aux">{_esc(c["aux"])}</div>' if c["aux"] else "") + "</div>" for c in cards_detalhe(r, regras))
    diag = (f'<div class="rmc-sku-bloco diagnostico"><div class="icone">!</div><div><div class="titulo">Diagnóstico</div>'
            f'<div class="texto">{_esc(dr.diagnostico(r, regras))}</div></div></div>')
    itens = "".join(f"<li><strong>{_esc(t)}:</strong> {_esc(x)}</li>" for t, x in dr.sugestoes(r))
    sug = (f'<div class="rmc-sku-bloco sugestao"><div class="icone">i</div><div><div class="titulo">Sugestão de ação</div>'
           f'<ul>{itens}</ul></div></div>')
    corpo = cab + f'<div class="rmc-sku-grade">{cards}</div>' + diag + sug
    return f'<div class="rmc-sku">{corpo}</div>'.replace("$", "&#36;")


@popups.popup("Detalhes do produto", "curto")
def _dialog_detalhes(r: dict, regras: dr.Regras) -> None:
    st.markdown(html_detalhe(r, regras), unsafe_allow_html=True)
