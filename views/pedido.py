"""Assistente de pedido — sugestão de compra de uma loja, a partir das
vendas, compras e estoque que a rotina da madrugada traz da API do GPS
(pacote `pedido/`). Na tela o nome é "Assistente de pedido" (01/10/2026);
por dentro (código, tabelas, pastas do Spaces) continua "pedido".

Regras do cálculo: pedido/calculo.py. De onde vêm os dados: pedido/pronto.py
(Spaces) + base de categorias + Base Genéricos (banco).

Fluxo ao abrir a loja (grill-me de 01/10/2026), nesta ordem:
1. pop-up "Estoque negativo" — corrigir manualmente ou considerar todos zero;
2. pop-up "Produtos sem classificação" — informativo, "Entendi";
   os dois aparecem UMA vez por foto do GPS, por usuário + loja
   (`pedido_avisos_vistos`);
3. loja com personalização (Configurações de Pedidos): pílulas
   "Personalizado desta loja" / "Padrão", nenhuma marcada — a lista só
   aparece depois da escolha. Loja sem personalização: direto, com o padrão.

Layout (02/10/2026): a loja vem da BARRA LATERAL (views/loja_barra.py),
que vale pra todas as telas; aqui, o título "Assistente de pedido" (a data
dos dados foi pro pé da barra), pílulas de
parâmetros, avisos contra pedido duplicado, 4 cards (do PEDIDO INTEIRO),
linha de filtros (busca, Categoria, Fabricante, Ocultar giro baixo — sem
pop-up desde 01/10/2026), tabela ("a revisar" no topo, em âmbar) e rodapé
fixo (Salvo automaticamente · hh:mm, Descartar, Listas (N), Exportar
selecionados (N)). O botão "Estoque negativo" saiu (o pop-up ao abrir a loja
já pergunta); "Listas" saiu do topo e virou o botão do rodapé, com as abas
"Salvar como lista" e "Listas salvas". Cada edição e marcação grava na hora
(integrations/pedido_area.py), por usuário + loja.

Desempenho: ler a loja do Spaces e calcular fica em cache por loja (5 min),
pela versão das categorias/genéricos e pelos parâmetros escolhidos.
Marcar, editar, filtrar e paginar só releem a área de trabalho (1 consulta
pequena) e aplicam em memória (~25 ms na Hudson, medido em 29/09/2026).
"""
from __future__ import annotations

import datetime as dt
import html
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core import analise, auth, theme, ui
from core.config import settings
from core.db import get_session
from pedido import calculo
from pedido import categorias as cat
from pedido import configuracao as cfg
from pedido.configuracao import ConfigPedido
from views import analise_comum as comum
from views import loja_barra
from views import tabela as tb

_KEY = "pedido"
_ORDEM_PADRAO = ("valor", False)  # o que mais vende primeiro (coluna oculta); "a revisar" antes de tudo
_ABRIR_NEGATIVOS = f"{_KEY}_abrir_negativos"   # "Corrigir manualmente" → abre a lista no rerun seguinte


# ---------------------------------------------------------------------------
# Dados (com cache)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def configuracao() -> ConfigPedido:
    """A vigente (Configurações de Pedidos), conferida no banco no máximo a
    cada 30 s; gravar na tela de configurações limpa este cache na hora."""
    from integrations import configuracao_pedido

    with get_session() as session:
        return configuracao_pedido.vigente(session).config


def parametros(config: ConfigPedido) -> calculo.Parametros:
    return config.parametros(settings.analise.limite_bonificacao)


@st.cache_data(ttl=30, show_spinner=False)
def _versoes() -> tuple:
    from integrations import categorias as categorias_integ
    from integrations import pedido_tela

    with get_session() as session:
        return categorias_integ.versao(session), pedido_tela.versao_genericos(session)


@st.cache_data(max_entries=2, show_spinner=False)
def _base_categorias(versao: tuple) -> pd.DataFrame:
    from integrations import categorias as categorias_integ

    with get_session() as session:
        return categorias_integ.tabela(session)


@st.cache_data(max_entries=2, show_spinner=False)
def _genericos(versao: tuple) -> pd.DataFrame:
    from integrations import pedido_tela

    with get_session() as session:
        return pedido_tela.genericos(session)


@st.cache_data(ttl=300, max_entries=30, show_spinner="Calculando o pedido da loja...")
def _calcular(empresa: str, loja: str, meses: int, versoes: tuple, params: calculo.Parametros,
              correcoes: tuple = ()) -> tuple[pd.DataFrame, dict] | None:
    """`versoes` e `params` só entram na chave do cache. 5 min: os brutos só
    mudam de madrugada, mas uma foto nova precisa aparecer no mesmo dia."""
    from pedido import pronto
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    p = pronto.atualizar(do_ambiente(), Chaves(settings.pedido.prefixo), empresa, loja, meses)
    if p is None:
        return None
    linhas = calculo.calcular(p, _base_categorias(versoes[0]), _genericos(versoes[1]), params, dict(correcoes))
    return linhas, p.meta


