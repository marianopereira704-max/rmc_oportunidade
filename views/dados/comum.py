"""Peças da aba Dados usadas por mais de uma seção."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.config import settings
from storage import filesystem as fs
from views import leitor_planilha as leitor

_LEITOR_CATEGORIAS = "categorias_leitor"
_LEITOR_CODIGOS_GPS = "codigos_gps_leitor"
_LEITOR_CATEGORIAS_PENDENCIAS = "categorias_pendencias_leitor"


def _subpasta(session, *caminho: str) -> int:
    """Arquivamento automático: garante (sem duplicar em re-uploads) a
    cadeia de subpastas e devolve o id da última — ex: Compras/GPS/2026-07."""
    node = fs.garantir_raiz(session)
    for nome in caminho:
        node = fs.obter_ou_criar_subpasta(session, node.id, nome, "sistema")
    return node.id


def _planilha_valida(validar, df) -> bool:
    """Mostra na hora, antes do botão de processar, quando a planilha escolhida
    é de outro tipo (ex: tabela Gruppy na seção da Base Genéricos). O
    processamento valida de novo — isto só evita o clique e deixa claro o erro."""
    try:
        validar(df)
    except ValueError as exc:
        st.error(str(exc))
        return False
    return True


def _mostrar_planilha_recebida(chave_leitor: str) -> None:
    erro = leitor.erro_recebido(chave_leitor)
    if erro:
        st.error(erro)
    recebida = leitor.planilha_recebida(chave_leitor)
    if recebida is not None:
        tempo = (
            f" em {recebida.segundos_leitura_navegador:.1f}s" if recebida.segundos_leitura_navegador else ""
        )
        st.caption(f"**{recebida.nome}** — {len(recebida.df):,} linhas lidas no seu navegador{tempo}.".replace(",", "."))


def _explicacao(o_que_e: str, de_onde_vem: str, como_alterar: str) -> None:
    """As três linhas-padrão de cada item da aba Dados (decisão de 27/09/2026:
    com mais itens na aba, cada um diz o que é e como mexer, sempre igual)."""
    st.markdown(
        f"**O que é:** {o_que_e}  \n**De onde vem:** {de_onde_vem}  \n**Como alterar:** {como_alterar}"
    )


def _xlsx(df: pd.DataFrame) -> bytes:
    import io

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    return buffer.getvalue()


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _categorias_contexto():
    from integrations import categorias as categorias_integ
    from pedido import categorias
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    return categorias_integ, categorias, do_ambiente(), Chaves(settings.pedido.prefixo)


def _mensagem_categorias() -> None:
    mensagem = st.session_state.pop("categorias_msg", None)
    if mensagem:
        st.success(mensagem)
