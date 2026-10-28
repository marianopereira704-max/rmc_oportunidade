"""Textos e peças da tela Compras do Dashboard (views/dashboard_compras.py): o que aparece nos cards, nas barras, nas
linhas da tabela e no detalhe do produto. As contas estão em tests/test_dashboard_compras_contas.py."""
import datetime as dt

import pandas as pd

from core import dashboard_compras as dc
from views import dashboard_compras as tela

REGRAS = dc.Regras()


def _metricas(**k):
    base = {"dotacao": 103000.0, "consumido": 82400.0, "venda_custo": 85500.0, "sem_demanda": 3300.0, "excesso": 8300.0,
            "nao_otimizado": 11600.0, "certa": 70800.0, "variacao": None, "compra_venda": 96.4, "eficiencia": 85.9,
            "pct_sem_demanda": 4.0, "pct_excesso": 10.1, "pct_nao_otimizado": 14.1, "pct_consumido": 80.0,
            "saldo": 20600.0}
    return {**base, **k}


def test_legenda_do_periodo():
    p = dc.periodos(dt.date(2026, 10, 20), dt.date(2026, 7, 1))
    assert tela.legenda_periodo(p[dc.ATUAL]) == "Outubro/2026 até 20/10, pela data de emissão da nota fiscal"
    assert tela.legenda_periodo(p[dc.PASSADO]) == "Setembro/2026, pela data de emissão da nota fiscal"
    assert tela.legenda_periodo(p[dc.TRES]) == "Agosto a outubro/2026 (até 20/10), pela data de emissão da nota fiscal"


def test_cards_curtos_e_dotacao_que_passou():
    c = tela.cards(_metricas(), REGRAS)
    assert [x["label"] for x in c] == ["Dotação", "Compra × Venda", "Compras não otimizadas", "Eficiência em compras"]
    assert c[0]["valor"] == "80,0% consumido" and c[0]["sub"] == "Saldo de R$ 20,6 mil" and c[0]["progresso"] == 80.0
    assert c[1]["sub"] == "Comprou menos do que vendeu"
    assert c[2]["sub"] == "14,1% do comprado"
    passou = tela.cards(_metricas(saldo=-36300.0, pct_consumido=119.8, compra_venda=124.0), REGRAS)
    assert passou[0]["sub"] == "Passou R$ 36,3 mil" and passou[1]["sub"] == "Comprou mais do que vendeu"


def test_barras_da_distribuicao_somam_100():
    itens = tela.itens_distribuicao([{"categoria": "genericos", "consumido": 1000.0, "certa": 700.0, "excesso": 200.0,
                                      "sem_demanda": 100.0}])
    assert itens[0]["rotulo"] == "Genérico" and itens[0]["texto"] == "R$ 1 mil"
    assert [round(p["valor"]) for p in itens[0]["partes"]] == [70, 20, 10]


def test_eficiencia_colorida():
    assert tela.classe_eficiencia(79.9) == "txt-vermelho"
    assert tela.classe_eficiencia(85) == "txt-ambar"
    assert tela.classe_eficiencia(90) == "txt-verde"
    assert tela.classe_eficiencia(None) is None


def _produto(**k):
    base = {"linha": "G1", "nome": "G.DIPIRONA 1G 10CP", "ean_principal": "7896181927131", "categoria": "genericos",
            "classe": dc.EXCESSO, "comprado_un": 300.0, "bonif_un": 50.0, "vendido_un": 110.0, "limite": 140.0,
            "excesso_un": 160.0, "excesso_pagas": 110.0, "nao_otimizado": 352.0, "estoque": 190.0, "ideal": 30.0,
            "demanda_dia": 4.0}
    return {**base, **k}


def test_linha_do_produto():
    linha = tela.linha_produto(_produto())
    assert linha["celulas"]["nome"][1][0]["t"] == "7896181927131 · Genérico"
    assert linha["celulas"]["classe"][0][0] == {"t": "Excesso", "c": "selo-sugestao"}
    assert linha["celulas"]["limite"][0][0]["t"] == "140 un."


def test_detalhe_com_bonificacao_e_notas():
    r = _produto()
    cards = tela.cards_detalhe(r)
    assert [c["rotulo"] for c in cards] == ["Comprado", "Vendido", "Limite", "Não otimizado"]
    assert cards[0]["aux"] == "50 bonificadas" and cards[3]["aux"] == "110 un. pagas"
    notas = pd.DataFrame({"data": pd.to_datetime(["2026-10-02", "2026-10-14"]), "fornecedor": ["EMS", "EMS"],
                          "nro_nf": ["1", "2"], "unidades": [150.0, 50.0], "preco": [3.2, 0.0], "valor": [480.0, 0.0],
                          "bonificado": [False, True]})
    texto = tela.html_detalhe(r, notas, REGRAS)
    assert "NOTAS FISCAIS DO PERÍODO" in texto and "Bonificação" in texto and "02/10/2026" in texto
    assert "110 delas pagas" in texto and "$" not in texto
    sem = tela.cards_detalhe(_produto(classe=dc.SEM_DEMANDA, bonif_un=0.0, nao_otimizado=50.0))
    assert sem[0]["aux"] == "" and sem[3]["aux"] == ""
