"""Aba 'Dados' — só para o admin.

Quatro responsabilidades:
1. Painel de status das 3 integrações.
2. Explorador de Arquivos estilo Windows sobre o DigitalOcean Spaces (criar
   pasta, mover, renomear, inativar/reativar — nunca excluir de verdade).
3. Importação manual das planilhas de Gruppy (ofertas) e GPS (compras),
   enquanto essas duas integrações não tiverem API.
4. Filas de pendência que o motor de reconciliação e a ingestão do GPS geram
   (EAN não resolvido, CNPJ órfão) — nunca descartadas silenciosamente.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core import auth, rotinas, theme, ui
from core.config import settings
from core.db import get_session
from core.models import (
    BaseGenerico,
    FilaCnpjOrfao,
    FilaResolucaoEAN,
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemFila,
    RegistroCompraGPS,
    StatusFila,
    TabelaGruppy,
    TipoNode,
    UploadGPS,
)
from integrations import base_genericos as base_genericos_integ
from integrations import gps as gps_integ
from integrations import gps_processamento
from integrations import gruppy as gruppy_integ
from integrations import mapeamento as mapeamento_integ
from integrations import sistema_interno as loja_integ
from integrations.base import StatusIntegracao
from integrations.planilha_navegador import ler_rodape
from reconciliation import motor as reconciliation_motor
from storage import filesystem as fs
from views import analise_comum
from views import pedido as pedido_view
from views import leitor_planilha as leitor

logger = logging.getLogger(__name__)

_SEM_SELECAO = "— selecione —"

_LEITOR_GPS = "gps_leitor"
_LEITOR_GRUPPY = "gruppy_leitor"
_LEITOR_BASE = "base_genericos_leitor"
_LEITOR_CATEGORIAS = "categorias_leitor"
_LEITOR_CATEGORIAS_PENDENCIAS = "categorias_pendencias_leitor"


_ROTULOS_CAMPOS_GRUPPY = {
    "ean": "EAN",
    "descricao": "Descrição",
    "custo": "Custo líquido (já pronto)",
    "preco_bruto": "Preço bruto (de tabela)",
    "desconto": "Desconto (%)",
}

_ROTULOS_CAMPOS_GPS = {
    "cnpj": "CNPJ da loja",
    "ean": "EAN",
    "descricao": "Descrição",
    "quantidade": "Quantidade comprada (ex: Quantidade)",
    "custo_unitario": "Valor unitário sem ST (ex: VlrUnitario)",
}

_STATUS_BADGE = {
    StatusIntegracao.DISPONIVEL: ("sucesso", "API disponível"),
    StatusIntegracao.MANUAL: ("sugestao", "Manual (planilha)"),
    StatusIntegracao.INDISPONIVEL: ("sugestao", "Indisponível"),
}

_MESES = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro",
}


def _painel_integracoes() -> None:
    st.markdown("#### Status das integrações")
    adapters = [loja_integ.SistemaInternoLojasAdapter(), gruppy_integ.GruppyAdapter(), gps_integ.GpsAdapter()]
    cols = st.columns(3)
    for col, adapter in zip(cols, adapters):
        tipo, texto = _STATUS_BADGE[adapter.status()]
        with col:
            st.markdown(
                f"""<div class="rmc-kpi"><div class="label">{adapter.nome}</div>
                {theme.badge(texto, tipo)}</div>""",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Explorador de Arquivos
# ---------------------------------------------------------------------------

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


@st.dialog("Excluir definitivamente — Gruppy", width="large")
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
    if confirmar and st.button(
        "Excluir definitivamente", type="primary", key=f"exec_exclusao_gruppy_{fs_node_id}", use_container_width=True,
    ):
        with get_session() as session:
            resultado = gruppy_integ.excluir_tabela_gruppy_definitivamente(session, fs_node_id)
        aviso_purga = _purgar_arquivo_fisico(resultado.storage_key)
        st.session_state["explorador_exclusao_resultado"] = resultado
        if aviso_purga:
            st.session_state["explorador_exclusao_aviso_purga"] = aviso_purga
        st.rerun()


@st.dialog("Excluir definitivamente — GPS", width="large")
def _dialog_excluir_upload_gps(nome_arquivo: str, fs_node_id: int, ano_mes: str) -> None:
    with get_session() as session:
        qtd_registros = session.execute(
            select(func.count()).select_from(RegistroCompraGPS)
            .where(RegistroCompraGPS.upload_fs_node_id == fs_node_id)
        ).scalar_one()

    st.warning(
        f"Isso apaga DE VERDADE (não é inativar — não dá pra desfazer) o upload GPS de "
        f"**{ano_mes}**, do arquivo **{nome_arquivo}**: **{qtd_registros} registro(s)** de "
        "compra serão removidos junto, além do próprio arquivo no Explorador."
    )
    confirmar = st.checkbox(
        "Sim, entendi — quero excluir definitivamente este upload.", key=f"confirma_exclusao_gps_{fs_node_id}",
    )
    if confirmar and st.button(
        "Excluir definitivamente", type="primary", key=f"exec_exclusao_gps_{fs_node_id}", use_container_width=True,
    ):
        with get_session() as session:
            resultado = gps_integ.excluir_upload_gps_definitivamente(session, fs_node_id)
        # Dois bytes físicos: o .xlsx e o atalho de linhas órfãs gravado ao
        # lado dele (ver integrations/gps_cache_orfaos.py). Um aviso só é
        # suficiente — a causa de falha é a mesma (storage fora do ar) e o
        # desfecho também (arquivo órfão até uma limpeza manual).
        aviso_purga = (
            _purgar_arquivo_fisico(resultado.storage_key)
            or _purgar_arquivo_fisico(resultado.storage_key_orfaos)
        )
        st.session_state["explorador_exclusao_gps_resultado"] = resultado
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
        "todos os arquivos enviados ao sistema (planilhas GPS, Gruppy, Base Genéricos, categorias), em pastas.",
        "cada envio das outras sub-abas guarda o arquivo original aqui, sozinho.",
        "\"Inativar\" só esconde (vai para _Inativos). \"Excluir definitivamente\" apaga o arquivo e desfaz o "
        "envio (as compras ou preços que vieram dele).",
    )

    if "explorador_exclusao_resultado" in st.session_state:
        r = st.session_state.pop("explorador_exclusao_resultado")
        st.success(
            f"Tabela Gruppy \"{r.laboratorio}\" excluída definitivamente — "
            f"{r.itens_removidos} item(ns) de preço removidos."
        )
    if "explorador_exclusao_gps_resultado" in st.session_state:
        r = st.session_state.pop("explorador_exclusao_gps_resultado")
        st.success(
            f"Upload GPS \"{r.ano_mes}\" excluído definitivamente — "
            f"{r.registros_removidos} registro(s) de compra removidos."
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
        # TabelaGruppy.upload_fs_node_id) e GPS (elo via UploadGPS.fs_node_id)
        # — Base Genéricos não tem esse vínculo, nunca entra aqui.
        tabelas_gruppy_por_fs_node = {
            t.upload_fs_node_id: (t.id, t.laboratorio)
            for t in session.execute(
                select(TabelaGruppy).where(TabelaGruppy.upload_fs_node_id.is_not(None))
            ).scalars().all()
        }
        uploads_gps_por_fs_node = {
            u.fs_node_id: u.ano_mes
            for u in session.execute(select(UploadGPS)).scalars().all()
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

            if item.tipo == TipoNode.ARQUIVO and item.id in uploads_gps_por_fs_node:
                ano_mes = uploads_gps_por_fs_node[item.id]
                st.divider()
                if st.button("Excluir definitivamente", key=f"excluir_def_gps_{item.id}", use_container_width=True):
                    _dialog_excluir_upload_gps(item.nome, item.id, ano_mes)


# ---------------------------------------------------------------------------
# Importar Planilhas
# ---------------------------------------------------------------------------

def _subpasta(session, *caminho: str) -> int:
    """Arquivamento automático: garante (sem duplicar em re-uploads) a
    cadeia de subpastas e devolve o id da última — ex: Compras/GPS/2026-07."""
    node = fs.garantir_raiz(session)
    for nome in caminho:
        node = fs.obter_ou_criar_subpasta(session, node.id, nome, "sistema")
    return node.id


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


@st.dialog("Confirmar mapeamento de colunas — Gruppy", width="large")
def _dialog_mapeamento_gruppy(
    usuario: dict, laboratorio: str, ufs: list[str], modo_custo: ModoCustoGruppy,
) -> None:
    recebida = leitor.planilha_recebida(_LEITOR_GRUPPY)
    if recebida is None:
        st.info("Selecione a planilha de novo.")
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
        st.caption("Selecione uma coluna para todo campo obrigatório antes de confirmar.")

    if completo and st.button(
        "Confirmar e processar", key="map_gruppy_confirmar", type="primary", use_container_width=True
    ):
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
            st.error(f"Erro ao processar planilha: {exc}")


def _rotulo_ano_mes(ano_mes: str) -> str:
    return f"{_MESES.get(ano_mes[5:7], ano_mes[5:7])}/{ano_mes[:4]}"


@st.dialog("Confirmar envio — Compras GPS", width="large")
def _dialog_mapeamento_gps(usuario: dict, ano_mes: str) -> None:
    recebida = leitor.planilha_recebida(_LEITOR_GPS)
    if recebida is None:
        st.info("Selecione a planilha de novo.")
        return
    df = recebida.df
    colunas_planilha = [str(c) for c in df.columns]
    mapa_automatico = gps_integ.detectar_colunas_automatico(df)
    campos = gps_integ.CAMPOS_OBRIGATORIOS

    with get_session() as session:
        sugestao = mapeamento_integ.sugerir_mapeamento(session, OrigemFila.GPS, campos, colunas_planilha, mapa_automatico)
        uploads_existentes = gps_integ.listar_uploads_gps_no_mes(session, ano_mes)

    # Mês: quem envia informa; o rodapé do BI é só a conferência. Divergência
    # exige confirmação explícita — com a substituição por CNPJ, o mês errado
    # sobrescreveria em silêncio os dados bons de outro mês.
    rodape = ler_rodape(df)
    mes_confirmado = True
    if rodape.ano_mes and rodape.ano_mes != ano_mes:
        st.error(
            f"**Divergência de mês.** O rodapé do arquivo indica **{_rotulo_ano_mes(rodape.ano_mes)}**, "
            f"mas o mês escolhido foi **{_rotulo_ano_mes(ano_mes)}**. Se confirmar, as compras dos CNPJs "
            f"deste arquivo em {_rotulo_ano_mes(ano_mes)} serão substituídas por estas."
        )
        mes_confirmado = st.checkbox(
            f"Confirmo que este arquivo é de {_rotulo_ano_mes(ano_mes)}", key=f"gps_confirma_mes_{ano_mes}",
        )
    if rodape.exportacao_cortada:
        st.warning(
            "O rodapé do arquivo avisa que a exportação do BI **passou do limite de linhas e foi cortada** "
            "(\"Exported data exceeded the allowed volume\"). Pode estar faltando compra de alguma loja — "
            "confira o filtro da exportação. Dá pra continuar mesmo assim."
        )
    if uploads_existentes:
        st.info(
            f"{_rotulo_ano_mes(ano_mes)} já tem {len(uploads_existentes)} envio(s). Os CNPJs presentes neste "
            "arquivo terão as compras desse mês **substituídas**; os demais CNPJs continuam como estão."
        )

    st.caption("Confira qual coluna da planilha corresponde a cada campo antes de processar.")
    mapa_escolhido = _selecionar_mapeamento(campos, _ROTULOS_CAMPOS_GPS, colunas_planilha, sugestao, "map_gps")
    if len(mapa_escolhido) != len(campos):
        st.caption("Selecione uma coluna para todo campo obrigatório antes de confirmar.")
        return

    # Campos opcionais (laboratório, razão social) não passam pelo popup: vêm
    # da detecção automática, e o que a pessoa escolheu vence em conflito.
    mapa = {**mapa_automatico, **mapa_escolhido}
    try:
        preparadas = gps_integ.preparar_compras(df, mapa)
    except Exception as exc:
        st.error(f"Não consegui interpretar a planilha com esse mapeamento: {exc}")
        return
    st.markdown(
        f"**{len(preparadas.compras):,} compras** de **{len(preparadas.cnpjs_no_arquivo)} CNPJs** "
        f"serão gravadas em {_rotulo_ano_mes(ano_mes)}. "
        f"Ignoradas: {preparadas.linhas_sem_compra:,} linhas só de venda, "
        f"{preparadas.linhas_invalidas} com quantidade ou valor ≤ 0, "
        f"{preparadas.linhas_sem_chave} sem CNPJ/EAN (rodapé/total).".replace(",", ".")
    )

    if preparadas.compras.empty:
        st.error(
            "Nenhuma compra encontrada com esse mapeamento (linhas com CNPJ, EAN, VlrUnitario e Quantidade). "
            "Confira se esta é a exportação de compras do GPS — tabela Gruppy e Base Genéricos têm seções próprias."
        )
        return

    if st.button(
        "Confirmar e processar", key="map_gps_confirmar", type="primary", use_container_width=True,
        disabled=not mes_confirmado,
    ):
        with get_session() as session:
            pasta_id = _subpasta(session, "Compras", "GPS", ano_mes)
        iniciou, motivo = gps_processamento.iniciar(recebida, preparadas, mapa, ano_mes, pasta_id, usuario["nome"])
        if not iniciou:
            st.error(motivo)
            return
        leitor.consumir(_LEITOR_GPS)
        st.rerun()


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


@st.fragment(run_every=2)
def _acompanhar_processamento_gps() -> None:
    """Só existe na tela ENQUANTO há processamento: se refaz sozinho a cada
    2s (só este trecho, lendo o estado da memória, sem banco). Quando o
    processamento termina, refaz a página inteira uma vez e some — deixar a
    atualização periódica ligada o tempo todo faria um arquivo escolhido no
    meio de uma dessas atualizações parciais ter o evento descartado."""
    estado = gps_processamento.estado_atual()
    if estado is None or estado.concluido:
        st.rerun()
        return
    fracao = min(1.0, estado.feito / estado.total) if estado.total else 0.0
    detalhe = f" — {estado.feito:,}/{estado.total:,}".replace(",", ".") if estado.total > 1 else ""
    st.progress(
        fracao,
        text=f"Processando **{estado.nome_arquivo}** ({_rotulo_ano_mes(estado.ano_mes)}): {estado.fase}{detalhe}",
    )
    st.caption("Pode fechar esta aba ou mudar de tela — o processamento continua e o resultado fica registrado aqui.")


def _painel_processamento_gps() -> None:
    estado = gps_processamento.estado_atual()
    if estado is None or estado.lido_na_tela:
        return
    if not estado.concluido:
        _acompanhar_processamento_gps()
        return
    if estado.sucesso:
        st.success(estado.mensagem)
    else:
        st.error(
            f"O processamento de **{estado.nome_arquivo}** falhou e **nada foi alterado** "
            f"(os dados anteriores continuam valendo). Erro: {estado.erro}"
        )
    if st.button("Ok, entendi", key="gps_resultado_ok"):
        gps_processamento.marcar_resultado_visto()
        st.rerun()


def _ultimo_envio_gps() -> None:
    with get_session() as session:
        controle = rotinas.obter(session, rotinas.ROTINA_UPLOAD_GPS)
    if controle is None:
        return
    if controle.ultimo_sucesso_em is not None:
        st.caption(
            f"Último envio concluído: {controle.ultimo_sucesso_em:%d/%m/%Y %H:%M} (UTC) — "
            f"{controle.ultima_mensagem or ''}"
        )
    if controle.ultimo_erro:
        st.caption(f"Última falha registrada: {controle.ultimo_erro}")


def _importar_gps(usuario: dict) -> None:
    st.markdown("##### Compras das Lojas (GPS)")
    _explicacao(
        "o que cada loja comprou no mês (quantidade e valor sem ST) — o \"preço pago\" da Análise de Oportunidade.",
        "a planilha exportada do BI do GPS, um ou mais arquivos por mês (divididos por atributo de loja).",
        "envie de novo o mês: o envio **substitui, nesse mês, as compras dos CNPJs presentes no arquivo** — os "
        "demais continuam. Para desfazer: Explorador de Arquivos → o arquivo → Excluir definitivamente.",
    )

    _painel_processamento_gps()
    _ultimo_envio_gps()

    col_mes, col_ano = st.columns(2)
    with col_mes:
        mes_label = st.selectbox("Mês de referência", options=list(_MESES.values()), key="gps_mes")
    with col_ano:
        ano = st.number_input("Ano", min_value=2020, max_value=2035, value=dt.date.today().year, step=1, key="gps_ano")
    mes_num = next(k for k, v in _MESES.items() if v == mes_label)
    ano_mes = f"{int(ano):04d}-{mes_num}"

    leitor.leitor_planilha(_LEITOR_GPS, "Selecionar planilha GPS (.xlsx)")
    _mostrar_planilha_recebida(_LEITOR_GPS)

    estado = gps_processamento.estado_atual()
    em_andamento = estado is not None and not estado.concluido
    if leitor.planilha_recebida(_LEITOR_GPS) is not None and st.button(
        "Processar planilha GPS", key="gps_processar", disabled=em_andamento,
    ):
        _dialog_mapeamento_gps(usuario, ano_mes)


@st.dialog("Sincronização automática de lojas falhou", width="large")
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
    if st.button("Entendi", key="lojas_falha_fechar", use_container_width=True):
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


# ---------------------------------------------------------------------------
# Fila de Resolução de EAN
# ---------------------------------------------------------------------------

def _fila_ean() -> None:
    usuario = auth.usuario_atual()
    st.markdown("##### Fila de Resolução de EAN")
    _explicacao(
        "EANs que ainda não estão ligados a um genérico da Base Genéricos — ficam fora da análise e, no Pedido, "
        "não somam com os outros EANs do mesmo genérico.",
        "compras do GPS, tabelas da Gruppy e, desde 29/09/2026, genéricos vendidos pelas lojas (\"Loja (API)\", "
        "conferidos uma vez por dia). Prioridade: dinheiro em jogo (compra; na Loja (API), o vendido).",
        "confirme a sugestão, vincule a um genérico existente, cadastre um genérico novo ou ignore. Itens "
        "\"Loja (API)\" nunca se resolvem sozinhos: sempre pedem confirmação.",
    )
    if "fila_loja_resultado" in st.session_state:
        st.success(st.session_state.pop("fila_loja_resultado"))
    if st.button("Conferir agora os genéricos vendidos nas lojas", key="fila_loja_atualizar"):
        with st.spinner("Lendo os produtos das lojas..."):
            try:
                st.session_state["fila_loja_resultado"] = _atualizar_fila_loja()
            except Exception as exc:  # noqa: BLE001
                st.session_state["fila_loja_resultado"] = f"Não foi possível conferir agora: {exc}"
        st.rerun()

    if "fila_ean_reprocesso_resultado" in st.session_state:
        r = st.session_state.pop("fila_ean_reprocesso_resultado")
        st.success(
            f"Limpeza de EAN sujo: {r.limpeza.registros_compra_gps_corrigidos} em RegistroCompraGPS "
            f"({r.limpeza.registros_compra_gps_colisoes_puladas} pulados por colisão), "
            f"{r.limpeza.itens_tabela_gruppy_corrigidos} em ItemTabelaGruppy, "
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


# ---------------------------------------------------------------------------
# Fila de CNPJ Órfão
# ---------------------------------------------------------------------------

def _fila_cnpj_orfao() -> None:
    usuario = auth.usuario_atual()
    st.markdown("##### Fila de CNPJ Órfão")
    _explicacao(
        "CNPJs que apareceram numa compra do GPS mas não batem com nenhuma loja cadastrada.",
        "os envios de compras do GPS (as compras deles ficam guardadas, não se perdem).",
        "vincule a uma loja (as compras passam na hora, e os próximos envios já reconhecem) ou ignore.",
    )

    with get_session() as session:
        itens = gps_integ.listar_fila_cnpj_orfao_priorizada(session)
        lojas = session.execute(select(Loja).order_by(Loja.razao_social)).scalars().all()
        opcoes_loja_id_por_nome = {f"{l.razao_social} — {l.cnpj}": l.id for l in lojas}

        if not itens:
            st.markdown('<p class="rmc-muted">Nenhum CNPJ pendente.</p>', unsafe_allow_html=True)
            return

        if opcoes_loja_id_por_nome and st.button(
            "Resolver automaticamente (CNPJ idêntico)", key="resolver_lote_cnpj_identico"
        ):
            with get_session() as s2:
                resultado = gps_integ.resolver_cnpjs_orfaos_identicos_em_lote(s2, usuario["nome"])
            if resultado["resolvidos"]:
                st.success(
                    f"{resultado['resolvidos']} CNPJ(s) resolvido(s) automaticamente (CNPJ idêntico a uma "
                    f"loja já cadastrada) — {resultado['linhas_inseridas']} linha(s) de compra "
                    "importada(s)/atualizada(s). Os CNPJs sem correspondência exata continuam na fila."
                )
            else:
                st.info("Nenhum CNPJ pendente bate exatamente com uma loja cadastrada.")
            st.rerun()

        st.divider()

        for item in itens:
            titulo = f"{item.razao_social_observada or 'Razão social não informada'} — CNPJ {item.cnpj}"
            painel = st.expander(titulo, key=f"exp_fila_cnpj_{item.id}", on_change="rerun")
            if not painel.open:
                continue
            with painel:
                st.markdown(
                    f"{ui.formatar_moeda(float(item.valor_total_acumulado))} acumulado · "
                    f"{item.qtd_ocorrencias} ocorrência(s)"
                )

                if not opcoes_loja_id_por_nome:
                    st.caption("Nenhuma loja cadastrada ainda — sincronize a base de lojas antes de vincular.")
                else:
                    escolha = st.selectbox(
                        "Vincular à loja", options=["—"] + list(opcoes_loja_id_por_nome.keys()),
                        key=f"select_loja_{item.id}",
                    )
                    if escolha != "—" and st.button(
                        "Vincular à loja", key=f"vincular_cnpj_{item.id}", use_container_width=True
                    ):
                        with get_session() as s2:
                            total = gps_integ.resolver_cnpj_orfao(
                                s2, item.id, opcoes_loja_id_por_nome[escolha], usuario["nome"]
                            )
                        st.success(f"{total} compra(s) passada(s) para a loja.")
                        st.rerun()

                if st.button("Ignorar este CNPJ", key=f"ignorar_cnpj_{item.id}", use_container_width=True):
                    with get_session() as s2:
                        gps_integ.ignorar_cnpj_orfao(s2, item.id)
                    st.rerun()


# ---------------------------------------------------------------------------
# Vínculo de lojas GPS (Pedido)
# ---------------------------------------------------------------------------

def _explicacao(o_que_e: str, de_onde_vem: str, como_alterar: str) -> None:
    """As três linhas-padrão de cada item da aba Dados (decisão de 27/09/2026:
    com mais itens na aba, cada um diz o que é e como mexer, sempre igual)."""
    st.markdown(
        f"**O que é:** {o_que_e}  \n**De onde vem:** {de_onde_vem}  \n**Como alterar:** {como_alterar}"
    )


def _vinculo_lojas_gps() -> None:
    from core.models import SituacaoVinculoGps, VinculoLojaGps
    from integrations import gps_vinculo
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    usuario = auth.usuario_atual()
    st.markdown("##### Vínculo de lojas GPS")
    _explicacao(
        "qual loja do GPS é qual loja do RMC — é o que liga a aba Pedido às vendas, compras e estoque de cada loja.",
        "a rotina da madrugada grava as lojas do GPS; o sistema casa sozinho pelo CNPJ (quase todas) ou pelo "
        "número do endereço + nome (lojas que o GPS manda sem CNPJ).",
        "confirme as sugestões abaixo, troque a loja se estiver errada, ou marque \"Não é cliente\". "
        "O que você decidir aqui fica salvo e nunca é desfeito pelo recálculo.",
    )

    if st.button("Atualizar vínculos (ler as lojas do GPS)", key="vinculo_gps_atualizar"):
        lojas_gps = gps_vinculo.lojas_gps_salvas(do_ambiente(), Chaves(settings.pedido.prefixo))
        if not lojas_gps:
            st.info("A rotina do Pedido ainda não gravou nenhuma loja do GPS — ela roda de madrugada.")
        else:
            with get_session() as s2:
                r = gps_vinculo.recalcular(s2, lojas_gps)
            st.success(
                f"{r.lojas_gps} loja(s) do GPS: {r.automaticos} vinculada(s) automaticamente, "
                f"{r.a_confirmar} para confirmar, {r.sem_candidato} sem loja parecida no RMC"
                + (f", {r.decisoes_preservadas} decisão(ões) sua(s) mantida(s)." if r.decisoes_preservadas else ".")
            )

    with get_session() as session:
        vinculos = session.scalars(select(VinculoLojaGps).order_by(VinculoLojaGps.nome_loja_gps)).all()
        lojas = session.execute(select(Loja).order_by(Loja.razao_social)).scalars().all()
        rotulo_loja = {l.id: f"{l.razao_social} — {ui.formatar_cnpj(l.cnpj)}"
                       + (f" (cód. {l.legacy_id})" if l.legacy_id else "") for l in lojas}

        if not vinculos:
            st.markdown('<p class="rmc-muted">Nenhum vínculo calculado ainda.</p>', unsafe_allow_html=True)
            return

        por_situacao: dict = {}
        for v in vinculos:
            por_situacao.setdefault(v.situacao, []).append(v)
        st.caption(
            " · ".join(f"{rotulo}: {len(por_situacao.get(sit, []))}" for sit, rotulo in (
                (SituacaoVinculoGps.AUTOMATICO, "automáticos"), (SituacaoVinculoGps.CONFIRMADO, "confirmados"),
                (SituacaoVinculoGps.CONFIRMAR, "para confirmar"), (SituacaoVinculoGps.NAO_CLIENTE, "não são clientes"),
                (SituacaoVinculoGps.SEM_CANDIDATO, "sem loja parecida"),
            ))
        )
        st.divider()

        pendentes = por_situacao.get(SituacaoVinculoGps.CONFIRMAR, []) + por_situacao.get(SituacaoVinculoGps.SEM_CANDIDATO, [])
        if not pendentes:
            st.markdown('<p class="rmc-muted">Nada para confirmar.</p>', unsafe_allow_html=True)
        for v in pendentes:
            local = "/".join(x for x in (v.cidade_gps, v.uf_gps) if x)
            titulo = f"{v.nome_loja_gps or 'Loja sem nome'} — {local} · nº {v.numero_gps or 's/n'}"
            painel = st.expander(titulo, key=f"exp_vinc_gps_{v.id}", on_change="rerun")
            if not painel.open:
                continue
            with painel:
                st.caption(f"Por que está aqui: {v.motivo or '—'}")
                opcoes = ["—"] + list(rotulo_loja.keys())
                escolha = st.selectbox(
                    "Loja do RMC", options=opcoes, index=opcoes.index(v.loja_id) if v.loja_id in rotulo_loja else 0,
                    format_func=lambda i: "—" if i == "—" else rotulo_loja[i], key=f"vinc_gps_loja_{v.id}",
                )
                c1, c2 = st.columns(2)
                if c1.button("Confirmar vínculo", key=f"vinc_gps_ok_{v.id}", use_container_width=True,
                             disabled=escolha == "—"):
                    with get_session() as s2:
                        gps_vinculo.confirmar(s2, v.id, escolha, usuario["nome"])
                    st.rerun()
                if c2.button("Não é cliente", key=f"vinc_gps_nao_{v.id}", use_container_width=True):
                    with get_session() as s2:
                        gps_vinculo.marcar_nao_cliente(s2, v.id, usuario["nome"])
                    st.rerun()


# ---------------------------------------------------------------------------
# Categorias de produtos (Pedido)
# ---------------------------------------------------------------------------

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


def _categorias_importar() -> None:
    """Importar → Categorias de produtos: contagem, carga inicial e envio."""
    categorias_integ, categorias, armaz, chaves = _categorias_contexto()
    usuario = auth.usuario_atual()
    st.markdown("##### Categorias de produtos")
    _explicacao(
        "a categoria de cada EAN. Decide quantos dias de estoque o Pedido sugere (medicamento, perfumaria — "
        "ver Configurações de Pedidos); produto Sem Classificação não recebe sugestão.",
        "a lista da FEBRAFAR e a da CMED/ANVISA (já prontas no sistema, é só carregar uma vez) e as planilhas "
        "enviadas aqui para os produtos que elas não cobrem.",
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
                             for chave, nome in (("FEBRAFAR", "FEBRAFAR"), ("CMED", "CMED"), ("MANUAL", "Manual")))
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

    st.markdown("###### Enviar categorias")
    _envio_categorias(_LEITOR_CATEGORIAS)


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


def _sem_classificacao() -> None:
    """Pendências → Sem Classificação: relatório, exportação e envio de volta
    no mesmo lugar (ciclo fechado, decisão de 27/09/2026)."""
    from integrations import categorias as categorias_integ

    st.markdown("##### Produtos Sem Classificação")
    _explicacao(
        "produtos das lojas sem categoria (ou \"em classificação\" na FEBRAFAR). No Pedido eles aparecem sem "
        "sugestão.",
        "os produtos que a rotina da madrugada traz do estoque de cada loja, comparados com a base de categorias.",
        "baixe a lista, preencha a coluna CATEGORIA e envie o mesmo arquivo aqui embaixo. Vale na hora.",
    )
    _mensagem_categorias()
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
    st.markdown("###### Enviar a lista preenchida")
    _envio_categorias(_LEITOR_CATEGORIAS_PENDENCIAS)


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
        st.session_state["categorias_msg"] = f"{_milhar(gravados)} categoria(s) gravada(s). Já valem no Pedido."
        st.rerun()


# ---------------------------------------------------------------------------
# Fila de EAN "Loja (API)"
# ---------------------------------------------------------------------------

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


def _fila_loja_automatica() -> None:
    """Uma vez por dia, na primeira abertura da aba Dados (mesmo esquema da
    sincronização de lojas). Falha não quebra a tela: vira aviso na fila."""
    if st.session_state.get("_fila_loja_ja_verificada"):
        return
    st.session_state["_fila_loja_ja_verificada"] = True
    with st.spinner("Conferindo os genéricos vendidos nas lojas (primeira abertura do dia)..."):
        rotinas.executar_se_necessario(rotinas.ROTINA_FILA_EAN_LOJA_API, _atualizar_fila_loja)


# ---------------------------------------------------------------------------
# Rotinas
# ---------------------------------------------------------------------------

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
    st.dataframe(pd.DataFrame([{
        "Parte": r.get("parte"),
        "Empresas": f"{r.get('empresas_processadas', 0)}/{r.get('empresas_na_parte', 0)}",
        "Lojas": r.get("lojas", 0),
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


# ---------------------------------------------------------------------------
# As quatro sub-abas
# ---------------------------------------------------------------------------

_REORGANIZADA_ATE = dt.date(2026, 10, 31)


def _aviso_reorganizacao() -> None:
    """Aviso de onde cada coisa foi parar (Q35 de 27/09/2026), até o fim de
    outubro/2026 ou até clicar em "Ok, entendi" na sessão."""
    if dt.date.today() > _REORGANIZADA_ATE or st.session_state.get("_aviso_dados_visto"):
        return
    with st.container(border=True):
        st.markdown(
            "**A aba Dados foi reorganizada.** As filas (EAN, CNPJ órfão), o vínculo de lojas GPS e os produtos "
            "sem classificação estão em **Pendências**. A sincronização de lojas e a rotina do GPS, em "
            "**Rotinas**. Os envios de planilha continuam em **Importar**."
        )
        if st.button("Ok, entendi", key="aviso_dados_ok"):
            st.session_state["_aviso_dados_visto"] = True
            st.rerun()


@st.cache_data(ttl=60, show_spinner=False)
def _contadores_banco() -> dict:
    from core.models import SituacaoVinculoGps, VinculoLojaGps

    with get_session() as session:
        return {
            "ean": session.scalar(select(func.count()).select_from(FilaResolucaoEAN)
                                  .where(FilaResolucaoEAN.status == StatusFila.PENDENTE)) or 0,
            "cnpj": session.scalar(select(func.count()).select_from(FilaCnpjOrfao)
                                   .where(FilaCnpjOrfao.status == StatusFila.PENDENTE)) or 0,
            "vinculo": session.scalar(select(func.count()).select_from(VinculoLojaGps).where(
                VinculoLojaGps.situacao.in_([SituacaoVinculoGps.CONFIRMAR, SituacaoVinculoGps.SEM_CANDIDATO]))) or 0,
        }


_PENDENCIAS = {
    "ean": ("Fila de EAN", _fila_ean),
    "cnpj": ("CNPJ órfão", _fila_cnpj_orfao),
    "vinculo": ("Vínculo de lojas GPS", _vinculo_lojas_gps),
    "sem_classificacao": ("Sem Classificação", _sem_classificacao),
}


def _pendencias() -> None:
    contadores = dict(_contadores_banco())
    try:
        relatorio, _total = _sem_classificacao_calculado(_versao_categorias())
        contadores["sem_classificacao"] = int((relatorio["LOJAS COM ESTOQUE"] > 0).sum()) if not relatorio.empty else 0
    except Exception:  # noqa: BLE001 — sem Spaces/catálogo: o contador fica em branco
        contadores["sem_classificacao"] = None

    def rotulo(chave: str) -> str:
        n = contadores.get(chave)
        return _PENDENCIAS[chave][0] + ("" if n is None else f" ({_milhar(n)})")

    escolhida = st.segmented_control(
        "O que resolver", options=list(_PENDENCIAS), format_func=rotulo, default="ean",
        key="dados_pendencia", label_visibility="collapsed",
    ) or "ean"
    st.divider()
    _PENDENCIAS[escolhida][1]()


def _importar() -> None:
    usuario = auth.usuario_atual()
    _importar_base_genericos(usuario)
    st.divider()
    _importar_gruppy(usuario)
    st.divider()
    _importar_gps(usuario)
    st.divider()
    _categorias_importar()


def _rotinas_aba() -> None:
    _sincronizar_lojas()
    st.divider()
    _rotina_gps()
    st.divider()
    _fontes_categorias()


def render() -> None:
    with theme.tela("dados"):
        _render()


def _render() -> None:
    theme.cabecalho("Dados", "Pendências, importação de planilhas, rotinas automáticas e arquivos.")
    # Toda ação que muda os dados da análise (envios, EAN/CNPJ resolvido,
    # exclusões) acontece nesta aba: descartar o cálculo guardado aqui faz
    # as telas de análise sempre refletirem o que acabou de mudar.
    analise_comum.invalidar()
    pedido_view.invalidar()  # categorias/genéricos mudam aqui também
    _contadores_banco.clear()
    _sincronizar_lojas_automatico()
    _fila_loja_automatica()
    _painel_integracoes()
    st.markdown(f'<p class="rmc-muted">Armazenamento: {fs.modo_storage()}</p>', unsafe_allow_html=True)
    _aviso_reorganizacao()

    # Abas preguiçosas: só a aba aberta é calculada. Sem isso, qualquer clique
    # (inclusive escolher uma planilha) redesenhava todas — e as filas, com
    # centenas de itens, custavam segundos por clique. Organização de
    # 27/09/2026 (Q35): pelo que o admin está fazendo.
    aba_pendencias, aba_importar, aba_rotinas, aba_explorador = st.tabs(
        ["Pendências", "Importar", "Rotinas", "Explorador de Arquivos"], key="dados_abas", on_change="rerun",
    )
    if aba_pendencias.open:
        with aba_pendencias:
            _pendencias()
    if aba_importar.open:
        with aba_importar:
            _importar()
    if aba_rotinas.open:
        with aba_rotinas:
            _rotinas_aba()
    if aba_explorador.open:
        with aba_explorador:
            _explorador()
