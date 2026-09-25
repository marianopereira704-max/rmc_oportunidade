"""Ponto de entrada do sistema (Streamlit).

Fluxo: garante o banco pronto -> aplica tema -> tenta restaurar sessão via
cookie de 'lembrar-me' -> se não tem usuário logado, mostra a tela de login;
se tem, mostra a sidebar de navegação e renderiza a seção escolhida.
"""
from __future__ import annotations

import logging

import streamlit as st

from core import auth, monitoramento, theme
from core.config import settings
from core.db import get_session, init_db
from views import dados, dashboard, login, oportunidade_loja, oportunidade_produto, pedido

st.set_page_config(
    page_title=f"{settings.nome_fornecedor} Oportunidades",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
theme.aplicar_tema()

usuario_logado = auth.tentar_restaurar_sessao()

if not usuario_logado:
    login.render()
    st.stop()

usuario = auth.usuario_atual()


@st.dialog("Aviso de infraestrutura")
def _dialog_aviso_ip_streamlit_cloud() -> None:
    st.write("Os IPs do Streamlit Cloud mudaram — atualize o firewall do servidor.")
    if st.button("Fechar", type="primary", use_container_width=True):
        st.session_state["ip_streamlit_aviso_fechado"] = True
        st.rerun()


# Checagem só 1x por sessão do navegador (o resultado em si já é cacheado no
# banco por settings.streamlit_cloud.intervalo_verificacao_horas — ver
# core/monitoramento.py — isso aqui evita repetir mesmo o SELECT/commit a
# cada rerun da página dentro da MESMA sessão). Só pro admin: é decisão de
# infraestrutura, sem relevância pro consultor.
if auth.is_admin() and "ip_streamlit_divergente" not in st.session_state:
    # A checagem é só informativa: qualquer falha nela (rede, banco, tabela)
    # vira aviso no log e nunca derruba o login — em 24/09/2026 uma tabela
    # ausente no banco publicado travava o admin logo após entrar.
    try:
        with get_session() as session:
            st.session_state["ip_streamlit_divergente"] = monitoramento.verificar_e_avisar_se_necessario(session)
    except Exception:
        logging.getLogger(__name__).warning("Falha na checagem de IPs do Streamlit Cloud", exc_info=True)
        st.session_state["ip_streamlit_divergente"] = False

if st.session_state.get("ip_streamlit_divergente") and not st.session_state.get("ip_streamlit_aviso_fechado"):
    _dialog_aviso_ip_streamlit_cloud()

# (chave, rótulo do botão, ícone, grupo da sidebar, função de render, admin-only).
# O rótulo do botão é curto ("Por Loja") — o título completo ("Análise de
# Oportunidade · Loja") continua no cabeçalho de cada tela; o grupo é só o
# rótulo de separação visual na sidebar (mesmo padrão do mockup aprovado).
# Ícones usam a sintaxe ":material/..." do Streamlit (Material Symbols) em
# vez de emoji — emoji tem cor fixa do sistema operacional e ignoraria o
# branco/navy do botão conforme ele está inativo ou ativo; o ícone Material
# herda a cor do texto do botão normalmente.
SECOES = [
    ("oportunidade_loja", "Por Loja", ":material/storefront:", "Análise de Oportunidade", oportunidade_loja.render, False),
    ("oportunidade_produto", "Por Produto", ":material/inventory_2:", "Análise de Oportunidade", oportunidade_produto.render, False),
    ("pedido", "Pedido", ":material/shopping_cart:", "Gestão", pedido.render, False),
    ("dashboard", "Dashboard", ":material/dashboard:", "Gestão", dashboard.render, False),
    ("dados", "Dados", ":material/folder:", "Administração", dados.render, True),  # só admin
]

_apenas_admin_por_chave = {chave: apenas_admin for chave, _r, _i, _g, _fn, apenas_admin in SECOES}

if "secao_ativa" not in st.session_state:
    st.session_state["secao_ativa"] = "oportunidade_loja"

# Trava de acesso: nunca renderizar uma seção admin-only pra quem não é admin
# — cobre tanto alguém digitando/injetando a chave quanto o caso real: um
# admin estava na aba Dados, deu logout, um consultor logou na mesma sessão
# do navegador, e sem essa checagem a última seção ativa (herdada do usuário
# anterior) continuava sendo renderizada mesmo sem o botão dela na sidebar.
if _apenas_admin_por_chave.get(st.session_state["secao_ativa"]) and not auth.is_admin():
    st.session_state["secao_ativa"] = "oportunidade_loja"

with st.sidebar:
    theme.marca_sidebar(
        f"{settings.nome_fornecedor} Oportunidades", usuario["nome"], "Admin" if auth.is_admin() else "Consultor",
    )

    _ultimo_grupo = None
    for chave, rotulo, icone, grupo, _fn, apenas_admin in SECOES:
        if apenas_admin and not auth.is_admin():
            continue
        if grupo != _ultimo_grupo:
            theme.nav_grupo_label(grupo, divisoria=_ultimo_grupo is not None)
            _ultimo_grupo = grupo
        ativa = st.session_state["secao_ativa"] == chave
        with st.container():
            st.markdown(f'<div class="nav-marker{"-ativo" if ativa else ""}"></div>', unsafe_allow_html=True)
            if st.button(rotulo, key=f"nav_{chave}", icon=icone, use_container_width=True):
                st.session_state["secao_ativa"] = chave
                st.rerun()

    theme.nav_divisoria()
    with st.container():
        st.markdown('<div class="nav-marker"></div>', unsafe_allow_html=True)
        if st.button("Sair", key="nav_sair", icon=":material/logout:", use_container_width=True):
            auth.encerrar_sessao()
            st.rerun()

_secoes_por_chave = {chave: fn for chave, _r, _i, _g, fn, _a in SECOES}
_secoes_por_chave[st.session_state["secao_ativa"]]()
