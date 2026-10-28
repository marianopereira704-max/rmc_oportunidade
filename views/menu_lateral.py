"""Item pai recolhível da barra lateral (21/10/2026): "Dashboard" abre e fecha a
lista de telas dele (Estoque, Ruptura, Compras).

O pai NÃO leva a nenhuma tela — só abre e fecha. Fica destacado (fundo claro,
negrito) enquanto uma tela filha está ativa; a filha ativa ganha o verde cheio
dos demais itens. A seta é desenhada em CSS (`core/theme.py`, bloco "MENU
RECOLHÍVEL"), pois o botão do Streamlit só aceita UM ícone, o da esquerda.

Aberto ou fechado fica na sessão e começa ABERTO quando a tela ativa é uma das
filhas. O botão usa `on_click` (e não `st.rerun`): o clique já roda o script
uma vez, e um segundo rerun custaria as consultas da barra lateral de novo.
"""
from __future__ import annotations

import streamlit as st

_CHAVE = "menu_{}_aberto"


def esta_aberto(painel: str, filha_ativa: bool) -> bool:
    chave = _CHAVE.format(painel)
    if chave not in st.session_state:
        st.session_state[chave] = filha_ativa
    return bool(st.session_state[chave])


def _alternar(painel: str) -> None:
    chave = _CHAVE.format(painel)
    st.session_state[chave] = not st.session_state.get(chave, False)


def botao_pai(painel: str, rotulo: str, icone: str, aberto: bool, filha_ativa: bool) -> None:
    classes = "nav-pai" + (" aberto" if aberto else "") + (" ativo" if filha_ativa else "")
    with st.container():
        st.markdown(f'<div class="{classes}"></div>', unsafe_allow_html=True)
        st.button(rotulo, key=f"nav_{painel}", icon=icone, use_container_width=True,
                  on_click=_alternar, args=(painel,))


def botao_filha(chave: str, rotulo: str, ativa: bool) -> bool:
    """True quando clicou."""
    with st.container():
        st.markdown(f'<div class="nav-filho{"-ativo" if ativa else ""}"></div>', unsafe_allow_html=True)
        return st.button(rotulo, key=f"nav_{chave}", use_container_width=True)
