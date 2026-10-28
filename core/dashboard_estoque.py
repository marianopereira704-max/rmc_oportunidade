"""Dashboard · página Estoque — as contas de leitura (22/10/2026).

Spec: docs/specs/2026-10-22-dashboard-estoque-design.md (seções 3 a 8).

Funções puras sobre o modelo de estoque da loja (`pedido/estoque_modelo.py`): sem Streamlit, sem banco, sem
leitura de arquivo. A tela (`views/dashboard_estoque.py`) só junta o que vem do banco (categoria, linha de
compra, configuração da loja) e desenha o que sai daqui.

O modelo traz UM SKU por linha (só estoque > 0) com o custo já resolvido; as vendas por dia vêm à parte. Aqui
se aplicam os parâmetros DA LOJA (que a rotina de madrugada não enxerga):

- parado: SKU com estoque e NENHUMA venda nos últimos N dias (`dias_parado`, padrão 90);
- excesso: o que passa de `dias_cobertura_excesso` dias de venda (padrão 60), nos SKUs que NÃO são parados.
  O ritmo de venda é unidades vendidas em N dias ÷ N. Parado fica fora do excesso: cada real do estoque está
  em uma classe só (eficiente, excesso ou parado) e os cards fecham com o total;
- cobertura em dias: unidades em estoque ÷ unidades vendidas por dia (em unidades, não em valor);
- curva AA a CC: a do Assistente (valor e unidades vendidos), FIXA em 90 dias, entre quem vendeu; "D" para o
  parado. O corte A/B vem da configuração da loja;
- custo: o do modelo. SKU com `custo_a_revisar` NÃO entra nos valores em R$ (as unidades contam); a tela
  mostra "a revisar".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import dashboard as dash
from pedido import calculo
from pedido import categorias as cat
from pedido import classificacao
from pedido.estoque_modelo import Modelo

DIAS_CURVA = 90            # a curva ABC é sempre a dos últimos 90 dias (Q4, 22/10/2026)
DIAS_COMPRA_RECENTE = 30   # "compra recente" = última compra nos últimos 30 dias (fixo)
SEM_CATEGORIA = "Sem classificação"
PARADO, EXCESSO, EFICIENTE = "parado", "excesso", "eficiente"
ROTULO_SITUACAO = {PARADO: "Parado", EXCESSO: "Excesso", EFICIENTE: "Eficiente"}

COLUNAS_NOVAS = [
    "vendidas_n", "parado", "demanda_dia", "cobertura", "excesso_un", "em_excesso", "situacao", "custo_ok",
    "valor_estoque", "valor_parado", "valor_excesso", "valor_eficiente", "valor_critico", "unidades_criticas",
    "valor_venda", "curva", "compra_recente", "categoria", "categoria_rotulo", "linha_compra",
    "fonte_setor", "fonte_febrafar", "fonte_cmed",
]


def dias_de_venda_guardados(meta: dict) -> int:
    """Quantos dias de venda o modelo tem (a janela da rotina menos os dias que não chegaram). A tela avisa
    quando o `dias_parado` pedido passa disto."""
    inicio, fim = pd.Timestamp(meta["inicio"]), pd.Timestamp(meta["fim"])
    return max(0, (fim - inicio).days + 1 - len(meta.get("dias_sem_vendas", [])))


def _janela(vendas: pd.DataFrame, fim: pd.Timestamp, dias: int) -> pd.DataFrame:
    corte = (fim - pd.Timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    return vendas[vendas["data"].astype("string") >= corte].groupby("codigo_produto")[["unidades", "valor"]].sum()


def preparar(m: Modelo, config, categoria_por_ean=None, classe_por_ean=None, caminho_por_ean=None) -> pd.DataFrame:
    """O quadro da página: o modelo + as colunas calculadas com a configuração da loja.

    `config`: a `ConfigPedido` efetiva da loja. `categoria_por_ean`: EAN (chave normalizada) → categoria da
    base de categorias (Series ou dict). `classe_por_ean`: EAN → (categoria fixa, origem) da cascata de
    classificação (`pedido.classificacao.mapa_da_loja`, 25/10/2026): é a linha de compra. `caminho_por_ean`: EAN →
    setor da IQVIA, categoria da FEBRAFAR e tipo da CMED (`classificacao.caminhos_da_loja`, DataFrame), para o
    detalhamento da tabela "Análise por categoria" (26/10/2026)."""
    d = m.skus.copy()
    if d.empty:
        for c in COLUNAS_NOVAS:
            d[c] = pd.Series(dtype="object")
        return d
    n = max(1, int(config.dias_parado))
    fim = pd.Timestamp(m.meta["fim"])
    vendas = m.vendas_dia

    vn = _janela(vendas, fim, n)
    d["vendidas_n"] = d["codigo_produto"].map(vn["unidades"]).fillna(0).clip(lower=0)
    d["parado"] = d["vendidas_n"] <= 0
    d["demanda_dia"] = d["vendidas_n"] / n
    d["cobertura"] = np.where(d["demanda_dia"] > 0, d["estoque"] / d["demanda_dia"].where(d["demanda_dia"] > 0), np.nan)
    limite = d["demanda_dia"] * config.dias_cobertura_excesso
    # Excesso em unidades INTEIRAS (22/10/2026): com 999 un. e 1 venda em 90 dias o limite é 0,67 un. e o excesso
    # saía 998,33 — a tela mostrava 998 un. × R$ 4,15 e o valor (R$ 4.143,08) não batia. Arredonda para baixo: só
    # conta a unidade que de fato passa do limite, e quantidade × custo = valor em todo lugar.
    d["excesso_un"] = np.floor((d["estoque"] - limite).clip(lower=0) + 1e-9).where(~d["parado"], 0.0)
    d["em_excesso"] = d["excesso_un"] > 0
    d["situacao"] = np.where(d["parado"], PARADO, np.where(d["em_excesso"], EXCESSO, EFICIENTE))

    # Valores em R$: só com custo confiável. O resto conta nas unidades e aparece como "a revisar".
    custo = pd.to_numeric(d["custo"], errors="coerce")
    d["custo_ok"] = ~d["custo_a_revisar"].astype(bool) & (custo > 0)
    c = custo.where(d["custo_ok"], 0.0).fillna(0.0)
    d["valor_estoque"] = d["estoque"] * c
    d["valor_parado"] = np.where(d["parado"], d["valor_estoque"], 0.0)
    d["valor_excesso"] = d["excesso_un"] * c
    d["valor_eficiente"] = d["valor_estoque"] - d["valor_parado"] - d["valor_excesso"]
    d["valor_critico"] = d["valor_parado"] + d["valor_excesso"]
    d["unidades_criticas"] = np.where(d["parado"], d["estoque"], d["excesso_un"])
    d["valor_venda"] = d["estoque"] * pd.to_numeric(d["preco_venda"], errors="coerce").fillna(0)

    d["curva"] = _curva(d, vendas, fim, config)
    corte_recente = (fim - pd.Timedelta(days=DIAS_COMPRA_RECENTE)).strftime("%Y-%m-%d")
    d["compra_recente"] = d["ultima_compra"].notna() & (d["ultima_compra"].astype("string").fillna("") >= corte_recente)

    categorias = d["ean"].map(categoria_por_ean) if categoria_por_ean is not None else pd.Series(None, index=d.index, dtype="object")
    categorias = categorias.astype("object")
    d["categoria"] = categorias.where(categorias.notna() & (categorias != cat.EM_CLASSIFICACAO), None)
    d["categoria_rotulo"] = d["categoria"].fillna(SEM_CATEGORIA)
    tmp = pd.DataFrame({"eans": [[e] if isinstance(e, str) else [] for e in d["ean"]], "ean_principal": d["ean"]},
                       index=d.index)
    d["linha_compra"] = classificacao.coluna(tmp, classe_por_ean or {})
    caminho = caminho_por_ean if caminho_por_ean is not None else pd.DataFrame(columns=["setor", "febrafar", "cmed"])
    for coluna_, origem_ in (("fonte_setor", "setor"), ("fonte_febrafar", "febrafar"), ("fonte_cmed", "cmed")):
        d[coluna_] = d["ean"].map(caminho[origem_]) if not caminho.empty else None
        d[coluna_] = d[coluna_].astype(object).where(d[coluna_].notna(), None)
    return d


def _curva(d: pd.DataFrame, vendas: pd.DataFrame, fim: pd.Timestamp, config) -> pd.Series:
    """AA a CC pela venda dos últimos 90 dias de TODOS os produtos que venderam (não só os que têm estoque,
    para a proporção ser a do Assistente); "D" para o parado e para quem não vendeu nos 90 dias."""
    v90 = _janela(vendas, fim, DIAS_CURVA)
    v90 = v90[v90["unidades"] > 0]
    if v90.empty:
        return pd.Series("D", index=d.index)
    letras = calculo._curva(v90["valor"], config.curva_a, config.curva_b) + calculo._curva(v90["unidades"], config.curva_a, config.curva_b)
    curva = d["codigo_produto"].map(letras)
    return pd.Series(np.where(d["parado"] | curva.isna(), "D", curva), index=d.index)


# ---------------------------------------------------------------------------
# Filtros (clique e Ctrl+clique)
# ---------------------------------------------------------------------------

def filtrar(d: pd.DataFrame, categorias=(), linhas=()) -> pd.DataFrame:
    """Várias da MESMA dimensão somam (OU); dimensões diferentes se combinam (E)."""
    if d.empty:
        return d
    mascara = pd.Series(True, index=d.index)
    if categorias:
        mascara &= d["categoria_rotulo"].isin(set(categorias))
    if linhas:
        mascara &= d["linha_compra"].isin(set(linhas))
    return d[mascara]


def sem_filtro_de(d: pd.DataFrame, dimensao: str, categorias=(), linhas=()) -> pd.DataFrame:
    """Filtrado por TUDO MENOS a dimensão do próprio bloco (as outras linhas/barras não somem).
    `dimensao`: "categoria" ou "linha"."""
    return filtrar(d, categorias=() if dimensao == "categoria" else categorias,
                   linhas=() if dimensao == "linha" else linhas)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _pct(parte: float, todo: float) -> float:
    return float(parte / todo * 100) if todo else 0.0


@dataclass(frozen=True)
class CardsEstoque:
    skus: int
    unidades: float
    estoque_compra: float        # R$ a custo (só SKUs com custo confiável)
    estoque_venda: float         # R$ a preço de venda
    excesso_valor: float
    excesso_pct: float           # % do estoque a preço de compra
    excesso_skus: int
    parado_valor: float
    parado_pct: float
    parado_skus: int
    a_revisar_skus: int          # SKUs sem custo confiável: contam nas unidades, não no R$


def cards(d: pd.DataFrame) -> CardsEstoque:
    if d.empty:
        return CardsEstoque(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0, 0)
    valor = float(d["valor_estoque"].sum())
    return CardsEstoque(
        skus=len(d), unidades=float(d["estoque"].sum()), estoque_compra=valor, estoque_venda=float(d["valor_venda"].sum()),
        excesso_valor=float(d["valor_excesso"].sum()), excesso_pct=_pct(d["valor_excesso"].sum(), valor),
        excesso_skus=int(d["em_excesso"].sum()), parado_valor=float(d["valor_parado"].sum()),
        parado_pct=_pct(d["valor_parado"].sum(), valor), parado_skus=int(d["parado"].sum()),
        a_revisar_skus=int((~d["custo_ok"]).sum()),
    )


# ---------------------------------------------------------------------------
# Tabela por categoria
# ---------------------------------------------------------------------------

COLUNAS_CATEGORIA = ["skus", "unidades", "estoque_compra", "pct_compra", "estoque_venda", "pct_venda", "cobertura"]


def _metricas(g, total_compra: float, total_venda: float) -> dict:
    """As colunas da tabela para um grupo de SKUs: % do total da tabela inteira (não do pai), e cobertura =
    unidades ÷ unidades vendidas por dia do grupo."""
    unidades, demanda = float(g["estoque"].sum()), float(g["demanda_dia"].sum())
    compra, venda = float(g["valor_estoque"].sum()), float(g["valor_venda"].sum())
    return {"skus": int(len(g)), "unidades": unidades, "estoque_compra": compra, "pct_compra": _pct(compra, total_compra),
            "estoque_venda": venda, "pct_venda": _pct(venda, total_venda),
            "cobertura": unidades / demanda if demanda > 0 else float("nan")}


def por_categoria(d: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por categoria da classificação de produtos (26/10/2026: Propagado, Genérico, Similar, Perfumaria,
    Em classificação — a `linha_compra`; antes era a categoria da base, da FEBRAFAR): SKUs, unidades, estoque a preço
    de compra e de venda (R$ e % do total) e cobertura em dias. Só as que têm SKU, do maior estoque a preço de compra
    para o menor. O índice é o CÓDIGO da categoria (o rótulo é da tela). O total sai de `total_por_categoria`."""
    if d.empty:
        return pd.DataFrame(columns=COLUNAS_CATEGORIA, index=pd.Index([], name="categoria"))
    tc, tv = float(d["valor_estoque"].sum()), float(d["valor_venda"].sum())
    t = pd.DataFrame({c: _metricas(g, tc, tv) for c, g in d.groupby("linha_compra")}).T
    t.index.name = "categoria"
    t = t.astype({"skus": int, **{c: float for c in COLUNAS_CATEGORIA if c != "skus"}})
    return t.sort_values(["estoque_compra", "unidades"], ascending=False, kind="stable")[COLUNAS_CATEGORIA]


