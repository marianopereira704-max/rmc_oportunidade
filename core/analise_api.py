"""Análise de Oportunidade pelas compras da API do GPS — regra de 05/10/2026
(usada pela visão Por Loja; a Por Produto continua em core/analise.py até a
conversa dela, e as duas são publicadas juntas).

O que mudou em relação à regra da planilha (core/analise.py):

- Fonte: as compras da REDE por mês fechado que a rotina monta
  (pedido/rede.py), não mais a planilha do BI. Período = os N últimos meses
  FECHADOS (1, 2 ou 3) que já têm arquivo — um mês só aparece depois do dia
  6 do mês seguinte (notas lançadas com atraso).
- Produtos: TODO produto da tabela Gruppy do laboratório, não só genérico.
  EAN da Gruppy na Base Genéricos → compara com as compras de qualquer EAN
  do mesmo genérico; senão → com as compras do mesmo EAN (`chave`: "G<id>"
  ou "E<ean>"). EAN comparado sem os zeros à esquerda.
- Economia: a SOMA de (preço pago − preço do laboratório) × quantidade de
  CADA compra acima da tabela no período (antes: só a compra de menor
  preço). Compra abaixo da tabela não desconta nada; aparece com a
  diferença negativa e economia zero.
- Uma linha por (loja, produto, nota fiscal): Qtd. = o que veio daquele
  produto naquela nota; preço = o da nota (média ponderada se o produto vier
  mais de uma vez na mesma nota).
- Preço errado, compra a compra, na ordem (mesma lógica do Assistente de
  pedido — pedido/calculo.py — e os mesmos números das Configurações de
  Pedidos):
    1. cadastro × nota divergem mais de N× → o preço de venda decide (custo
       coerente = 15–100% da venda): só o cadastro coerente → vale o
       cadastro ("ajustado"); os dois ou nenhum → "A revisar";
    2. nota acima do preço de venda → cadastro se ele for coerente; senão
       "A revisar";
    3. mediana das compras do produto na loja: mais de 3× ou menos de 1/3
       → "A revisar".
  "A revisar" não soma economia. Medido em 05/10/2026 (4 lojas, 14.634
  compras): 589 corrigidas pelo cadastro, 662 "a revisar" — 600 delas nas
  lojas da Reis, quase todas notas da MILLENIUM a ~2,2× o cadastro, com o
  preço Gruppy perto do cadastro em 47 de 49: ficam "a revisar" pra
  identificar (decisão de 05/10/2026), e serão reanalisadas com a rede.
- Bonificação (preço < R$ 0,10) sai da conta e só é contada.
- Sem "Preço médio (3m)" e sem o recuo VlrUnitario → CMV → custo médio da
  planilha (a API não tem esses campos).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import analise
from core.config import settings
from core.models import (
    CategoriaEan,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    SituacaoVinculoGps,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
    VinculoLojaGps,
    loja_da_rede,
)
from pedido import categorias as cat

MOTIVO_DIVERGENCIA = "divergencia"   # cadastro × nota e o preço de venda não decide
MOTIVO_ACIMA_VENDA = "acima_venda"   # nota acima do preço de venda, cadastro não ajuda
MOTIVO_MEDIANA = "mediana"           # muito fora das outras compras do produto na loja

COLUNAS = [
    *analise.COLUNAS_LOJA, "produto_id", "base_generico_id", "ean", "nome_canonico", "descricao", "categoria",
    "laboratorio", "ano_mes", "data", "nro_nf", "fornecedor", "preco_pago", "preco_nota", "ajustado", "a_revisar",
    "revisar_motivo", "custo_cadastro", "preco_venda", "preco_laboratorio", "diferenca", "quantidade", "economia",
]


@dataclass(frozen=True)
class Parametros:
    """Os números das regras de preço — os mesmos do Assistente de pedido
    (Configurações de Pedidos, padrão de todas as lojas)."""
    limite_bonificacao: float = 0.10
    custo_faixa_min: float = 0.15
    custo_faixa_max: float = 1.0
    fator_cadastro_nota: float = 2.0
    fator_preco_fora: float = 3.0


def chaves_produto(eans: pd.Series, id_generico: pd.Series) -> pd.Series:
    """"G<id>" para EAN da Base Genéricos, "E<ean>" para o resto."""
    gen = id_generico.astype("Int64").astype("string")
    return ("G" + gen).where(id_generico.notna(), "E" + eans.astype("string"))


def precos_por_chave(itens: pd.DataFrame, genericos: pd.DataFrame) -> pd.DataFrame:
    """Itens da tabela Gruppy (uf, ean, preco) → menor preço por (uf, chave)."""
    if itens.empty:
        return pd.DataFrame(columns=["uf", "chave", "preco_laboratorio"])
    df = itens.assign(ean=cat.chaves_ean(itens["ean"])).dropna(subset=["ean"])
    id_gen = df["ean"].map(genericos.set_index("ean")["base_generico_id"])
    df = df.assign(chave=chaves_produto(df["ean"], id_gen))
    return df.groupby(["uf", "chave"], as_index=False)["preco"].min().rename(columns={"preco": "preco_laboratorio"})


def eans_de_interesse(itens: pd.DataFrame, genericos: pd.DataFrame) -> list[str]:
    """Os EANs cujas compras interessam: os da tabela Gruppy e TODOS os EANs
    dos genéricos que ela tem — o arquivo da rede é lido filtrado por eles."""
    eans = set(cat.chaves_ean(itens["ean"]).dropna())
    ids = set(genericos.loc[genericos["ean"].isin(eans), "base_generico_id"])
    eans |= set(genericos.loc[genericos["base_generico_id"].isin(ids), "ean"])
    return sorted(eans)


def _regras_de_preco(c: pd.DataFrame, p: Parametros) -> pd.DataFrame:
    """Preço usado e "a revisar" de cada compra (ordem no topo do módulo)."""
    nota = c["preco"]
    cad = pd.to_numeric(c["custo_cadastro"], errors="coerce")
    venda = pd.to_numeric(c["preco_venda"], errors="coerce")
    tem_venda = venda > 0

    def coerente(x: pd.Series) -> pd.Series:
        return tem_venda & (x > 0) & (x >= venda * p.custo_faixa_min) & (x <= venda * p.custo_faixa_max)

    nota_ok, cad_ok = coerente(nota), coerente(cad)
    razao = cad / nota.where(nota > 0)
    diverge = (cad > 0) & ((razao > p.fator_cadastro_nota) | (razao < 1 / p.fator_cadastro_nota))
    usa_cad = diverge & cad_ok & ~nota_ok
    rev_div = diverge & ~(cad_ok ^ nota_ok)
    acima = ~diverge & tem_venda & (nota > venda * p.custo_faixa_max)
    usa_cad |= acima & cad_ok
    rev_acima = acima & ~cad_ok
    usado = nota.where(~usa_cad, cad)
    base = ~(rev_div | rev_acima)
    mediana = usado.where(base).groupby([c["loja_id"], c["chave"]]).transform("median")
    rev_med = base & ((usado > mediana * p.fator_preco_fora) | (usado < mediana / p.fator_preco_fora))
    return c.assign(
        preco_usado=usado, ajustado=usa_cad, a_revisar=rev_div | rev_acima | rev_med,
        revisar_motivo=np.select([rev_div, rev_acima, rev_med], [MOTIVO_DIVERGENCIA, MOTIVO_ACIMA_VENDA, MOTIVO_MEDIANA],
                                 default=None),
    )


def calcular(compras: pd.DataFrame, vinculos: pd.DataFrame, lojas: pd.DataFrame, precos: pd.DataFrame,
             genericos: pd.DataFrame, categorias: pd.DataFrame, meses_periodo: list[str],
             p: Parametros = Parametros()) -> tuple[pd.DataFrame, pd.Series]:
    """Pura. `compras`: linhas do arquivo da rede (pedido/rede.py) dos meses
    carregados; `vinculos`: empresa, codigo_loja, loja_id; `lojas`:
    analise.COLUNAS_LOJA; `precos`: `precos_por_chave`; `genericos`: ean,
    base_generico_id, nome_canonico; `categorias`: ean, categoria.

    Devolve (uma linha por loja × produto × nota no período — `COLUNAS`,
    bonificações por loja_id)."""
    vazio = pd.DataFrame(columns=COLUNAS), pd.Series(dtype="int64")
    if compras.empty or precos.empty:
        return vazio
    c = compras.merge(vinculos, on=["empresa", "codigo_loja"], how="inner")
    c = c.merge(lojas[["loja_id", "uf"]], on="loja_id", how="inner")
    gen = genericos.drop_duplicates("ean").set_index("ean")
    c["base_generico_id"] = c["ean"].map(gen["base_generico_id"])
    c["chave"] = chaves_produto(c["ean"], c["base_generico_id"])
    c = c.merge(precos, on=["uf", "chave"], how="inner")
    if c.empty:
        return vazio
    c["ano_mes"] = c["data"].astype("string").str[:7]
    bonificada = c["preco"] < p.limite_bonificacao
    bonificacoes = c[bonificada & c["ano_mes"].isin(meses_periodo)].groupby("loja_id").size()
    c = _regras_de_preco(c[~bonificada].reset_index(drop=True), p)
    c = c[c["ano_mes"].isin(meses_periodo)]
    if c.empty:
        return pd.DataFrame(columns=COLUNAS), bonificacoes

    acima = ~c["a_revisar"] & (c["preco_usado"] > c["preco_laboratorio"])
    c = c.assign(
        _valor=c["preco_usado"] * c["quantidade"], _valor_nota=c["preco"] * c["quantidade"],
        _economia=np.where(acima, (c["preco_usado"] - c["preco_laboratorio"]) * c["quantidade"], 0.0),
        nro_nf=c["nro_nf"].fillna(""), fornecedor=c["fornecedor"].fillna(""),
    )
    chave = ["loja_id", "chave", "nro_nf", "fornecedor", "data"]
    g = c.groupby(chave, sort=False, dropna=False)
    linhas = g.agg(
        quantidade=("quantidade", "sum"), _valor=("_valor", "sum"), _valor_nota=("_valor_nota", "sum"),
        economia=("_economia", "sum"), a_revisar=("a_revisar", "any"), ajustado=("ajustado", "any"),
        revisar_motivo=("revisar_motivo", "first"), preco_laboratorio=("preco_laboratorio", "first"),
        base_generico_id=("base_generico_id", "first"), ean=("ean", "first"), descricao=("nome", "first"),
        custo_cadastro=("custo_cadastro", "first"), preco_venda=("preco_venda", "first"), ano_mes=("ano_mes", "first"),
    ).reset_index()
    labs = (c[c["laboratorio"].fillna("") != ""].drop_duplicates([*chave, "laboratorio"])
            .groupby(chave, sort=False, dropna=False)["laboratorio"].agg(" / ".join))
    linhas = linhas.join(labs, on=chave)
    qtd = linhas["quantidade"].replace(0, np.nan)
    linhas["preco_pago"] = linhas["_valor"] / qtd
    linhas["preco_nota"] = linhas["_valor_nota"] / qtd
    linhas["diferenca"] = linhas["preco_pago"] - linhas["preco_laboratorio"]
    linhas["nome_canonico"] = linhas["base_generico_id"].map(gen.drop_duplicates("base_generico_id")
                                                             .set_index("base_generico_id")["nome_canonico"])
    linhas["nome_canonico"] = linhas["nome_canonico"].fillna(linhas["descricao"]).fillna("EAN " + linhas["ean"].astype(str))
    linhas["categoria"] = linhas["ean"].map(categorias.drop_duplicates("ean").set_index("ean")["categoria"])
    linhas = linhas.rename(columns={"chave": "produto_id"})
    linhas = linhas.merge(lojas, on="loja_id", how="inner")
    linhas = analise._texto_ausente_como_none(
        linhas, (*analise.COLUNAS_LOJA[1:], "nome_canonico", "descricao", "laboratorio", "categoria", "revisar_motivo"))
    return linhas[COLUNAS].reset_index(drop=True), bonificacoes


# ---------------------------------------------------------------------------
# Indicadores e visão por loja
# ---------------------------------------------------------------------------

def indicadores(df: pd.DataFrame) -> analise.Indicadores:
    """Mesmos cards de antes; "produto" = produto distinto (genérico ou EAN)."""
    if df.empty:
        return analise.Indicadores(0.0, 0, 0, 0, 0)
    com = df[df["economia"] > 0]
    return analise.Indicadores(
        economia=float(df["economia"].sum()),
        lojas_com_oportunidade=int(com["loja_id"].nunique()),
        lojas_analisadas=int(df["loja_id"].nunique()),
        produtos_com_oportunidade=int(com["produto_id"].nunique()),
        produtos_analisados=int(df["produto_id"].nunique()),
    )


def por_loja(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por loja: economia somada e quantos produtos DISTINTOS têm
    economia (um produto comprado em 3 notas conta 1 — Q17 de 05/10/2026)."""
    if df.empty:
        return pd.DataFrame(columns=[*analise.COLUNAS_LOJA, "economia", "qtd_produtos"])
    com = df[df["economia"] > 0].groupby("loja_id")["produto_id"].nunique().rename("qtd_produtos")
    lojas = (df.groupby(analise.COLUNAS_LOJA, dropna=False, sort=False)
             .agg(economia=("economia", "sum")).reset_index().join(com, on="loja_id"))
    lojas["qtd_produtos"] = lojas["qtd_produtos"].fillna(0).astype(int)
    return analise._texto_ausente_como_none(lojas, analise.COLUNAS_LOJA[1:])


