"""Contas da página Ruptura do Dashboard (27/10/2026, design em docs/specs/2026-10-26-dashboard-ruptura-design.md).

Sem Streamlit e sem banco: recebe o cálculo do Assistente (um produto por linha — o genérico com vários EANs é UM
produto, R5), as vendas por dia, o histórico do estoque (pedido/historico_estoque.py) e as regras, e devolve o quadro
da página e as agregações. Regras do design, por número:

- em ruptura = estoque de até N unidades (todos contam, inclusive giro baixo e venda pontual); ruptura próxima = acima
  disso e estoque para até D dias de venda (R3: só na tabela de críticos);
- risco pela frequência de venda: dias com venda ÷ dias com dado nos últimos 90 (R2);
- a falta é MEDIDA no histórico (R1): um produto está em falta no dia de foto em que a soma do estoque dos seus códigos
  (negativo conta como 0, como no Assistente) é de até N; entre duas fotos em falta, os dias sem foto contam como
  falta (R9); um período de falta vai da 1ª à última foto em falta;
- venda esperada = média do produto nos dias SEM falta conhecida, nos 90 dias antes do início da falta (R1a). Antes de
  o histórico existir não se sabe quais dias tinham estoque: esses dias entram como dias normais (só a falta MEDIDA
  sai da conta). Sem nenhum dia na base: "sem base" (R10);
- perda medida = venda esperada × preço de venda × dias em falta; no período (30 dias) conta também a falta que já
  acabou (R8). Projeção = venda esperada × preço × 30, só de quem está em falta hoje (R1b). A página usa a projeção até
  o histórico ter os 30 dias do período ("transição"); depois, a medida;
- investimento = sugestão do Assistente × custo; custo "a revisar" fica fora do R$ (R4);
- nível de serviço = 1 − em ruptura ÷ produtos (R6).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pedido import linhas as lc

MODO_TRANSICAO = "transicao"
MODO_MEDIDA = "medida"
ALTO, MEDIO, BAIXO = "alto", "medio", "baixo"
RISCOS = (ALTO, MEDIO, BAIXO)
ROTULO_RISCO = {ALTO: "Alto risco", MEDIO: "Médio risco", BAIXO: "Baixo risco"}
CURVAS = ("A", "B", "C")
RUPTURA, PROXIMA = "ruptura", "proxima"
DIAS_BASE = 90          # base da venda esperada e da frequência (fixa, como a "Venda 90 dias" do Assistente)
DIAS_PROJECAO = 30      # a projeção da transição é sempre de 30 dias (R1b)


@dataclass(frozen=True)
class Regras:
    """Os números da página. Os da ruptura vêm da configuração do Assistente; os novos (período da perda, limites do
    risco e do nível de serviço) da seção Dashboard das Configurações de Pedidos (R13)."""
    ruptura_unidades: float = 0
    dias_ruptura: int = 3
    periodo_perda: int = 30
    risco_alto: float = 0.14      # vende pelo menos 1 vez por semana (Q15 de 27/10/2026)
    risco_medio: float = 0.03     # vende pelo menos 1 vez por mês
    sla_vermelho: float = 0.65
    sla_verde: float = 0.75

    @classmethod
    def da_config(cls, config) -> "Regras":
        p = cls()
        return cls(
            ruptura_unidades=float(getattr(config, "ruptura_unidades", p.ruptura_unidades)),
            dias_ruptura=int(getattr(config, "dias_ruptura", p.dias_ruptura)),
            periodo_perda=int(getattr(config, "ruptura_perda_dias", p.periodo_perda)),
            risco_alto=float(getattr(config, "ruptura_risco_alto", p.risco_alto)),
            risco_medio=float(getattr(config, "ruptura_risco_medio", p.risco_medio)),
            sla_vermelho=float(getattr(config, "ruptura_sla_vermelho", p.sla_vermelho)),
            sla_verde=float(getattr(config, "ruptura_sla_verde", p.sla_verde)),
        )


def _d(texto) -> dt.date:
    return dt.date.fromisoformat(str(texto)[:10])


# ---------------------------------------------------------------------------
# Falta medida no histórico
# ---------------------------------------------------------------------------

def estoque_por_dia(historico: pd.DataFrame, fotos: list[str], codigos_por_linha: dict[str, list[str]]) -> pd.DataFrame:
    """Estoque de cada produto do Assistente em cada dia de foto: dias de foto × linhas. O histórico só tem as
    MUDANÇAS (e a abertura de cada mês): o valor de um dia é o último registrado até ele. Código sem registro (fora do
    cadastro naquele dia) conta 0; negativo conta 0 (como no Assistente)."""
    linhas = list(codigos_por_linha)
    if not fotos or not linhas:
        return pd.DataFrame(index=pd.Index(fotos, name="data"), columns=linhas, dtype="float64").fillna(0.0)
    linha_do_codigo = {str(c): l for l, cods in codigos_por_linha.items() for c in cods}
    h = historico[historico["codigo_produto"].astype(str).isin(linha_do_codigo)]
    if h.empty:
        return pd.DataFrame(0.0, index=pd.Index(fotos, name="data"), columns=linhas)
    pivo = h.pivot_table(index="data", columns="codigo_produto", values="estoque", aggfunc="last")
    dias = sorted(set(pivo.index) | set(fotos))
    pivo = pivo.reindex(dias).ffill().reindex(fotos).fillna(0.0).clip(lower=0)
    por_linha = pivo.T.groupby(pivo.columns.map(lambda c: linha_do_codigo[str(c)])).sum().T
    return por_linha.reindex(columns=linhas, fill_value=0.0).rename_axis("data")


def periodos_de_falta(estoque_dia: pd.DataFrame, ruptura_unidades: float) -> pd.DataFrame:
    """Os períodos de falta medidos: [linha, inicio, fim, aberta, dias, dias_sem_foto]. `fim` = a última foto em falta
    do período; `aberta` = ainda em falta na última foto; `dias` = dias corridos de `inicio` a `fim` (os sem foto
    entre duas fotos em falta contam — R9); `dias_sem_foto` = quantos desses dias não tiveram foto."""
    colunas = ["linha", "inicio", "fim", "aberta", "dias", "dias_sem_foto"]
    if estoque_dia.empty:
        return pd.DataFrame(columns=colunas)
    fotos = [_d(x) for x in estoque_dia.index]
    falta = (estoque_dia.to_numpy() <= ruptura_unidades)
    saida = []
    for j, linha in enumerate(estoque_dia.columns):
        coluna = falta[:, j]
        if not coluna.any():
            continue
        anterior = np.r_[False, coluna[:-1]]
        seguinte = np.r_[coluna[1:], False]
        for ini, fim in zip(np.flatnonzero(coluna & ~anterior), np.flatnonzero(coluna & ~seguinte)):
            dias = (fotos[fim] - fotos[ini]).days + 1
            saida.append((linha, fotos[ini], fotos[fim], fim == len(fotos) - 1, dias, dias - (fim - ini + 1)))
    return pd.DataFrame(saida, columns=colunas)


# ---------------------------------------------------------------------------
# Venda esperada e frequência
# ---------------------------------------------------------------------------

def _calendario(inicio: dt.date, fim: dt.date) -> list[dt.date]:
    return [inicio + dt.timedelta(days=i) for i in range((fim - inicio).days + 1)] if fim >= inicio else []


def _matrizes(vendas: pd.DataFrame, linhas: list[str], linha_do_codigo: dict, dias: list[dt.date],
              sem_dados: set[dt.date], periodos: pd.DataFrame):
    """Vendas (dias × linhas), dia válido (com dado de venda) e falta conhecida (dias × linhas)."""
    pos_dia = {d: i for i, d in enumerate(dias)}
    pos_linha = {l: j for j, l in enumerate(linhas)}
    V = np.zeros((len(dias), len(linhas)))
    if not vendas.empty:
        v = vendas.assign(linha=vendas["codigo_produto"].astype(str).map(linha_do_codigo)).dropna(subset=["linha"])
        v = v.groupby(["data", "linha"])["unidades"].sum()
        for (data, linha), un in v.items():
            i, j = pos_dia.get(_d(data)), pos_linha.get(linha)
            if i is not None and j is not None:
                V[i, j] = max(float(un), 0.0)
    valido = np.array([d not in sem_dados for d in dias], dtype=bool)
    F = np.zeros((len(dias), len(linhas)), dtype=bool)
    for p in periodos.itertuples(index=False):
        j = pos_linha.get(p.linha)
        if j is None:
            continue
        a, b = pos_dia.get(p.inicio), pos_dia.get(p.fim)
        a = 0 if a is None and p.inicio < dias[0] else a
        b = len(dias) - 1 if b is None and p.fim > dias[-1] else b
        if a is not None and b is not None and a <= b:
            F[a:b + 1, j] = True
    return V, valido, F


def _media_antes(Cv, Cn, i_inicio: int, j: int, n_base: int) -> float:
    """Média por dia nos `n_base` dias antes da posição `i_inicio` (somas acumuladas: O(1) por consulta)."""
    a = max(0, i_inicio - n_base)
    soma = Cv[i_inicio, j] - Cv[a, j]
    dias = Cn[i_inicio, j] - Cn[a, j]
    return float(soma / dias) if dias > 0 else float("nan")


# ---------------------------------------------------------------------------
# O quadro da página
# ---------------------------------------------------------------------------

def preparar(todas: pd.DataFrame, vendas: pd.DataFrame, historico: pd.DataFrame, fotos: list[str], regras: Regras,
             meta: dict, linha_compra: pd.Series | None = None) -> tuple[pd.DataFrame, dict]:
    """(quadro, resumo). `todas`: o cálculo do Assistente (com `codigos`); `vendas`: codigo_produto, data, unidades
    (as do "pronto" da loja); `historico`/`fotos`: `historico_estoque.ler`; `meta`: a do cálculo (`inicio`, `fim`,
    `dias_sem_vendas`); `linha_compra`: a categoria fixa de cada produto (a tela monta pela classificação)."""
    d = todas.copy()
    fotos = sorted(fotos or [])
    if d.empty:
        return d, _resumo(d, fotos, regras)
    d.index = d["linha"].astype(str) if "linha" in d else d.index.astype(str)
    estoque = pd.to_numeric(d["estoque"], errors="coerce").fillna(0).clip(lower=0)
    demanda = pd.to_numeric(d["demanda_dia"], errors="coerce").fillna(0)
    d["em_ruptura"] = estoque <= regras.ruptura_unidades
    d["ruptura_proxima"] = ~d["em_ruptura"] & (estoque <= demanda * regras.dias_ruptura)
    d["situacao"] = np.where(d["em_ruptura"], RUPTURA, np.where(d["ruptura_proxima"], PROXIMA, ""))
    d["linha_compra"] = (linha_compra.reindex(d.index) if linha_compra is not None else lc.SEM_LINHA)
    d["linha_compra"] = d["linha_compra"].fillna(lc.SEM_LINHA)
    d["curva_letra"] = d["curva"].astype(str).str[:1]

    codigos = {l: [str(c) for c in (cs if isinstance(cs, list) else [])] for l, cs in zip(d.index, d["codigos"])}
    linha_do_codigo = {c: l for l, cs in codigos.items() for c in cs}
    linhas = list(d.index)

    # Falta medida.
    estoque_dia = estoque_por_dia(historico, fotos, codigos) if fotos else pd.DataFrame()
    periodos = periodos_de_falta(estoque_dia, regras.ruptura_unidades) if fotos else pd.DataFrame(
        columns=["linha", "inicio", "fim", "aberta", "dias", "dias_sem_foto"])

    # Calendário: do início das vendas até a última foto (ou o fim das vendas).
    fim_vendas = _d(meta["fim"])
    ultimo = max(fim_vendas, _d(fotos[-1])) if fotos else fim_vendas
    inicio_cal = min(_d(meta["inicio"]), _d(fotos[0])) if fotos else _d(meta["inicio"])
    dias = _calendario(inicio_cal, ultimo)
    sem_dados = {_d(x) for x in meta.get("dias_sem_vendas", [])} | {x for x in dias if x > fim_vendas or x < _d(meta["inicio"])}
    V, valido, F = _matrizes(vendas, linhas, linha_do_codigo, dias, sem_dados, periodos)
    M = valido[:, None] & ~F                      # dias da base: com dado de venda e sem falta conhecida
    Cv = np.vstack([np.zeros((1, len(linhas))), np.cumsum(V * M, axis=0)])
    Cn = np.vstack([np.zeros((1, len(linhas))), np.cumsum(M, axis=0)])
    pos_dia = {x: i for i, x in enumerate(dias)}
    pos_linha = {l: j for j, l in enumerate(linhas)}

    # Dias com venda na base (para a frequência do risco, R2): a MESMA base da venda esperada — sem os dias de falta
    # conhecida. Contar os dias em falta (sem estoque não há venda) baixava o risco de quem está em falta há mais
    # tempo: na loja 60, em 27/10/2026, nenhum dos 901 produtos em ruptura saía "alto".
    Cs = np.vstack([np.zeros((1, len(linhas))), np.cumsum((V > 0) & M, axis=0)])
    d["ultima_venda"] = [
        max((dias[i] for i in np.flatnonzero(V[:, j] > 0)), default=None) for j in range(len(linhas))]

    # Perda por período de falta (R1a, R8, R9, R10).
    preco = pd.to_numeric(d["preco_venda"], errors="coerce").fillna(0).clip(lower=0)
    # A falta só é conhecida até a ÚLTIMA FOTO: o período da perda termina nela (sem foto mais nova, não se sabe se o
    # produto continuou em falta).
    ref = _d(fotos[-1]) if fotos else fim_vendas
    janela_ini = ref - dt.timedelta(days=regras.periodo_perda - 1)
    perda_periodo = pd.Series(0.0, index=d.index)
    sem_base = pd.Series(False, index=d.index)
    aberta = {}
    for p in periodos.itertuples(index=False):
        j = pos_linha[p.linha]
        venda = _media_antes(Cv, Cn, pos_dia.get(p.inicio, 0), j, DIAS_BASE)
        if np.isnan(venda):
            sem_base[p.linha] = sem_base[p.linha] or bool(p.aberta)
            venda = 0.0
        a, b = max(p.inicio, janela_ini), p.fim
        if p.aberta:
            aberta[p.linha] = (p.inicio, p.dias, p.dias_sem_foto, venda)
        if b >= a:
            perda_periodo[p.linha] += venda * preco[p.linha] * ((b - a).days + 1)
    d["perda_periodo"] = perda_periodo

    # Quem está em falta hoje: a venda esperada vem do período aberto; sem histórico ainda, dos 90 dias até hoje.
    venda_atual, dias_falta, inicio_falta, sem_foto, perda_aberta, frequencia = [], [], [], [], [], []
    for l in linhas:
        j = pos_linha[l]
        if l in aberta:
            ini, n, sf, venda = aberta[l]
            i_ref = pos_dia.get(ini, 0)
        else:
            ini, n, sf = None, 0, 0
            i_ref = len(dias)
            venda = _media_antes(Cv, Cn, i_ref, j, DIAS_BASE)
            if np.isnan(venda):
                venda = 0.0
                if d.at[l, "em_ruptura"]:
                    sem_base[l] = True
        frequencia.append(_media_antes(Cs, Cn, i_ref, j, DIAS_BASE))
        venda_atual.append(venda)
        inicio_falta.append(ini)
        dias_falta.append(n)
        sem_foto.append(sf)
        perda_aberta.append(venda * preco[l] * n)
    d["venda_esperada"] = venda_atual
    d["frequencia"] = pd.Series(frequencia, index=d.index).fillna(0.0)
    d["risco"] = np.where(d["frequencia"] >= regras.risco_alto, ALTO,
                          np.where(d["frequencia"] >= regras.risco_medio, MEDIO, BAIXO))
    d["inicio_falta"] = inicio_falta
    d["dias_falta"] = dias_falta
    d["dias_sem_foto"] = sem_foto
    d["perda_aberta"] = np.where(d["em_ruptura"], perda_aberta, np.nan)
    d["sem_base"] = sem_base & d["em_ruptura"]
    d["venda_risco_dia"] = np.where(d["em_ruptura"], d["venda_esperada"] * preco, 0.0)
    d["projecao"] = np.where(d["em_ruptura"], d["venda_esperada"] * preco * DIAS_PROJECAO, 0.0)

    # Investimento (R4): a sugestão do Assistente × custo; custo "a revisar" só nas unidades.
    sugestao = pd.to_numeric(d["sugestao"], errors="coerce").fillna(0).clip(lower=0)
    custo = pd.to_numeric(d["preco"], errors="coerce")
    d["custo_ok"] = ~d["a_revisar"].astype(bool) & (custo > 0)
    d["repor_un"] = np.where(d["em_ruptura"] | d["ruptura_proxima"], sugestao, 0)
    d["repor_valor"] = np.where(d["custo_ok"], d["repor_un"] * custo.fillna(0), 0.0)

    resumo = _resumo(d, fotos, regras)
    # O valor de perda que a página mostra (R1b): projeção na transição; depois, a medida.
    transicao = resumo["modo"] == MODO_TRANSICAO
    d["perda"] = d["projecao"] if transicao else d["perda_periodo"]
    d["perda_produto"] = np.where(d["em_ruptura"], d["projecao"] if transicao else d["perda_aberta"], np.nan)
    return d, resumo


def _resumo(d: pd.DataFrame, fotos: list[str], regras: Regras) -> dict:
    """Quando o histórico começou, quantos dias o período já tem medidos e o modo da página."""
    if not fotos:
        return {"primeira_foto": None, "ultima_foto": None, "dias_medidos": 0, "modo": MODO_TRANSICAO,
                "periodo": regras.periodo_perda, "completo_em": None}
    primeira, ultima = _d(fotos[0]), _d(fotos[-1])
    medidos = min((ultima - primeira).days + 1, regras.periodo_perda)
    completo = primeira + dt.timedelta(days=regras.periodo_perda - 1)
    return {"primeira_foto": primeira, "ultima_foto": ultima, "dias_medidos": medidos,
            "modo": MODO_MEDIDA if medidos >= regras.periodo_perda else MODO_TRANSICAO,
            "periodo": regras.periodo_perda, "completo_em": completo}


# ---------------------------------------------------------------------------
# Filtros e agregações
# ---------------------------------------------------------------------------

def filtrar(d: pd.DataFrame, categorias=(), curvas=(), riscos=()) -> pd.DataFrame:
    """Filtro cruzado da página: OU dentro de uma dimensão, E entre elas."""
    if d.empty:
        return d
    ok = pd.Series(True, index=d.index)
    if categorias:
        ok &= d["linha_compra"].isin(list(categorias))
    if curvas:
        ok &= d["curva_letra"].isin(list(curvas))
    if riscos:
        ok &= d["risco"].isin(list(riscos))
    return d[ok]


def sem_filtro_de(d: pd.DataFrame, dimensao: str, categorias=(), curvas=(), riscos=()) -> pd.DataFrame:
    """Cada bloco não se filtra por si mesmo (o que não está escolhido fica escurecido na tela)."""
    return filtrar(d, *(() if dimensao == nome else valor for nome, valor in
                        (("categoria", categorias), ("curva", curvas), ("risco", riscos))))


@dataclass
class Cards:
    produtos: int
    ruptura: int
    ruptura_pct: float
    ruptura_curva_a: int
    venda_risco_dia: float
    perda: float                 # a que a página mostra (projeção na transição)
    perda_medida: float          # a medida no período até agora
    projecao: float
    investimento: float
    investimento_un: int
    sem_base: int


def _pct(parte, todo) -> float:
    return float(parte / todo * 100) if todo else 0.0


def cards(d: pd.DataFrame) -> Cards:
    if d.empty:
        return Cards(0, 0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    r = d[d["em_ruptura"]]
    return Cards(
        produtos=len(d), ruptura=len(r), ruptura_pct=_pct(len(r), len(d)),
        ruptura_curva_a=int((r["curva_letra"] == "A").sum()),
        venda_risco_dia=float(d["venda_risco_dia"].sum()), perda=float(d["perda"].sum()),
        perda_medida=float(d["perda_periodo"].sum()), projecao=float(d["projecao"].sum()),
        investimento=float(r["repor_valor"].sum()), investimento_un=int(r["repor_un"].sum()),
        sem_base=int(r["sem_base"].sum()),
    )


def _metricas(g: pd.DataFrame) -> dict:
    n, rup = len(g), int(g["em_ruptura"].sum())
    return {"produtos": n, "em_ruptura": rup, "nivel_servico": (1 - rup / n) * 100 if n else 100.0,
            "perda": float(g["perda"].sum()), "investimento": float(g.loc[g["em_ruptura"], "repor_valor"].sum())}


def por_categoria(d: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por categoria fixa: Produtos | Em ruptura | Nível de serviço | Perda | Investimento (R6). Do maior
    número em ruptura para o menor."""
    colunas = ["produtos", "em_ruptura", "nivel_servico", "perda", "investimento"]
    if d.empty:
        return pd.DataFrame(columns=colunas, index=pd.Index([], name="categoria"))
    t = pd.DataFrame({c: _metricas(g) for c, g in d.groupby("linha_compra")}).T
    t.index.name = "categoria"
    t = t.astype({"produtos": int, "em_ruptura": int, "nivel_servico": float, "perda": float, "investimento": float})
    return t.sort_values(["em_ruptura", "perda"], ascending=False, kind="stable")[colunas]


