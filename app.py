"""Ponto de entrada do sistema (Streamlit).

Fluxo: garante o banco pronto -> aplica tema -> tenta restaurar sessão via
cookie de 'lembrar-me' -> se não tem usuário logado, mostra a tela de login;
se tem, mostra a sidebar de navegação e renderiza a seção escolhida.
"""
from __future__ import annotations

import logging

import streamlit as st

from core import acesso, auth, monitoramento, theme
from core.config import settings
from core.db import get_session, init_db
from views import (cadastro, config_pedido, dados, dashboard_compras, dashboard_estoque, dashboard_ruptura, login,
                   loja_barra, menu_lateral, notificacoes, oportunidade_loja, popups, oportunidade_produto, pedido)

st.set_page_config(
    page_title=f"{settings.nome_fornecedor} Oportunidades",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
theme.aplicar_tema()

# Link do e-mail de ativação / "esqueci minha senha" (07/10/2026): a tela de
# criar senha vem antes de tudo — mesmo com alguém já logado neste navegador.
if st.query_params.get("token"):
    login.render_criar_senha(st.query_params["token"])
    st.stop()

usuario_logado = auth.tentar_restaurar_sessao()

if not usuario_logado:
    login.render()
    st.stop()

usuario = auth.usuario_atual()


# Vínculo automático das lojas do GPS (Q4 de 08/10/2026), em segundo plano —
# uma vez por sessão; a trava de 10 min é do processo (views/loja_barra.py).
if not st.session_state.get("_vinculo_conferido"):
    st.session_state["_vinculo_conferido"] = True
    loja_barra.conferir_vinculos_em_segundo_plano()

# Fotos dos insumos do cálculo de madrugada (29/10/2026, integrations/insumos.py): em segundo plano, no máximo a cada
# 10 min por processo e só grava se algo mudou — pega o que mudou na aba Dados (categorias, genéricos) antes da noite.
from integrations import insumos as _insumos  # noqa: E402

_insumos.publicar_em_segundo_plano()


def _aviso_ip_fechado() -> None:
    st.session_state["ip_streamlit_aviso_fechado"] = True


# Fechar pelo X/ESC/clique fora também conta como "visto" (07/10/2026): antes
# só o botão gravava a marca, e o pop-up reabria a cada clique no sistema.
@popups.popup("Aviso de infraestrutura", "aviso", on_dismiss=_aviso_ip_fechado)
def _dialog_aviso_ip_streamlit_cloud() -> None:
    st.write("Os IPs do Streamlit Cloud mudaram — atualize o firewall do servidor.")
    _, entendi = popups.botoes("Entendi", None, chave="ip_streamlit_aviso")
    if entendi:
        _aviso_ip_fechado()
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
    ("pedido", "Assistente de pedido", ":material/shopping_cart:", "Gestão", pedido.render, False),
    # O Dashboard é um item PAI recolhível (MENU_PAIS) com três telas filhas
    # (core/acesso.py::SECOES_DO_PAINEL); o ícone das filhas não aparece.
    ("dashboard_estoque", "Estoque", ":material/inventory:", "Gestão", dashboard_estoque.render, False),
    ("dashboard_ruptura", "Ruptura", ":material/dashboard:", "Gestão", dashboard_ruptura.render, False),
    ("dashboard_compras", "Compras", ":material/shopping_bag:", "Gestão", dashboard_compras.render, False),
    ("dados", "Dados", ":material/folder:", "Administração", dados.render, True),  # só admin
    ("config_pedido", "Configurações de Pedidos", ":material/tune:", "Administração", config_pedido.render, True),
    # Cadastro: ADM e Consultor (core/acesso.py) — o Consultor vê a
    # Administração só com ele.
    ("cadastro", "Cadastro", ":material/group:", "Administração", cadastro.render, False),
]

# Itens recolhíveis da barra lateral: painel → (rótulo, ícone). As telas filhas
# estão em SECOES e em `acesso.SECOES_DO_PAINEL`.
MENU_PAIS = {"dashboard": ("Dashboard", ":material/dashboard:")}

# Quem vê o quê: core/acesso.py (níveis ADM, Consultor, Comprador,
# Proprietário). A última coluna de SECOES continua dizendo o que é da
# Administração, pra leitura; a regra que vale é `acesso.pode_ver`.
_nivel = usuario["nivel"]
_paineis = usuario["paineis"]   # usuário de loja: só os painéis atribuídos (Cadastro)
_ROTULO_NIVEL = {acesso.ADM: "Admin", acesso.CONSULTOR: "Consultor", acesso.COMPRADOR: "Comprador",
                 acesso.PROPRIETARIO: "Proprietário"}

