from __future__ import annotations

import re

import streamlit as st

from core import auth, theme
from core.config import settings


def _extrair_digitos_cnpj(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")[:14]


def _formatar_cnpj_progressivo(digitos: str) -> str:
    d = digitos
    if len(d) <= 2:
        return d
    if len(d) <= 5:
        return f"{d[:2]}.{d[2:]}"
    if len(d) <= 8:
        return f"{d[:2]}.{d[2:5]}.{d[5:]}"
    if len(d) <= 12:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:]}"
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def _on_change_cnpj() -> None:
    digitos = _extrair_digitos_cnpj(st.session_state.get("login_cnpj", ""))
    st.session_state["login_cnpj"] = _formatar_cnpj_progressivo(digitos)


def render() -> None:
    theme.aplicar_estilo_login()

    with st.container(key="login-center-wrap"):
        st.markdown(theme.logo_login_markup(), unsafe_allow_html=True)
        st.markdown(
            f'<p class="login-headline">Bem-vindo ao <span>{settings.nome_fornecedor} Oportunidades</span></p>'
            f'<p class="login-subheadline">Comparativo de preço de compra — {settings.nome_rede}</p>',
            unsafe_allow_html=True,
        )

        with st.container(key="login-card"):
            cnpj = st.text_input(
                "CNPJ",
                key="login_cnpj",
                placeholder="00.000.000/0000-00",
                on_change=_on_change_cnpj,
            )
            senha = st.text_input("Senha", type="password", key="login_senha")
            lembrar = st.checkbox("Lembrar-me", value=True, key="login_lembrar")
            entrar = st.button("Entrar", type="primary", width="stretch")

            if entrar:
                if not cnpj or not senha:
                    st.error("Informe CNPJ e senha.")
                else:
                    usuario = auth.autenticar(cnpj, senha)
                    if usuario is None:
                        st.error("CNPJ ou senha inválidos.")
                    else:
                        auth.iniciar_sessao(usuario, lembrar)
                        st.rerun()
