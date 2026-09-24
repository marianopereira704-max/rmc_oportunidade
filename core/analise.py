"""Análise de Oportunidade — regra de 09/2026, sempre de UM laboratório Gruppy.

Para cada (loja, genérico) no período filtrado, compara o MENOR preço que a
loja pagou com o preço do laboratório escolhido na UF da loja:

1. Bonificação (preço unitário abaixo de `limite_bonificacao`, R$ 0,10) sai
   de tudo: disputa, quantidade e média. Só é contada, pra aparecer no detalhe.
2. Preço EFETIVO de cada compra — o primeiro que estiver a até ±50%
   (`tolerancia_preco`) do preço do laboratório:
     VlrUnitario  →  Fat × %CMV ÷ QTD  →  R$ Custo médio
   Se nenhum estiver, fica o VlrUnitario marcado como "fora do padrão"
   (inconsistência de cadastro da loja).
3. VENCEDOR: a compra de menor preço efetivo. Compras "fora do padrão" só
   vencem se não houver nenhuma coerente — erro de cadastro não pode esconder
   uma oportunidade real. Empate no menor preço: as linhas empatadas somam.
4. Qtd. = só a quantidade da(s) compra(s) vencedora(s). Diferença un. = preço
   pago − preço do laboratório (com sinal). Economia = diferença positiva ×
   Qtd., e ZERO quando o vencedor é "fora do padrão".
5. Preço médio: média ponderada pela quantidade dos preços efetivos, de
   todos os laboratórios, nos `meses_preco_medio` últimos meses carregados,
   sem bonificação e sem "fora do padrão". Só informativo.

Desempenho: uma única consulta ao banco traz as compras (só dos genéricos que
o laboratório vende, na UF coberta por ele, nos meses necessários); regras,
filtros, ordenação e paginação rodam em memória. A tela guarda o resultado
por (laboratório, período, versão dos dados) — ver views/analise_comum.py —
então ordenar pelo cabeçalho, paginar ou abrir o detalhe não vão ao banco.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    BaseGenerico,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)
from core.queries import listar_ultimos_ano_meses

FONTE_VLR = "vlr_unitario"
FONTE_CMV = "cmv"
FONTE_CUSTO_MEDIO = "custo_medio"
FONTE_FORA = "fora_do_padrao"

COLUNAS_LOJA = [
    "loja_id", "cnpj", "razao_social", "uf", "cidade", "atendente_comercial",
    "consultor_farma", "consultor_interno", "grupo_economico",
]

COLUNAS_RESULTADO = [
    *COLUNAS_LOJA, "base_generico_id", "nome_canonico", "descricao", "laboratorio", "preco_pago",
    "fonte", "vlr_unitario_original", "preco_laboratorio", "diferenca", "quantidade", "economia",
    "preco_medio", "meses_preco_medio", "bonificacoes",
]


@dataclass
class Filtros:
    laboratorio: str | None = None
    periodo_meses: int = 1
    uf: str | None = None
    busca: str | None = None
    atendente_comercial: str | None = None
    grupo_economico: str | None = None
    loja_ids: list[int] | None = None


def laboratorios_disponiveis(session: Session) -> list[str]:
    """Laboratórios com tabela Gruppy ATIVA em ao menos uma UF — as opções do
    filtro principal."""
    linhas = session.execute(
        select(TabelaGruppy.laboratorio)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA)
        .distinct()
        .order_by(TabelaGruppy.laboratorio)
    ).all()
    return [r[0] for r in linhas]


def versao_dados(session: Session) -> tuple:
    """Assinatura barata do que afeta o resultado (compras, EANs resolvidos,
    tabelas Gruppy, coberturas, lojas). Entra na chave do cache da tela: um
    envio novo, um EAN resolvido ou uma tabela nova mudam a assinatura, e o
    resultado guardado deixa de ser usado sozinho."""
    return tuple(session.execute(select(
        select(func.count()).select_from(RegistroCompraGPS).scalar_subquery(),
        select(func.max(RegistroCompraGPS.id)).scalar_subquery(),
        select(func.sum(RegistroCompraGPS.quantidade * RegistroCompraGPS.custo_unitario)).scalar_subquery(),
        select(func.count()).select_from(EanGenerico).scalar_subquery(),
        select(func.max(EanGenerico.resolvido_em)).scalar_subquery(),
        select(func.max(TabelaGruppy.id)).scalar_subquery(),
        select(func.count()).select_from(TabelaGruppyCobertura)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA).scalar_subquery(),
        select(func.max(Loja.atualizado_em)).scalar_subquery(),
    )).one())


def buscar_compras(session: Session, laboratorio: str, ano_meses: list[str]) -> pd.DataFrame:
    """Compras dos `ano_meses`, já com o preço do laboratório na UF da loja.
    O INNER JOIN com o preço é o que restringe aos genéricos que o
    laboratório vende e às UFs que a tabela dele cobre."""
    preco_lab = (
        select(
            TabelaGruppyCobertura.uf.label("uf"),
            EanGenerico.base_generico_id.label("base_generico_id"),
            func.min(ItemTabelaGruppy.custo_liquido).label("preco_laboratorio"),
        )
        .select_from(ItemTabelaGruppy)
        .join(EanGenerico, EanGenerico.ean == ItemTabelaGruppy.ean)
        .join(TabelaGruppy, TabelaGruppy.id == ItemTabelaGruppy.tabela_gruppy_id)
        .join(TabelaGruppyCobertura, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(TabelaGruppyCobertura.status == StatusCobertura.ATIVA, TabelaGruppy.laboratorio == laboratorio)
        .group_by(TabelaGruppyCobertura.uf, EanGenerico.base_generico_id)
        .subquery()
    )
    stmt = (
        select(
            RegistroCompraGPS.loja_id,
            EanGenerico.base_generico_id,
            BaseGenerico.nome_canonico,
            RegistroCompraGPS.ano_mes,
            RegistroCompraGPS.laboratorio_compra,
            RegistroCompraGPS.descricao_origem,
            RegistroCompraGPS.quantidade,
            RegistroCompraGPS.custo_unitario,
            RegistroCompraGPS.custo_cmv_unitario,
            RegistroCompraGPS.custo_medio_planilha,
            preco_lab.c.preco_laboratorio,
        )
        .select_from(RegistroCompraGPS)
        .join(Loja, Loja.id == RegistroCompraGPS.loja_id)
        .join(EanGenerico, EanGenerico.ean == RegistroCompraGPS.ean)
        .join(BaseGenerico, BaseGenerico.id == EanGenerico.base_generico_id)
        .join(preco_lab, (preco_lab.c.uf == Loja.uf) & (preco_lab.c.base_generico_id == EanGenerico.base_generico_id))
        .where(RegistroCompraGPS.ano_mes.in_(ano_meses))
    )
    df = pd.DataFrame(session.execute(stmt).all(), columns=[
        "loja_id", "base_generico_id", "nome_canonico", "ano_mes", "laboratorio_compra", "descricao_origem",
        "quantidade", "custo_unitario", "custo_cmv_unitario", "custo_medio_planilha", "preco_laboratorio",
    ])
    for coluna in ("quantidade", "custo_unitario", "custo_cmv_unitario", "custo_medio_planilha", "preco_laboratorio"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").astype(float)
    return df


def buscar_lojas(session: Session, loja_ids) -> pd.DataFrame:
    ids = sorted({int(i) for i in loja_ids})
    linhas = []
    for inicio in range(0, len(ids), 1000):
        bloco = ids[inicio:inicio + 1000]
        linhas.extend(session.execute(
            select(Loja.id, Loja.cnpj, Loja.razao_social, Loja.uf, Loja.cidade, Loja.atendente_comercial,
                   Loja.consultor_farma, Loja.consultor_interno, Loja.grupo_economico)
            .where(Loja.id.in_(bloco))
        ).all())
    return pd.DataFrame(linhas, columns=COLUNAS_LOJA)


def calcular(
    compras: pd.DataFrame, meses_periodo: list[str], meses_media: list[str],
    limite_bonificacao: float | None = None, tolerancia: float | None = None,
) -> pd.DataFrame:
    """Aplica as regras 1–5 (ver docstring do módulo). Pura: recebe as
    compras e devolve uma linha por (loja, genérico), sem dados da loja."""
    limite_bonificacao = settings.analise.limite_bonificacao if limite_bonificacao is None else limite_bonificacao
    tolerancia = settings.analise.tolerancia_preco if tolerancia is None else tolerancia
    chave = ["loja_id", "base_generico_id"]
    if compras.empty:
        return pd.DataFrame(columns=[c for c in COLUNAS_RESULTADO if c not in COLUNAS_LOJA[1:]])

    df = compras.copy()
    bonificada = df["custo_unitario"] < limite_bonificacao
    bonificacoes = (
        df[bonificada & df["ano_mes"].isin(meses_periodo)].groupby(chave).size().rename("bonificacoes")
    )
    df = df[~bonificada]

    preco_lab = df["preco_laboratorio"]
    limite = tolerancia * preco_lab

    def _coerente(valor: pd.Series) -> pd.Series:
        return valor.notna() & ((valor - preco_lab).abs() <= limite)

    ok_vlr = _coerente(df["custo_unitario"])
    ok_cmv = _coerente(df["custo_cmv_unitario"])
    ok_medio = _coerente(df["custo_medio_planilha"])
    df = df.assign(
        preco_efetivo=np.select(
            [ok_vlr, ok_cmv, ok_medio],
            [df["custo_unitario"], df["custo_cmv_unitario"], df["custo_medio_planilha"]],
            default=df["custo_unitario"],
        ),
        fonte=np.select([ok_vlr, ok_cmv, ok_medio], [FONTE_VLR, FONTE_CMV, FONTE_CUSTO_MEDIO], default=FONTE_FORA),
    )
    df["coerente"] = df["fonte"] != FONTE_FORA

    # --- Preço médio (regra 5)
    base_media = df[df["ano_mes"].isin(meses_media) & df["coerente"]]
    media = (
        base_media.assign(_valor=base_media["preco_efetivo"] * base_media["quantidade"])
        .groupby(chave)
        .agg(_valor=("_valor", "sum"), _qtd=("quantidade", "sum"), meses_preco_medio=("ano_mes", "nunique"))
    )
    media["preco_medio"] = media["_valor"] / media["_qtd"].replace(0, np.nan)
    media = media[["preco_medio", "meses_preco_medio"]]

    # --- Vencedor (regras 3 e 4)
    periodo = df[df["ano_mes"].isin(meses_periodo)].copy()
    if periodo.empty:
        return pd.DataFrame(columns=[c for c in COLUNAS_RESULTADO if c not in COLUNAS_LOJA[1:]])
    tem_coerente = periodo.groupby(chave)["coerente"].transform("any")
    candidatas = periodo[periodo["coerente"] | ~tem_coerente].copy()
    # Arredonda pra comparar empate sem ruído de ponto flutuante.
    candidatas["_preco"] = candidatas["preco_efetivo"].round(4)
    menor = candidatas.groupby(chave)["_preco"].transform("min")
    vencedoras = candidatas[candidatas["_preco"] == menor]

    resultado = vencedoras.groupby(chave, sort=False).agg(
        nome_canonico=("nome_canonico", "first"),
        descricao=("descricao_origem", "first"),
        preco_pago=("preco_efetivo", "first"),
        fonte=("fonte", "first"),
        vlr_unitario_original=("custo_unitario", "first"),
        preco_laboratorio=("preco_laboratorio", "first"),
        quantidade=("quantidade", "sum"),
    ).reset_index()
    resultado = resultado.join(_laboratorios_vencedores(vencedoras, chave), on=chave)
    resultado["diferenca"] = resultado["preco_pago"] - resultado["preco_laboratorio"]
    resultado["economia"] = np.where(
        (resultado["fonte"] != FONTE_FORA) & (resultado["diferenca"] > 0),
        resultado["diferenca"] * resultado["quantidade"], 0.0,
    )
    resultado = resultado.join(media, on=chave).join(bonificacoes, on=chave)
    resultado["bonificacoes"] = resultado["bonificacoes"].fillna(0).astype(int)
    resultado["meses_preco_medio"] = resultado["meses_preco_medio"].fillna(0).astype(int)
    return resultado


def _laboratorios_vencedores(vencedoras: pd.DataFrame, chave: list[str]) -> pd.Series:
    """Nome do(s) laboratório(s) vencedor(es) por (loja, genérico); empate
    vira "SANDOZ / TEUTO". Só os grupos com mais de um laboratório passam
    pela junção de texto em Python — juntar grupo a grupo nos ~7 mil grupos
    custava ~0,3 s por cálculo, e quase todos têm um laboratório só."""
    labs = (
        vencedoras.loc[vencedoras["laboratorio_compra"].fillna("") != "", [*chave, "laboratorio_compra"]]
        .drop_duplicates()
        .sort_values([*chave, "laboratorio_compra"])
    )
    repetidos = labs.duplicated(chave, keep=False)
    unicos = labs[~repetidos].set_index(chave)["laboratorio_compra"]
    varios = labs[repetidos].groupby(chave)["laboratorio_compra"].agg(" / ".join)
    return pd.concat([unicos, varios]).rename("laboratorio")


def carregar(session: Session, laboratorio: str, periodo_meses: int) -> pd.DataFrame:
    """Resultado completo (todas as lojas) de um laboratório e período — o
    que a tela guarda em cache. Filtros de loja, busca, ordenação e paginação
    são aplicados depois, em memória (`filtrar`, `ordenar`, `por_loja`)."""
    meses_periodo = listar_ultimos_ano_meses(session, periodo_meses)
    meses_media = listar_ultimos_ano_meses(session, settings.analise.meses_preco_medio)
    if not meses_periodo:
        return pd.DataFrame(columns=COLUNAS_RESULTADO)
    compras = buscar_compras(session, laboratorio, sorted(set(meses_periodo) | set(meses_media)))
    resultado = calcular(compras, meses_periodo, meses_media)
    if resultado.empty:
        return pd.DataFrame(columns=COLUNAS_RESULTADO)
    lojas = buscar_lojas(session, resultado["loja_id"].unique())
    final = resultado.merge(lojas, on="loja_id", how="inner")[COLUNAS_RESULTADO]
    return _texto_ausente_como_none(final, (*COLUNAS_LOJA[1:], "nome_canonico", "descricao", "laboratorio"))


def filtrar(df: pd.DataFrame, filtros: Filtros) -> pd.DataFrame:
    if df.empty:
        return df
    mascara = pd.Series(True, index=df.index)
    if filtros.uf:
        mascara &= df["uf"] == filtros.uf
    if filtros.atendente_comercial:
        mascara &= df["atendente_comercial"] == filtros.atendente_comercial
    if filtros.grupo_economico:
        mascara &= df["grupo_economico"] == filtros.grupo_economico
    if filtros.loja_ids:
        mascara &= df["loja_id"].isin(filtros.loja_ids)
    if filtros.busca:
        termo = filtros.busca.strip().lower()
        alvo = (
            df["razao_social"].fillna("") + " " + df["cnpj"].fillna("") + " " + df["nome_canonico"].fillna("")
            + " " + df["laboratorio"].fillna("") + " " + df["descricao"].fillna("")
        ).str.lower()
        mascara &= alvo.str.contains(termo, regex=False)
    return df[mascara]


def por_loja(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por loja: economia somada e quantos genéricos."""
    if df.empty:
        return pd.DataFrame(columns=[*COLUNAS_LOJA, "economia", "qtd_produtos"])
    lojas = (
        df.groupby(COLUNAS_LOJA, dropna=False, sort=False)
        .agg(economia=("economia", "sum"), qtd_produtos=("base_generico_id", "nunique"))
        .reset_index()
    )
    return _texto_ausente_como_none(lojas, COLUNAS_LOJA[1:])


