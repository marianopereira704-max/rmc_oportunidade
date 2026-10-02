"""Login (CNPJ + senha) e persistência de sessão ("lembrar-me").

Regra: um único CNPJ de login (30.208.213/0001-74), e a senha diferencia o
papel — adm123 = admin, consultor = consultor. A única diferença entre os
dois papéis é que o admin enxerga a aba extra "Dados". O consultor vê a rede
inteira — não há escopo por loja/consultor.
"""
from __future__ import annotations

import hashlib

import extra_streamlit_components as stx
import streamlit as st
from sqlalchemy import select

from core.config import settings
from core.db import get_session
from core.models import Papel, Usuario
from core.security import verificar_senha

_COOKIE_NAME = "rmc_oportunidades_remember"


def _cookie_manager() -> stx.CookieManager:
    if "_cookie_manager" not in st.session_state:
        st.session_state["_cookie_manager"] = stx.CookieManager(key="cookie_manager_rmc")
    return st.session_state["_cookie_manager"]


def _token_lembrar(cnpj: str, papel: str) -> str:
    # Segredo vem de core/config.py (settings.cookie.segredo) — nunca
    # hardcoded no código-fonte (bug corrigido em relação ao projeto anterior).
    return hashlib.sha256(f"{settings.cookie.segredo}::{cnpj}::{papel}".encode()).hexdigest()


def autenticar(cnpj: str, senha: str) -> Usuario | None:
    with get_session() as session:
        candidatos = session.execute(
            select(Usuario).where(Usuario.cnpj_login == cnpj.strip(), Usuario.ativo.is_(True))
        ).scalars().all()
        for usuario in candidatos:
            if verificar_senha(senha.strip(), usuario.senha_hash):
                session.expunge(usuario)
                return usuario
        return None


def _guardar_acesso(usuario_id: int) -> None:
    """Nível e lojas do usuário na sessão (core/acesso.py) — lidos uma vez
    no login, não a cada tela."""
    from core import acesso
    from core.models import UsuarioLoja

    with get_session() as session:
        u = session.get(Usuario, usuario_id)
        lojas = [ul.loja_id for ul in session.scalars(select(UsuarioLoja).where(UsuarioLoja.usuario_id == usuario_id))]
        st.session_state["usuario_id"] = usuario_id
        st.session_state["usuario_nivel"] = acesso.nivel_do_usuario(u.papel.value, u.nivel)
        st.session_state["usuario_lojas"] = lojas


def iniciar_sessao(usuario: Usuario, lembrar: bool) -> None:
    st.session_state["usuario_cnpj"] = usuario.cnpj_login
    st.session_state["usuario_papel"] = usuario.papel.value
    st.session_state["usuario_nome"] = usuario.nome_exibicao
    _guardar_acesso(usuario.id)

    if lembrar:
        cm = _cookie_manager()
        cm.set(
            _COOKIE_NAME,
            f"{usuario.cnpj_login}|{usuario.papel.value}|{_token_lembrar(usuario.cnpj_login, usuario.papel.value)}",
            expires_at=None,
            max_age=60 * 60 * 24 * 30,  # 30 dias
            key="set_remember_cookie",
        )


def encerrar_sessao() -> None:
    for chave in ("usuario_cnpj", "usuario_papel", "usuario_nome", "usuario_id", "usuario_nivel", "usuario_lojas",
                  "secao_ativa"):
        st.session_state.pop(chave, None)
    cm = _cookie_manager()
    cm.delete(_COOKIE_NAME, key="delete_remember_cookie")


def tentar_restaurar_sessao() -> bool:
    """Se já existe sessão ativa, ok. Senão, tenta restaurar via cookie de
    'lembrar-me'. Retorna True se o usuário está autenticado ao final."""
    if "usuario_cnpj" in st.session_state:
        return True

    cm = _cookie_manager()
    valor = cm.get(_COOKIE_NAME)
    if not valor:
        return False

    try:
        cnpj, papel, token = valor.split("|")
    except ValueError:
        return False

    if token != _token_lembrar(cnpj, papel):
        return False

    with get_session() as session:
        usuario = session.execute(
            select(Usuario).where(
                Usuario.cnpj_login == cnpj, Usuario.papel == Papel(papel), Usuario.ativo.is_(True)
            )
        ).scalar_one_or_none()
        if usuario is None:
            return False
        usuario_id = usuario.id

    st.session_state["usuario_cnpj"] = cnpj
    st.session_state["usuario_papel"] = papel
    st.session_state["usuario_nome"] = usuario.nome_exibicao
    _guardar_acesso(usuario_id)
    return True


def usuario_atual() -> dict | None:
    if "usuario_cnpj" not in st.session_state:
        return None
    from core import acesso

    return {
        "cnpj": st.session_state["usuario_cnpj"],
        "papel": st.session_state["usuario_papel"],
        "nome": st.session_state["usuario_nome"],
        "id": st.session_state.get("usuario_id"),
        # Sessão aberta antes desta versão (sem nível guardado): deduz do papel.
        "nivel": st.session_state.get("usuario_nivel")
                 or acesso.nivel_do_usuario(st.session_state["usuario_papel"], None),
        "lojas": st.session_state.get("usuario_lojas", []),
    }


def is_admin() -> bool:
    usuario = usuario_atual()
    return bool(usuario and usuario["papel"] == Papel.ADMIN.value)
