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
import time
from dataclasses import dataclass
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
_ABRIR_NEGATIVOS = f"{_KEY}_abrir_negativos"
# Notificações (02/10/2026): nada fica "permanente" na tela — sucesso, erro,
# aviso e informação viram notificação no canto superior direito, com X e
# 5 s, cada tipo com o seu ícone (o ✅ de sucesso, o ❌ de erro…).
_ICONE_NOTIFICACAO = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
_AVISADOS = f"{_KEY}_avisados"   # avisos de estado já mostrados nesta sessão: (loja, texto)
_ESTADO = f"{_KEY}_estado"       # o que a tela lê do banco sobre a loja (ver `_estado_loja`)
_ESTADO_SEGUNDOS = 30


@dataclass
class _EstadoLoja:
    """Tudo o que a tela lê do banco sobre a loja e o usuário, guardado na
    sessão (P1 de 02/10/2026): antes eram 8 consultas a CADA clique (~1,1 s
    contra o banco em NY), e quase nada muda entre um clique e outro. Relê
    depois de 30 s (o que outra pessoa fez aparece com até 30 s de atraso) ou
    depois de uma ação da tela (`_invalidar_estado`). A área de trabalho,
    que muda a cada clique, é atualizada na cópia depois de gravar."""
    chave: tuple
    lido_em: float
    correcoes: dict
    area: object
    listas: list
    avisos: list
    vistos: dict
    personalizacao: dict


def _estado_loja(uid: int, usuario_nome: str, loja_id: int) -> _EstadoLoja:
    from integrations import configuracao_pedido, pedido_area, pedido_rascunho

    atual = st.session_state.get(_ESTADO)
    if atual is not None and atual.chave == (uid, loja_id) and time.monotonic() - atual.lido_em < _ESTADO_SEGUNDOS:
        return atual
    with get_session() as session:
        listas = pedido_area.listas(session, loja_id)
        estado = _EstadoLoja(
            chave=(uid, loja_id), lido_em=time.monotonic(),
            correcoes=pedido_rascunho.correcoes(session, loja_id),
            area=pedido_area.area(session, uid, loja_id),
            listas=listas,
            avisos=pedido_area.avisos(session, uid, usuario_nome, loja_id,
                                      dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date(),
                                      listas_da_loja=listas),
            vistos=pedido_area.avisos_vistos(session, uid, loja_id),
            personalizacao=configuracao_pedido.da_loja(session, loja_id),
        )
    st.session_state[_ESTADO] = estado
    return estado


def _invalidar_estado() -> None:
    st.session_state.pop(_ESTADO, None)


def _atualizar_area(uid: int, loja_id: int, mudar) -> None:
    """Depois de gravar uma quantidade/marcação: a cópia na sessão recebe o
    que acabou de ir pro banco (mesmas regras — integrations/pedido_area.py).
    `mudar(itens) → itens novos` parte da cópia ATUAL da sessão, não da área
    que a tela mostrava: em dois cliques rápidos (antes do redesenho), o
    segundo partia da versão anterior e apagava o primeiro da cópia."""
    from integrations import pedido_area

    estado = st.session_state.get(_ESTADO)
    if estado is not None and estado.chave == (uid, loja_id):
        estado.area = pedido_area.Area(mudar(estado.area.itens), dt.datetime.utcnow(), estado.area.lista_aberta_id)


def _concluir(tipo: str, texto: str) -> None:
    """Fim de uma ação que mudou dados (lista, exportação, correção…): a
    notificação no próximo redesenho e o estado relido do banco."""
    st.session_state[f"{_KEY}_msg"] = (tipo, texto)
    _invalidar_estado()


def _notificar(tipo: str, texto: str) -> None:
    st.toast(texto, icon=_ICONE_NOTIFICACAO.get(tipo, "ℹ️"), duration=5)


