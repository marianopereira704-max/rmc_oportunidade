"""Aba Dados → Importar: Base Genéricos, tabelas Gruppy e categorias de produtos."""
from __future__ import annotations

import streamlit as st

from core import auth
from core.config import settings
from core.db import get_session
from core.models import ModoCustoGruppy, OrigemFila
from integrations import base_genericos as base_genericos_integ
from integrations import gruppy as gruppy_integ
from integrations import mapeamento as mapeamento_integ
from views import leitor_planilha as leitor
from views import popups
from views.dados.comum import (
    _LEITOR_CATEGORIAS,
    _categorias_contexto,
    _explicacao,
    _mensagem_categorias,
    _milhar,
    _mostrar_planilha_recebida,
    _planilha_valida,
    _subpasta,
)
from views.dados.sem_classificacao import _envio_categorias, _sem_classificacao_calculado

_SEM_SELECAO = "— selecione —"
_LEITOR_GRUPPY = "gruppy_leitor"
_LEITOR_BASE = "base_genericos_leitor"
_ROTULOS_CAMPOS_GRUPPY = {
    "ean": "EAN",
    "descricao": "Descrição",
    "custo": "Custo líquido (já pronto)",
    "preco_bruto": "Preço bruto (de tabela)",
    "desconto": "Desconto (%)",
}


def _selecionar_mapeamento(
    campos_obrigatorios: set[str], rotulos: dict[str, str], colunas_planilha: list[str],
    sugestao: dict[str, str], key_prefix: str,
) -> dict[str, str]:
    opcoes = [_SEM_SELECAO] + colunas_planilha
    mapa_escolhido: dict[str, str] = {}
    for campo in sorted(campos_obrigatorios, key=lambda c: rotulos.get(c, c)):
        valor_sugerido = sugestao.get(campo, _SEM_SELECAO)
        indice = opcoes.index(valor_sugerido) if valor_sugerido in opcoes else 0
        escolha = st.selectbox(
            rotulos.get(campo, campo), options=opcoes, index=indice, key=f"{key_prefix}_{campo}",
        )
        if escolha != _SEM_SELECAO:
            mapa_escolhido[campo] = escolha
    return mapa_escolhido


@popups.popup("Confirmar mapeamento de colunas — Gruppy", "longo")
def _dialog_mapeamento_gruppy(
    usuario: dict, laboratorio: str, ufs: list[str], modo_custo: ModoCustoGruppy,
) -> None:
    recebida = leitor.planilha_recebida(_LEITOR_GRUPPY)
    if recebida is None:
        st.info("Selecione a planilha de novo.")
        # Formulário longo não tem X (Q14): a saída é este botão.
        if popups.botoes(None, "Fechar", chave="map_gruppy_sem_planilha")[0]:
            st.rerun()
        return
    df_preview = recebida.df
    colunas_planilha = [str(c) for c in df_preview.columns]
    mapa_automatico = gruppy_integ.detectar_colunas_automatico(df_preview, modo_custo)
    campos = gruppy_integ.campos_obrigatorios(modo_custo)

    with get_session() as session:
        sugestao = mapeamento_integ.sugerir_mapeamento(
            session, OrigemFila.GRUPPY, campos, colunas_planilha, mapa_automatico,
        )

    st.caption(
        'Confira qual coluna da planilha corresponde a cada campo antes de processar — nomes '
        'ambíguos (ex: "Preço" pode ser bruto ou líquido) não dá pra resolver só por heurística.'
    )
    mapa_escolhido = _selecionar_mapeamento(campos, _ROTULOS_CAMPOS_GRUPPY, colunas_planilha, sugestao, "map_gruppy")

    completo = len(mapa_escolhido) == len(campos)
    if not completo:
        st.caption("Selecione uma coluna para todo campo obrigatório antes de processar.")
    aviso = st.empty()   # erro acima do rodapé (que fica preso embaixo)
    cancelar, processar = popups.botoes("Processar tabela", chave="map_gruppy", desabilitado=not completo)
    if cancelar:
        st.rerun()
    if processar and completo:
        try:
            with st.spinner("Processando a tabela..."):
                with get_session() as session:
                    pasta_id = _subpasta(session, "Ofertas", "Gruppy")
                    resultado = gruppy_integ.processar_planilha_gruppy(
                        session, recebida.conteudo, recebida.nome, usuario["nome"], pasta_id,
                        laboratorio=laboratorio, ufs=ufs, modo_custo=modo_custo, mapa_confirmado=mapa_escolhido,
                        df=df_preview, storage_key=recebida.storage_key, tamanho_bytes=recebida.tamanho_bytes,
                    )
                    mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GRUPPY, mapa_escolhido, usuario["nome"])
            leitor.consumir(_LEITOR_GRUPPY)
            st.session_state["gruppy_upload_sucesso"] = resultado.mensagem
            st.rerun()
        except Exception as exc:
            aviso.error(f"Erro ao processar planilha: {exc}")


