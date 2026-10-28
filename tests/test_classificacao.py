"""Classificação de produtos em cascata (pedido/classificacao.py, design de 25/10/2026): a regra pura."""
import pandas as pd
import pytest

from pedido import classificacao as cl
from pedido import linhas as lc


def _df(linhas):
    return pd.DataFrame(linhas, columns=["ean", "setor_nec", "febrafar", "cmed_tipo", "manual"])


def _cat(df, ean, **kw):
    return cl.classificar(df, **kw).set_index("ean").loc[ean, ["categoria", "origem"]].tolist()


# --- um EAN --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("setor,esperado", [
    ("RX_GENERICO", lc.GENERICOS), ("MIP_GENERICO", lc.GENERICOS),
    ("RX_PROMOVIDO", lc.PROPAGADOS), ("MIP_MARCA", lc.PROPAGADOS),
    ("RX_TRADE", lc.SIMILARES), ("MIP_TRADE", lc.SIMILARES),
    ("NAO_MEDICAMENTO_PEC", lc.PERFUMARIA), ("NAO_MEDICAMENTO_PAC", lc.PERFUMARIA),
    ("NAO_MEDICAMENTO_NTR", lc.PERFUMARIA), ("NAO_MEDICAMENTO_OTC", lc.PERFUMARIA),
])
def test_setor_da_iqvia_define_a_categoria(setor, esperado):
    assert cl.categoria_do_ean(setor=setor) == (esperado, cl.ORIGEM_IQVIA)


def test_iqvia_vence_a_cmed():
    assert cl.categoria_do_ean(setor="RX_PROMOVIDO", cmed_tipo="Genérico") == (lc.PROPAGADOS, cl.ORIGEM_IQVIA)


def test_cmed_so_classifica_generico_e_similar():
    assert cl.categoria_do_ean(cmed_tipo="Genérico") == (lc.GENERICOS, cl.ORIGEM_CMED)
    assert cl.categoria_do_ean(cmed_tipo="  SIMILAR ") == (lc.SIMILARES, cl.ORIGEM_CMED)
    for outro in ("Novo", "Biológico", "Específico", "Fitoterápico", "Radiofármaco", "Dinamizado"):
        assert cl.categoria_do_ean(cmed_tipo=outro) == (lc.SEM_LINHA, cl.ORIGEM_NENHUMA)


def test_sem_nada_fica_em_classificacao():
    assert cl.categoria_do_ean() == (lc.SEM_LINHA, cl.ORIGEM_NENHUMA)


def test_febrafar_nao_classifica_nem_e_argumento_da_decisao():
    """A FEBRAFAR não entra em `categoria_do_ean`/`classificar`: com ela dizendo "Higiene" e nenhuma outra fonte,
    o EAN fica em Em classificação (D3 do design)."""
    d = _df([["1", None, "HIGIENE", None, None]])
    assert _cat(d, "1") == [lc.SEM_LINHA, cl.ORIGEM_NENHUMA]
    with pytest.raises(TypeError):
        cl.categoria_do_ean(febrafar="HIGIENE")


def test_manual_vence_tudo_quando_tem_correspondencia_clara():
    assert cl.categoria_do_ean("RX_PROMOVIDO", "Similar", "PRESCRIÇÃO GENÉRICO") == (lc.GENERICOS, cl.ORIGEM_MANUAL)
    assert cl.categoria_do_ean(manual="PRESCRIÇÃO PROPAGADO") == (lc.PROPAGADOS, cl.ORIGEM_MANUAL)
    assert cl.categoria_do_ean(manual="PRESCRIÇÃO TRADE") == (lc.SIMILARES, cl.ORIGEM_MANUAL)
    assert cl.categoria_do_ean(manual="HIGIENE") == (lc.PERFUMARIA, cl.ORIGEM_MANUAL)


def test_manual_sem_correspondencia_clara_nao_sobrescreve():
    """MIP/OTC se analisa à parte e "Medicamento (CMED)" é rótulo provisório: seguem para a IQVIA."""
    assert cl.categoria_do_ean("RX_PROMOVIDO", manual="MIP/OTC") == (lc.PROPAGADOS, cl.ORIGEM_IQVIA)
    assert cl.categoria_do_ean(manual="MEDICAMENTO (CMED)") == (lc.SEM_LINHA, cl.ORIGEM_NENHUMA)
    assert cl.manual_para_categoria("EM CLASSIFICAÇÃO") is None and cl.manual_para_categoria(None) is None


