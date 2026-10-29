"""Assistente de pedido: pop-ups ao abrir a loja (estoque negativo, sem classificação) e a escolha
Personalizado desta loja × Padrão."""
from __future__ import annotations

import streamlit as st

from core import dicas, ui
from core.db import get_session
from pedido.configuracao import ConfigPedido
from views import popups
from views.pedido.estado import _ABRIR_NEGATIVOS, _KEY, _invalidar_estado

def _texto_sem_classificacao(n: int, dias: int) -> str:
    return (f"**{ui.formatar_numero(n)} produto(s) desta sugestão estão sem classificação.** A inteligência de pedido "
            "não se aplica a eles: sem categoria, o sistema não sabe se são medicamento ou perfumaria, nem quantos "
            f"dias de estoque usar. Para evitar ruptura, sugerimos um pedido mínimo: estoque para **{dias} dias** de "
            "venda.  \nA classificação é feita pela administração; classificado, o produto passa a seguir a regra "
            "normal.")


@popups.popup("Produtos sem classificação", "aviso")
def _dialog_saiba_mais(n: int, dias: int) -> None:
    st.markdown(_texto_sem_classificacao(n, dias))
    _, entendi = popups.botoes("Entendi", None, chave=f"{_KEY}_saiba_mais_rodape")
    if entendi:
        st.rerun()


def _responder_aviso(usuario_id: int, loja_id: int, tipo: str, foto: str, resposta: str) -> None:
    from integrations import pedido_area

    with get_session() as session:
        pedido_area.marcar_aviso_visto(session, usuario_id, loja_id, tipo, foto, resposta)
    _invalidar_estado()


@popups.popup("Estoque negativo", "aviso", dismissible=False)
def _alerta_negativos(usuario_id: int, loja_id: int, foto: str, n: int) -> None:
    """1º pop-up ao abrir a loja. Sem X: a resposta é o que libera a lista."""
    from integrations import pedido_area

    st.markdown(f"**{ui.formatar_numero(n)} produto(s) com estoque negativo.** Deseja corrigir manualmente ou "
                "considerar todos com estoque zero?")
    st.caption("Com estoque zero, a sugestão cobre o ideal inteiro do produto. Corrigindo, a linha é recalculada "
               "com o estoque que você digitar; os que ficarem sem correção contam como zero. A pergunta volta "
               "quando chegar uma foto nova do estoque.")
    corrigir, zero = popups.botoes("Considerar todos zero", "Corrigir manualmente", chave=f"{_KEY}_alerta_neg")
    if corrigir:
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_NEGATIVO, foto, "corrigir")
        st.session_state[_ABRIR_NEGATIVOS] = True   # um pop-up não abre outro: abre no rerun
        st.rerun()
    if zero:
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_NEGATIVO, foto, "zero")
        st.rerun()


@popups.popup("Produtos sem classificação", "aviso", dismissible=False)
def _alerta_sem_classificacao(usuario_id: int, loja_id: int, foto: str, n: int, dias: int) -> None:
    """2º pop-up ao abrir a loja: só informa (classificar é da administração)."""
    from integrations import pedido_area

    st.markdown(_texto_sem_classificacao(n, dias))
    _, entendi = popups.botoes("Entendi", None, chave=f"{_KEY}_alerta_sem")
    if entendi:
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_SEM_CLASSIFICACAO, foto, "entendi")
        st.rerun()


def _resumo_config(c: ConfigPedido) -> str:
    proprias = (f", {dicas.qtd(len(c.dias_por_categoria), 'categoria')} com dias próprios"
                if c.dias_por_categoria else "")
    return (f"medicamento {c.dias_medicamento} dias, perfumaria {c.dias_perfumaria}, "
            f"ruptura próxima {c.dias_ruptura} dias{proprias}")


def _escolher_parametros(loja_id: int, personalizado: ConfigPedido, padrao: ConfigPedido) -> str | None:
    """Pílulas da loja com personalização (Q5i de 01/10/2026): nenhuma vem
    marcada — a lista só aparece depois da escolha; trocar recalcula na hora
    (as quantidades digitadas continuam: são da área de trabalho)."""
    rotulos = {"loja": "Personalizado desta loja", "padrao": "Padrão"}
    with st.container(key="pedido-parametros", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown('<span class="rmc-pedido-rotulo">Parâmetros</span>', unsafe_allow_html=True)
        return st.pills(
            "Parâmetros", list(rotulos), format_func=rotulos.get, key=f"{_KEY}_param_{loja_id}",
            label_visibility="collapsed",
            help=dicas.dica(f"Personalizado desta loja: {_resumo_config(personalizado)}",
                        f"Padrão: {_resumo_config(padrao)}"),
        )