def total(d: pd.DataFrame) -> dict:
    return _metricas(d) if not d.empty else {"produtos": 0, "em_ruptura": 0, "nivel_servico": 100.0, "perda": 0.0,
                                              "investimento": 0.0}


NIVEIS_DETALHE = (("iqvia", "fonte_setor"), ("febrafar", "fonte_febrafar"), ("cmed", "fonte_cmed"))


def detalhe_por_categoria(d: pd.DataFrame) -> dict[str, list[dict]]:
    """O que a setinha de cada categoria abre: setor da IQVIA → FEBRAFAR → CMED, como no Estoque (o nível que o
    produto não tem é pulado). Só com as colunas `fonte_*` (a tela junta pela classificação)."""
    if d.empty or not any(c in d for _, c in NIVEIS_DETALHE):
        return {}

    def nos(g: pd.DataFrame, niveis) -> list[dict]:
        saida = []
        for i, (fonte, coluna) in enumerate(niveis):
            com = g[g[coluna].notna()] if coluna in g else g.iloc[0:0]
            for chave, sub in com.groupby(coluna):
                saida.append({"fonte": fonte, "chave": str(chave), "metricas": _metricas(sub),
                              "filhos": nos(sub, niveis[i + 1:])})
            g = g[g[coluna].isna()] if coluna in g else g
            if g.empty:
                break
        saida.sort(key=lambda n: (-n["metricas"]["em_ruptura"], -n["metricas"]["perda"], n["chave"]))
        return saida

    return {c: nos(g, NIVEIS_DETALHE) for c, g in d.groupby("linha_compra")}