def invalidar() -> None:
    """Chamar depois de mudar categorias ou genéricos (aba Dados)."""
    _versoes.clear()
    _calcular.clear()
    configuracao.clear()


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def _numero(valor, casas: int = 0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    texto = f"{float(valor):,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _moeda(valor) -> str:
    return "—" if valor is None or pd.isna(valor) else ui.formatar_moeda(float(valor))


def _data(valor) -> str:
    if not isinstance(valor, str) or not valor:
        return "—"
    ano, mes, dia = valor[:10].split("-")
    return f"{dia}/{mes}/{ano[2:]}"


def _hora_local(momento: dt.datetime | None) -> str:
    """Horário de Brasília (o banco grava em UTC)."""
    if momento is None:
        return ""
    return momento.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(settings.pedido.fuso)).strftime("%H:%M")


def _rotulo_loja(l: dict) -> str:
    local = "/".join(x for x in (l.get("cidade"), l.get("uf")) if x)
    codigo = f" · cód. {l['legacy_id']}" if l.get("legacy_id") else ""
    return f"{(l['razao_social'] or '').strip()} — {local}{codigo}"


# ---------------------------------------------------------------------------
# Tabela
# ---------------------------------------------------------------------------

def _colunas(p: calculo.Parametros) -> list[tb.ColunaTabela]:
    # Laboratório embaixo do produto e Mín./Máx. numa coluna só: abrem espaço
    # pra seleção, Status e Subtotal caberem em 1280 px (Q7 de 29/09/2026).
    return [
        # Produto até 150 px e sem "?" em Curva/Subtotal: com seleção, Status e
        # Subtotal a tabela passava 48 px da borda em 1280 px (29/09/2026).
        tb.ColunaTabela("nome", "Produto", "fit-content(150px)"),
        # "auto" + tags sempre empilhadas: a coluna fica da largura da maior
        # tag. Com fit-content(140px) e 2 tags lado a lado que não cabiam, a
        # coluna ficava ~30 px mais larga que o texto e o vão visível até a
        # Curva saía desigual no teste visual (29/09/2026).
        tb.ColunaTabela(None, "Status", id="status"),
        tb.ColunaTabela("curva", "Curva"),
        tb.ColunaTabela("preco", "Última compra", direita=True, numerica=True,
                        dica="A compra mais recente não bonificada, por unidade, sem ST. Compra fora do padrão da "
                             "loja fica de fora (\"a revisar\"). Sem compra: custo de cadastro do GPS (aproximado). "
                             "Cadastro e nota muito diferentes: vale o que for coerente com o preço de venda."),
        # "Venda 90 dias" no lugar de "Demanda/dia" (01/10/2026): "vendeu 15
        # em 90 dias" se entende; "0,17 por dia" parecia contradizer a
        # sugestão. Só exibição — a sugestão segue a demanda da janela.
        tb.ColunaTabela("venda_90d", "Venda 90 dias", direita=True, numerica=True,
                        dica="Unidades vendidas nos últimos 90 dias. A sugestão usa a média por dia da janela "
                             "de vendas inteira (Configurações de Pedidos)."),
        tb.ColunaTabela("estoque_max", "Mín./Máx.", direita=True, numerica=True,
                        dica=f"Mín. = estoque para {p.dias_ruptura} dias de venda (abaixo: ruptura próxima). Máx. = "
                             "onde o pedido + o estoque devem chegar."),
        tb.ColunaTabela("estoque", "Est. atual", direita=True, numerica=True),
        tb.ColunaTabela("quantidade", "Sugestão", direita=True, numerica=True,
                        dica="A quantidade do pedido. Digite outro número para mudar; em azul = alterado à mão."),
        tb.ColunaTabela("subtotal", "Subtotal", direita=True, numerica=True),
    ]


_CLASSE_STATUS = {
    calculo.NEGATIVO: "selo-perigo", calculo.RUPTURA: "selo-ruptura", calculo.RUPTURA_PROXIMA: "selo-sugestao",
    calculo.CORRIGIDO: "selo-alterado", calculo.ALTERADO: "selo-alterado",
    calculo.SEM_CLASSIFICACAO: "selo-neutro", calculo.GIRO_BAIXO: "selo-neutro",
}


