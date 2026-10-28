"""Loja escolhida na barra lateral (grill-me de 02/10/2026) — vale para
todas as telas: Por Loja, Por Produto, Assistente de pedido e a
Personalização em Configurações de Pedidos.

Quem vê quais lojas (core/acesso.py):
- ADM e Consultor: todas, com "Todas as lojas" no topo (a visão da rede);
- Proprietário: as lojas dele, com "Todas" no topo quando tem mais de uma;
- Comprador: a(s) loja(s) dele, sem "Todas".
Com uma loja só, ela vem escolhida e aparece como texto fixo, sem lista.

A escolha vale enquanto durar a sessão (o login ainda é único: lembrar a
última loja misturaria a escolha de pessoas diferentes). O widget fica
sempre desenhado (na barra), então o Streamlit não descarta o estado ao
trocar de tela.

Embaixo do "Sair", discreto: quando os dados foram atualizados — o estoque
do GPS (Assistente) e o último mês de compras carregado (Análise).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import threading
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import streamlit as st

from core import acesso, dicas
from core.config import settings
from core.db import get_session

TODAS = "todas"
_CHAVE = "loja_barra"


@dataclass(frozen=True)
class Escolha:
    loja: dict | None                    # None = "Todas"
    permitidas: tuple[int, ...] | None   # lojas que o usuário pode ver; None = todas (ADM/Consultor)

    @property
    def todas(self) -> bool:
        return self.loja is None


@st.cache_data(ttl=60, show_spinner=False)
def _lojas() -> list[dict]:
    from integrations import pedido_tela

    with get_session() as session:
        return pedido_tela.lojas(session)


# Vínculo automático das lojas do GPS (Q4 de 08/10/2026): a rotina grava as
# lojas de empresas novas, e sem vínculo a loja não aparece no Assistente até
# alguém clicar em "Atualizar vínculos". Confere em SEGUNDO PLANO (ler as
# lojas de ~400 empresas leva alguns segundos — ninguém espera), no máximo a
# cada 10 min no PROCESSO (aqui, e não no app.py: o app.py roda de novo a
# cada clique e zeraria a trava), e só recalcula se houver empresa nova.
_VINCULO_INTERVALO_S = 600
_vinculo_trava = threading.Lock()
_vinculo_ultimo = [0.0]


def _conferir_vinculos() -> None:
    from integrations import gps_vinculo
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    try:
        with get_session() as session:
            r = gps_vinculo.recalcular_se_houver_novas(session, do_ambiente(), Chaves(settings.pedido.prefixo))
        if r is not None:
            limpar_cache()
            logging.getLogger(__name__).info("Vínculos GPS recalculados: %s automáticos, %s a confirmar",
                                             r.automaticos, r.a_confirmar)
    except Exception:  # noqa: BLE001 — automático e em segundo plano: nunca derruba a tela
        logging.getLogger(__name__).warning("Falha no vínculo automático das lojas GPS", exc_info=True)


def conferir_vinculos_em_segundo_plano() -> None:
    with _vinculo_trava:
        if time.monotonic() - _vinculo_ultimo[0] <= _VINCULO_INTERVALO_S:
            return
        _vinculo_ultimo[0] = time.monotonic()
    threading.Thread(target=_conferir_vinculos, daemon=True, name="vinculo-gps").start()


def limpar_cache() -> None:
    """Depois de um vínculo novo (app.py): a loja passa a ter `empresa_gps`."""
    _lojas.clear()


def lojas_do_usuario(usuario: dict) -> list[dict]:
    return acesso.filtrar_lojas(_lojas(), usuario["nivel"], usuario["lojas"])


def rotulo(l: dict) -> str:
    """"60 · HUDSON VENZEL PÊGO" (02/10/2026 — sem o "cód.", pedido do
    Mariano): o código vem primeiro — mesmo cortado pela largura da barra, ele
    já identifica a loja."""
    nome = (l.get("razao_social") or "").strip()
    return f"{l['legacy_id']} · {nome}" if l.get("legacy_id") else nome


def _dica(l: dict) -> str:
    local = "/".join(x for x in (l.get("cidade"), l.get("uf")) if x)
    razao = (l.get("razao_social") or "").strip()
    return dicas.dica(razao, local + (f"\nCódigo: {l['legacy_id']}" if l.get("legacy_id") else ""))


def escolha(usuario: dict) -> Escolha:
    """A escolha atual (lê o estado do widget da barra)."""
    lojas = lojas_do_usuario(usuario)
    por_id = {l["id"]: l for l in lojas}
    permitidas = None if acesso.ve_todas_as_lojas(usuario["nivel"]) else tuple(sorted(por_id))
    todas = acesso.opcao_todas(usuario["nivel"], len(lojas))
    if len(lojas) == 1 and todas is None:
        return Escolha(lojas[0], permitidas)
    valor = st.session_state.get(_CHAVE, TODAS if todas else None)
    return Escolha(por_id.get(valor), permitidas)


def barra(usuario: dict) -> None:
    """O grupo LOJA no topo da barra lateral."""
    lojas = lojas_do_usuario(usuario)
    todas = acesso.opcao_todas(usuario["nivel"], len(lojas))
    por_id = {l["id"]: l for l in lojas}
    atual = st.session_state.get(_CHAVE, TODAS if todas else None)
    # Sem o "?" com o nome completo (tirado em 02/10/2026: "dá pra ler bem a
    # loja" com código + razão social).
    st.markdown('<div class="rmc-nav-grupo-label">LOJA</div>', unsafe_allow_html=True)
    if not lojas:
        st.caption("Nenhuma loja liberada para o seu usuário.")
        return
    if len(lojas) == 1 and todas is None:
        # Uma loja só: texto fixo (uma lista com uma opção é clique à toa).
        st.markdown(f'<div class="rmc-loja-fixa dica-abaixo" data-dica="{dicas.atributo(_dica(lojas[0]))}">'
                    f'{html.escape(rotulo(lojas[0]))}</div>', unsafe_allow_html=True)
        return
    opcoes = ([TODAS] if todas else []) + list(por_id)
    if atual not in opcoes:   # loja que saiu da lista (ex.: outro usuário na mesma sessão)
        st.session_state.pop(_CHAVE, None)
        atual = TODAS if todas else None
    st.selectbox(
        "Loja", opcoes, index=opcoes.index(atual) if atual in opcoes else None,
        format_func=lambda v: todas if v == TODAS else rotulo(por_id[v]),
        placeholder="Selecione a loja", key=_CHAVE, label_visibility="collapsed",
    )
    if todas and atual in por_id:
        # Voltar pra "Todas" pela lista era difícil: ~400 lojas, a lista abre
        # perto da loja atual e a busca aproximada do Streamlit põe "PRODUTOS
        # FARMACEUTICOS" na frente de "Todas" (visto em 02/10/2026).
        st.button(f"Ver {todas.lower()}", key=f"{_CHAVE}_todas", type="tertiary", icon=":material/close:",
                  on_click=lambda: st.session_state.update({_CHAVE: TODAS}))


# ---------------------------------------------------------------------------
# Atualização dos dados (texto discreto no pé da barra)
# ---------------------------------------------------------------------------

def _hora_local(texto: str) -> dt.datetime | None:
    """`gerado_em` da rotina: hora do runner do GitHub Actions (UTC)."""
    try:
        momento = dt.datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=dt.timezone.utc)
    return momento.astimezone(ZoneInfo(settings.pedido.fuso))


@st.cache_data(ttl=300, show_spinner=False)
def marcador_estoque(empresa: str | None) -> dict | None:
    """O marcador da foto de estoque da empresa ({"data", "gerado_em"}),
    conferido no Spaces no máximo a cada 5 min. Usado aqui (texto de
    atualização) e no Assistente (chave do cálculo da loja)."""
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    if not empresa:
        return None
    try:
        armaz, chaves = do_ambiente(), Chaves(settings.pedido.prefixo)
        if not armaz.existe(chaves.marcador_estoque(empresa)):
            return None
        return armaz.ler_json(chaves.marcador_estoque(empresa))
    except Exception:  # noqa: BLE001 — sem Spaces/arquivo: sem marcador
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _gps(empresa: str | None) -> str | None:
    """Da empresa da loja: quando o estoque foi baixado (marcador da rotina,
    com `gerado_em` desde 29/09/2026; antes só a data). Sem loja ("Todas"):
    a execução mais recente da rotina."""
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    try:
        armaz, chaves = do_ambiente(), Chaves(settings.pedido.prefixo)
        if empresa:
            marcador = marcador_estoque(empresa)
            if not marcador:
                return None
            momento = _hora_local(marcador.get("gerado_em"))
            if momento:
                return f"{momento:%d/%m/%y} às {momento:%H:%M}"
            data = marcador.get("data")
            return f"{data[8:10]}/{data[5:7]}/{data[2:4]}" if data else None
        prefixo = f"{chaves.prefixo}/controle/execucoes/"
        nomes = [c[len(prefixo):] for c in armaz.listar(prefixo) if c.endswith(".json")]
        if not nomes:
            return None
        # {data}/{parte}-{HHMMSS}.json, hora do runner (UTC)
        dia, arquivo = max((n.split("/")[0], n.rsplit("-", 1)[-1][:6]) for n in nomes if "/" in n)
        momento = _hora_local(f"{dia}T{arquivo[:2]}:{arquivo[2:4]}:{arquivo[4:6]}")
        return f"{momento:%d/%m/%y} às {momento:%H:%M}" if momento else None
    except Exception:  # noqa: BLE001 — sem Spaces/arquivo: a linha some
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _analise() -> str | None:
    """Último mês fechado com compras da rede (a fonte da Análise desde
    05/10/2026 — core/analise_api.py); antes era o último mês da planilha."""
    from views.analise_comum import meses_api, rotulo_meses

    try:
        meses = meses_api(1)
    except Exception:  # noqa: BLE001 — Spaces fora do ar: a linha some
        return None
    return f"até {rotulo_meses(meses)}" if meses else None


def atualizacao(e: Escolha) -> None:
    """Texto discreto no pé da barra (Q5 de 02/10/2026)."""
    gps = _gps(e.loja.get("empresa_gps") if e.loja else None)
    analise = _analise()
    linhas = ([f"Pedido (GPS): {gps}"] if gps else []) + ([f"Análise (compras): {analise}"] if analise else [])
    if linhas:
        st.markdown('<div class="rmc-atualizacao">Atualização dos dados<br>'
                    + "<br>".join(html.escape(l) for l in linhas) + "</div>", unsafe_allow_html=True)