def por_curva(d: pd.DataFrame) -> pd.DataFrame:
    """Curva A, B e C (a 1ª letra, a do valor): produtos, em ruptura, % da curva e a perda."""
    base = pd.DataFrame(index=pd.Index(CURVAS, name="curva"), data={"produtos": 0, "em_ruptura": 0, "perda": 0.0})
    if not d.empty:
        g = d.groupby("curva_letra")
        base["produtos"] = g.size().reindex(CURVAS).fillna(0).astype(int)
        base["em_ruptura"] = g["em_ruptura"].sum().reindex(CURVAS).fillna(0).astype(int)
        base["perda"] = g["perda"].sum().reindex(CURVAS).fillna(0.0)
    base["pct"] = [_pct(r, p) for r, p in zip(base["em_ruptura"], base["produtos"])]
    return base


def por_risco(d: pd.DataFrame) -> pd.DataFrame:
    """Alto, Médio e Baixo: a perda e a parte de cada um no total."""
    base = pd.DataFrame(index=pd.Index(RISCOS, name="risco"), data={"perda": 0.0, "em_ruptura": 0})
    if not d.empty:
        g = d.groupby("risco")
        base["perda"] = g["perda"].sum().reindex(RISCOS).fillna(0.0)
        base["em_ruptura"] = g["em_ruptura"].sum().reindex(RISCOS).fillna(0).astype(int)
    tot = float(base["perda"].sum())
    base["pct"] = [_pct(v, tot) for v in base["perda"]]
    return base