def _dica_status(tag: str, r: dict, p: calculo.Parametros) -> str | None:
    if tag == calculo.NEGATIVO:
        return (f"EAN negativo no GPS: {r['eans_negativos']}. Conta como 0 no cálculo; corrija em "
                "\"Estoque negativo\", no topo.")
    if tag == calculo.RUPTURA:
        return "Estoque zerado." if p.ruptura_unidades == 0 else f"Estoque de até {_numero(p.ruptura_unidades)} unidade(s)."
    if tag == calculo.RUPTURA_PROXIMA:
        return f"O estoque dá para {p.dias_ruptura} dia(s) de venda ou menos."
    if tag == calculo.CORRIGIDO:
        return "O GPS mostra estoque negativo; vale o estoque corrigido aqui até a próxima foto sem negativo."
    if tag == calculo.SEM_CLASSIFICACAO:
        return (f"Produto sem categoria: sugestão com {p.dias_sem_classificacao} dias estimados. Classifique em "
                "Dados → Pendências → Sem Classificação.")
    if tag == calculo.GIRO_BAIXO:
        return f"No máximo {_numero(p.giro_baixo_max_unidades)} unidade(s) vendida(s) nos últimos {p.giro_baixo_dias} dias."
    if tag == calculo.ALTERADO:
        return f"Quantidade digitada à mão (o sistema sugeria {_numero(r['sugestao'])})."
    return None


def _celula_produto(r: dict) -> list:
    # Só nome e categoria (pedido de 29/09/2026): EAN e laboratório saíram da
    # célula — a busca continua achando por eles e a exportação leva os EANs.
    linhas = [tb.pedaco(r["nome"], "forte quebra")]
    if r["categoria"]:
        categoria = r["categoria"]
        linhas.append(tb.pedaco(categoria.capitalize() if categoria.isupper() else categoria, "aux"))
    return tb.celula(*linhas)


def _celula_status(r: dict, p: calculo.Parametros) -> list:
    tags = [tb.pedaco(t, _CLASSE_STATUS[t], _dica_status(t, r, p)) for t in r["status"] if t in _CLASSE_STATUS]
    if not tags:
        return tb.celula(tb.pedaco("—", "aux"))
    # Uma embaixo da outra ("se não couber", pedido de 29/09/2026 — na
    # coluna do Pedido, duas lado a lado nunca cabem em 1280 px).
    return tb.celula(tb.pilha(tags))


def _cadastro_nota_venda(r: dict) -> str:
    return (f"cadastro {_moeda(r['custo_cadastro'])} · nota {_moeda(r['preco_nota'])} · "
            f"preço de venda {_moeda(r['preco_venda'])}")


def _dica_revisar(r: dict) -> str:
    motivo = r.get("revisar_motivo")
    if motivo == calculo.MOTIVO_DIVERGENCIA:
        return (f"Custo de cadastro e nota muito diferentes ({_cadastro_nota_venda(r)}) e o preço de venda não "
                "mostra qual está certo. Usada a nota; confira a fração no GPS.")
    if motivo == calculo.MOTIVO_CADASTRO:
        return (f"Sem compra na janela e o custo de cadastro ({_moeda(r['custo_cadastro'])}) está fora da faixa "
                f"coerente com o preço de venda ({_moeda(r['preco_venda'])}). Confira o cadastro no GPS.")
    if pd.notna(r["compra_ignorada_preco"]):
        return (f"A compra de {_data(r['compra_ignorada_data'])} a {_moeda(r['compra_ignorada_preco'])} ficou de "
                "fora: muito diferente das outras compras deste produto.")
    return "Todas as compras estão fora do padrão; usada a mais recente."


def _dica_custo_ajustado(r: dict) -> str:
    if r.get("custo_ajustado") == "cadastro":
        return (f"A nota ({_moeda(r['preco_nota'])}) está fora da faixa coerente com o preço de venda "
                f"({_moeda(r['preco_venda'])}) — provavelmente sem a fração. Usado o custo de cadastro "
                f"{_moeda(r['custo_cadastro'])}.")
    return (f"O custo de cadastro do GPS ({_moeda(r['custo_cadastro'])}) está fora da faixa coerente com o preço "
            f"de venda ({_moeda(r['preco_venda'])}); a nota está certa e foi usada. Vale corrigir o cadastro no GPS.")


def _celula_compra(r: dict) -> list:
    linhas = [tb.pedaco(_moeda(r["preco"]))]
    if r["preco_origem"] == "compra":
        detalhe = (f"VlrUnitario {_moeda(r['compra_vlr_unitario'])}, desconto "
                   f"{_numero(r['compra_vlr_desconto'] or 0, 2)}%, embalagem de {_numero(r['compra_fracao'])} un."
                   + (f" — {r['compra_fornecedor']}" if isinstance(r["compra_fornecedor"], str) else ""))
        linhas.append(tb.pedaco(_data(r["compra_data"]), "aux", dica=detalhe))
    elif r["preco_origem"] == "sem":
        linhas = [tb.pedaco("—", "aux"), tb.pedaco("sem compra", "aux")]
    for t in r["tags_preco"]:
        if t == calculo.BONIFICADO:
            linhas.append(tb.pedaco(t, "selo-sucesso", "Houve bonificação (preço abaixo de R$ 0,10) depois desta "
                                                      "compra; ela não entra no preço."))
        elif t == calculo.A_REVISAR:
            linhas.append(tb.pedaco(t, "selo-neutro", _dica_revisar(r)))
        elif t == calculo.CUSTO_AJUSTADO:
            linhas.append(tb.pedaco(t, "selo-alterado", _dica_custo_ajustado(r)))
        elif t == calculo.CUSTO_CADASTRO:
            linhas.append(tb.pedaco(t, "selo-neutro", "Sem compra paga na janela: preço de compra do cadastro do GPS "
                                                      "(aproximado — bate com a última compra em só ~metade dos produtos)."))
    return tb.celula(*linhas)


