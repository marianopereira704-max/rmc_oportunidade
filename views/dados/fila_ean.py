"""Aba Dados → Pendências → Fila de Resolução de EAN (origens GPS, Gruppy e Loja (API))."""
from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from core import auth, ui
from core.db import get_session
from core.models import BaseGenerico, OrigemFila
from reconciliation import motor as reconciliation_motor
from views import analise_comum
from views.dados.comum import _categorias_contexto, _explicacao, _milhar
from views.dados.sem_classificacao import _meses_compras

def _fila_ean() -> None:
    usuario = auth.usuario_atual()
    st.markdown("##### Fila de Resolução de EAN")
    _explicacao(
        "genéricos (pela base de categorias) cujo EAN ainda não está ligado a um genérico da Base Genéricos — na "
        "Análise não somam com os outros EANs do mesmo genérico e, no Pedido, aparecem soltos.",
        "compras das lojas pela API (origem GPS, conferidas uma vez por dia), tabelas da Gruppy e genéricos "
        "vendidos pelas lojas (\"Loja (API)\"). Prioridade: dinheiro em jogo (o comprado nos últimos 3 meses "
        "fechados; na Loja (API), o vendido). Produto que não é genérico não passa por aqui: compara pelo próprio EAN.",
        "confirme a sugestão, vincule a um genérico existente, cadastre um genérico novo ou ignore. Itens "
        "\"Loja (API)\" nunca se resolvem sozinhos: sempre pedem confirmação.",
    )
    if "fila_loja_resultado" in st.session_state:
        st.success(st.session_state.pop("fila_loja_resultado"))
    b1, b2 = st.columns(2)
    if b1.button("Conferir agora os genéricos comprados", key="fila_compras_atualizar", use_container_width=True):
        with st.spinner("Lendo as compras das lojas..."):
            try:
                st.session_state["fila_loja_resultado"] = _atualizar_fila_compras()
            except Exception as exc:  # noqa: BLE001
                st.session_state["fila_loja_resultado"] = f"Não foi possível conferir agora: {exc}"
        st.rerun()
    if b2.button("Conferir agora os genéricos vendidos nas lojas", key="fila_loja_atualizar", use_container_width=True):
        with st.spinner("Lendo os produtos das lojas..."):
            try:
                st.session_state["fila_loja_resultado"] = _atualizar_fila_loja()
            except Exception as exc:  # noqa: BLE001
                st.session_state["fila_loja_resultado"] = f"Não foi possível conferir agora: {exc}"
        st.rerun()

    if "fila_ean_reprocesso_resultado" in st.session_state:
        r = st.session_state.pop("fila_ean_reprocesso_resultado")
        st.success(
            f"Limpeza de EAN sujo: {r.limpeza.itens_tabela_gruppy_corrigidos} em ItemTabelaGruppy, "
            f"{r.limpeza.fila_resolucao_ean_corrigidos} na própria fila "
            f"({r.limpeza.fila_resolucao_ean_colisoes_mescladas} mesclados por colisão). "
            f"Reprocessamento: {r.itens_avaliados} itens pendentes avaliados — "
            f"{r.resolvidos_por_ean_existente} resolvidos por EAN já cadastrado na Base Genéricos, "
            f"{r.resolvidos_automaticamente} resolvidos automaticamente (fuzzy-match), "
            f"{r.sugestao_atualizada} com sugestão nova, {r.sem_mudanca} sem mudança relevante."
        )

    with st.expander("Reprocessar fila contra a base atual"):
        st.caption(
            "Refaz a busca de candidato pra todo item pendente contra a Base Genéricos de HOJE — "
            "útil quando a base foi importada/cresceu depois de uploads que já tinham caído na fila. "
            "Também corrige, de quebra, qualquer EAN gravado sujo (sufixo \".0\" de planilha lida como "
            "float antes da normalização de EAN estar em uso)."
        )
        if st.button("Reprocessar fila contra a base atual", key="fila_ean_reprocessar", type="primary"):
            with get_session() as session:
                resultado = reconciliation_motor.reprocessar_fila_resolucao(session)
            st.session_state["fila_ean_reprocesso_resultado"] = resultado
            st.rerun()

    origem_opcoes = {"Todos": None, "GPS": OrigemFila.GPS, "Gruppy": OrigemFila.GRUPPY, "Loja (API)": OrigemFila.LOJA_API}
    origem_label = st.selectbox("Origem", options=list(origem_opcoes.keys()), key="fila_ean_origem_filtro")

    # Só Loja (API): pareto pelo nº de lojas que vendem (01/10/2026), com o %
    # acumulado no título de cada item.
    por_lojas = origem_opcoes[origem_label] == OrigemFila.LOJA_API
    with get_session() as session:
        itens = reconciliation_motor.listar_fila_priorizada(session, origem_filtro=origem_opcoes[origem_label],
                                                            por_lojas=por_lojas)
        total_lojas = reconciliation_motor.soma_ocorrencias_pendentes(session, OrigemFila.LOJA_API) if por_lojas else 0
        genericos = session.execute(
            select(BaseGenerico).where(BaseGenerico.ativo.is_(True)).order_by(BaseGenerico.nome_canonico)
        ).scalars().all()
        opcoes_generico_id_por_nome = {g.nome_canonico: g.id for g in genericos}
        nome_generico_por_id = {g.id: g.nome_canonico for g in genericos}

        if not itens:
            st.markdown('<p class="rmc-muted">Nenhum item pendente na fila.</p>', unsafe_allow_html=True)
            return

        if por_lojas:
            st.caption("Em ordem de pareto: os genéricos vendidos em mais lojas primeiro. O % acumulado diz quanto das "
                       "aparições fica resolvido cadastrando até aquele item.")
        acumulado = 0
        for item in itens:
            titulo = f"{item.descricao_observada}  —  EAN {item.ean}"
            if por_lojas and total_lojas:
                acumulado += item.qtd_ocorrencias or 0
                titulo = (f"{item.qtd_ocorrencias} loja(s) · {acumulado / total_lojas:.0%} acumulado  —  "
                          f"{titulo}")
            # Expander preguiçoso: o seletor com todos os genéricos (milhares
            # de opções) só é montado no item aberto — antes era montado em
            # TODOS os itens listados, a cada clique na tela.
            painel = st.expander(titulo, key=f"exp_fila_ean_{item.id}", on_change="rerun")
            if not painel.open:
                continue
            with painel:
                if item.origem == OrigemFila.LOJA_API:
                    # Loja (API): o valor é o VENDIDO nas lojas na janela, e as
                    # "ocorrências" são as lojas que venderam (integrations/fila_loja.py).
                    st.markdown(analise_comum.texto_markdown(
                        f"{ui.formatar_moeda(float(item.valor_total_acumulado))} vendidos · "
                        f"{item.qtd_ocorrencias} loja(s) · origem Loja (API) — confirme antes de vincular"
                    ))
                else:
                    st.markdown(
                        f"{ui.formatar_moeda(float(item.valor_total_acumulado))} acumulado · "
                        f"{item.qtd_ocorrencias} ocorrência(s) · origem {item.origem.value.upper()}"
                    )

                if item.sugestao_base_generico_id is not None:
                    nome_sugestao = nome_generico_por_id.get(item.sugestao_base_generico_id, "(genérico removido)")
                    score_texto = f"{float(item.sugestao_score):.0f}%" if item.sugestao_score is not None else "—"
                    st.markdown(f"Sugestão automática: **{nome_sugestao}** ({score_texto} de similaridade)")
                    if st.button("Confirmar sugestão", key=f"confirmar_sug_{item.id}", use_container_width=True):
                        with get_session() as s2:
                            reconciliation_motor.confirmar_resolucao_manual(
                                s2, item.id, item.sugestao_base_generico_id, usuario["nome"]
                            )
                        st.rerun()
                    st.markdown("— ou vincular a outro —")

                escolha = st.selectbox(
                    "Vincular a um genérico existente",
                    options=["—"] + list(opcoes_generico_id_por_nome.keys()),
                    key=f"select_generico_{item.id}",
                )
                if escolha != "—" and st.button("Vincular", key=f"vincular_{item.id}", use_container_width=True):
                    with get_session() as s2:
                        reconciliation_motor.confirmar_resolucao_manual(
                            s2, item.id, opcoes_generico_id_por_nome[escolha], usuario["nome"]
                        )
                    st.rerun()

                st.markdown("— ou —")
                novo_nome = st.text_input("Cadastrar como genérico novo", key=f"novo_generico_{item.id}")
                if novo_nome.strip() and st.button("Cadastrar e vincular", key=f"cadastrar_{item.id}", use_container_width=True):
                    with get_session() as s2:
                        reconciliation_motor.registrar_novo_generico(s2, item.id, novo_nome.strip(), usuario["nome"])
                    st.rerun()

                if st.button("Ignorar este item", key=f"ignorar_{item.id}", use_container_width=True):
                    with get_session() as s2:
                        reconciliation_motor.ignorar_fila(s2, item.id)
                    st.rerun()


