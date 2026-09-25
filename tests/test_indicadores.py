"""Cards de indicadores (core/analise.py → views/analise_comum.py): números,
variação e o período anterior usado pela seta."""
import pandas as pd
import pytest

from core import analise
from tests.test_analise import AGO, session  # noqa: F401 — fixture reaproveitada
from core.models import (
    BaseGenerico, EanGenerico, ItemTabelaGruppy, Loja, ModoCustoGruppy, OrigemResolucao, RegistroCompraGPS,
    StatusCobertura, TabelaGruppy, TabelaGruppyCobertura,
)
from views import analise_comum as comum


def _resultado() -> pd.DataFrame:
    # loja 1: dois genéricos com economia; loja 2: um com, um já otimizado;
    # loja 3: só "já otimizada" — analisada, mas sem oportunidade.
    return pd.DataFrame({
        "loja_id":          [1, 1, 2, 2, 3],
        "base_generico_id": [10, 20, 10, 30, 30],
        "economia":         [100.0, 50.0, 30.0, 0.0, 0.0],
    })


def test_indicadores_contam_lojas_e_produtos_distintos():
    ind = analise.indicadores(_resultado())
    assert ind.economia == pytest.approx(180.0)
    assert (ind.lojas_com_oportunidade, ind.lojas_analisadas) == (2, 3)
    # genérico 10 em duas lojas conta uma vez; o 30 só tem economia zero
    assert (ind.produtos_com_oportunidade, ind.produtos_analisados) == (2, 3)
    assert ind.economia_media_loja == pytest.approx(90.0)
    assert ind.economia_media_produto == pytest.approx(90.0)


def test_indicadores_vazio_sem_divisao_por_zero():
    ind = analise.indicadores(pd.DataFrame(columns=["loja_id", "base_generico_id", "economia"]))
    assert ind.economia == 0 and ind.economia_media_loja is None and ind.economia_media_produto is None


def test_variacao():
    assert analise.variacao(120, 100) == pytest.approx(0.2)
    assert analise.variacao(80, 100) == pytest.approx(-0.2)
    assert analise.variacao(5, 0) is None
    assert analise.variacao(5, None) is None


def test_periodo_anterior_so_com_todos_os_meses_de_calendario_carregados():
    assert analise.meses_periodo_anterior(["2026-08"], {"2026-08", "2026-07"}) == ["2026-07"]
    # agosto e janeiro carregados: "último mês" NÃO compara com janeiro
    assert analise.meses_periodo_anterior(["2026-08"], {"2026-08", "2026-01"}) is None
    # 2 meses, virada de ano
    assert analise.meses_periodo_anterior(["2026-02", "2026-01"], {"2025-12", "2025-11"}) == ["2025-11", "2025-12"]
    assert analise.meses_periodo_anterior(["2026-02", "2026-01"], {"2025-12"}) is None


def test_rotulo_dos_meses_comparados():
    assert comum.rotulo_meses(["2026-07"]) == "jul/2026"
    assert comum.rotulo_meses(["2026-05", "2026-06", "2026-07"]) == "mai–jul/2026"
    assert comum.rotulo_meses(["2025-11", "2025-12", "2026-01"]) == "nov/2025–jan/2026"


def test_carregar_anterior_no_banco(session):  # noqa: F811
    bg = BaseGenerico(nome_canonico="DIPIRONA 500MG")
    loja = Loja(cnpj="11.111.111/0001-11", razao_social="Farmácia MG", uf="MG", cidade="BH")
    tabela = TabelaGruppy(laboratorio="Ranbaxy", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="r", criado_por="t")
    session.add_all([bg, loja, tabela])
    session.flush()
    session.add_all([
        EanGenerico(ean="111", base_generico_id=bg.id, origem_resolucao=OrigemResolucao.MANUAL,
                    descricao_origem_snapshot="x", resolvido_por="t"),
        TabelaGruppyCobertura(tabela_gruppy_id=tabela.id, uf="MG", status=StatusCobertura.ATIVA),
        ItemTabelaGruppy(tabela_gruppy_id=tabela.id, ean="111", descricao_origem="d", custo_liquido=8.0),
    ])
    for ano_mes, custo in ((AGO, 9.0), ("2026-07", 10.0)):
        session.add(RegistroCompraGPS(loja_id=loja.id, ean="111", descricao_origem="D", laboratorio_compra="X",
                                      ano_mes=ano_mes, quantidade=10, custo_unitario=custo))
    session.flush()

    df_ant, meses = analise.carregar_anterior(session, "Ranbaxy", periodo_meses=1)
    assert meses == ["2026-07"]
    assert df_ant["economia"].sum() == pytest.approx(20.0)  # (10 − 8) × 10, só julho
    assert analise.carregar_anterior(session, "Ranbaxy", periodo_meses=2) is None  # faltam mai/jun