def meses_do_periodo(disponiveis: list[str], periodo_meses: int) -> list[str]:
    """Os N meses FECHADOS mais recentes com arquivo da rede."""
    return sorted(disponiveis)[-periodo_meses:] if disponiveis else []


# ---------------------------------------------------------------------------
# Leitura (banco + arquivos da rede)
# ---------------------------------------------------------------------------

_VINCULO_VALIDO = (SituacaoVinculoGps.AUTOMATICO, SituacaoVinculoGps.CONFIRMADO)


def itens_gruppy(session: Session, laboratorio: str) -> pd.DataFrame:
    """uf, ean, preco dos itens das tabelas ATIVAS do laboratório."""
    linhas = session.execute(
        select(TabelaGruppyCobertura.uf, ItemTabelaGruppy.ean, ItemTabelaGruppy.custo_liquido)
        .join(TabelaGruppy, TabelaGruppy.id == ItemTabelaGruppy.tabela_gruppy_id)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA, TabelaGruppy.laboratorio == laboratorio)
    ).all()
    df = pd.DataFrame(linhas, columns=["uf", "ean", "preco"])
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce").astype(float)
    return df


def vinculos(session: Session) -> pd.DataFrame:
    linhas = session.execute(
        select(VinculoLojaGps.id_empresa_gps, VinculoLojaGps.codigo_loja_gps, VinculoLojaGps.loja_id)
        .join(Loja, Loja.id == VinculoLojaGps.loja_id)
        # Loja que saiu da rede sai da Análise (07/10/2026).
        .where(VinculoLojaGps.situacao.in_(_VINCULO_VALIDO), VinculoLojaGps.loja_id.is_not(None), loja_da_rede())
    ).all()
    df = pd.DataFrame(linhas, columns=["empresa", "codigo_loja", "loja_id"])
    df["empresa"] = df["empresa"].astype("string")
    # Sem espaços: os dados das compras vêm limpos (ver gps_vinculo.recalcular, 08/10/2026).
    df["codigo_loja"] = df["codigo_loja"].astype("string").str.strip()
    return df