def _atualizar_fila_loja() -> str:
    """Genéricos vendidos nas lojas e fora da Base Genéricos → fila de EAN
    (integrations/fila_loja.py). Devolve a frase do resultado."""
    from integrations import fila_loja

    categorias_integ, _cat, armaz, chaves = _categorias_contexto()
    catalogo = categorias_integ.catalogos(armaz, chaves)
    with get_session() as session:
        base = categorias_integ.tabela(session)
        r = fila_loja.atualizar(session, catalogo, base)
    return (f"{_milhar(r.genericos_vendidos)} genérico(s) vendido(s) nas lojas; {_milhar(r.ja_na_base)} já na Base "
            f"Genéricos; {_milhar(r.novos_na_fila)} novo(s) na fila; {_milhar(r.atualizados)} atualizado(s)"
            + (f"; {r.em_outra_origem} já estavam na fila vindos do GPS/Gruppy" if r.em_outra_origem else "") + ".")


def _atualizar_fila_compras() -> str:
    """Genéricos comprados fora da Base Genéricos → fila de EAN, e o valor
    da fila regravado (integrations/fila_compras.py)."""
    from integrations import fila_compras

    categorias_integ, _cat, armaz, chaves = _categorias_contexto()
    meses = _meses_compras()
    if not meses:
        return "As compras da rede ainda não foram montadas (a rotina da madrugada junta os meses fechados)."
    resumo = fila_compras.resumo_dos_meses(armaz, chaves, meses)
    with get_session() as session:
        base = categorias_integ.tabela(session, com_origem=False)
        r = fila_compras.atualizar(session, resumo, base)
    return (f"{_milhar(r.genericos_comprados)} genérico(s) comprado(s) nas lojas; {_milhar(r.ja_na_base)} já na Base "
            f"Genéricos; {_milhar(r.novos_na_fila)} novo(s) na fila; {_milhar(r.resolvidos_automaticamente)} "
            f"resolvido(s) automaticamente; valor regravado em {_milhar(r.valores_regravados)} item(ns).")
