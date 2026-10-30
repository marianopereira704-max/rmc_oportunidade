"""Dashboard — as contas (21/10/2026). Funções puras sobre o resultado do
`pedido/calculo.calcular` (Assistente de pedido): sem Streamlit, sem banco,
sem leitura de arquivo. A tela (`views/dashboard_*.py`) só desenha o que sai
daqui, e o teste confere cada número.

Por que não reaproveitar as colunas prontas do cálculo (`ruptura`,
`giro_baixo`): no Assistente o objetivo é SUGERIR COMPRA, então a "venda
pontual" (giro baixo de curva A/B) sai da ruptura e do giro baixo, e o giro
baixo é filtrado do pedido. No Dashboard o objetivo é ANÁLISE — produto sem
estoque é ruptura, vendendo pouco ou muito (decisão do Mariano, 21/10/2026).
Por isso a ruptura e o parado são recalculados aqui a partir do estoque e das
unidades vendidas, com os mesmos limites da configuração da loja.

O Assistente e o Dashboard são DUAS VISÕES: o Assistente diz quanto comprar
(o "ideal" de 7 a 15 dias de venda é a meta da compra); o Dashboard diz como
está o estoque. Por isso o Dashboard só usa do Assistente o dado de base
(estoque, venda por dia, custo, curva) e NUNCA o ideal dele.

Definições (medidas em 18 lojas reais, 21/10/2026):
- Ruptura: estoque até `ruptura_unidades` (padrão 0), vendendo muito ou pouco.
  Numa loja de 3.915 produtos, 1.220 (31%) — 702 deles de giro baixo. A tela
  mostra os dois números, em vez de esconder os de giro baixo.
- Estoque em excesso: as unidades que passam de `dias_cobertura_excesso` dias
  de venda (configurável por loja), a custo. Produto com estoque para 90 dias
  e limite de 60 tem 30 dias em excesso, e os outros 60 são estoque eficiente.
  A primeira versão usava o ideal do Assistente como régua (a parte acima de
  7 dias), o que marcava 60% do estoque da loja 60 como excesso: era meta de
  compra usada como régua de análise.
- Estoque parado: tem estoque e vendeu até `giro_baixo_max_unidades` na janela
  do giro baixo. Não conta também como excesso (cada real do estoque está em
  uma só classe: eficiente, excesso ou parado).
- Venda em risco por dia: demanda por dia × preço de venda dos itens em
  ruptura. É risco, não perda: não medimos há quantos dias o item faltou.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from pedido import classificacao

CURVAS = ("A", "B", "C")
# Situação de cada produto na lista de críticos, da mais grave para a menos.
RUPTURA, RUPTURA_PROXIMA, PARADO, EXCESSO, EFICIENTE = "ruptura", "ruptura_proxima", "parado", "excesso", "eficiente"
ROTULO_SITUACAO = {RUPTURA: "Ruptura", RUPTURA_PROXIMA: "Ruptura próxima", PARADO: "Parado", EXCESSO: "Excesso",
                   EFICIENTE: "Eficiente"}

# As colunas que `preparar` acrescenta; o resto do quadro é o do cálculo.
COLUNAS_NOVAS = ["custo", "estoque_pos", "cobertura", "em_ruptura", "em_ruptura_proxima", "giro_baixo_real", "parado",
                 "em_excesso", "valor_estoque", "valor_excesso", "valor_parado", "valor_eficiente", "venda_risco_dia",
                 "curva_valor_letra", "linha_compra", "situacao"]


def preparar(df: pd.DataFrame, config, linha: pd.Series | None = None) -> pd.DataFrame:
    """O quadro do cálculo do Assistente + as colunas do Dashboard.

    `config`: a `ConfigPedido` EFETIVA da loja (padrão + personalização).
    `linha`: a linha de compra de cada produto, alinhada ao `df` (a tela monta
    com a cascata de classificação, `pedido/classificacao.coluna`); sem ela,
    tudo "Em classificação".

    Estoque negativo já vem como 0 do cálculo (e com a correção manual do
    Assistente, se houver); aqui o `clip` garante que uma linha estranha nunca
    vire valor de estoque negativo nos cards."""
    d = df.copy()
    if d.empty:
        for c in COLUNAS_NOVAS:
            d[c] = pd.Series(dtype="object")
        return d
    estoque = pd.to_numeric(d["estoque"], errors="coerce").fillna(0).clip(lower=0)
    demanda = pd.to_numeric(d["demanda_dia"], errors="coerce").fillna(0)
    # Custo = preço da última compra (já com as regras de cadastro × nota do
    # Assistente). Sem custo conhecido, o produto não entra nos valores em R$.
    custo = pd.to_numeric(d["preco"], errors="coerce").fillna(0).clip(lower=0)
    d["custo"] = custo
    d["estoque_pos"] = estoque
    d["cobertura"] = np.where(demanda > 0, estoque / demanda.where(demanda > 0), np.nan)

    d["em_ruptura"] = estoque <= config.ruptura_unidades
    d["em_ruptura_proxima"] = ~d["em_ruptura"] & (estoque <= demanda * config.dias_ruptura)
    # Giro baixo "de verdade": inclui a venda pontual, que o Assistente separa.
    d["giro_baixo_real"] = pd.to_numeric(d["unidades_90d"], errors="coerce").fillna(0) <= config.giro_baixo_max_unidades
    d["parado"] = d["giro_baixo_real"] & (estoque > 0)
    # Excesso = o que passa de N dias de venda (régua do Dashboard, não o ideal
    # do Assistente). Parado e ruptura ficam de fora: cada real, uma classe só.
    limite_unidades = demanda * config.dias_cobertura_excesso
    acima = (estoque - limite_unidades).clip(lower=0)
    d["em_excesso"] = ~d["parado"] & ~d["em_ruptura"] & (acima > 0)

    d["valor_estoque"] = estoque * custo
    d["valor_parado"] = np.where(d["parado"], d["valor_estoque"], 0.0)
    d["valor_excesso"] = np.where(d["em_excesso"], acima * custo, 0.0)
    d["valor_eficiente"] = d["valor_estoque"] - d["valor_parado"] - d["valor_excesso"]
    preco_venda = pd.to_numeric(d["preco_venda"], errors="coerce").fillna(0).clip(lower=0)
    d["venda_risco_dia"] = np.where(d["em_ruptura"], demanda * preco_venda, 0.0)

    d["curva_valor_letra"] = d["curva"].astype(str).str[:1]
    # Sem a linha calculada pela tela (testes, ou nenhuma fonte de classificação): tudo "Em classificação".
    d["linha_compra"] = (linha if linha is not None else classificacao.coluna(d, {})).reindex(d.index)
    d["situacao"] = np.select(
        [d["em_ruptura"], d["em_ruptura_proxima"], d["parado"], d["em_excesso"]],
        [RUPTURA, RUPTURA_PROXIMA, PARADO, EXCESSO], default=EFICIENTE)
    return d


def _pct(parte: float, todo: float) -> float:
    return float(parte / todo * 100) if todo else 0.0


def estoque_por_linha(d: pd.DataFrame) -> pd.DataFrame:
    """Estoque a custo por linha de compra, em eficiente / excesso / parado
    (as três somam o estoque da linha). Do maior para o menor."""
    colunas = ["eficiente", "excesso", "parado", "total"]
    if d.empty:
        return pd.DataFrame(columns=colunas, index=pd.Index([], name="linha"))
    g = d.groupby("linha_compra")
    saida = pd.DataFrame({"eficiente": g["valor_eficiente"].sum(), "excesso": g["valor_excesso"].sum(),
                          "parado": g["valor_parado"].sum()})
    saida["total"] = saida.sum(axis=1)
    saida.index.name = "linha"
    return saida.sort_values("total", ascending=False)


def criticos(d: pd.DataFrame) -> pd.DataFrame:
    """Produtos que pedem atenção: tudo que não é "eficiente". Ordem: venda em
    risco por dia (as rupturas que mais custam), depois o valor parado/em
    excesso. O desempate pelo nome deixa a ordem estável entre cliques."""
    if d.empty:
        return d
    c = d[d["situacao"] != EFICIENTE].copy()
    c["_peso"] = c["valor_parado"] + c["valor_excesso"]
    return (c.sort_values(["venda_risco_dia", "_peso", "nome"], ascending=[False, False, True], kind="stable")
             .drop(columns="_peso"))


TAMANHO_PAGINA = 25   # no máximo 25 linhas por tabela (21/10/2026): o app não pode quebrar com tabela enorme


def fatiar(d: pd.DataFrame, pagina: int, tamanho: int = TAMANHO_PAGINA) -> tuple[pd.DataFrame, int, int]:
    """(as linhas da página, a página corrigida, o total de páginas). Página
    fora do intervalo (o filtro mudou e a lista encolheu) vira a mais próxima."""
    total = max(1, -(-len(d) // tamanho))
    pagina = min(max(1, int(pagina)), total)
    return d.iloc[(pagina - 1) * tamanho: pagina * tamanho], pagina, total


def filtrar(d: pd.DataFrame, linhas=(), curvas=()) -> pd.DataFrame:
    """O filtro cruzado da tela. Várias da MESMA dimensão somam (OU); dimensões
    diferentes se combinam (E)."""
    if d.empty:
        return d
    mascara = pd.Series(True, index=d.index)
    if linhas:
        mascara &= d["linha_compra"].isin(set(linhas))
    if curvas:
        mascara &= d["curva_valor_letra"].isin(set(curvas))
    return d[mascara]


def sem_filtro_de(d: pd.DataFrame, dimensao: str, linhas=(), curvas=()) -> pd.DataFrame:
    """O quadro filtrado por TUDO MENOS a dimensão do próprio gráfico: a barra
    de Genéricos não pode fazer as outras barras sumirem. `dimensao`: "linha"
    ou "curva"."""
    return filtrar(d, linhas=() if dimensao == "linha" else linhas, curvas=() if dimensao == "curva" else curvas)


# ---------------------------------------------------------------------------
# Eixo dos gráficos
# ---------------------------------------------------------------------------

def _passo_bonito(alvo: float, inteiro: bool = False) -> float:
    """Menor 1, 2, 2,5, 5 ou 10 × 10ⁿ que não é menor que `alvo`. O 2,5 (marcas
    0, 250, 500, 750, 1.000) evita pular de 250 para 500: com só 1-2-5, uma
    barra de R$ 829 ficava numa escala até R$ 2 mil, usando 41% da altura
    (visto na tela em 21/10/2026). `inteiro`: gráfico de contagem — o passo
    precisa ser inteiro, então o 2,5 só vale de 25 em diante."""
    if alvo <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(alvo))
    for f in (1, 2, 2.5, 5, 10):
        passo = f * p
        if inteiro and abs(passo - round(passo)) > 1e-9:
            continue
        if passo >= alvo * (1 - 1e-9):
            return float(passo)
    return float(10 * p)


def eixo(valores, marcas: int = 5, inteiro: bool = False) -> tuple[float, float, list[float]]:
    """Eixo Y de todos os gráficos: (mínimo, máximo, marcas), SEMPRE com 5
    marcas (4 intervalos iguais) e passo 1-2-2,5-5 × 10ⁿ. Sem valor negativo, parte
    do zero. Com negativo (opção A de 21/10/2026: quase impossível, mas o
    gráfico não pode quebrar), o eixo desce abaixo do zero; a divisão entre
    parte negativa e positiva é a que dá o MENOR passo, e o zero é sempre uma
    das marcas. `inteiro`: gráfico de contagem (itens em ruptura) — o passo
    nunca é menor que 1, para o eixo não mostrar "1,5 item"."""
    v = pd.to_numeric(pd.Series(list(valores), dtype="float64"), errors="coerce").dropna()
    alto = max(float(v.max()), 0.0) if len(v) else 0.0
    baixo = min(float(v.min()), 0.0) if len(v) else 0.0
    intervalos = marcas - 1
    piso = 1.0 if inteiro else 0.0

    def bonito(alvo: float) -> float:
        return _passo_bonito(max(alvo, piso), inteiro)

    if baixo == 0:
        passo, n_neg = bonito(alto / intervalos), 0
    elif alto == 0:
        passo, n_neg = bonito(-baixo / intervalos), intervalos
    else:
        melhor = None
        for k in range(1, intervalos):    # k intervalos abaixo do zero, o resto acima
            candidato = bonito(max(-baixo / k, alto / (intervalos - k)))
            if melhor is None or candidato < melhor[0]:
                melhor = (candidato, k)
        passo, n_neg = melhor
    minimo = -n_neg * passo
    return minimo, minimo + intervalos * passo, [minimo + i * passo for i in range(marcas)]