def criticos(d: pd.DataFrame, busca: str = "", situacao: str = "todos") -> pd.DataFrame:
    """Em ruptura e com ruptura próxima (R3). Ordem padrão (R12): os em ruptura da maior perda para a menor; depois a
    ruptura próxima, pela maior venda em risco por dia — que, sem falta ainda, é a venda esperada × preço."""
    if d.empty:
        return d
    c = d[d["em_ruptura"] | d["ruptura_proxima"]].copy()
    if situacao in (RUPTURA, PROXIMA):
        c = c[c["situacao"] == situacao]
    termo = (busca or "").strip().lower()
    if termo:
        nome = c["nome"].astype(str).str.lower().str.contains(termo, regex=False)
        ean = c["eans"].map(lambda es: any(termo in str(e) for e in (es if isinstance(es, list) else [])))
        c = c[nome | ean]
    preco = pd.to_numeric(c["preco_venda"], errors="coerce").fillna(0)
    c["_ordem_prox"] = np.where(c["ruptura_proxima"], c["venda_esperada"] * preco, 0.0)
    c["_grupo"] = np.where(c["em_ruptura"], 0, 1)
    c["_perda"] = c["perda_produto"].fillna(-1.0)
    c = c.sort_values(["_grupo", "_perda", "_ordem_prox", "nome"], ascending=[True, False, False, True], kind="stable")
    return c.drop(columns=["_ordem_prox", "_grupo", "_perda"])


