"""Verificação periódica se a lista de IPs de saída do Streamlit Community
Cloud (liberada no firewall do servidor de persistência, pra ele conseguir
falar com o Postgres) ainda bate com a publicada oficialmente pelo
Streamlit em docs.streamlit.io/deploy/streamlit-community-cloud/status.

Streamlit avisa, textualmente, que essa lista "pode mudar a qualquer
momento sem aviso". Se isso acontecer e ninguém perceber, o app só vai
descobrir quando o firewall bloquear a conexão e o Postgres parar de
responder — um jeito ruim de descobrir. Este módulo antecipa isso: no
login do admin (ver app.py), confere se a lista mudou e, se sim, devolve
um aviso pra tela — nunca ajusta o firewall sozinho, só avisa.

Fluxo de `verificar_e_avisar_se_necessario`:
1. Já verificou há menos de `settings.streamlit_cloud.intervalo_verificacao_
   horas`? Não bate na rede de novo — devolve o resultado da última
   verificação guardada no banco (evita checar a cada rerun da página).
2. Senão, busca a lista atual publicada pelo Streamlit e compara (como
   conjunto, ordem não importa) com `settings.streamlit_cloud.ips_conhecidos`
   (a lista que foi usada pra configurar o firewall). Grava o resultado
   (nunca falha o login por causa disso — qualquer erro de rede/parsing
   é tratado como "sem novidade" e logado, ver `_buscar_ips_publicados_
   atuais`)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import VerificacaoIpsStreamlitCloud

logger = logging.getLogger(__name__)

_URL_STATUS_STREAMLIT_CLOUD = "https://docs.streamlit.io/deploy/streamlit-community-cloud/status"
_REGEX_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _buscar_ips_publicados_atuais() -> set[str] | None:
    """None em qualquer falha (rede fora do ar, página mudou de formato,
    timeout) — o chamador trata como "sem novidade" e mantém o resultado da
    última verificação bem-sucedida, nunca travando o login do admin por
    causa de uma checagem que é só informativa."""
    try:
        resp = requests.get(_URL_STATUS_STREAMLIT_CLOUD, timeout=10)
        resp.raise_for_status()
        return set(_REGEX_IPV4.findall(resp.text))
    except Exception:
        logger.warning(
            "Falha ao verificar a lista de IPs do Streamlit Community Cloud "
            "(%s) — mantendo o resultado da última verificação.",
            _URL_STATUS_STREAMLIT_CLOUD, exc_info=True,
        )
        return None


def _ultima_verificacao(session: Session) -> VerificacaoIpsStreamlitCloud | None:
    return session.execute(
        select(VerificacaoIpsStreamlitCloud).order_by(VerificacaoIpsStreamlitCloud.id.desc())
    ).scalars().first()


def verificar_e_avisar_se_necessario(session: Session) -> bool:
    """Devolve True só quando a lista publicada pelo Streamlit diverge da
    configurada em `settings.streamlit_cloud.ips_conhecidos` — quem chamou
    (app.py) decide como avisar o admin (popup)."""
    ultima = _ultima_verificacao(session)
    intervalo = dt.timedelta(hours=settings.streamlit_cloud.intervalo_verificacao_horas)

    if ultima is not None and dt.datetime.utcnow() - ultima.verificado_em < intervalo:
        return ultima.divergente

    ips_publicados = _buscar_ips_publicados_atuais()
    if ips_publicados is None:
        return ultima.divergente if ultima is not None else False

    divergente = ips_publicados != set(settings.streamlit_cloud.ips_conhecidos)

    session.add(VerificacaoIpsStreamlitCloud(
        ips_publicados_snapshot=json.dumps(sorted(ips_publicados)),
        divergente=divergente,
    ))

    return divergente
