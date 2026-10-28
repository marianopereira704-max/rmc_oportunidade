"""Login e persistência de sessão ("lembrar-me").

Dois jeitos de entrar, no mesmo campo "E-mail ou CNPJ" (07/10/2026):
- EQUIPE RMC: o CNPJ da rede (30.208.213/0001-74) + senha, e a senha
  diferencia o papel (ADM ou Consultor). Veem a rede inteira.
- LOJAS: e-mail + senha (integrations/usuarios.py). Comprador/Proprietário,
  só das lojas dele (principal + adicionais).

Cookie "lembrar-me": `u|<id>|<assinatura>` — a assinatura leva o hash da
senha, então trocar a senha derruba os "lembrar-me" antigos. O formato
anterior (`cnpj|papel|assinatura`) continua aceito.
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


def _token_usuario(usuario_id: int, senha_hash: str) -> str:
    return hashlib.sha256(f"{settings.cookie.segredo}::u::{usuario_id}::{senha_hash}".encode()).hexdigest()


def autenticar(identificador: str, senha: str) -> Usuario | None:
    """E-mail (loja) ou CNPJ (equipe RMC) — decide pelo "@"."""
    from integrations import usuarios

    with get_session() as session:
        if usuarios.parece_email(identificador):
            u = usuarios.autenticar_email(session, identificador, senha.strip())
            if u is not None:
                session.expunge(u)
            return u
        candidatos = session.execute(
            select(Usuario).where(Usuario.cnpj_login == identificador.strip(), Usuario.ativo.is_(True))
        ).scalars().all()
        for usuario in candidatos:
            if verificar_senha(senha.strip(), usuario.senha_hash):
                session.expunge(usuario)
                return usuario
        return None


def _guardar_acesso(usuario_id: int) -> None:
    """Nível, lojas (principal + adicionais), painéis e linhas de compra na
    sessão (core/acesso.py) — lidos uma vez no login, não a cada tela."""
    from core import acesso
    from core.models import UsuarioLoja
    from integrations import usuarios

    with get_session() as session:
        u = session.get(Usuario, usuario_id)
        lojas = [ul.loja_id for ul in session.scalars(select(UsuarioLoja).where(UsuarioLoja.usuario_id == usuario_id))]
        if u.loja_principal_id is not None and u.loja_principal_id not in lojas:
            lojas.insert(0, u.loja_principal_id)
        st.session_state["usuario_id"] = usuario_id
        st.session_state["usuario_nivel"] = acesso.nivel_do_usuario(u.papel.value, u.nivel)
        st.session_state["usuario_lojas"] = lojas
        st.session_state["usuario_login"] = u.login
        st.session_state["usuario_paineis"] = usuarios.codigos(u.paineis)
        st.session_state["usuario_linhas"] = usuarios.codigos(u.linhas_compra)


def iniciar_sessao(usuario: Usuario, lembrar: bool) -> None:
    # "usuario_cnpj" é a marca de "tem alguém logado" (vazio pra loja).
    st.session_state["usuario_cnpj"] = usuario.cnpj_login or ""
    st.session_state["usuario_papel"] = usuario.papel.value
    st.session_state["usuario_nome"] = usuario.nome_exibicao
    _guardar_acesso(usuario.id)

    if lembrar:
        cm = _cookie_manager()
        cm.set(
            _COOKIE_NAME,
            f"u|{usuario.id}|{_token_usuario(usuario.id, usuario.senha_hash)}",
            expires_at=None,
            max_age=60 * 60 * 24 * 30,  # 30 dias
            key="set_remember_cookie",
        )


def encerrar_sessao() -> None:
    for chave in ("usuario_cnpj", "usuario_papel", "usuario_nome", "usuario_id", "usuario_nivel", "usuario_lojas",
                  "usuario_login", "usuario_paineis", "usuario_linhas", "secao_ativa"):
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
        a, b, token = valor.split("|")
    except ValueError:
        return False

    with get_session() as session:
        if a == "u":
            # Formato novo (07/10/2026): id + assinatura com o hash da senha.
            usuario = session.get(Usuario, int(b)) if b.isdigit() else None
            if (usuario is None or not usuario.ativo or not usuario.senha_hash
                    or token != _token_usuario(usuario.id, usuario.senha_hash)):
                return False
        else:
            cnpj, papel = a, b
            if token != _token_lembrar(cnpj, papel):
                return False
            usuario = session.execute(
                select(Usuario).where(
                    Usuario.cnpj_login == cnpj, Usuario.papel == Papel(papel), Usuario.ativo.is_(True)
                )
            ).scalar_one_or_none()
            if usuario is None:
                return False
        usuario_id, nome, cnpj_login, papel_valor = usuario.id, usuario.nome_exibicao, usuario.cnpj_login, usuario.papel.value

    st.session_state["usuario_cnpj"] = cnpj_login or ""
    st.session_state["usuario_papel"] = papel_valor
    st.session_state["usuario_nome"] = nome
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
        "login": st.session_state.get("usuario_login"),
        "paineis": st.session_state.get("usuario_paineis", []),
        "linhas": st.session_state.get("usuario_linhas", []),
    }


def is_admin() -> bool:
    usuario = usuario_atual()
    return bool(usuario and usuario["papel"] == Papel.ADMIN.value)
