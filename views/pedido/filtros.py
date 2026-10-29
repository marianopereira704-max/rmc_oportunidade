"""Assistente de pedido: a linha de filtros acima da tabela (busca, Categoria, Fabricante, Status, Ocultar giro baixo)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pedido import calculo
from views.pedido.estado import _KEY

# Filtro Status: o valor fica em `<base>_valor` e o campo tem chave com
# versão (`<base>_<n>`). Tirar "Giro baixo" de dentro do callback do
# interruptor escrevendo na chave do campo funcionava uma vez; na escolha
# seguinte o campo voltava vazio (visto em 05/10/2026). Campo novo, com o
# valor certo como padrão, não tem esse problema.
def _status_mudou(base: str) -> None:
    """Filtro Status × "Ocultar giro baixo" (Q10/Q12 de 05/10/2026): escolher
    "Giro baixo" desliga o interruptor; tirar "Giro baixo" o religa."""
    novo = list(st.session_state.get(f"{base}_{st.session_state.get(f'{base}_v', 0)}", []))
    antes = calculo.GIRO_BAIXO in st.session_state.get(f"{base}_valor", [])
    agora = calculo.GIRO_BAIXO in novo
    if agora != antes:
        st.session_state[f"{_KEY}_giro"] = not agora
    st.session_state[f"{base}_valor"] = novo


def _giro_mudou(base: str) -> None:
    """Religar "Ocultar giro baixo" tira "Giro baixo" do filtro Status (Q13)."""
    valor = st.session_state.get(f"{base}_valor", [])
    if st.session_state.get(f"{_KEY}_giro") and calculo.GIRO_BAIXO in valor:
        st.session_state[f"{base}_valor"] = [t for t in valor if t != calculo.GIRO_BAIXO]
        st.session_state[f"{base}_v"] = st.session_state.get(f"{base}_v", 0) + 1


def _linha_filtros(loja_id: int, todas: pd.DataFrame) -> tuple[str | None, tuple, tuple, tuple]:
    """Busca, Categoria, Status, Fabricante e Ocultar giro baixo, numa linha
    acima da tabela (sem pop-up desde 01/10/2026; Status desde 05/10/2026).
    As opções vêm do que a loja tem na lista — calculadas em memória com o
    resto da loja (~4 ms na Hudson) — sem depender do "Ocultar giro baixo":
    uma opção que some da lista com a escolha feita quebraria o campo (o
    multiselect descarta calado o valor que não está nas opções)."""
    listadas = todas[todas["listar"]]
    categorias, fabricantes = calculo.opcoes_filtros(listadas)
    base = f"{_KEY}_status_{loja_id}"
    ja_escolhidos = st.session_state.get(f"{base}_valor", [])
    status = [t for t in calculo.TAGS_STATUS if t in set(calculo.opcoes_status(listadas)) | set(ja_escolhidos)]
    with st.container(key="pedido-filtros-linha", horizontal=True, vertical_alignment="center", gap="small"):
        busca = st.text_input("Buscar", placeholder="Buscar produto, EAN ou fabricante", key=f"{_KEY}_busca",
                              icon=":material/search:",
                              label_visibility="collapsed")
        escolhidas = st.multiselect("Categoria", categorias, placeholder="Categoria", key=f"{_KEY}_cat_{loja_id}",
                                    label_visibility="collapsed")
        tags = st.multiselect("Status", status, default=ja_escolhidos, placeholder="Status",
                              key=f"{base}_{st.session_state.get(f'{base}_v', 0)}",
                              on_change=_status_mudou, args=(base,), label_visibility="collapsed")
        fabs = st.multiselect("Fabricante", fabricantes, placeholder="Fabricante", key=f"{_KEY}_fab_{loja_id}",
                              label_visibility="collapsed")
        st.toggle("Ocultar giro baixo", value=True, key=f"{_KEY}_giro", on_change=_giro_mudou, args=(base,),
                  help="Ligado: os itens de giro baixo saem do pedido (não aparecem, não contam nos cards e não "
                       "vão na exportação).")
    return (busca or None), tuple(escolhidas), tuple(fabs), tuple(tags)
