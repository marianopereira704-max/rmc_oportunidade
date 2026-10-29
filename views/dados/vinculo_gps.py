"""Aba Dados → Pendências → Vínculo de lojas GPS ↔ RMC e os códigos de empresa do GPS."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core import auth, ui
from pedido import vinculo as vinculo_rotina
from core.config import settings
from core.db import get_session
from core.models import Loja, loja_da_rede
from views import leitor_planilha as leitor
from views.dados.comum import _LEITOR_CODIGOS_GPS, _explicacao, _milhar, _mostrar_planilha_recebida

_SITUACAO_ROTULO = {"AUTOMATICO": "automático", "CONFIRMADO": "confirmado", "CONFIRMAR": "para confirmar",
                    "NAO_CLIENTE": "não é cliente", "SEM_CANDIDATO": "sem loja parecida"}


def _vinculo_lojas_gps() -> None:
    from core.models import SituacaoVinculoGps, VinculoLojaGps
    from integrations import gps_vinculo
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    usuario = auth.usuario_atual()
    st.markdown("##### Vínculo de lojas GPS")
    if st.session_state.get("vinculo_gps_msg"):
        st.success(st.session_state.pop("vinculo_gps_msg"))
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

    _codigos_gps(usuario)

    # Reconhecer a loja (08/10/2026): o nome do GPS sozinho não dizia qual era
    # a loja — agora o código da empresa no GPS (planilha), as pendências
    # agrupadas por empresa e a loja do RMC com o código da T.I. na frente.
    with get_session() as session:
        vinculos = [{
            "id": v.id, "empresa": v.id_empresa_gps, "loja": (v.codigo_loja_gps or "").strip(),
            "nome": v.nome_loja_gps, "cidade": v.cidade_gps, "uf": v.uf_gps, "numero": v.numero_gps,
            "cnpj": v.cnpj_gps, "situacao": v.situacao, "loja_id": v.loja_id, "motivo": v.motivo,
        } for v in session.scalars(select(VinculoLojaGps))]
        lojas = [{"id": l.id, "legacy": l.legacy_id, "razao": l.razao_social, "cnpj": vinculo_rotina.so_digitos(l.cnpj),
                  "cidade": l.cidade, "uf": l.uf}
                 for l in session.scalars(select(Loja).where(loja_da_rede()).order_by(Loja.legacy_id, Loja.razao_social))]
        codigos = gps_vinculo.empresas(session)

    if not vinculos:
        st.markdown('<p class="rmc-muted">Nenhum vínculo calculado ainda.</p>', unsafe_allow_html=True)
        return

    por_id = {l["id"]: l for l in lojas}
    lojas_por_empresa: dict[str, int] = {}
    for v in vinculos:
        lojas_por_empresa[v["empresa"]] = lojas_por_empresa.get(v["empresa"], 0) + 1

    def rotulo_loja(i) -> str:
        l = por_id[i]
        local = "/".join(x for x in (l["cidade"], l["uf"]) if x)
        return (f"{l['legacy']} · " if l["legacy"] else "") + f"{l['razao']}" + (f" — {local}" if local else "")

    def nome_empresa(emp: str) -> str:
        info = codigos.get(emp)
        n = lojas_por_empresa.get(emp, 0)
        lojas_txt = f"{n} loja{'s' if n != 1 else ''} no GPS"
        if info:
            return f"{info.codigo} · {info.nome or 'sem nome'} ({lojas_txt})"
        primeira = next((v["nome"] for v in vinculos if v["empresa"] == emp and v["nome"]), "sem nome")
        # Empresa que a planilha do GPS não trouxe (46 das 388 da API em 08/10/2026).
        return f"{primeira} ({lojas_txt}; sem código — fora da planilha do GPS)" if codigos else             f"{primeira} ({lojas_txt}; sem código — envie a planilha do GPS acima)"

    por_situacao: dict = {}
    for v in vinculos:
        por_situacao.setdefault(v["situacao"], []).append(v)
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
    # Agrupadas por empresa do GPS: as lojas de um grupo (ex.: as 8 da Mega
    # Farma, as 2 da Farmácia Vida = 793/1880) ficam juntas.
    grupos: dict[str, list[dict]] = {}
    for v in pendentes:
        grupos.setdefault(v["empresa"], []).append(v)
    for emp in sorted(grupos, key=lambda e: (codigos[e].codigo.zfill(8) if e in codigos else "~", e)):
        st.markdown(f"**{html.escape(nome_empresa(emp))}**")
        cnpjs_empresa = codigos[emp].cnpjs if emp in codigos else frozenset()
        for v in sorted(grupos[emp], key=lambda x: x["loja"]):
            local = "/".join(x for x in (v["cidade"], v["uf"]) if x)
            titulo = f"Loja {v['loja']} · {v['nome'] or 'sem nome'}" + (f" — {local}" if local else "") \
                + f" · nº {v['numero'] or 's/n'}"
            painel = st.expander(titulo, key=f"exp_vinc_gps_{v['id']}", on_change="rerun")
            if not painel.open:
                continue
            with painel:
                st.caption(f"Por que está aqui: {v['motivo'] or '—'}"
                           + (f" · CNPJ no GPS: {ui.formatar_cnpj(v['cnpj'])}" if v["cnpj"] else ""))
                # Primeiro as lojas desta empresa segundo a planilha do GPS
                # (CNPJ), depois as da mesma cidade, depois o resto — pelo
                # código da T.I. Dá pra digitar o código pra achar.
                da_empresa = [l["id"] for l in lojas if l["cnpj"] in cnpjs_empresa]
                mesma_cidade = [l["id"] for l in lojas if l["id"] not in da_empresa and v["uf"] and l["uf"] == v["uf"]
                                and (l["cidade"] or "").strip().upper() == (v["cidade"] or "").strip().upper()]
                resto = [l["id"] for l in lojas if l["id"] not in da_empresa and l["id"] not in mesma_cidade]
                if da_empresa:
                    st.caption("Lojas desta empresa no GPS (pelo CNPJ): "
                               + "; ".join(rotulo_loja(i) for i in da_empresa))
                opcoes = ["—"] + da_empresa + mesma_cidade + resto
                escolha = st.selectbox(
                    "Loja do RMC", options=opcoes, index=opcoes.index(v["loja_id"]) if v["loja_id"] in por_id else 0,
                    format_func=lambda i: "—" if i == "—" else rotulo_loja(i), key=f"vinc_gps_loja_{v['id']}",
                )
                c1, c2 = st.columns(2)
                if c1.button("Confirmar vínculo", key=f"vinc_gps_ok_{v['id']}", use_container_width=True,
                             disabled=escolha == "—"):
                    with get_session() as s2:
                        gps_vinculo.confirmar(s2, v["id"], escolha, usuario["nome"])
                    st.rerun()
                if c2.button("Não é cliente", key=f"vinc_gps_nao_{v['id']}", use_container_width=True):
                    with get_session() as s2:
                        gps_vinculo.marcar_nao_cliente(s2, v["id"], usuario["nome"])
                    st.rerun()

    # Conferência: todos os vínculos, inclusive os automáticos.
    ligados = [v for v in vinculos if v["loja_id"] in por_id]
    tudo = st.expander(f"Conferir todos os vínculos ({len(vinculos)})", key="vinc_gps_conferir", on_change="rerun")
    if tudo.open:
        with tudo:
            st.dataframe(pd.DataFrame([{
                "Código GPS": codigos[v["empresa"]].codigo if v["empresa"] in codigos else "",
                "Empresa no GPS": (codigos[v["empresa"]].nome if v["empresa"] in codigos else None) or "",
                "Loja GPS": v["loja"],
                "Nome no GPS": v["nome"] or "",
                "Cidade/UF": "/".join(x for x in (v["cidade"], v["uf"]) if x),
                "Loja do RMC": rotulo_loja(v["loja_id"]) if v["loja_id"] in por_id else "",
                "Situação": _SITUACAO_ROTULO.get(v["situacao"].name, v["situacao"].name),
            } for v in sorted(vinculos, key=lambda x: (codigos[x["empresa"]].codigo.zfill(8) if x["empresa"] in codigos
                                                       else "~", x["empresa"], x["loja"]))]),
                hide_index=True, use_container_width=True)
            st.caption(f"{len(ligados)} loja(s) do GPS ligadas a uma loja do RMC.")


def _codigos_gps(usuario: dict) -> None:
    """Planilha do GPS com o código de cada empresa (08/10/2026) — a API não
    manda esse número. Reenviar quando o GPS tiver clientes novos."""
    from integrations import gps_vinculo

    with get_session() as session:
        n = len(gps_vinculo.empresas(session))
    painel = st.expander(f"Códigos do GPS ({n} empresa(s) com código)" if n else "Códigos do GPS (envie a planilha)",
                         key="vinc_gps_codigos", on_change="rerun")
    if not painel.open:
        return
    with painel:
        st.caption("Planilha exportada do GPS com as colunas **Empresa** (\"2089 - Mega Farma\"), **Cnpj** e "
                   "**idEmp**. O código aparece nas pendências e na conferência abaixo. Reenviar não apaga nada: "
                   "atualiza quem veio e mantém o resto.")
        leitor.leitor_planilha(_LEITOR_CODIGOS_GPS, "Selecionar planilha de códigos do GPS (.xlsx)")
        _mostrar_planilha_recebida(_LEITOR_CODIGOS_GPS)
        recebida = leitor.planilha_recebida(_LEITOR_CODIGOS_GPS)
        if recebida is None:
            return
        try:
            lida = gps_vinculo.ler_planilha_codigos(recebida.df)
        except ValueError as exc:
            st.error(str(exc))
            return
        if lida.ignoradas:
            st.warning(f"{len(lida.ignoradas)} linha(s) ignorada(s): "
                       + "; ".join(f"linha {n}: {m}" for n, m in lida.ignoradas[:8])
                       + (" …" if len(lida.ignoradas) > 8 else ""))
        st.caption(f"{_milhar(len(lida.empresas))} empresa(s) com código serão gravadas.")
        if lida.empresas and st.button("Gravar códigos", key=f"{_LEITOR_CODIGOS_GPS}_gravar"):
            with get_session() as s2:
                gravadas = gps_vinculo.importar_codigos(s2, lida, usuario["nome"])
            leitor.consumir(_LEITOR_CODIGOS_GPS)
            st.session_state["vinculo_gps_msg"] = f"{_milhar(gravadas)} empresa(s) com o código do GPS gravadas."
            st.rerun()
