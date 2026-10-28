"""Contas da página Ruptura (core/dashboard_ruptura.py, 27/10/2026): falta medida, perda, projeção, risco e o resto.

Cenário: vendas de 01/09 a 26/10/2026; fotos de estoque de 20 a 27/10, SEM a do dia 22.
- P1 (código 1): vende 2/dia, R$ 10; tinha 5 un. e zerou em 24/10 — em falta até hoje (4 dias).
- P2 (genérico, códigos 2 e 3): vende 1/dia, R$ 20; o código 2 está sempre zerado, o 3 zerou só no dia 26 e voltou —
  o produto faltou UM dia (só quando os dois zeraram). Hoje tem 3 un.: ruptura próxima (3 dias de venda).
- P3 (código 4): vendeu 1 un. em 05/09, R$ 49; em falta em todas as fotos (o dia 22, sem foto, conta — R9).
"""
import dataclasses
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from core import dashboard_ruptura as dr
from pedido import linhas as lc

META = {"inicio": "2026-09-01", "fim": "2026-10-26", "dias_sem_vendas": []}
FOTOS = ["2026-10-20", "2026-10-21", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]


def _dias(ini, fim):
    a, b = dt.date.fromisoformat(ini), dt.date.fromisoformat(fim)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _todas():
    return pd.DataFrame({
        "linha": ["P1", "G2", "P3"], "nome": ["DIPIRONA", "LOSARTANA", "RARO"],
        "codigos": [["1"], ["2", "3"], ["4"]], "eans": [["7891"], ["7892", "7893"], ["7894"]],
        "estoque": [0, 3, 0], "demanda_dia": [2.0, 1.0, 1 / 56], "preco_venda": [10.0, 20.0, 49.0],
        "preco": [4.0, 8.0, 30.0], "a_revisar": [False, False, True], "sugestao": [10, 4, 5],
        "curva": ["AA", "BB", "CC"],
    })


def _vendas():
    linhas = [("1", d, 2) for d in _dias("2026-09-01", "2026-10-26")]
    linhas += [("2", d, 1) for d in _dias("2026-09-01", "2026-10-26")]
    linhas += [("4", "2026-09-05", 1)]
    return pd.DataFrame(linhas, columns=["codigo_produto", "data", "unidades"])


def _historico():
    # Só as mudanças (e a abertura em 20/10), como a rotina grava.
    h = [("1", "2026-10-20", 5), ("1", "2026-10-24", 0),
         ("2", "2026-10-20", -1),                                     # negativo conta 0
         ("3", "2026-10-20", 4), ("3", "2026-10-26", 0), ("3", "2026-10-27", 3),
         ("4", "2026-10-20", 0)]
    return pd.DataFrame(h, columns=["codigo_produto", "data", "estoque"]).astype({"estoque": float})


def _prep(regras=dr.Regras(), linha_compra=None):
    return dr.preparar(_todas(), _vendas(), _historico(), FOTOS, regras, META, linha_compra)


# --- falta medida ------------------------------------------------------------------------------------------

def test_estoque_por_dia_soma_os_codigos_da_linha_e_repete_o_ultimo_valor():
    e = dr.estoque_por_dia(_historico(), FOTOS, {"P1": ["1"], "G2": ["2", "3"], "X": ["999"]})
    assert e.loc["2026-10-23", "P1"] == 5 and e.loc["2026-10-24", "P1"] == 0        # repete até mudar
    assert e.loc["2026-10-25", "G2"] == 4                                            # 0 (negativo) + 4
    assert e.loc["2026-10-26", "G2"] == 0 and e.loc["2026-10-27", "G2"] == 3
    assert (e["X"] == 0).all()                                                       # sem registro: 0


