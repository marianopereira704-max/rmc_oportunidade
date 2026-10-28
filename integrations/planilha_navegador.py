"""Planilha lida no NAVEGADOR (views/leitor_planilha.py) e entregue ao app.

Por que existe: ler um .xlsx de ~150 mil linhas no servidor custava ~25s
(openpyxl) e centenas de MB de RAM — num servidor com pouca memória, o
processo morria no meio (OOM) e o upload "travava" sem gravar nada. O
navegador de quem envia tem memória de sobra: ele abre o .xlsx, converte a
primeira aba em CSV, compacta (gzip) e manda só isso. Aqui o CSV vira um
DataFrame equivalente ao que `pd.read_excel` produzia, com uma fração do
custo.

(A leitura do rodapé do Power BI saiu em 05/10/2026 junto com o envio da
planilha do GPS — as compras vêm da API.)
"""
from __future__ import annotations

import base64
import gzip
import io
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





