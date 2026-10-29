"""Aba Dados → Explorador de Arquivos (Spaces/disco; nada se apaga, "inativar" move pra `_Inativos`)."""
from __future__ import annotations

import logging

import streamlit as st
from sqlalchemy import func, select

from core import auth, theme
from core.db import get_session
from core.models import ItemTabelaGruppy, TabelaGruppy, TipoNode
from integrations import gruppy as gruppy_integ
from storage import filesystem as fs
from views import popups
from views.dados.comum import _explicacao

logger = logging.getLogger(__name__)


def _pasta_atual_id() -> int:
    if "dados_pasta_atual_id" not in st.session_state:
        with get_session() as session:
            st.session_state["dados_pasta_atual_id"] = fs.garantir_raiz(session).id
    return st.session_state["dados_pasta_atual_id"]


def _icone(node) -> str:
    return "📁" if node.tipo == TipoNode.PASTA else "📄"


def _purgar_arquivo_fisico(storage_key: str | None) -> str | None:
    """Chamada depois que o `with get_session()` do chamador já fechou (ou
    seja, depois do commit confirmado) — nunca antes, pra nunca apagar o
    byte físico de uma exclusão de banco que ainda pode dar rollback (ver
    docstring de `excluir_tabela_gruppy_definitivamente`/
    `excluir_upload_gps_definitivamente`). `storage_key=None` (FSNode já não
    existia) é um no-op silencioso.

    Devolve uma mensagem de aviso se a purga falhar (None se deu certo ou não
    havia nada pra purgar) — o chamador guarda em session_state pra mostrar
    DEPOIS do st.rerun() que segue a exclusão; um st.warning chamado aqui
    seria descartado pelo rerun antes do usuário ver. Uma falha aqui (ex:
    Spaces fora do ar naquele instante) não desfaz a exclusão do banco, que
    já está commitada — só avisa, porque o arquivo fica órfão no Spaces até
    uma limpeza manual, mas os dados (o que realmente importa) já foram
    removidos."""
    if not storage_key:
        return None
    try:
        fs.backend().excluir_fisicamente(storage_key)
        return None
    except Exception:
        logger.exception(
            "Falha ao purgar arquivo físico do Spaces (storage_key=%s) após exclusão definitiva.", storage_key
        )
        return (
            "Os dados foram excluídos definitivamente, mas não foi possível remover o arquivo "
            "físico do armazenamento agora — ele ficará órfão até uma limpeza manual."
        )


@popups.popup("Excluir definitivamente — Gruppy", "aviso")
def _dialog_excluir_tabela_gruppy(nome_arquivo: str, fs_node_id: int, tabela_id: int, laboratorio: str) -> None:
    with get_session() as session:
        qtd_itens = session.execute(
            select(func.count()).select_from(ItemTabelaGruppy)
            .where(ItemTabelaGruppy.tabela_gruppy_id == tabela_id)
        ).scalar_one()

    st.warning(
        f"Isso apaga DE VERDADE (não é inativar — não dá pra desfazer) a tabela de preço "
        f"**{laboratorio}**, do arquivo **{nome_arquivo}**: **{qtd_itens} item(ns)** de preço "
        "serão removidos junto, além do próprio arquivo no Explorador."
    )
    confirmar = st.checkbox(
        "Sim, entendi — quero excluir definitivamente esta tabela.", key=f"confirma_exclusao_gruppy_{fs_node_id}",
    )
    cancelar, excluir = popups.botoes("Excluir definitivamente", chave=f"exclusao_gruppy_{fs_node_id}",
                                      destrutivo=True, desabilitado=not confirmar)
    if cancelar:
        st.rerun()
    if excluir and confirmar:
        with get_session() as session:
            resultado = gruppy_integ.excluir_tabela_gruppy_definitivamente(session, fs_node_id)
        aviso_purga = _purgar_arquivo_fisico(resultado.storage_key)
        st.session_state["explorador_exclusao_resultado"] = resultado
        if aviso_purga:
            st.session_state["explorador_exclusao_aviso_purga"] = aviso_purga
        st.rerun()


