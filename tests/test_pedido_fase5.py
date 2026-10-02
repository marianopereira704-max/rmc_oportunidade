"""Pedido, Fase 5: níveis de acesso (core/acesso.py), rascunho, estoque
negativo e exportação (integrations/pedido_rascunho.py + pedido/calculo.py)."""
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core import acesso
from core.models import Base, ExportacaoPedido, Loja
from integrations import pedido_rascunho as rasc
from pedido import calculo
from tests.test_pedido_calculo import SEM_GENERICOS, _linha, _pronto


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Loja(id=1, cnpj="1", razao_social="L", uf="MG", cidade="X"))
        s.flush()
        yield s


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------

def test_niveis_e_secoes():
    assert acesso.nivel_do_usuario("admin", None) == acesso.ADM
    assert acesso.nivel_do_usuario("consultor", None) == acesso.CONSULTOR
    assert acesso.nivel_do_usuario("consultor", "COMPRADOR") == acesso.COMPRADOR
    assert acesso.nivel_do_usuario("consultor", "GERENTE") == acesso.COMPRADOR    # nome antigo (até 01/10/2026)
    assert acesso.pode_ver(acesso.ADM, "config_pedido")
    assert acesso.pode_ver(acesso.CONSULTOR, "pedido") and not acesso.pode_ver(acesso.CONSULTOR, "dados")
    assert not acesso.pode_ver(acesso.CONSULTOR, "config_pedido")
    for nivel in (acesso.COMPRADOR, acesso.PROPRIETARIO):
        assert acesso.pode_ver(nivel, "pedido") and not acesso.pode_ver(nivel, "oportunidade_loja")
        assert acesso.pode_ver(nivel, "oportunidade_produto") and not acesso.pode_ver(nivel, "dashboard")
        assert acesso.secao_inicial(nivel) == "pedido"
    # Por Loja é só do ADM (02/10/2026); o Consultor começa na Por Produto.
    assert acesso.pode_ver(acesso.ADM, "oportunidade_loja") and not acesso.pode_ver(acesso.CONSULTOR, "oportunidade_loja")
    assert acesso.secao_inicial(acesso.CONSULTOR) == "oportunidade_produto"
    assert acesso.secao_inicial(acesso.ADM) == "oportunidade_loja"


def test_opcao_todas_da_barra_lateral():
    assert acesso.opcao_todas(acesso.ADM, 300) == acesso.opcao_todas(acesso.CONSULTOR, 1) == "Todas as lojas"
    assert acesso.opcao_todas(acesso.PROPRIETARIO, 4) == "Todas"
    assert acesso.opcao_todas(acesso.PROPRIETARIO, 1) is None
    assert acesso.opcao_todas(acesso.COMPRADOR, 2) is None


def test_comprador_e_proprietario_so_veem_as_proprias_lojas():
    lojas = [{"id": 1}, {"id": 2}, {"id": 3}]
    assert acesso.filtrar_lojas(lojas, acesso.CONSULTOR, None) == lojas
    assert acesso.filtrar_lojas(lojas, acesso.COMPRADOR, [2]) == [{"id": 2}]               # comprador da loja 2
    assert acesso.filtrar_lojas(lojas, acesso.PROPRIETARIO, [1, 2, 3]) == lojas            # dono das 3
    assert acesso.filtrar_lojas(lojas, acesso.PROPRIETARIO, []) == []      # sem loja ligada: nenhuma, nunca todas


# ---------------------------------------------------------------------------
# Rascunho
# ---------------------------------------------------------------------------

def test_rascunho_grava_atualiza_e_volta_a_sugestao(session):
    rasc.alterar_quantidade(session, 1, "G7", 10, 4, "ana")
    rasc.alterar_quantidade(session, 1, "P9", 0, 3, "ana")
    rasc.alterar_quantidade(session, 1, "G7", 12, 4, "bia")                  # mesma linha: atualiza
    r = rasc.rascunho(session, 1)
    assert r["G7"].quantidade == 12 and r["G7"].alterado_por == "bia" and r["G7"].sugestao_calculada == 4
    assert r["P9"].quantidade == 0
    rasc.alterar_quantidade(session, 1, "G7", 4, 4, "bia")                   # igual à sugestão: sai do rascunho
    assert set(rasc.rascunho(session, 1)) == {"P9"}
    with pytest.raises(ValueError):
        rasc.alterar_quantidade(session, 1, "P9", -1, 3, "ana")
    assert rasc.descartar_rascunho(session, 1) == 1 and rasc.rascunho(session, 1) == {}