def _importar_gruppy(usuario: dict) -> None:
    st.markdown("##### Tabela de Preços RMC (Gruppy)")
    _explicacao(
        "o preço de cada genérico num laboratório — a referência da Análise de Oportunidade.",
        "a planilha da Gruppy de UM laboratório, enviada aqui, com as UFs que ela cobre.",
        "envie a tabela nova do laboratório: só as UFs que se repetem são substituídas, as demais continuam "
        "valendo pela anterior. Para desfazer: Explorador de Arquivos → o arquivo → Excluir definitivamente.",
    )

    if "gruppy_upload_sucesso" in st.session_state:
        st.success(st.session_state.pop("gruppy_upload_sucesso"))

    with get_session() as session:
        labs_existentes = gruppy_integ.laboratorios_existentes(session)

    col1, col2 = st.columns(2)
    with col1:
        laboratorio = st.selectbox(
            "Laboratório",
            options=labs_existentes,
            accept_new_options=True,
            placeholder="Selecione ou digite um novo laboratório",
            key="gruppy_laboratorio",
        )
    with col2:
        ufs = st.multiselect("UFs cobertas por esta tabela", options=settings.geografia.ufs_brasil, key="gruppy_ufs")

    modo_label = st.radio(
        "Como a planilha traz o custo?",
        options=["Custo líquido já pronto", "Preço bruto + % de desconto"],
        key="gruppy_modo",
        horizontal=True,
    )
    modo_custo = ModoCustoGruppy.PRONTO if modo_label.startswith("Custo") else ModoCustoGruppy.BRUTO_DESCONTO

    leitor.leitor_planilha(_LEITOR_GRUPPY, "Selecionar planilha Gruppy (.xlsx)")
    _mostrar_planilha_recebida(_LEITOR_GRUPPY)
    recebida = leitor.planilha_recebida(_LEITOR_GRUPPY)
    if recebida and not _planilha_valida(gruppy_integ.validar_planilha, recebida.df):
        return

    if recebida and not (laboratorio and ufs):
        st.caption("Selecione o laboratório e ao menos uma UF antes de processar.")
    if recebida and laboratorio and ufs and st.button("Processar planilha Gruppy", key="gruppy_processar"):
        _dialog_mapeamento_gruppy(usuario, laboratorio, ufs, modo_custo)


def _importar_base_genericos(usuario: dict) -> None:
    st.markdown("##### Base Genéricos (planilha curada)")
    _explicacao(
        "qual EAN é qual genérico (nome canônico) — junta os EANs do mesmo genérico na análise e no Pedido.",
        "a planilha curada da rede (EAN + descrição), enviada aqui, e as resoluções da Fila de EAN.",
        "envie a planilha com os EANs novos. EAN que já tem resolução nunca é sobrescrito, só pulado; depois, "
        "use \"Reprocessar fila contra a base atual\" em Pendências → Fila de EAN.",
    )

    leitor.leitor_planilha(_LEITOR_BASE, "Selecionar planilha Base Genéricos (.xlsx)")
    _mostrar_planilha_recebida(_LEITOR_BASE)
    recebida = leitor.planilha_recebida(_LEITOR_BASE)
    if recebida and not _planilha_valida(base_genericos_integ.validar_planilha, recebida.df):
        return

    if recebida and st.button("Processar planilha Base Genéricos", key="base_genericos_processar"):
        try:
            with st.spinner("Processando a Base Genéricos..."):
                with get_session() as session:
                    pasta_id = _subpasta(session, "Base Genéricos")
                    resultado = base_genericos_integ.processar_planilha_base_genericos(
                        session, recebida.conteudo, recebida.nome, usuario["nome"], pasta_id,
                        df=recebida.df, storage_key=recebida.storage_key, tamanho_bytes=recebida.tamanho_bytes,
                    )
            leitor.consumir(_LEITOR_BASE)
            st.success(resultado.mensagem)
        except Exception as exc:
            st.error(f"Erro ao processar planilha: {exc}")


