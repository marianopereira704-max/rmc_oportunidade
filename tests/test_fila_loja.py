"""Fase 6: fila de EAN com a origem "Loja (API)" (integrations/fila_loja.py),
vendas no catálogo da empresa e relatórios da rotina na tela Rotinas."""
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import (
    Base, BaseGenerico, EanGenerico, FilaResolucaoEAN, OrigemFila, OrigemResolucao, StatusFila,
)
from integrations import fila_loja
from pedido import categorias as cat
from pedido import coleta, pronto
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves
from reconciliation import motor
from tests.test_pedido_calculo import _pronto

BASE = pd.DataFrame([("111", cat.GENERICO), ("222", cat.GENERICO), ("333", "HIGIENE"), ("444", cat.GENERICO),
                     ("555", cat.GENERICO)], columns=["ean", "categoria"])


def _catalogo(linhas):
    return pd.DataFrame(linhas, columns=["ean", "nome", "laboratorio", "lojas_com_venda", "valor_venda"])


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        g = BaseGenerico(nome_canonico="DIPIRONA SODICA 500MG 10 COMPRIMIDOS")
        s.add(g)
        s.flush()
        s.add(EanGenerico(ean="444", base_generico_id=g.id, origem_resolucao=OrigemResolucao.IMPORTADA,
                          descricao_origem_snapshot="x", resolvido_por="admin"))
        s.add(FilaResolucaoEAN(ean="555", descricao_observada="DO GPS", origem=OrigemFila.GPS,
                               valor_total_acumulado=10, qtd_ocorrencias=1))
        s.flush()
        yield s


def test_so_genericos_vendidos_e_fora_da_base_entram_e_nunca_resolvem_sozinhos(session):
    cat_ = _catalogo([
        ("111", "DIPIRONA SODICA 500MG 10 COMPRIMIDOS", "EMS", 3, 150.0),   # nota altíssima: ainda assim só sugestão
        ("222", "XPTO QUALQUER", "L", 1, 20.0),
        ("333", "SABONETE", "L", 5, 99.0),                                  # não é genérico
        ("444", "DIPIRONA", "L", 2, 10.0),                                  # já na Base Genéricos
        ("555", "DIPIRONA GPS", "L", 1, 5.0),                               # já na fila vindo do GPS
        ("666", "SEM CATEGORIA", "L", 1, 5.0),
        ("777", "GENERICO SEM VENDA", "L", 0, 0.0),
    ])
    base = pd.concat([BASE, pd.DataFrame([("777", cat.GENERICO)], columns=["ean", "categoria"])])
    r = fila_loja.atualizar(session, cat_, base)
    assert (r.genericos_vendidos, r.ja_na_base, r.novos_na_fila, r.em_outra_origem) == (4, 1, 2, 1)
    itens = {i.ean: i for i in session.query(FilaResolucaoEAN)}
    assert itens["111"].origem == OrigemFila.LOJA_API and itens["111"].status == StatusFila.PENDENTE
    assert itens["111"].sugestao_base_generico_id is not None and float(itens["111"].valor_total_acumulado) == 150
    assert itens["111"].qtd_ocorrencias == 3
    assert itens["555"].origem == OrigemFila.GPS                            # origem não muda
    assert "333" not in itens and "666" not in itens and "777" not in itens
    assert session.query(EanGenerico).filter_by(ean="111").count() == 0     # nada resolvido sozinho


def test_nova_atualizacao_regrava_o_valor_sem_somar(session):
    fila_loja.atualizar(session, _catalogo([("222", "XPTO", "L", 1, 20.0)]), BASE)
    r = fila_loja.atualizar(session, _catalogo([("222", "XPTO", "L", 2, 35.0)]), BASE)
    item = session.query(FilaResolucaoEAN).filter_by(ean="222").one()
    assert r.novos_na_fila == 0 and r.atualizados == 1
    assert float(item.valor_total_acumulado) == 35 and item.qtd_ocorrencias == 2