def categorias_dos_eans(session: Session, eans: list[str]) -> pd.DataFrame:
    linhas = []
    for i in range(0, len(eans), 1000):
        linhas.extend(session.execute(
            select(CategoriaEan.ean, CategoriaEan.categoria).where(CategoriaEan.ean.in_(eans[i:i + 1000]))).all())
    return pd.DataFrame(linhas, columns=["ean", "categoria"])


def versao_banco(session: Session) -> tuple:
    """O que muda o resultado do lado do banco: tabelas Gruppy, coberturas,
    genéricos, categorias, vínculos e lojas (os arquivos da rede entram pela
    data em que foram montados — `meses_disponiveis`)."""
    return tuple(session.execute(select(
        select(func.max(TabelaGruppy.id)).scalar_subquery(),
        select(func.count()).select_from(TabelaGruppyCobertura)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA).scalar_subquery(),
        select(func.count()).select_from(EanGenerico).scalar_subquery(),
        select(func.max(EanGenerico.resolvido_em)).scalar_subquery(),
        select(func.max(CategoriaEan.atualizado_em)).scalar_subquery(),
        select(func.max(VinculoLojaGps.atualizado_em)).scalar_subquery(),
        select(func.max(Loja.atualizado_em)).scalar_subquery(),
    )).one())


