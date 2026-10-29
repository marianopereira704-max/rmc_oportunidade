"""Aba 'Dados' — só para o admin.

Quatro sub-abas, organizadas pelo que o admin está fazendo (Q35, 27/09/2026), um
arquivo por seção desde 28/10/2026 (o dados.py único tinha 1.496 linhas):
1. Pendências — fila de EAN (`fila_ean.py`), vínculo de lojas GPS
   (`vinculo_gps.py`) e Sem Classificação (`sem_classificacao.py`); nunca
   descartadas silenciosamente.
2. Importar (`importar.py`) — planilhas da Gruppy (preços), Base Genéricos e
   categorias. As compras vêm da API do GPS pela rotina diária desde
   05/10/2026 — o envio da planilha do GPS e a fila de CNPJ órfão saíram.
3. Rotinas (`rotinas.py`) — sincronização de lojas, rotina do GPS, listas
   FEBRAFAR/CMED.
4. Explorador de Arquivos (`explorador.py`) estilo Windows sobre o
   DigitalOcean Spaces (criar pasta, mover, renomear, inativar/reativar —
   nunca excluir de verdade).
Peças usadas por mais de uma seção: `comum.py`.
"""
from __future__ import annotations


import streamlit as st
from sqlalchemy import func, select

from core import auth, theme
from core.db import get_session
from core.models import FilaResolucaoEAN, StatusFila
from integrations import gruppy as gruppy_integ
from integrations import sistema_interno as loja_integ
from integrations.base import StatusIntegracao
from storage import filesystem as fs
from views import analise_comum
from views.pedido import dados_loja as dados_pedido
from views.dados.comum import _milhar
from views.dados.explorador import _explorador
from views.dados.fila_ean import _fila_ean
from views.dados.importar import _categorias_importar, _importar_base_genericos, _importar_gruppy
from views.dados.rotinas import (
    _fila_loja_automatica,
    _fontes_categorias,
    _rotina_gps,
    _sincronizar_lojas,
    _sincronizar_lojas_automatico,
)
from views.dados.sem_classificacao import _sem_classificacao, _sem_classificacao_calculado, _versao_categorias
from views.dados.vinculo_gps import _vinculo_lojas_gps

_STATUS_BADGE = {
    StatusIntegracao.DISPONIVEL: ("sucesso", "API disponível"),
    StatusIntegracao.MANUAL: ("sugestao", "Manual (planilha)"),
    StatusIntegracao.INDISPONIVEL: ("sugestao", "Indisponível"),
}


def _painel_integracoes() -> None:
    st.markdown("#### Status das integrações")
    adapters = [loja_integ.SistemaInternoLojasAdapter(), gruppy_integ.GruppyAdapter()]
    cartoes = [(a.nome, *_STATUS_BADGE[a.status()]) for a in adapters]
    # Compras desde 05/10/2026: API do GPS, pela rotina da madrugada (Rotinas).
    cartoes.append(("Compras das Lojas (GPS)", "sucesso", "API (rotina diária)"))
    cols = st.columns(3)
    for col, (nome, tipo, texto) in zip(cols, cartoes):
        with col:
            st.markdown(
                f"""<div class="rmc-kpi"><div class="label">{nome}</div>
                {theme.badge(texto, tipo)}</div>""",
                unsafe_allow_html=True,
            )


@st.cache_data(ttl=60, show_spinner=False)
def _contadores_banco() -> dict:
    from core.models import SituacaoVinculoGps, VinculoLojaGps

    with get_session() as session:
        return {
            "ean": session.scalar(select(func.count()).select_from(FilaResolucaoEAN)
                                  .where(FilaResolucaoEAN.status == StatusFila.PENDENTE)) or 0,
            "vinculo": session.scalar(select(func.count()).select_from(VinculoLojaGps).where(
                VinculoLojaGps.situacao.in_([SituacaoVinculoGps.CONFIRMAR, SituacaoVinculoGps.SEM_CANDIDATO]))) or 0,
        }


_PENDENCIAS = {
    "ean": ("Fila de EAN", _fila_ean),
    "vinculo": ("Vínculo de lojas GPS", _vinculo_lojas_gps),
    "sem_classificacao": ("Sem Classificação", _sem_classificacao),
}


def _pendencias() -> None:
    contadores = dict(_contadores_banco())
    try:
        relatorio, _total = _sem_classificacao_calculado(_versao_categorias())
        contadores["sem_classificacao"] = int((relatorio["LOJAS COM ESTOQUE"] > 0).sum()) if not relatorio.empty else 0
    except Exception:  # noqa: BLE001 — sem Spaces/catálogo: o contador fica em branco
        contadores["sem_classificacao"] = None

    def rotulo(chave: str) -> str:
        n = contadores.get(chave)
        return _PENDENCIAS[chave][0] + ("" if n is None else f" ({_milhar(n)})")

    escolhida = st.segmented_control(
        "O que resolver", options=list(_PENDENCIAS), format_func=rotulo, default="ean",
        key="dados_pendencia", label_visibility="collapsed",
    ) or "ean"
    st.divider()
    _PENDENCIAS[escolhida][1]()


def _importar() -> None:
    usuario = auth.usuario_atual()
    _importar_base_genericos(usuario)
    st.divider()
    _importar_gruppy(usuario)
    st.divider()
    _categorias_importar()


def _rotinas_aba() -> None:
    _sincronizar_lojas()
    st.divider()
    _rotina_gps()
    st.divider()
    _fontes_categorias()


def render() -> None:
    with theme.tela("dados"):
        _render()


def _render() -> None:
    theme.cabecalho("Dados", "Pendências, importação de planilhas, rotinas automáticas e arquivos.")
    # Toda ação que muda os dados da análise (envios, EAN/CNPJ resolvido,
    # exclusões) acontece nesta aba: descartar o cálculo guardado aqui faz
    # as telas de análise sempre refletirem o que acabou de mudar.
    analise_comum.invalidar()
    dados_pedido.invalidar()  # categorias/genéricos mudam aqui também
    _contadores_banco.clear()
    _sincronizar_lojas_automatico()
    _fila_loja_automatica()
    _painel_integracoes()
    st.markdown(f'<p class="rmc-muted">Armazenamento: {fs.modo_storage()}</p>', unsafe_allow_html=True)

    # Abas preguiçosas: só a aba aberta é calculada. Sem isso, qualquer clique
    # (inclusive escolher uma planilha) redesenhava todas — e as filas, com
    # centenas de itens, custavam segundos por clique. Organização de
    # 27/09/2026 (Q35): pelo que o admin está fazendo.
    aba_pendencias, aba_importar, aba_rotinas, aba_explorador = st.tabs(
        ["Pendências", "Importar", "Rotinas", "Explorador de Arquivos"], key="dados_abas", on_change="rerun",
    )
    if aba_pendencias.open:
        with aba_pendencias:
            _pendencias()
    if aba_importar.open:
        with aba_importar:
            _importar()
    if aba_rotinas.open:
        with aba_rotinas:
            _rotinas_aba()
    if aba_explorador.open:
        with aba_explorador:
            _explorador()