def test_aplicar_rascunho_muda_quantidade_orcamento_e_cards():
    p = _pronto([("1", "2026-09-20", 118, 1), ("2", "2026-09-20", 118, 1)],
                [("1", "200", "A", "L", 0, 2), ("2", "200", "B", "L", 0, 2)])
    base = pd.DataFrame([("200", "MIP/OTC")], columns=["ean", "categoria"])
    df = calculo.calcular(p, base, SEM_GENERICOS)
    linha_a = _linha(df, "A")["linha"]
    item = rasc.ItemRascunho(quantidade=20, sugestao_calculada=7, alterado_por="ana", alterado_em=None)
    com = calculo.aplicar_rascunho(df, {linha_a: item})
    a, b = _linha(com, "A"), _linha(com, "B")
    assert a["quantidade"] == 20 and a["alterado"] and a["orcamento"] == pytest.approx(40)
    assert b["quantidade"] == 7 and not b["alterado"]
    i = calculo.indicadores(calculo.filtrar(com))
    assert i.unidades == 27 and i.itens == 2


# ---------------------------------------------------------------------------
# Estoque negativo
# ---------------------------------------------------------------------------

def test_correcoes_gravam_removem_e_recusam_negativo(session):
    assert rasc.salvar_correcoes(session, 1, {"P1": 5, "P2": 0}, {"P1": -3, "P2": -1}, "2026-09-27", "ana") == (2, 0)
    c = rasc.correcoes(session, 1)
    assert c["P1"].estoque == 5 and c["P1"].estoque_gps == -3 and c["P1"].corrigido_por == "ana"
    assert rasc.salvar_correcoes(session, 1, {"P1": None, "P2": 2}, {}, None, "bia") == (1, 1)
    assert set(rasc.correcoes(session, 1)) == {"P2"}
    with pytest.raises(ValueError):
        rasc.salvar_correcoes(session, 1, {"P2": -4}, {}, None, "bia")
    v1 = rasc.versao_correcoes(session, 1)
    rasc.salvar_correcoes(session, 1, {"P3": 1}, {}, None, "bia")
    assert rasc.versao_correcoes(session, 1) != v1


def test_correcao_so_vale_enquanto_o_gps_mostra_negativo():
    base = pd.DataFrame([("200", "MIP/OTC")], columns=["ean", "categoria"])
    p = _pronto([("1", "2026-09-20", 118, 1), ("2", "2026-09-20", 118, 1)],
                [("1", "200", "NEG", "L", -3, 1), ("2", "200", "POS", "L", 2, 1)])
    sem = calculo.calcular(p, base, SEM_GENERICOS)
    assert _linha(sem, "NEG")["estoque"] == 0 and _linha(sem, "NEG")["sugestao"] == 7   # negativo = 0, não trava
    l_neg, l_pos = _linha(sem, "NEG")["linha"], _linha(sem, "POS")["linha"]
    df = calculo.calcular(p, base, SEM_GENERICOS, correcoes={l_neg: 1, l_pos: 50})
    neg, pos = _linha(df, "NEG"), _linha(df, "POS")
    assert neg["estoque"] == 1 and neg["estoque_gps"] == -3 and neg["estoque_corrigido"]
    assert neg["sugestao"] == 6 and calculo.CORRIGIDO in neg["status"] and calculo.NEGATIVO not in neg["status"]
    assert pos["estoque"] == 2 and not pos["estoque_corrigido"]                # foto não negativa: correção ignorada


# ---------------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------------

def test_exportacao_produto_quantidade_e_eans_em_colunas(session):
    linhas = pd.DataFrame({
        "nome": ["DIPIRONA", "SABONETE", "ZERO"],
        "quantidade": [5, 2, 0],
        "eans": [["111", "222", "333"], ["444"], ["555"]],
    })
    t = calculo.exportacao(linhas)
    assert list(t.columns) == ["PRODUTO", "QUANTIDADE", "EAN 1", "EAN 2", "EAN 3"]
    assert t.to_dict("records") == [
        {"PRODUTO": "DIPIRONA", "QUANTIDADE": 5, "EAN 1": "111", "EAN 2": "222", "EAN 3": "333"},
        {"PRODUTO": "SABONETE", "QUANTIDADE": 2, "EAN 1": "444", "EAN 2": "", "EAN 3": ""},
    ]
    rasc.registrar_exportacao(session, 1, "Excel", 2, 7, 12.345, "ana")
    e = rasc.ultima_exportacao(session, 1)
    assert (e.formato, e.itens, e.unidades, float(e.valor), e.exportado_por) == ("Excel", 2, 7, 12.35, "ana")
    assert session.query(ExportacaoPedido).count() == 1


def test_arquivo_csv_abre_no_excel_em_portugues():
    from views.pedido import _arquivo_exportacao

    t = pd.DataFrame({"PRODUTO": ["AÇÚCAR"], "QUANTIDADE": [3], "EAN 1": ["789"]})
    csv = _arquivo_exportacao(t, "CSV")
    assert csv.startswith(b"\xef\xbb\xbf") and "AÇÚCAR;3;789".encode("utf-8") in csv
    assert _arquivo_exportacao(t, "Excel")[:2] == b"PK"                     # xlsx = zip
