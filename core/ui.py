"""Pequenos helpers de UI reaproveitados entre as telas (paginação,
formatação de moeda/número) — pra não duplicar a mesma lógica em cada view.

Os controles de paginação (`controles_paginacao`, `resetar_pagina_se_filtro_
mudou`) dependem de `core.queries.PaginaResultado`, que existe a partir do
Bloco 3 (motor de oportunidade) — reaproveitam o mesmo padrão já validado no
projeto anterior."""
from __future__ import annotations

import re

import streamlit as st

from core.config import settings
from core.queries import PaginaResultado


def formatar_moeda(valor: float | None) -> str:
    if valor is None:
        return "R$ 0,00"
    texto = f"R$ {valor:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_cnpj(cnpj: str | None) -> str:
    """Formata CNPJ pra exibição estática (00.000.000/0000-00). Só formata
    quando os 14 dígitos batem — CNPJ malformado/incompleto volta como veio,
    em vez de quebrar a tela. Não usar em campo de input (views/login.py já
    tem sua própria máscara progressiva, pensada pra digitação)."""
    if not cnpj:
        return cnpj or ""
    digitos = re.sub(r"\D", "", cnpj)
    if len(digitos) != 14:
        return cnpj
    d = digitos
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def formatar_numero(valor: float | int | None) -> str:
    if valor is None:
        return "0"
    texto = f"{valor:,.0f}"
    return texto.replace(",", ".")


def controles_paginacao(resultado: PaginaResultado, key_prefix: str) -> int:
    """Renderiza os controles de página e devolve a página selecionada (o
    caller decide se precisa re-rodar a query com a nova página). Os
    tamanhos de página disponíveis vêm de `settings.page_size_opcoes`
    (core/config.py) — configurável, não fixo aqui."""
    col_info, col_prev, col_num, col_next, col_tam = st.columns([3, 1, 1.4, 1, 1.6])

    with col_info:
        st.markdown(
            f'<span class="rmc-muted">{formatar_numero(resultado.total_linhas)} registro(s) · '
            f'página {resultado.pagina} de {resultado.total_paginas}</span>',
            unsafe_allow_html=True,
        )

    pagina_key = f"{key_prefix}_pagina"
    if pagina_key not in st.session_state:
        st.session_state[pagina_key] = 1

    with col_prev:
        if st.button("◀", key=f"{key_prefix}_prev", disabled=resultado.pagina <= 1, use_container_width=True):
            st.session_state[pagina_key] = max(1, resultado.pagina - 1)
            st.rerun()

    with col_num:
        nova_pagina = st.number_input(
            "Página", min_value=1, max_value=max(1, resultado.total_paginas),
            value=resultado.pagina, step=1, key=f"{key_prefix}_input_pagina", label_visibility="collapsed",
        )
        if nova_pagina != resultado.pagina:
            st.session_state[pagina_key] = int(nova_pagina)
            st.rerun()

    with col_next:
        if st.button("▶", key=f"{key_prefix}_next", disabled=resultado.pagina >= resultado.total_paginas, use_container_width=True):
            st.session_state[pagina_key] = min(resultado.total_paginas, resultado.pagina + 1)
            st.rerun()

    with col_tam:
        tam_key = f"{key_prefix}_tamanho"
        if tam_key not in st.session_state:
            st.session_state[tam_key] = settings.page_size_padrao
        st.selectbox(
            "Por página", options=settings.page_size_opcoes, key=tam_key,
            label_visibility="collapsed",
        )

    return st.session_state[pagina_key]


def tamanho_pagina_atual(key_prefix: str) -> int:
    return st.session_state.get(f"{key_prefix}_tamanho", settings.page_size_padrao)


def pagina_atual(key_prefix: str) -> int:
    return st.session_state.get(f"{key_prefix}_pagina", 1)


def resetar_pagina_se_filtro_mudou(key_prefix: str, assinatura: str) -> None:
    """Se os filtros mudaram desde a última renderização, volta pra página 1
    — senão o usuário pode acabar numa página que não existe mais."""
    chave_assinatura = f"{key_prefix}_filtro_assinatura"
    if st.session_state.get(chave_assinatura) != assinatura:
        st.session_state[chave_assinatura] = assinatura
        st.session_state[f"{key_prefix}_pagina"] = 1