def meses_disponiveis(armaz, chaves, hoje: dt.date) -> dict[str, str]:
    """mês → quando o arquivo da rede foi montado, só dos meses que já podem
    aparecer (pedido/rede.py)."""
    from pedido import rede

    visiveis = set(rede.meses_visiveis(hoje))
    chaves_meses = [k for k in armaz.listar(chaves.prefixo_compras_rede())
                    if k.endswith(".json") and k.rsplit("/", 1)[1][:-5] in visiveis]
    # Os manifestos (até 6) lidos ao mesmo tempo — em sequência eram ~140 ms cada.
    manifestos = armaz.ler_varios(chaves_meses, armaz.ler_json)
    return dict(sorted((k.rsplit("/", 1)[1][:-5], m.get("gerado_em", "")) for k, m in zip(chaves_meses, manifestos)))


def ler_compras(armaz, chaves, meses: list[str], eans: list[str]) -> pd.DataFrame:
    """As compras da rede dos `meses`, só dos `eans` (lidos com filtro: o
    arquivo pula os blocos sem nenhum deles — pedido/rede.py)."""
    from pedido import rede

    if not meses or not eans:
        return pd.DataFrame(columns=rede.COLUNAS)
    partes = [armaz.ler_df(chaves.compras_rede(m), filtros=[("ean", "in", eans)]) for m in meses]
    partes = [p for p in partes if not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=rede.COLUNAS)