def _celula_estoque(r: dict) -> list:
    if r["estoque_corrigido"]:
        return tb.celula(tb.pedaco(_numero(r["estoque"])),
                         tb.pedaco(f"GPS: {_numero(r['estoque_gps'])}", "aux", "Estoque negativo no GPS, corrigido aqui."))
    classe = ("txt-vermelho" if r["negativo"] else "txt-laranja" if r["ruptura"]
              else "txt-ambar" if r["ruptura_proxima"] else None)
    linhas = [tb.pedaco(_numero(r["estoque"]), classe)]
    if r["negativo"]:
        linhas.append(tb.pedaco(f"GPS: {_numero(r['estoque_gps'])}", "aux txt-vermelho",
                                f"EAN negativo no GPS: {r['eans_negativos']}. Conta como 0 no cálculo."))
    return tb.celula(*linhas)


def _celula_sugestao(r: dict) -> list:
    if r["alterado"]:
        dica = f"Sugerido: {_numero(r['sugestao'])}. Digite {_numero(r['sugestao'])} para voltar à sugestão."
        linhas = [tb.entrada(r["quantidade"], "destaque alterada", dica)]
    else:
        linhas = [tb.entrada(r["quantidade"], "destaque", "Digite outra quantidade para mudar o pedido.")]
    if r["aviso_embalagem"]:
        frac = _numero(r["compra_fracao"])
        linhas.append(tb.pedaco(f"emb. {frac}", "selo-sugestao",
                                f"A última compra veio em embalagem de {frac} unidades; "
                                f"{_numero(r['quantidade'])} não fecha uma embalagem inteira."))
    return tb.celula(*linhas)


def _linha(r: dict, p: calculo.Parametros) -> dict:
    return {
        "id": r["linha"],
        # "a revisar" em destaque âmbar (Q14 de 01/10/2026).
        "classe": "revisar" if r.get("a_revisar") else None,
        "celulas": {
            "nome": _celula_produto(r),
            "status": _celula_status(r, p),
            "curva": tb.celula(tb.pedaco(r["curva"], "forte")),
            "preco": _celula_compra(r),
            "venda_90d": tb.celula(tb.pedaco(_numero(r["venda_90d"]))),
            "estoque_max": tb.celula(tb.pedaco(f"{_numero(r['estoque_min'])} / {_numero(r['estoque_max'])}")),
            "estoque": _celula_estoque(r),
            "quantidade": _celula_sugestao(r),
            "subtotal": tb.celula(tb.pedaco(_moeda(r["subtotal"]), None if r["selecionado"] else "aux")),
        },
    }


# ---------------------------------------------------------------------------
# Cards e faixa de filtros ativos
# ---------------------------------------------------------------------------

def _indicadores(pedido: pd.DataFrame, p: calculo.Parametros) -> None:
    """Os 4 cards — sobre o PEDIDO INTEIRO, não sobre o filtro da tela (Q3b
    de 29/09/2026). Desde 01/10/2026: as unidades viraram subtítulo do
    Orçamento e o 4º card é "Sem classificação", com "Saiba mais". Um card
    por coluna: o 4º leva um botão do Streamlit (HTML não aceita widget) —
    o botão cobre o card inteiro, transparente (core/theme.py)."""
    i = calculo.indicadores(pedido)
    sem = int((pedido["grupo"] == cat.SEM_CLASSIFICACAO).sum()) if not pedido.empty else 0
    cards = [
        {"icone": "moeda", "tom": "verde", "valor": ui.formatar_moeda(i.orcamento), "label": "Orçamento total",
         "sub": f"{ui.formatar_numero(i.unidades)} unidades marcadas"},
        {"icone": "produto", "tom": "roxo", "valor": ui.formatar_numero(i.itens), "label": "Itens com sugestão",
         "sub": f"{ui.formatar_numero(i.marcados)} marcados"},
        {"icone": "alerta", "tom": "ambar", "valor": ui.formatar_numero(i.ruptura), "label": "Ruptura",
         "sub": f"{ui.formatar_numero(i.ruptura_sem_pedido)} sem pedido"},
        {"icone": "duvida", "tom": "azul", "valor": ui.formatar_numero(sem), "label": "Sem classificação",
         "sub": "Saiba mais ›"},
    ]
    with st.container(key="pedido-cards"):
        colunas = st.columns(4, gap="small")
        for coluna, card in zip(colunas[:3], cards):
            with coluna:
                theme.kpis([card], unico=True)
        with colunas[3], st.container(key="pedido-card-sem"):
            theme.kpis([cards[3]], unico=True)
            if st.button("Saiba mais sobre os produtos sem classificação", key=f"{_KEY}_saiba_mais"):
                _dialog_saiba_mais(sem, p.dias_sem_classificacao)


