"""Testes das consultas de apoio (core/queries.py) contra um SQLite em
memória. O cálculo da oportunidade é testado em tests/test_analise.py.

Cenário usado na maior parte dos testes:
- BaseGenerico "DIPIRONA 500MG" com dois EAN mapeados pra ele (111 e 222) —
  testa a consolidação de múltiplos EAN do mesmo genérico numa linha só.
- Cobertura Gruppy ATIVA só em SP (dois laboratórios, preços diferentes) —
  testa o escopo por UF (loja do RJ não deve aparecer nos resultados, já que
  não há cobertura ativa pra RJ) e o MIN() entre laboratórios concorrentes.
- Compras da loja de SP espalhadas em dois meses (06 e 07) — testa a
  resolução de período (1 mês vs. 2 meses) e a média ponderada por
  quantidade ao consolidar.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import (
    Base,
    BaseGenerico,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)
from core.models import ModoCustoGruppy
from core.queries import (
    historico_compras,
    listar_ultimos_ano_meses,
    precos_por_laboratorio,
)


def _ean_generico(session, ean, base_generico_id):
    session.add(
        EanGenerico(
            ean=ean,
            base_generico_id=base_generico_id,
            origem_resolucao=OrigemResolucao.MANUAL,
            descricao_origem_snapshot=f"desc {ean}",
            resolvido_por="teste",
        )
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def cenario(session):
    """Monta o cenário descrito no docstring do módulo e devolve os ids
    relevantes pros testes."""
    bg = BaseGenerico(nome_canonico="DIPIRONA 500MG", criado_por="teste")
    session.add(bg)
    session.flush()

    _ean_generico(session, "111", bg.id)
    _ean_generico(session, "222", bg.id)

    # Gruppy: dois laboratórios cobrindo SP, preços diferentes -> MIN vale 8.0 (LabX)
    lab_x = TabelaGruppy(laboratorio="LabX", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="x.xlsx", criado_por="teste")
    lab_y = TabelaGruppy(laboratorio="LabY", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="y.xlsx", criado_por="teste")
    session.add_all([lab_x, lab_y])
    session.flush()

    session.add_all([
        TabelaGruppyCobertura(tabela_gruppy_id=lab_x.id, uf="SP", status=StatusCobertura.ATIVA),
        TabelaGruppyCobertura(tabela_gruppy_id=lab_y.id, uf="SP", status=StatusCobertura.ATIVA),
    ])
    session.add_all([
        ItemTabelaGruppy(tabela_gruppy_id=lab_x.id, ean="111", descricao_origem="Dipirona 500mg cpr", custo_liquido=8.0),
        ItemTabelaGruppy(tabela_gruppy_id=lab_y.id, ean="222", descricao_origem="Dipirona 500mg cpr", custo_liquido=9.0),
    ])

    loja_sp = Loja(cnpj="11.111.111/0001-11", razao_social="Farmácia SP", uf="SP", cidade="São Paulo", atendente_comercial="Ana")
    loja_rj = Loja(cnpj="22.222.222/0001-22", razao_social="Farmácia RJ", uf="RJ", cidade="Rio de Janeiro", atendente_comercial="Ana")
    session.add_all([loja_sp, loja_rj])
    session.flush()

    # Loja SP: EAN 111 em junho (qtd 10, custo 12.0), EAN 222 em julho (qtd 5, custo 14.0)
    session.add_all([
        RegistroCompraGPS(
            loja_id=loja_sp.id, ean="111", descricao_origem="Dipirona", ano_mes="2026-06",
            quantidade=10, fat_liquido=1000, pct_cmv=0.5, custo_unitario=12.0, estoque=5,
        ),
        RegistroCompraGPS(
            loja_id=loja_sp.id, ean="222", descricao_origem="Dipirona", ano_mes="2026-07",
            quantidade=5, fat_liquido=500, pct_cmv=0.5, custo_unitario=14.0, estoque=2,
        ),
        # Loja RJ compra o mesmo genérico em julho, mas não há cobertura ATIVA pra RJ -> não deve aparecer
        RegistroCompraGPS(
            loja_id=loja_rj.id, ean="111", descricao_origem="Dipirona", ano_mes="2026-07",
            quantidade=3, fat_liquido=300, pct_cmv=0.5, custo_unitario=20.0, estoque=1,
        ),
    ])
    session.flush()

    return {"bg_id": bg.id, "loja_sp_id": loja_sp.id, "loja_rj_id": loja_rj.id}


def test_listar_ultimos_ano_meses(session, cenario):
    assert listar_ultimos_ano_meses(session, 1) == ["2026-07"]
    assert listar_ultimos_ano_meses(session, 2) == ["2026-07", "2026-06"]
    assert listar_ultimos_ano_meses(session, 6) == ["2026-07", "2026-06"]  # não existem mais que 2


def test_historico_compras_por_mes(session, cenario):
    historico = historico_compras(session, cenario["loja_sp_id"], cenario["bg_id"])
    assert [h["ano_mes"] for h in historico] == ["2026-06", "2026-07"]
    assert float(historico[0]["quantidade"]) == pytest.approx(10)
    assert float(historico[1]["quantidade"]) == pytest.approx(5)


def test_precos_por_laboratorio(session, cenario):
    precos = precos_por_laboratorio(session, "SP", [cenario["bg_id"]])
    assert precos[("LabX", cenario["bg_id"])] == pytest.approx(8.0)
    assert precos[("LabY", cenario["bg_id"])] == pytest.approx(9.0)