def _categorias_importar() -> None:
    """Importar → Categorias de produtos: contagem, carga inicial e envio."""
    categorias_integ, categorias, armaz, chaves = _categorias_contexto()
    usuario = auth.usuario_atual()
    st.markdown("##### Categorias de produtos")
    _explicacao(
        "a categoria de cada EAN. Decide quantos dias de estoque o Pedido sugere (medicamento, perfumaria — "
        "ver Configurações de Pedidos); produto Sem Classificação não recebe sugestão.",
        "a lista da FEBRAFAR e a da CMED/ANVISA (já prontas no sistema, é só carregar uma vez), os genéricos do "
        "catálogo IQVIA que elas não cobrem e as planilhas enviadas aqui.",
        "envie uma planilha com EAN e CATEGORIA (abaixo). O que você enviar vale na hora e nunca é desfeito "
        "por uma nova carga da FEBRAFAR/CMED. A lista do que falta classificar está em Pendências → Sem Classificação.",
    )
    _mensagem_categorias()
    with get_session() as session:
        cont = categorias_integ.contagens(session)
    if cont["total"]:
        g, o = cont["por_grupo"], cont["por_origem"]
        grupos = " · ".join(f"{categorias.ROTULO_GRUPO[k]} {_milhar(g.get(k, 0))}"
                            for k in (categorias.MEDICAMENTO, categorias.PERFUMARIA, categorias.SEM_CLASSIFICACAO))
        origens = " · ".join(f"{nome} {_milhar(o.get(chave, 0))}"
                             for chave, nome in (("FEBRAFAR", "FEBRAFAR"), ("CMED", "CMED"), ("IQVIA", "IQVIA"),
                                                 ("MANUAL", "Manual")))
        st.caption(f"Na base: **{_milhar(cont['total'])}** EANs — {grupos}  |  origem: {origens}")

    st.markdown("###### Base inicial (FEBRAFAR + CMED)")
    info = categorias_integ.base_inicial_disponivel(armaz, chaves)
    if info is None:
        st.info("O arquivo da base inicial ainda não está no armazenamento. Ele é gerado uma vez com "
                "`python -m pedido.carga_categorias --febrafar <planilha> --cmed <planilha> --executar`.")
    else:
        st.caption(f"Arquivo pronto: {_milhar(int(info.get('eans', 0)))} EANs, "
                   f"gerado em {str(info.get('gerado_em', '?'))[:10]}.")
        rotulo = "Recarregar base inicial (mantém as categorias manuais)" if cont["total"] else "Carregar base inicial"
        if st.button(rotulo, key="categorias_carregar"):
            with st.spinner("Carregando a base de categorias..."):
                base = armaz.ler_df(chaves.base_categorias())
                with get_session() as s2:
                    r = categorias_integ.carregar_base_inicial(s2, base, usuario["nome"])
            _sem_classificacao_calculado.clear()
            st.session_state["categorias_msg"] = (
                f"{_milhar(r.eans_no_arquivo)} EANs carregados em {r.segundos:.0f}s"
                + (f"; {r.manuais_mantidas} categoria(s) manual(is) mantida(s)." if r.manuais_mantidas else ".")
            )
            st.rerun()

    _catalogo_iqvia(armaz, chaves, usuario)
    _fontes_classificacao(armaz, chaves)

    st.markdown("###### Enviar categorias")
    _envio_categorias(_LEITOR_CATEGORIAS)


