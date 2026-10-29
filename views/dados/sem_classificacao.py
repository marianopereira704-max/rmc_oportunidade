"""Aba Dados → Pendências → Sem Classificação (vendidos nas lojas e da Análise de Oportunidade)."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core import auth
from core.db import get_session
from storage import filesystem as fs
from views import analise_comum
from views import leitor_planilha as leitor
from views.dados.comum import (
    _LEITOR_CATEGORIAS_PENDENCIAS,
    _categorias_contexto,
    _explicacao,
    _mensagem_categorias,
    _milhar,
    _mostrar_planilha_recebida,
    _subpasta,
    _xlsx,
)

@st.cache_data(ttl=600, show_spinner="Lendo os produtos das lojas...")
def _sem_classificacao_calculado(versao: tuple) -> tuple[pd.DataFrame, int]:
    """(todos os produtos sem classificação, total de produtos nas lojas).
    Guardado por 10 min e pela versão da base de categorias: é o contador de
    Pendências, e ler os catálogos + a base custa alguns segundos."""
    categorias_integ, _cat, armaz, chaves = _categorias_contexto()
    catalogo = categorias_integ.catalogos(armaz, chaves)
    with get_session() as session:
        base = categorias_integ.tabela(session)
    return categorias_integ.sem_classificacao(catalogo, base), len(catalogo)


def _versao_categorias() -> tuple:
    from integrations import categorias as categorias_integ

    with get_session() as session:
        return categorias_integ.versao(session)


@st.cache_data(ttl=600, show_spinner="Lendo as tabelas Gruppy e as compras...")
def _sem_classificacao_analise_calculado(versao: tuple) -> pd.DataFrame:
    """Sub-aba da Análise (Q35 de 05/10/2026): EANs das tabelas Gruppy sem
    categoria, com o que as lojas compraram deles nos últimos 3 meses."""
    from core.models import ItemTabelaGruppy, StatusCobertura, TabelaGruppy, TabelaGruppyCobertura
    from integrations import fila_compras

    categorias_integ, cat, armaz, chaves = _categorias_contexto()
    with get_session() as session:
        base = categorias_integ.tabela(session, com_origem=False)
        linhas = session.execute(
            select(ItemTabelaGruppy.ean, ItemTabelaGruppy.descricao_origem, TabelaGruppy.laboratorio)
            .join(TabelaGruppy, TabelaGruppy.id == ItemTabelaGruppy.tabela_gruppy_id)
            .where(select(TabelaGruppyCobertura.id).where(
                TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id,
                TabelaGruppyCobertura.status == StatusCobertura.ATIVA).exists())
        ).all()
    itens = pd.DataFrame(linhas, columns=["ean", "descricao", "laboratorio"])
    itens["ean"] = cat.chaves_ean(itens["ean"])
    resumo = fila_compras.resumo_dos_meses(armaz, chaves, _meses_compras())
    return categorias_integ.sem_classificacao_analise(itens, resumo, base)


def _meses_compras() -> list[str]:
    """Os últimos 3 meses fechados com compras da rede (o período da Análise)."""
    from integrations import fila_compras

    return analise_comum.meses_api(fila_compras.MESES_DO_VALOR)


def _sem_classificacao() -> None:
    """Pendências → Sem Classificação: relatório, exportação e envio de volta
    no mesmo lugar (ciclo fechado, decisão de 27/09/2026). Duas sub-abas
    desde 05/10/2026 (Q35): os produtos vendidos nas lojas (Assistente de
    pedido) e os EANs das tabelas Gruppy (Análise de Oportunidade) — o envio
    da lista preenchida é um só, embaixo das duas."""
    st.markdown("##### Produtos Sem Classificação")
    _mensagem_categorias()
    vendidos, analise_aba = st.tabs(["Vendidos nas lojas", "Análise de Oportunidade"])
    with vendidos:
        _sem_classificacao_vendidos()
    with analise_aba:
        _sem_classificacao_analise()
    st.markdown("###### Enviar a lista preenchida")
    _envio_categorias(_LEITOR_CATEGORIAS_PENDENCIAS)


def _sem_classificacao_analise() -> None:
    from integrations import categorias as categorias_integ

    _explicacao(
        "EANs das tabelas Gruppy que a base de categorias não conhece. Na Análise eles comparam pelo próprio EAN; "
        "classificados como genérico, passam a somar com os outros EANs do mesmo genérico (pela fila de EAN).",
        "os itens das tabelas Gruppy ativas, com o que as lojas compraram deles nos últimos 3 meses fechados.",
        "baixe a lista, preencha a coluna CATEGORIA e envie o arquivo embaixo. Vale na hora.",
    )
    try:
        lista = _sem_classificacao_analise_calculado(_versao_categorias())
    except Exception as exc:  # noqa: BLE001 — sem Spaces: a sub-aba avisa, o resto da tela segue
        st.warning(f"Não foi possível ler as tabelas e as compras agora: {exc}")
        return
    if lista.empty:
        st.success("Todos os EANs das tabelas Gruppy estão classificados.")
        return
    lista = lista.assign(**{"% ACUMULADO": categorias_integ.pareto_acumulado(lista["LOJAS QUE COMPRARAM"])})
    st.caption(f"**{_milhar(len(lista))}** EAN(s) das tabelas Gruppy sem classificação (pareto: os comprados por "
               "mais lojas primeiro; a tela mostra os 200 primeiros, o arquivo tem todos).")
    st.dataframe(
        lista.head(200), hide_index=True, use_container_width=True,
        column_order=["EAN", "PRODUTO", "LOJAS QUE COMPRARAM", "% ACUMULADO", "VALOR COMPRADO", "TABELA GRUPPY",
                      "SITUACAO"],
        column_config={"% ACUMULADO": st.column_config.NumberColumn(format="%.1f%%"),
                       "VALOR COMPRADO": st.column_config.NumberColumn(format="R$ %.2f")},
    )
    st.download_button(
        "Baixar lista (.xlsx)", data=_xlsx(lista), file_name="sem_classificacao_analise.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="categorias_analise_baixar",
    )


def _sem_classificacao_vendidos() -> None:
    from integrations import categorias as categorias_integ

    _explicacao(
        "produtos das lojas sem categoria (ou \"em classificação\" na FEBRAFAR). No Pedido eles aparecem sem "
        "sugestão.",
        "os produtos que a rotina da madrugada traz do estoque de cada loja, comparados com a base de categorias.",
        "baixe a lista, preencha a coluna CATEGORIA e envie o mesmo arquivo aqui embaixo. Vale na hora.",
    )
    relatorio, total = _sem_classificacao_calculado(_versao_categorias())
    if not total:
        st.info("A rotina do Pedido ainda não gravou os produtos das lojas — ela roda de madrugada.")
    elif relatorio.empty:
        st.success(f"Todos os {_milhar(total)} produtos das lojas estão classificados.")
    else:
        so_estoque = st.checkbox("Só produtos com estoque em alguma loja", value=True, key="categorias_so_estoque")
        lista = relatorio[relatorio["LOJAS COM ESTOQUE"] > 0] if so_estoque else relatorio
        # Pareto (01/10/2026): vendido em mais lojas primeiro, com o % acumulado
        # das aparições — "classificando até aqui, resolvo X%".
        lista = lista.assign(**{"% ACUMULADO": categorias_integ.pareto_acumulado(lista["LOJAS COM VENDA"])})
        st.caption(f"**{_milhar(len(lista))}** de {_milhar(total)} produtos das lojas sem classificação "
                   "(pareto: os vendidos em mais lojas primeiro — a coluna % ACUMULADO mostra quanto das aparições "
                   "fica resolvido classificando até aquela linha; a tela mostra os 200 primeiros, o arquivo tem todos).")
        # As colunas do pareto na frente: no fim da tabela ficavam fora da tela.
        st.dataframe(
            lista.head(200), hide_index=True, use_container_width=True,
            column_order=["EAN", "PRODUTO", "LOJAS COM VENDA", "% ACUMULADO", "LOJAS COM ESTOQUE", "LOJAS",
                          "LABORATORIO", "GRUPO NO GPS", "CATEGORIA NO GPS", "SITUACAO"],
            column_config={"% ACUMULADO": st.column_config.NumberColumn(format="%.1f%%")},
        )
        st.download_button(
            "Baixar lista (.xlsx)", data=_xlsx(lista), file_name="produtos_sem_classificacao.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="categorias_baixar",
        )


def _envio_categorias(chave_leitor: str) -> None:
    categorias_integ, categorias, _armaz, _chaves = _categorias_contexto()
    usuario = auth.usuario_atual()
    st.caption("Planilha com as colunas **EAN** e **CATEGORIA**. Linha com CATEGORIA vazia é ignorada. "
               "Categorias aceitas: " + ", ".join(categorias.CATEGORIAS_MANUAIS) + ".")
    leitor.leitor_planilha(chave_leitor, "Selecionar planilha de categorias (.xlsx)")
    _mostrar_planilha_recebida(chave_leitor)
    recebida = leitor.planilha_recebida(chave_leitor)
    if recebida is None:
        return
    try:
        lida = categorias_integ.ler_planilha_manual(recebida.df)
    except ValueError as exc:
        st.error(str(exc))
        return
    if lida.invalidas:
        st.warning(f"{len(lida.invalidas)} linha(s) com erro não serão gravadas: "
                   + "; ".join(f"linha {n}: {motivo}" for n, motivo in lida.invalidas[:10])
                   + (" …" if len(lida.invalidas) > 10 else ""))
    st.caption(f"{_milhar(len(lida.validas))} EAN(s) com categoria serão gravados.")
    if len(lida.validas) and st.button("Gravar categorias", key=f"{chave_leitor}_gravar"):
        with get_session() as s2:
            fs.guardar_planilha(
                s2, _subpasta(s2, "Categorias"), recebida.nome, usuario["nome"],
                conteudo=recebida.conteudo, storage_key=recebida.storage_key, tamanho_bytes=recebida.tamanho_bytes,
            )
            gravados = categorias_integ.aplicar_manual(s2, lida.validas, usuario["nome"])
        leitor.consumir(chave_leitor)
        _sem_classificacao_calculado.clear()
        _sem_classificacao_analise_calculado.clear()
        st.session_state["categorias_msg"] = f"{_milhar(gravados)} categoria(s) gravada(s). Já valem no Pedido."
        st.rerun()