# ---------------------------------------------------------------------------
# Detalhe do produto: diagnóstico e sugestão de ação
# ---------------------------------------------------------------------------

def _brl(valor) -> str:
    from core import ui
    return ui.formatar_moeda(float(valor or 0))


def _qtd(n, um: str) -> str:
    from core import dicas
    return dicas.qtd(n, um)


def _data_curta(valor) -> str:
    if valor is None or (not isinstance(valor, (str, dt.date)) and pd.isna(valor)) or str(valor) in ("", "NaT", "None"):
        return "—"
    d = _d(valor)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def diagnostico(r, regras: Regras) -> str:
    """O que está acontecendo com o produto, em frases curtas e com os números dele."""
    venda = float(r.get("venda_esperada") or 0)
    if r.get("ruptura_proxima"):
        return (f"O estoque cobre {_qtd(regras.dias_ruptura, 'dia')} de venda ou menos. Se não repor, o produto "
                "entra em falta nos próximos dias.")
    if r.get("sem_base"):
        return ("Sem estoque. O produto faltou em todos os dias da base de 90 dias, então não há venda esperada "
                "para medir a perda.")
    # Texto enxuto (pedido do Mariano, 27/10/2026): sem histórico da falta, só quanto se deixa de vender por dia.
    partes = []
    dias = int(r.get("dias_falta") or 0)
    if dias:
        partes.append(f"Sem estoque há {_qtd(dias, 'dia')} (medido desde {_data_curta(r.get('inicio_falta'))}).")
        if int(r.get("dias_sem_foto") or 0):
            partes.append(f"{_qtd(int(r['dias_sem_foto']), 'dia')} desse período sem foto do estoque.")
    preco = pd.to_numeric(r.get("preco_venda"), errors="coerce")
    if venda > 0:
        # Sem preço de venda no cadastro não há valor a dizer (o teste visual mostrou "R$ nan" em 27/10/2026).
        partes.append(f"A cada dia em falta deixa de vender {_brl(venda * float(preco))}." if pd.notna(preco) and preco > 0
                      else "O produto não tem preço de venda no cadastro, então a perda fica sem valor.")
    return " ".join(partes) or "Sem estoque na foto de hoje."


def sugestoes(r) -> list[tuple[str, str]]:
    """As ações, como (título, explicação), da mais urgente para a menos."""
    itens: list[tuple[str, str]] = []
    un = int(r.get("repor_un") or 0)
    if un > 0:
        valor = f" ({_brl(r.get('repor_valor'))})" if r.get("custo_ok") else " (custo a revisar)"
        itens.append((f"Comprar {_qtd(un, 'unidade')}{valor}", "para ficar com o estoque ideal."))
    fornecedor = r.get("compra_fornecedor")
    if isinstance(fornecedor, str) and fornecedor.strip():
        custo = f", a {_brl(r.get('preco'))} por unidade" if r.get("custo_ok") else ""
        itens.append(("Fornecedor", f"A última compra foi com {' '.join(fornecedor.split())}{custo}."))
    itens.append(("Conferir o pedido", "Veja se já não há compra a caminho antes de pedir de novo."))
    return itens