def test_mapas_editaveis_do_admin():
    # Setor movido de categoria; setor mandado para "sem_linha" não classifica e o EAN cai na CMED.
    assert cl.categoria_do_ean("RX_TRADE", por_setor={"RX_TRADE": lc.PROPAGADOS}) == (lc.PROPAGADOS, cl.ORIGEM_IQVIA)
    assert cl.categoria_do_ean("RX_TRADE", "Genérico", por_setor={"RX_TRADE": lc.SEM_LINHA}) == (lc.GENERICOS, cl.ORIGEM_CMED)
    assert cl.categoria_do_ean(cmed_tipo="Novo", por_cmed={"Novo": lc.PROPAGADOS}) == (lc.PROPAGADOS, cl.ORIGEM_CMED)


# --- a tabela inteira ----------------------------------------------------------------------------------------

def test_classificar_a_tabela_da_o_mesmo_que_o_ean_sozinho():
    d = _df([
        ["1", "RX_GENERICO", "PRESCRIÇÃO GENÉRICO", "Genérico", None],
        ["2", None, "MIP/OTC", "Similar", None],
        ["3", None, "HIGIENE", None, None],
        ["4", "NAO_MEDICAMENTO_PEC", None, None, "PRESCRIÇÃO TRADE"],
        ["5", None, None, "Novo", None],
    ])
    out = cl.classificar(d).set_index("ean")
    esperado = {e: cl.categoria_do_ean(r.setor_nec, r.cmed_tipo, r.manual)
                for e, r in d.set_index("ean").iterrows()}
    assert {e: (out.loc[e, "categoria"], out.loc[e, "origem"]) for e in out.index} == esperado
    assert out.loc["2", "categoria"] == lc.SIMILARES and out.loc["3", "categoria"] == lc.SEM_LINHA


def test_classificar_aceita_tabela_sem_algumas_colunas():
    out = cl.classificar(pd.DataFrame({"ean": ["1", "2"], "setor_nec": ["RX_TRADE", None]}))
    assert out["categoria"].tolist() == [lc.SIMILARES, lc.SEM_LINHA]


# --- produto com vários EANs ---------------------------------------------------------------------------------

def test_produto_com_varios_eans_vale_o_ean_de_origem_mais_forte():
    mapa = {"a": (lc.GENERICOS, cl.ORIGEM_CMED), "b": (lc.PROPAGADOS, cl.ORIGEM_IQVIA), "c": (lc.SIMILARES, cl.ORIGEM_IQVIA)}
    assert cl.categoria_do_produto(["a", "b", "c"], None, mapa) == lc.PROPAGADOS        # IQVIA vence a CMED
    assert cl.categoria_do_produto(["a", "b", "c"], "c", mapa) == lc.SIMILARES          # o principal vem primeiro
    assert cl.categoria_do_produto(["a"], None, mapa) == lc.GENERICOS
    assert cl.categoria_do_produto(["x", "y"], "z", mapa) == lc.SEM_LINHA
    assert cl.categoria_do_produto(None, None, mapa) == lc.SEM_LINHA
    manual = {**mapa, "m": (lc.PERFUMARIA, cl.ORIGEM_MANUAL)}
    assert cl.categoria_do_produto(["b", "m"], "b", manual) == lc.PERFUMARIA              # manual vence tudo


def test_mapa_por_ean_guarda_so_os_classificados_e_coluna_usa_ele():
    d = cl.classificar(_df([["1", "RX_TRADE", None, None, None], ["2", None, "HIGIENE", None, None]]))
    mapa = cl.mapa_por_ean(d)
    assert mapa == {"1": (lc.SIMILARES, cl.ORIGEM_IQVIA)}
    produtos = pd.DataFrame({"eans": [["1", "2"], ["2"]], "ean_principal": ["2", "2"]}, index=[10, 11])
    assert cl.coluna(produtos, mapa).tolist() == [lc.SIMILARES, lc.SEM_LINHA]
    assert cl.coluna(produtos.iloc[0:0], mapa).empty


# --- a cascata -----------------------------------------------------------------------------------------------

def _arv(linhas, **kw):
    return {n["chave"]: n for n in cl.arvore(cl.classificar(_df(linhas)), **kw)}


def _filho(no, chave):
    return next(f for f in no["filhos"] if f["chave"] == chave)


def test_arvore_tem_as_cinco_categorias_na_ordem():
    arv = cl.arvore(cl.classificar(_df([])))
    assert [n["chave"] for n in arv] == list(cl.CATEGORIAS) and all(n["eans"] == 0 and n["filhos"] == [] for n in arv)


