"""Filtros das telas de análise (views/analise_comum.py)."""
from core.config import AppConfig
from views import analise_comum as comum


def test_periodo_se_explica_sem_rotulo():
    assert comum.rotulo_periodo(1) == "Último mês"
    assert comum.rotulo_periodo(3) == "Últimos 3 meses"


def test_opcoes_de_periodo_padrao(monkeypatch):
    monkeypatch.delenv("PERIODOS_MESES_OPCOES", raising=False)
    monkeypatch.setenv("RMC_IGNORAR_SECRETS", "1")
    assert AppConfig().periodos_meses_opcoes == [1, 2, 3, 6]