def _avisar_uma_vez(loja_id: int, tipo: str, texto: str) -> None:
    """Aviso de ESTADO (pedido duplicado, dia sem dado, parâmetros): a tela é
    redesenhada a cada clique, e o aviso reapareceria o tempo todo — mostra
    uma vez por loja na sessão (ao abrir ou trocar de loja)."""
    vistos = st.session_state.setdefault(_AVISADOS, set())
    if (loja_id, texto) not in vistos:
        vistos.add((loja_id, texto))
        _notificar(tipo, texto)   # "Corrigir manualmente" → abre a lista no rerun seguinte


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
        return categorias_integ.tabela(session, com_origem=False)   # o cálculo só usa ean + categoria


@st.cache_data(max_entries=2, show_spinner=False)
def _genericos(versao: tuple) -> pd.DataFrame:
    from integrations import pedido_tela

    with get_session() as session:
        return pedido_tela.genericos(session)


@st.cache_data(ttl=3600, max_entries=30, show_spinner="Calculando o pedido da loja...")
def _calcular(empresa: str, loja: str, meses: int, versoes: tuple, params: calculo.Parametros,
              correcoes: tuple = (), foto: str | None = None) -> tuple[pd.DataFrame, dict] | None:
    """`versoes`, `params` e `foto` só entram na chave do cache. `foto` = o
    marcador do estoque da empresa (data + hora em que a rotina o gravou,
    conferido a cada 5 min): foto nova → recalcula na hora. Antes o cache
    expirava a cada 5 min e recalculava sem dado novo (~0,7 s, medido em
    02/10/2026); a 1 h fica só de garantia pra dias de venda que a rotina
    completa depois da foto."""
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
    _invalidar_estado()   # personalização salva em Configurações: vale já, nesta sessão


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


def _identificacao_loja(l: dict) -> str:
    """"RAZÃO SOCIAL - 04.076.088/0001-86" (pop-up de Listas, 02/10/2026)."""
    return f"{(l.get('razao_social') or '').strip()} - {ui.formatar_cnpj(l.get('cnpj') or '')}"


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
        # Dicas (02/10/2026): frases curtas, um parágrafo por ideia ("\n\n"),
        # e "sistema de vendas" no lugar de "GPS" — é lá que a loja corrige.
        tb.ColunaTabela("preco", "Última compra", direita=True, numerica=True,
                        dica="A compra mais recente, por unidade e sem ST. Bonificações não entram."),
        # "Venda 90 dias" no lugar de "Demanda/dia" (01/10/2026): "vendeu 15
        # em 90 dias" se entende; "0,17 por dia" parecia contradizer a
        # sugestão. Só exibição — a sugestão segue a demanda da janela.
        # Quantidades centralizadas e dinheiro à direita (Q6 de 02/10/2026).
        tb.ColunaTabela("venda_90d", "Venda 90 dias", centro=True, numerica=True,
                        dica="Unidades vendidas nos últimos 90 dias.\n\nA sugestão usa a média por dia de todo "
                             "o período de vendas."),
        # "Ideal" no lugar de "Máx." (02/10/2026): é onde o estoque deve chegar.
        tb.ColunaTabela("estoque_max", "Mín./Ideal", centro=True, numerica=True,
                        dica=f"Mín.: estoque para {p.dias_ruptura} dias de venda; abaixo disso, ruptura próxima."
                             "\n\nIdeal: onde o estoque deve chegar com o pedido."),
        tb.ColunaTabela("estoque", "Est. atual", centro=True, numerica=True),
        tb.ColunaTabela("quantidade", "Sugestão", centro=True, numerica=True,
                        dica="A quantidade do pedido. Digite outro número para mudar.\n\nEm azul: alterada à mão."),
        tb.ColunaTabela("subtotal", "Subtotal", direita=True, numerica=True),
    ]


