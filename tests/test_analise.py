"""Peças comuns da Análise de Oportunidade (core/analise.py): filtros,
ordenação e a lista de laboratórios. O cálculo da oportunidade (compras da
API, 05/10/2026) é testado em tests/test_analise_api.py; os testes da regra
da planilha saíram com ela.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core import analise
from core.models import Base, ModoCustoGruppy, StatusCobertura, TabelaGruppy, TabelaGruppyCobertura


# ---------------------------------------------------------------------------
# Filtros e ordenação (em memória)
# ---------------------------------------------------------------------------

def _tabela():
    df = pd.DataFrame([
        {"loja_id": 1, "base_generico_id": 1, "razao_social": "Farmácia B", "cnpj": "1", "uf": "MG",
         "atendente_comercial": "Ana", "grupo_economico": None, "nome_canonico": "DIPIRONA", "laboratorio": "SANDOZ",
         "descricao": "DIPIRONA SANDOZ", "economia": 10.0},
        {"loja_id": 2, "base_generico_id": 1, "razao_social": "farmácia a", "cnpj": "2", "uf": "SP",
         "atendente_comercial": "Bia", "grupo_economico": "G1", "nome_canonico": "DIPIRONA", "laboratorio": None,
         "descricao": "DIPIRONA", "economia": 30.0},
        {"loja_id": 2, "base_generico_id": 2, "razao_social": "farmácia a", "cnpj": "2", "uf": "SP",
         "atendente_comercial": "Bia", "grupo_economico": "G1", "nome_canonico": "LOSARTANA", "laboratorio": "EMS",
         "descricao": "LOSARTANA EMS", "economia": 5.0},
    ])
    for coluna in analise.COLUNAS_LOJA:
        if coluna not in df.columns:
            df[coluna] = None
    return df


def test_ordenar_texto_ignora_maiuscula_e_vazio_fica_no_fim_nos_dois_sentidos():
    df = _tabela()
    assert analise.ordenar(df, "razao_social", True)["loja_id"].tolist() == [2, 2, 1]
    assert pd.isna(analise.ordenar(df, "laboratorio", True)["laboratorio"].tolist()[-1])
    assert pd.isna(analise.ordenar(df, "laboratorio", False)["laboratorio"].tolist()[-1])


def test_ordenar_texto_ignora_espaco_nas_pontas():
    df = pd.DataFrame({"loja_id": [1, 2, 3], "razao_social": [" DROGARIA LIZ", "AFONSO", "BELA VISTA "]})
    assert analise.ordenar(df, "razao_social", True)["loja_id"].tolist() == [2, 3, 1]


def test_ordenar_numero():
    assert analise.ordenar(_tabela(), "economia", False)["economia"].tolist() == [30.0, 10.0, 5.0]


def test_filtros_em_memoria():
    df = _tabela()
    assert len(analise.filtrar(df, analise.Filtros(uf="SP"))) == 2
    assert len(analise.filtrar(df, analise.Filtros(busca="ems"))) == 1
    assert len(analise.filtrar(df, analise.Filtros(grupo_economico="G1", loja_ids=[2]))) == 2
    # Corte de acesso (Comprador/Proprietário, 02/10/2026): vale sempre, mesmo
    # com uma escolha de loja fora dele.
    assert set(analise.filtrar(df, analise.Filtros(lojas_permitidas=(2,)))["loja_id"]) == {2}
    assert analise.filtrar(df, analise.Filtros(lojas_permitidas=(2,), loja_ids=[1])).empty
    assert analise.filtrar(df, analise.Filtros(lojas_permitidas=())).empty


def test_filtro_de_categoria_conta_vazio_como_sem_classificacao():
    df = _tabela().assign(categoria=["PRESCRIÇÃO GENÉRICO", None, "HIGIENE"])
    assert analise.filtrar(df, analise.Filtros(categorias=("Sem Classificação",)))["base_generico_id"].tolist() == [1]
    assert len(analise.filtrar(df, analise.Filtros(categorias=("HIGIENE", "PRESCRIÇÃO GENÉRICO")))) == 2


def test_texto_ausente_sai_como_none_e_nao_nan():
    """Na tela, `valor or "—"` mostraria "nan" se o vazio viesse como NaN
    (NaN é verdadeiro em Python) — aconteceu no detalhe da loja."""
    df = analise._texto_ausente_como_none(_tabela(), ["grupo_economico"])
    assert df.loc[0, "grupo_economico"] is None


def test_laboratorios_disponiveis_so_com_cobertura_ativa():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        tabelas = [TabelaGruppy(laboratorio=lab, modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="x",
                                criado_por="t") for lab in ("Ranbaxy", "Medley", "Antigo")]
        session.add_all(tabelas)
        session.flush()
        session.add_all([
            TabelaGruppyCobertura(tabela_gruppy_id=tabelas[0].id, uf="MG", status=StatusCobertura.ATIVA),
            TabelaGruppyCobertura(tabela_gruppy_id=tabelas[1].id, uf="SP", status=StatusCobertura.ATIVA),
            TabelaGruppyCobertura(tabela_gruppy_id=tabelas[2].id, uf="MG", status=StatusCobertura.INATIVA),
        ])
        session.flush()
        assert analise.laboratorios_disponiveis(session) == ["Medley", "Ranbaxy"]


def _para_busca() -> pd.DataFrame:
    return pd.DataFrame({
        "loja_id": [1, 2], "razao_social": ["DROGARIA UM", "FARMACIA 2020"], "cnpj": ["10482949000129", "20643529000130"],
        "nome_canonico": ["LOSARTANA", "DIPIRONA"], "laboratorio": ["EMS", "TEUTO"], "descricao": [None, None],
        "uf": ["MG", "MG"], "atendente_comercial": [None, None], "grupo_economico": [None, None],
    })


def test_busca_acha_cnpj_digitado_com_pontuacao():
    df = _para_busca()
    for termo in ("10.482.949/0001-29", "10.482.949", "10482949000129"):
        assert analise.filtrar(df, analise.Filtros(laboratorio="X", periodo_meses=1, busca=termo))["loja_id"].tolist() == [1]


def test_busca_de_texto_com_numero_continua_por_texto():
    df = _para_busca()
    assert analise.filtrar(df, analise.Filtros(laboratorio="X", periodo_meses=1, busca="farmacia 2020"))["loja_id"].tolist() == [2]