def _baixar_arquivo_ui(item: fs.FSNode) -> None:
    """O corpo de um `with popover(...):` roda a cada rerun mesmo com o
    popover fechado — antes disso, `fs.ler_arquivo(item)` era chamado pra
    TODO arquivo listado na pasta, todo rerun, mesmo que ninguém abrisse o
    popover daquela linha (pior ainda numa pasta com muitos arquivos grandes).

    Caminho preferido: se o backend suporta URL pré-assinada (Spaces), o
    navegador baixa direto de lá — o conteúdo nunca entra na memória do
    processo Streamlit. Fallback (storage local de dev, sem endpoint HTTP):
    um botão "Preparar download" guarda o id em session_state e só lê o
    arquivo no rerun seguinte, exibindo aí o download_button real — ou seja,
    o `ler_arquivo` só roda quando o usuário pediu, e só pra aquele item."""
    url = fs.backend().url_assinada(item.storage_key)
    if url is not None:
        st.link_button("Baixar", url, use_container_width=True)
        return

    if st.session_state.get("dados_baixar_pendente_id") == item.id:
        conteudo = fs.ler_arquivo(item)
        st.download_button(
            "Baixar", data=conteudo, file_name=item.nome, key=f"baixar_{item.id}", use_container_width=True,
        )
        st.session_state.pop("dados_baixar_pendente_id", None)
    elif st.button("Preparar download", key=f"preparar_baixar_{item.id}", use_container_width=True):
        st.session_state["dados_baixar_pendente_id"] = item.id
        st.rerun()