_CLASSE_STATUS = {
    calculo.A_REVISAR: "selo-revisar",
    calculo.NEGATIVO: "selo-perigo", calculo.RUPTURA: "selo-ruptura", calculo.RUPTURA_PROXIMA: "selo-sugestao",
    calculo.CORRIGIDO: "selo-alterado", calculo.ALTERADO: "selo-alterado",
    calculo.SEM_CLASSIFICACAO: "selo-neutro", calculo.GIRO_BAIXO: "selo-neutro",
    calculo.VENDA_PONTUAL: "selo-sucesso",
}


def _dica_status(tag: str, r: dict, p: calculo.Parametros) -> str | None:
    if tag == calculo.A_REVISAR:
        return _dica_revisar(r)
    if tag == calculo.NEGATIVO:
        return (f"O sistema de vendas mostra estoque negativo em: {r['eans_negativos']}.\n\n"
                "No cálculo, o negativo conta como zero.")
    if tag == calculo.RUPTURA:
        return "Estoque zerado." if p.ruptura_unidades == 0 else f"Estoque de até {_numero(p.ruptura_unidades)} unidade(s)."
    if tag == calculo.RUPTURA_PROXIMA:
        return f"O estoque cobre {p.dias_ruptura} dia(s) de venda ou menos."
    if tag == calculo.CORRIGIDO:
        return ("O sistema de vendas mostra estoque negativo.\n\nVale o estoque digitado aqui até o sistema de "
                "vendas deixar de mostrar negativo.")
    if tag == calculo.SEM_CLASSIFICACAO:
        return f"Produto sem categoria: sugestão com {p.dias_sem_classificacao} dias estimados."
    if tag == calculo.GIRO_BAIXO:
        return f"Vendeu no máximo {_numero(p.giro_baixo_max_unidades)} unidade(s) nos últimos {p.giro_baixo_dias} dias."
    if tag == calculo.VENDA_PONTUAL:
        unidades = f"{_numero(r['unidades_90d'])} unidade" + ("" if r["unidades_90d"] == 1 else "s")
        entre = ("os produtos que mais faturam na loja" if r["curva_valor"] == "A"
                 else "os produtos que faturam bem na loja")
        return (f"Produto de alto valor que vende pouco.\n\nVendeu {unidades} nos últimos "
                f"{p.giro_baixo_dias} dias, mas essa venda o coloca entre {entre}.\n\n"
                "Vem desmarcado: só entra no pedido se você marcar.")
    if tag == calculo.ALTERADO:
        return f"Quantidade digitada à mão.\n\nO sistema sugeria {_numero(r['sugestao'])}."
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


def _preco_usado(r: dict) -> str:
    """O preço que o pedido usa, dito como o usuário vê na coluna: com o
    custo ajustado, é o cadastro — a dica do "A revisar" dizia "usa a compra
    de R$ 40,29" no Carvedilol, que estava a R$ 3,53 (bug de 02/10/2026)."""
    if r.get("custo_ajustado") == "cadastro":
        return (f"o pedido usa o custo de cadastro do sistema de vendas ({_moeda(r['preco'])}), que combina com o "
                f"preço de venda ({_moeda(r['preco_venda'])})")
    return f"o pedido usa a compra de {_data(r['compra_data'])}, de {_moeda(r['preco'])}"


