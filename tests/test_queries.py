"""Testes do motor de oportunidade (core/queries.py) contra um SQLite em
memória.

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
    Filtros,
    detalhe_produtos_da_loja,
    historico_compras,
    listar_ultimos_ano_meses,
    oportunidade_por_loja,
    oportunidade_por_produto,
    precos_por_laboratorio,
    soma_economia_conjunto,
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


def test_escopo_por_uf_exclui_loja_sem_cobertura_ativa(session, cenario):
    """Loja do RJ não tem cobertura Gruppy ativa pra RJ -> some do resultado
    (INNER JOIN), nunca aparece com placeholder."""
    resultado = oportunidade_por_loja(session, Filtros(periodo_meses=2), pagina=1, tamanho_pagina=50)
    lojas = {linha["razao_social"] for linha in resultado.linhas}
    assert "Farmácia SP" in lojas
    assert "Farmácia RJ" not in lojas


def test_consolidacao_multiplos_ean_e_media_ponderada(session, cenario):
    """EAN 111 (jun, qtd 10, custo 12) + EAN 222 (jul, qtd 5, custo 14), mesmo
    genérico -> consolida numa linha só: qtd 15, custo médio ponderado por
    quantidade = (12*10 + 14*5) / 15 = 12.6667. Menor preço RMC em SP = 8.0
    (LabX). Economia = (12.6667 - 8.0) * 15 = 70.0."""
    resultado = oportunidade_por_produto(session, Filtros(periodo_meses=2), pagina=1, tamanho_pagina=50)
    assert resultado.total_linhas == 1  # uma única linha (loja SP, genérico consolidado)

    linha = resultado.linhas[0]
    assert linha["quantidade_total"] == pytest.approx(15)
    # tolerância mais larga aqui: a coluna é Numeric(14,4) no banco, então o
    # SQLite/SQLAlchemy arredonda o resultado da divisão a poucas casas
    # decimais ao converter pra Python -- irrelevante na prática (exibido
    # sempre como moeda, 2 casas), então não vale travar no valor exato.
    assert float(linha["custo_medio_ponderado"]) == pytest.approx(190 / 15, rel=1e-3)
    assert float(linha["menor_preco"]) == pytest.approx(8.0)
    assert float(linha["economia"]) == pytest.approx(70.0, rel=1e-2)
    # EAN 111 e EAN 222 são apresentações DIFERENTES do mesmo genérico, cada
    # uma com seu próprio histórico -- o snapshot "mais recente" é por
    # (loja, EAN), não por genérico, então aqui cada EAN só tem 1 mês
    # carregado (é o próprio "mais recente" dele) e os dois somam: 5 + 2 = 7.
    assert float(linha["estoque_total"]) == pytest.approx(7)


def test_resolucao_periodo_1_mes_considera_so_o_mes_mais_recente(session, cenario):
    """Com período=1 mês, só julho entra -> só o EAN 222 (qtd 5, custo 14).
    Economia = (14 - 8) * 5 = 30.0."""
    resultado = oportunidade_por_produto(session, Filtros(periodo_meses=1), pagina=1, tamanho_pagina=50)
    assert resultado.total_linhas == 1
    linha = resultado.linhas[0]
    assert linha["quantidade_total"] == pytest.approx(5)
    assert float(linha["economia"]) == pytest.approx(30.0)


def test_oportunidade_por_loja_soma_economia_de_todos_os_genericos(session, cenario):
    resultado = oportunidade_por_loja(session, Filtros(periodo_meses=2), pagina=1, tamanho_pagina=50)
    assert resultado.total_linhas == 1
    assert float(resultado.linhas[0]["economia"]) == pytest.approx(70.0)
    assert resultado.linhas[0]["qtd_produtos"] == 1


def test_soma_economia_conjunto_nao_depende_da_paginacao(session, cenario):
    total = soma_economia_conjunto(session, Filtros(periodo_meses=2))
    assert total == pytest.approx(70.0)

    # mesmo com tamanho_pagina=1 (que limitaria a listagem), a soma do conjunto
    # inteiro não muda -- ela nunca pagina.
    pagina = oportunidade_por_produto(session, Filtros(periodo_meses=2), pagina=1, tamanho_pagina=1)
    assert pagina.total_linhas == 1
    assert total == pytest.approx(70.0)


def test_detalhe_produtos_da_loja(session, cenario):
    detalhe = detalhe_produtos_da_loja(session, cenario["loja_sp_id"], Filtros(periodo_meses=2))
    assert len(detalhe) == 1
    assert detalhe[0]["nome_canonico"] == "DIPIRONA 500MG"


def test_historico_compras_por_mes(session, cenario):
    historico = historico_compras(session, cenario["loja_sp_id"], cenario["bg_id"])
    assert [h["ano_mes"] for h in historico] == ["2026-06", "2026-07"]
    assert float(historico[0]["quantidade"]) == pytest.approx(10)
    assert float(historico[1]["quantidade"]) == pytest.approx(5)


def test_precos_por_laboratorio(session, cenario):
    precos = precos_por_laboratorio(session, "SP", [cenario["bg_id"]])
    assert precos[("LabX", cenario["bg_id"])] == pytest.approx(8.0)
    assert precos[("LabY", cenario["bg_id"])] == pytest.approx(9.0)


def test_filtro_busca_por_nome_de_generico(session, cenario):
    resultado = oportunidade_por_produto(
        session, Filtros(periodo_meses=2, busca="dipirona"), pagina=1, tamanho_pagina=50
    )
    assert resultado.total_linhas == 1

    vazio = oportunidade_por_produto(
        session, Filtros(periodo_meses=2, busca="produto_inexistente"), pagina=1, tamanho_pagina=50
    )
    assert vazio.total_linhas == 0


def test_estoque_usa_sempre_o_snapshot_mais_recente_do_mesmo_ean(session, cenario):
    """Diferente do teste de consolidação (que junta EANs DIFERENTES), aqui é
    o MESMO EAN em dois meses -- o estoque tem que ser o do mês mais
    recente, nunca a soma do histórico."""
    session.add(
        RegistroCompraGPS(
            loja_id=cenario["loja_sp_id"], ean="222", descricao_origem="Dipirona", ano_mes="2026-05",
            quantidade=1, fat_liquido=100, pct_cmv=0.5, custo_unitario=13.0, estoque=99,
        )
    )
    session.flush()

    resultado = oportunidade_por_produto(session, Filtros(periodo_meses=6), pagina=1, tamanho_pagina=50)
    linha = resultado.linhas[0]
    # EAN 111 (jun, estoque 5) + EAN 222 (mais recente = jul, estoque 2, não
    # o de maio que tinha 99) = 7, igual ao teste de consolidação.
    assert float(linha["estoque_total"]) == pytest.approx(7)


def test_sem_gps_carregado_devolve_pagina_vazia(session):
    """Sem nenhum RegistroCompraGPS (banco vazio), o motor não deve
    quebrar — só devolver uma página vazia."""
    resultado = oportunidade_por_loja(session, Filtros(periodo_meses=1), pagina=1, tamanho_pagina=50)
    assert resultado.total_linhas == 0
    assert resultado.linhas == []
    assert soma_economia_conjunto(session, Filtros(periodo_meses=1)) == 0.0
