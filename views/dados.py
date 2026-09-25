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
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemFila,
    RegistroCompraGPS,
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
from views import leitor_planilha as leitor

logger = logging.getLogger(__name__)

_SEM_SELECAO = "— selecione —"

_LEITOR_GPS = "gps_leitor"
_LEITOR_GRUPPY = "gruppy_leitor"
_LEITOR_BASE = "base_genericos_leitor"


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
    st.caption(
        "Cada upload é a tabela de UM laboratório. A vigência é granular por UF: subir uma "
        "tabela nova só substitui (inativa) as UFs que se repetem — as demais continuam valendo "
        "da tabela anterior."
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
    st.caption(
        "Cada envio cobre um mês e **substitui, nesse mês, as compras dos CNPJs presentes no arquivo** — "
        "os demais CNPJs continuam como estão (dá pra dividir a exportação por UF e enviar em partes). "
        "Entram as linhas com VlrUnitario e Quantidade preenchidos (valor de compra sem ST); linhas só de "
        "venda são ignoradas."
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
    st.caption(
        "Traz CNPJ, razão social, UF, cidade e time de atendimento direto da API do sistema "
        "interno. CNPJ do GPS que ainda não tem loja fica na fila de CNPJ órfão e passa pra loja quando "
        "ela for vinculada — a ordem entre sincronizar lojas e enviar o GPS não importa."
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
    st.caption(
        "Importação em massa de EAN já revisados por gente, ligados ao nome canônico do "
        "genérico — sem passar pelo fuzzy-match. EAN que já tem resolução (automática, manual "
        "ou de uma importação anterior) nunca é sobrescrito, só pulado."
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


def _importar_planilhas() -> None:
    usuario = auth.usuario_atual()
    _sincronizar_lojas()
    st.divider()
    _importar_base_genericos(usuario)
    st.divider()
    _importar_gruppy(usuario)
    st.divider()
    _importar_gps(usuario)


# ---------------------------------------------------------------------------
# Fila de Resolução de EAN
# ---------------------------------------------------------------------------

def _fila_ean() -> None:
    usuario = auth.usuario_atual()
    st.markdown("##### Fila de Resolução de EAN")
    st.caption(
        "Priorizada pelo valor de compra em jogo (quantidade × valor unitário nas compras vigentes) — "
        "recalculado a cada envio, nunca somado envio a envio."
    )

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

    origem_opcoes = {"Todos": None, "GPS": OrigemFila.GPS, "Gruppy": OrigemFila.GRUPPY}
    origem_label = st.selectbox("Origem", options=list(origem_opcoes.keys()), key="fila_ean_origem_filtro")

    with get_session() as session:
        itens = reconciliation_motor.listar_fila_priorizada(session, origem_filtro=origem_opcoes[origem_label])
        genericos = session.execute(
            select(BaseGenerico).where(BaseGenerico.ativo.is_(True)).order_by(BaseGenerico.nome_canonico)
        ).scalars().all()
        opcoes_generico_id_por_nome = {g.nome_canonico: g.id for g in genericos}
        nome_generico_por_id = {g.id: g.nome_canonico for g in genericos}

        if not itens:
            st.markdown('<p class="rmc-muted">Nenhum item pendente na fila.</p>', unsafe_allow_html=True)
            return

        for item in itens:
            # Expander preguiçoso: o seletor com todos os genéricos (milhares
            # de opções) só é montado no item aberto — antes era montado em
            # TODOS os itens listados, a cada clique na tela.
            painel = st.expander(
                f"{item.descricao_observada}  —  EAN {item.ean}", key=f"exp_fila_ean_{item.id}", on_change="rerun",
            )
            if not painel.open:
                continue
            with painel:
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
    st.caption(
        "CNPJs que apareceram numa compra do GPS mas não batem com nenhuma loja cadastrada. "
        "Vincular a uma loja passa na hora as compras desse CNPJ para a loja — e os próximos envios "
        "já reconhecem o vínculo."
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


def render() -> None:
    with theme.tela("dados"):
        _render()


def _render() -> None:
    theme.cabecalho("Dados", "Status das integrações, importação de planilhas e filas de pendência.")
    # Toda ação que muda os dados da análise (envios, EAN/CNPJ resolvido,
    # exclusões) acontece nesta aba: descartar o cálculo guardado aqui faz
    # as telas de análise sempre refletirem o que acabou de mudar.
    analise_comum.invalidar()
    _sincronizar_lojas_automatico()
    _painel_integracoes()
    st.markdown(f'<p class="rmc-muted">Armazenamento: {fs.modo_storage()}</p>', unsafe_allow_html=True)

    # Abas preguiçosas: só a aba aberta é calculada. Sem isso, qualquer clique
    # (inclusive escolher uma planilha) redesenhava as quatro — e as filas,
    # com centenas de itens, custavam segundos por clique.
    aba_explorador, aba_importar, aba_fila_ean, aba_fila_cnpj = st.tabs(
        ["Explorador de Arquivos", "Importar Planilhas", "Fila de Resolução de EAN", "Fila de CNPJ Órfão"],
        key="dados_abas", on_change="rerun",
    )
    if aba_explorador.open:
        with aba_explorador:
            _explorador()
    if aba_importar.open:
        with aba_importar:
            _importar_planilhas()
    if aba_fila_ean.open:
        with aba_fila_ean:
            _fila_ean()
    if aba_fila_cnpj.open:
        with aba_fila_cnpj:
            _fila_cnpj_orfao()