def _dica_revisar(r: dict) -> str:
    """Por que o preço está "A revisar" — o que aconteceu, qual preço o
    pedido usou e onde corrigir (catálogo de dicas de 02/10/2026)."""
    motivo = r.get("revisar_motivo")
    if motivo == calculo.MOTIVO_DIVERGENCIA:
        return (f"Preço a conferir.\n\nO custo de cadastro do sistema de vendas ({_moeda(r['custo_cadastro'])}) e a "
                f"última nota ({_moeda(r['preco_nota'])}) estão muito diferentes, e o preço de venda "
                f"({_moeda(r['preco_venda'])}) não mostra qual está certo.\n\nO pedido usa a nota. Confira a "
                "fração no sistema de vendas.")
    if motivo == calculo.MOTIVO_ACIMA_VENDA:
        return (f"Preço a conferir.\n\nA nota de {_data(r['compra_ignorada_data'])}, de "
                f"{_moeda(r['compra_ignorada_preco'])} por unidade, passava do preço de venda "
                f"({_moeda(r['preco_venda'])}): provavelmente lançada sem a fração. O pedido usa a compra de "
                f"{_data(r['compra_data'])}, de {_moeda(r['preco'])}.\n\nConfira as notas no sistema de vendas.")
    if motivo == calculo.MOTIVO_CADASTRO:
        return (f"Preço a conferir.\n\nSem compra nos últimos meses, o preço vem do cadastro do sistema de vendas "
                f"({_moeda(r['custo_cadastro'])}). Esse valor não combina com o preço de venda "
                f"({_moeda(r['preco_venda'])}).\n\nConfira o cadastro no sistema de vendas.")
    if pd.notna(r["compra_ignorada_preco"]):
        return (f"Preço a conferir.\n\nA compra de {_data(r['compra_ignorada_data'])}, de "
                f"{_moeda(r['compra_ignorada_preco'])} por unidade, está muito diferente das outras compras deste "
                f"produto e não foi usada como preço: {_preco_usado(r)}.\n\nConfira as notas no sistema de vendas. "
                "Geralmente é a embalagem ou a fração lançada errada.")
    return (f"Preço a conferir.\n\nAs compras deste produto variam muito entre si, e nenhuma serve de referência "
            f"segura: {_preco_usado(r)}.\n\nConfira as notas no sistema de vendas.")


def _dica_custo_ajustado(r: dict) -> str:
    if r.get("custo_ajustado") == "cadastro":
        return (f"Custo ajustado: a nota ({_moeda(r['preco_nota'])}) não combina com o preço de venda "
                f"({_moeda(r['preco_venda'])}), provavelmente lançada sem a fração. Foi usado o cadastro "
                f"({_moeda(r['custo_cadastro'])}).")
    return (f"Custo ajustado: o cadastro do sistema de vendas ({_moeda(r['custo_cadastro'])}) não combina com o "
            f"preço de venda ({_moeda(r['preco_venda'])}). A nota está certa e foi usada.\n\nVale corrigir o "
            "cadastro no sistema de vendas.")


def _celula_compra(r: dict) -> list:
    """Preço e data (desde 02/10/2026, sem etiquetas): "A revisar" foi pra
    coluna Status, "custo ajustado" vai na dica do preço e da data, e preço
    de cadastro/bonificação viram texto miúdo — são detalhe, não decisão."""
    partes_dica = []
    if r["preco_origem"] == "compra":
        fornecedor = (f"\n\nFornecedor: {' '.join(r['compra_fornecedor'].split())}."
                      if isinstance(r["compra_fornecedor"], str) and r["compra_fornecedor"].strip() else "")
        partes_dica.append(
            f"Última compra: {_moeda(r['compra_vlr_unitario'])} por embalagem de {_numero(r['compra_fracao'])} un., "
            f"desconto de {_numero(r['compra_vlr_desconto'] or 0, 2)}%.{fornecedor}\n\nNo pedido, o preço é por "
            "unidade, sem ST.")
    if r.get("custo_ajustado"):
        partes_dica.append(_dica_custo_ajustado(r))
    dica = "\n\n".join(partes_dica) or None
    if r["preco_origem"] == "sem":
        linhas = [tb.pedaco("—", "aux"), tb.pedaco("sem compra", "aux")]
    else:
        linhas = [tb.pedaco(_moeda(r["preco"]), dica=dica)]
        if r["preco_origem"] == "compra":
            linhas.append(tb.pedaco(_data(r["compra_data"]), "aux", dica=dica))
    for t in r["tags_preco"]:
        if t == calculo.BONIFICADO:
            linhas.append(tb.pedaco("· bonificado depois", "aux", "Depois desta compra houve uma bonificação (preço "
                                                                 "abaixo de R$ 0,10). Ela não entra no preço."))
        elif t == calculo.CUSTO_CADASTRO:
            # "-" no lugar de "· cadastro" (02/10/2026); a explicação fica na dica.
            linhas.append(tb.pedaco("-", "aux", "Sem compra nos últimos meses.\n\nO preço é o custo de cadastro do "
                                                "sistema de vendas, um valor aproximado."))
    return tb.celula(*linhas)


