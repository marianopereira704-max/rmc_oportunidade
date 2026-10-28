"""Histórico do estoque (pedido/historico_estoque.py, 27/10/2026): a base da perda por ruptura medida."""
import datetime as dt

import pandas as pd
import pytest

from pedido import historico_estoque as he
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves

CH = Chaves("pedido")


@pytest.fixture()
def armaz(tmp_path):
    return ArmazenamentoLocal(tmp_path)


def _foto(**estoques):
    return pd.DataFrame({"codigo_produto": list(estoques), "qtd_estoque": list(estoques.values())})


def _mes(armaz, mes):
    return armaz.ler_df(CH.historico_estoque("E", "L", mes))


def test_primeira_foto_do_mes_e_inteira_e_as_seguintes_so_o_que_mudou(armaz):
    assert he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 0, "3": 2}), dt.date(2026, 10, 27)) == 3
    assert he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 0, "3": 1}), dt.date(2026, 10, 28)) == 1
    assert he.registrar(armaz, CH, "E", "L", _foto(**{"1": 0, "2": 4, "3": 1}), dt.date(2026, 10, 29)) == 2
    m = _mes(armaz, "2026-10")
    assert list(zip(m["codigo_produto"], m["data"], m["estoque"])) == [
        ("1", "2026-10-27", 5.0), ("2", "2026-10-27", 0.0), ("3", "2026-10-27", 2.0),
        ("3", "2026-10-28", 1.0),
        ("1", "2026-10-29", 0.0), ("2", "2026-10-29", 4.0),
    ]


def test_produto_novo_entra_e_estoque_vazio_vale_zero(armaz):
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5}), dt.date(2026, 10, 27))
    assert he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "9": None}), dt.date(2026, 10, 28)) == 1
    m = _mes(armaz, "2026-10")
    assert m[m["codigo_produto"] == "9"]["estoque"].tolist() == [0.0]


def test_rodar_de_novo_no_mesmo_dia_substitui_o_dia(armaz):
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 3}), dt.date(2026, 10, 27))
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 4, "2": 3}), dt.date(2026, 10, 28))
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 0}), dt.date(2026, 10, 28))     # 2ª passada do dia 28
    m = _mes(armaz, "2026-10")
    dia28 = m[m["data"] == "2026-10-28"]
    assert list(zip(dia28["codigo_produto"], dia28["estoque"])) == [("2", 0.0)]              # o 1 voltou a 5: não mudou


def test_cada_mes_abre_com_a_foto_inteira(armaz):
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 3}), dt.date(2026, 10, 31))
    assert he.registrar(armaz, CH, "E", "L", _foto(**{"1": 5, "2": 3}), dt.date(2026, 11, 1)) == 2
    assert len(_mes(armaz, "2026-11")) == 2                                                # lido sozinho, sem outubro


def test_dias_com_foto_e_leitura_por_intervalo(armaz):
    for dia, est in ((27, 5), (28, 5), (30, 0)):                                           # sem foto no dia 29
        he.registrar(armaz, CH, "E", "L", _foto(**{"1": est}), dt.date(2026, 10, dia))
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 2}), dt.date(2026, 11, 2))
    linhas, fotos = he.ler(armaz, CH, "E", "L", dt.date(2026, 10, 28), dt.date(2026, 11, 1))
    assert fotos == ["2026-10-28", "2026-10-30"]
    # O mês inteiro vem (a abertura do dia 27 é o estado de partida); nada depois do fim pedido.
    assert linhas["data"].tolist() == ["2026-10-27", "2026-10-30"]
    vazio, sem_fotos = he.ler(armaz, CH, "X", "Y", dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert vazio.empty and sem_fotos == []


def test_retencao_de_13_meses_apaga_so_o_mes_velho_desta_loja(armaz):
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 1}), dt.date(2025, 9, 15))
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 1}), dt.date(2025, 10, 15))
    he.registrar(armaz, CH, "E", "OUTRA", _foto(**{"1": 1}), dt.date(2025, 9, 15))
    armaz.salvar_json(CH.historico_estoque("E", "L", "nao-e-mes").replace(".parquet", ".json"), {"x": 1})
    he.registrar(armaz, CH, "E", "L", _foto(**{"1": 1}), dt.date(2026, 10, 27))            # 13 meses: out/25 a out/26
    restam = armaz.listar(CH.prefixo_historico_estoque("E", "L"))
    assert CH.historico_estoque("E", "L", "2025-09") not in restam                         # mais velho que 13 meses
    assert CH.historico_estoque("E", "L", "2025-10") in restam
    assert any(c.endswith("nao-e-mes.json") for c in restam)                               # nome fora do padrão: fica
    assert armaz.existe(CH.historico_estoque("E", "OUTRA", "2025-09"))                     # outra loja: não é tocada
    assert armaz.ler_json(CH.historico_estoque_fotos("E", "L"))["dias"] == ["2025-10-15", "2026-10-27"]


def test_limite_de_13_meses():
    assert he._limite(dt.date(2026, 10, 27)) == "2025-10-01"
    assert he._limite(dt.date(2026, 12, 1)) == "2025-12-01"
    assert he._limite(dt.date(2027, 1, 5)) == "2026-01-01"
