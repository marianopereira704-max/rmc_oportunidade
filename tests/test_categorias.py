"""Base de categorias do Pedido: regras (pedido/categorias.py) e banco
(integrations/categorias.py)."""
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, CategoriaEan, OrigemCategoria
from integrations import categorias as integ
from pedido import categorias as cat
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Nomes, grupos e EAN
# ---------------------------------------------------------------------------

def test_categoria_oficial_aceita_grafias_da_febrafar_e_do_admin():
    assert cat.categoria_oficial("MIP l OTC") == "MIP/OTC"            # como vem no arquivo da FEBRAFAR
    assert cat.categoria_oficial("prescrição generico") == cat.GENERICO
    assert cat.categoria_oficial("Conveniencia") == "CONVENIÊNCIA"
    assert cat.categoria_oficial("genérico") == cat.GENERICO
    assert cat.categoria_oficial("remédio") is None                  # sem adivinhar
    assert cat.categoria_oficial("") is None


def test_grupos_dias_de_estoque():
    assert cat.grupo(cat.GENERICO) == cat.MEDICAMENTO
    assert cat.grupo("MIP/OTC") == cat.MEDICAMENTO
    assert cat.grupo(cat.MEDICAMENTO_CMED) == cat.MEDICAMENTO
    assert cat.grupo("INFANTIL") == cat.PERFUMARIA                   # Q26: fica em perfumaria por enquanto
    assert cat.grupo(cat.EM_CLASSIFICACAO) == cat.SEM_CLASSIFICACAO
    assert cat.grupo(None) == cat.SEM_CLASSIFICACAO
    assert cat.EM_CLASSIFICACAO not in cat.CATEGORIAS_MANUAIS


def test_chave_ean_tira_zero_a_esquerda_e_sufixo_do_excel():
    assert cat.chave_ean("07896422507295") == "7896422507295"        # GTIN-14 da CMED = EAN-13 do GPS
    assert cat.chave_ean(7896422507295.0) == "7896422507295"
    assert cat.chave_ean("7896422507295.0") == "7896422507295"
    assert cat.chave_ean(" - ") is None and cat.chave_ean(None) is None and cat.chave_ean(float("nan")) is None
    serie = cat.chaves_ean(pd.Series(["07891", "7891.0", "-", None]))
    assert serie.tolist()[:2] == ["7891", "7891"] and serie.isna().tolist()[2:] == [True, True]


# ---------------------------------------------------------------------------
# Junção FEBRAFAR + CMED
# ---------------------------------------------------------------------------

def _febrafar(linhas):
    return pd.DataFrame(linhas, columns=["ean", "categoria", "descricao"])


def _cmed(linhas):
    return pd.DataFrame(linhas, columns=["ean", "tipo", "descricao"])


def test_juncao_segue_a_ordem_das_regras():
    f = _febrafar([
        ("1", "BELEZA PELE CABELOS", "pomada"),       # CMED diz genérico → genérico
        ("2", "MIP/OTC", "dipirona similar"),         # FEBRAFAR vale sobre CMED não-genérico
        ("3", cat.EM_CLASSIFICACAO, "novo"),          # FEBRAFAR pendente → CMED preenche
        ("4", cat.EM_CLASSIFICACAO, "sem cmed"),      # fica EM CLASSIFICAÇÃO
        ("6", "HIGIENE", "sabonete"),
    ])
    c = _cmed([
        ("1", "Genérico", "NISTATINA"),
        ("2", "Similar", "DIPIRONA"),
        ("3", "Novo", "REMEDIO NOVO"),
        ("5", "Biológico", "SO NA CMED"),
    ])
    base = cat.montar_base_inicial(f, c).set_index("ean")
    assert base.loc["1", "categoria"] == cat.GENERICO and base.loc["1", "origem"] == "CMED"
    assert base.loc["2", "categoria"] == "MIP/OTC" and base.loc["2", "origem"] == "FEBRAFAR"
    assert base.loc["3", "categoria"] == cat.MEDICAMENTO_CMED
    assert base.loc["4", "categoria"] == cat.EM_CLASSIFICACAO
    assert base.loc["5", "categoria"] == cat.MEDICAMENTO_CMED and base.loc["5", "descricao"] == "SO NA CMED"
    assert base.loc["6", "categoria"] == "HIGIENE"
    assert len(base) == 6