if "secao_ativa" not in st.session_state:
    st.session_state["secao_ativa"] = acesso.secao_inicial(_nivel, _paineis)

# Link do rodapé dos e-mails para as lojas (29/10/2026): `?pagina=notificacoes` abre "Minhas notificações" depois
# do login. Lido uma vez: o parâmetro sai do endereço, e a navegação segue normal.
if st.query_params.get("pagina") == acesso.NOTIFICACOES:
    del st.query_params["pagina"]
    if acesso.pode_ver(_nivel, acesso.NOTIFICACOES, _paineis):
        st.session_state["secao_ativa"] = acesso.NOTIFICACOES

# Trava de acesso: nunca renderizar uma seção admin-only pra quem não é admin
# — cobre tanto alguém digitando/injetando a chave quanto o caso real: um
# admin estava na aba Dados, deu logout, um consultor logou na mesma sessão
# do navegador, e sem essa checagem a última seção ativa (herdada do usuário
# anterior) continuava sendo renderizada mesmo sem o botão dela na sidebar.
if not acesso.pode_ver(_nivel, st.session_state["secao_ativa"], _paineis):
    st.session_state["secao_ativa"] = acesso.secao_inicial(_nivel, _paineis)

with st.sidebar:
    theme.marca_sidebar(
        f"{settings.nome_fornecedor} Oportunidades", usuario["nome"], _ROTULO_NIVEL.get(_nivel, "Consultor"),
    )
    # A loja vem primeiro e vale pra todas as telas (02/10/2026).
    loja_barra.barra(usuario)

    _ultimo_grupo = None
    _pais_desenhados = set()
    for chave, rotulo, icone, grupo, _fn, _admin in SECOES:
        if not acesso.pode_ver(_nivel, chave, _paineis):
            continue
        if grupo != _ultimo_grupo:
            theme.nav_grupo_label(grupo, divisoria=True)   # o grupo LOJA vem antes de todos
            _ultimo_grupo = grupo
        ativa = st.session_state["secao_ativa"] == chave
        pai = acesso.PAINEL_DA_SECAO.get(chave)
        if pai:
            # Tela filha de um item recolhível: o pai aparece uma vez, antes da
            # primeira filha; as filhas só aparecem com o menu aberto.
            filhas_ativa = acesso.PAINEL_DA_SECAO.get(st.session_state["secao_ativa"]) == pai
            aberto = menu_lateral.esta_aberto(pai, filhas_ativa)
            if pai not in _pais_desenhados:
                menu_lateral.botao_pai(pai, MENU_PAIS[pai][0], MENU_PAIS[pai][1], aberto, filhas_ativa)
                _pais_desenhados.add(pai)
            if aberto and menu_lateral.botao_filha(chave, rotulo, ativa):
                st.session_state["secao_ativa"] = chave
                st.rerun()
            continue
        with st.container():
            st.markdown(f'<div class="nav-marker{"-ativo" if ativa else ""}"></div>', unsafe_allow_html=True)
            if st.button(rotulo, key=f"nav_{chave}", icon=icone, use_container_width=True):
                st.session_state["secao_ativa"] = chave
                st.rerun()

    theme.nav_divisoria()
    # "Minhas notificações" (29/10/2026): no pé, acima do "Sair", só para usuários de loja.
    if acesso.pode_ver(_nivel, acesso.NOTIFICACOES, _paineis):
        _ativa = st.session_state["secao_ativa"] == acesso.NOTIFICACOES
        with st.container():
            st.markdown(f'<div class="nav-marker{"-ativo" if _ativa else ""}"></div>', unsafe_allow_html=True)
            if st.button("Minhas notificações", key="nav_notificacoes", icon=":material/notifications:",
                         use_container_width=True):
                st.session_state["secao_ativa"] = acesso.NOTIFICACOES
                st.rerun()
    with st.container():
        st.markdown('<div class="nav-marker"></div>', unsafe_allow_html=True)
        if st.button("Sair", key="nav_sair", icon=":material/logout:", use_container_width=True):
            auth.encerrar_sessao()
            st.rerun()
    loja_barra.atualizacao(loja_barra.escolha(usuario))

_secoes_por_chave = {chave: fn for chave, _r, _i, _g, fn, _a in SECOES}
_secoes_por_chave[acesso.NOTIFICACOES] = notificacoes.render
if st.session_state["secao_ativa"] is None:
    # Usuário de loja sem nenhum painel (não deveria acontecer: o Cadastro
    # exige ao menos um) — melhor um aviso que uma tela que ele não pode ver.
    st.info("Nenhum painel liberado para o seu usuário. Peça a quem fez o seu cadastro para atribuir os painéis.")
else:
    _secoes_por_chave[st.session_state["secao_ativa"]]()
