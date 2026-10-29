"""Fotos dos insumos do cálculo de madrugada (29/10/2026) — `docs/specs/2026-10-29-calculo-madrugada-design.md`.

A rotina do GitHub não alcança o banco (o firewall só libera o Streamlit Cloud). O que as contas das telas leem do
banco vai, então, para o Spaces: as 3 tabelas grandes pela cópia de `core/copia_spaces.py` (a mesma que o app lê) e
o resto num `manifesto.json` pequeno — configuração padrão, personalização e correções de estoque negativo de cada
loja, lojas com vínculo e as versões das bases. Uma pasta por banco (`pedido/insumos/<banco>/`): o app local (banco
dev) e o publicado nunca se misturam.

Quando publica: depois de cada mudança (salvar Configurações, corrigir estoque negativo) e, como garantia, no 1º
acesso de cada sessão — no máximo a cada 10 min por processo, em segundo plano. Só grava se algo mudou (assinatura
do conteúdo). Falhar aqui nunca quebra a tela: a rotina usa a foto anterior, a assinatura não bate e o app calcula na
hora, como antes.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

INTERVALO_S = 600
_trava = threading.Lock()
_ultimo = [0.0]


def montar(session: Session, armaz) -> dict:
    """O manifesto do banco desta sessão, garantindo no Spaces as cópias das 3 tabelas na versão atual."""
    from core import copia_spaces
    from core.models import CorrecaoEstoque
    from integrations import catalogo, classificacao, configuracao_pedido, pedido_tela
    from integrations import categorias as categorias_integ

    v_categorias, v_genericos = categorias_integ.versao(session), pedido_tela.versao_genericos(session)
    versao_catalogo = (catalogo.versao(session), classificacao.versao(session), v_categorias)
    # As MESMAS versões (e as mesmas funções de montar) que views/pedido/dados_loja.py usa: a cópia é uma só.
    tabelas = {}
    for nome, versao, montar_tabela in (
        ("base_categorias", v_categorias, lambda: categorias_integ.tabela(session, com_origem=False)),
        ("genericos", v_genericos, lambda: pedido_tela.genericos(session)),
        ("classificacao_universo", versao_catalogo, lambda: classificacao.universo(session)),
    ):
        copia_spaces.ler_ou_montar(nome, versao, montar_tabela, armaz)
        tabelas[nome] = copia_spaces.chave(nome, versao)

    personalizacoes = {str(loja_id): json.loads(p.valores) for loja_id, p in
                       configuracao_pedido._ultimas_por_loja(session).items() if json.loads(p.valores)}
    correcoes: dict[str, list] = {}
    for c in session.scalars(select(CorrecaoEstoque)):
        correcoes.setdefault(str(c.loja_id), []).append([c.linha, float(c.estoque_corrigido)])
    lojas = [{"id": l["id"], "legacy_id": l.get("legacy_id"), "empresa_gps": l["empresa_gps"], "loja_gps": l["loja_gps"]}
             for l in pedido_tela.lojas(session) if l.get("empresa_gps") and l.get("loja_gps")]
    return {
        "banco": copia_spaces.nome_do_banco(),
        "config_padrao": dataclasses.asdict(configuracao_pedido.vigente(session).config),
        "personalizacoes": personalizacoes,
        # Ordenadas como o app monta a chave do cache (`tuple(sorted(...))`): a assinatura sai igual.
        "correcoes": {k: sorted(v) for k, v in correcoes.items()},
        "lojas": lojas,
        "versoes": [v_categorias, v_genericos],
        "versao_catalogo": list(versao_catalogo),
        "tabelas": tabelas,
    }


def _assinatura(manifesto: dict) -> str:
    return hashlib.sha256(json.dumps(manifesto, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def publicar(session: Session, armaz=None) -> bool:
    """Grava o manifesto se mudou desde a última publicação. True = gravou."""
    from core import copia_spaces
    from core.config import settings
    from pedido import precalculo
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    armaz = armaz or do_ambiente()
    manifesto = montar(session, armaz)
    k = f"{precalculo.pasta_insumos(Chaves(settings.pedido.prefixo), copia_spaces.nome_do_banco())}manifesto.json"
    assinatura = _assinatura(manifesto)
    if armaz.existe(k) and armaz.ler_json(k).get("assinatura") == assinatura:
        return False
    armaz.salvar_json(k, {**manifesto, "assinatura": assinatura,
                          "publicado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    return True


def _publicar_agora() -> None:
    from core.db import get_session

    try:
        with get_session() as session:
            publicar(session)
    except Exception:  # noqa: BLE001 — só um atalho: a tela segue, a rotina usa a foto anterior
        logger.warning("fotos dos insumos não publicadas", exc_info=True)


def publicar_em_segundo_plano(forcar: bool = False) -> None:
    """Publica numa thread, sem segurar a tela. Sem `forcar`, no máximo a cada `INTERVALO_S` por processo (o 1º
    acesso de cada sessão chama isto); `forcar` = acabou de mudar algo (Configurações, correção de estoque)."""
    agora = time.monotonic()
    with _trava:
        if not forcar and agora - _ultimo[0] < INTERVALO_S:
            return
        _ultimo[0] = agora
    threading.Thread(target=_publicar_agora, name="publicar_insumos", daemon=True).start()