# ---------------------------------------------------------------------------
# Pop-ups
# ---------------------------------------------------------------------------

def _texto_sem_classificacao(n: int, dias: int) -> str:
    return (f"**{ui.formatar_numero(n)} produto(s) desta sugestão estão sem classificação.** A inteligência de pedido "
            "não se aplica a eles: sem categoria, o sistema não sabe se são medicamento ou perfumaria, nem quantos "
            f"dias de estoque usar. Para evitar ruptura, sugerimos um pedido mínimo: estoque para **{dias} dias** de "
            "venda.  \nA classificação é feita pela administração; classificado, o produto passa a seguir a regra "
            "normal.")


@st.dialog("Produtos sem classificação")
def _dialog_saiba_mais(n: int, dias: int) -> None:
    st.markdown(_texto_sem_classificacao(n, dias))


def _responder_aviso(usuario_id: int, loja_id: int, tipo: str, foto: str, resposta: str) -> None:
    from integrations import pedido_area

    with get_session() as session:
        pedido_area.marcar_aviso_visto(session, usuario_id, loja_id, tipo, foto, resposta)


@st.dialog("Estoque negativo", dismissible=False)
def _alerta_negativos(usuario_id: int, loja_id: int, foto: str, n: int) -> None:
    """1º pop-up ao abrir a loja. Sem X: a resposta é o que libera a lista."""
    from integrations import pedido_area

    st.markdown(f"**{ui.formatar_numero(n)} produto(s) com estoque negativo.** Deseja corrigir manualmente ou "
                "considerar todos com estoque zero?")
    st.caption("Com estoque zero, a sugestão cobre o máximo inteiro do produto. Corrigindo, a linha é recalculada "
               "com o estoque que você digitar; os que ficarem sem correção contam como zero. A pergunta volta "
               "quando chegar uma foto nova do estoque.")
    c1, c2 = st.columns(2)
    if c1.button("Corrigir manualmente", use_container_width=True):
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_NEGATIVO, foto, "corrigir")
        st.session_state[_ABRIR_NEGATIVOS] = True   # um pop-up não abre outro: abre no rerun
        st.rerun()
    if c2.button("Considerar todos zero", type="primary", use_container_width=True):
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_NEGATIVO, foto, "zero")
        st.rerun()


@st.dialog("Produtos sem classificação", dismissible=False)
def _alerta_sem_classificacao(usuario_id: int, loja_id: int, foto: str, n: int, dias: int) -> None:
    """2º pop-up ao abrir a loja: só informa (classificar é da administração)."""
    from integrations import pedido_area

    st.markdown(_texto_sem_classificacao(n, dias))
    if st.button("Entendi", type="primary", use_container_width=True):
        _responder_aviso(usuario_id, loja_id, pedido_area.AVISO_SEM_CLASSIFICACAO, foto, "entendi")
        st.rerun()


def _resumo_config(c: ConfigPedido) -> str:
    proprias = f" · {len(c.dias_por_categoria)} categoria(s) com dias próprios" if c.dias_por_categoria else ""
    return (f"medicamento {c.dias_medicamento} dias · perfumaria {c.dias_perfumaria} · "
            f"ruptura próxima {c.dias_ruptura} dias{proprias}")