def test_periodos_de_falta_com_dia_sem_foto_e_falta_que_acabou():
    e = dr.estoque_por_dia(_historico(), FOTOS, {"P1": ["1"], "G2": ["2", "3"], "P3": ["4"]})
    p = dr.periodos_de_falta(e, 0).set_index("linha")
    assert (p.loc["P1", "inicio"], p.loc["P1", "fim"], p.loc["P1", "dias"]) == (dt.date(2026, 10, 24), dt.date(2026, 10, 27), 4)
    assert p.loc["P1", "aberta"] and p.loc["P1", "dias_sem_foto"] == 0
    # O genérico só faltou no dia em que os DOIS códigos estavam zerados, e voltou.
    assert (p.loc["G2", "dias"], bool(p.loc["G2", "aberta"])) == (1, False)
    # P3: 20 a 27/10 = 8 dias corridos, o 22 sem foto conta (estava em falta antes e depois — R9).
    assert (p.loc["P3", "dias"], p.loc["P3", "dias_sem_foto"]) == (8, 1)


def test_falta_que_acaba_conta_so_ate_a_ultima_foto_em_falta():
    e = pd.DataFrame({"X": [0.0, 0.0, 5.0]}, index=pd.Index(["2026-10-20", "2026-10-21", "2026-10-25"], name="data"))
    p = dr.periodos_de_falta(e, 0).iloc[0]
    assert (p["fim"], p["dias"], bool(p["aberta"])) == (dt.date(2026, 10, 21), 2, False)   # não conta 22 a 24


# --- venda esperada, perda e projeção -------------------------------------------------------------------------

def test_venda_esperada_usa_os_dias_sem_falta_antes_do_inicio():
    d, _ = _prep()
    assert d.loc["P1", "venda_esperada"] == pytest.approx(2.0)                       # 01/09 a 23/10, sem falta
    assert d.loc["P3", "venda_esperada"] == pytest.approx(1 / 49)                   # 1 un. em 01/09 a 19/10


def test_perda_medida_e_projecao():
    d, resumo = _prep()
    assert d.loc["P1", "dias_falta"] == 4 and d.loc["P1", "perda_aberta"] == pytest.approx(2 * 10 * 4)
    assert d.loc["P1", "projecao"] == pytest.approx(2 * 10 * 30)
    assert d.loc["G2", "perda_periodo"] == pytest.approx(1 * 20 * 1)                # a falta que já acabou conta (R8)
    assert np.isnan(d.loc["G2", "perda_aberta"]) and d.loc["G2", "projecao"] == 0    # hoje não está em falta
    assert d.loc["P3", "perda_aberta"] == pytest.approx(1 / 49 * 49 * 8)
    assert (resumo["primeira_foto"], resumo["ultima_foto"], resumo["dias_medidos"]) == (
        dt.date(2026, 10, 20), dt.date(2026, 10, 27), 8)


def test_transicao_usa_a_projecao_e_depois_a_medida():
    d, resumo = _prep()
    assert resumo["modo"] == dr.MODO_TRANSICAO and resumo["completo_em"] == dt.date(2026, 11, 18)
    assert d["perda"].tolist() == pytest.approx(d["projecao"].tolist())
    d5, r5 = _prep(dataclasses.replace(dr.Regras(), periodo_perda=5))              # 5 dias já medidos
    assert r5["modo"] == dr.MODO_MEDIDA
    # Janela de 23 a 27/10: P1 4 dias, G2 o dia 26, P3 5 dias.
    assert d5.loc["P1", "perda"] == pytest.approx(80) and d5.loc["G2", "perda"] == pytest.approx(20)
    assert d5.loc["P3", "perda"] == pytest.approx(5)
    assert d5.loc["P1", "perda_produto"] == pytest.approx(80)                       # nos críticos: a falta aberta


def test_sem_base_quando_nao_ha_dia_sem_falta_antes_da_falta():
    meta = {**META, "inicio": "2026-10-20"}                                         # vendas começam com a falta
    vendas = _vendas()[lambda v: v["data"] >= "2026-10-20"]
    d, _ = dr.preparar(_todas(), vendas, _historico(), FOTOS, dr.Regras(), meta)
    assert bool(d.loc["P3", "sem_base"]) and d.loc["P3", "perda_aberta"] == 0


def test_sem_historico_tudo_e_projecao_pelos_ultimos_90_dias():
    d, resumo = dr.preparar(_todas(), _vendas(), _historico().iloc[0:0], [], dr.Regras(), META)
    assert resumo["modo"] == dr.MODO_TRANSICAO and resumo["primeira_foto"] is None
    assert d.loc["P1", "venda_esperada"] == pytest.approx(2.0) and d.loc["P1", "dias_falta"] == 0
    assert d.loc["P1", "perda"] == pytest.approx(600)