# O que a setinha de cada categoria abre: setor da IQVIA → categoria da FEBRAFAR → tipo da CMED, com as regras da
# cascata das Configurações (o nível que o SKU não tem é pulado: sem IQVIA, a FEBRAFAR vem direto abaixo da
# categoria). Sem linha "Sem classificação": decisão de 26/10/2026 — os filhos podem não somar o pai.
NIVEIS_DETALHE = (("iqvia", "fonte_setor"), ("febrafar", "fonte_febrafar"), ("cmed", "fonte_cmed"))


def detalhe_por_categoria(d: pd.DataFrame) -> dict[str, list[dict]]:
    """Categoria → os nós que abrem embaixo dela: [{"fonte", "chave", "metricas", "filhos": [...]}], cada nível do
    maior estoque a preço de compra para o menor."""
    if d.empty:
        return {}
    tc, tv = float(d["valor_estoque"].sum()), float(d["valor_venda"].sum())

    def nos(g: pd.DataFrame, niveis) -> list[dict]:
        saida = []
        for i, (fonte, coluna) in enumerate(niveis):
            com = g[g[coluna].notna()] if coluna in g else g.iloc[0:0]
            for chave, sub in com.groupby(coluna):
                saida.append({"fonte": fonte, "chave": str(chave), "metricas": _metricas(sub, tc, tv),
                              "filhos": nos(sub, niveis[i + 1:])})
            # Quem não tem este nível desce direto ao próximo (sem nó intermediário).
            g = g[g[coluna].isna()] if coluna in g else g
            if g.empty:
                break
        saida.sort(key=lambda n: (-n["metricas"]["estoque_compra"], -n["metricas"]["unidades"], n["chave"]))
        return saida

    return {c: nos(g, NIVEIS_DETALHE) for c, g in d.groupby("linha_compra")}