def _escolher_parametros(loja_id: int, personalizado: ConfigPedido, padrao: ConfigPedido) -> str | None:
    """Pílulas da loja com personalização (Q5i de 01/10/2026): nenhuma vem
    marcada — a lista só aparece depois da escolha; trocar recalcula na hora
    (as quantidades digitadas continuam: são da área de trabalho)."""
    rotulos = {"loja": "Personalizado desta loja", "padrao": "Padrão"}
    with st.container(key="pedido-parametros", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown('<span class="rmc-pedido-rotulo">Parâmetros</span>', unsafe_allow_html=True)
        return st.pills(
            "Parâmetros", list(rotulos), format_func=rotulos.get, key=f"{_KEY}_param_{loja_id}",
            label_visibility="collapsed",
            help=f"Personalizado desta loja: {_resumo_config(personalizado)}.  \nPadrão: {_resumo_config(padrao)}.",
        )


def _linha_filtros(loja_id: int, todas: pd.DataFrame) -> tuple[str | None, tuple]:
    """Busca, Categoria, Fabricante e Ocultar giro baixo, numa linha acima da
    tabela (sem pop-up desde 01/10/2026). As opções vêm do que a loja tem na
    lista — calculadas em memória com o resto da loja (~4 ms na Hudson) —
    sem depender do "Ocultar giro baixo": uma opção que some da lista com a
    escolha feita quebraria o campo."""
    opcoes = calculo.opcoes_de_valor(todas[todas["listar"]])
    categorias = [v for c, v in opcoes if c == "categoria"]
    fabricantes = [v for c, v in opcoes if c == "laboratorio"]
    with st.container(key="pedido-filtros-linha", horizontal=True, vertical_alignment="center", gap="small"):
        busca = st.text_input("Buscar", placeholder="Buscar produto, EAN ou fabricante", key=f"{_KEY}_busca",
                              label_visibility="collapsed")
        escolhidas = st.multiselect("Categoria", categorias, placeholder="Categoria", key=f"{_KEY}_cat_{loja_id}",
                                    label_visibility="collapsed")
        fabs = st.multiselect("Fabricante", fabricantes, placeholder="Fabricante", key=f"{_KEY}_fab_{loja_id}",
                              label_visibility="collapsed")
        st.toggle("Ocultar giro baixo", value=True, key=f"{_KEY}_giro",
                  help="Ligado: os itens de giro baixo saem do pedido (não aparecem, não contam nos cards e não "
                       "vão na exportação).")
    valores = tuple([("categoria", v) for v in escolhidas] + [("laboratorio", v) for v in fabs])
    return (busca or None), valores


@st.dialog("Estoque negativo", width="large")
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
    c1, c2 = st.columns(2)
    if c1.button("Fechar", use_container_width=True):
        st.rerun()
    if c2.button("Salvar", type="primary", use_container_width=True):
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
            for e in erros:
                st.error(e)
            return
        gps = dict(zip(negativos["linha"], negativos["estoque_gps"].astype(float)))
        with get_session() as session:
            gravadas, removidas = pedido_rascunho.salvar_correcoes(session, loja["id"], valores, gps,
                                                                   meta.get("data_foto"), usuario["nome"])
        partes = [f"{gravadas} correção(ões) salva(s)"] + ([f"{removidas} removida(s)"] if removidas else [])
        st.session_state[f"{_KEY}_msg"] = ("success", "Estoque negativo: " + ", ".join(partes) + ".")
        st.rerun()


@st.dialog("Listas", width="large")
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
            f"**{html.escape(_rotulo_loja(loja))}**  \n{ui.formatar_numero(i.marcados)} itens marcados · "
            f"{ui.formatar_numero(i.unidades)} unidades · {ui.formatar_moeda(i.orcamento)} · "
            f"{dt.datetime.now(ZoneInfo(settings.pedido.fuso)):%d/%m/%Y %H:%M}"))
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
            st.session_state[f"{_KEY}_msg"] = ("success", f"Lista \"{nome.strip()}\" salva.")
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
                        st.session_state[f"{_KEY}_msg"] = ("success", f"Lista \"{l.nome}\" aberta na sua área de trabalho.")
                        st.rerun()
                if c3.button("Excluir", key=f"{_KEY}_excluir_{l.id}", use_container_width=True):
                    with get_session() as session:
                        pedido_area.excluir_lista(session, l.id)
                    st.session_state[f"{_KEY}_msg"] = ("success", f"Lista \"{l.nome}\" excluída.")
                    st.rerun()


@st.dialog("Descartar alterações")
def _dialog_descartar(loja: dict, usuario: dict, alterados: int) -> None:
    from integrations import pedido_area

    st.write(f"As {alterados} alteração(ões) da sua área de trabalho nesta loja (quantidades digitadas e marcações) "
             "voltam para a sugestão do sistema. As listas salvas não mudam.")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", use_container_width=True):
        st.rerun()
    if c2.button("Descartar", type="primary", use_container_width=True):
        with get_session() as session:
            pedido_area.descartar(session, usuario["id"], loja["id"])
        st.session_state[f"{_KEY}_msg"] = ("success", "Alterações descartadas: o pedido voltou à sugestão.")
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


@st.dialog("Exportar")
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
            st.session_state[f"{_KEY}_msg"] = ("success", "Pedido exportado" + (
                f"; a lista \"{lista}\" foi encerrada." if lista else "."))
            st.session_state[f"{_KEY}_exportou"] = True

        st.download_button(f"Baixar {formato}", data=_arquivo_exportacao(tabela, formato), file_name=nome,
                           mime=_XLSX if formato == "Excel" else "text/csv", type="primary", use_container_width=True,
                           on_click=_registrar, key=f"{_KEY}_baixar")
    elif tipo == "completa":
        abas = {"Pedido": calculo.tabela_completa(pedido), "Giro baixo": calculo.giro_baixo(todas)}
        st.caption(f"Pedido: {ui.formatar_numero(len(abas['Pedido']))} itens · Giro baixo: "
                   f"{ui.formatar_numero(len(abas['Giro baixo']))} produtos.")
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


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

