"""Importação em massa da Base Genéricos a partir de uma planilha CURADA por
humano — bem diferente de Gruppy/GPS: aqui não tem fuzzy-match nenhum, a
coluna de descrição já É o nome canônico, decidido por gente antes do
upload. Ver `reconciliation.motor.importar_base_genericos` pra regra de
agrupamento (mesma descrição -> mesmo genérico) e idempotência (EAN já
resolvido nunca é sobrescrito).

Planilha esperada (nomes de coluna flexíveis, sem acento/maiúscula):
  ean | codigo | sku                        -> EAN do produto (aceita
                                                célula vazia noutra linha
                                                virando float com sufixo
                                                ".0" — ver normalizar_ean)
  descricaomarcos | descricao | produto      -> nome canônico já curado
Outras colunas (ex.: "FCC", código interno) são ignoradas — sem campo
correspondente no schema.
"""
from __future__ import annotations

import io
import re
import unicodedata

import pandas as pd
from sqlalchemy.orm import Session

from core.config import settings
from integrations.base import ResultadoSincronizacao, StatusIntegracao
from reconciliation import motor as reconciliation_motor
from reconciliation.normalizador import normalizar_ean
from storage import filesystem


def _normalizar_coluna(col: str) -> str:
    col = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", col.strip().lower())


def mapear_colunas(df: pd.DataFrame) -> dict[str, str]:
    colunas = settings.colunas.base_genericos
    mapa: dict[str, str] = {}
    for col in df.columns:
        norm = _normalizar_coluna(col)
        for chave in ("ean", "descricao"):
            if norm in set(colunas.get(chave, [])) and chave not in mapa:
                mapa[chave] = col

    obrigatorias = {"ean", "descricao"}
    faltando = obrigatorias - mapa.keys()
    if faltando:
        raise ValueError(
            f"A planilha precisa ter colunas para: {', '.join(sorted(faltando))}. "
            f"Colunas encontradas: {', '.join(str(c) for c in df.columns)}"
        )
    return mapa


def processar_planilha_base_genericos(
    session: Session, conteudo: bytes, nome_arquivo: str, criado_por: str, pasta_destino_id: int,
) -> ResultadoSincronizacao:
    df = pd.read_excel(io.BytesIO(conteudo))
    mapa = mapear_colunas(df)

    filesystem.salvar_arquivo(
        session, pasta_destino_id, nome_arquivo, conteudo, criado_por,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    linhas: list[tuple[str, str]] = []
    for _, linha in df.iterrows():
        ean = normalizar_ean(linha[mapa["ean"]])
        descricao = str(linha[mapa["descricao"]]).strip() if pd.notna(linha[mapa["descricao"]]) else ""
        linhas.append((ean, descricao))

    resultado = reconciliation_motor.importar_base_genericos(session, linhas, criado_por)

    partes = [
        f"{resultado.genericos_criados} genérico(s) novo(s) criado(s)",
        f"{resultado.genericos_reaproveitados} genérico(s) já existente(s) reaproveitado(s)",
        f"{resultado.eans_vinculados} EAN(s) vinculado(s)",
    ]
    if resultado.eans_pulados:
        partes.append(f"{resultado.eans_pulados} EAN(s) pulado(s) (já resolvido antes)")
    if resultado.linhas_invalidas:
        partes.append(f"{resultado.linhas_invalidas} linha(s) inválida(s) (EAN ou descrição vazios)")

    return ResultadoSincronizacao(
        status=StatusIntegracao.MANUAL,
        registros_processados=resultado.eans_vinculados,
        mensagem=f"Planilha '{nome_arquivo}': " + "; ".join(partes) + ".",
    )
