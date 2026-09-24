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


def validar_planilha(df: pd.DataFrame) -> None:
    """Recusa planilha que não é uma Base Genéricos curada. A Base tem só EAN
    + nome canônico (ex: FCC | EAN | DESCRIÇÃO MARCOS); uma tabela de preços
    Gruppy também tem EAN e "Produto", e por isso PASSAVA no mapeamento — e
    cada descrição dela virava um genérico canônico (aconteceu em 24/09/2026:
    22 genéricos falsos). Colunas de preço, custo, desconto, quantidade ou
    CNPJ denunciam que é Gruppy ou GPS. Levanta ValueError antes de qualquer
    gravação."""
    proibidas = settings.colunas.proibidas_base_genericos
    suspeitas = [str(c) for c in df.columns if any(t in _normalizar_coluna(c) for t in proibidas)]
    if suspeitas:
        raise ValueError(
            "Esta planilha não parece a Base Genéricos: ela tem colunas de preço/quantidade/CNPJ "
            f"({', '.join(suspeitas)}), típicas de tabela Gruppy ou compras GPS. A Base Genéricos deve "
            "ter só EAN e o nome canônico do genérico. Nada foi importado — envie esta planilha na "
            "seção certa."
        )


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
    session: Session, conteudo: bytes | None, nome_arquivo: str, criado_por: str, pasta_destino_id: int,
    df: pd.DataFrame | None = None, storage_key: str | None = None, tamanho_bytes: int | None = None,
) -> ResultadoSincronizacao:
    """`df` (planilha já lida no navegador) e `storage_key` (original já no
    Spaces) vêm da tela — ver views/leitor_planilha.py. Sem eles, lê e grava
    `conteudo` como antes."""
    if df is None:
        df = pd.read_excel(io.BytesIO(conteudo))
    validar_planilha(df)
    mapa = mapear_colunas(df)

    filesystem.guardar_planilha(
        session, pasta_destino_id, nome_arquivo, criado_por,
        conteudo=conteudo, storage_key=storage_key, tamanho_bytes=tamanho_bytes,
    )

    df_norm = pd.DataFrame({"ean": df[mapa["ean"]], "descricao": df[mapa["descricao"]]})
    linhas: list[tuple[str, str]] = [
        (normalizar_ean(linha.ean), str(linha.descricao).strip() if pd.notna(linha.descricao) else "")
        for linha in df_norm.itertuples(index=False)
    ]

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
