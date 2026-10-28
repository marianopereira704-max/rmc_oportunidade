"""Tela de login e tela "Criar senha" (link do e-mail).

Um campo só, "E-mail ou CNPJ" (Q19 de 06/10/2026): a equipe RMC entra pelo
CNPJ da rede, as lojas pelo e-mail — core/auth.py decide pelo "@". A máscara
de CNPJ só entra quando o texto é só número: com ela sempre, um e-mail
digitado virava "ab.cd…".
"""
from __future__ import annotations

import re

import streamlit as st

from core import auth, theme
from core.config import settings
from core.db import get_session


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


def _on_change_identificador() -> None:
    texto = st.session_state.get("login_cnpj", "")
    if re.fullmatch(r"[\d./\-\s]*", texto or ""):   # só número: é CNPJ
        st.session_state["login_cnpj"] = _formatar_cnpj_progressivo(_extrair_digitos_cnpj(texto))
    else:
        st.session_state["login_cnpj"] = (texto or "").strip()


def _cabecalho() -> None:
    st.markdown(theme.logo_login_markup(), unsafe_allow_html=True)
    st.markdown(
        f'<p class="login-headline">Bem-vindo ao <span>{settings.nome_fornecedor} Oportunidades</span></p>'
        f'<p class="login-subheadline">Comparativo de preço de compra — {settings.nome_rede}</p>',
        unsafe_allow_html=True,
    )


def render() -> None:
    theme.aplicar_estilo_login()

    with st.container(key="login-center-wrap"):
        _cabecalho()
        with st.container(key="login-card"):
            if st.session_state.get("login_esqueci"):
                _esqueci_senha()
                return
            # A key continua "login_cnpj" (o rótulo mudou): o teste visual e
            # o "lembrar o que foi digitado" dependem dela.
            # Sem texto de exemplo dentro do campo (pedido de 07/10/2026).
            identificador = st.text_input(
                "E-mail ou CNPJ",
                key="login_cnpj",
                on_change=_on_change_identificador,
            )
            senha = st.text_input("Senha", type="password", key="login_senha")
            lembrar = st.checkbox("Lembrar-me", value=True, key="login_lembrar")
            entrar = st.button("Entrar", type="primary", width="stretch")
            if st.button("Esqueci minha senha", type="tertiary", key="login_ir_esqueci"):
                st.session_state["login_esqueci"] = True
                st.rerun()

            if entrar:
                if not identificador or not senha:
                    st.error("Informe o e-mail (ou CNPJ) e a senha.")
                else:
                    usuario = auth.autenticar(identificador, senha)
                    if usuario is None:
                        st.error("E-mail/CNPJ ou senha inválidos.")
                    else:
                        auth.iniciar_sessao(usuario, lembrar)
                        st.rerun()


def _esqueci_senha() -> None:
    """Manda o link pro e-mail. A mensagem é a mesma exista o e-mail ou não
    (não dá pra descobrir quem tem cadastro). A equipe RMC (CNPJ) não usa."""
    from integrations import usuarios

    st.markdown("**Esqueci minha senha**")
    st.caption("Digite o e-mail do seu acesso")
    email = st.text_input("E-mail", key="login_esqueci_email")
    if st.button("Enviar link", type="primary", width="stretch", key="login_esqueci_enviar"):
        if not usuarios.parece_email(email):
            st.error("Digite um e-mail.")
        else:
            try:
                with get_session() as session:
                    usuarios.pedir_recuperacao(session, email)
            except Exception:  # noqa: BLE001 — falha no envio: a mesma mensagem (e o link fica válido)
                pass
            st.success("Se este e-mail tiver cadastro, o link chega em alguns minutos. Confira também o spam.")
    if st.button("Voltar ao login", type="tertiary", key="login_esqueci_voltar"):
        st.session_state["login_esqueci"] = False
        st.rerun()


def render_criar_senha(token: str) -> None:
    """Tela aberta pelo link do e-mail (?token=…): o usuário cria a senha e
    já entra."""
    from integrations import usuarios

    theme.aplicar_estilo_login()
    with st.container(key="login-center-wrap"):
        _cabecalho()
        with st.container(key="login-card"):
            with get_session() as session:
                valido = usuarios.validar_token(session, token)
                nome = valido.usuario.nome_exibicao if valido else None
                login = valido.usuario.login if valido else None
            if valido is None:
                st.error("Este link não vale mais: já foi usado ou venceu. Peça um novo em \"Esqueci minha senha\" "
                         "ou a quem fez o seu cadastro.")
                if st.button("Ir para o login", type="primary", width="stretch"):
                    st.query_params.clear()
                    st.rerun()
                return
            st.markdown(f"**Olá, {nome.split(' ')[0] if nome else ''}!** Crie a sua senha.")
            st.caption(f"Seu login é o e-mail **{login}**. A senha precisa ter pelo menos "
                       f"{usuarios.SENHA_MINIMA} caracteres.")
            senha = st.text_input("Nova senha", type="password", key="criar_senha_1")
            repetida = st.text_input("Repita a senha", type="password", key="criar_senha_2")
            if st.button("Criar senha e entrar", type="primary", width="stretch"):
                if senha != repetida:
                    st.error("As duas senhas estão diferentes.")
                    return
                try:
                    with get_session() as session:
                        usuario = usuarios.definir_senha(session, token, senha)
                        session.expunge(usuario)
                except ValueError as exc:
                    st.error(str(exc))
                    return
                st.query_params.clear()
                auth.iniciar_sessao(usuario, lembrar=True)
                st.rerun()