def _fontes_classificacao(armaz, chaves) -> None:
    """Fontes da classificação (25/10/2026): o que a FEBRAFAR e a CMED dizem de cada EAN, para a cascata de
    classificação das Configurações de Pedidos. Mesmo caminho da base inicial: arquivo no Spaces, botão aqui."""
    from integrations import classificacao

    st.markdown("###### Fontes da classificação (FEBRAFAR e CMED por EAN)")
    st.caption("Guarda a categoria da FEBRAFAR e o tipo da CMED de cada produto, para a cascata de classificação "
               "das Configurações de Pedidos. Não muda nenhuma categoria da base nem os dias de estoque.")
    with get_session() as session:
        total, _ultima = classificacao.versao(session)
        tipos = classificacao.contagem_por_cmed(session) if total else {}
    info = classificacao.disponivel(armaz, chaves)
    if info is None:
        st.info("O arquivo ainda não está no armazenamento. Ele é gerado junto com a base inicial, com "
                "`python -m pedido.carga_categorias --febrafar <planilha> --cmed <planilha> --executar`.")
        return
    st.caption(f"Arquivo pronto: {_milhar(int(info.get('eans', 0)))} EANs, gerado em "
               f"{str(info.get('gerado_em', '?'))[:10]}." + (f" Na base: {_milhar(total)} EANs." if total else ""))
    if tipos:
        st.caption("Tipos da CMED na base: " + ", ".join(f"{t} {_milhar(n)}" for t, n in
                                                        sorted(tipos.items(), key=lambda kv: -kv[1])) + ".")
    if st.button("Recarregar fontes" if total else "Carregar fontes", key="classificacao_carregar"):
        with st.spinner("Carregando as fontes da classificação..."):
            dados = armaz.ler_df(chaves.classificacao_ean())
            with get_session() as s2:
                r = classificacao.carregar(s2, dados)
        from views.pedido import dados_loja as dados_pedido
        dados_pedido._versao_catalogo.clear()      # a classificação nova vale já no Assistente e no Dashboard
        st.session_state["categorias_msg"] = (
            f"Fontes da classificação: {_milhar(r.eans)} EANs carregados em {r.segundos:.0f}s "
            f"({_milhar(r.com_febrafar)} com FEBRAFAR, {_milhar(r.com_cmed)} com CMED).")
        st.rerun()


def _catalogo_iqvia(armaz, chaves, usuario: dict) -> None:
    """Catálogo IQVIA (07/10/2026): linha de compra de cada EAN e genérico
    pelo GMRS. Mesmo caminho da base inicial: arquivo no Spaces, botão aqui."""
    from integrations import catalogo
    from views.pedido import dados_loja as dados_pedido

    st.markdown("###### Catálogo IQVIA (linhas de compra)")
    st.caption("Dá a linha de compra de cada produto (Genérico, Similar, Propagado, Perfumaria) para o "
               "Comprador no Assistente de pedido, e completa a base com os genéricos que FEBRAFAR e CMED não "
               "conhecem (nunca troca uma categoria que já existe).")
    with get_session() as session:
        total, _ultima = catalogo.versao(session)
    info = catalogo.disponivel(armaz, chaves)
    if info is None:
        st.info("O arquivo do catálogo ainda não está no armazenamento. Ele é gerado uma vez com "
                "`python -m pedido.carga_catalogo --catalogo <planilha> --executar`.")
        return
    st.caption(f"Arquivo pronto: {_milhar(int(info.get('eans', 0)))} EANs, gerado em "
               f"{str(info.get('gerado_em', '?'))[:10]}." + (f" Na base: {_milhar(total)} EANs." if total else ""))
    if st.button("Recarregar catálogo" if total else "Carregar catálogo", key="catalogo_carregar"):
        with st.spinner("Carregando o catálogo..."):
            dados = armaz.ler_df(chaves.catalogo_iqvia())
            with get_session() as s2:
                r = catalogo.carregar(s2, dados, usuario["nome"])
        _sem_classificacao_calculado.clear()
        dados_pedido._versao_catalogo.clear()
        st.session_state["categorias_msg"] = (
            f"Catálogo: {_milhar(r.eans)} EANs carregados em {r.segundos:.0f}s; "
            f"{_milhar(r.genericos_novos)} genérico(s) novo(s) na base de categorias.")
        st.rerun()