def total_por_categoria(d: pd.DataFrame) -> dict:
    """A linha de total (cobertura do total = Σ unidades ÷ Σ unidades vendidas por dia)."""
    if d.empty:
        return {"skus": 0, "unidades": 0.0, "estoque_compra": 0.0, "estoque_venda": 0.0, "cobertura": float("nan")}
    demanda = float(d["demanda_dia"].sum())
    unidades = float(d["estoque"].sum())
    return {"skus": len(d), "unidades": unidades, "estoque_compra": float(d["valor_estoque"].sum()),
            "estoque_venda": float(d["valor_venda"].sum()), "cobertura": unidades / demanda if demanda > 0 else float("nan")}


def estoque_por_linha(d: pd.DataFrame) -> pd.DataFrame:
    """Eficiente / excesso / parado em R$ a custo por linha de compra (as colunas do quadro têm os mesmos nomes
    das da Ruptura: é a mesma conta)."""
    return dash.estoque_por_linha(d)


# ---------------------------------------------------------------------------
# Tabela de produtos críticos
# ---------------------------------------------------------------------------

def _normalizar(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def criticos(d: pd.DataFrame, busca: str = "", situacao: str = "todos") -> pd.DataFrame:
    """SKUs em excesso ou parados. `busca`: parte do nome (sem acento e sem maiúscula) ou do EAN. `situacao`:
    "todos", "excesso" ou "parado". Ordem: valor (excesso + parado, a custo), depois as unidades; o desempate
    pelo nome deixa a ordem estável entre cliques. SKU "a revisar" tem valor 0 e vem depois, por unidades."""
    if d.empty:
        return d
    c = d[d["situacao"].isin([PARADO, EXCESSO])]
    if situacao in (PARADO, EXCESSO):
        c = c[c["situacao"] == situacao]
    termos = _normalizar(busca).split()
    if termos and not c.empty:
        # TODAS as palavras, em qualquer ordem ("lenco bebe" acha "LENÇO UMEDECIDO BEBÊ"); uma palavra só de
        # números (3 ou mais) também vale como pedaço do EAN.
        nomes = c["nome"].map(_normalizar)
        eans = c["ean"].astype("string").fillna("")
        achou = pd.Series(True, index=c.index)
        for t in termos:
            ok = nomes.str.contains(t, regex=False)
            if t.isdigit() and len(t) >= 3:
                ok |= eans.str.contains(t, regex=False)
            achou &= ok
        c = c[achou]
    return c.sort_values(["valor_critico", "unidades_criticas", "nome"], ascending=[False, False, True], kind="stable")


# O custo "a revisar" guarda um código (os do Assistente de pedido) ou um texto curto (os do modelo): a tela mostra
# a frase. Código desconhecido cai no texto genérico, nunca aparece cru.
_MOTIVO_CUSTO = {
    "divergencia": "O custo de cadastro e a última compra estão muito diferentes, e o preço de venda não mostra "
                   "qual está certo.",
    "acima_venda": "A última compra passa do preço de venda, e o custo de cadastro também não combina com ele.",
    "cadastro": "Sem compra recente, e o custo de cadastro não combina com o preço de venda.",
    "compra": "A última compra está muito diferente das outras compras deste produto na loja.",
    "Sem custo no cadastro nem em compra": "O produto não tem custo no cadastro nem em compra.",
    "Custo fora da faixa do preço de venda": "O custo não combina com o preço de venda: fica fora da faixa coerente.",
}


def motivo_custo(motivo) -> str:
    """O porquê do custo "a revisar", em frase."""
    return _MOTIVO_CUSTO.get(str(motivo), "O custo deste produto não é confiável.")


# ---------------------------------------------------------------------------
# Detalhe do SKU: diagnóstico e sugestão de ação (22/10/2026)
# ---------------------------------------------------------------------------
# Textos FIXOS escolhidos pela situação do SKU, com os números dele. Os limites (dias de cobertura do excesso,
# dias sem venda do parado) vêm da `config` que a tela já usa — a efetiva da loja, personalizada ou padrão —,
# nunca de um número escrito aqui. Ficam no módulo de contas (e não na tela) para serem testados sem Streamlit.

def dias_desde(data, hoje) -> int | None:
    """Dias inteiros entre `data` (texto ISO ou data) e `hoje`; None se não há data."""
    if data is None or (not isinstance(data, str) and pd.isna(data)) or str(data) in ("", "NaT", "<NA>"):
        return None
    return max(int((pd.Timestamp(hoje).normalize() - pd.Timestamp(str(data)[:10])).days), 0)


def _n(valor) -> str:
    from core import ui
    return ui.formatar_numero(valor)


def diagnostico(r, config, hoje=None) -> str:
    """O que está acontecendo com o SKU, em até três frases curtas, com os números dele."""
    from core import ui
    frases: list[str] = []
    if r["situacao"] == PARADO:
        valor = f"O estoque, de {ui.formatar_moeda(float(r['valor_parado']))}, está todo parado." if r["custo_ok"] \
            else f"As {_n(r['estoque'])} unidades em estoque estão todas paradas."
        frases.append(f"Sem nenhuma venda nos últimos {_n(config.dias_parado)} dias. {valor}")
    elif r["situacao"] == EXCESSO:
        valor = f"somam {ui.formatar_moeda(float(r['valor_excesso']))}" if r["custo_ok"] else "não têm custo confiável para somar em R$"
        frases.append(f"O estoque cobre {_n(round(float(r['cobertura'])))} dias de venda, acima da meta de "
                      f"{_n(config.dias_cobertura_excesso)}. As {_n(r['excesso_un'])} unidades acima do limite {valor}.")
    else:
        frases.append("O estoque está dentro do esperado para a venda.")
    dias = dias_desde(r["ultima_compra"], hoje) if hoje is not None and r["compra_recente"] else None
    if dias is not None:
        frases.append("Comprado hoje." if dias == 0 else f"Comprado há {_n(dias)} dia" + ("" if dias == 1 else "s") + ".")
    if not r["custo_ok"]:
        frases.append("O custo deste produto não é confiável, então o valor em R$ pode estar errado.")
    return " ".join(frases)


def sugestoes(r, config, hoje=None) -> list[tuple[str, str]]:
    """As ações que o lojista pode tomar, como (título, explicação), da mais urgente para a menos."""
    itens: list[tuple[str, str]] = []
    if not r["custo_ok"]:
        itens.append(("Conferir o custo", "Confira o custo no cadastro antes de decidir qualquer ação."))
    dias = dias_desde(r["ultima_compra"], hoje) if hoje is not None and r["compra_recente"] else None
    if dias is not None:
        itens.append(("Conferir o pedido", f"Comprado há {_n(dias)} dia{'' if dias == 1 else 's'}: veja se o pedido não "
                                           "foi duplicado e espere antes de comprar de novo."))
    if r["situacao"] == PARADO:
        itens += [("Kit ou combo", "Venda junto com um produto de giro alto da mesma categoria."),
                  ("Brinde ou sorteio", "Ofereça na compra de outro item para escoar o estoque."),
                  ("Fornecedor", "Peça devolução ou troca. Se não der, não reponha o produto.")]
    elif r["situacao"] == EXCESSO:
        itens += [("Suspender a compra", f"Não compre até a cobertura voltar para {_n(config.dias_cobertura_excesso)} dias."),
                  ("Ponta de gôndola ou balcão", "Dê destaque ou um desconto leve para acelerar a venda."),
                  ("Kit ou brinde", "Faça compra casada com um produto de giro alto.")]
    return itens