def _texto_ausente_como_none(df: pd.DataFrame, colunas) -> pd.DataFrame:
    """Texto ausente como None, não NaN: NaN é "verdadeiro" em Python, então
    `valor or "—"` na tela mostraria "nan" em vez do traço. O groupby recria
    NaN mesmo quando a entrada tinha None, por isso a limpeza vale nos dois
    pontos de saída (`carregar` e `por_loja`)."""
    for coluna in colunas:
        df[coluna] = df[coluna].astype(object).where(df[coluna].notna(), None)
    return df


def ordenar(df: pd.DataFrame, coluna: str, crescente: bool) -> pd.DataFrame:
    """Ordena pela coluna; vazios SEMPRE no fim, nos dois sentidos. Texto sem
    diferença de maiúscula/minúscula. Desempate estável por loja e genérico,
    pra paginação não embaralhar linhas de mesmo valor."""
    if df.empty or coluna not in df.columns:
        return df
    def chave(serie: pd.Series) -> pd.Series:
        # pandas 3 guarda texto num dtype próprio ("str"), não mais em object —
        # checar só `object` deixava a ordenação sensível a maiúsculas.
        if pd.api.types.is_string_dtype(serie) or serie.dtype == object:
            return serie.astype("string").str.lower()
        return serie
    desempate = [c for c in ("loja_id", "base_generico_id") if c in df.columns and c != coluna]
    return df.sort_values(
        [coluna, *desempate], ascending=[crescente, *([True] * len(desempate))],
        na_position="last", key=chave, kind="mergesort",
    )