def render() -> None:
    with theme.tela("pedido"):
        _render()


def _titulo() -> None:
    """Só o título: a data dos dados foi pro pé da barra lateral (02/10/2026)."""
    st.markdown('<div class="rmc-pedido-titulo">Assistente de pedido</div>', unsafe_allow_html=True)


def _render() -> None:
    from integrations import configuracao_pedido, pedido_area, pedido_rascunho

    usuario = auth.usuario_atual()
    # A loja vem da barra lateral (02/10/2026). "Todas" não carrega: a
    # sugestão, o estoque e a exportação são de uma loja por vez.
    escolha = loja_barra.escolha(usuario)
    loja = escolha.loja
    if not loja_barra.lojas_do_usuario(usuario):
        st.info("Nenhuma loja ligada ao seu usuário. Peça ao administrador para liberar a sua loja.")
        return

    if loja is None or not loja.get("empresa_gps"):
        if loja is None:
            st.info("Escolha uma loja na barra lateral: o assistente trabalha com uma loja por vez.")
        else:
            st.info("Loja sem vínculo com o GPS: o sistema ainda não sabe qual loja do GPS é esta. "
                    "O administrador liga as duas em Dados → Pendências → Vínculo de lojas GPS.")
        return

    uid = usuario["id"] if usuario.get("id") is not None else -1
    with get_session() as session:
        correcoes = pedido_rascunho.correcoes(session, loja["id"])
        area = pedido_area.area(session, uid, loja["id"])
        listas = pedido_area.listas(session, loja["id"])
        avisos = pedido_area.avisos(session, uid, usuario["nome"], loja["id"], dt.datetime.utcnow().date())
        vistos = pedido_area.avisos_vistos(session, uid, loja["id"])
        personalizacao = configuracao_pedido.da_loja(session, loja["id"])
    padrao = configuracao()
    # Loja com personalização: vale a pílula escolhida; enquanto nenhuma for
    # escolhida, os pop-ups contam pelo padrão (a lista ainda não aparece).
    escolha = st.session_state.get(f"{_KEY}_param_{loja['id']}") if personalizacao else None
    config = cfg.aplicar(padrao, personalizacao) if escolha == "loja" else padrao
    p = parametros(config)
    resultado = _calcular(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados, _versoes(), p,
                          tuple(sorted((l, c.estoque) for l, c in correcoes.items())))
    if resultado is None:
        st.info("Os dados desta loja ainda não foram coletados. A rotina da madrugada traz as vendas, as "
                "compras e o estoque do GPS; a loja aparece aqui no dia seguinte à primeira coleta.")
        return
    todas, meta = resultado
    todas = calculo.aplicar_area(todas, area.itens)
    ocultar = st.session_state.get(f"{_KEY}_giro", True)
    pedido = calculo.lista(todas, ocultar_giro_baixo=ocultar)
    # Só os do PEDIDO: a loja inteira tinha 60 produtos com EAN negativo na
    # Hudson, a maioria fora da lista (estoque acima do máximo).
    negativos = pedido[pedido["negativo"] | pedido["estoque_corrigido"]]
    n_negativos = int(negativos["negativo"].sum())
    n_sem = int((pedido["grupo"] == cat.SEM_CLASSIFICACAO).sum()) if not pedido.empty else 0

    with st.container(key="pedido-topo"):
        _titulo()
    # Um pop-up por vez (Streamlit): o que estiver pendente abre, na ordem
    # combinada — negativos, sem classificação. Sem botão "Estoque negativo"
    # (02/10/2026): corrigir é pelo pop-up, uma vez por foto do GPS.
    foto = meta["data_foto"]
    if st.session_state.pop(_ABRIR_NEGATIVOS, False):
        _dialog_negativos(loja, negativos, meta, usuario)
    elif n_negativos and vistos.get(pedido_area.AVISO_NEGATIVO) != foto:
        _alerta_negativos(uid, loja["id"], foto, n_negativos)
    elif n_sem and vistos.get(pedido_area.AVISO_SEM_CLASSIFICACAO) != foto:
        _alerta_sem_classificacao(uid, loja["id"], foto, n_sem, p.dias_sem_classificacao)

    if personalizacao and _escolher_parametros(loja["id"], cfg.aplicar(padrao, personalizacao), padrao) is None:
        st.info("Esta loja tem parâmetros personalizados. Escolha acima com quais parâmetros ver a sugestão.")
        return

    for aviso in avisos:
        st.warning(aviso, icon=":material/group:")
    sem_vendas, sem_compras = meta.get("dias_sem_vendas", []), meta.get("dias_sem_compras", [])
    if sem_vendas:
        st.warning(f"{len(sem_vendas)} dia(s) sem vendas coletadas (a demanda usa só os dias com dado).")
    if sem_compras:
        st.info(f"Compras de {len(sem_compras)} dia(s) ainda não coletadas — nesses dias, o preço pode vir do "
                "custo de cadastro.")

    _indicadores(pedido, p)
    busca, valores = _linha_filtros(loja["id"], todas)
    filtros = calculo.Filtros(valores=valores, hoje=meta["data_foto"])
    linhas = calculo.filtrar(todas, busca=busca, ocultar_giro_baixo=ocultar, filtros=filtros)
    mensagem = st.session_state.pop(f"{_KEY}_msg", None)
    if mensagem:
        getattr(st, mensagem[0])(mensagem[1])

    ui.resetar_pagina_se_filtro_mudou(_KEY, f"{loja['id']}|{busca}|{valores}|{ocultar}|{escolha}")
    ordem = st.session_state.get(f"{_KEY}_ordem")
    if ordem is None:
        # Padrão (Q14 de 01/10/2026): os "a revisar" primeiro, depois o que
        # mais vende em valor. Clicar num título troca pela ordem da coluna.
        ordem = _ORDEM_PADRAO
        ordenadas = linhas.sort_values(["a_revisar", "valor"], ascending=[False, False], kind="stable")
    else:
        ordenadas = analise.ordenar(linhas, *ordem)
    fatia, pagina = comum.pagina(ordenadas, _KEY)
    sugestao_da_linha = dict(zip(todas["linha"], todas["sugestao"]))

    def _editar(linha: str, texto: str) -> None:
        texto = texto.strip()
        if not texto.isdigit():
            st.session_state[f"{_KEY}_msg"] = ("warning", "Quantidade não gravada: use um número inteiro, 0 ou maior.")
            return
        with get_session() as s2:
            pedido_area.alterar_quantidade(s2, uid, loja["id"], linha, int(texto), int(sugestao_da_linha.get(linha, 0)))

    def _marcar(marcacoes: dict[str, bool]) -> None:
        with get_session() as s2:
            pedido_area.marcar(s2, uid, loja["id"], marcacoes)

    tb.tabela(
        f"{_KEY}_tabela", _colunas(p), [_linha(r, p) for r in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        vazio=("Nenhum produto para pedir", "O estoque desta loja cobre o máximo de todos os produtos com esses filtros"),
        densa=True, ao_editar=_editar,
        selecao=dict(zip(fatia["linha"], fatia["selecionado"])), ao_marcar=_marcar,
    )
    if not linhas.empty:
        ui.controles_paginacao(pagina, _KEY)

    _rodape(loja, usuario, pedido, todas, area, listas)


def _rodape(loja: dict, usuario: dict, pedido: pd.DataFrame, todas: pd.DataFrame, area, listas: list) -> None:
    """Rodapé do Pedido. Fica fixo no pé da tela (CSS `st-key-rodape-pedido`
    em core/theme.py) — os botões ficam DENTRO da faixa fixa, nada clicável
    embaixo dela (regra da identidade visual: sticky sobre elemento clicável
    já causou clique que não funcionava)."""
    marcados = pedido[pedido["selecionado"] & (pedido["quantidade"] > 0)]
    with st.container(key="rodape-pedido"):
        # Botões na largura natural, alinhados à direita: em colunas iguais,
        # "Descartar alterações" quebrava em 2 linhas em 1280 px (29/09/2026).
        esq, dir_ = st.columns([1, 3.4], vertical_alignment="center")
        texto = (f"Salvo automaticamente · {_hora_local(area.atualizado_em)}" if area.atualizado_em is not None
                 else "Segue a sugestão")
        esq.markdown(f'<span class="rmc-muted">{texto}</span>', unsafe_allow_html=True)
        # "Ocultar giro baixo" saiu daqui pra linha de filtros (01/10/2026).
        with dir_.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if st.button("Descartar alterações", key=f"{_KEY}_descartar", disabled=not area.itens):
                _dialog_descartar(loja, usuario, len(area.itens))
            # "Listas (N)": o nº avisa, sem abrir nada, que já há pedido montado
            # pra esta loja (era a função do botão do topo, que saiu).
            if st.button(f"Listas ({len(listas)})" if listas else "Listas", key=f"{_KEY}_listas",
                         icon=":material/list_alt:"):
                _dialog_listas(loja, usuario, pedido, listas, area.lista_aberta_id, bool(area.itens))
            # Habilitado mesmo sem marcados: "Só giro baixo" e "Tabela
            # completa" não dependem da seleção (01/10/2026).
            if st.button(f"Exportar selecionados ({ui.formatar_numero(len(marcados))})", key=f"{_KEY}_exportar",
                         icon=":material/download:", type="primary", disabled=todas.empty):
                _dialog_exportar(loja, pedido, todas, usuario)
