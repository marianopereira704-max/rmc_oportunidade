"""Página Ruptura do Dashboard (views/dashboard_ruptura.py, redesenho de 27/10/2026): as partes puras da tela."""
import datetime as dt

import pytest

from core import dashboard_ruptura as dr
from core import dicas
from pedido import linhas as lc
from views import dashboard_ruptura as tela

REGRAS = dr.Regras()


def _linha(**mudar):
    base = {"linha": "P1", "nome": "DIPIRONA 1G", "eans": ["7891000000019"], "linha_compra": lc.GENERICOS,
            "situacao": dr.RUPTURA, "em_ruptura": True, "ruptura_proxima": False, "curva": "AB", "risco": dr.ALTO,
            "ultima_venda": dt.date(2026, 10, 20), "compra_data": "2026-10-01", "venda_90d": 180, "estoque": 0,
            "venda_esperada": 2.0, "preco_venda": 10.0, "preco": 4.0, "custo_ok": True, "dias_falta": 4,
            "dias_sem_foto": 0, "inicio_falta": dt.date(2026, 10, 24), "perda_aberta": 80.0, "projecao": 600.0,
            "perda_produto": 80.0, "sem_base": False, "repor_un": 10, "repor_valor": 40.0,
            "compra_fornecedor": "DISTRIBUIDORA  X"}
    return {**base, **mudar}


def _texto(celula):
    return [[p["t"] for p in linha] for linha in celula]


def test_pct_e_cor_do_nivel_de_servico():
    assert tela.pct(66.333) == "66,3%"
    assert tela.classe_nivel(60, REGRAS) == "txt-vermelho"
    assert tela.classe_nivel(70, REGRAS) == "txt-ambar"
    assert tela.classe_nivel(75, REGRAS) == "txt-verde"


def test_textos_da_perda_pelo_modo_da_pagina():
    c = dr.Cards(10, 2, 20.0, 1, 30.0, 900.0, 120.0, 900.0, 50.0, 5, 0)
    sem_hist = {"modo": dr.MODO_TRANSICAO, "primeira_foto": None, "dias_medidos": 0, "periodo": 30, "completo_em": None}
    rot, sub, leg = tela.textos_perda(sem_hist, c)
    assert rot == "Perda em 30 dias se não repor" and "primeira foto" in sub and "projeção de 30 dias" in leg
    trans = {**sem_hist, "primeira_foto": dt.date(2026, 10, 28), "dias_medidos": 3, "completo_em": dt.date(2026, 11, 26)}
    rot, sub, leg = tela.textos_perda(trans, c)
    assert sub == "Medida até agora: R$ 120,00 em 3 dias (desde 28/10/2026)" and "26/11/2026" in leg
    medida = {**trans, "modo": dr.MODO_MEDIDA, "dias_medidos": 30}
    rot, sub, leg = tela.textos_perda(medida, c)
    assert rot == "Perda" and "R$ 900,00 em 30" in sub


def test_linha_de_critico_em_ruptura():
    l = tela.linha_critico(_linha())
    c = l["celulas"]
    assert l["id"] == "P1"
    assert _texto(c["nome"]) == [["DIPIRONA 1G"], ["7891000000019 · Genérico"]]
    assert c["situacao"][0][0] == {"t": "Em ruptura", "c": "selo-perigo"}
    assert _texto(c["perda_produto"]) == [["R$ 80,00"]] and _texto(c["ultima_venda"]) == [["20/10/2026"]]
    assert c["risco"][0][0] == {"t": "Alto", "c": "selo-perigo"}
    assert _texto(c["repor_un"]) == [["10 un."], ["R$ 40,00"]]


def test_linha_de_critico_ruptura_proxima_sem_base_e_custo_a_revisar():
    prox = tela.linha_critico(_linha(situacao=dr.PROXIMA, em_ruptura=False, ruptura_proxima=True, risco=dr.MEDIO))
    assert _texto(prox["celulas"]["perda_produto"]) == [["—"]] and prox["celulas"]["situacao"][0][0]["t"] == "Ruptura próxima"
    sem = tela.linha_critico(_linha(sem_base=True))
    assert sem["celulas"]["perda_produto"][0][0]["t"] == "sem base" and not dicas.problemas(sem["celulas"]["perda_produto"][0][0]["dica"])
    rev = tela.linha_critico(_linha(custo_ok=False, repor_valor=0.0))
    assert _texto(rev["celulas"]["repor_un"]) == [["10 un."], ["custo a revisar"]]
    nada = tela.linha_critico(_linha(repor_un=0))
    assert _texto(nada["celulas"]["repor_un"]) == [["—"]]


def test_cada_coluna_de_criticos_tem_celula():
    colunas = tela._colunas_criticos()
    assert len({c.campo for c in colunas}) == len(colunas)
    assert set(tela.linha_critico(_linha())["celulas"]) == {c.id for c in colunas}


