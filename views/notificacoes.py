"""Minhas notificações (29/10/2026, etapa 7 — docs/specs/2026-10-29-emails-lojas-design.md).

O usuário de loja (Comprador e Proprietário) escolhe os e-mails que recebe: o aviso de ruptura (ligado de saída,
decisão do Mariano) e o relatório diário (opcional). Grava na hora e a lista de destinatários vai para a rotina
(integrations/destinatarios.py); vale a partir do e-mail da manhã seguinte. O link do rodapé dos e-mails
(`?pagina=notificacoes`) abre esta tela depois do login (app.py).
"""
from __future__ import annotations

import html

import streamlit as st

from core import auth, theme
from core.db import get_session

_KEY = "notificacoes"
_MSG = f"{_KEY}_msg"


def render() -> None:
    with theme.tela("notificacoes"):
        _render()


def _gravar(usuario_id: int) -> None:
    from integrations import insumos, usuarios

    ruptura = bool(st.session_state.get(f"{_KEY}_ruptura"))
    relatorio = bool(st.session_state.get(f"{_KEY}_relatorio"))
    with get_session() as session:
        usuarios.gravar_preferencias(session, usuario_id, ruptura, relatorio)
    insumos.publicar_em_segundo_plano(forcar=True)
    st.session_state[_MSG] = "Preferência salva: vale a partir do e-mail de amanhã."


def _render() -> None:
    from core.models import Usuario
    from integrations import usuarios

    usuario = auth.usuario_atual()
    mensagem = st.session_state.pop(_MSG, None)
    if mensagem:
        st.toast(mensagem, icon="✅", duration=5)
    with get_session() as session:
        u = session.get(Usuario, usuario["id"])
        ruptura, relatorio = usuarios.preferencias(u)
        email = u.login or ""
    st.markdown('<div class="rmc-pedido-titulo">Minhas notificações</div>', unsafe_allow_html=True)
    # E-mail de login: trocar, só pelo Cadastro (quem cadastrou a loja).
    st.markdown(f'<p class="rmc-notif-para">Os e-mails vão para <strong>{html.escape(email)}</strong>. '
                'Para trocar o endereço, fale com quem fez o seu cadastro.</p>', unsafe_allow_html=True)
    with st.container(key="notificacoes-opcoes", border=True):
        st.toggle("Aviso de ruptura", value=ruptura, key=f"{_KEY}_ruptura", on_change=_gravar,
                  args=(usuario["id"],))
        st.caption("Quando um produto que mais fatura na loja zera. Só chega nos dias em que isso acontece.")
        st.toggle("Relatório diário", value=relatorio, key=f"{_KEY}_relatorio", on_change=_gravar,
                  args=(usuario["id"],))
        st.caption("Todo dia de manhã: orçamento, itens a comprar, ruptura e os 10 mais críticos.")
    st.caption("Com os dois ligados, chega um e-mail só, com o aviso no topo. Quem tem várias lojas recebe um e-mail "
               "com todas elas.")
