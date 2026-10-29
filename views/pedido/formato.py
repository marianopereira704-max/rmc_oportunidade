"""Assistente de pedido: formatação de números, moeda, datas e da loja."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from core import ui
from core.config import settings

def _numero(valor, casas: int = 0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    texto = f"{float(valor):,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _moeda(valor) -> str:
    return "—" if valor is None or pd.isna(valor) else ui.formatar_moeda(float(valor))


def _data(valor) -> str:
    if not isinstance(valor, str) or not valor:
        return "—"
    ano, mes, dia = valor[:10].split("-")
    return f"{dia}/{mes}/{ano[2:]}"


def _hora_local(momento: dt.datetime | None) -> str:
    """Horário de Brasília (o banco grava em UTC)."""
    if momento is None:
        return ""
    return momento.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(settings.pedido.fuso)).strftime("%H:%M")


def _agora() -> dt.datetime:
    """Agora em Brasília. `RMC_AGORA` (AAAA-MM-DDTHH:MM) fixa o horário — só o teste visual usa (29/10/2026): a
    prévia do "Salvar como lista" mostra a hora, e a captura do pop-up Listas mudava a cada execução."""
    import os

    if os.environ.get("RMC_AGORA"):
        return dt.datetime.fromisoformat(os.environ["RMC_AGORA"])
    return dt.datetime.now(ZoneInfo(settings.pedido.fuso))


def _identificacao_loja(l: dict) -> str:
    """"RAZÃO SOCIAL - 04.076.088/0001-86" (pop-up de Listas, 02/10/2026)."""
    return f"{(l.get('razao_social') or '').strip()} - {ui.formatar_cnpj(l.get('cnpj') or '')}"
