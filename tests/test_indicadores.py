"""Cards de indicadores (core/analise_api.py → views/analise_comum.py): números,
variação e o período anterior usado pela seta."""
import pandas as pd
import pytest

from core import analise, analise_api
from views import analise_comum as comum


def _resultado() -> pd.DataFrame:
    # loja 1: dois genéricos com economia; loja 2: um com, um já otimizado;
    # loja 3: só "já otimizada" — analisada, mas sem oportunidade.
    return pd.DataFrame({
        "loja_id":          [1, 1, 2, 2, 3],
        "produto_id":       ["G10", "G20", "G10", "G30", "G30"],
        "economia":         [100.0, 50.0, 30.0, 0.0, 0.0],
    })


def test_indicadores_contam_lojas_e_produtos_distintos():
    ind = analise_api.indicadores(_resultado())
    assert ind.economia == pytest.approx(180.0)
    assert (ind.lojas_com_oportunidade, ind.lojas_analisadas) == (2, 3)
    # genérico 10 em duas lojas conta uma vez; o 30 só tem economia zero
    assert (ind.produtos_com_oportunidade, ind.produtos_analisados) == (2, 3)
    assert ind.economia_media_loja == pytest.approx(90.0)
    assert ind.economia_media_produto == pytest.approx(90.0)


def test_indicadores_vazio_sem_divisao_por_zero():
    ind = analise_api.indicadores(pd.DataFrame(columns=["loja_id", "produto_id", "economia"]))
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
