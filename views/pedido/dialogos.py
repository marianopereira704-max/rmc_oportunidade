"""Assistente de pedido: pop-ups do rodapé e da correção — Estoque negativo, Listas, Descartar e Exportar."""
from __future__ import annotations

import datetime as dt
import html

import pandas as pd
import streamlit as st

from core import ui
from core.db import get_session
from pedido import calculo
from views import analise_comum as comum
from views import popups
from views.pedido.estado import _KEY, _concluir
from views.pedido.formato import _agora, _hora_local, _identificacao_loja, _numero

@popups.popup("Estoque negativo", "longo")
def _dialog_negativos(loja: dict, negativos: pd.DataFrame, meta: dict, usuario: dict) -> None:
    from integrations import pedido_rascunho

    st.caption("O GPS mostra estes produtos com algum EAN negativo. No cálculo, o negativo conta como 0 — nada "
               "trava. Se souber o estoque real, digite aqui: a linha é recalculada com ele (vale só neste sistema, "
               "e só enquanto a foto do GPS continuar negativa). Em branco = tirar a correção.")
    tabela = pd.DataFrame({
        "linha": negativos["linha"].to_numpy(),
        "Produto": negativos["nome"].to_numpy(),
        "EAN(s) negativo(s)": negativos["eans_negativos"].fillna("").to_numpy(),
        "Estoque usado": [_numero(v) for v in negativos["estoque"]],
        # Texto: a tabela editável escreve "None" em número vazio.
        "Estoque correto": [_numero(v) if c else "" for v, c in zip(negativos["estoque"], negativos["estoque_corrigido"])],
    })
    editada = st.data_editor(
        tabela, hide_index=True, use_container_width=True, key=f"{_KEY}_negativos_editor",
        column_order=["Produto", "EAN(s) negativo(s)", "Estoque usado", "Estoque correto"],
        disabled=["Produto", "EAN(s) negativo(s)", "Estoque usado"],
        column_config={"Estoque correto": st.column_config.TextColumn(max_chars=8)},
        height=min(35 * len(tabela) + 40, 420),
    )
    aviso = st.empty()   # erros acima do rodapé (que fica preso embaixo)
    cancelar, salvar = popups.botoes("Salvar correções", chave=f"{_KEY}_negativos_rodape")
    if cancelar:
        st.rerun()
    if salvar:
        valores, erros = {}, []
        for r in editada.to_dict("records"):
            texto = str(r["Estoque correto"] or "").strip().replace(".", "").replace(",", ".")
            if not texto:
                valores[r["linha"]] = None
                continue
            try:
                valor = float(texto)
            except ValueError:
                erros.append(f"{r['Produto']}: \"{r['Estoque correto']}\" não é um número.")
                continue
            if valor < 0:
                erros.append(f"{r['Produto']}: o estoque correto não pode ser negativo.")
                continue
            valores[r["linha"]] = valor
        if erros:
            aviso.error("\n".join(f"- {e}" for e in erros))
            return
        gps = dict(zip(negativos["linha"], negativos["estoque_gps"].astype(float)))
        with get_session() as session:
            gravadas, removidas = pedido_rascunho.salvar_correcoes(session, loja["id"], valores, gps,
                                                                   meta.get("data_foto"), usuario["nome"])
        partes = [f"{gravadas} correção(ões) salva(s)"] + ([f"{removidas} removida(s)"] if removidas else [])
        _concluir("success", "Estoque negativo: " + ", ".join(partes) + ".")
        st.rerun()


