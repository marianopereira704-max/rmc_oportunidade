"""Carga e leitura da `classificacao_ean` (integrations/classificacao.py, 25/10/2026) e a junção das três fontes."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, CatalogoIqvia, CategoriaEan, ClassificacaoEan, OrigemCategoria
from integrations import classificacao as dados
from pedido import classificacao as cl
from pedido import linhas as lc


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _fontes(**mudar):
    df = pd.DataFrame({"ean": ["111", "222", "333"],
                       "febrafar": ["MIP/OTC", "HIGIENE", None],
                       "cmed_tipo": ["Genérico", None, "Novo"]})
    return df.assign(**mudar)


def test_carga_grava_febrafar_e_tipo_da_cmed(session):
    r = dados.carregar(session, _fontes())
    assert (r.eans, r.com_febrafar, r.com_cmed) == (3, 2, 2)
    assert session.get(ClassificacaoEan, "111").febrafar == "MIP/OTC"
    assert session.get(ClassificacaoEan, "111").cmed_tipo == "Genérico"
    assert session.get(ClassificacaoEan, "333").febrafar is None            # vazio = a fonte não conhece
    assert dados.contagem_por_cmed(session) == {"Genérico": 1, "Novo": 1}
    assert dados.versao(session)[0] == 3


def test_carregar_de_novo_atualiza_sem_duplicar_nem_apagar(session):
    dados.carregar(session, _fontes())
    dados.carregar(session, pd.DataFrame({"ean": ["111", "444"], "febrafar": ["HIGIENE", "INFANTIL"],
                                          "cmed_tipo": ["Similar", None]}))
    assert session.query(ClassificacaoEan).count() == 4                        # o 222 e o 333 continuam
    assert session.get(ClassificacaoEan, "111").febrafar == "HIGIENE"
    assert session.get(ClassificacaoEan, "111").cmed_tipo == "Similar"
    assert dados.contagem_por_cmed(session) == {"Similar": 1, "Novo": 1}


def test_carga_ignora_ean_vazio_e_repetido(session):
    r = dados.carregar(session, pd.DataFrame({"ean": ["1", "1", None], "febrafar": ["A", "B", "C"], "cmed_tipo": [None] * 3}))
    assert r.eans == 1 and session.query(ClassificacaoEan).count() == 1


def test_universo_junta_iqvia_fontes_e_manual(session):
    agora = dt.datetime(2026, 10, 25)
    dados.carregar(session, _fontes())
    session.add_all([
        CatalogoIqvia(ean="111", setor_nec="RX_PROMOVIDO", gmrs="M", atualizado_em=agora),
        CatalogoIqvia(ean="999", setor_nec="NAO_MEDICAMENTO_PEC", gmrs=None, atualizado_em=agora),   # só a IQVIA conhece
        CategoriaEan(ean="222", categoria="PRESCRIÇÃO TRADE", origem=OrigemCategoria.MANUAL, atualizado_em=agora),
        CategoriaEan(ean="333", categoria="HIGIENE", origem=OrigemCategoria.FEBRAFAR, atualizado_em=agora),  # não é manual
    ])
    session.flush()
    u = dados.universo(session).set_index("ean")
    assert set(u.index) == {"111", "222", "333", "999"}                          # a união de todas as fontes
    assert u.loc["111", "setor_nec"] == "RX_PROMOVIDO" and u.loc["111", "cmed_tipo"] == "Genérico"
    assert u.loc["222", "manual"] == "PRESCRIÇÃO TRADE" and pd.isna(u.loc["333", "manual"])
    assert pd.isna(u.loc["999", "febrafar"])


def test_universo_alimenta_a_classificacao(session):
    agora = dt.datetime(2026, 10, 25)
    dados.carregar(session, _fontes())
    session.add_all([
        CatalogoIqvia(ean="111", setor_nec="RX_PROMOVIDO", gmrs="M", atualizado_em=agora),
        CategoriaEan(ean="222", categoria="PRESCRIÇÃO TRADE", origem=OrigemCategoria.MANUAL, atualizado_em=agora),
    ])
    session.flush()
    c = cl.classificar(dados.universo(session)).set_index("ean")
    assert c.loc["111", "categoria"] == lc.PROPAGADOS and c.loc["111", "origem"] == cl.ORIGEM_IQVIA   # IQVIA vence a CMED
    assert c.loc["222", "categoria"] == lc.SIMILARES and c.loc["222", "origem"] == cl.ORIGEM_MANUAL
    assert c.loc["333", "categoria"] == lc.SEM_LINHA                                                  # CMED "Novo" não classifica
    arv = {n["chave"]: n for n in cl.arvore(c.reset_index())}
    assert arv[lc.PROPAGADOS]["eans"] == 1 and arv[lc.SEM_LINHA]["eans"] == 1


def test_universo_de_banco_vazio(session):
    assert dados.universo(session).empty
    assert dados.versao(session) == (0, "")


# --- a carga (C3): FEBRAFAR + CMED por EAN, sem decidir -------------------------------------------------------

from pedido import carga_categorias
from pedido import categorias as cat
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves


def _febrafar(linhas):
    return pd.DataFrame(linhas, columns=["ean", "categoria", "descricao"])


def _cmed(linhas):
    return pd.DataFrame(linhas, columns=["ean", "tipo", "descricao"])


def test_montar_classificacao_guarda_as_duas_fontes_mesmo_quando_a_cmed_vence():
    """Na base inicial, o EAN que a CMED diz genérico vira PRESCRIÇÃO GENÉRICO e a FEBRAFAR (MIP/OTC) se perde. Aqui
    as duas ficam."""
    f = _febrafar([["1", "MIP/OTC", "X"], ["2", "HIGIENE", "Y"], ["3", cat.EM_CLASSIFICACAO, "Z"]])
    c = _cmed([["1", "Genérico", "X"], ["4", "Similar", "W"], ["3", "Novo", "Z"]])
    out = cat.montar_classificacao(f, c).set_index("ean")
    assert out.loc["1", "febrafar"] == "MIP/OTC" and out.loc["1", "cmed_tipo"] == "Genérico"
    assert pd.isna(out.loc["2", "cmed_tipo"]) and pd.isna(out.loc["4", "febrafar"])
    assert out.loc["4", "cmed_tipo"] == "Similar"
    assert out.loc["3", "febrafar"] == cat.EM_CLASSIFICACAO                      # gravado como está
    assert list(out.index) == ["1", "2", "3", "4"]


def test_montar_classificacao_resolve_ean_repetido():
    f = _febrafar([["1", cat.EM_CLASSIFICACAO, "a"], ["1", "HIGIENE", "b"]])      # a categoria de verdade vence
    c = _cmed([["1", "Novo", "a"], ["1", "Genérico", "b"], ["1", "Similar", "c"]])  # o Genérico vence
    out = cat.montar_classificacao(f, c)
    assert len(out) == 1 and out.loc[0, "febrafar"] == "HIGIENE" and out.loc[0, "cmed_tipo"] == "Genérico"


def test_resumo_classificacao_lista_os_tipos_da_cmed():
    c = _cmed([["1", "Genérico", "a"], ["2", "Genérico", "b"], ["3", " Similar ", "c"], ["4", "Biológico", "d"]])
    out = cat.montar_classificacao(_febrafar([["1", "MIP/OTC", "a"]]), c)
    r = cat.resumo_classificacao(out)
    assert r["eans"] == 4 and r["com_febrafar"] == 1 and r["com_cmed"] == 4
    assert r["por_tipo_cmed"] == {"Genérico": 2, "Similar": 1, "Biológico": 1}    # o espaço de " Similar " saiu


@pytest.fixture()
def planilhas_falsas(monkeypatch):
    monkeypatch.setattr(cat, "ler_febrafar", lambda caminho: _febrafar([["1", "MIP/OTC", "X"], ["2", "HIGIENE", "Y"]]))
    monkeypatch.setattr(cat, "ler_cmed", lambda caminho: _cmed([["1", "Genérico", "X"], ["3", "Similar", "W"]]))


def test_carga_previa_nao_grava_nada(tmp_path, planilhas_falsas, capsys):
    destino = tmp_path / "spaces"
    assert carga_categorias.main(["--febrafar", "f.xlsx", "--cmed", "c.xlsx", "--destino", f"local:{destino}"]) == 0
    saida = capsys.readouterr().out
    assert "PRÉVIA" in saida and "por_tipo_cmed" in saida and '"Genérico": 1' in saida   # os tipos aparecem na prévia
    assert not destino.exists() or not any(destino.rglob("*.parquet"))


def test_carga_executar_grava_a_base_e_a_classificacao(tmp_path, planilhas_falsas):
    destino = tmp_path / "spaces"
    assert carga_categorias.main(["--febrafar", "f.xlsx", "--cmed", "c.xlsx", "--destino", f"local:{destino}",
                                  "--executar"]) == 0
    armaz, chaves = ArmazenamentoLocal(str(destino)), Chaves("pedido")
    assert armaz.existe(chaves.base_categorias()) and armaz.existe(chaves.classificacao_ean())
    lido = armaz.ler_df(chaves.classificacao_ean()).set_index("ean")
    assert lido.loc["1", "febrafar"] == "MIP/OTC" and lido.loc["1", "cmed_tipo"] == "Genérico"
    info = armaz.ler_json(chaves.classificacao_ean_info())
    assert info["eans"] == 3 and info["por_tipo_cmed"] == {"Genérico": 1, "Similar": 1}


def test_disponivel_e_carga_do_arquivo_para_o_banco(tmp_path, planilhas_falsas, session):
    destino = tmp_path / "spaces"
    armaz, chaves = ArmazenamentoLocal(str(destino)), Chaves("pedido")
    assert dados.disponivel(armaz, chaves) is None                                   # ainda não gerado
    carga_categorias.main(["--febrafar", "f.xlsx", "--cmed", "c.xlsx", "--destino", f"local:{destino}", "--executar"])
    assert dados.disponivel(armaz, chaves)["eans"] == 3
    r = dados.carregar(session, armaz.ler_df(chaves.classificacao_ean()))
    assert (r.eans, r.com_febrafar, r.com_cmed) == (3, 2, 2)
    assert dados.contagem_por_cmed(session) == {"Genérico": 1, "Similar": 1}


def test_carga_so_classificacao_nao_refaz_a_base_inicial(tmp_path, planilhas_falsas):
    destino = tmp_path / "spaces"
    assert carga_categorias.main(["--febrafar", "f.xlsx", "--cmed", "c.xlsx", "--destino", f"local:{destino}",
                                  "--executar", "--so-classificacao"]) == 0
    armaz, chaves = ArmazenamentoLocal(str(destino)), Chaves("pedido")
    assert armaz.existe(chaves.classificacao_ean()) and not armaz.existe(chaves.base_categorias())
