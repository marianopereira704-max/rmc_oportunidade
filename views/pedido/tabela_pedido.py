"""Assistente de pedido: colunas, células e dicas da tabela, e os 4 cards do pedido inteiro."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import dicas, revisar, theme, ui
from pedido import calculo
from pedido import categorias as cat
from views import tabela as tb
from views.pedido.avisos import _dialog_saiba_mais
from views.pedido.estado import _KEY
from views.pedido.formato import _data, _moeda, _numero

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
        # Curva sem "?" no título (29/10/2026, de novo): o "?" alargava a coluna ~20 px e, em 1280 px, a tabela
        # deixava de caber (o nome do produto quebrava em 2 linhas; teste visual). A explicação fica na própria
        # letra de cada linha (`_DICA_CURVA`).
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
        # "Mínimo / Ideal" por extenso (29/10/2026) passava ~30 px e a tabela deixava de caber em 1280 px (teste
        # visual): o título fica "Mín./Ideal"; a dica, por extenso.
        tb.ColunaTabela("estoque_max", "Mín./Ideal", centro=True, numerica=True,
                        dica=f"Mínimo: estoque para {p.dias_ruptura} dias de venda; abaixo disso, vai faltar."
                             "\n\nIdeal: quanto ter depois do pedido."),
        tb.ColunaTabela("estoque", "Estoque", centro=True, numerica=True),
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
        return "Estoque zerado." if p.ruptura_unidades == 0 else f"Estoque de até {dicas.qtd(p.ruptura_unidades, 'unidade')}."
    if tag == calculo.RUPTURA_PROXIMA:
        return f"O estoque dura {dicas.qtd(p.dias_ruptura, 'dia')} de venda ou menos."
    if tag == calculo.CORRIGIDO:
        return ("O sistema de vendas mostra estoque negativo.\n\nVale o estoque digitado aqui até o sistema de "
                "vendas deixar de mostrar negativo.")
    if tag == calculo.SEM_CLASSIFICACAO:
        return f"Produto sem categoria: sugestão com {p.dias_sem_classificacao} dias estimados."
    if tag == calculo.GIRO_BAIXO:
        return (f"Vendeu até {dicas.qtd(p.giro_baixo_max_unidades, 'unidade')} nos últimos "
                f"{dicas.qtd(p.giro_baixo_dias, 'dia')}.\n\nCom \"Ocultar giro baixo\" ligado, sai do pedido.")
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


# Nome que a etiqueta mostra na tela, quando difere do valor interno (29/10/2026, textos em linguagem de dono de
# farmácia aprovados pelo Mariano). Só na tela e no filtro Status: os valores internos seguem nos filtros
# guardados, na exportação e no Dashboard.
ROTULO_STATUS = {
    calculo.A_REVISAR: "Conferir preço",
    calculo.SEM_CLASSIFICACAO: "Sem categoria",
    calculo.ALTERADO: "Mudado por você",
}


def rotulo_status(tag: str) -> str:
    return ROTULO_STATUS.get(tag, tag)


def _celula_status(r: dict, p: calculo.Parametros) -> list:
    tags = [tb.pedaco(rotulo_status(t), _CLASSE_STATUS[t], _dica_status(t, r, p))
            for t in r["status"] if t in _CLASSE_STATUS]
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
    pedido usou e o que fazer (catálogo de dicas de 02/10/2026; a ação, uma
    por motivo e igual nas três telas, vem de core/revisar.py desde 28/10)."""
    motivo = r.get("revisar_motivo")
    if motivo == calculo.MOTIVO_DIVERGENCIA:
        return dicas.dica("Preço a conferir.",
                          f"O custo de cadastro do sistema de vendas ({_moeda(r['custo_cadastro'])}) e a última nota "
                          f"({_moeda(r['preco_nota'])}) estão muito diferentes, e o preço de venda "
                          f"({_moeda(r['preco_venda'])}) não mostra qual está certo. O pedido usa a nota.",
                          revisar.acao(motivo))
    if motivo == calculo.MOTIVO_ACIMA_VENDA:
        return dicas.dica("Preço a conferir.",
                          f"A nota de {_data(r['compra_ignorada_data'])}, de {_moeda(r['compra_ignorada_preco'])} por "
                          f"unidade, passava do preço de venda ({_moeda(r['preco_venda'])}). O pedido usa a compra de "
                          f"{_data(r['compra_data'])}, de {_moeda(r['preco'])}.",
                          revisar.acao(motivo))
    if motivo == calculo.MOTIVO_CADASTRO:
        return dicas.dica("Preço a conferir.",
                          f"Sem compra nos últimos meses, o preço vem do cadastro do sistema de vendas "
                          f"({_moeda(r['custo_cadastro'])}). Esse valor não combina com o preço de venda "
                          f"({_moeda(r['preco_venda'])}).",
                          revisar.acao(motivo))
    if pd.notna(r["compra_ignorada_preco"]):
        return dicas.dica("Preço a conferir.",
                          f"A compra de {_data(r['compra_ignorada_data'])}, de {_moeda(r['compra_ignorada_preco'])} "
                          f"por unidade, está muito diferente das outras compras deste produto e não foi usada como "
                          f"preço: {_preco_usado(r)}.",
                          revisar.acao(calculo.MOTIVO_COMPRA))
    return dicas.dica("Preço a conferir.",
                      f"As compras deste produto variam muito entre si, e nenhuma serve de referência segura: "
                      f"{_preco_usado(r)}.",
                      revisar.acao(calculo.MOTIVO_COMPRA))


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