def test_ean_repetido_prefere_categoria_de_verdade_e_genérico_da_cmed():
    f = _febrafar([("1", cat.EM_CLASSIFICACAO, "a"), ("1", "HIGIENE", "b"), ("2", "CUIDADOS", "c")])
    c = _cmed([("2", "Similar", "x"), ("2", "Genérico", "y")])       # mesmo EAN em duas apresentações
    base = cat.montar_base_inicial(f, c).set_index("ean")
    assert base.loc["1", "categoria"] == "HIGIENE"
    assert base.loc["2", "categoria"] == cat.GENERICO


def test_ler_cmed_acha_o_cabecalho_depois_da_apresentacao_e_os_tres_eans(tmp_path):
    topo = pd.DataFrame([["LISTA DE PREÇOS"], ["Publicada em ..."], [None]])
    corpo = pd.DataFrame(
        [["DIPIRONA", "500MG", "07891000000019", "7891000000026", "    -     ", "Genérico"],
         ["OUTRO", "10ML", "7891000000033", "-", "-", "    -     "]],
        columns=["PRODUTO", "APRESENTAÇÃO", "EAN 1", "EAN 2", "EAN 3", "TIPO DE PRODUTO (STATUS DO PRODUTO)"],
    )
    caminho = tmp_path / "cmed.xlsx"
    with pd.ExcelWriter(caminho) as w:
        topo.to_excel(w, index=False, header=False)
        corpo.to_excel(w, index=False, startrow=len(topo))
    df = cat.ler_cmed(caminho)
    assert sorted(df["ean"]) == ["7891000000019", "7891000000026"]   # tipo "-" sai
    assert set(df["tipo"]) == {"Genérico"} and df["descricao"].iloc[0] == "DIPIRONA 500MG"


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def _base(linhas):
    return pd.DataFrame(linhas, columns=["ean", "categoria", "origem", "descricao"])


def test_carga_inicial_insere_atualiza_e_nunca_toca_na_manual(session):
    integ.aplicar_manual(session, pd.DataFrame([{"ean": "3", "categoria": "HIGIENE", "descricao": "meu"}]), "admin")
    r = integ.carregar_base_inicial(session, _base([
        ("1", "CUIDADOS", "FEBRAFAR", "a"), ("2", cat.GENERICO, "CMED", None), ("3", "INFANTIL", "FEBRAFAR", "b"),
    ]), "admin")
    assert r.eans_no_arquivo == 3 and r.manuais_mantidas == 1
    integ.carregar_base_inicial(session, _base([("1", "HIGIENE", "FEBRAFAR", "a2")]), "admin")  # recarga
    por_ean = {c.ean: c for c in session.query(CategoriaEan)}
    assert por_ean["1"].categoria == "HIGIENE"                                    # atualizou a da carga anterior
    assert por_ean["2"].categoria == cat.GENERICO                                 # saiu da lista, mas não some
    assert por_ean["3"].categoria == "HIGIENE" and por_ean["3"].origem == OrigemCategoria.MANUAL
    t = integ.tabela(session)
    assert set(t["origem"]) == {"FEBRAFAR", "CMED", "MANUAL"}
    assert integ.contagens(session)["por_grupo"] == {cat.PERFUMARIA: 2, cat.MEDICAMENTO: 1}


def test_planilha_manual_ignora_vazia_recusa_desconhecida_e_ultima_linha_vence(session):
    df = pd.DataFrame({
        "EAN": [7891.0, "07892", "7893", "", "7891"],
        "PRODUTO": ["a", "b", "c", "d", "a de novo"],
        "Categoria": ["higiene", None, "remédio", "HIGIENE", "MIP l OTC"],
    })
    lida = integ.ler_planilha_manual(df)
    assert lida.validas.to_dict("records") == [{"ean": "7891", "categoria": "MIP/OTC", "descricao": "a de novo"}]
    assert lida.invalidas == [(4, 'categoria desconhecida: "remédio"'), (5, "EAN vazio ou inválido")]
    integ.carregar_base_inicial(session, _base([("7891", "HIGIENE", "FEBRAFAR", None)]), "admin")
    assert integ.aplicar_manual(session, lida.validas, "admin") == 1
    linha = session.get(CategoriaEan, "7891")
    assert linha.categoria == "MIP/OTC" and linha.origem == OrigemCategoria.MANUAL   # manual passa por cima


def test_planilha_manual_sem_as_colunas_e_recusada():
    with pytest.raises(ValueError, match="EAN e CATEGORIA"):
        integ.ler_planilha_manual(pd.DataFrame({"EAN": ["1"], "PRODUTO": ["x"]}))


