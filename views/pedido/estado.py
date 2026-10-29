"""Assistente de pedido: o que a tela lê do banco sobre a loja (relido a cada 30 s ou depois de uma ação) e as notificações."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import streamlit as st

from core.config import settings
from core.db import get_session

_KEY = "pedido"
_ORDEM_PADRAO = ("valor", False)  # o que mais vende primeiro (coluna oculta); "a revisar" antes de tudo
_ABRIR_NEGATIVOS = f"{_KEY}_abrir_negativos"
# Notificações (02/10/2026): nada fica "permanente" na tela — sucesso, erro,
# aviso e informação viram notificação no canto superior direito, com X e
# 5 s, cada tipo com o seu ícone (o ✅ de sucesso, o ❌ de erro…).
_ICONE_NOTIFICACAO = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
_AVISADOS = f"{_KEY}_avisados"   # avisos de estado já mostrados nesta sessão: (loja, texto)
_ESTADO = f"{_KEY}_estado"       # o que a tela lê do banco sobre a loja (ver `_estado_loja`)
_ESTADO_SEGUNDOS = 30


@dataclass
class _EstadoLoja:
    """Tudo o que a tela lê do banco sobre a loja e o usuário, guardado na
    sessão (P1 de 02/10/2026): antes eram 8 consultas a CADA clique (~1,1 s
    contra o banco em NY), e quase nada muda entre um clique e outro. Relê
    depois de 30 s (o que outra pessoa fez aparece com até 30 s de atraso) ou
    depois de uma ação da tela (`_invalidar_estado`). A área de trabalho,
    que muda a cada clique, é atualizada na cópia depois de gravar."""
    chave: tuple
    lido_em: float
    correcoes: dict
    area: object
    listas: list
    avisos: list
    vistos: dict
    personalizacao: dict


def _estado_loja(uid: int, usuario_nome: str, loja_id: int) -> _EstadoLoja:
    from integrations import configuracao_pedido, pedido_area, pedido_rascunho

    atual = st.session_state.get(_ESTADO)
    if atual is not None and atual.chave == (uid, loja_id) and time.monotonic() - atual.lido_em < _ESTADO_SEGUNDOS:
        return atual
    with get_session() as session:
        listas = pedido_area.listas(session, loja_id)
        estado = _EstadoLoja(
            chave=(uid, loja_id), lido_em=time.monotonic(),
            correcoes=pedido_rascunho.correcoes(session, loja_id),
            area=pedido_area.area(session, uid, loja_id),
            listas=listas,
            avisos=pedido_area.avisos(session, uid, usuario_nome, loja_id,
                                      dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date(),
                                      listas_da_loja=listas),
            vistos=pedido_area.avisos_vistos(session, uid, loja_id),
            personalizacao=configuracao_pedido.da_loja(session, loja_id),
        )
    st.session_state[_ESTADO] = estado
    return estado


def _invalidar_estado() -> None:
    st.session_state.pop(_ESTADO, None)


def _atualizar_area(uid: int, loja_id: int, mudar) -> None:
    """Depois de gravar uma quantidade/marcação: a cópia na sessão recebe o
    que acabou de ir pro banco (mesmas regras — integrations/pedido_area.py).
    `mudar(itens) → itens novos` parte da cópia ATUAL da sessão, não da área
    que a tela mostrava: em dois cliques rápidos (antes do redesenho), o
    segundo partia da versão anterior e apagava o primeiro da cópia."""
    from integrations import pedido_area

    estado = st.session_state.get(_ESTADO)
    if estado is not None and estado.chave == (uid, loja_id):
        estado.area = pedido_area.Area(mudar(estado.area.itens), dt.datetime.utcnow(), estado.area.lista_aberta_id)


def _concluir(tipo: str, texto: str) -> None:
    """Fim de uma ação que mudou dados (lista, exportação, correção…): a
    notificação no próximo redesenho e o estado relido do banco."""
    st.session_state[f"{_KEY}_msg"] = (tipo, texto)
    _invalidar_estado()


def _notificar(tipo: str, texto: str) -> None:
    st.toast(texto, icon=_ICONE_NOTIFICACAO.get(tipo, "ℹ️"), duration=5)


def _avisar_uma_vez(loja_id: int, tipo: str, texto: str) -> None:
    """Aviso de ESTADO (pedido duplicado, dia sem dado, parâmetros): a tela é
    redesenhada a cada clique, e o aviso reapareceria o tempo todo — mostra
    uma vez por loja na sessão (ao abrir ou trocar de loja)."""
    vistos = st.session_state.setdefault(_AVISADOS, set())
    if (loja_id, texto) not in vistos:
        vistos.add((loja_id, texto))
        _notificar(tipo, texto)   # "Corrigir manualmente" → abre a lista no rerun seguinte
