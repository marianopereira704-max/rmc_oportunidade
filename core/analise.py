"""Análise de Oportunidade — peças comuns às visões Por Loja e Por Produto:
filtros em memória, ordenação, indicadores (cards), comparação com o
período anterior, lojas e laboratórios.

O cálculo da oportunidade está em core/analise_api.py (compras da API do
GPS, desde 05/10/2026). A regra da planilha do BI que morava aqui (menor
preço pago, recuo VlrUnitario → CMV → custo médio, preço médio 3m) saiu
junto com o envio da planilha — está no histórico do git.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Loja, StatusCobertura, TabelaGruppy, TabelaGruppyCobertura

SEM_CATEGORIA = "Sem Classificação"

COLUNAS_LOJA = [
    "loja_id", "cnpj", "razao_social", "uf", "cidade", "atendente_comercial",
    "consultor_farma", "consultor_interno", "grupo_economico",
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
    # Lojas que o USUÁRIO pode ver (Comprador/Proprietário, 02/10/2026);
    # None = todas. Diferente de `loja_ids` (escolha da tela): é o corte de
    # acesso, aplicado sempre — inclusive na seta do período anterior.
    lojas_permitidas: tuple[int, ...] | None = None
    # Categoria do produto (Por Produto, 05/10/2026 — base de categorias);
    # produto sem categoria conta como "Sem Classificação".
    categorias: tuple[str, ...] | None = None


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





# ---------------------------------------------------------------------------
# Indicadores (cards do topo) e comparação com o período anterior
# ---------------------------------------------------------------------------

def _mes_menos(ano_mes: str, meses: int) -> str:
    ano, mes = (int(p) for p in ano_mes.split("-"))
    total = ano * 12 + (mes - 1) - meses
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def meses_periodo_anterior(meses_periodo: list[str], carregados: set[str]) -> list[str] | None:
    """Os N meses de CALENDÁRIO imediatamente antes do período, se TODOS
    estiverem carregados; senão None (a tela não mostra seta). Não usa "os N
    carregados anteriores": com agosto e janeiro carregados, "último mês"
    compararia agosto com janeiro e a seta diria que algo mudou "desde o mês
    passado" quando são sete meses de diferença."""
    if not meses_periodo:
        return None
    mais_antigo = min(meses_periodo)
    anteriores = [_mes_menos(mais_antigo, k) for k in range(1, len(meses_periodo) + 1)]
    return sorted(anteriores) if all(m in carregados for m in anteriores) else None


@dataclass
class Indicadores:
    economia: float
    lojas_com_oportunidade: int
    lojas_analisadas: int
    produtos_com_oportunidade: int
    produtos_analisados: int

    @property
    def economia_media_loja(self) -> float | None:
        return self.economia / self.lojas_com_oportunidade if self.lojas_com_oportunidade else None

    @property
    def economia_media_produto(self) -> float | None:
        return self.economia / self.produtos_com_oportunidade if self.produtos_com_oportunidade else None


def variacao(atual: float | None, anterior: float | None) -> float | None:
    """Variação relativa (0,12 = +12%). None quando não dá pra comparar
    (sem período anterior, ou anterior zero — "de 0 para 5" não é %)."""
    if atual is None or anterior is None or anterior == 0:
        return None
    return (atual - anterior) / anterior


def filtrar(df: pd.DataFrame, filtros: Filtros) -> pd.DataFrame:
    if df.empty:
        return df
    mascara = pd.Series(True, index=df.index)
    if filtros.lojas_permitidas is not None:
        mascara &= df["loja_id"].isin(filtros.lojas_permitidas)
    if filtros.uf:
        mascara &= df["uf"] == filtros.uf
    if filtros.atendente_comercial:
        mascara &= df["atendente_comercial"] == filtros.atendente_comercial
    if filtros.grupo_economico:
        mascara &= df["grupo_economico"] == filtros.grupo_economico
    if filtros.loja_ids:
        mascara &= df["loja_id"].isin(filtros.loja_ids)
    if filtros.categorias and "categoria" in df.columns:
        mascara &= df["categoria"].fillna(SEM_CATEGORIA).isin(filtros.categorias)
    if filtros.busca:
        termo = filtros.busca.strip().lower()
        alvo = (
            df["razao_social"].fillna("") + " " + df["cnpj"].fillna("") + " " + df["nome_canonico"].fillna("")
            + " " + df["laboratorio"].fillna("") + " " + df["descricao"].fillna("")
        ).str.lower()
        encontrado = alvo.str.contains(termo, regex=False)
        # CNPJ digitado com pontuação ("10.482.949/0001-29") também acha: o
        # banco guarda só os dígitos, e o CNPJ formatado é o que aparece na
        # tela — copiar de lá e colar na busca não achava nada.
        if re.fullmatch(r"[\d./\-\s]+", termo):
            digitos = re.sub(r"\D", "", termo)
            if digitos:
                encontrado |= df["cnpj"].fillna("").str.contains(digitos, regex=False)
        mascara &= encontrado
    return df[mascara]


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
        # strip: 22 lojas do cadastro vêm com espaço antes da razão social
        # (visto em 24/09/2026) — " DROGARIA LIZ" ia pro topo do A–Z.
        if pd.api.types.is_string_dtype(serie) or serie.dtype == object:
            return serie.astype("string").str.strip().str.lower()
        return serie
    desempate = [c for c in ("loja_id", "base_generico_id") if c in df.columns and c != coluna]
    return df.sort_values(
        [coluna, *desempate], ascending=[crescente, *([True] * len(desempate))],
        na_position="last", key=chave, kind="mergesort",
    )
