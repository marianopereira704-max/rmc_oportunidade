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
import hashlib
import io

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core import auth, theme, ui
from core.config import settings
from core.db import get_session
from core.models import BaseGenerico, ItemTabelaGruppy, Loja, ModoCustoGruppy, OrigemFila, TabelaGruppy, TipoNode
from integrations import base_genericos as base_genericos_integ
from integrations import gps as gps_integ
from integrations import gruppy as gruppy_integ
from integrations import mapeamento as mapeamento_integ
from integrations import sistema_interno as loja_integ
from integrations.base import StatusIntegracao
from reconciliation import motor as reconciliation_motor
from storage import filesystem as fs

_SEM_SELECAO = "— selecione —"

_CACHE_DF_GRUPPY = "_cache_df_gruppy"
_CACHE_DF_GPS = "_cache_df_gps"


def _ler_planilha_cacheada(chave_cache: str, arquivo_bytes: bytes) -> pd.DataFrame:
    """Evita reparsear a mesma planilha do zero toda vez que o popup de
    mapeamento rerenderiza (a cada interação — trocar uma coluna no
    selectbox, por exemplo) e de novo dentro do `processar_planilha_*`
    (ver chamada abaixo, que agora passa `df=` em vez de deixar reler) —
    numa planilha GPS real de 150 mil linhas, só o parse já custa ~19s.

    Cache de slot ÚNICO por tipo (`chave_cache`), guardado com o fingerprint
    (hash) do conteúdo: um upload de arquivo DIFERENTE não acumula no
    session_state, só substitui o slot — sem isso, cada novo upload dentro
    da mesma sessão do navegador ia empilhando DataFrames grandes na memória
    do processo Streamlit indefinidamente."""
    fingerprint = hashlib.md5(arquivo_bytes).hexdigest()
    cache = st.session_state.get(chave_cache)
    if cache is not None and cache[0] == fingerprint:
        return cache[1]
    df = pd.read_excel(io.BytesIO(arquivo_bytes))
    st.session_state[chave_cache] = (fingerprint, df)
    return df

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
    "quantidade": "Quantidade comprada",
    "fat_liquido": "Faturamento líquido",
    "pct_cmv": "% de CMV",
    "estoque": "Estoque atual",
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
        st.session_state["explorador_exclusao_resultado"] = resultado
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
        # "Excluir definitivamente" só existe pra arquivo Gruppy (tem elo de
        # volta pro FSNode via TabelaGruppy.upload_fs_node_id) — GPS/Base
        # Genéricos não têm esse vínculo, então nunca entram aqui.
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
                conteudo = fs.ler_arquivo(item)
                st.download_button(
                    "Baixar", data=conteudo, file_name=item.nome, key=f"baixar_{item.id}", use_container_width=True
                )

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
    usuario: dict, arquivo_bytes: bytes, nome_arquivo: str, laboratorio: str, ufs: list[str],
    modo_custo: ModoCustoGruppy,
) -> None:
    df_preview = _ler_planilha_cacheada(_CACHE_DF_GRUPPY, arquivo_bytes)
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
            with get_session() as session:
                pasta_id = _subpasta(session, "Ofertas", "Gruppy")
                resultado = gruppy_integ.processar_planilha_gruppy(
                    session, arquivo_bytes, nome_arquivo, usuario["nome"], pasta_id,
                    laboratorio=laboratorio, ufs=ufs, modo_custo=modo_custo, mapa_confirmado=mapa_escolhido,
                    df=df_preview,
                )
                mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GRUPPY, mapa_escolhido, usuario["nome"])
            st.session_state.pop(_CACHE_DF_GRUPPY, None)
            st.session_state["gruppy_upload_sucesso"] = resultado.mensagem
            st.rerun()
        except Exception as exc:
            st.error(f"Erro ao processar planilha: {exc}")


@st.dialog("Confirmar mapeamento de colunas — GPS", width="large")
def _dialog_mapeamento_gps(usuario: dict, arquivo_bytes: bytes, nome_arquivo: str, ano_mes: str) -> None:
    df_preview = _ler_planilha_cacheada(_CACHE_DF_GPS, arquivo_bytes)
    colunas_planilha = [str(c) for c in df_preview.columns]
    mapa_automatico = gps_integ.detectar_colunas_automatico(df_preview)
    campos = gps_integ.CAMPOS_OBRIGATORIOS

    with get_session() as session:
        sugestao = mapeamento_integ.sugerir_mapeamento(
            session, OrigemFila.GPS, campos, colunas_planilha, mapa_automatico,
        )

    st.caption("Confira qual coluna da planilha corresponde a cada campo antes de processar.")
    mapa_escolhido = _selecionar_mapeamento(campos, _ROTULOS_CAMPOS_GPS, colunas_planilha, sugestao, "map_gps")

    completo = len(mapa_escolhido) == len(campos)
    if not completo:
        st.caption("Selecione uma coluna para todo campo obrigatório antes de confirmar.")

    if completo and st.button(
        "Confirmar e processar", key="map_gps_confirmar", type="primary", use_container_width=True
    ):
        try:
            with get_session() as session:
                pasta_id = _subpasta(session, "Compras", "GPS", ano_mes)
                resultado = gps_integ.processar_planilha_gps(
                    session, arquivo_bytes, nome_arquivo, usuario["nome"], pasta_id,
                    ano_mes=ano_mes, mapa_confirmado=mapa_escolhido, df=df_preview,
                )
                mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GPS, mapa_escolhido, usuario["nome"])
            st.session_state.pop(_CACHE_DF_GPS, None)
            st.session_state["gps_upload_sucesso"] = resultado.mensagem
            st.rerun()
        except Exception as exc:
            st.error(f"Erro ao processar planilha: {exc}")


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

    arquivo = st.file_uploader("Planilha Gruppy (.xlsx)", type=["xlsx", "xls"], key="gruppy_upload")

    pronto = bool(arquivo and laboratorio and ufs)
    if arquivo and not (laboratorio and ufs):
        st.caption("Selecione o laboratório e ao menos uma UF antes de processar.")

    if pronto and st.button("Processar planilha Gruppy", key="gruppy_processar"):
        _dialog_mapeamento_gruppy(usuario, arquivo.getvalue(), arquivo.name, laboratorio, ufs, modo_custo)