def test_reprocessar_nao_resolve_loja_api_e_recalculo_nao_zera(session):
    fila_loja.atualizar(session, _catalogo([("111", "DIPIRONA SODICA 500MG 10 COMPRIMIDOS", "EMS", 3, 150.0)]), BASE)
    r = motor.reprocessar_fila_resolucao(session)
    item = session.query(FilaResolucaoEAN).filter_by(ean="111").one()
    assert r.resolvidos_automaticamente == 0 and item.status == StatusFila.PENDENTE and item.sugestao_base_generico_id
    motor.recalcular_valores_fila_ean(session)
    session.refresh(item)
    assert float(item.valor_total_acumulado) == 150                        # valor vendido fica
    gps = session.query(FilaResolucaoEAN).filter_by(ean="555").one()
    session.refresh(gps)
    assert float(gps.valor_total_acumulado) == 0                           # GPS sem compra vigente: recalculado


def test_confirmar_item_loja_api_liga_o_ean_ao_generico(session):
    fila_loja.atualizar(session, _catalogo([("111", "DIPIRONA SODICA 500MG 10 COMPRIMIDOS", "EMS", 3, 150.0)]), BASE)
    item = session.query(FilaResolucaoEAN).filter_by(ean="111").one()
    motor.confirmar_resolucao_manual(session, item.id, item.sugestao_base_generico_id, "admin")
    assert session.query(EanGenerico).filter_by(ean="111").one().base_generico_id == item.sugestao_base_generico_id


def test_catalogo_ganha_as_vendas_das_lojas():
    p1 = _pronto([("1", "2026-09-20", 3, 30), ("2", "2026-09-20", 0, 0)],
                 [("1", "0111", "A", "L", 1, 1), ("2", "222", "B", "L", 1, 1)])
    p2 = _pronto([("9", "2026-09-21", 1, 12)], [("9", "111", "A", "L", 0, 1)])
    v1, v2 = pronto.vendas_por_ean(p1), pronto.vendas_por_ean(p2)
    catalogo = pd.DataFrame({"ean": ["111", "222", "333"], "nome": ["A", "B", "C"], "laboratorio": "L",
                             "grupo_gps": "G", "categoria_gps": "C", "lojas": 2.0, "lojas_com_estoque": 1.0})
    c = cat.completar_com_vendas(catalogo, [v1, v2]).set_index("ean")
    assert c.loc["111", "lojas_com_venda"] == 2 and c.loc["111", "valor_venda"] == 42
    assert c.loc["222", "lojas_com_venda"] == 0 and c.loc["333", "valor_venda"] == 0
    assert list(c.reset_index().columns) == cat.COLUNAS_CATALOGO


def test_ultimas_execucoes_pega_o_ultimo_relatorio_de_cada_parte(tmp_path):
    armaz, ch = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    import datetime as dt
    armaz.salvar_json(ch.execucao(dt.date(2026, 9, 28), "parte1de4", "030000"), {"parte": "parte1de4", "lojas": 1})
    armaz.salvar_json(ch.execucao(dt.date(2026, 9, 29), "parte1de4", "030000"), {"parte": "parte1de4", "lojas": 2})
    armaz.salvar_json(ch.execucao(dt.date(2026, 9, 29), "parte1de4", "101500"), {"parte": "parte1de4", "lojas": 3})
    armaz.salvar_json(ch.execucao(dt.date(2026, 9, 29), "parte2de4", "030000"), {"parte": "parte2de4", "lojas": 7})
    data, rels = coleta.ultimas_execucoes(armaz, ch)
    assert data == "2026-09-29" and [(r["parte"], r["lojas"]) for r in rels] == [("parte1de4", 3), ("parte2de4", 7)]
    assert coleta.ultimas_execucoes(ArmazenamentoLocal(tmp_path / "vazio"), ch) == (None, [])


def test_fila_loja_api_em_ordem_de_pareto_pelas_lojas(session):
    """01/10/2026: Loja (API) ordena pelo nº de lojas que vendem; o resto da
    fila continua pelo valor."""
    fila_loja.atualizar(session, _catalogo([("111", "DIPIRONA", "EMS", 2, 500.0), ("222", "XPTO", "L", 6, 20.0)]), BASE)
    por_lojas = motor.listar_fila_priorizada(session, origem_filtro=OrigemFila.LOJA_API, por_lojas=True)
    assert [i.ean for i in por_lojas] == ["222", "111"]
    por_valor = motor.listar_fila_priorizada(session, origem_filtro=OrigemFila.LOJA_API)
    assert [i.ean for i in por_valor] == ["111", "222"]
    assert motor.soma_ocorrencias_pendentes(session, OrigemFila.LOJA_API) == 8