def parametros_da_configuracao() -> Parametros:
    """As regras de preço com os números das Configurações de Pedidos
    (padrão de todas as lojas)."""
    from core.db import get_session
    from integrations import configuracao_pedido

    with get_session() as session:
        c = configuracao_pedido.vigente(session).config
    return Parametros(limite_bonificacao=settings.analise.limite_bonificacao, custo_faixa_min=c.custo_faixa_min,
                      custo_faixa_max=c.custo_faixa_max, fator_cadastro_nota=c.fator_cadastro_nota,
                      fator_preco_fora=c.fator_preco_fora)


# ---------------------------------------------------------------------------
# Visão Por Produto: uma linha por (loja, produto) — decisões de 05/10/2026
# ---------------------------------------------------------------------------

COLUNAS_PRODUTO = [
    *analise.COLUNAS_LOJA, "produto_id", "base_generico_id", "ean", "nome_canonico", "descricao", "categoria",
    "laboratorio", "preco_pago", "preco_laboratorio", "diferenca", "quantidade", "economia",
    "notas", "notas_acima", "notas_revisar", "so_revisar",
]


def _labs(df: pd.DataFrame, chave: list[str]) -> pd.Series:
    """Laboratórios distintos por grupo, "EMS / MEDLEY"."""
    partes = (df.loc[df["laboratorio"].fillna("") != "", [*chave, "laboratorio"]]
              .assign(laboratorio=lambda d: d["laboratorio"].str.split(" / "))
              .explode("laboratorio").drop_duplicates().sort_values([*chave, "laboratorio"]))
    return partes.groupby(chave)["laboratorio"].agg(" / ".join)