def _explorador() -> None:
    usuario = auth.usuario_atual()
    pasta_id = _pasta_atual_id()
    _explicacao(
        "todos os arquivos enviados ao sistema (Gruppy, Base Genéricos, categorias e as planilhas do GPS "
        "de antes da API), em pastas.",
        "cada envio das outras sub-abas guarda o arquivo original aqui, sozinho.",
        "\"Inativar\" só esconde (vai para _Inativos). Na tabela Gruppy, \"Excluir definitivamente\" apaga o "
        "arquivo e os preços que vieram dele.",
    )

    if "explorador_exclusao_resultado" in st.session_state:
        r = st.session_state.pop("explorador_exclusao_resultado")
        st.success(
            f"Tabela Gruppy \"{r.laboratorio}\" excluída definitivamente — "
            f"{r.itens_removidos} item(ns) de preço removidos."
        )
    if "explorador_exclusao_aviso_purga" in st.session_state:
        st.warning(st.session_state.pop("explorador_exclusao_aviso_purga"))

    with get_session() as session:
        pasta_atual = session.get(fs.FSNode, pasta_id)
        if pasta_atual is None:
            pasta_atual = fs.garantir_raiz(session)
            st.session_state["dados_pasta_atual_id"] = pasta_atual.id
        breadcrumb = fs.caminho_completo(session, pasta_atual)
        breadcrumb_info = [(n.id, n.nome) for n in breadcrumb]

    st.markdown("##### Local atual")
    bc_cols = st.columns(len(breadcrumb_info) + 1)
    for i, (col, (node_id, nome)) in enumerate(zip(bc_cols, breadcrumb_info)):
        with col:
            e_ultimo = i == len(breadcrumb_info) - 1
            st.markdown(f'<div class="bc-btn{"-ativo" if e_ultimo else ""}"></div>', unsafe_allow_html=True)
            if st.button(nome, key=f"bc_{node_id}", use_container_width=True, disabled=e_ultimo):
                st.session_state["dados_pasta_atual_id"] = node_id
                st.rerun()

    col_nova, col_btn = st.columns([3, 1])
    with col_nova:
        nome_nova_pasta = st.text_input(
            "Nova pasta", key="dados_nova_pasta", label_visibility="collapsed", placeholder="Nome da nova pasta"
        )
    with col_btn:
        if st.button("Criar pasta", use_container_width=True) and nome_nova_pasta.strip():
            with get_session() as session:
                fs.criar_pasta(session, pasta_id, nome_nova_pasta.strip(), usuario["nome"])
            st.rerun()

    mostrar_inativos = st.toggle("Mostrar itens inativos desta pasta", value=False, key="dados_mostrar_inativos")

    with get_session() as session:
        itens = fs.listar_conteudo(session, pasta_id, incluir_inativos=mostrar_inativos)
        pastas_para_mover = fs.listar_todas_pastas(session)
        opcoes_mover = {fs.caminho_texto(session, p): p.id for p in pastas_para_mover}
        # "Excluir definitivamente" existe pra arquivo Gruppy (elo via
        # TabelaGruppy.upload_fs_node_id). As planilhas GPS antigas ficam como
        # histórico (05/10/2026): as compras delas saem pelo script de limpeza.
        tabelas_gruppy_por_fs_node = {
            t.upload_fs_node_id: (t.id, t.laboratorio)
            for t in session.execute(
                select(TabelaGruppy).where(TabelaGruppy.upload_fs_node_id.is_not(None))
            ).scalars().all()
        }

    if not itens:
        st.markdown('<p class="rmc-muted">Pasta vazia.</p>', unsafe_allow_html=True)
        return

    header = st.columns([3, 1, 1, 1.6, 1.6])
    for col, titulo in zip(header, ["Nome", "Tipo", "Tamanho", "Criado por", "Ações"]):
        col.markdown(f"**{titulo}**")

    for item in itens:
        c = st.columns([3, 1, 1, 1.6, 1.6])
        rotulo = f"{_icone(item)} {item.nome}"
        if item.status.value == "inativo":
            rotulo += "  " + theme.badge("inativo", "inativo")
        if item.tipo == TipoNode.PASTA and item.status.value == "ativo":
            if c[0].button(rotulo, key=f"abrir_{item.id}"):
                st.session_state["dados_pasta_atual_id"] = item.id
                st.rerun()
        else:
            c[0].markdown(rotulo, unsafe_allow_html=True)

        c[1].write("Pasta" if item.tipo == TipoNode.PASTA else "Arquivo")
        c[2].write(f"{item.tamanho_bytes/1024:.1f} KB" if item.tamanho_bytes else "—")
        c[3].write(item.criado_por or "—")

        with c[4].popover("Ações", use_container_width=True):
            if item.tipo == TipoNode.ARQUIVO and item.status.value == "ativo":
                _baixar_arquivo_ui(item)

            if item.status.value == "ativo":
                novo_nome = st.text_input("Renomear para", value=item.nome, key=f"renomear_txt_{item.id}")
                if st.button("Salvar novo nome", key=f"renomear_btn_{item.id}", use_container_width=True):
                    with get_session() as session:
                        fs.renomear(session, item.id, novo_nome)
                    st.rerun()

                destino_label = st.selectbox(
                    "Mover para", options=list(opcoes_mover.keys()), key=f"mover_sel_{item.id}"
                )
                if st.button("Mover", key=f"mover_btn_{item.id}", use_container_width=True):
                    with get_session() as session:
                        fs.mover(session, item.id, opcoes_mover[destino_label])
                    st.rerun()

                if st.button("Inativar", key=f"inativar_{item.id}", use_container_width=True):
                    with get_session() as session:
                        fs.inativar(session, item.id)
                    st.rerun()
            else:
                if st.button("Reativar", key=f"reativar_{item.id}", use_container_width=True):
                    with get_session() as session:
                        fs.reativar(session, item.id)
                    st.rerun()

            if item.tipo == TipoNode.ARQUIVO and item.id in tabelas_gruppy_por_fs_node:
                tabela_id, laboratorio = tabelas_gruppy_por_fs_node[item.id]
                st.divider()
                if st.button("Excluir definitivamente", key=f"excluir_def_{item.id}", use_container_width=True):
                    _dialog_excluir_tabela_gruppy(item.nome, item.id, tabela_id, laboratorio)
