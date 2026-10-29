"""Aba Dados → Rotinas: sincronização de lojas, fila de EAN automática, rotina do GPS e listas FEBRAFAR/CMED."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import rotinas
from core.config import settings
from core.db import get_session
from integrations import sistema_interno as loja_integ
from integrations.base import StatusIntegracao
from views import popups
from views.dados.comum import _categorias_contexto, _explicacao, _milhar
from views.dados.fila_ean import _atualizar_fila_compras, _atualizar_fila_loja

@popups.popup("Sincronização automática de lojas falhou", "aviso")
def _dialog_falha_sincronizacao_lojas(erro: str) -> None:
    """Falha da API externa vira aviso em pop-up, NUNCA erro no meio da tela:
    a aba continua utilizável e as lojas que já estavam no banco continuam
    visíveis — só não foram atualizadas hoje."""
    st.error(erro)
    st.markdown(
        "As lojas que já estavam cadastradas continuam disponíveis normalmente — "
        "o que não aconteceu foi a atualização de hoje. Você pode tentar de novo pelo "
        "botão **Forçar sincronização agora**, na aba Importar Planilhas."
    )
    if popups.botoes("Entendi", None, chave="lojas_falha")[1]:
        st.rerun()


def _sincronizar_lojas_automatico() -> None:
    """Item 16/18 do plano: ao abrir a aba Dados, sincroniza as lojas se ainda
    não sincronizou hoje — sem depender de alguém lembrar de clicar.

    A trava em `st.session_state` evita consultar o controle de rotina a cada
    rerun do Streamlit (que acontece a cada clique em qualquer lugar da tela);
    a trava de verdade contra execução dupla é do lado do banco, em
    `rotinas.reivindicar`."""
    if st.session_state.get("_lojas_auto_ja_verificado"):
        return
    st.session_state["_lojas_auto_ja_verificado"] = True

    adaptador = loja_integ.SistemaInternoLojasAdapter()
    if adaptador.status() != StatusIntegracao.DISPONIVEL:
        # Sem credenciais configuradas não há o que sincronizar — e marcar
        # "sucesso" aqui faria a rotina se considerar feita pelo resto do dia.
        return

    # O spinner não é enfeite: enquanto o item 17 (N+1 da sincronização de
    # lojas) não for corrigido, essa chamada leva alguns minutos, e ela roda
    # sozinha — sem o aviso, a primeira pessoa a abrir a aba no dia ficaria
    # olhando uma tela parada sem saber por quê.
    with st.spinner("Sincronizando a base de lojas (primeira abertura do dia)..."):
        resultado = rotinas.executar_se_necessario(
            rotinas.ROTINA_SINCRONIZACAO_LOJAS,
            lambda: adaptador.sincronizar().mensagem,
        )
    if resultado.executou and not resultado.sucesso and resultado.erro:
        _dialog_falha_sincronizacao_lojas(resultado.erro)


def _sincronizar_lojas() -> None:
    st.markdown("##### Base de Lojas (sistema interno)")
    _explicacao(
        "as lojas ativas da rede: CNPJ, razão social, UF, cidade, responsáveis e o código (legacyId).",
        "a API do sistema interno, sozinha, na primeira abertura desta aba em cada dia.",
        "nada a fazer: o cadastro é do sistema interno. \"Forçar sincronização agora\" traz na hora o que "
        "mudou lá.",
    )

    with get_session() as session:
        controle = rotinas.obter(session, rotinas.ROTINA_SINCRONIZACAO_LOJAS)
        ultimo_sucesso = controle.ultimo_sucesso_em if controle else None
        ultimo_erro = controle.ultimo_erro if controle else None

    if ultimo_sucesso is not None:
        st.caption(f"Última sincronização bem-sucedida: {ultimo_sucesso:%d/%m/%Y %H:%M} (UTC).")
    else:
        st.caption("Ainda não há registro de sincronização bem-sucedida.")
    if ultimo_erro:
        st.caption(f"Última falha registrada: {ultimo_erro}")

    if st.button("Forçar sincronização agora", key="lojas_sincronizar"):
        resultado = rotinas.executar_forcado(
            rotinas.ROTINA_SINCRONIZACAO_LOJAS,
            lambda: loja_integ.SistemaInternoLojasAdapter().sincronizar().mensagem,
        )
        if resultado.sucesso:
            st.success(resultado.mensagem or "Sincronização concluída.")
        else:
            st.error(f"Erro ao sincronizar lojas: {resultado.erro}")


def _fila_loja_automatica() -> None:
    """Uma vez por dia, na primeira abertura da aba Dados (mesmo esquema da
    sincronização de lojas): os genéricos vendidos (Loja (API)) e, desde
    05/10/2026, os comprados (origem GPS). Falha não quebra a tela: vira
    aviso na fila."""
    if st.session_state.get("_fila_loja_ja_verificada"):
        return
    st.session_state["_fila_loja_ja_verificada"] = True
    with st.spinner("Conferindo os genéricos vendidos e comprados nas lojas (primeira abertura do dia)..."):
        rotinas.executar_se_necessario(rotinas.ROTINA_FILA_EAN_LOJA_API, _atualizar_fila_loja)
        rotinas.executar_se_necessario(rotinas.ROTINA_FILA_EAN_COMPRAS, _atualizar_fila_compras)


def _rotina_gps() -> None:
    from pedido import coleta
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    st.markdown("##### Rotina do GPS (dados do Pedido)")
    _explicacao(
        "a coleta de vendas, compras e estoque de cada loja na API do GPS, que alimenta o Pedido.",
        "roda sozinha à meia-noite no GitHub Actions, em 4 partes (passo a passo em docs/rotina_pedido.md).",
        "nada a fazer aqui: o que falhar é tentado de novo na mesma execução e na seguinte. Para rodar na hora: "
        "GitHub → Actions → Rotina Pedido → Run workflow.",
    )
    try:
        data, relatorios = coleta.ultimas_execucoes(do_ambiente(), Chaves(settings.pedido.prefixo))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Não consegui ler os relatórios da rotina ({type(exc).__name__}).")
        return
    if not relatorios:
        st.info("A rotina ainda não rodou (ou não gravou relatório).")
        return
    st.caption(f"Última execução: {data[8:10]}/{data[5:7]}/{data[:4]}.")
    # Execução que caiu antes/fora da coleta (07/10/2026): o motivo aparece
    # aqui, sem precisar abrir o log do GitHub.
    for r in relatorios:
        if r.get("erro_geral"):
            st.error(f"{r.get('parte')}: a execução caiu — {r['erro_geral']}")
    st.dataframe(pd.DataFrame([{
        "Parte": r.get("parte"),
        "Empresas": f"{r.get('empresas_processadas', 0)}/{r.get('empresas_na_parte', 0)}",
        "Lojas": r.get("lojas", 0),
        "Sem lista de lojas": r.get("empresas_sem_lista_de_lojas", 0),
        "Consultas ok": r.get("unidades_ok", 0),
        "Falhas": r.get("unidades_com_falha", 0),
        "Pendentes p/ próxima": len(r.get("pendentes_para_proxima", [])),
        "Lojas prontas": r.get("prontos_montados", 0),
        "Minutos": round(float(r.get("segundos", 0)) / 60, 1),
        "Parou pelo tempo": "sim" if r.get("parou_por_tempo") else "não",
    } for r in relatorios]), hide_index=True, use_container_width=True)
    falhas = [f for r in relatorios for f in r.get("falhas", [])]
    if falhas:
        with st.expander(f"{len(falhas)} falha(s) nesta execução"):
            st.dataframe(pd.DataFrame(falhas).rename(columns={"unidade": "Consulta", "erro": "Erro"}),
                         hide_index=True, use_container_width=True)


def _fontes_categorias() -> None:
    categorias_integ, _cat, armaz, chaves = _categorias_contexto()
    st.markdown("##### Listas de categorias (FEBRAFAR e CMED)")
    _explicacao(
        "as duas listas públicas/comerciais que dão a categoria da maioria dos produtos.",
        "FEBRAFAR (planilha da rede) e CMED/ANVISA (lista de preços do gov.br), unidas num arquivo só.",
        "gere o arquivo novo com `python -m pedido.carga_categorias … --executar` e clique em Importar → "
        "Categorias de produtos → Recarregar base inicial.",
    )
    info = categorias_integ.base_inicial_disponivel(armaz, chaves)
    if info is None:
        st.caption("Arquivo ainda não gerado.")
        return
    fontes = info.get("fontes", {})
    st.caption(f"Arquivo gerado em {str(info.get('gerado_em', '?'))[:10]} com "
               f"{_milhar(int(info.get('eans', 0)))} EANs — FEBRAFAR: {fontes.get('febrafar', '?')}; "
               f"CMED: {fontes.get('cmed', '?')}. A CMED é publicada todo mês pela ANVISA.")