def test_tabela_por_categoria_linhas_e_detalhe():
    m = {"produtos": 10, "em_ruptura": 3, "nivel_servico": 70.0, "perda": 500.0, "investimento": 90.0}
    r = {"codigo": lc.PROPAGADOS, "categoria": "Propagado", **m}
    cat = tela.linha_categoria(r, REGRAS, True)
    assert cat["id"] == lc.PROPAGADOS and cat["filhos"] is True
    assert cat["celulas"]["nivel_servico"][0][0] == {"t": "70,0%", "c": "txt-ambar"}
    no = {"fonte": "iqvia", "chave": "RX_PROMOVIDO", "metricas": m,
          "filhos": [{"fonte": "febrafar", "chave": "MIP/OTC", "metricas": m, "filhos": []}]}
    linhas = tela.linhas_detalhe(no, lc.PROPAGADOS, lc.PROPAGADOS, REGRAS)
    assert [l["nivel"] for l in linhas] == [1, 2] and all(l["selecionavel"] is False for l in linhas)
    assert _texto(linhas[0]["celulas"]["categoria"]) == [["Prescrição de marca (ético)"]]
    tot = tela.linha_total(m, REGRAS)
    assert tot["classe"] == "total" and tot["selecionavel"] is False


def test_cards_do_detalhe():
    cards = {c["rotulo"]: c for c in tela.cards_detalhe(_linha(), REGRAS)}
    assert len(cards) == 6
    assert cards["Dias sem estoque"]["valor"] == "4 dias" and cards["Dias sem estoque"]["aux"] == "medido desde 24/10/2026"
    assert cards["Perda"]["valor"] == "R$ 80,00" and "R$ 600,00 em 30 dias" in cards["Perda"]["aux"]
    assert cards["Última compra"]["aux"] == "DISTRIBUIDORA X · R$ 4,00/un."
    com_buraco = {c["rotulo"]: c for c in tela.cards_detalhe(_linha(dias_sem_foto=1), REGRAS)}
    assert com_buraco["Dias sem estoque"]["valor"] == "≈ 4 dias"
    sem_hist = {c["rotulo"]: c for c in tela.cards_detalhe(_linha(dias_falta=0), REGRAS)}
    assert sem_hist["Dias sem estoque"]["valor"] == "medindo"
    # Sem a falta medida, a perda do card é a projeção, não R$ 0,00 (pedido de 27/10/2026).
    assert (sem_hist["Perda"]["valor"], sem_hist["Perda"]["aux"]) == ("R$ 600,00", "em 30 dias se não repor")
    assert (cards["Vendas em 90 dias"]["valor"], cards["Vendas em 90 dias"]["aux"]) == ("180 un.", "")


def test_html_do_detalhe_escapa_e_tem_diagnostico_e_sugestao():
    h = tela.html_detalhe(_linha(nome="<b>X</b> $5"), REGRAS)
    assert "&lt;b&gt;" in h and "<b>X" not in h and "$" not in h
    assert "Diagnóstico" in h and "Sugestão de ação" in h and "Comprar 10 unidades" in h


def test_diagnostico_e_sugestoes():
    assert dr.diagnostico(_linha(), REGRAS).startswith("Sem estoque há 4 dias (medido desde 24/10/2026).")
    assert dr.diagnostico(_linha(), REGRAS).endswith("A cada dia em falta deixa de vender R$ 20,00.")
    assert dr.diagnostico(_linha(dias_falta=0), REGRAS) == "A cada dia em falta deixa de vender R$ 20,00."
    assert "base de 90 dias" in dr.diagnostico(_linha(sem_base=True), REGRAS)
    assert "próximos dias" in dr.diagnostico(_linha(ruptura_proxima=True), REGRAS)
    sem_preco = dr.diagnostico(_linha(preco_venda=float("nan")), REGRAS)
    assert "nan" not in sem_preco and "sem valor" in sem_preco
    sem_compra = {c["rotulo"]: c for c in tela.cards_detalhe(_linha(compra_data=None), REGRAS)}["Última compra"]
    assert (sem_compra["valor"], sem_compra["aux"]) == ("Sem compra", "custo do cadastro: R$ 4,00/un.")
    titulos = [t for t, _ in dr.sugestoes(_linha())]
    assert titulos == ["Comprar 10 unidades (R$ 40,00)", "Fornecedor", "Conferir o pedido"]
    assert dict(dr.sugestoes(_linha()))["Comprar 10 unidades (R$ 40,00)"] == "para ficar com o estoque ideal."
    assert dict(dr.sugestoes(_linha()))["Fornecedor"] == "A última compra foi com DISTRIBUIDORA X, a R$ 4,00 por unidade."
    assert [t for t, _ in dr.sugestoes(_linha(repor_un=0, compra_fornecedor=None))] == ["Conferir o pedido"]