def test_arvore_encadeia_iqvia_febrafar_cmed():
    arv = _arv([
        ["1", "RX_GENERICO", "PRESCRIÇÃO GENÉRICO", "Genérico", None],
        ["2", "RX_GENERICO", "MIP/OTC", "Genérico", None],
        ["3", "RX_GENERICO", "MIP/OTC", None, None],
    ])
    g = arv[lc.GENERICOS]
    assert g["eans"] == 3
    iq = _filho(g, "RX_GENERICO")
    assert iq["nivel"] == cl.NIVEL_IQVIA and iq["eans"] == 3 and iq["rotulo"] == lc.SETORES["RX_GENERICO"]
    mip = _filho(iq, "MIP/OTC")
    assert mip["nivel"] == cl.NIVEL_FEBRAFAR and mip["eans"] == 2
    assert [(f["chave"], f["eans"]) for f in mip["filhos"]] == [("GENERICO", 1)]
    assert _filho(iq, "PRESCRIÇÃO GENÉRICO")["filhos"][0]["rotulo"] == "Genérico"


def test_arvore_sem_iqvia_desce_direto_da_categoria_para_a_febrafar():
    arv = _arv([["1", None, "PRESCRIÇÃO TRADE", "Similar", None]])
    s = arv[lc.SIMILARES]
    assert s["eans"] == 1 and [f["nivel"] for f in s["filhos"]] == [cl.NIVEL_FEBRAFAR]       # sem nó de IQVIA
    assert s["filhos"][0]["filhos"][0]["nivel"] == cl.NIVEL_CMED


def test_arvore_pula_o_nivel_que_o_ean_nao_tem():
    arv = _arv([["1", "RX_TRADE", None, "Similar", None]])                                    # sem FEBRAFAR
    iq = _filho(arv[lc.SIMILARES], "RX_TRADE")
    assert [f["nivel"] for f in iq["filhos"]] == [cl.NIVEL_CMED]


def test_arvore_so_mostra_tipos_da_cmed_que_classificam():
    arv = _arv([["1", "RX_PROMOVIDO", "PRESCRIÇÃO PROPAGADO", "Novo", None]])
    febrafar = _filho(_filho(arv[lc.PROPAGADOS], "RX_PROMOVIDO"), "PRESCRIÇÃO PROPAGADO")
    assert febrafar["filhos"] == []                                                          # "Novo" não classifica
    arv2 = _arv([["1", "RX_PROMOVIDO", None, "Novo", None]], por_cmed={"Novo": lc.PROPAGADOS})
    assert _filho(_filho(arv2[lc.PROPAGADOS], "RX_PROMOVIDO"), "NOVO")["eans"] == 1            # o admin mudou o mapa


def test_arvore_em_classificacao_lista_a_febrafar_de_quem_so_ela_conhece():
    """D7: a FEBRAFAR não classifica, mas o admin enxerga o que falta em "Em classificação"."""
    arv = _arv([
        ["1", None, "HIGIENE", None, None],
        ["2", None, "HIGIENE", "Novo", None],
        ["3", None, None, "Biológico", None],
        ["4", "RX_TRADE", "HIGIENE", None, None],
    ])
    resto = arv[lc.SEM_LINHA]
    assert resto["eans"] == 3                                                                # 1, 2 e 3
    assert [(f["chave"], f["eans"]) for f in resto["filhos"]] == [("HIGIENE", 2)]            # o 3 não tem FEBRAFAR
    assert arv[lc.SIMILARES]["eans"] == 1 and _filho(_filho(arv[lc.SIMILARES], "RX_TRADE"), "HIGIENE")["eans"] == 1


def test_arvore_setor_levado_a_outra_categoria_aparece_sob_ela():
    d = cl.classificar(_df([["1", "RX_TRADE", None, None, None]]), por_setor={"RX_TRADE": lc.PROPAGADOS})
    arv = {n["chave"]: n for n in cl.arvore(d)}
    assert arv[lc.SIMILARES]["eans"] == 0 and _filho(arv[lc.PROPAGADOS], "RX_TRADE")["eans"] == 1


def test_arvore_ean_de_origem_manual_ou_cmed_nao_tem_no_de_iqvia():
    """Categoria que veio da CMED ou do manual não passa pelo setor da IQVIA, mesmo que o EAN tenha um."""
    arv = _arv([["1", "NAO_MEDICAMENTO_PEC", None, None, "PRESCRIÇÃO GENÉRICO"]])
    g = arv[lc.GENERICOS]
    assert g["eans"] == 1 and g["filhos"] == []                                              # sem IQVIA nem FEBRAFAR: só o total


def test_arvore_ordena_do_maior_para_o_menor():
    arv = _arv([["1", "RX_PROMOVIDO", None, None, None], ["2", "MIP_MARCA", None, None, None],
                ["3", "MIP_MARCA", None, None, None]])
    assert [(f["chave"], f["eans"]) for f in arv[lc.PROPAGADOS]["filhos"]] == [("MIP_MARCA", 2), ("RX_PROMOVIDO", 1)]
