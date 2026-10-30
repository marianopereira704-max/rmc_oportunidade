"""Assistente de pedido: configuração, versões das bases e o cálculo da loja, com cache. O Dashboard, as
Configurações de Pedidos e a aba Dados também usam (e limpam) o que está aqui."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import copia_spaces, limite
from core.config import settings
from core.db import get_session
from pedido import calculo
from pedido.configuracao import ConfigPedido
from views.pedido.estado import _invalidar_estado

@st.cache_data(ttl=30, show_spinner=False)
def configuracao() -> ConfigPedido:
    """A vigente (Configurações de Pedidos), conferida no banco no máximo a
    cada 30 s; gravar na tela de configurações limpa este cache na hora."""
    from integrations import configuracao_pedido

    with get_session() as session:
        return configuracao_pedido.vigente(session).config


def parametros(config: ConfigPedido) -> calculo.Parametros:
    return config.parametros(settings.analise.limite_bonificacao)


@st.cache_data(ttl=30, show_spinner=False)
def _versoes() -> tuple:
    from integrations import categorias as categorias_integ
    from integrations import pedido_tela

    with get_session() as session:
        return categorias_integ.versao(session), pedido_tela.versao_genericos(session)


@st.cache_data(ttl=300, show_spinner=False)
def _versao_catalogo() -> tuple:
    """Versão das fontes da classificação de produtos (catálogo IQVIA, FEBRAFAR/CMED por EAN e as categorias
    manuais), conferida no máximo a cada 5 min: uma consulta a mais a cada clique (~140 ms até NY) seria cara.
    O nome ficou o de antes (07/10/2026), quando só o catálogo IQVIA dava a linha de compra."""
    from integrations import catalogo, classificacao
    from integrations import categorias as categorias_integ

    with get_session() as session:
        return catalogo.versao(session), classificacao.versao(session), categorias_integ.versao(session)


@st.cache_data(max_entries=2, show_spinner=False)
@limite.pesado
def _classificacao(versao: tuple, por_setor: tuple, por_cmed: tuple) -> pd.DataFrame:
    """EAN → (categoria, origem) de todos os EANs classificados (cascata de classificação, 25/10/2026: manual,
    IQVIA, CMED — pedido/classificacao.py). Lido e classificado uma vez por versão das fontes e por mapa de
    correspondência, e compartilhado por todas as sessões: ~6 s para ler as três fontes do banco em NY, 0,5 s
    para classificar (medido em 26/10/2026, 273 mil EANs). Guarda só os classificados, como categorias do pandas.
    As fontes vêm da cópia no Spaces quando há (core/copia_spaces.py): 16 s do banco em 28/10/2026."""
    from integrations import classificacao as dados
    from pedido import classificacao as cl

    def do_banco():
        with get_session() as session:
            return dados.universo(session)
    universo = copia_spaces.ler_ou_montar("classificacao_universo", versao, do_banco)
    return cl.tabela_por_ean(cl.classificar(universo, dict(por_setor), dict(por_cmed)), dict(por_cmed))


def _categorias_da_loja(todas: pd.DataFrame, config: ConfigPedido) -> dict:
    """EAN → (categoria, origem) só dos EANs desta loja (a busca por produto é por EAN). As correspondências são
    as do PADRÃO: a classificação é do produto, igual em todas as lojas (`linhas_*` não se personalizam)."""
    from pedido import classificacao as cl

    if todas.empty:
        return {}
    tabela = _classificacao(_versao_catalogo(), tuple(sorted(config.linhas_por_setor.items())),
                            tuple(sorted(config.linhas_por_cmed.items())))
    return cl.mapa_da_loja(tabela, cl.eans_da_tabela(todas))


@st.cache_data(max_entries=2, show_spinner=False)
@limite.pesado
def _base_categorias(versao: tuple) -> pd.DataFrame:
    """Da cópia no Spaces quando há (core/copia_spaces.py): 6,9 s do banco em 28/10/2026."""
    from integrations import categorias as categorias_integ

    def do_banco():
        with get_session() as session:
            return categorias_integ.tabela(session, com_origem=False)   # o cálculo só usa ean + categoria
    return copia_spaces.ler_ou_montar("base_categorias", versao, do_banco)


@st.cache_data(max_entries=2, show_spinner=False)
def _genericos(versao: tuple) -> pd.DataFrame:
    """Da cópia no Spaces quando há (core/copia_spaces.py) — a mesma que o cálculo de madrugada lê (29/10/2026)."""
    from integrations import pedido_tela

    def do_banco():
        with get_session() as session:
            return pedido_tela.genericos(session)
    return copia_spaces.ler_ou_montar("genericos", versao, do_banco)


@st.cache_data(ttl=3600, max_entries=30, show_spinner="Calculando o pedido da loja...")
@limite.pesado
def _calcular(empresa: str, loja: str, meses: int, versoes: tuple, params: calculo.Parametros,
              correcoes: tuple = (), foto: str | None = None) -> tuple[pd.DataFrame, dict] | None:
    """`versoes`, `params` e `foto` só entram na chave do cache. `foto` = o
    marcador do estoque da empresa (data + hora em que a rotina o gravou,
    conferido a cada 5 min): foto nova → recalcula na hora. Antes o cache
    expirava a cada 5 min e recalculava sem dado novo (~0,7 s, medido em
    02/10/2026); a 1 h fica só de garantia pra dias de venda que a rotina
    completa depois da foto."""
    from pedido import precalculo, telas_loja
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    # Pronto da madrugada (29/10/2026) quando a assinatura bate; senão a conta na hora, de pedido/telas_loja.py —
    # a mesma que a rotina usa.
    pronto = _pronto("assistente", empresa, loja, precalculo.assinatura_assistente(foto, versoes, meses, params,
                                                                                    correcoes))
    if pronto is not None:
        return pronto
    return telas_loja.assistente(do_ambiente(), Chaves(settings.pedido.prefixo), empresa, loja, meses, params,
                                 dict(correcoes), _base_categorias(versoes[0]), _genericos(versoes[1]))


def _pronto(tela: str, empresa: str, loja: str, assinatura: str):
    """O resultado do cálculo de madrugada (pedido/precalculo.py) desta tela, se a assinatura bate e o selo confere;
    senão None. Qualquer falha também é None: a tela calcula na hora, como antes."""
    import logging

    from pedido import precalculo
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    chave = settings.pedido.precalculo_chave
    if not chave:
        return None
    try:
        return precalculo.ler(do_ambiente(), Chaves(settings.pedido.prefixo), copia_spaces.nome_do_banco(), empresa,
                              loja, tela, assinatura, chave)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("pronto de %s não lido; calculando na hora", tela, exc_info=True)
        return None


def invalidar(publicar: bool = False) -> None:
    """Chamar depois de mudar categorias ou genéricos (aba Dados). `publicar` (29/10/2026): acabou de gravar
    Configurações ou personalização — a foto dos insumos do cálculo de madrugada vai na hora. A aba Dados chama sem
    ele a cada clique; as mudanças de lá entram pela publicação de no máximo 10 em 10 min (app.py)."""
    _versoes.clear()
    _versao_catalogo.clear()   # categoria manual muda a classificação de produtos (cascata, 25/10/2026)
    _calcular.clear()
    configuracao.clear()
    _invalidar_estado()   # personalização salva em Configurações: vale já, nesta sessão
    if publicar:
        from integrations import insumos

        insumos.publicar_em_segundo_plano(forcar=True)