def _celula_estoque(r: dict) -> list:
    if r["estoque_corrigido"]:
        return tb.celula(tb.pedaco(_numero(r["estoque"])),
                         tb.pedaco(f"PDV: {_numero(r['estoque_gps'])}", "aux",
                                   "O sistema de vendas mostra estoque negativo.\n\nVale o estoque digitado aqui."))
    classe = ("txt-vermelho" if r["negativo"] else "txt-laranja" if r["ruptura"]
              else "txt-ambar" if r["ruptura_proxima"] else None)
    linhas = [tb.pedaco(_numero(r["estoque"]), classe)]
    if r["negativo"]:
        # "PDV" (ponto de venda): onde não cabe "sistema de vendas" (02/10/2026).
        linhas.append(tb.pedaco(f"PDV: {_numero(r['estoque_gps'])}", "aux txt-vermelho",
                                "O sistema de vendas mostra estoque negativo.\n\nNo cálculo, conta como zero."))
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
                                f"A última compra veio em embalagem de {frac} unidades. "
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
         "sub": f"{ui.formatar_numero(i.marcados)} selecionados"},
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
    _invalidar_estado()


@st.dialog("Estoque negativo", dismissible=False)
def _alerta_negativos(usuario_id: int, loja_id: int, foto: str, n: int) -> None:
    """1º pop-up ao abrir a loja. Sem X: a resposta é o que libera a lista."""
    from integrations import pedido_area

    st.markdown(f"**{ui.formatar_numero(n)} produto(s) com estoque negativo.** Deseja corrigir manualmente ou "
                "considerar todos com estoque zero?")
    st.caption("Com estoque zero, a sugestão cobre o ideal inteiro do produto. Corrigindo, a linha é recalculada "
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


# Filtro Status: o valor fica em `<base>_valor` e o campo tem chave com
# versão (`<base>_<n>`). Tirar "Giro baixo" de dentro do callback do
# interruptor escrevendo na chave do campo funcionava uma vez; na escolha
# seguinte o campo voltava vazio (visto em 05/10/2026). Campo novo, com o
# valor certo como padrão, não tem esse problema.
def _status_mudou(base: str) -> None:
    """Filtro Status × "Ocultar giro baixo" (Q10/Q12 de 05/10/2026): escolher
    "Giro baixo" desliga o interruptor; tirar "Giro baixo" o religa."""
    novo = list(st.session_state.get(f"{base}_{st.session_state.get(f'{base}_v', 0)}", []))
    antes = calculo.GIRO_BAIXO in st.session_state.get(f"{base}_valor", [])
    agora = calculo.GIRO_BAIXO in novo
    if agora != antes:
        st.session_state[f"{_KEY}_giro"] = not agora
    st.session_state[f"{base}_valor"] = novo


def _giro_mudou(base: str) -> None:
    """Religar "Ocultar giro baixo" tira "Giro baixo" do filtro Status (Q13)."""
    valor = st.session_state.get(f"{base}_valor", [])
    if st.session_state.get(f"{_KEY}_giro") and calculo.GIRO_BAIXO in valor:
        st.session_state[f"{base}_valor"] = [t for t in valor if t != calculo.GIRO_BAIXO]
        st.session_state[f"{base}_v"] = st.session_state.get(f"{base}_v", 0) + 1


def _linha_filtros(loja_id: int, todas: pd.DataFrame) -> tuple[str | None, tuple, tuple, tuple]:
    """Busca, Categoria, Status, Fabricante e Ocultar giro baixo, numa linha
    acima da tabela (sem pop-up desde 01/10/2026; Status desde 05/10/2026).
    As opções vêm do que a loja tem na lista — calculadas em memória com o
    resto da loja (~4 ms na Hudson) — sem depender do "Ocultar giro baixo":
    uma opção que some da lista com a escolha feita quebraria o campo (o
    multiselect descarta calado o valor que não está nas opções)."""
    listadas = todas[todas["listar"]]
    categorias, fabricantes = calculo.opcoes_filtros(listadas)
    base = f"{_KEY}_status_{loja_id}"
    ja_escolhidos = st.session_state.get(f"{base}_valor", [])
    status = [t for t in calculo.TAGS_STATUS if t in set(calculo.opcoes_status(listadas)) | set(ja_escolhidos)]
    with st.container(key="pedido-filtros-linha", horizontal=True, vertical_alignment="center", gap="small"):
        busca = st.text_input("Buscar", placeholder="Buscar produto, EAN ou fabricante", key=f"{_KEY}_busca",
                              icon=":material/search:",
                              label_visibility="collapsed")
        escolhidas = st.multiselect("Categoria", categorias, placeholder="Categoria", key=f"{_KEY}_cat_{loja_id}",
                                    label_visibility="collapsed")
        tags = st.multiselect("Status", status, default=ja_escolhidos, placeholder="Status",
                              key=f"{base}_{st.session_state.get(f'{base}_v', 0)}",
                              on_change=_status_mudou, args=(base,), label_visibility="collapsed")
        fabs = st.multiselect("Fabricante", fabricantes, placeholder="Fabricante", key=f"{_KEY}_fab_{loja_id}",
                              label_visibility="collapsed")
        st.toggle("Ocultar giro baixo", value=True, key=f"{_KEY}_giro", on_change=_giro_mudou, args=(base,),
                  help="Ligado: os itens de giro baixo saem do pedido (não aparecem, não contam nos cards e não "
                       "vão na exportação).")
    return (busca or None), tuple(escolhidas), tuple(fabs), tuple(tags)


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
        _concluir("success", "Estoque negativo: " + ", ".join(partes) + ".")
        st.rerun()


@st.dialog("Listas", width="medium")   # "large" ficava largo demais (02/10/2026)
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
    from integrations import pedido_area

    usuario = auth.usuario_atual()
    # Resultado de uma ação (lista salva, exportação…): notificação, logo no
    # início — antes de qualquer saída antecipada da tela.
    mensagem = st.session_state.pop(f"{_KEY}_msg", None)
    if mensagem:
        _notificar(*mensagem)
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
    estado = _estado_loja(uid, usuario["nome"], loja["id"])
    correcoes, area, listas = estado.correcoes, estado.area, estado.listas
    avisos, vistos, personalizacao = estado.avisos, estado.vistos, estado.personalizacao
    padrao = configuracao()
    # Loja com personalização: vale a pílula escolhida; enquanto nenhuma for
    # escolhida, os pop-ups contam pelo padrão (a lista ainda não aparece).
    escolha = st.session_state.get(f"{_KEY}_param_{loja['id']}") if personalizacao else None
    config = cfg.aplicar(padrao, personalizacao) if escolha == "loja" else padrao
    p = parametros(config)
    marcador = loja_barra.marcador_estoque(loja["empresa_gps"]) or {}
    resultado = _calcular(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados, _versoes(), p,
                          tuple(sorted((l, c.estoque) for l, c in correcoes.items())),
                          foto=f"{marcador.get('data')}|{marcador.get('gerado_em')}")
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
        _avisar_uma_vez(loja["id"], "info", "Esta loja tem parâmetros personalizados. Escolha acima com quais "
                                            "parâmetros ver a sugestão.")
        return

    for aviso in avisos:
        _avisar_uma_vez(loja["id"], "warning", aviso)
    sem_vendas, sem_compras = meta.get("dias_sem_vendas", []), meta.get("dias_sem_compras", [])
    if sem_vendas:
        _avisar_uma_vez(loja["id"], "warning",
                        f"{len(sem_vendas)} dia(s) sem vendas coletadas (a demanda usa só os dias com dado).")
    if sem_compras:
        _avisar_uma_vez(loja["id"], "info", f"Compras de {len(sem_compras)} dia(s) ainda não coletadas — nesses "
                                            "dias, o preço pode vir do custo de cadastro.")

    _indicadores(pedido, p)
    busca, categorias, fabricantes, status = _linha_filtros(loja["id"], todas)
    linhas = calculo.filtrar(todas, busca=busca, ocultar_giro_baixo=ocultar, categorias=categorias,
                             fabricantes=fabricantes, status=status)

    ui.resetar_pagina_se_filtro_mudou(
        _KEY, f"{loja['id']}|{busca}|{categorias}|{fabricantes}|{status}|{ocultar}|{escolha}")
    ordem = st.session_state.get(f"{_KEY}_ordem")
    if ordem is None:
        # Padrão (Q14 de 01/10/2026): os "a revisar" primeiro, depois o que
        # mais vende em valor; "Venda pontual" (desmarcados) no fim — Q14 de
        # 05/10/2026. Clicar num título troca pela ordem da coluna.
        ordem = _ORDEM_PADRAO
        ordenadas = linhas.sort_values(["venda_pontual", "a_revisar", "valor"], ascending=[True, False, False],
                                       kind="stable")
    else:
        ordenadas = analise.ordenar(linhas, *ordem)
    fatia, pagina = comum.pagina(ordenadas, _KEY)

    def _editar(linha: str, texto: str) -> None:
        texto = texto.strip()
        if not texto.isdigit():
            st.session_state[f"{_KEY}_msg"] = ("error", "Quantidade não gravada: use um número inteiro, 0 ou maior.")
            return
        # Só a linha digitada (antes: um dicionário das ~5 mil linhas a cada clique).
        achada = todas.loc[todas["linha"] == linha, "sugestao"]
        sugestao = int(achada.iloc[0]) if not achada.empty else 0
        with get_session() as s2:
            pedido_area.alterar_quantidade(s2, uid, loja["id"], linha, int(texto), sugestao)
        _atualizar_area(uid, loja["id"], lambda itens: pedido_area.com_quantidade(itens, linha, int(texto), sugestao))

    def _marcar(marcacoes: dict[str, bool]) -> None:
        with get_session() as s2:
            pedido_area.marcar(s2, uid, loja["id"], marcacoes)
        _atualizar_area(uid, loja["id"], lambda itens: pedido_area.com_marcacoes(itens, marcacoes))

    tb.tabela(
        f"{_KEY}_tabela", _colunas(p), [_linha(r, p) for r in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        vazio=("Nenhum produto para pedir", "O estoque desta loja cobre o ideal de todos os produtos com esses filtros"),
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
        if area.atualizado_em is not None:
            # ✓ do mesmo tamanho e cinza-esverdeado apagado: diz "está salvo"
            # sem chamar atenção (Q8 de 02/10/2026).
            esq.markdown(f'<span class="rmc-muted"><span class="rmc-salvo">✓</span> Salvo automaticamente · '
                         f'{_hora_local(area.atualizado_em)}</span>', unsafe_allow_html=True)
        else:
            esq.markdown('<span class="rmc-muted">Segue a sugestão</span>', unsafe_allow_html=True)
        # "Ocultar giro baixo" saiu daqui pra linha de filtros (01/10/2026).
        with dir_.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if st.button("Descartar alterações", key=f"{_KEY}_descartar", disabled=not area.itens,
                         icon=":material/delete:"):
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