def por_produto(linhas: pd.DataFrame) -> pd.DataFrame:
    """Notas (uma linha por loja × produto × nota, de `calcular`) → uma linha
    por (loja, produto) (Q7–Q10 de 05/10/2026):

    - preço = média ponderada SÓ das notas acima da tabela; Qtd. e economia,
      dessas notas — a conta Diferença × Qtd. = Economia fecha;
    - nenhuma nota acima: média de todas as notas válidas, economia zero
      ("Já otimizada");
    - notas "a revisar" ficam fora da conta (contadas em `notas_revisar`);
      só "a revisar": a média delas, `so_revisar`;
    - laboratório: os das notas que entraram na conta."""
    if linhas.empty:
        return pd.DataFrame(columns=COLUNAS_PRODUTO)
    chave = ["loja_id", "produto_id"]
    df = linhas.copy()
    revisar = df["a_revisar"].astype(bool)
    acima = ~revisar & (df["economia"] > 0)
    tem_acima = acima.groupby([df[c] for c in chave]).transform("any")
    tem_valida = (~revisar).groupby([df[c] for c in chave]).transform("any")
    # A nota entra na conta da linha: acima (se houver), senão as válidas, senão as "a revisar".
    entra = np.where(tem_acima, acima, np.where(tem_valida, ~revisar, revisar))
    df["_entra"] = entra
    conta = df[df["_entra"]].assign(_valor=lambda d: d["preco_pago"] * d["quantidade"])
    soma = conta.groupby(chave).agg(_valor=("_valor", "sum"), quantidade=("quantidade", "sum"),
                                    economia=("economia", "sum"))
    contagem = df.assign(_acima=acima, _revisar=revisar).groupby(chave).agg(
        notas=("nro_nf", "size"), notas_acima=("_acima", "sum"), notas_revisar=("_revisar", "sum"))
    fixos = df.drop_duplicates(chave).set_index(chave)[[
        *analise.COLUNAS_LOJA[1:], "base_generico_id", "ean", "nome_canonico", "descricao", "categoria",
        "preco_laboratorio"]]
    saida = fixos.join(soma).join(contagem).join(_labs(conta, chave).rename("laboratorio")).reset_index()
    saida["preco_pago"] = saida["_valor"] / saida["quantidade"].replace(0, np.nan)
    saida["diferenca"] = saida["preco_pago"] - saida["preco_laboratorio"]
    saida["economia"] = saida["economia"].where(saida["notas_acima"] > 0, 0.0)
    saida["so_revisar"] = saida["notas_revisar"] == saida["notas"]
    for c in ("notas", "notas_acima", "notas_revisar"):
        saida[c] = saida[c].astype(int)
    saida = analise._texto_ausente_como_none(
        saida, (*analise.COLUNAS_LOJA[1:], "nome_canonico", "descricao", "laboratorio", "categoria"))
    return saida[COLUNAS_PRODUTO]


def historico_mensal(linhas: pd.DataFrame) -> pd.DataFrame:
    """Preço médio pago por mês (sem "a revisar"), pro gráfico do detalhe:
    ano_mes, preco, quantidade."""
    validas = linhas[~linhas["a_revisar"].astype(bool)]
    if validas.empty:
        return pd.DataFrame(columns=["ano_mes", "preco", "quantidade"])
    g = (validas.assign(_valor=validas["preco_pago"] * validas["quantidade"])
         .groupby("ano_mes", as_index=False).agg(_valor=("_valor", "sum"), quantidade=("quantidade", "sum")))
    g["preco"] = g["_valor"] / g["quantidade"].replace(0, np.nan)
    return g[["ano_mes", "preco", "quantidade"]].sort_values("ano_mes")
