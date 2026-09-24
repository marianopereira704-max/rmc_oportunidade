"""Planilha lida no NAVEGADOR (views/leitor_planilha.py) e entregue ao app.

Por que existe: ler um .xlsx de ~150 mil linhas no servidor custava ~25s
(openpyxl) e centenas de MB de RAM — num servidor com pouca memória, o
processo morria no meio (OOM) e o upload "travava" sem gravar nada. O
navegador de quem envia tem memória de sobra: ele abre o .xlsx, converte a
primeira aba em CSV, compacta (gzip) e manda só isso. Aqui o CSV vira um
DataFrame equivalente ao que `pd.read_excel` produzia, com uma fração do
custo.

Também lê o RODAPÉ que o Power BI acrescenta à exportação — de onde saem o
mês filtrado ("2026/Ago. (AnoMesFiltro)") e o aviso de exportação cortada no
limite de linhas ("Exported data exceeded the allowed volume").
"""
from __future__ import annotations

import base64
import gzip
import io
import re
import unicodedata
from dataclasses import dataclass

import pandas as pd


@dataclass
class PlanilhaRecebida:
    nome: str
    tamanho_bytes: int
    df: pd.DataFrame
    # Chave do original no storage quando o navegador já o enviou direto
    # (Spaces); None em modo local, quando os bytes vêm em `conteudo`.
    storage_key: str | None
    conteudo: bytes | None
    segundos_leitura_navegador: float | None = None


def decodificar_payload(payload: dict) -> PlanilhaRecebida:
    """`payload` é o que o componente devolve em `setTriggerValue("arquivo", ...)`."""
    csv_bytes = gzip.decompress(base64.b64decode(payload["csv_gz_b64"]))
    df = pd.read_csv(io.BytesIO(csv_bytes), low_memory=False)
    original_b64 = payload.get("original_b64")
    return PlanilhaRecebida(
        nome=str(payload.get("nome") or "planilha.xlsx"),
        tamanho_bytes=int(payload.get("tamanho") or 0),
        df=df,
        storage_key=payload.get("storage_key") if payload.get("enviado_storage") else None,
        conteudo=base64.b64decode(original_b64) if original_b64 else None,
        segundos_leitura_navegador=payload.get("segundos_leitura"),
    )


@dataclass
class InfoRodape:
    ano_mes: str | None  # "2026-08", quando o rodapé traz o filtro AnoMesFiltro
    exportacao_cortada: bool


_MESES_ABREVIADOS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    # Power BI em inglês
    "feb": 2, "apr": 4, "may": 5, "aug": 8, "sep": 9, "oct": 10, "dec": 12,
}

_PADRAO_ANO_MES = re.compile(r"(\d{4})\s*/\s*([a-z]{3})[a-z]*\.?\s*\(\s*anomesfiltro\s*\)", re.IGNORECASE)
_MARCAS_CORTE = ("exceeded the allowed volume", "excederam o volume permitido", "excedeu o volume permitido")


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def ler_rodape(df: pd.DataFrame, linhas: int = 20) -> InfoRodape:
    """Procura, só no texto das últimas `linhas` linhas, o filtro de mês e o
    aviso de corte. Nenhum dos dois achados é obrigatório: arquivo sem rodapé
    devolve `InfoRodape(None, False)`."""
    textos = [v for v in df.tail(linhas).to_numpy().ravel() if isinstance(v, str)]
    texto = _sem_acento(" ".join(textos)).lower()

    ano_mes = None
    achado = _PADRAO_ANO_MES.search(texto)
    if achado and achado.group(2).lower() in _MESES_ABREVIADOS:
        ano_mes = f"{int(achado.group(1)):04d}-{_MESES_ABREVIADOS[achado.group(2).lower()]:02d}"

    return InfoRodape(ano_mes=ano_mes, exportacao_cortada=any(m in texto for m in _MARCAS_CORTE))
