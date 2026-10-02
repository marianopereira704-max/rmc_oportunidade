"""Sugestão de pedido de uma loja — regras de 25–27/09/2026, revistas em
29/09/2026 (novo layout da aba Pedido). Função pura sobre o "pronto" da loja
(pedido/pronto.py), a base de categorias e a Base Genéricos: sem banco e sem
Streamlit, pra testar cada regra isolada.

Uma LINHA do pedido = um genérico (todos os EANs dele na Base Genéricos,
somados: demanda, estoque e compras) ou, fora da Base Genéricos, um produto
do cadastro da loja.

- Demanda/dia = unidades vendidas na janela ÷ dias da janela COM dado. Dia
  sem arquivo de vendas (rotina falhou) sai da conta dos dois lados.
- Unidades: venda e estoque já vêm na menor unidade (docs/mapa_api_gps.md §7).
- Estoque negativo: cada EAN negativo conta como 0 e o cálculo segue — não
  trava a linha (29/09/2026). O item ganha a tag "Estoque negativo" e fica na
  lista de correção; digitado o estoque certo, ele vale no lugar da soma
  enquanto a foto do GPS continuar negativa. Caso real da Hudson: genérico
  com Medley −1 e Eurofarma +1 travava inteiro; agora vale 1.
- Máximo = ⌈demanda × dias⌉ (medicamento 7, perfumaria 15, Sem Classificação
  7 — dias estimados, com a tag; tudo configurável), com piso. É onde o
  pedido + o estoque devem chegar. Sugestão = máximo − estoque.
- Aparece na lista só quem vendeu na janela e está ABAIXO do máximo.
- Ruptura = estoque até N unidades (padrão 0, zerado). Ruptura próxima =
  estoque acima de N que cobre até D dias de venda (padrão 3). Antes os dois
  eram um só ("≤ 3 dias"): 354 das 374 linhas da Hudson, maior que os 303
  itens com sugestão, e o card não fechava a conta.
- Giro baixo = no máximo 1 unidade vendida nos últimos 90 dias.
- Curva ABC em duas letras: valor vendido e unidades, cortes 50/40/10.
- Preço (só no Pedido — a Análise de Oportunidade continua no menor preço) =
  a ÚLTIMA compra não bonificada: é o preço que a loja consegue com mais
  frequência. VlrUnitario × (1 − desconto%) ÷ Fracao, por unidade, sem ST.
  "A revisar" = compra fora do padrão da PRÓPRIA loja (mais de N× ou menos de
  1/N da mediana das compras daquela linha, padrão 3): é pulada, vale a
  última coerente, e a dica mostra o valor ignorado. O custo de cadastro do
  GPS (VlrPrecoCompra) bate com a última compra em só ~40–48% dos produtos
  (medido em 29/09/2026), por isso deixou de ser a régua. Só bonificação ou
  nenhuma compra → custo de cadastro, com a tag (valor aproximado).
- Cadastro × nota (01/10/2026): quando o custo de cadastro e a nota divergem
  mais de N× (padrão 2), o preço de venda decide quem está certo — custo
  coerente = entre 15% e 100% do preço de venda. Só um coerente: vale ele,
  com a tag "custo ajustado". Os dois ou nenhum: fica a nota, "a revisar".
  Sem compra: cadastro fora da faixa → "a revisar". Medido na Hudson: o
  cadastro bate com a nota (±10%) em 75% dos produtos e diverge mais de 2× em
  10 de 3.332; a faixa resolveu 9 desses 10 (display comprado sem fração na
  nota — Maskaclets R$ 79,49 = 30 × R$ 2,65 do cadastro; seringa cadastrada
  pela caixa de 100). O custo normal fica entre 22% e 79% do preço de venda.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pedido import categorias as cat
from pedido.pronto import Pronto

# Tags de situação (coluna Status) — em ordem de gravidade.
NEGATIVO = "Estoque negativo"
RUPTURA = "Ruptura"
RUPTURA_PROXIMA = "Ruptura próxima"
CORRIGIDO = "Estoque corrigido"
SEM_CLASSIFICACAO = "Sem Classificação"
GIRO_BAIXO = "Giro baixo"
ALTERADO = "Alterado à mão"
TAGS_STATUS = [NEGATIVO, RUPTURA, RUPTURA_PROXIMA, CORRIGIDO, SEM_CLASSIFICACAO, GIRO_BAIXO, ALTERADO]
# Tags de preço (embaixo do preço, na coluna Última compra).
BONIFICADO = "Bonificado"
A_REVISAR = "a revisar"
CUSTO_CADASTRO = "custo de cadastro"
CUSTO_AJUSTADO = "custo ajustado"
# Por que o item está "a revisar" (coluna revisar_motivo, pra dica da tela).
MOTIVO_COMPRA = "compra"            # compra fora do padrão das compras da loja
MOTIVO_DIVERGENCIA = "divergencia"  # cadastro × nota divergem e a faixa não decide
MOTIVO_CADASTRO = "cadastro"        # sem compra e cadastro fora da faixa


@dataclass(frozen=True)
class Parametros:
    """Os números configuráveis (Configurações de Pedidos —
    pedido/configuracao.py). `dias_por_categoria`: ((categoria, dias), …),
    tupla pra entrar na chave do cache da tela."""
    dias_medicamento: int = 7
    dias_perfumaria: int = 15
    dias_ruptura: int = 3                 # Ruptura próxima: cobertura até N dias
    giro_baixo_dias: int = 90
    giro_baixo_max_unidades: float = 1
    curva_a: float = 0.50
    curva_b: float = 0.40
    tolerancia_preco: float = 0.50        # não usado desde 29/09/2026 (ver fator_preco_fora)
    limite_bonificacao: float = 0.10
    dias_por_categoria: tuple = ()
    piso_maximo: int = 1
    ruptura_unidades: float = 0           # Ruptura: estoque até N unidades
    dias_sem_classificacao: int = 7
    fator_preco_fora: float = 3.0         # "a revisar": > N× ou < 1/N da mediana da loja
    custo_faixa_min: float = 0.15         # custo coerente: entre min e max × preço de venda
    custo_faixa_max: float = 1.0
    fator_cadastro_nota: float = 2.0      # cadastro × nota divergem acima disso → a faixa decide


def _teto(valor: float) -> int:
    # 1/3 × 3 = 1.0000000000000002 no float: sem a folga, virava 2.
    return int(math.ceil(valor - 1e-9))


def _curva(valores: pd.Series, corte_a: float, corte_b: float) -> pd.Series:
    """A = os itens que somam os primeiros 50% (o item que cruza a linha
    ainda é A), B = os próximos 40%, C = o resto. Sem venda nenhuma → C."""
    total = valores.sum()
    if total <= 0:
        return pd.Series("C", index=valores.index)
    ordem = valores.sort_values(ascending=False, kind="stable")
    antes = (ordem.cumsum() - ordem) / total
    letra = np.where(antes < corte_a, "A", np.where(antes < corte_a + corte_b, "B", "C"))
    return pd.Series(letra, index=ordem.index).reindex(valores.index)


_COLUNAS_COMPRA = ["data", "preco", "vlr_unitario", "vlr_desconto", "fracao", "laboratorio", "fornecedor"]


def _compra_de_referencia(c: pd.DataFrame, fator: float) -> pd.DataFrame:
    """Uma compra por linha: a mais recente NÃO bonificada e dentro do padrão
    da loja. Devolve também se houve bonificação mais recente e a compra
    ignorada por estar fora do padrão (pra dica)."""
    if c.empty:
        return pd.DataFrame(columns=_COLUNAS_COMPRA + ["bonificado_recente", "ignorada_preco", "ignorada_data", "a_revisar"])
    pagas = c[~c["bonificado"]]
    mediana = pagas.groupby("linha")["preco"].median()
    pagas = pagas.assign(_mediana=pagas["linha"].map(mediana))
    fora = (pagas["preco"] > pagas["_mediana"] * fator) | (pagas["preco"] < pagas["_mediana"] / fator)
    pagas = pagas.assign(_fora=fora).sort_values(["linha", "data"], ascending=[True, False], kind="stable")
    coerente = pagas[~pagas["_fora"]].drop_duplicates("linha").set_index("linha")
    qualquer = pagas.drop_duplicates("linha").set_index("linha")
    escolhida = coerente.reindex(qualquer.index)
    sem_coerente = escolhida["preco"].isna()
    escolhida.loc[sem_coerente] = qualquer.loc[sem_coerente]
    # A mais recente de todas foi ignorada por estar fora do padrão?
    ignorada = qualquer[qualquer["_fora"] & ~sem_coerente]
    escolhida["ignorada_preco"] = ignorada["preco"].reindex(escolhida.index)
    escolhida["ignorada_data"] = ignorada["data"].reindex(escolhida.index)
    escolhida["a_revisar"] = escolhida["ignorada_preco"].notna() | sem_coerente & qualquer["_fora"]
    ultima_bonif = c[c["bonificado"]].groupby("linha")["data"].max()
    escolhida["bonificado_recente"] = ultima_bonif.reindex(escolhida.index) > escolhida["data"]
    # Linhas que só têm bonificação: sem compra paga (preço vem do cadastro).
    so_bonif = ultima_bonif.index.difference(escolhida.index)
    if len(so_bonif):
        extra = pd.DataFrame(index=so_bonif, columns=escolhida.columns)
        extra["bonificado_recente"] = True
        extra["a_revisar"] = False
        escolhida = pd.concat([escolhida, extra])
    return escolhida


def calcular(pronto: Pronto, base_categorias: pd.DataFrame, genericos: pd.DataFrame,
             p: Parametros = Parametros(), correcoes: dict[str, float] | None = None) -> pd.DataFrame:
    """Todas as linhas que VENDERAM na janela (listadas ou não — o filtro
    `listar` diz quais aparecem), com as colunas da tela e as tags.

    `base_categorias`: ean (chave), categoria. `genericos`: ean (chave),
    base_generico_id, nome_canonico. `correcoes`: linha → estoque correto
    digitado pra um item que o GPS mostra negativo — só vale enquanto a foto
    continuar negativa (foto nova sem negativo: a correção é ignorada)."""
    meta = pronto.meta
    inicio, fim = dt.date.fromisoformat(meta["inicio"]), dt.date.fromisoformat(meta["fim"])
    dias = max(1, (fim - inicio).days + 1 - len(meta.get("dias_sem_vendas", [])))

    est = pronto.estoque.copy()
    est["ean"] = cat.chaves_ean(est["ean"])
    compras = pronto.compras.copy()
    compras["ean"] = cat.chaves_ean(compras["ean"])
    vendas = pronto.vendas

    # -- produto (código da loja) → linha do pedido --------------------------
    if "vlr_preco_venda" not in est.columns:  # pronto montado antes da coluna existir
        est["vlr_preco_venda"] = np.nan
    produtos = pd.concat([
        est[["codigo_produto", "ean", "nome", "laboratorio", "qtd_estoque", "vlr_preco_compra", "vlr_preco_venda"]],
        compras[["codigo_produto", "ean", "laboratorio"]].drop_duplicates("codigo_produto"),
        vendas[["codigo_produto"]].drop_duplicates(),
    ], ignore_index=True).drop_duplicates("codigo_produto", keep="first").reset_index(drop=True)
    gen = genericos.drop_duplicates("ean").set_index("ean")
    id_gen = produtos["ean"].map(gen["base_generico_id"])
    produtos["linha"] = np.where(id_gen.notna(), "G" + id_gen.astype("Int64").astype(str), "P" + produtos["codigo_produto"].astype(str))
    produtos["nome_linha"] = produtos["ean"].map(gen["nome_canonico"]).fillna(produtos["nome"])
    produtos["nome_linha"] = produtos["nome_linha"].fillna("Produto " + produtos["codigo_produto"].astype(str))
    linha_do_produto = produtos.set_index("codigo_produto")["linha"]

    # -- vendas por linha ------------------------------------------------------
    v = vendas.assign(linha=vendas["codigo_produto"].map(linha_do_produto))
    corte_90 = (fim - dt.timedelta(days=p.giro_baixo_dias - 1)).isoformat()
    v90 = v[v["data"] >= corte_90]
    por_linha = pd.DataFrame({
        "unidades": v.groupby("linha")["unidades"].sum(),
        "valor": v.groupby("linha")["valor"].sum(),
    })
    por_linha["unidades_90d"] = v90.groupby("linha")["unidades"].sum().reindex(por_linha.index).fillna(0)
    # Coluna "Venda 90 dias" da tela (01/10/2026): só exibição — "vendeu 15 em
    # 90 dias" se entende melhor que "0,17 por dia". Fixa em 90, separada do
    # giro baixo (que tem os dias configuráveis); o cálculo segue na janela.
    v90_fixo = v[v["data"] >= (fim - dt.timedelta(days=89)).isoformat()]
    por_linha["venda_90d"] = v90_fixo.groupby("linha")["unidades"].sum().reindex(por_linha.index).fillna(0)
    por_linha = por_linha[por_linha["unidades"] > 0]
    if por_linha.empty:
        return pd.DataFrame(columns=COLUNAS)

    # -- estoque e cadastro por linha ------------------------------------------
    membros = produtos[produtos["linha"].isin(por_linha.index)]
    # Produto que mais vendeu dá o nome/laboratório de cadastro da linha.
    vendido = v.groupby("codigo_produto")["unidades"].sum()
    membros = membros.assign(_vendido=membros["codigo_produto"].map(vendido).fillna(0),
                             _estoque=membros["qtd_estoque"].fillna(0))
    principal = membros.sort_values(["linha", "_vendido"], ascending=[True, False]).drop_duplicates("linha").set_index("linha")
    grp = membros.groupby("linha")
    por_linha["nome"] = principal["nome_linha"]
    por_linha["laboratorio_cadastro"] = principal["laboratorio"]
    por_linha["ean_principal"] = principal["ean"]
    # Sem lambda por grupo: com 5 mil linhas, `agg(lambda)` custava 2,8 s
    # dos 4,6 s do cálculo (perfil de 28/09/2026, Hudson).
    com_ean = membros.dropna(subset=["ean"]).drop_duplicates(["linha", "ean"]).sort_values(["linha", "ean"])
    por_linha["eans"] = com_ean.groupby("linha")["ean"].agg(list).reindex(por_linha.index)
    por_linha["eans"] = [e if isinstance(e, list) else [] for e in por_linha["eans"]]
    # Negativo por EAN conta como 0 (não trava); o GPS "cru" fica pra mostrar.
    por_linha["estoque_gps"] = grp["_estoque"].sum()
    por_linha["estoque"] = membros["_estoque"].clip(lower=0).groupby(membros["linha"]).sum().reindex(por_linha.index)
    por_linha["negativo"] = (membros["_estoque"] < 0).groupby(membros["linha"]).any().reindex(por_linha.index)
    negativos = membros[membros["_estoque"] < 0]
    textos = [f"{e if isinstance(e, str) else 'sem EAN'} ({q:g})" for e, q in zip(negativos["ean"], negativos["_estoque"])]
    por_linha["eans_negativos"] = (pd.Series(textos, index=negativos.index, dtype="object")
                                   .groupby(negativos["linha"]).agg(", ".join).reindex(por_linha.index))
    por_linha["custo_cadastro"] = principal["vlr_preco_compra"]
    por_linha["preco_venda"] = pd.to_numeric(principal["vlr_preco_venda"], errors="coerce")
    corrigir = por_linha.index.isin(list(correcoes or {})) & por_linha["negativo"].to_numpy()
    por_linha["estoque_corrigido"] = corrigir
    if corrigir.any():
        por_linha.loc[corrigir, "estoque"] = [float(correcoes[l]) for l in por_linha.index[corrigir]]
    por_linha["generico"] = por_linha.index.str.startswith("G")

    # -- categoria --------------------------------------------------------------
    # Genérico da Base Genéricos é PRESCRIÇÃO GENÉRICO, qualquer que seja a
    # categoria de cada EAN dele. Os demais: a primeira categoria de verdade
    # entre os EANs da linha ("EM CLASSIFICAÇÃO" não conta).
    categoria_ean = base_categorias.drop_duplicates("ean").set_index("ean")["categoria"].astype("object")
    cats = com_ean.assign(categoria=com_ean["ean"].map(categoria_ean))
    cats = cats[cats["categoria"].notna() & (cats["categoria"] != cat.EM_CLASSIFICACAO)]
    por_linha["categoria"] = cats.groupby("linha")["categoria"].first().reindex(por_linha.index).astype("object")
    por_linha.loc[por_linha["generico"], "categoria"] = cat.GENERICO
    por_linha["categoria"] = por_linha["categoria"].where(por_linha["categoria"].notna(), None)
    por_linha["grupo"] = por_linha["categoria"].map(cat.grupo)

    # -- estoque alvo e sugestão ----------------------------------------------------
    por_linha["demanda_dia"] = por_linha["unidades"] / dias
    dias_alvo = por_linha["grupo"].map({cat.MEDICAMENTO: p.dias_medicamento, cat.PERFUMARIA: p.dias_perfumaria,
                                        cat.SEM_CLASSIFICACAO: p.dias_sem_classificacao})
    dias_alvo = dias_alvo.fillna(p.dias_sem_classificacao)
    proprios = dict(p.dias_por_categoria)
    if proprios:
        dias_alvo = por_linha["categoria"].map(proprios).fillna(dias_alvo)
    por_linha["estoque_max"] = [max(p.piso_maximo, _teto(d * a)) for d, a in zip(por_linha["demanda_dia"], dias_alvo)]
    por_linha["estoque_min"] = [max(1, _teto(d * p.dias_ruptura)) for d in por_linha["demanda_dia"]]
    por_linha["listar"] = por_linha["estoque"] < por_linha["estoque_max"]
    falta = (por_linha["estoque_max"] - por_linha["estoque"]).clip(lower=0)
    por_linha["sugestao"] = np.where(por_linha["listar"], np.ceil(falta - 1e-9), 0).astype(int)
    por_linha["ruptura"] = por_linha["estoque"] <= p.ruptura_unidades
    por_linha["ruptura_proxima"] = ~por_linha["ruptura"] & (por_linha["estoque"] <= por_linha["demanda_dia"] * p.dias_ruptura)
    por_linha["giro_baixo"] = por_linha["unidades_90d"] <= p.giro_baixo_max_unidades
    por_linha["curva_valor"] = _curva(por_linha["valor"], p.curva_a, p.curva_b)
    por_linha["curva_unidades"] = _curva(por_linha["unidades"], p.curva_a, p.curva_b)
    por_linha["curva"] = por_linha["curva_valor"] + por_linha["curva_unidades"]

    # -- compra de referência ----------------------------------------------------------
    c = compras.assign(linha=compras["codigo_produto"].map(linha_do_produto))
    c = c[c["linha"].isin(por_linha.index)]
    fracao = c["fracao"].where(c["fracao"] > 0, 1).fillna(1)
    c = c.assign(fracao=fracao, preco=c["vlr_unitario"].fillna(0) * (1 - c["vlr_desconto"].fillna(0) / 100) / fracao)
    c = c.assign(bonificado=c["preco"] < p.limite_bonificacao)
    ref = _compra_de_referencia(c, p.fator_preco_fora)
    for origem in _COLUNAS_COMPRA:
        por_linha["compra_" + origem] = ref[origem].reindex(por_linha.index) if origem in ref else None
    por_linha["compra_bonificado"] = ref["bonificado_recente"].reindex(por_linha.index).astype("boolean").fillna(False).astype(bool)
    por_linha["compra_a_revisar"] = ref["a_revisar"].reindex(por_linha.index).astype("boolean").fillna(False).astype(bool)
    por_linha["compra_ignorada_preco"] = ref["ignorada_preco"].reindex(por_linha.index)
    por_linha["compra_ignorada_data"] = ref["ignorada_data"].reindex(por_linha.index)
    por_linha["compra_preco"] = pd.to_numeric(por_linha["compra_preco"], errors="coerce")
    sem_compra = por_linha["compra_preco"].isna()
    por_linha["preco"] = por_linha["compra_preco"].where(~sem_compra, por_linha["custo_cadastro"])
    por_linha["preco_origem"] = np.where(sem_compra, np.where(por_linha["custo_cadastro"] > 0, "cadastro", "sem"), "compra")
    por_linha["laboratorio"] = por_linha["compra_laboratorio"].where(~sem_compra, por_linha["laboratorio_cadastro"])
    _ajustar_custo(por_linha, p)
    por_linha["orcamento"] = por_linha["sugestao"] * por_linha["preco"].fillna(0)
    frac = pd.to_numeric(por_linha["compra_fracao"], errors="coerce").fillna(1)
    por_linha["compra_fracao"] = frac
    por_linha["aviso_embalagem"] = (frac > 1) & (por_linha["sugestao"] > 0) & (por_linha["sugestao"] % frac != 0)

    # -- tags ----------------------------------------------------------------------
    marcas_status = [
        (por_linha["negativo"].to_numpy() & ~por_linha["estoque_corrigido"].to_numpy(), NEGATIVO),
        (por_linha["ruptura"].to_numpy(), RUPTURA),
        (por_linha["ruptura_proxima"].to_numpy(), RUPTURA_PROXIMA),
        (por_linha["estoque_corrigido"].to_numpy(), CORRIGIDO),
        ((por_linha["grupo"] == cat.SEM_CLASSIFICACAO).to_numpy(), SEM_CLASSIFICACAO),
        (por_linha["giro_baixo"].to_numpy(), GIRO_BAIXO),
    ]
    marcas_preco = [
        (por_linha["compra_bonificado"].to_numpy(), BONIFICADO),
        (por_linha["a_revisar"].to_numpy(), A_REVISAR),
        (por_linha["custo_ajustado"].notna().to_numpy(), CUSTO_AJUSTADO),
        ((por_linha["preco_origem"] == "cadastro").to_numpy(), CUSTO_CADASTRO),
    ]
    por_linha["status"] = _tags(marcas_status)
    por_linha["tags_preco"] = _tags(marcas_preco)

    return por_linha.rename_axis("linha").reset_index()[COLUNAS]


def _ajustar_custo(df: pd.DataFrame, p: Parametros) -> None:
    """Cadastro × nota pelo preço de venda (regra no topo do módulo). Grava,
    no próprio quadro: `preco_nota` (o preço da nota antes do ajuste),
    `custo_ajustado` ("cadastro" = usou o cadastro no lugar da nota; "nota" =
    a nota estava certa e o cadastro do GPS errado; None = sem ajuste),
    `a_revisar` (todas as razões juntas) e `revisar_motivo`."""
    nota = df["compra_preco"]
    cadastro = pd.to_numeric(df["custo_cadastro"], errors="coerce")
    venda = df["preco_venda"]
    tem_venda = venda > 0

    def coerente(x: pd.Series) -> pd.Series:
        return tem_venda & (x > 0) & (x >= venda * p.custo_faixa_min) & (x <= venda * p.custo_faixa_max)

    com_compra = nota.notna() & (nota > 0) & (cadastro > 0)
    razao = cadastro / nota.where(nota > 0)
    diverge = com_compra & ((razao > p.fator_cadastro_nota) | (razao < 1 / p.fator_cadastro_nota))
    cad_ok, nota_ok = coerente(cadastro), coerente(nota)
    usa_cadastro = diverge & cad_ok & ~nota_ok
    nota_certa = diverge & nota_ok & ~cad_ok
    sem_decisao = diverge & ~usa_cadastro & ~nota_certa
    cadastro_fora = (df["preco_origem"] == "cadastro") & tem_venda & ~cad_ok

    df["preco_nota"] = nota
    df.loc[usa_cadastro, "preco"] = cadastro[usa_cadastro]
    # Listas com None (não np.where): o pandas troca None por NaN, e a tela
    # testa "is None".
    df["custo_ajustado"] = pd.Series(["cadastro" if c else "nota" if n else None
                                      for c, n in zip(usa_cadastro, nota_certa)], index=df.index, dtype="object")
    df["a_revisar"] = df["compra_a_revisar"] | sem_decisao | cadastro_fora
    df["revisar_motivo"] = pd.Series([MOTIVO_DIVERGENCIA if d else MOTIVO_CADASTRO if f else MOTIVO_COMPRA if c else None
                                      for d, f, c in zip(sem_decisao, cadastro_fora, df["compra_a_revisar"])],
                                     index=df.index, dtype="object")


def _tags(marcas: list) -> list[list[str]]:
    nomes = [n for _, n in marcas]
    return [[n for marcado, n in zip(linha, nomes) if marcado] for linha in zip(*[m for m, _ in marcas])]


COLUNAS = [
    "linha", "nome", "generico", "ean_principal", "eans", "categoria", "grupo", "laboratorio", "laboratorio_cadastro",
    "unidades", "valor", "unidades_90d", "venda_90d", "demanda_dia", "estoque", "estoque_gps", "estoque_corrigido", "negativo",
    "eans_negativos", "estoque_min", "estoque_max",
    "sugestao", "listar", "ruptura", "ruptura_proxima", "giro_baixo", "curva", "curva_valor", "curva_unidades",
    "compra_data", "compra_preco", "compra_vlr_unitario", "compra_vlr_desconto", "compra_fracao", "compra_fornecedor",
    "compra_bonificado", "compra_a_revisar", "compra_ignorada_preco", "compra_ignorada_data",
    "custo_cadastro", "preco", "preco_origem", "orcamento", "aviso_embalagem", "status", "tags_preco",
    "preco_venda", "preco_nota", "custo_ajustado", "a_revisar", "revisar_motivo",
]


# ---------------------------------------------------------------------------
# Área de trabalho (quantidades digitadas e seleção) e exportação
# ---------------------------------------------------------------------------

def aplicar_area(linhas: pd.DataFrame, area: dict) -> pd.DataFrame:
    """Aplica a área de trabalho do usuário (integrations/pedido_area.py):
    `area`: linha → objeto com `quantidade` (None = segue a sugestão) e
    `selecionado` (None = marcado se a quantidade > 0 — Q1/Q2 de 29/09/2026).

    `quantidade` = o que vai no pedido; `alterado` = digitada à mão;
    `selecionado`; `subtotal` = preço da última compra × quantidade."""
    df = linhas.copy()
    qtd_digitada = [getattr(area.get(l), "quantidade", None) for l in df["linha"]]
    marca = [getattr(area.get(l), "selecionado", None) for l in df["linha"]]
    df["alterado"] = [q is not None and q != s for q, s in zip(qtd_digitada, df["sugestao"])]
    df["quantidade"] = [int(q) if q is not None else int(s) for q, s in zip(qtd_digitada, df["sugestao"])]
    df["selecionado"] = [bool(m) if m is not None else q > 0 for m, q in zip(marca, df["quantidade"])]
    df["subtotal"] = df["quantidade"] * df["preco"].fillna(0)
    df["orcamento"] = df["subtotal"]
    frac = df["compra_fracao"].fillna(1)
    df["aviso_embalagem"] = (frac > 1) & (df["quantidade"] > 0) & (df["quantidade"] % frac != 0)
    df["status"] = [s + [ALTERADO] if a else s for s, a in zip(df["status"], df["alterado"])]
    return df


def aplicar_rascunho(linhas: pd.DataFrame, rascunho: dict) -> pd.DataFrame:
    """Compatibilidade (Fase 5): rascunho da loja = só quantidades."""
    return aplicar_area(linhas, rascunho)


def exportacao(linhas: pd.DataFrame) -> pd.DataFrame:
    """PRODUTO, QUANTIDADE e os EANs em colunas (EAN 1, EAN 2…) — formato
    decidido em 27/09/2026 (Q29, opção B): o sistema de compra casa por
    qualquer um dos EANs do genérico. Só itens com quantidade > 0."""
    df = linhas[linhas["quantidade"] > 0]
    maximo = max([len(e) for e in df["eans"]] + [1])
    saida = pd.DataFrame({"PRODUTO": df["nome"].to_numpy(), "QUANTIDADE": df["quantidade"].astype(int).to_numpy()})
    for i in range(maximo):
        saida[f"EAN {i + 1}"] = [e[i] if len(e) > i else "" for e in df["eans"]]
    return saida


def _colunas_ean(df: pd.DataFrame, saida: pd.DataFrame) -> pd.DataFrame:
    maximo = max([len(e) for e in df["eans"]] + [1])
    for i in range(maximo):
        saida[f"EAN {i + 1}"] = [e[i] if len(e) > i else "" for e in df["eans"]]
    return saida


def tabela_completa(pedido: pd.DataFrame) -> pd.DataFrame:
    """Aba "Pedido" da exportação "Tabela completa" (01/10/2026): o pedido
    inteiro como está na tela (marcados ou não), com as colunas da tabela —
    pra conferir e discutir com a loja. O formato Gruppy (`exportacao`) é o
    que vai pro sistema de compra."""
    df = pedido
    saida = pd.DataFrame({
        "PRODUTO": df["nome"].to_numpy(),
        "CATEGORIA": df["categoria"].fillna("").to_numpy(),
        "FABRICANTE": df["laboratorio"].fillna("").to_numpy(),
        "CURVA": df["curva"].to_numpy(),
        "SITUAÇÃO": [", ".join(s) for s in df["status"]],
        "PREÇO (ÚLTIMA COMPRA)": pd.to_numeric(df["preco"], errors="coerce").round(2).to_numpy(),
        "DATA DA COMPRA": df["compra_data"].fillna("").astype(str).str[:10].to_numpy(),
        "A REVISAR": np.where(df["a_revisar"].astype(bool), "sim", ""),
        "VENDA 90 DIAS": df["venda_90d"].to_numpy(),
        "ESTOQUE": df["estoque"].to_numpy(),
        "MÍNIMO": df["estoque_min"].to_numpy(),
        "MÁXIMO": df["estoque_max"].to_numpy(),
        "SUGESTÃO": df["sugestao"].astype(int).to_numpy(),
        "QUANTIDADE": df["quantidade"].astype(int).to_numpy(),
        "SUBTOTAL": pd.to_numeric(df["subtotal"], errors="coerce").round(2).to_numpy(),
        "MARCADO": np.where(df["selecionado"].astype(bool), "sim", "não"),
    })
    return _colunas_ean(df, saida)


def giro_baixo(linhas: pd.DataFrame) -> pd.DataFrame:
    """Aba "Giro baixo" / exportação "Só giro baixo" (01/10/2026): a evidência
    pra loja — "vendeu 1 unidade em 90 dias e tem R$ X parado aqui". Os
    produtos de giro baixo que venderam na janela e TÊM ESTOQUE (com ou sem
    sugestão), o maior valor parado primeiro. Sem estoque não há nada parado:
    na Hudson eram 625 dos 2.260 de giro baixo (medido em 02/10/2026; ficam
    1.635, R$ 210 mil parados)."""
    df = linhas[linhas["giro_baixo"] & (linhas["estoque"] > 0)]
    preco = pd.to_numeric(df["preco"], errors="coerce")
    parado = (df["estoque"].clip(lower=0) * preco.fillna(0)).round(2)
    saida = pd.DataFrame({
        "PRODUTO": df["nome"].to_numpy(),
        "CATEGORIA": df["categoria"].fillna("").to_numpy(),
        "FABRICANTE": df["laboratorio"].fillna("").to_numpy(),
        "ESTOQUE": df["estoque"].to_numpy(),
        "VENDA 90 DIAS": df["venda_90d"].to_numpy(),
        "ÚLTIMA COMPRA": df["compra_data"].fillna("").astype(str).str[:10].to_numpy(),
        "PREÇO": preco.round(2).to_numpy(),
        "VALOR PARADO EM ESTOQUE": parado.to_numpy(),
    })
    saida = _colunas_ean(df, saida)
    return saida.sort_values("VALOR PARADO EM ESTOQUE", ascending=False, kind="stable").reset_index(drop=True)


@dataclass
class Indicadores:
    orcamento: float        # Σ subtotal dos marcados
    unidades: int           # Σ quantidade dos marcados
    itens: int              # SKUs com quantidade > 0 (o pedido inteiro)
    marcados: int           # desses, marcados
    ruptura: int            # tag Ruptura (estoque zerado), com ou sem pedido
    ruptura_sem_pedido: int # em ruptura e desmarcado ou com quantidade 0


def indicadores(linhas: pd.DataFrame) -> Indicadores:
    """Os 4 cards — sobre o PEDIDO INTEIRO (a lista com a escolha do "Ocultar
    giro baixo"), não sobre o filtro da tela (Q3b de 29/09/2026)."""
    if linhas.empty:
        return Indicadores(0.0, 0, 0, 0, 0, 0)
    qtd = linhas["quantidade"] if "quantidade" in linhas.columns else linhas["sugestao"]
    sel = linhas["selecionado"] if "selecionado" in linhas.columns else qtd > 0
    no_pedido = sel & (qtd > 0)
    sub = linhas["subtotal"] if "subtotal" in linhas.columns else linhas["orcamento"]
    return Indicadores(
        orcamento=float(sub[no_pedido].sum()),
        unidades=int(qtd[no_pedido].sum()),
        itens=int((qtd > 0).sum()),
        marcados=int(no_pedido.sum()),
        ruptura=int(linhas["ruptura"].sum()),
        ruptura_sem_pedido=int((linhas["ruptura"] & ~no_pedido).sum()),
    )


def lista(linhas: pd.DataFrame, ocultar_giro_baixo: bool = True) -> pd.DataFrame:
    """O pedido: as linhas abaixo do máximo, sem as de giro baixo quando o
    "Ocultar giro baixo" está ligado — ele tira o item do PEDIDO (cards e
    exportação), não só da tela (Q6b de 29/09/2026)."""
    df = linhas[linhas["listar"]]
    return df[~df["giro_baixo"]] if ocultar_giro_baixo else df


def filtrar(linhas: pd.DataFrame, busca: str | None = None, categoria: str | None = None,
            ocultar_giro_baixo: bool = True, filtros: "Filtros | None" = None) -> pd.DataFrame:
    """A lista (`lista`) com a busca e os filtros da tela. Busca: nome, EAN
    (qualquer um da linha) ou laboratório."""
    df = lista(linhas, ocultar_giro_baixo)
    if categoria:
        df = df[df["categoria"].fillna(SEM_CLASSIFICACAO) == categoria]
    if busca:
        termo = busca.strip().upper()
        digitos = "".join(ch for ch in termo if ch.isdigit())
        alvo = (df["nome"].fillna("").str.upper().str.contains(termo, regex=False)
                | df["laboratorio"].fillna("").str.upper().str.contains(termo, regex=False))
        if digitos:
            alvo |= df["eans"].map(lambda es: any(digitos in e for e in es))
        df = df[alvo]
    if filtros is not None:
        df = filtros.aplicar(df)
    return df


# ---------------------------------------------------------------------------
# Filtros da tela (pop-up "Filtros")
# ---------------------------------------------------------------------------

# Pílulas de Preço do pop-up, em ORDEM FIXA — a ordem dá a cor de cada
# pílula no CSS (core/theme.py, bloco "FILTROS DO PEDIDO"), assim como a de
# TAGS_STATUS dá a das pílulas de Situação. Mudou a ordem aqui, mude lá.
# "Sem compra" cobre o "custo de cadastro" (sem compra paga na janela) e o
# "sem preço nenhum" — não há pílula própria pra custo de cadastro (Q3 de
# 29/09/2026).
SEM_COMPRA = "Sem compra"
A_REVISAR_FILTRO = "A revisar"
TAGS_PRECO_FILTRO = [BONIFICADO, A_REVISAR_FILTRO, SEM_COMPRA]

# Campos de valor (busca do pop-up): campo → (rótulo, coluna da linha).
CAMPOS_VALOR = {
    "categoria": ("Categoria", "categoria"),
    "laboratorio": ("Laboratório", "laboratorio"),
    "fornecedor": ("Fornecedor", "compra_fornecedor"),
    "curva_valor": ("Curva por valor", "curva_valor"),
    "curva_unidades": ("Curva por unidades", "curva_unidades"),
    "grupo": ("Grupo", "grupo"),
    "tag": ("Situação", "status"),
    "preco": ("Preço", None),            # calculado por _precos_da_linha
}


def _precos_da_linha(df: pd.DataFrame) -> pd.Series:
    """As pílulas de Preço de cada linha (lista, na ordem de TAGS_PRECO_FILTRO)."""
    bonif = df["compra_bonificado"].astype(bool).to_numpy()
    revisar = df["a_revisar"].astype(bool).to_numpy()
    sem = (df["preco_origem"] != "compra").to_numpy()
    return pd.Series([[n for m, n in zip(t, TAGS_PRECO_FILTRO) if m] for t in zip(bonif, revisar, sem)],
                     index=df.index, dtype="object")


@dataclass(frozen=True)
class Filtros:
    """Filtros do pop-up. `valores`: ((campo, valor), …) — OU dentro do
    mesmo campo, E entre campos (vale também pras pílulas: Bonificado + A
    revisar = "um OU outro", desde 29/09/2026). Os numéricos: None = desligado.

    Saíram em 29/09/2026: `estoque_zerado` (é a pílula Ruptura, que segue o
    parâmetro `ruptura_unidades`) e `sem_compra`/`bonificado`/`a_revisar`
    (viraram ("preco", valor))."""
    valores: tuple = ()
    selecao: str | None = None            # "marcados" | "nao_marcados"
    cobertura_ate_dias: int | None = None
    estoque_de: float | None = None
    estoque_ate: float | None = None
    demanda_min: float | None = None
    ultima_compra_mais_de_dias: int | None = None
    hoje: str | None = None               # data de referência da "última compra há N dias" (não é salva)

    def quantos(self) -> int:
        n = len({c for c, _ in self.valores})
        n += int(bool(self.selecao))
        n += sum(x is not None for x in (self.cobertura_ate_dias, self.demanda_min, self.ultima_compra_mais_de_dias))
        n += int(self.estoque_de is not None or self.estoque_ate is not None)
        return n

    def erros(self) -> list[str]:
        if self.estoque_de is not None and self.estoque_ate is not None and self.estoque_de > self.estoque_ate:
            return [f"\"Estoque de\" ({self.estoque_de:g}) é maior que \"até\" ({self.estoque_ate:g}): nenhum item "
                    "caberia nessa faixa."]
        return []

    def aplicar(self, df: pd.DataFrame) -> pd.DataFrame:
        por_campo: dict[str, set] = {}
        for campo, valor in self.valores:
            por_campo.setdefault(campo, set()).add(valor)
        for campo, valores in por_campo.items():
            if campo not in CAMPOS_VALOR:
                continue  # filtro salvo com campo que não existe mais
            coluna = CAMPOS_VALOR[campo][1]
            if campo == "tag":
                df = df[df[coluna].map(lambda ts: bool(valores & set(ts)))]
            elif campo == "preco":
                df = df[_precos_da_linha(df).map(lambda ts: bool(valores & set(ts)))] if not df.empty else df
            elif campo == "grupo":
                df = df[df[coluna].map(lambda g: cat.ROTULO_GRUPO.get(g, g)).isin(valores)]
            elif campo == "categoria":
                df = df[df[coluna].fillna(SEM_CLASSIFICACAO).isin(valores)]
            else:
                df = df[df[coluna].isin(valores)]
        if self.selecao == "marcados" and "selecionado" in df:
            df = df[df["selecionado"]]
        elif self.selecao == "nao_marcados" and "selecionado" in df:
            df = df[~df["selecionado"]]
        if self.cobertura_ate_dias is not None:
            df = df[df["estoque"] <= df["demanda_dia"] * self.cobertura_ate_dias]
        if self.estoque_de is not None:
            df = df[df["estoque"] >= self.estoque_de]
        if self.estoque_ate is not None:
            df = df[df["estoque"] <= self.estoque_ate]
        if self.demanda_min is not None:
            df = df[df["demanda_dia"] >= self.demanda_min]
        if self.ultima_compra_mais_de_dias is not None and self.hoje:
            # Sem compra na janela (~4 meses) também entra: pra o sistema, a
            # última compra dele é "antes da janela".
            limite = (dt.date.fromisoformat(self.hoje) - dt.timedelta(days=self.ultima_compra_mais_de_dias)).isoformat()
            data = df["compra_data"].fillna("")
            df = df[(data == "") | (data < limite)]
        return df

    def para_dict(self) -> dict:
        """O que o "Salvar como meu filtro" grava (JSON) — sem `hoje`, que é
        do dia em que o filtro é usado, não de quando foi salvo."""
        return {
            "valores": [list(v) for v in self.valores], "selecao": self.selecao,
            "cobertura_ate_dias": self.cobertura_ate_dias, "estoque_de": self.estoque_de,
            "estoque_ate": self.estoque_ate, "demanda_min": self.demanda_min,
            "ultima_compra_mais_de_dias": self.ultima_compra_mais_de_dias,
        }

    @classmethod
    def de_dict(cls, dados: dict, hoje: str | None = None) -> "Filtros":
        """Lê um filtro salvo. Campo desconhecido (versão antiga ou mais nova
        do formato) é ignorado, em vez de quebrar a tela."""
        dados = dados or {}

        def inteiro(v):
            return int(v) if v is not None else None

        def real(v):
            return float(v) if v is not None else None

        return cls(
            valores=tuple(tuple(v) for v in dados.get("valores", []) if isinstance(v, (list, tuple)) and len(v) == 2),
            selecao=dados.get("selecao") if dados.get("selecao") in ("marcados", "nao_marcados") else None,
            cobertura_ate_dias=inteiro(dados.get("cobertura_ate_dias")), estoque_de=real(dados.get("estoque_de")),
            estoque_ate=real(dados.get("estoque_ate")), demanda_min=real(dados.get("demanda_min")),
            ultima_compra_mais_de_dias=inteiro(dados.get("ultima_compra_mais_de_dias")), hoje=hoje,
        )


def opcoes_de_valor(linhas: pd.DataFrame) -> list[tuple[str, str]]:
    """(campo, valor) de tudo que existe na lista da loja — as opções da
    busca do pop-up. Vêm da loja já calculada em memória (~4 ms na Reis F2,
    277 laboratórios; medido em 29/09/2026): nada de tabela única de todas as
    lojas (11,7 mi de linhas, 1,8 GB). Situação e Preço têm lista fixa
    (`contar_opcoes`), mas também entram aqui pra busca achar."""
    saida = []
    for campo, (_rotulo, coluna) in CAMPOS_VALOR.items():
        if campo == "tag":
            valores = list(TAGS_STATUS)
        elif campo == "preco":
            valores = list(TAGS_PRECO_FILTRO)
        elif campo == "grupo":
            valores = sorted({cat.ROTULO_GRUPO.get(g, g) for g in linhas[coluna].dropna()})
        elif campo == "categoria":
            valores = sorted(set(linhas[coluna].fillna(SEM_CLASSIFICACAO)))
        else:
            valores = sorted({str(v) for v in linhas[coluna].dropna() if str(v).strip()})
        saida.extend((campo, v) for v in valores)
    return saida


def contar_opcoes(pedido: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Quantos itens do PEDIDO INTEIRO cada pílula traria sozinha (Q1 de
    29/09/2026: não depende dos outros filtros, que o formulário não
    recalcula a cada clique). Lista fixa, na ordem, com 0 nas vazias."""
    situacao = {t: 0 for t in TAGS_STATUS}
    for ts in pedido["status"]:
        for t in ts:
            if t in situacao:
                situacao[t] += 1
    preco = {t: 0 for t in TAGS_PRECO_FILTRO}
    if not pedido.empty:
        for ts in _precos_da_linha(pedido):
            for t in ts:
                preco[t] += 1
    return {"tag": situacao, "preco": preco}
