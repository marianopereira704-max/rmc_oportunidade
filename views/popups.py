"""Padrão dos pop-ups do sistema (grill de 07/10/2026) — como foi feito com
as tabelas: todo pop-up passa por aqui e o CSS fica num bloco só
(core/theme.py, "POP-UPS").

Decisões:
- Q4 — quatro TIPOS, cada um com uma largura (a do tipo ou a área do
  conteúdo menos 32 px, a menor; no celular, a tela menos 32 px):
    aviso   440 px  confirmação, alerta, resultado
    curto   560 px  formulário curto (Atribuir Painéis, Listas, Exportar…)
    longo   760 px  formulário longo (Novo usuário, Mapeamento da Gruppy…)
    detalhe 1000 px tabela/gráfico (Detalhes da loja, do produto)
  O `st.dialog` do 1.62 só tem "small"/"medium"/"large"; a largura do tipo
  vem do CSS, pelo marcador `st-key-popup-<tipo>` que a fábrica põe dentro.
- Q2/Q3 — centralizado na vertical e na área do conteúdo (à direita da
  barra lateral, se ela estiver aberta).
- Q5 — altura máxima de 85% da tela: o conteúdo rola e o rodapé de
  botões (`botoes`) fica sempre visível.
- Q6 — botões lado a lado ocupando a largura: secundário à esquerda,
  principal à direita, sempre com verbo; destrutivo em vermelho.
- Q14 — formulário longo NÃO fecha por X/ESC/clique fora (perderia o que
  foi digitado): só pelo botão de cancelar do rodapé.
- Q15 — `notificar` é o padrão das notificações (as que já existem não
  foram mexidas).

Uso:

    @popups.popup("Excluir usuário", "aviso")
    def _dialog_excluir(r): ...
        cancelar, excluir = popups.botoes("Excluir usuário", chave="excluir", destrutivo=True)
"""
from __future__ import annotations

import functools
import html

import streamlit as st

from core.theme import POPUP_LARGURAS as TIPOS  # a tabela é uma só: o CSS usa a mesma
# Largura nativa mais próxima — só o ponto de partida; quem manda é o CSS.
_NATIVA = {"aviso": "small", "curto": "medium", "longo": "medium", "detalhe": "large"}
_ICONES = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}


def popup(titulo: str, tipo: str, *, dismissible: bool | None = None, on_dismiss="ignore"):
    """Fábrica de pop-up: `@popup("Título", "aviso")` no lugar de
    `@st.dialog(...)`. `dismissible` padrão: todos menos o formulário longo."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo de pop-up desconhecido: {tipo} (use {', '.join(TIPOS)})")
    fecha = (tipo != "longo") if dismissible is None else dismissible

    def decorar(funcao):
        @functools.wraps(funcao)
        def conteudo(*args, **kwargs):
            # O marcador do tipo: é por ele que o CSS acha este pop-up.
            with st.container(key=f"popup-{tipo}"):
                return funcao(*args, **kwargs)

        opcoes = {"width": _NATIVA[tipo], "dismissible": fecha}
        if fecha:
            opcoes["on_dismiss"] = on_dismiss
        return st.dialog(titulo, **opcoes)(conteudo)

    return decorar


def botoes(principal: str | None, secundario: str | None = "Cancelar", *, chave: str, destrutivo: bool = False,
           desabilitado: bool = False) -> tuple[bool, bool]:
    """Rodapé do pop-up: (clicou no secundário, clicou no principal).
    Sem `secundario`: um botão só, ocupando a largura (ex.: "Concluir").
    O rodapé fica fixo embaixo quando o conteúdo rola (CSS)."""
    marca = "popup-rodape-destrutivo" if destrutivo else "popup-rodape"
    with st.container(key=f"{marca}-{chave}"):
        if principal and secundario:
            c1, c2 = st.columns(2)
            sec = c1.button(secundario, key=f"{chave}_secundario", use_container_width=True)
            pri = c2.button(principal, type="primary", key=f"{chave}_principal", use_container_width=True,
                            disabled=desabilitado)
            return sec, pri
        if principal:
            return False, st.button(principal, type="primary", key=f"{chave}_principal", use_container_width=True,
                                    disabled=desabilitado)
        return st.button(secundario, key=f"{chave}_secundario", use_container_width=True), False


def secao(texto: str) -> None:
    """Rótulo de seção dentro do pop-up (Q12): "IDENTIFICAÇÃO ———"."""
    st.markdown(f'<div class="rmc-popup-secao">{html.escape(texto)}</div>', unsafe_allow_html=True)


def resultado(tom: str, icone_svg: str, titulo: str, texto_html: str) -> None:
    """Cabeçalho de resultado (ícone num círculo, título, uma frase) — o
    modelo do "Convite enviado". `tom`: ok (verde), alerta (âmbar), link (azul).
    `texto_html` já escapado por quem chama."""
    st.markdown(
        f'<div class="rmc-convite"><div class="icone {tom}"><svg viewBox="0 0 24 24" aria-hidden="true">'
        f'{icone_svg}</svg></div><div class="titulo">{html.escape(titulo)}</div>'
        f'<div class="texto">{texto_html}</div></div>',
        unsafe_allow_html=True,
    )


def notificar(texto: str, tipo: str = "success") -> None:
    """Notificação padrão (Q15): 5 s no canto, ícone por tipo, frase curta."""
    st.toast(texto, icon=_ICONES.get(tipo, "ℹ️"), duration=5)
