"""Configurações de Pedidos (só admin) — os números da sugestão de pedido.
Regras e validação: pedido/configuracao.py; gravação e histórico:
integrations/configuracao_pedido.py.

Duas seções (01/10/2026):
- **Padrão de todas as lojas** — o que valia antes, igual.
- **Personalização por loja** — uma loja pode ter os seus valores (combinado
  em reunião com ela); o que não for personalizado segue o padrão. Só o
  admin mexe; a loja não muda nada.

Um formulário só por seção (`st.form`): mudar um campo não recalcula a
tela, e nada vale até "Salvar". Cada gravação vira uma linha nova no banco,
com quem e quando — o histórico fica no fim da tela.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import auth, theme
from core.config import settings
from core.db import get_session
from pedido import categorias as cat
from pedido import configuracao as cfg
from pedido.configuracao import MESES_MAXIMO, ConfigPedido

_ROTULOS = {
    "dias_medicamento": "Dias de estoque — medicamento",
    "dias_perfumaria": "Dias de estoque — perfumaria",
    "dias_por_categoria": "Dias por categoria",
    "piso_maximo": "Piso do estoque máximo",
    "dias_ruptura": "Dias da ruptura próxima",
    "ruptura_unidades": "Ruptura (unidades)",
    "dias_sem_classificacao": "Dias para Sem Classificação",
    "fator_preco_fora": "Compra fora do padrão",
    "giro_baixo_dias": "Dias do giro baixo",
    "giro_baixo_max_unidades": "Unidades do giro baixo",
    "curva_a": "Curva A",
    "curva_b": "Curva B",
    "meses_fechados": "Janela de meses",
    "tolerancia_preco": "Tolerância de preço",
    "custo_faixa_min": "Custo coerente (mínimo)",
    "custo_faixa_max": "Custo coerente (máximo)",
    "fator_cadastro_nota": "Cadastro × nota",
}
_SECOES = {"padrao": "Padrão de todas as lojas", "loja": "Personalização por loja"}


def _explicacao(o_que_e: str, como_funciona: str) -> None:
    st.markdown(f"**O que é:** {o_que_e}  \n**Como funciona:** {como_funciona}")


def _tabela_categorias(config: ConfigPedido, padrao: ConfigPedido | None) -> pd.DataFrame:
    linhas = []
    for categoria, grupo in cat.CATEGORIAS.items():
        if grupo == cat.SEM_CLASSIFICACAO:
            continue
        linha = {
            "Categoria": categoria,
            "Grupo": cat.ROTULO_GRUPO[grupo],
            # Texto, não número: a tabela editável do Streamlit escreve "None"
            # em célula numérica vazia (visto em 28/09/2026); texto vazio
            # fica em branco. O número é conferido em `_dias_proprios`.
            "Dias próprios": str(config.dias_por_categoria.get(categoria, "")),
        }
        if padrao is not None:
            linha["Padrão"] = str(padrao.dias_da_categoria(categoria))
        linhas.append(linha)
    return pd.DataFrame(linhas)


def _dias_proprios(editada: pd.DataFrame) -> tuple[dict[str, int], list[str]]:
    dias, erros = {}, []
    for linha in editada.to_dict("records"):
        texto = str(linha["Dias próprios"] or "").strip()
        if not texto:
            continue
        if not texto.isdigit():
            erros.append(f"Dias de {linha['Categoria']}: use um número inteiro de dias (está \"{texto}\").")
            continue
        dias[linha["Categoria"]] = int(texto)
    return dias, erros


def _formulario(c: ConfigPedido, chave: str, padrao: ConfigPedido | None = None) -> tuple[ConfigPedido | None, str | None]:
    """Os campos da configuração. `padrao` = None: é o próprio padrão (com a
    janela de meses e "Restaurar padrões"); senão é a personalização de uma
    loja, e cada rótulo mostra o valor do padrão ao lado. Devolve (config
    nova, ação) — ação "salvar"/"restaurar", ou (None, None) sem clique."""
    loja = padrao is not None

    def grade(n: int) -> list:
        """n colunas lado a lado; na personalização, 2 por linha — com o
        "· padrão N" no rótulo, 4 colunas quebravam o rótulo em 2 linhas e
        desalinhavam os campos (visto em 01/10/2026)."""
        if not loja:
            return list(st.columns(n))
        saida = []
        while len(saida) < n:
            saida += list(st.columns(2))
        return saida[:n]

    def rotulo(texto: str, valor_padrao) -> str:
        if not loja:
            return texto
        return f"{texto} · padrão {str(valor_padrao).replace('.', ',')}"

    with st.form(f"config_pedido_{chave}"):
        theme.titulo_secao("Estoque máximo (quanto pedir)")
        _explicacao(
            "quantos dias de venda o pedido + o estoque devem cobrir. A sugestão é o máximo menos o estoque atual.",
            "máximo = demanda por dia × dias, arredondado para cima. Ex.: vende 2 por dia, medicamento com "
            f"{c.dias_medicamento} dias → máximo {2 * c.dias_medicamento}.",
        )
        a1, a2, a3, a4 = grade(4)
        p = padrao or c
        dias_medicamento = a1.number_input(rotulo("Medicamento (dias)", p.dias_medicamento), 1, 180,
                                           c.dias_medicamento, step=1, key=f"{chave}_med")
        dias_perfumaria = a2.number_input(rotulo("Perfumaria (dias)", p.dias_perfumaria), 1, 180, c.dias_perfumaria,
                                          step=1, key=f"{chave}_perf")
        dias_sem = a3.number_input(rotulo("Sem Classificação (dias)", p.dias_sem_classificacao), 1, 180,
                                   c.dias_sem_classificacao, step=1, key=f"{chave}_sem",
                                   help="Dias estimados para produto sem categoria: ele recebe sugestão com a tag "
                                        "\"Sem Classificação\" até alguém classificar (Dados → Pendências).")
        piso = a4.number_input(rotulo("Piso do máximo (unidades)", p.piso_maximo), 0, 1000, c.piso_maximo, step=1,
                               key=f"{chave}_piso",
                               help="Menor máximo possível para um produto que vendeu na janela. Com 1, quem "
                                    "vendeu pelo menos uma vez fica com ao menos 1 unidade na loja.")
        st.caption("Dias próprios de uma categoria (opcional). Em branco = dias do grupo dela."
                   + (" A coluna Padrão mostra os dias que valem sem personalização." if loja else ""))
        editada = st.data_editor(
            _tabela_categorias(c, padrao), hide_index=True, use_container_width=True, key=f"{chave}_categorias",
            height=32 + 31 * (len(cat.CATEGORIAS) - 1) + 3,  # cabeçalho + 1 linha por categoria, sem rolagem
            disabled=["Categoria", "Grupo", "Padrão"],
            column_config={"Dias próprios": st.column_config.TextColumn(max_chars=3)},
        )

        theme.titulo_secao("Ruptura e ruptura próxima")
        _explicacao(
            "Ruptura = o produto acabou (card Ruptura e tag vermelho-alaranjada). Ruptura próxima = ainda tem, "
            "mas está para acabar (tag âmbar). A coluna Mín. do Pedido usa os dias da ruptura próxima.",
            "ruptura quando o estoque é de até N unidades (0 = zerado); ruptura próxima quando o estoque passa "
            "disso, mas cobre esses dias de venda ou menos.",
        )
        r1, r2, _ = grade(3)
        ruptura_unidades = r1.number_input(rotulo("Ruptura: estoque até (unidades)", int(p.ruptura_unidades)), 0, 1000,
                                           int(c.ruptura_unidades), step=1, key=f"{chave}_rup")
        dias_ruptura = r2.number_input(rotulo("Ruptura próxima: cobertura até (dias)", p.dias_ruptura), 0, 60,
                                       c.dias_ruptura, step=1, key=f"{chave}_rupd")

        theme.titulo_secao("Giro baixo")
        _explicacao(
            "produto que quase não vende. Ganha o selo \"Giro baixo\" e o filtro \"Ocultar giro baixo\" o tira do pedido.",
            "giro baixo quando vendeu no máximo estas unidades nestes últimos dias.",
        )
        g1, g2, _ = grade(3)
        giro_dias = g1.number_input(rotulo("Últimos dias", p.giro_baixo_dias), 7, 365, c.giro_baixo_dias, step=1,
                                    key=f"{chave}_girod")
        giro_unidades = g2.number_input(rotulo("No máximo (unidades)", p.giro_baixo_max_unidades), 0.0, 1000.0,
                                        float(c.giro_baixo_max_unidades), step=1.0, key=f"{chave}_girou")

        theme.titulo_secao("Curva ABC")
        _explicacao(
            "a importância do produto na loja, em duas letras: a 1ª pelo valor vendido, a 2ª pelas unidades.",
            "A = os produtos que somam os primeiros % da venda; B = os % seguintes; C = o resto.",
        )
        k1, k2, k3 = grade(3)
        curva_a = k1.number_input(rotulo("A (%)", round(p.curva_a * 100)), 1, 98, round(c.curva_a * 100), step=1,
                                  key=f"{chave}_ca")
        curva_b = k2.number_input(rotulo("B (%)", round(p.curva_b * 100)), 1, 98, round(c.curva_b * 100), step=1,
                                  key=f"{chave}_cb")
        k3.markdown(f"<div style='padding-top:30px'>C = o resto ({max(0, 100 - curva_a - curva_b)}%)</div>",
                    unsafe_allow_html=True)

        meses = c.meses_fechados
        if not loja:
            theme.titulo_secao("Janela de vendas")
            _explicacao(
                "quantos meses de venda entram na demanda por dia: os meses fechados + os dias do mês corrente. "
                "Vale para todas as lojas (não entra na personalização: a rotina baixa o mesmo período de todas).",
                "diminuir vale na hora. Aumentar vale a partir da coleta da madrugada seguinte (os meses a mais "
                "precisam ser baixados do GPS; até lá o Pedido avisa os dias sem dado).",
            )
            meses = st.columns(3)[0].number_input("Meses fechados", 1, MESES_MAXIMO, c.meses_fechados, step=1,
                                                  key=f"{chave}_meses")

        theme.titulo_secao("Preço da última compra")
        _explicacao(
            "o preço do Pedido é a última compra não bonificada da loja. Uma compra muito fora do padrão das "
            "outras compras do produto é tratada como erro (tag \"a revisar\") e fica de fora.",
            "fora do padrão = mais de N vezes (ou menos de 1/N) a mediana das compras do produto na janela. Ex.: "
            "caixa de 100 lançada sem a fração (R$ 79,90 em vez de R$ 0,80). Com uma compra só, nunca é fora do padrão.",
        )
        fator = grade(3)[0].number_input(rotulo("Fora do padrão acima de (vezes a mediana)", p.fator_preco_fora),
                                              1.5, 20.0, float(c.fator_preco_fora), step=0.5, key=f"{chave}_fator")

        theme.titulo_secao("Custo de cadastro × nota fiscal")
        _explicacao(
            "quando o custo de cadastro do GPS e a última nota divergem muito, um dos dois está errado — quase "
            "sempre a fração (display de 30 lançado como 1 unidade, caixa de 100 cadastrada como unidade).",
            "custo coerente = entre o mínimo e o máximo % do preço de venda. Se só um dos dois é coerente, o Pedido "
            "usa esse (tag \"custo ajustado\"); se os dois ou nenhum, fica a nota e o item vai para \"a revisar\". "
            "Produto sem compra com cadastro fora da faixa também vai para \"a revisar\". Medido na Hudson: o custo "
            "normal fica entre 22% e 79% do preço de venda.",
        )
        f1, f2, f3 = grade(3)
        faixa_min = f1.number_input(rotulo("Custo coerente: mínimo (% da venda)", round(p.custo_faixa_min * 100)), 0, 499,
                                    round(c.custo_faixa_min * 100), step=1, key=f"{chave}_fmin")
        faixa_max = f2.number_input(rotulo("Custo coerente: máximo (% da venda)", round(p.custo_faixa_max * 100)), 1, 500,
                                    round(c.custo_faixa_max * 100), step=1, key=f"{chave}_fmax")
        cadastro_nota = f3.number_input(rotulo("Divergência acima de (vezes)", p.fator_cadastro_nota), 1.2, 20.0,
                                        float(c.fator_cadastro_nota), step=0.5, key=f"{chave}_cadnota")

        if loja:
            b1, _ = st.columns([1.4, 4])
            salvar = b1.form_submit_button("Salvar personalização", type="primary", use_container_width=True)
            restaurar = False
        else:
            b1, b2, _ = st.columns([1.2, 1.2, 3])
            salvar = b1.form_submit_button("Salvar configurações", type="primary", use_container_width=True)
            restaurar = b2.form_submit_button("Restaurar padrões", use_container_width=True)

    if restaurar:
        return ConfigPedido.padrao(), "restaurar"
    if not salvar:
        return None, None
    proprios, erros = _dias_proprios(editada)
    nova = ConfigPedido(
        dias_medicamento=int(dias_medicamento), dias_perfumaria=int(dias_perfumaria), dias_por_categoria=proprios,
        piso_maximo=int(piso), dias_ruptura=int(dias_ruptura), giro_baixo_dias=int(giro_dias),
        giro_baixo_max_unidades=float(giro_unidades), curva_a=curva_a / 100, curva_b=curva_b / 100,
        meses_fechados=int(meses), tolerancia_preco=c.tolerancia_preco, ruptura_unidades=int(ruptura_unidades),
        dias_sem_classificacao=int(dias_sem), fator_preco_fora=float(fator), custo_faixa_min=faixa_min / 100,
        custo_faixa_max=faixa_max / 100, fator_cadastro_nota=float(cadastro_nota),
    )
    erros += nova.erros()
    if erros:
        for e in erros:
            st.error(e)
        return None, None
    return nova, "salvar"


def _salvar(config: ConfigPedido, usuario: str) -> None:
    from integrations import configuracao_pedido
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves
    from views import pedido as pedido_view

    with get_session() as session:
        configuracao_pedido.salvar(session, config, usuario)
    pedido_view.invalidar()
    try:
        configuracao_pedido.publicar_para_rotina(do_ambiente(), Chaves(settings.pedido.prefixo), config, usuario)
        st.session_state["config_pedido_msg"] = (
            "success", "Configurações salvas. Já valem no Pedido de todas as lojas sem personalização (e nos campos "
                       "que as personalizadas não mudaram).")
    except Exception as exc:  # noqa: BLE001
        st.session_state["config_pedido_msg"] = (
            "warning", f"Configurações salvas no sistema, mas a janela de meses não chegou à rotina da madrugada "
                       f"({type(exc).__name__}). Salve de novo mais tarde.")


def render() -> None:
    with theme.tela("config-pedido"):
        _render()


def _render() -> None:
    theme.cabecalho("Configurações de Pedidos", "Os números que definem a sugestão de pedido das lojas.")
    mensagem = st.session_state.pop("config_pedido_msg", None)
    if mensagem:
        getattr(st, mensagem[0])(mensagem[1])
    secao = st.segmented_control("Seção", options=list(_SECOES), format_func=_SECOES.get, default="padrao",
                                 key="config_pedido_secao", label_visibility="collapsed") or "padrao"
    if secao == "loja":
        _personalizacao()
    else:
        _padrao()


def _padrao() -> None:
    from integrations import configuracao_pedido

    usuario = auth.usuario_atual()
    with get_session() as session:
        atual = configuracao_pedido.vigente(session)
        historico = configuracao_pedido.historico(session)
    c = atual.config
    if atual.alterado_por:
        st.caption(f"Última alteração: {atual.alterado_por}, em {atual.alterado_em:%d/%m/%Y %H:%M} (UTC).")
    else:
        st.caption("Ainda com os valores padrão (nenhuma alteração salva).")

    nova, acao = _formulario(c, "padrao")
    if acao == "restaurar":
        _salvar(nova, usuario["nome"])
        st.rerun()
    if acao == "salvar":
        if nova == c:
            st.info("Nada mudou.")
        else:
            _salvar(nova, usuario["nome"])
            st.rerun()

    if historico:
        theme.titulo_secao("Histórico de alterações")
        st.dataframe(
            pd.DataFrame([{
                "Quando (UTC)": h["em"].strftime("%d/%m/%Y %H:%M"),
                "Quem": h["por"],
                "O que mudou": ", ".join(_ROTULOS.get(c_, c_) for c_ in h["campos"]) or "—",
            } for h in historico]),
            hide_index=True, use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Personalização por loja
# ---------------------------------------------------------------------------

def _rotulo_loja(l: dict) -> str:
    local = "/".join(x for x in (l.get("cidade"), l.get("uf")) if x)
    return f"{l['razao_social']}" + (f" — {local}" if local else "")


def _descrever(dif: dict) -> str:
    partes = [f"{_ROTULOS.get(k, k)}: {str(v).replace('.', ',')}" for k, v in dif.items() if k != "dias_por_categoria"]
    partes += [f"{c_.capitalize()}: {d} dias" for c_, d in dif.get("dias_por_categoria", {}).items()]
    return "; ".join(partes) or "volta ao padrão"


def _personalizacao() -> None:
    from integrations import configuracao_pedido, pedido_tela
    from views import pedido as pedido_view

    usuario = auth.usuario_atual()
    _explicacao(
        "os valores de uma loja específica, combinados com ela em reunião (ex.: Reis F1 compra para 10 dias).",
        "mude só o que for diferente; o resto continua seguindo o padrão — inclusive se o padrão mudar depois. "
        "Campo deixado igual ao padrão volta a seguir o padrão. A janela de meses vale para todas as lojas.",
    )
    with get_session() as session:
        lojas = pedido_tela.lojas(session)
        personalizadas = configuracao_pedido.lojas_personalizadas(session)
        padrao = configuracao_pedido.vigente(session).config
    por_id = {l["id"]: l for l in lojas}

    if personalizadas:
        theme.titulo_secao("Lojas com personalização")
        st.dataframe(pd.DataFrame([{
            "Loja": p["nome"], "Campos personalizados": p["campos"], "Quem": p["por"],
            "Quando (UTC)": p["em"].strftime("%d/%m/%Y %H:%M"),
        } for p in personalizadas]), hide_index=True, use_container_width=True)
    else:
        st.caption("Nenhuma loja personalizada ainda: todas seguem o padrão.")

    theme.titulo_secao("Personalizar uma loja")
    # Abre com a loja da barra lateral (Q8 de 02/10/2026): quem está olhando
    # a Reis F1 e vem personalizar quase sempre quer a Reis F1.
    from views import loja_barra

    da_barra = loja_barra.escolha(usuario).loja
    ids = [l["id"] for l in lojas]
    loja_id = st.selectbox("Loja", options=ids, index=ids.index(da_barra["id"]) if da_barra and da_barra["id"] in ids else None,
                           placeholder="Selecione a loja", format_func=lambda i: _rotulo_loja(por_id[i]),
                           key="config_pedido_loja")
    if loja_id is None:
        return

    with get_session() as session:
        dif = configuracao_pedido.da_loja(session, loja_id)
        historico = configuracao_pedido.historico_loja(session, loja_id)

    # "Copiar personalização de…": traz os valores de outra loja para o
    # formulário (cópia, não ligação — mudar uma depois não muda a outra).
    copia = st.session_state.get(f"config_pedido_copia_{loja_id}")
    outras = [p for p in personalizadas if p["loja_id"] != loja_id]
    if outras:
        c1, c2, _ = st.columns([2.4, 1, 1.6], vertical_alignment="bottom")
        origem = c1.selectbox("Copiar personalização de…", options=[p["loja_id"] for p in outras], index=None,
                              placeholder="Escolha uma loja personalizada",
                              format_func=lambda i: next(p["nome"] for p in outras if p["loja_id"] == i),
                              key=f"config_pedido_origem_{loja_id}")
        if c2.button("Copiar", disabled=origem is None, use_container_width=True, key=f"config_pedido_copiar_{loja_id}"):
            with get_session() as session:
                st.session_state[f"config_pedido_copia_{loja_id}"] = configuracao_pedido.da_loja(session, origem)
            st.session_state[f"config_pedido_versao_{loja_id}"] = st.session_state.get(f"config_pedido_versao_{loja_id}", 0) + 1
            st.rerun()
    if copia is not None:
        st.info("Valores copiados para o formulário abaixo. Confira e clique em \"Salvar personalização\" — "
                "nada foi gravado ainda.")

    st.caption(("Personalizada: " + _descrever(dif) + ".") if dif else "Esta loja segue o padrão em tudo.")
    base = cfg.aplicar(padrao, copia if copia is not None else dif)
    versao = st.session_state.get(f"config_pedido_versao_{loja_id}", 0)
    nova, acao = _formulario(base, f"loja_{loja_id}_{versao}", padrao=padrao)
    if acao == "salvar":
        with get_session() as session:
            gravada = configuracao_pedido.salvar_loja(session, loja_id, nova, usuario["nome"])
        st.session_state.pop(f"config_pedido_copia_{loja_id}", None)
        pedido_view.invalidar()
        st.session_state["config_pedido_msg"] = (
            ("success", f"Personalização salva ({cfg.quantos_campos(gravada)} campo(s) diferentes do padrão).")
            if gravada else ("info", "Tudo igual ao padrão: a loja segue o padrão."))
        st.rerun()

    if dif:
        confirmar = st.session_state.get("config_pedido_remover") == loja_id
        rotulo = "Confirmar: voltar ao padrão" if confirmar else "Remover personalização"
        if st.button(rotulo, type="primary" if confirmar else "secondary", key=f"config_pedido_remover_{loja_id}"):
            if confirmar:
                with get_session() as session:
                    configuracao_pedido.remover_loja(session, loja_id, usuario["nome"])
                st.session_state.pop("config_pedido_remover", None)
                pedido_view.invalidar()
                st.session_state["config_pedido_msg"] = ("success", "Personalização removida: a loja segue o padrão.")
            else:
                st.session_state["config_pedido_remover"] = loja_id
            st.rerun()

    if historico:
        theme.titulo_secao("Histórico desta loja")
        st.dataframe(pd.DataFrame([{
            "Quando (UTC)": h["em"].strftime("%d/%m/%Y %H:%M"), "Quem": h["por"], "Ficou": _descrever(h["valores"]),
        } for h in historico]), hide_index=True, use_container_width=True)