_DICA_CURVA = dicas.dica("A: os produtos que mais faturam na loja. B: os do meio. C: os que menos faturam",
                         "A 1ª letra é pelo valor vendido; a 2ª, pela quantidade")


def _linha(r: dict, p: calculo.Parametros) -> dict:
    return {
        "id": r["linha"],
        # "a revisar" em destaque âmbar (Q14 de 01/10/2026).
        "classe": "revisar" if r.get("a_revisar") else None,
        "celulas": {
            "nome": _celula_produto(r),
            "status": _celula_status(r, p),
            "curva": tb.celula(tb.pedaco(r["curva"], "forte", _DICA_CURVA)),
            "preco": _celula_compra(r),
            "venda_90d": tb.celula(tb.pedaco(_numero(r["venda_90d"]))),
            "estoque_max": tb.celula(tb.pedaco(f"{_numero(r['estoque_min'])} / {_numero(r['estoque_max'])}")),
            "estoque": _celula_estoque(r),
            "quantidade": _celula_sugestao(r),
            "subtotal": tb.celula(tb.pedaco(_moeda(r["subtotal"]), None if r["selecionado"] else "aux")),
        },
    }


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
        {"icone": "produto", "tom": "roxo", "valor": ui.formatar_numero(i.itens), "label": "Produtos para comprar",
         "sub": f"{ui.formatar_numero(i.marcados)} marcados no pedido"},
        {"icone": "alerta", "tom": "ambar", "valor": ui.formatar_numero(i.ruptura), "label": "Ruptura",
         "sub": f"{ui.formatar_numero(i.ruptura_sem_pedido)} ainda fora do pedido"},
        {"icone": "duvida", "tom": "azul", "valor": ui.formatar_numero(sem), "label": "Sem categoria",
         "sub": "Saiba mais ›"},
    ]
    with st.container(key="pedido-cards"):
        colunas = st.columns(4, gap="small")
        for coluna, card in zip(colunas[:3], cards):
            with coluna:
                theme.kpis([card], unico=True)
        with colunas[3], st.container(key="pedido-card-sem"):
            theme.kpis([cards[3]], unico=True)
            if st.button("Saiba mais sobre os produtos sem categoria", key=f"{_KEY}_saiba_mais"):
                _dialog_saiba_mais(sem, p.dias_sem_classificacao)