def test_versao_muda_quando_a_base_muda(session):
    v0 = integ.versao(session)
    integ.aplicar_manual(session, pd.DataFrame([{"ean": "1", "categoria": "HIGIENE", "descricao": None}]), "admin")
    assert integ.versao(session) != v0


# ---------------------------------------------------------------------------
# Catálogo das lojas e relatório Sem Classificação
# ---------------------------------------------------------------------------

def _estoque(linhas):
    return pd.DataFrame(linhas, columns=["codigo_loja", "ean", "nome", "laboratorio", "grupo", "categoria_gps", "qtd_estoque"])


def test_catalogo_conta_lojas_vinculadas_e_lojas_com_estoque():
    est = _estoque([
        ("1", "07891", "A", "L", "G", "C", 3), ("2", "7891", "A", "L", "G", "C", 0),
        ("9", "7891", "A", "L", "G", "C", 5),                         # loja não vinculada: fora
        ("1", None, "SEM EAN", "L", "G", "C", 1),
    ])
    c = cat.catalogo(est, ["1", "2"]).set_index("ean")
    assert list(c.index) == ["7891"]
    assert c.loc["7891", "lojas"] == 2 and c.loc["7891", "lojas_com_estoque"] == 1
    assert list(cat.catalogo(est.iloc[0:0], ["1"]).columns) == cat.COLUNAS_CATALOGO


def test_relatorio_lista_nao_encontrado_e_em_classificacao(tmp_path, session):
    armaz, chaves = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    armaz.salvar_df(chaves.catalogo("E1"), cat.catalogo(_estoque([
        ("1", "1", "CLASSIFICADO", "L", "G", "C", 1),
        ("1", "2", "PENDENTE FEBRAFAR", "L", "G", "C", 1),
        ("1", "3", "DESCONHECIDO", "L", "G", "C", 2),
        ("1", "4", "SEM ESTOQUE", "L", "G", "C", 0),
    ]), ["1"]))
    armaz.salvar_df(chaves.catalogo("E2"), cat.catalogo(_estoque([("5", "3", "DESCONHECIDO", "L", "G", "C", 1)]), ["5"]))
    integ.carregar_base_inicial(session, _base([
        ("1", "HIGIENE", "FEBRAFAR", None), ("2", cat.EM_CLASSIFICACAO, "FEBRAFAR", None),
    ]), "admin")
    catalogo = integ.catalogos(armaz, chaves)
    rel = integ.sem_classificacao(catalogo, integ.tabela(session), so_com_estoque=True)
    assert rel["EAN"].tolist() == ["3", "2"]                        # sem venda gravada: desempata pelo estoque
    assert rel.loc[0, "LOJAS COM ESTOQUE"] == 2                      # somado entre empresas
    assert rel["SITUACAO"].tolist() == ["Não encontrado", "Em classificação na FEBRAFAR"]
    assert list(rel.columns) == integ.COLUNAS_RELATORIO
    todos = integ.sem_classificacao(catalogo, integ.tabela(session))
    assert "4" in todos["EAN"].tolist()
    # O relatório preenchido volta pela mesma tela.
    todos["CATEGORIA"] = "HIGIENE"
    assert len(integ.ler_planilha_manual(todos).validas) == 3


def test_relatorio_em_ordem_de_pareto_pelas_lojas_que_vendem():
    """01/10/2026: o EAN vendido em mais lojas primeiro, com o % acumulado."""
    catalogo = pd.DataFrame({
        "ean": ["A", "B", "C"], "nome": ["A", "B", "C"], "laboratorio": "L", "grupo_gps": "G", "categoria_gps": "C",
        "lojas": [5, 5, 5], "lojas_com_estoque": [5, 1, 1], "lojas_com_venda": [1, 6, 3],
    })
    rel = integ.sem_classificacao(catalogo, pd.DataFrame(columns=["ean", "categoria"]))
    assert rel["EAN"].tolist() == ["B", "C", "A"]
    assert integ.pareto_acumulado(rel["LOJAS COM VENDA"]).tolist() == [60.0, 90.0, 100.0]


def test_base_inicial_disponivel_le_os_metadados(tmp_path):
    armaz, chaves = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    assert integ.base_inicial_disponivel(armaz, chaves) is None
    armaz.salvar_df(chaves.base_categorias(), _base([("1", "HIGIENE", "FEBRAFAR", None)]))
    armaz.salvar_json(chaves.base_categorias_info(), {"eans": 1})
    assert integ.base_inicial_disponivel(armaz, chaves) == {"eans": 1}
