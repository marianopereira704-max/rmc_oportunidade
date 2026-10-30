"""Análise de Oportunidade · Por Produto — uma linha por (loja, produto),
contra o laboratório escolhido no filtro principal. Desde 05/10/2026 pelas
compras da API do GPS e com todo produto da tabela Gruppy (regras em
core/analise_api.py — as mesmas da Por Loja):

- preço = média ponderada SÓ das notas acima da tabela ("N de M notas
  acima" embaixo), Qtd. e economia dessas notas — Diferença × Qtd. =
  Economia; sem nota acima, a média de todas e "Já otimizada";
- notas "a revisar" ficam fora da conta, com aviso na economia;
- o detalhe é uma tela só: loja, a conta da linha, o preço por mês nos
  meses que a rotina guarda (até 6) contra o preço do laboratório, e as
  notas fiscais da maior diferença para a menor (mesma tabela do detalhe da
  Por Loja).

Ordenação e paginação rodam sobre o resultado guardado em memória
(views/analise_comum.py); a tabela é o componente de views/tabela.py."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import analise, analise_api, auth, theme, ui
from core.config import settings
from views import analise_comum as comum
from views import loja_barra
from views import popups
from views import tabela as tb

_KEY = "op_produto"


def _colunas(laboratorio: str) -> list[tb.ColunaTabela]:
    return [
        tb.ColunaTabela("nome_canonico", "Produto", "fit-content(230px)", celular="titulo"),
        tb.ColunaTabela("laboratorio", "Laboratório", "fit-content(150px)"),
        tb.ColunaTabela("preco_pago", "Preço pago", direita=True, numerica=True,
                        dica="Média das notas com preço acima da tabela, pela quantidade de cada uma.\n\n"
                             "Sem nenhuma nota acima, a média de todas as notas do período."),
        tb.ColunaTabela("preco_laboratorio", laboratorio, direita=True, numerica=True),
        tb.ColunaTabela("diferenca", "Diferença un.", direita=True, numerica=True, celular=None),
        tb.ColunaTabela("quantidade", "Qtd.", direita=True, numerica=True,
                        dica="Unidades das notas acima da tabela (as que entram na economia)."),
        tb.ColunaTabela("economia", "Economia", direita=True, numerica=True, celular="destaque"),
    ]


def _categoria(valor) -> str | None:
    if not valor:
        return None
    return valor.capitalize() if valor.isupper() else valor


def _celula_produto(r: dict, com_loja: bool) -> list:
    linhas = [tb.pedaco(r["nome_canonico"], "forte quebra")]
    if r.get("categoria"):
        linhas.append(tb.pedaco(_categoria(r["categoria"]), "aux so-pc"))
    if com_loja:
        linhas.append([
            tb.pedaco(f"{(r.get('razao_social') or '').strip()} ·", "aux"),
            tb.pedaco(ui.formatar_cnpj(r["cnpj"]), "aux inteiro"),
        ])
    return tb.celula(*linhas)


def _celula_preco(r: dict) -> list:
    validas = r["notas"] - r["notas_revisar"]
    if r["notas_acima"]:
        texto = f"{r['notas_acima']} de {validas} nota{'s' if validas != 1 else ''} acima"
    else:
        texto = f"{validas} nota{'s' if validas != 1 else ''}" if validas else "só a revisar"
    # "N de M notas acima" fica só no PC (29/10/2026, Mariano): no cartão do celular, o detalhe mostra as notas.
    return tb.celula(tb.pedaco(comum.moeda(r["preco_pago"])), tb.pedaco(texto, "aux so-pc"))


def _celula_economia(r: dict) -> list:
    if r["so_revisar"]:
        return tb.celula(tb.pedaco(
            "A revisar", "selo-revisar",
            "Preço a conferir.\n\nTodas as notas deste produto no período têm o preço com cara de erro (embalagem "
            "ou fração). Elas não somam economia.\n\nAbra os detalhes para ver cada nota."))
    if r["economia"] and r["economia"] > 0:
        pedacos = [tb.pedaco(ui.formatar_moeda(float(r["economia"])), "selo-sucesso")]
    else:
        pedacos = [tb.pedaco("Já otimizada", "selo-neutro")]
    if r["notas_revisar"]:
        n = r["notas_revisar"]
        pedacos.append(tb.pedaco(
            f"{n} nota{'s' if n > 1 else ''} a revisar", "aux",
            f"{n} nota{'s' if n > 1 else ''} deste produto {'têm' if n > 1 else 'tem'} o preço com cara de erro e "
            f"{'ficaram' if n > 1 else 'ficou'} fora da conta.\n\nAbra os detalhes para ver qual."))
    return tb.celula(*pedacos)


def _linha(r: dict, com_loja: bool) -> dict:
    return {
        "id": f"{r['loja_id']}|{r['produto_id']}",
        "celulas": {
            "nome_canonico": _celula_produto(r, com_loja),
            "laboratorio": tb.celula(tb.pedaco(r.get("laboratorio") or "—")),
            "preco_pago": _celula_preco(r),
            "preco_laboratorio": tb.celula(tb.pedaco(comum.moeda(r["preco_laboratorio"]))),
            "diferenca": comum._celula_diferenca(r["diferenca"]),
            "quantidade": tb.celula(tb.pedaco(ui.formatar_numero(float(r["quantidade"] or 0)))),
            "economia": _celula_economia(r),
        },
    }


def _grafico(historico: pd.DataFrame, preco_laboratorio: float, laboratorio: str) -> go.Figure:
    """Preço pago por mês (navy) contra o preço do laboratório (verde,
    tracejado) — no máximo 2 cores por gráfico (identidade visual)."""
    meses = [comum.rotulo_meses([m]) for m in historico["ano_mes"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=meses, y=historico["preco"], name="Preço pago (média do mês)", mode="lines+markers",
        line=dict(color=theme.PALETA["navy"], width=3), marker=dict(color=theme.PALETA["navy"], size=8),
        hovertemplate="%{x}<br>R$ %{y:,.2f}<extra></extra>",
    ))
    # O preço do laboratório é uma linha que cruza o gráfico inteiro: com um
    # mês só, uma série de um ponto não desenhava linha nenhuma (05/10/2026).
    # A série vazia só põe o nome na legenda.
    fig.add_hline(y=preco_laboratorio, line=dict(color=theme.PALETA["verde"], width=2, dash="dash"))
    fig.add_trace(go.Scatter(x=[None], y=[None], name=f"{laboratorio}: {ui.formatar_moeda(preco_laboratorio)}",
                             mode="lines", line=dict(color=theme.PALETA["verde"], width=2, dash="dash")))
    menor = min(float(historico["preco"].min()), preco_laboratorio)
    maior = max(float(historico["preco"].max()), preco_laboratorio)
    folga = (maior - menor) * 0.15 or maior * 0.1 or 1
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10), height=260, plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        legend=dict(orientation="h", y=-0.25), xaxis=dict(showgrid=False),
        yaxis=dict(title="R$ por unidade", showgrid=True, gridcolor=theme.PALETA["borda"],
                   range=[max(0, menor - folga), maior + folga]),
    )
    return fig


@popups.popup("Detalhes do produto", "detalhe")
def _dialog_detalhes(linha: dict, notas: pd.DataFrame, laboratorio: str) -> None:
    """Uma tela só, rolando (Q11 de 05/10/2026)."""
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Loja**  \n{linha['razao_social']} · {ui.formatar_cnpj(linha['cnpj'])}")
        st.markdown(f"**UF / Cidade**  \n{linha['uf']} · {linha['cidade']}")
        # Os três responsáveis, com o nome inteiro — mesma ordem da coluna
        # Responsável da Por Loja.
        st.markdown(f"**Consultor interno**  \n{linha.get('consultor_interno') or '—'}")
        st.markdown(f"**Consultor farma**  \n{linha.get('consultor_farma') or '—'}")
        st.markdown(f"**Atendente comercial**  \n{linha.get('atendente_comercial') or '—'}")
    with c2:
        st.markdown(f"**Produto**  \n{linha['nome_canonico']}")
        st.markdown(f"**Categoria**  \n{_categoria(linha.get('categoria')) or 'Sem Classificação'}")
        st.markdown(f"**Laboratório da compra**  \n{linha.get('laboratorio') or '—'}")
        if linha["notas_acima"]:
            st.markdown(
                f"**Preço pago − {laboratorio}**  \n"
                + comum.texto_markdown(f"{comum.moeda(linha['preco_pago'])} − {comum.moeda(linha['preco_laboratorio'])} = ")
                + comum.diferenca(linha["diferenca"])
                + f" por unidade × {ui.formatar_numero(float(linha['quantidade']))} un.",
                unsafe_allow_html=True,
            )
    theme.card_destaque("Economia perdida nesse produto/período", ui.formatar_moeda(float(linha["economia"] or 0)))

    theme.titulo_secao("Preço por mês")
    historico = comum.historico_api(laboratorio)
    historico = historico[(historico["loja_id"] == linha["loja_id"]) & (historico["produto_id"] == linha["produto_id"])]
    mensal = analise_api.historico_mensal(historico)
    if mensal.empty:
        st.caption("Sem compras válidas deste produto nos meses guardados.")
    else:
        st.plotly_chart(_grafico(mensal, float(linha["preco_laboratorio"]), laboratorio), use_container_width=True,
                        config={"displayModeBar": False})
        st.caption(f"Meses guardados pela rotina: {comum.rotulo_meses(list(mensal['ano_mes']))}. Notas \"a revisar\" "
                   "ficam fora da média.")

    theme.titulo_secao("Notas fiscais do período")
    # Padrão: da maior diferença para a menor (Q11) — onde a loja mais pagou
    # a mais primeiro; clicar num título ordena por ele, como nas outras tabelas.
    chave = f"{_KEY}_notas_{linha['loja_id']}_{linha['produto_id']}"
    st.session_state.setdefault(f"{chave}_ordem", ("diferenca", False))
    ordem = comum.ordem_atual(chave)
    tb.tabela(
        f"{chave}_tabela", comum.colunas_notas(laboratorio, com_produto=False),
        [comum.linha_nota(r) for r in analise.ordenar(notas, *ordem).to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(chave, coluna.campo, coluna.numerica), densa=True,
        vazio=("Nenhuma nota", "Nenhuma compra deste produto no período"),
    )


def render() -> None:
    with theme.tela("por-produto"):
        _render()


def _render() -> None:
    # Sem cabeçalho de página (pedido de 25/09/2026): a tela abre direto na
    # faixa Laboratório. A loja vem da barra lateral (02/10/2026): "Todas" =
    # a rede (ou as lojas do usuário); uma loja = só os produtos dela.
    escolha = loja_barra.escolha(auth.usuario_atual())
    uma_loja = not escolha.todas
    laboratorio = comum.faixa_laboratorio(_KEY)
    # Cards entre a faixa e os filtros, preenchidos depois de ler os filtros.
    area_indicadores = st.container()
    notas, meses = pd.DataFrame(columns=analise_api.COLUNAS), []
    if laboratorio is not None:
        notas, _bonif, meses = comum.resultado_api(laboratorio, settings_periodo(_KEY))
    categorias = sorted(set(notas["categoria"].fillna(analise.SEM_CATEGORIA))) if not notas.empty else []
    filtros = comum.filtros(_KEY, laboratorio, "Buscar produto ou laboratório" if uma_loja
                            else "Buscar produto, laboratório, loja ou CNPJ", escolha, categorias=categorias)
    if laboratorio is None:
        comum.aviso_sem_laboratorio()
        return
    if not meses:
        st.info("As compras da rede ainda não foram montadas. A rotina da madrugada junta as compras de todas as "
                "lojas por mês fechado; cada mês aparece a partir do dia 6 do mês seguinte.")
        return

    produtos = analise.filtrar(analise_api.por_produto(notas), filtros)
    with area_indicadores:
        comum.indicadores("produto", produtos, laboratorio, filtros, uma_loja=uma_loja)
    st.caption(f"Compras de {comum.rotulo_meses(meses)}, pela data da nota fiscal.")

    ui.resetar_pagina_se_filtro_mudou(_KEY, f"{laboratorio}|{filtros}")
    ordem = comum.ordem_atual(_KEY)
    fatia, pagina = comum.pagina(analise.ordenar(produtos, *ordem), _KEY)
    tb.tabela(
        f"{_KEY}_tabela", _colunas(laboratorio), [_linha(r, not uma_loja) for r in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        acao="Ver detalhes", densa=True, celular="cartao",
    )

    clicada = tb.acao_clicada(f"{_KEY}_tabela")
    if clicada is not None:
        loja_id, produto_id = clicada.split("|", 1)
        escolhida = fatia[(fatia["loja_id"].astype(str) == loja_id) & (fatia["produto_id"] == produto_id)]
        if not escolhida.empty:
            linha = escolhida.iloc[0].to_dict()
            _dialog_detalhes(linha, notas[(notas["loja_id"] == linha["loja_id"]) & (notas["produto_id"] == produto_id)],
                             laboratorio)

    if not produtos.empty:
        ui.controles_paginacao(pagina, _KEY)


def settings_periodo(key: str) -> int:
    """O período escolhido (o campo é desenhado depois, na linha de filtros:
    as opções de Categoria dependem do resultado do período)."""
    valor = st.session_state.get(f"{key}_periodo")
    return valor if valor in settings.periodos_meses_opcoes else settings.periodos_meses_opcoes[0]
