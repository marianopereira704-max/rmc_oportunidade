"""Peças que as páginas do Dashboard dividem: a loja da barra lateral, o estado da loja guardado na sessão e a
carga de dados de cada página — Estoque (modelo de madrugada, 22/10/2026) e Ruptura (cálculo do Assistente +
histórico do estoque + classificação, 27/10/2026).

A configuração é a DA LOJA, aplicada automaticamente: personalização gravada, ela; senão, o padrão (decisão de
21/10/2026 — não há pílula Padrão/Personalizado no Dashboard). O filtro de cada página é dela.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core import limite
from core.db import get_session
from pedido import configuracao as cfg
from pedido.configuracao import ConfigPedido
from views import loja_barra
from views.pedido import dados_loja as dados_pedido

_ESTADO = "dashboard_estado"
_ESTADO_SEGUNDOS = 30


def _ler_estado(loja_id: int) -> tuple[dict, dict]:
    """Correções de estoque negativo e personalização da loja: 2 consultas
    pequenas, guardadas na sessão por 30 s (cada consulta custa ~140 ms até o
    banco em NY, e o clique num filtro roda o script inteiro)."""
    from integrations import configuracao_pedido, pedido_registros

    atual = st.session_state.get(_ESTADO)
    if atual and atual["loja"] == loja_id and time.monotonic() - atual["em"] < _ESTADO_SEGUNDOS:
        return atual["correcoes"], atual["personalizacao"]
    with get_session() as session:
        correcoes = pedido_registros.correcoes(session, loja_id)
        personalizacao = configuracao_pedido.da_loja(session, loja_id)
    st.session_state[_ESTADO] = {"loja": loja_id, "em": time.monotonic(), "correcoes": correcoes,
                                 "personalizacao": personalizacao}
    return correcoes, personalizacao


def _loja_ou_aviso(usuario: dict) -> dict | str:
    """A loja da barra lateral, ou a frase que a tela mostra no lugar. UMA loja por vez: "Todas as lojas" não
    calcula nada (cada loja ocupa memória, e as 594 da rede não cabem)."""
    escolha = loja_barra.escolha(usuario)
    if not loja_barra.lojas_do_usuario(usuario):
        return "Nenhuma loja ligada ao seu usuário. Peça ao administrador para liberar a sua loja."
    loja = escolha.loja
    if loja is None:
        return "Escolha uma loja na barra lateral: o Dashboard trabalha com uma loja por vez."
    if not loja.get("empresa_gps"):
        return ("Loja sem vínculo com o sistema de vendas: ainda não se sabe qual é a loja de lá. "
                "O administrador liga as duas em Dados → Pendências → Vínculo de lojas GPS.")
    return loja


# ---------------------------------------------------------------------------
# Página Estoque (22/10/2026): modelo de madrugada + parâmetros da loja
# ---------------------------------------------------------------------------

# O filtro da página Estoque é DELA (decisão de 22/10/2026): Ruptura e Compras têm os seus.
EST_CATEGORIAS = "dashboard_estoque_filtro_categorias"
EST_LINHAS = "dashboard_estoque_filtro_linhas"
_EST_LOJA = "dashboard_estoque_filtro_loja"
_PERSONALIZACAO = "dashboard_estoque_personalizacao"


@dataclass
class DadosEstoque:
    """A loja da página Estoque, já com a configuração dela aplicada."""
    loja: dict
    df: pd.DataFrame            # `core.dashboard_estoque.preparar`: um SKU por linha, com as colunas calculadas
    config: ConfigPedido
    meta: dict                  # do modelo: data da foto, janela, SKUs negativos


def filtros_estoque(loja_id: int) -> tuple[list[str], list[str]]:
    """(categorias, linhas) escolhidas na página Estoque. O filtro é DA LOJA: trocar de loja na barra lateral
    limpa tudo (uma categoria selecionada numa loja não existe na outra)."""
    if st.session_state.get(_EST_LOJA) != loja_id:
        st.session_state[_EST_LOJA] = loja_id
        st.session_state[EST_CATEGORIAS] = []
        st.session_state[EST_LINHAS] = []
    return list(st.session_state.get(EST_CATEGORIAS, [])), list(st.session_state.get(EST_LINHAS, []))


def _ler_personalizacao(loja_id: int) -> dict:
    """A personalização da loja (1 consulta, guardada na sessão por 30 s — cada consulta custa ~140 ms até o
    banco em NY e o clique num filtro roda o script inteiro). As correções de estoque negativo do Assistente
    NÃO entram na página Estoque."""
    from integrations import configuracao_pedido

    atual = st.session_state.get(_PERSONALIZACAO)
    if atual and atual["loja"] == loja_id and time.monotonic() - atual["em"] < _ESTADO_SEGUNDOS:
        return atual["valores"]
    with get_session() as session:
        valores = configuracao_pedido.da_loja(session, loja_id)
    st.session_state[_PERSONALIZACAO] = {"loja": loja_id, "em": time.monotonic(), "valores": valores}
    return valores


@st.cache_data(ttl=3600, max_entries=8, show_spinner="Preparando o estoque da loja...")
@limite.pesado
def _estoque_preparado(empresa: str, loja_gps: str, meses: int, foto: str, versao_categorias: tuple,
                       versao_catalogo: tuple, config: ConfigPedido):
    """O quadro da página (modelo de madrugada + categoria + linha + parâmetros da loja), guardado por loja.
    `foto`, as versões e a `config` só entram na chave do cache: foto nova, categorias novas ou parâmetro
    novo → recalcula. A 1ª abertura de uma loja leva alguns segundos (monta o modelo se a rotina ainda não
    montou); depois, cada clique só reagrega. None = a rotina ainda não tirou foto de estoque.
    A conta mora em pedido/telas_loja.py (29/10/2026): a rotina da madrugada usa a mesma."""
    from core.config import settings
    from pedido import precalculo, telas_loja
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    pronto = dados_pedido._pronto("estoque", empresa, loja_gps, precalculo.assinatura_estoque(
        foto, versao_categorias, versao_catalogo, config, settings.analise.limite_bonificacao))
    if pronto is not None:
        return pronto
    return telas_loja.estoque(
        do_ambiente(), Chaves(settings.pedido.prefixo), empresa, loja_gps, meses, config,
        dados_pedido._base_categorias(versao_categorias),
        dados_pedido._classificacao(versao_catalogo, tuple(sorted(config.linhas_por_setor.items())),
                                    tuple(sorted(config.linhas_por_cmed.items()))))


def carregar_estoque(usuario: dict) -> DadosEstoque | str:
    """Os dados da página Estoque, ou a frase que a tela mostra no lugar."""
    loja = _loja_ou_aviso(usuario)
    if isinstance(loja, str):
        return loja
    config = cfg.aplicar(dados_pedido.configuracao(), _ler_personalizacao(loja["id"]))
    marcador = loja_barra.marcador_estoque(loja["empresa_gps"]) or {}
    resultado = _estoque_preparado(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados,
                                   f"{marcador.get('data')}|{marcador.get('gerado_em')}", dados_pedido._versoes()[0],
                                   dados_pedido._versao_catalogo(), config)
    if resultado is None:
        return ("Os dados desta loja ainda não foram coletados. A rotina da madrugada traz as vendas, as compras "
                "e o estoque; a loja aparece aqui no dia seguinte à primeira coleta.")
    df, meta = resultado
    if df.empty:
        return "Esta loja não tem nenhum produto com estoque no sistema de vendas."
    return DadosEstoque(loja, df, config, meta)


# ---------------------------------------------------------------------------
# Página Ruptura (27/10/2026): cálculo do Assistente + histórico do estoque + classificação
# ---------------------------------------------------------------------------

# O filtro da página Ruptura é DELA (como o do Estoque): categoria, curva e risco.
RUP_CATEGORIAS = "dashboard_ruptura_filtro_categorias"
RUP_CURVAS = "dashboard_ruptura_filtro_curvas"
RUP_RISCOS = "dashboard_ruptura_filtro_riscos"
_RUP_LOJA = "dashboard_ruptura_filtro_loja"


@dataclass
class DadosRuptura:
    """A loja da página Ruptura, pronta: o quadro (um produto do Assistente por linha), a configuração efetiva, a
    meta do cálculo e o resumo do histórico (desde quando mede, modo transição × medida)."""
    loja: dict
    df: pd.DataFrame
    config: ConfigPedido
    meta: dict
    resumo: dict


def filtros_ruptura(loja_id: int) -> tuple[list[str], list[str], list[str]]:
    """(categorias, curvas, riscos) escolhidos na página Ruptura; trocar de loja limpa tudo."""
    if st.session_state.get(_RUP_LOJA) != loja_id:
        st.session_state[_RUP_LOJA] = loja_id
        for chave in (RUP_CATEGORIAS, RUP_CURVAS, RUP_RISCOS):
            st.session_state[chave] = []
    return tuple(list(st.session_state.get(c, [])) for c in (RUP_CATEGORIAS, RUP_CURVAS, RUP_RISCOS))


@st.cache_data(ttl=3600, max_entries=8, show_spinner="Preparando a ruptura da loja...")
@limite.pesado
def _ruptura_preparada(empresa: str, loja_gps: str, meses: int, foto: str, versoes: tuple, versao_catalogo: tuple,
                       config: ConfigPedido, correcoes: tuple):
    """O quadro da página, guardado por loja: `foto`, as versões, a `config` e as correções só entram na chave do
    cache (foto nova, categoria nova, parâmetro novo → recalcula). Reaproveita o cálculo e o cache do Assistente e
    as vendas por dia do modelo de estoque; lê só os últimos ~5 meses do histórico. None = sem foto de estoque.
    A conta mora em pedido/telas_loja.py (29/10/2026): a rotina da madrugada usa a mesma."""
    from core.config import settings
    from pedido import precalculo, telas_loja
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    pronto = dados_pedido._pronto("ruptura", empresa, loja_gps, precalculo.assinatura_ruptura(
        foto, versoes, versao_catalogo, config, correcoes, settings.analise.limite_bonificacao))
    if pronto is not None:
        return pronto
    calc = dados_pedido._calcular(empresa, loja_gps, meses, versoes, dados_pedido.parametros(config), correcoes, foto=foto)
    return telas_loja.ruptura(
        do_ambiente(), Chaves(settings.pedido.prefixo), empresa, loja_gps, meses, config, calc,
        lambda: dados_pedido._classificacao(versao_catalogo, tuple(sorted(config.linhas_por_setor.items())),
                                            tuple(sorted(config.linhas_por_cmed.items()))))


def carregar_ruptura(usuario: dict) -> DadosRuptura | str:
    """Os dados da página Ruptura, ou a frase que a tela mostra no lugar. A configuração é a efetiva da loja
    (padrão + personalização, como na Ruptura antiga); as correções de estoque negativo do Assistente valem."""
    loja = _loja_ou_aviso(usuario)
    if isinstance(loja, str):
        return loja
    correcoes, personalizacao = _ler_estado(loja["id"])
    config = cfg.aplicar(dados_pedido.configuracao(), personalizacao)
    marcador = loja_barra.marcador_estoque(loja["empresa_gps"]) or {}
    resultado = _ruptura_preparada(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados,
                                   f"{marcador.get('data')}|{marcador.get('gerado_em')}", dados_pedido._versoes(),
                                   dados_pedido._versao_catalogo(), config,
                                   tuple(sorted((l, c.estoque) for l, c in correcoes.items())))
    if resultado is None:
        return ("Os dados desta loja ainda não foram coletados. A rotina da madrugada traz as vendas, as compras "
                "e o estoque; a loja aparece aqui no dia seguinte à primeira coleta.")
    (d, resumo), meta = resultado
    if d.empty:
        return (f"Esta loja não teve vendas de {meta['inicio'][8:]}/{meta['inicio'][5:7]} a "
                f"{meta['fim'][8:]}/{meta['fim'][5:7]}. Sem vendas não há como medir a ruptura.")
    return DadosRuptura(loja, d, config, meta, resumo)


# ---------------------------------------------------------------------------
# Página Compras (27/10/2026): compras e vendas do pronto + cálculo do Assistente + classificação
# ---------------------------------------------------------------------------

# O filtro da página Compras é DELE: o período (pílulas), as categorias (tabela e gráfico) e a classificação dos
# produtos (Todos / Excesso / Sem demanda).
COM_PERIODO = "dashboard_compras_periodo"
COM_CATEGORIAS = "dashboard_compras_filtro_categorias"
COM_CLASSE = "dashboard_compras_filtro_classe"
_COM_LOJA = "dashboard_compras_filtro_loja"


@dataclass
class DadosCompras:
    """A loja da página Compras, pronta: um quadro por período (`core.dashboard_compras.preparar`), os itens de nota e
    as vendas por linha (para o gráfico do mês e as notas do detalhe), as regras e a meta do pronto."""
    loja: dict
    quadros: dict               # período → quadro
    periodos: dict              # período → core.dashboard_compras.Periodo
    itens: pd.DataFrame
    vendas: pd.DataFrame
    regras: object
    meta: dict


def filtros_compras(loja_id: int) -> tuple[str, list[str], str]:
    """(período, categorias, classe) da página Compras; trocar de loja limpa as categorias e a classe (o período
    fica: é o mesmo para qualquer loja)."""
    if st.session_state.get(_COM_LOJA) != loja_id:
        st.session_state[_COM_LOJA] = loja_id
        st.session_state[COM_CATEGORIAS] = []
        st.session_state[COM_CLASSE] = "todos"
    return (st.session_state.get(COM_PERIODO, "atual"), list(st.session_state.get(COM_CATEGORIAS, [])),
            st.session_state.get(COM_CLASSE, "todos"))


@st.cache_data(ttl=3600, max_entries=8, show_spinner="Preparando as compras da loja...")
@limite.pesado
def _compras_preparadas(empresa: str, loja_gps: str, meses: int, foto: str, versoes: tuple, versao_catalogo: tuple,
                        config: ConfigPedido):
    """Os quadros dos 3 períodos, guardados por loja (`foto`, as versões e a `config` só entram na chave). Reaproveita
    o pronto e o cálculo do Assistente (custo e estoque ideal), sem as correções de estoque negativo — aqui o
    estoque só entra na sugestão "cerca de N dias". None = sem foto de estoque.
    A conta mora em pedido/telas_loja.py (29/10/2026): a rotina da madrugada usa a mesma."""
    from core.config import settings
    from pedido import precalculo, telas_loja
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    pronto = dados_pedido._pronto("compras", empresa, loja_gps, precalculo.assinatura_compras(
        foto, versoes, versao_catalogo, config, settings.analise.limite_bonificacao))
    if pronto is not None:
        return pronto
    return telas_loja.compras(
        do_ambiente(), Chaves(settings.pedido.prefixo), empresa, loja_gps, meses, config,
        lambda: dados_pedido._calcular(empresa, loja_gps, meses, versoes, dados_pedido.parametros(config), (),
                                       foto=foto),
        dados_pedido._genericos(versoes[1]),
        dados_pedido._classificacao(versao_catalogo, tuple(sorted(config.linhas_por_setor.items())),
                                    tuple(sorted(config.linhas_por_cmed.items()))),
        settings.analise.limite_bonificacao)


def carregar_compras(usuario: dict) -> DadosCompras | str:
    """Os dados da página Compras, ou a frase que a tela mostra no lugar. A configuração é a efetiva da loja."""
    loja = _loja_ou_aviso(usuario)
    if isinstance(loja, str):
        return loja
    config = cfg.aplicar(dados_pedido.configuracao(), _ler_personalizacao(loja["id"]))
    marcador = loja_barra.marcador_estoque(loja["empresa_gps"]) or {}
    resultado = _compras_preparadas(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados,
                                    f"{marcador.get('data')}|{marcador.get('gerado_em')}", dados_pedido._versoes(),
                                    dados_pedido._versao_catalogo(), config)
    if resultado is None:
        return ("Os dados desta loja ainda não foram coletados. A rotina da madrugada traz as vendas, as compras "
                "e o estoque; a loja aparece aqui no dia seguinte à primeira coleta.")
    quadros, periodos, itens, vendas, regras, meta = resultado
    if not periodos:
        return ("A janela de dados desta loja ainda não cobre um mês inteiro antes do mês atual: a dotação parte "
                "das compras do mês anterior. Aumente a janela de meses nas Configurações de Pedidos.")
    if itens.empty:
        return "Esta loja não tem nenhuma compra no sistema de vendas no período guardado."
    return DadosCompras(loja, quadros, periodos, itens, vendas, regras, meta)