@popups.popup("Listas", "curto")
def _dialog_listas(loja: dict, usuario: dict, pedido: pd.DataFrame, listas: list, aberta_id: int | None,
                   tem_alteracoes: bool) -> None:
    """Botão "Listas" do rodapé (02/10/2026): duas abas — salvar o pedido
    atual com nome (a que abre primeiro) e as listas já salvas da loja.
    Junta o antigo "Salvar como lista" do rodapé com o "Listas" do topo."""
    from integrations import pedido_area

    aba_salvar, aba_salvas = st.tabs(["Salvar como lista", "Listas salvas"])
    with aba_salvar:
        st.caption("Guarda os itens marcados e as quantidades de agora com um nome, para continuar depois ou para "
                   "outra pessoa ver.")
        i = calculo.indicadores(pedido)
        st.markdown(comum.texto_markdown(
            f"**{html.escape(_identificacao_loja(loja))}**  \n{ui.formatar_numero(i.marcados)} itens marcados · "
            f"{ui.formatar_numero(i.unidades)} unidades · {ui.formatar_moeda(i.orcamento)} · "
            f"{_agora():%d/%m/%Y %H:%M}"))
        nome = st.text_input("Nome da lista", max_chars=120, placeholder="Ex.: Reposição semanal — genéricos",
                             key=f"{_KEY}_nome_lista")
        if pedido.empty:
            st.caption("Nenhum item no pedido para salvar.")
        if st.button("Salvar", type="primary", use_container_width=True, key=f"{_KEY}_salvar_nova_lista",
                     disabled=pedido.empty or not nome.strip()):
            itens = [(l, int(q), bool(s)) for l, q, s in zip(pedido["linha"], pedido["quantidade"], pedido["selecionado"])]
            with get_session() as session:
                pedido_area.salvar_lista(session, usuario["id"], usuario["nome"], loja["id"], nome, itens,
                                         i.unidades, i.orcamento)
            _concluir("success", f"Lista \"{nome.strip()}\" salva.")
            st.rerun()

    with aba_salvas:
        st.caption("Pedidos já montados. \"Abrir\" traz a lista para a sua área de trabalho; exportar a partir dela "
                   "encerra a lista.")
        if not listas:
            st.info("Nenhuma lista salva para esta loja.")
        for l in listas:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3.2, 1, 1], vertical_alignment="center")
                aberta = " · aberta na sua área" if l.id == aberta_id else ""
                c1.markdown(comum.texto_markdown(
                    f"**{html.escape(l.nome)}**{aberta}  \n"
                    f"{html.escape(l.criado_por)} · {l.criado_em:%d/%m/%Y} às {_hora_local(l.criado_em)} · "
                    f"{ui.formatar_numero(l.itens)} itens · {ui.formatar_numero(l.unidades)} un. · "
                    f"{ui.formatar_moeda(l.valor)}"))
                confirmar = st.session_state.get(f"{_KEY}_confirmar_abrir") == l.id
                if c2.button("Confirmar" if confirmar else "Abrir", key=f"{_KEY}_abrir_{l.id}", type="primary",
                             use_container_width=True):
                    if tem_alteracoes and not confirmar:
                        st.session_state[f"{_KEY}_confirmar_abrir"] = l.id
                        st.warning("Abrir substitui as suas alterações atuais desta loja. Clique em \"Confirmar\".")
                    else:
                        with get_session() as session:
                            pedido_area.abrir_lista(session, usuario["id"], loja["id"], l.id)
                        st.session_state.pop(f"{_KEY}_confirmar_abrir", None)
                        _concluir("success", f"Lista \"{l.nome}\" aberta na sua área de trabalho.")
                        st.rerun()
                if c3.button("Excluir", key=f"{_KEY}_excluir_{l.id}", use_container_width=True):
                    with get_session() as session:
                        pedido_area.excluir_lista(session, l.id)
                    _concluir("success", f"Lista \"{l.nome}\" excluída.")
                    st.rerun()


@popups.popup("Descartar alterações", "aviso")
def _dialog_descartar(loja: dict, usuario: dict, alterados: int) -> None:
    from integrations import pedido_area

    st.write(f"As {alterados} alteração(ões) da sua área de trabalho nesta loja (quantidades digitadas e marcações) "
             "voltam para a sugestão do sistema. As listas salvas não mudam.")
    cancelar, descartar = popups.botoes("Descartar alterações", chave=f"{_KEY}_descartar_rodape", destrutivo=True)
    if cancelar:
        st.rerun()
    if descartar:
        with get_session() as session:
            pedido_area.descartar(session, usuario["id"], loja["id"])
        _concluir("success", "Alterações descartadas: o pedido voltou à sugestão.")
        st.rerun()


def _excel(abas: dict[str, pd.DataFrame]) -> bytes:
    """Uma planilha com uma aba por item, colunas na largura do conteúdo
    (até 50 caracteres) e o cabeçalho travado — abre pronta pra ler."""
    import io

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        for nome, tabela in abas.items():
            tabela.to_excel(escritor, index=False, sheet_name=nome)
            folha = escritor.sheets[nome]
            folha.freeze_panes = "A2"
            for i, coluna in enumerate(tabela.columns, start=1):
                largura = max([len(str(coluna))] + [len(str(v)) for v in tabela[coluna].head(500)])
                folha.column_dimensions[folha.cell(row=1, column=i).column_letter].width = min(50, largura + 2)
    return buffer.getvalue()


def _arquivo_exportacao(tabela: pd.DataFrame, formato: str) -> bytes:
    if formato == "Excel":
        return _excel({"Pedido": tabela})
    # ";" e BOM: o Excel em português abre direto, com acento e sem juntar colunas.
    return tabela.to_csv(index=False, sep=";").encode("utf-8-sig")