def _importar_gps(usuario: dict) -> None:
    st.markdown("##### Compras das Lojas (GPS)")
    st.caption(
        "Cada upload cobre um mês de compras. O arquivo já traz o estoque atual na mesma linha "
        "da compra (QtdEstoque) — não é um upload separado."
    )

    if "gps_upload_sucesso" in st.session_state:
        st.success(st.session_state.pop("gps_upload_sucesso"))

    col_mes, col_ano = st.columns(2)
    with col_mes:
        mes_label = st.selectbox("Mês de referência", options=list(_MESES.values()), key="gps_mes")
    with col_ano:
        ano = st.number_input("Ano", min_value=2020, max_value=2035, value=dt.date.today().year, step=1, key="gps_ano")
    mes_num = next(k for k, v in _MESES.items() if v == mes_label)
    ano_mes = f"{int(ano):04d}-{mes_num}"

    arquivo = st.file_uploader("Planilha GPS (.xlsx)", type=["xlsx", "xls"], key="gps_upload")

    if arquivo and st.button("Processar planilha GPS", key="gps_processar"):
        _dialog_mapeamento_gps(usuario, arquivo.getvalue(), arquivo.name, ano_mes)


def _sincronizar_lojas() -> None:
    st.markdown("##### Base de Lojas (sistema interno)")
    st.caption(
        "Traz CNPJ, razão social, UF, cidade e time de atendimento direto da API do sistema "
        "interno — precisa rodar antes do upload do GPS pra ter loja cadastrada pra bater o CNPJ."
    )
    if st.button("Sincronizar lojas", key="lojas_sincronizar"):
        try:
            resultado = loja_integ.SistemaInternoLojasAdapter().sincronizar()
            st.success(resultado.mensagem)
        except Exception as exc:
            st.error(f"Erro ao sincronizar lojas: {exc}")


def _importar_base_genericos(usuario: dict) -> None:
    st.markdown("##### Base Genéricos (planilha curada)")
    st.caption(
        "Importação em massa de EAN já revisados por gente, ligados ao nome canônico do "
        "genérico — sem passar pelo fuzzy-match. EAN que já tem resolução (automática, manual "
        "ou de uma importação anterior) nunca é sobrescrito, só pulado."
    )

    arquivo = st.file_uploader(
        "Planilha Base Genéricos (.xlsx)", type=["xlsx", "xls"], key="base_genericos_upload"
    )

    if arquivo and st.button("Processar planilha Base Genéricos", key="base_genericos_processar"):
        try:
            with get_session() as session:
                pasta_id = _subpasta(session, "Base Genéricos")
                resultado = base_genericos_integ.processar_planilha_base_genericos(
                    session, arquivo.getvalue(), arquivo.name, usuario["nome"], pasta_id,
                )
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
        "Priorizada por: aparece em estoque agora (risco de contradição — dizer pra loja que ela "
        "não tem o produto quando na verdade tem) e, dentro disso, por valor acumulado."
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
            f"{r.resolvidos_automaticamente} resolvidos automaticamente, "
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
            marcador_estoque = " · " + theme.badge("em estoque", "alterado") if item.aparece_em_estoque else ""
            with st.expander(f"{item.descricao_observada}  —  EAN {item.ean}"):
                st.markdown(
                    f"{ui.formatar_moeda(float(item.valor_total_acumulado))} acumulado · "
                    f"{item.qtd_ocorrencias} ocorrência(s) · origem {item.origem.value.upper()}"
                    f"{marcador_estoque}",
                    unsafe_allow_html=True,
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
        "Vincular a uma loja reprocessa automaticamente as compras que tinham ficado de fora."
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
            with st.expander(titulo):
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
                        "Vincular e reprocessar", key=f"vincular_cnpj_{item.id}", use_container_width=True
                    ):
                        with get_session() as s2:
                            total = gps_integ.resolver_cnpj_orfao(
                                s2, item.id, opcoes_loja_id_por_nome[escolha], usuario["nome"]
                            )
                        st.success(f"{total} compra(s) reprocessada(s) e importada(s).")
                        st.rerun()

                if st.button("Ignorar este CNPJ", key=f"ignorar_cnpj_{item.id}", use_container_width=True):
                    with get_session() as s2:
                        gps_integ.ignorar_cnpj_orfao(s2, item.id)
                    st.rerun()


def render() -> None:
    theme.cabecalho("Dados", "Status das integrações, importação de planilhas e filas de pendência.")
    _painel_integracoes()
    st.markdown(f'<p class="rmc-muted">Armazenamento: {fs.modo_storage()}</p>', unsafe_allow_html=True)

    aba_explorador, aba_importar, aba_fila_ean, aba_fila_cnpj = st.tabs(
        ["Explorador de Arquivos", "Importar Planilhas", "Fila de Resolução de EAN", "Fila de CNPJ Órfão"]
    )
    with aba_explorador:
        _explorador()
    with aba_importar:
        _importar_planilhas()
    with aba_fila_ean:
        _fila_ean()
    with aba_fila_cnpj:
        _fila_cnpj_orfao()
