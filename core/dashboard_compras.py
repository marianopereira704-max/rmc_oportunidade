"""Contas da página Compras do Dashboard (27/10/2026, design em docs/specs/2026-10-27-dashboard-compras-design.md).

Sem Streamlit e sem banco: recebe as compras item a item e as vendas por produto e dia da loja (o "pronto" do
Assistente), a linha do pedido de cada código de produto e um quadro `info` por linha (nome, EANs, categoria, custo,
estoque ideal, estoque e demanda do Assistente), e devolve o quadro da página e as agregações. Regras do design:

- compra = item de nota; unidades = quantidade × fração; preço un. = a regra da casa (sem ST); bonificação entra nas
  unidades e vale R$ 0 (R1, R2);
- produto = a linha do Assistente, inclusive o comprado que nunca vendeu (R3);
- sem demanda = comprou no período e não vendeu nada de N dias antes da 1ª compra do período até o fim dele (R6);
- limite = vendido no período + estoque ideal; excesso = parte inteira de (comprado − limite); em R$, as unidades acima
  do limite são primeiro as bonificadas (Q17 B), o resto vale o custo médio das unidades pagas (R7);
- compra certa = consumido − excesso − sem demanda; eficiência = certa ÷ consumido (R8);
- compra × venda = consumido ÷ venda a custo (R5);
- dotação por CATEGORIA, mês a mês: compras da categoria no mês anterior × a variação da venda POR SEMANA (R9, R11):
  semanas fixas 1–7, 8–14, 15–21 e 22 ao fim do mês; em cada semana COMPLETA, a venda média por dia contra a da mesma
  semana do mês anterior, sem os "dias fora da conta" (a loja inteira vendeu menos de 40% da mediana do mesmo dia da
  semana nas 8 semanas anteriores: dado incompleto ou feriado). Sem semana completa, fator 1. Um grupo com várias
  categorias soma a dotação de cada uma — a variação de uma não corrige a outra (Q5 B).
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CERTA, EXCESSO, SEM_DEMANDA = "certa", "excesso", "sem_demanda"
ROTULO_CLASSE = {EXCESSO: "Excesso", SEM_DEMANDA: "Sem demanda"}
ATUAL, PASSADO, TRES = "atual", "passado", "tres"
ROTULO_PERIODO = {ATUAL: "Mês atual", PASSADO: "Mês passado", TRES: "Últimos 3 meses"}
NIVEIS_DETALHE = (("iqvia", "fonte_setor"), ("febrafar", "fonte_febrafar"), ("cmed", "fonte_cmed"))


@dataclass(frozen=True)
class Regras:
    """Os números da página (R13): quando um dia fica fora da conta da dotação (abaixo de `fora_pct` da mediana do
    mesmo dia da semana nas `fora_semanas` anteriores), os dias sem venda da "sem demanda" e o limite da bonificação
    (o mesmo da Análise e do Assistente)."""
    fora_pct: float = 0.40
    fora_semanas: int = 8
    sem_demanda_dias: int = 90
    limite_bonificacao: float = 0.10

    @classmethod
    def da_config(cls, config, limite_bonificacao: float = 0.10) -> "Regras":
        p = cls()
        return cls(fora_pct=float(getattr(config, "compras_dia_fora_pct", p.fora_pct)),
                   fora_semanas=int(getattr(config, "compras_dia_fora_semanas", p.fora_semanas)),
                   sem_demanda_dias=int(getattr(config, "compras_sem_demanda_dias", p.sem_demanda_dias)),
                   limite_bonificacao=float(limite_bonificacao))


@dataclass(frozen=True)
class Periodo:
    """Um período da página: de `inicio` a `fim` e os meses dele, cada um como (1º dia, último dia com dado)."""
    chave: str
    inicio: dt.date
    fim: dt.date
    meses: tuple = field(default_factory=tuple)


def _mes_anterior(primeiro: dt.date) -> tuple[dt.date, dt.date]:
    fim = primeiro - dt.timedelta(days=1)
    return fim.replace(day=1), fim


def periodos(ultimo_dia: dt.date, inicio_janela: dt.date) -> dict[str, Periodo]:
    """Os períodos que a janela de dados da loja cobre (R4). `ultimo_dia` = o último dia com venda baixada (a véspera
    da foto). Cada período precisa também do mês ANTERIOR ao seu 1º mês (é a base da dotação): sem ele, o período
    não aparece."""
    atual = (ultimo_dia.replace(day=1), ultimo_dia)
    passado = _mes_anterior(atual[0])
    retrasado = _mes_anterior(passado[0])
    candidatos = {
        ATUAL: (atual,),
        PASSADO: (passado,),
        TRES: (retrasado, passado, atual),
    }
    saida = {}
    for chave, meses in candidatos.items():
        base = _mes_anterior(meses[0][0])
        if base[0] >= inicio_janela:
            saida[chave] = Periodo(chave, meses[0][0], meses[-1][1], meses)
    return saida


# ---------------------------------------------------------------------------
# Compras e vendas por linha
# ---------------------------------------------------------------------------

def itens(compras: pd.DataFrame, linha_do_codigo: dict, limite_bonificacao: float) -> pd.DataFrame:
    """Um item de nota por linha, com a linha do pedido, as unidades, o preço un., o valor e se é bonificação."""
    colunas = ["data", "linha", "codigo_produto", "unidades", "preco", "valor", "bonificado", "nro_nf", "fornecedor"]
    if compras.empty:
        return pd.DataFrame(columns=colunas)
    fracao = pd.to_numeric(compras["fracao"], errors="coerce")
    fracao = fracao.where(fracao > 0, 1).fillna(1)
    preco = (pd.to_numeric(compras["vlr_unitario"], errors="coerce").fillna(0)
             * (1 - pd.to_numeric(compras["vlr_desconto"], errors="coerce").fillna(0) / 100) / fracao)
    unidades = pd.to_numeric(compras["quantidade"], errors="coerce").fillna(0) * fracao
    bonificado = preco < limite_bonificacao
    saida = pd.DataFrame({
        "data": pd.to_datetime(compras["data"]).dt.normalize(),
        "linha": compras["codigo_produto"].astype(str).map(linha_do_codigo),
        "codigo_produto": compras["codigo_produto"].astype(str),
        "unidades": unidades,
        "preco": preco.where(~bonificado, 0.0),
        "valor": (unidades * preco).where(~bonificado, 0.0),
        "bonificado": bonificado,
        "nro_nf": compras["nro_nf"] if "nro_nf" in compras else None,
        "fornecedor": compras["fornecedor"] if "fornecedor" in compras else None,
    })
    return saida[saida["linha"].notna() & (saida["unidades"] > 0)].reset_index(drop=True)


def vendas_por_linha(vendas: pd.DataFrame, linha_do_codigo: dict) -> pd.DataFrame:
    """(linha, dia): unidades e valor vendidos."""
    if vendas.empty:
        return pd.DataFrame(columns=["linha", "data", "unidades", "valor"])
    v = vendas.assign(linha=vendas["codigo_produto"].astype(str).map(linha_do_codigo),
                      data=pd.to_datetime(vendas["data"]).dt.normalize())
    v = v[v["linha"].notna()]
    return v.groupby(["linha", "data"], as_index=False).agg(unidades=("unidades", "sum"), valor=("valor", "sum"))


def _entre(df: pd.DataFrame, inicio: dt.date, fim: dt.date) -> pd.DataFrame:
    return df[(df["data"] >= pd.Timestamp(inicio)) & (df["data"] <= pd.Timestamp(fim))]


# Semanas fixas do mês (28/10/2026): a 4ª vai do dia 22 ao fim, com 7 a 10 dias conforme o mês — por isso a conta
# é pela venda MÉDIA POR DIA de cada semana. A 1ª versão comparava os dias 1 a N com os mesmos dias do mês anterior:
# na loja 60, outubro (até 20/10) misturava dias da semana diferentes e dois dias úteis que vieram quase vazios do
# sistema de vendas (06/10 R$ 893, 07/10 R$ 1.223, contra R$ 9–15 mil), e a venda de todas as categorias "caía"
# 22–50%.
SEMANAS = ((1, 7), (8, 14), (15, 21), (22, 31))


def dias_fora(vendas: pd.DataFrame, regras: Regras) -> frozenset:
    """Os dias que saem da conta da dotação: a LOJA INTEIRA vendeu menos de `fora_pct` da mediana do mesmo dia da
    semana nas `fora_semanas` anteriores (com ao menos 3 delas na janela). Pega dia que veio incompleto e feriado (dia
    sem nenhuma venda conta como zero). Mediana e não média (testado na loja 60, 28/10/2026): os poucos domingos
    abertos puxavam a média dos domingos para cima e todo domingo fechado saía da conta — agosto passava de −3% para
    +12% contra julho. Com a mediana, domingo fechado é o normal do domingo e fica."""
    if vendas.empty:
        return frozenset()
    total = vendas.groupby("data")["valor"].sum()
    total = total.reindex(pd.date_range(total.index.min(), total.index.max()), fill_value=0.0)
    ref = pd.concat([total.shift(7 * i) for i in range(1, regras.fora_semanas + 1)], axis=1)
    fora = (ref.notna().sum(axis=1) >= 3) & (total < regras.fora_pct * ref.median(axis=1))
    return frozenset(total.index[fora])


def _pesos(primeiro: dt.date, ultimo: dt.date, fora: frozenset) -> tuple[pd.Series, pd.Series]:
    """O peso de cada dia na variação do mês (R9): em cada semana completa até `ultimo`, 1 ÷ os dias que ficam na
    conta — no mês e na mesma semana do mês anterior. Somar venda × peso dá a soma das médias por dia das semanas."""
    ini_ant, fim_ant = _mes_anterior(primeiro)
    fim_mes = calendar.monthrange(primeiro.year, primeiro.month)[1]
    atual, anterior = {}, {}
    for a, b in SEMANAS:
        if ultimo.day < min(b, fim_mes):
            break
        dias_m = [d for d in pd.date_range(primeiro.replace(day=a), primeiro.replace(day=min(b, fim_mes)))
                  if d not in fora]
        dias_p = [d for d in pd.date_range(ini_ant.replace(day=a), ini_ant.replace(day=min(b, fim_ant.day)))
                  if d not in fora]
        if dias_m and dias_p:
            atual.update(dict.fromkeys(dias_m, 1 / len(dias_m)))
            anterior.update(dict.fromkeys(dias_p, 1 / len(dias_p)))
    return pd.Series(atual, dtype=float), pd.Series(anterior, dtype=float)


def _ponderado(vendas: pd.DataFrame, pesos: pd.Series, chave: pd.Series) -> pd.Series:
    """Σ venda × peso do dia, agrupado por `chave` (a linha ou a categoria de cada venda)."""
    v = vendas[vendas["data"].isin(pesos.index)]
    return (v["valor"] * v["data"].map(pesos)).groupby(chave.reindex(v.index)).sum()


# ---------------------------------------------------------------------------
# O quadro da página
# ---------------------------------------------------------------------------

def preparar(itens_: pd.DataFrame, vendas: pd.DataFrame, info: pd.DataFrame, periodo: Periodo,
             regras: Regras) -> pd.DataFrame:
    """Uma linha do Assistente por linha do quadro: toda linha que comprou ou vendeu no período, ou comprou no mês
    anterior a algum mês do período (base da dotação). `info` é indexado pela linha (colunas opcionais: nome,
    ean_principal, categoria, custo, ideal, estoque, demanda_dia, fonte_*)."""
    cp = _entre(itens_, periodo.inicio, periodo.fim)
    vp = _entre(vendas, periodo.inicio, periodo.fim)
    g = cp.groupby("linha")
    d = pd.DataFrame({
        "comprado_un": g["unidades"].sum(),
        "bonif_un": cp[cp["bonificado"]].groupby("linha")["unidades"].sum(),
        "consumido": g["valor"].sum(),
        "primeira_compra": g["data"].min(),
    })
    gv = vp.groupby("linha")
    vend = pd.DataFrame({"vendido_un": gv["unidades"].sum(), "venda_valor": gv["valor"].sum()})
    # Dotação (R9): por mês do período, as compras do mês anterior e a venda ponderada pelas semanas completas
    # (vc = o mês, vp = o mês anterior; a soma por categoria dá o fator).
    fora = dias_fora(vendas, regras)
    dot = {}
    for k, (primeiro, ultimo) in enumerate(periodo.meses):
        ini_ant, fim_ant = _mes_anterior(primeiro)
        pesos_m, pesos_p = _pesos(primeiro, ultimo, fora)
        dot[f"base_{k}"] = _entre(itens_, ini_ant, fim_ant).groupby("linha")["valor"].sum()
        dot[f"vc_{k}"] = _ponderado(vendas, pesos_m, vendas["linha"])
        dot[f"vp_{k}"] = _ponderado(vendas, pesos_p, vendas["linha"])
        dot[f"cons_{k}"] = _entre(itens_, primeiro, ultimo).groupby("linha")["valor"].sum()
    dot = pd.DataFrame(dot)
    indice = d.index.union(vend.index).union(dot.index)
    d = d.reindex(indice).join(vend.reindex(indice)).join(dot.reindex(indice))
    d.index.name = "linha"
    numericas = [c for c in d.columns if c != "primeira_compra"]
    d[numericas] = d[numericas].astype(float).fillna(0.0)

    info = info.reindex(indice)
    for coluna, padrao in (("nome", None), ("ean_principal", None), ("categoria", None), ("custo", np.nan),
                           ("ideal", 0.0), ("estoque", 0.0), ("demanda_dia", 0.0)):
        d[coluna] = info[coluna] if coluna in info else padrao
    for _, coluna in NIVEIS_DETALHE:
        d[coluna] = info[coluna] if coluna in info else None
    d["nome"] = d["nome"].fillna(pd.Series(indice, index=indice).map(lambda x: f"Produto {x[1:]}"))
    for coluna in ("ideal", "estoque", "demanda_dia"):
        d[coluna] = pd.to_numeric(d[coluna], errors="coerce").fillna(0.0)
    d["custo"] = pd.to_numeric(d["custo"], errors="coerce")

    # Compra × venda (R5): a venda a custo; linha sem custo fica fora.
    d["venda_custo"] = (d["vendido_un"] * d["custo"]).fillna(0.0)

    # Sem demanda (R6): nenhuma venda de N dias antes da 1ª compra do período até o fim dele.
    comprou = d["comprado_un"] > 0
    d["sem_demanda"] = False
    if comprou.any():
        inicio = (d.loc[comprou, "primeira_compra"] - pd.Timedelta(days=regras.sem_demanda_dias)).rename("_desde")
        v = vendas[vendas["linha"].isin(inicio.index)].join(inicio, on="linha")
        v = v[(v["data"] >= v["_desde"]) & (v["data"] <= pd.Timestamp(periodo.fim))]
        vendeu = v.groupby("linha")["unidades"].sum().reindex(inicio.index).fillna(0)
        d.loc[comprou, "sem_demanda"] = (vendeu <= 0).to_numpy()

    # Limite e excesso (R7), com as bonificadas primeiro.
    d["pagas_un"] = d["comprado_un"] - d["bonif_un"]
    d["custo_medio"] = (d["consumido"] / d["pagas_un"].where(d["pagas_un"] > 0)).fillna(0.0)
    d["limite"] = d["vendido_un"] + d["ideal"]
    excesso = np.floor((d["comprado_un"] - d["limite"]).clip(lower=0) + 1e-9)
    d["excesso_un"] = excesso.where(comprou & ~d["sem_demanda"], 0.0)
    d["excesso_pagas"] = np.minimum((d["excesso_un"] - d["bonif_un"]).clip(lower=0), d["pagas_un"].clip(lower=0))
    d["excesso_rs"] = d["excesso_pagas"] * d["custo_medio"]
    d["sem_demanda_rs"] = d["consumido"].where(d["sem_demanda"], 0.0)
    d["nao_otimizado"] = d["excesso_rs"] + d["sem_demanda_rs"]
    d["certa"] = d["consumido"] - d["nao_otimizado"]
    d["classe"] = np.select([d["sem_demanda"], d["excesso_un"] > 0, comprou], [SEM_DEMANDA, EXCESSO, CERTA], None)
    return d


@dataclass(frozen=True)
class Contexto:
    """O que as agregações precisam além do quadro: os meses do período e as regras."""
    periodo: Periodo
    regras: Regras


def _fator(vc: float, vp: float) -> float:
    """Sem semana completa (ou sem venda dela no mês anterior), vp = 0 e o fator é 1."""
    return vc / vp if vp > 0 else 1.0


def dotacao(g: pd.DataFrame, ctx: Contexto) -> float:
    """A dotação do grupo: por categoria e por mês, base × fator, somados (R9, Q5 B)."""
    if g.empty:
        return 0.0
    total = 0.0
    for _, c in g.groupby(g["categoria"].fillna("")):
        for k in range(len(ctx.periodo.meses)):
            total += c[f"base_{k}"].sum() * _fator(c[f"vc_{k}"].sum(), c[f"vp_{k}"].sum())
    return float(total)


def variacao(g: pd.DataFrame, ctx: Contexto) -> float | None:
    """A variação da venda do último mês do período, pelas semanas completas (o "venda +12%" da tabela, que só aparece
    no Mês atual). None antes de fechar a 1ª semana."""
    if g.empty:
        return None
    k = len(ctx.periodo.meses) - 1
    vp = g[f"vp_{k}"].sum()
    return float(g[f"vc_{k}"].sum() / vp - 1) if vp > 0 else None


def _pct(parte, todo) -> float | None:
    return float(parte / todo * 100) if todo else None


def metricas(g: pd.DataFrame, ctx: Contexto) -> dict:
    consumido = float(g["consumido"].sum())
    dot = dotacao(g, ctx)
    m = {
        "dotacao": dot, "consumido": consumido, "venda_custo": float(g["venda_custo"].sum()),
        "sem_demanda": float(g["sem_demanda_rs"].sum()), "excesso": float(g["excesso_rs"].sum()),
        "nao_otimizado": float(g["nao_otimizado"].sum()), "certa": float(g["certa"].sum()),
        "variacao": variacao(g, ctx),
    }
    m["compra_venda"] = _pct(consumido, m["venda_custo"])
    m["eficiencia"] = _pct(m["certa"], consumido)
    m["pct_sem_demanda"] = _pct(m["sem_demanda"], consumido)
    m["pct_excesso"] = _pct(m["excesso"], consumido)
    m["pct_nao_otimizado"] = _pct(m["nao_otimizado"], consumido)
    m["pct_consumido"] = _pct(consumido, dot)
    m["saldo"] = dot - consumido
    return m


def filtrar(d: pd.DataFrame, categorias=()) -> pd.DataFrame:
    return d[d["categoria"].isin(list(categorias))] if categorias else d


def por_categoria(d: pd.DataFrame, ctx: Contexto) -> list[tuple[str, dict]]:
    """Uma entrada por categoria, do maior consumido para o menor."""
    saida = [(str(c), metricas(g, ctx)) for c, g in d.groupby(d["categoria"].fillna(""))]
    return sorted(saida, key=lambda x: (-x[1]["consumido"], -x[1]["dotacao"], x[0]))


def detalhe_por_categoria(d: pd.DataFrame, ctx: Contexto) -> dict[str, list[dict]]:
    """O que a setinha de cada categoria abre: setor da IQVIA → FEBRAFAR → CMED (como no Estoque e na Ruptura)."""
    if d.empty:
        return {}

    def nos(g: pd.DataFrame, niveis) -> list[dict]:
        saida = []
        for i, (fonte, coluna) in enumerate(niveis):
            com = g[g[coluna].notna()]
            for chave, sub in com.groupby(coluna):
                saida.append({"fonte": fonte, "chave": str(chave), "metricas": metricas(sub, ctx),
                              "filhos": nos(sub, niveis[i + 1:])})
            g = g[g[coluna].isna()]
            if g.empty:
                break
        saida.sort(key=lambda n: (-n["metricas"]["consumido"], n["chave"]))
        return saida

    return {str(c): nos(g, NIVEIS_DETALHE) for c, g in d.groupby(d["categoria"].fillna(""))}


def distribuicao(d: pd.DataFrame, ctx: Contexto) -> list[dict]:
    """Por categoria (só as que compraram): o R$ de compra certa, excesso e sem demanda e a parte de cada um."""
    saida = []
    for categoria, m in por_categoria(d, ctx):
        if m["consumido"] <= 0:
            continue
        saida.append({"categoria": categoria, "consumido": m["consumido"], "certa": m["certa"],
                      "excesso": m["excesso"], "sem_demanda": m["sem_demanda"]})
    return saida


def curva_do_mes(itens_: pd.DataFrame, vendas: pd.DataFrame, d: pd.DataFrame, primeiro: dt.date, ultimo: dt.date,
                 regras: Regras) -> pd.DataFrame:
    """O gráfico Dotação × Consumido de um mês (R11): no dia `dia`, a dotação com as semanas completas até ele (muda
    em degraus, uma vez por semana) e o consumido acumulado até ele, só das linhas de `d` (a página já filtrada). A
    dotação vai até o fim do mês (fica a do último dia com dado); o consumido, só até `ultimo`."""
    dias_mes = calendar.monthrange(primeiro.year, primeiro.month)[1]
    linhas = set(d.index)
    categoria = d["categoria"].fillna("")
    ini_ant, fim_ant = _mes_anterior(primeiro)
    fora = dias_fora(vendas, regras)                     # da loja inteira, antes do filtro da página
    it = itens_[itens_["linha"].isin(linhas)]
    ve = vendas[vendas["linha"].isin(linhas)]
    base = _entre(it, ini_ant, fim_ant).groupby(it["linha"].map(categoria))["valor"].sum()
    cat_venda = ve["linha"].map(categoria)

    n = (ultimo - primeiro).days + 1
    cons = _entre(it, primeiro, ultimo)
    cons = cons.groupby((cons["data"] - pd.Timestamp(primeiro)).dt.days)["valor"].sum()
    cons = cons.reindex(range(n)).fillna(0.0).cumsum()
    linhas_saida, dotacao_do_dia = [], {}
    for i in range(dias_mes):
        j = min(i, n - 1)
        if j not in dotacao_do_dia:
            pm, pp = _pesos(primeiro, primeiro + dt.timedelta(days=j), fora)
            vc, vp = _ponderado(ve, pm, cat_venda), _ponderado(ve, pp, cat_venda)
            dotacao_do_dia[j] = float(sum(b * _fator(vc.get(cat, 0.0), vp.get(cat, 0.0)) for cat, b in base.items()))
        linhas_saida.append({"dia": i + 1, "dotacao": dotacao_do_dia[j],
                             "consumido": float(cons.iloc[i]) if i < n else None})
    return pd.DataFrame(linhas_saida)


def por_mes(d: pd.DataFrame, ctx: Contexto) -> list[dict]:
    """Últimos 3 meses (R11): a dotação final e o comprado de cada mês."""
    saida = []
    for k, (primeiro, ultimo) in enumerate(ctx.periodo.meses):
        so = Contexto(Periodo(ctx.periodo.chave, primeiro, ultimo, ((primeiro, ultimo),)), ctx.regras)
        g = d[["categoria"]].assign(**{f"{p}_0": d[f"{p}_{k}"] for p in ("base", "vc", "vp")})
        saida.append({"primeiro": primeiro, "ultimo": ultimo, "dotacao": dotacao(g, so),
                      "consumido": float(d[f"cons_{k}"].sum())})
    return saida


# ---------------------------------------------------------------------------
# Produtos, notas, diagnóstico
# ---------------------------------------------------------------------------

def produtos(d: pd.DataFrame, busca: str = "", classe: str = "todos") -> pd.DataFrame:
    """Os produtos com compra não otimizada (excesso ou sem demanda), do maior R$ para o menor (Q12)."""
    p = d[d["classe"].isin([EXCESSO, SEM_DEMANDA])]
    if classe in (EXCESSO, SEM_DEMANDA):
        p = p[p["classe"] == classe]
    termo = (busca or "").strip().lower()
    if termo:
        nome = p["nome"].fillna("").str.lower()
        ean = p["ean_principal"].fillna("").astype(str)
        p = p[nome.str.contains(termo, regex=False) | ean.str.contains(termo, regex=False)]
    return p.sort_values(["nao_otimizado", "comprado_un"], ascending=False, kind="stable")


def notas(itens_: pd.DataFrame, linha: str, periodo: Periodo) -> pd.DataFrame:
    """As notas do produto no período, da mais antiga para a mais recente."""
    n = _entre(itens_[itens_["linha"] == linha], periodo.inicio, periodo.fim)
    return n.sort_values(["data", "bonificado"], kind="stable")[["data", "fornecedor", "nro_nf", "unidades", "preco",
                                                                 "valor", "bonificado"]]


def _brl(valor) -> str:
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _un(n) -> str:
    return f"{int(round(float(n))):,}".replace(",", ".") + " un."


def diagnostico(r, regras: Regras) -> str:
    """R14, em frase."""
    bonif = float(r["bonif_un"])
    comprou = f"Comprou {_un(r['comprado_un'])}" + (f" ({int(bonif)} bonificadas)" if bonif > 0 else "")
    if r["classe"] == SEM_DEMANDA:
        return (f"Comprou {_un(r['comprado_un'])} ({_brl(r['nao_otimizado'])}) e não vendeu nenhuma nos "
                f"{regras.sem_demanda_dias} dias antes da compra nem depois dela.")
    acima = f"{_un(r['excesso_un'])} acima do necessário"
    if bonif > 0:
        acima += f", {int(r['excesso_pagas'])} delas pagas ({_brl(r['nao_otimizado'])})"
    else:
        acima += f" ({_brl(r['nao_otimizado'])})"
    return (f"{comprou} e vendeu {int(round(float(r['vendido_un'])))} no período. "
            f"O limite era {_un(r['limite'])}: {acima}.")


def sugestoes(r) -> list[tuple[str, str]]:
    """R14: (ação em negrito, complemento)."""
    if r["classe"] == SEM_DEMANDA:
        return [("Suspender a compra", "deste produto."),
                ("Girar o estoque", "avaliar promoção, kit ou exposição no balcão.")]
    sobra, ritmo = float(r["estoque"]) - float(r["ideal"]), float(r["demanda_dia"])
    if sobra > 0 and ritmo > 0:
        dias = math.ceil(sobra / ritmo)
        primeira = ("Não comprar", f"até o estoque baixar para o ideal: cerca de {dias} dias no ritmo atual.")
    else:
        primeira = ("Comprar pela sugestão do Assistente", "o estoque de hoje já está no ideal ou abaixo dele.")
    return [primeira, ("Conferir o pedido", "veja se a quantidade da última nota não veio de uma promoção do "
                                            "fornecedor.")]