# --- risco, investimento, nível de serviço --------------------------------------------------------------------

def test_risco_pela_frequencia_de_venda():
    d, _ = _prep()
    assert d.loc["P1", "frequencia"] == pytest.approx(1.0) and d.loc["P1", "risco"] == dr.ALTO
    assert d.loc["P3", "risco"] == dr.BAIXO                                          # 1 dia em 56
    d2, _ = _prep(dataclasses.replace(dr.Regras(), risco_alto=1.01, risco_medio=0.5))
    assert d2.loc["P1", "risco"] == dr.MEDIO


def test_investimento_pela_sugestao_e_custo_a_revisar_fica_fora_do_rs():
    d, _ = _prep()
    assert (d.loc["P1", "repor_un"], d.loc["P1", "repor_valor"]) == (10, 40)
    assert (d.loc["P3", "repor_un"], d.loc["P3", "repor_valor"]) == (5, 0)          # a revisar: só as unidades
    assert d.loc["G2", "repor_un"] == 4                                              # ruptura próxima também repõe


def test_situacoes_e_venda_em_risco():
    d, _ = _prep()
    assert d["situacao"].tolist() == [dr.RUPTURA, dr.PROXIMA, dr.RUPTURA]
    assert d.loc["P1", "venda_risco_dia"] == pytest.approx(20) and d.loc["G2", "venda_risco_dia"] == 0


def test_cards_e_agregacoes():
    classes = pd.Series({"P1": lc.GENERICOS, "G2": lc.GENERICOS, "P3": lc.PROPAGADOS})
    d, _ = _prep(linha_compra=classes)
    c = dr.cards(d)
    assert (c.produtos, c.ruptura, c.ruptura_curva_a) == (3, 2, 1)
    assert c.perda == pytest.approx(600 + 30) and c.perda_medida == pytest.approx(80 + 20 + 8)
    assert (c.investimento, c.investimento_un) == (40, 15)
    t = dr.por_categoria(d)
    assert t.loc[lc.GENERICOS, "produtos"] == 2 and t.loc[lc.GENERICOS, "nivel_servico"] == pytest.approx(50)
    assert t.loc[lc.PROPAGADOS, "nivel_servico"] == pytest.approx(0)
    curva = dr.por_curva(d)
    assert curva.loc["A", "em_ruptura"] == 1 and curva.loc["B", "pct"] == 0
    risco = dr.por_risco(d)
    assert risco["pct"].sum() == pytest.approx(100) and risco.loc[dr.ALTO, "perda"] == pytest.approx(600)


def test_criticos_ordem_padrao_filtro_e_busca():
    d, _ = _prep()
    assert dr.criticos(d)["linha"].tolist() == ["P1", "P3", "G2"]                   # ruptura pela perda; depois a próxima
    assert dr.criticos(d, situacao=dr.PROXIMA)["linha"].tolist() == ["G2"]
    assert dr.criticos(d, busca="7893")["linha"].tolist() == ["G2"]                 # acha pelo 2º EAN do genérico


def test_filtro_cruzado_e_cada_bloco_ignora_o_proprio():
    d, _ = _prep(linha_compra=pd.Series({"P1": lc.GENERICOS, "G2": lc.GENERICOS, "P3": lc.PROPAGADOS}))
    assert set(dr.filtrar(d, categorias=[lc.GENERICOS], riscos=[dr.ALTO]).index) == {"P1", "G2"}
    assert set(dr.sem_filtro_de(d, "risco", categorias=[lc.PROPAGADOS], riscos=[dr.ALTO]).index) == {"P3"}


def test_regras_da_config_com_os_campos_que_existirem():
    class Config:
        ruptura_unidades = 1
        ruptura_perda_dias = 15
    r = dr.Regras.da_config(Config())
    assert (r.ruptura_unidades, r.periodo_perda, r.risco_alto) == (1.0, 15, 0.14)