_TIPOS_EXPORTACAO = {
    "gruppy": ("Formato Gruppy", "O pedido para o sistema de compra: PRODUTO, QUANTIDADE e os EANs. Só os itens "
                                 "marcados com quantidade."),
    "completa": ("Tabela completa", "Para conferir: aba Pedido (todos os itens, marcados ou não, com as colunas da "
                                    "tela) e aba Giro baixo."),
    "giro": ("Só giro baixo", "Para mostrar à loja os produtos com estoque que quase não vendem (até 1 unidade "
                              "em 90 dias) e quanto está parado."),
}
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@popups.popup("Exportar", "curto")
def _dialog_exportar(loja: dict, pedido: pd.DataFrame, todas: pd.DataFrame, usuario: dict) -> None:
    """Três exportações (01/10/2026). Só o formato Gruppy é "o pedido": ele
    fica registrado (aviso contra pedido duplicado) e encerra a lista aberta.
    Tabela completa e Só giro baixo são pra conferir e conversar com a loja."""
    from integrations import pedido_area, pedido_rascunho

    tipo = st.radio("O que exportar", list(_TIPOS_EXPORTACAO), format_func=lambda t: _TIPOS_EXPORTACAO[t][0],
                    captions=[d for _, d in _TIPOS_EXPORTACAO.values()], key=f"{_KEY}_tipo_exportacao")
    codigo = loja.get("legacy_id") or "".join(ch for ch in (loja.get("cnpj") or "") if ch.isdigit())
    hoje = f"{dt.date.today():%Y%m%d}"

    if tipo == "gruppy":
        marcados = pedido[pedido["selecionado"] & (pedido["quantidade"] > 0)]
        tabela = calculo.exportacao(marcados)
        i = calculo.indicadores(marcados)
        st.markdown(comum.texto_markdown(
            f"**{ui.formatar_numero(len(tabela))}** itens · **{ui.formatar_numero(i.unidades)}** unidades · "
            f"**{ui.formatar_moeda(i.orcamento)}**"))
        if tabela.empty:
            st.info("Nenhum item marcado com quantidade maior que zero.")
            return
        formato = st.radio("Formato", ["Excel", "CSV"], horizontal=True, key=f"{_KEY}_formato")
        nome = f"assistente_pedido_{codigo}_{hoje}.{'xlsx' if formato == 'Excel' else 'csv'}"

        def _registrar() -> None:
            with get_session() as session:
                pedido_rascunho.registrar_exportacao(session, loja["id"], formato, len(tabela), i.unidades,
                                                     i.orcamento, usuario["nome"])
                lista = pedido_area.ao_exportar(session, usuario["id"], loja["id"])
            _concluir("success", "Pedido exportado" + (
                f"; a lista \"{lista}\" foi encerrada." if lista else "."))
            st.session_state[f"{_KEY}_exportou"] = True

        st.download_button(f"Baixar {formato}", data=_arquivo_exportacao(tabela, formato), file_name=nome,
                           mime=_XLSX if formato == "Excel" else "text/csv", type="primary", use_container_width=True,
                           on_click=_registrar, key=f"{_KEY}_baixar")
    elif tipo == "completa":
        abas = {"Pedido": calculo.tabela_completa(pedido), "Giro baixo": calculo.giro_baixo(todas)}
        st.markdown(comum.texto_markdown(f"**{ui.formatar_numero(len(abas['Pedido']))}** itens no pedido · "
                                         f"**{ui.formatar_numero(len(abas['Giro baixo']))}** produtos de giro baixo"))
        st.download_button("Baixar Excel", data=_excel(abas), file_name=f"assistente_pedido_completo_{codigo}_{hoje}.xlsx",
                           mime=_XLSX, type="primary", use_container_width=True, key=f"{_KEY}_baixar_completa")
    else:
        giro = calculo.giro_baixo(todas)
        parado = float(giro["VALOR PARADO EM ESTOQUE"].sum()) if not giro.empty else 0.0
        st.markdown(comum.texto_markdown(f"**{ui.formatar_numero(len(giro))}** produtos · "
                                         f"**{ui.formatar_moeda(parado)}** parados em estoque"))
        if giro.empty:
            st.info("Nenhum produto de giro baixo nesta loja.")
            return
        st.download_button("Baixar Excel", data=_excel({"Giro baixo": giro}), file_name=f"giro_baixo_{codigo}_{hoje}.xlsx",
                           mime=_XLSX, type="primary", use_container_width=True, key=f"{_KEY}_baixar_giro")
    # O clique no download refaz só o pop-up: sem este rerun da página
    # inteira, o botão continuava "Listas (1)" com a lista já apagada no banco
    # (visto em 29/09/2026). O arquivo já saiu no navegador antes do rerun.
    if st.session_state.pop(f"{_KEY}_exportou", False):
        st.rerun()
