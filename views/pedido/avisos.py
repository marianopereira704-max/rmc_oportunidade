"""Assistente de pedido: pop-ups ao abrir a loja (estoque negativo, sem classificação) e o rótulo de quais regras
valem (desta loja ou padrão)."""
from __future__ import annotations

import streamlit as st

from core import dicas
from core.db import get_session
from pedido.configuracao import ConfigPedido
from views import popups
from views.pedido.estado import _ABRIR_NEGATIVOS, _KEY, _invalidar_estado

def _texto_sem_classificacao(n: int, dias: int) -> str:
    # Textos em linguagem de dono de farmácia (etapa 4 do plano de 29/10/2026, aprovados pelo Mariano).
    return (f"**{dicas.qtd(n, 'produto')} ainda sem categoria.** Para não faltar, a inteligência de pedido sugere "
            f"comprar o suficiente para **{dicas.qtd(dias, 'dia')} de venda**.")


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

    st.markdown(f"**{dicas.qtd(n, 'produto')} com estoque negativo no seu sistema.** Normalmente é venda lançada "
                "antes da entrada da nota.")
    st.caption("Você pode digitar o estoque certo de cada um ou considerar todos como zero. Considerando como zero, "
               "a sugestão pede o estoque completo. A pergunta volta quando o estoque for atualizado.")
    corrigir, zero = popups.botoes("Considerar como zero", "Digitar o estoque certo", chave=f"{_KEY}_alerta_neg")
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


def _regras_usadas(personalizado: ConfigPedido | None, padrao: ConfigPedido) -> None:
    """Qual regra vale, em texto discreto no topo (29/10/2026). Antes, a loja com personalização mostrava as pílulas
    "Personalizado desta loja" / "Padrão" (Q5i de 01/10/2026), nenhuma marcada, e a lista só aparecia depois da
    escolha — mas, se a loja tem regras próprias, são elas que valem: a escolha era um passo a mais sem resposta
    certa para o dono da farmácia."""
    if personalizado is not None:
        rotulo, frase = "Regras desta loja", f"Esta loja tem regras próprias: {_resumo_config(personalizado)}"
    else:
        rotulo, frase = "Regras padrão", f"Esta loja segue as regras padrão: {_resumo_config(padrao)}"
    texto = dicas.dica(frase, "Quem muda é a administração, em Configurações de Pedidos.")
    with st.container(key="pedido-parametros"):
        st.markdown(f'<span class="rmc-pedido-rotulo dica-abaixo" data-dica="{dicas.atributo(texto)}">{rotulo}</span>',
                    unsafe_allow_html=True)
