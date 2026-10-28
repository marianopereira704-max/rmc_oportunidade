"""Página Estoque (22/10/2026): as contas de leitura de core/dashboard_estoque.py, sobre um modelo de estoque
pequeno montado à mão — cada número dá para conferir de cabeça. A janela vai de 01/07 a 20/10/2026, então "90 dias"
começam em 23/07 e "30 dias", em 21/09."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from core import dashboard_estoque as de
from pedido import categorias as cat
from pedido import linhas as lc
from pedido.configuracao import ConfigPedido
from pedido.estoque_modelo import COLUNAS_SKUS, Modelo

CONFIG = ConfigPedido.padrao()          # parado: 90 dias · excesso: 60 dias · curva 50/40/10
NA_JANELA = "2026-09-10"                # 40 dias antes do fim: dentro de 90 dias, fora de 30
ANTES_DA_JANELA = "2026-07-01"          # 111 dias antes do fim: fora dos 90 dias


def _sku(codigo, estoque, custo=2.0, venda=5.0, ean=None, nome=None, revisar=False, ultima_compra=None):
    return dict(codigo_produto=codigo, ean=ean or f"789{codigo}", nome=nome or f"PRODUTO {codigo}", laboratorio="LAB",
                estoque=float(estoque), custo=custo, custo_origem="compra", custo_a_revisar=revisar, custo_motivo=None,
                custo_cadastro=custo, preco_venda=venda, compra_data=None, compra_fornecedor=None, compra_preco=None,
                ultima_compra=ultima_compra, ultima_venda=None)


def _modelo(skus, vendas=(), **meta):
    meta = {"inicio": "2026-07-01", "fim": "2026-10-20", "dias_sem_vendas": [], **meta}
    v = pd.DataFrame(list(vendas), columns=["codigo_produto", "data", "unidades", "valor"])
    return Modelo(pd.DataFrame(skus, columns=COLUNAS_SKUS), v, meta)


def _classes(setores):
    """EAN → setor da IQVIA virando EAN → (categoria, origem) pela cascata de classificação (25/10/2026)."""
    from pedido import classificacao as cl
    if not setores:
        return None
    return cl.mapa_por_ean(cl.classificar(pd.DataFrame({"ean": list(setores), "setor_nec": list(setores.values())})))


def _prep(skus, vendas=(), config=CONFIG, categorias=None, setores=None):
    return de.preparar(_modelo(skus, vendas), config, categorias, _classes(setores))


def _por(d, codigo):
    return d[d["codigo_produto"] == codigo].iloc[0]


# 180 unidades na janela de 90 dias = 2 por dia.
V2_POR_DIA = ("%s", NA_JANELA, 180, 360.0)


def _vende(codigo, un=180, valor=360.0, data=NA_JANELA):
    return (codigo, data, un, valor)


# ---------------------------------------------------------------------------
# Parado, excesso e cobertura
# ---------------------------------------------------------------------------

def test_parado_e_o_sku_sem_nenhuma_venda_nos_ultimos_n_dias():
    d = _prep([_sku("A", 10), _sku("B", 10), _sku("C", 10)],
              [_vende("A"), _vende("C", data=ANTES_DA_JANELA)])
    assert dict(zip(d["codigo_produto"], d["parado"])) == {"A": False, "B": True, "C": True}   # C só vendeu antes dos 90 dias
    assert (d.loc[d["parado"], "vendidas_n"] == 0).all()
    assert (d.loc[d["parado"], "situacao"] == "parado").all()


def test_excesso_e_so_o_que_passa_de_60_dias_de_venda():
    d = _prep([_sku("A", 300), _sku("B", 100)], [_vende("A"), _vende("B")])
    a, b = _por(d, "A"), _por(d, "B")
    assert a["demanda_dia"] == pytest.approx(2.0)
    assert a["excesso_un"] == pytest.approx(300 - 2 * 60) and a["situacao"] == "excesso"       # 180 acima de 60 dias
    assert a["valor_excesso"] == pytest.approx(180 * 2.0) and a["valor_eficiente"] == pytest.approx(120 * 2.0)
    assert b["excesso_un"] == 0 and b["situacao"] == "eficiente" and b["cobertura"] == pytest.approx(50)


def test_cobertura_e_unidades_em_estoque_por_unidades_vendidas_por_dia():
    d = _prep([_sku("A", 300), _sku("B", 5)], [_vende("A")])
    assert _por(d, "A")["cobertura"] == pytest.approx(150)
    assert np.isnan(_por(d, "B")["cobertura"])                                  # sem venda: não há cobertura a calcular


def test_parado_fica_fora_do_excesso_e_as_tres_classes_somam_o_estoque():
    d = _prep([_sku("A", 300), _sku("B", 1000), _sku("C", 100)], [_vende("A"), _vende("C")])
    b = _por(d, "B")
    assert b["parado"] and b["excesso_un"] == 0 and b["valor_excesso"] == 0 and b["valor_parado"] == pytest.approx(2000)
    assert (d["valor_eficiente"] + d["valor_excesso"] + d["valor_parado"]).tolist() == pytest.approx(d["valor_estoque"].tolist())
    assert (d["valor_eficiente"] >= 0).all()


def test_o_n_do_parado_e_da_loja_e_muda_o_ritmo_de_venda():
    """Vendeu há 40 dias: não é parado em 90 dias, é em 30. E 180 un. em 30 dias são 6 por dia, não 2."""
    skus, vendas = [_sku("A", 300)], [_vende("A")]
    assert not _por(_prep(skus, vendas), "A")["parado"]
    d30 = _prep(skus, vendas, dataclasses.replace(CONFIG, dias_parado=30))
    assert _por(d30, "A")["parado"]
    d120 = _prep(skus, vendas, dataclasses.replace(CONFIG, dias_parado=120))      # janela maior que a de 90 dias da curva
    assert not _por(d120, "A")["parado"] and _por(d120, "A")["demanda_dia"] == pytest.approx(180 / 120)


def test_o_limite_de_dias_do_excesso_e_da_loja():
    skus, vendas = [_sku("A", 150)], [_vende("A")]
    assert _por(_prep(skus, vendas), "A")["excesso_un"] == pytest.approx(30)       # 150 − 2×60
    d = _prep(skus, vendas, dataclasses.replace(CONFIG, dias_cobertura_excesso=90))
    assert _por(d, "A")["excesso_un"] == 0                                         # 150 − 2×90 < 0


# ---------------------------------------------------------------------------
# Custo
# ---------------------------------------------------------------------------

def test_custo_a_revisar_conta_as_unidades_mas_nao_entra_no_valor():
    d = _prep([_sku("A", 10, custo=5.0, venda=9.0, revisar=True), _sku("B", 10, custo=5.0)], [_vende("B")])
    a = _por(d, "A")
    assert not a["custo_ok"] and a["valor_estoque"] == 0 and a["valor_parado"] == 0 and a["valor_critico"] == 0
    assert a["valor_venda"] == pytest.approx(90) and a["estoque"] == 10
    c = de.cards(d)
    assert c.unidades == 20 and c.estoque_compra == pytest.approx(50) and c.a_revisar_skus == 1
    assert c.parado_skus == 1 and c.parado_valor == 0                              # conta o SKU parado, não os R$ dele


def test_sku_sem_custo_vira_a_revisar_sem_quebrar_nada():
    d = _prep([_sku("A", 10, custo=None, revisar=True)], [_vende("A")])
    assert not _por(d, "A")["custo_ok"] and _por(d, "A")["valor_estoque"] == 0


# ---------------------------------------------------------------------------
# Curva
# ---------------------------------------------------------------------------

def test_curva_aa_a_cc_pela_venda_de_90_dias_e_d_para_o_parado():
    d = _prep([_sku("A", 5), _sku("B", 5), _sku("C", 5), _sku("P", 5)],
              [_vende("A", 100, 100.0), _vende("B", 40, 40.0), _vende("C", 10, 10.0)])
    assert dict(zip(d["codigo_produto"], d["curva"])) == {"A": "AA", "B": "BB", "C": "CC", "P": "D"}


def test_a_proporcao_da_curva_conta_quem_vendeu_mesmo_sem_estoque():
    """Z vendeu muito e não está na lista (sem estoque): ele ainda empurra os outros para B e C, como no Assistente."""
    d = _prep([_sku("A", 5), _sku("B", 5)], [_vende("Z", 1000, 1000.0), _vende("A", 100, 100.0), _vende("B", 40, 40.0)])
    assert dict(zip(d["codigo_produto"], d["curva"])) == {"A": "BB", "B": "CC"}


def test_a_curva_e_sempre_de_90_dias_mesmo_com_o_n_do_parado_menor():
    """Com N=30, quem vendeu há 40 dias é parado ("D"); quem vendeu na janela de 30 mantém a curva dos 90."""
    recente = "2026-10-10"
    skus = [_sku("A", 5), _sku("B", 5)]
    vendas = [_vende("A", 100, 100.0, recente), _vende("B", 100, 100.0)]
    d = _prep(skus, vendas, dataclasses.replace(CONFIG, dias_parado=30))
    assert _por(d, "B")["curva"] == "D" and _por(d, "A")["curva"] != "D"


def test_o_corte_da_curva_e_o_da_configuracao_da_loja():
    vendas = [_vende("A", 60, 60.0), _vende("B", 40, 40.0)]
    d = _prep([_sku("A", 5), _sku("B", 5)], vendas)                                # padrão 50/40
    assert _por(d, "A")["curva"] == "AA" and _por(d, "B")["curva"] == "BB"
    d = _prep([_sku("A", 5), _sku("B", 5)], vendas, dataclasses.replace(CONFIG, curva_a=0.7, curva_b=0.2))
    # B começa em 60% da venda: com A de 70% ele ainda é A (o item que cruza a linha continua A)
    assert _por(d, "A")["curva"] == "AA" and _por(d, "B")["curva"] == "AA"


# ---------------------------------------------------------------------------
# Compra recente, categoria e linha
# ---------------------------------------------------------------------------

def test_compra_recente_e_a_ultima_compra_nos_ultimos_30_dias():
    d = _prep([_sku("A", 5, ultima_compra="2026-10-05"), _sku("B", 5, ultima_compra="2026-08-01"), _sku("C", 5)])
    assert dict(zip(d["codigo_produto"], d["compra_recente"])) == {"A": True, "B": False, "C": False}
    assert _por(d, "A")["parado"]                                                    # continua parado: a tabela só marca


def test_categoria_sem_classificacao_para_vazio_e_para_em_classificacao():
    d = _prep([_sku("A", 5, ean="111"), _sku("B", 5, ean="222"), _sku("C", 5, ean="333")],
              categorias={"111": "INFANTIL", "222": cat.EM_CLASSIFICACAO})
    assert dict(zip(d["codigo_produto"], d["categoria_rotulo"])) == {"A": "INFANTIL", "B": de.SEM_CATEGORIA, "C": de.SEM_CATEGORIA}
    assert d["categoria"].isna().sum() == 2


def test_linha_de_compra_pela_cascata_de_classificacao():
    d = _prep([_sku("A", 5, ean="111"), _sku("B", 5, ean="222")], categorias={"111": "INFANTIL", "222": "HIGIENE"},
              setores={"111": "RX_GENERICO"})
    assert _por(d, "A")["linha_compra"] == lc.GENERICOS                              # o setor do catálogo manda
    # Fora da IQVIA e da CMED: "Em classificação" (a FEBRAFAR, "HIGIENE" aqui, não classifica desde 25/10/2026).
    assert _por(d, "B")["linha_compra"] == lc.SEM_LINHA


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def _tres():
    return _prep([_sku("A", 5, ean="111"), _sku("B", 5, ean="222"), _sku("C", 5, ean="333")],
                 categorias={"111": "INFANTIL", "222": "INFANTIL", "333": "HIGIENE"}, setores={"111": "RX_GENERICO", "222": "RX_TRADE"})


def test_filtro_cruzado_ou_dentro_da_dimensao_e_entre_dimensoes():
    d = _tres()
    assert len(de.filtrar(d)) == 3
    assert set(de.filtrar(d, categorias=["INFANTIL"])["codigo_produto"]) == {"A", "B"}
    assert set(de.filtrar(d, categorias=["INFANTIL", "HIGIENE"])["codigo_produto"]) == {"A", "B", "C"}     # OU
    assert set(de.filtrar(d, categorias=["INFANTIL"], linhas=[lc.GENERICOS])["codigo_produto"]) == {"A"}   # E
    assert de.filtrar(d, categorias=["nao_existe"]).empty


def test_cada_bloco_ignora_o_proprio_filtro():
    d = _tres()
    so_linha = de.sem_filtro_de(d, "categoria", categorias=["HIGIENE"], linhas=[lc.GENERICOS])
    assert set(so_linha["codigo_produto"]) == {"A"}                                  # a categoria escolhida não vale aqui
    so_cat = de.sem_filtro_de(d, "linha", categorias=["INFANTIL"], linhas=[lc.GENERICOS])
    assert set(so_cat["codigo_produto"]) == {"A", "B"}


# ---------------------------------------------------------------------------
# Cards e tabela por categoria
# ---------------------------------------------------------------------------

def _loja():
    skus = [_sku("A", 300, ean="111"), _sku("B", 100, ean="222"), _sku("C", 50, ean="333"), _sku("D", 20, ean="444")]
    vendas = [_vende("A"), _vende("B")]                                               # C e D: parados
    return _prep(skus, vendas, categorias={"111": "INFANTIL", "222": "INFANTIL", "333": "HIGIENE"})


def test_cards_somam_o_estoque_a_preco_de_compra():
    c = de.cards(_loja())
    # estoque a custo: 300×2 + 100×2 + 50×2 + 20×2 = 940; excesso de A: 180 un. = 360; parado: C + D = 140
    assert (c.skus, c.unidades, c.estoque_compra) == (4, 470, pytest.approx(940))
    assert (c.excesso_valor, c.excesso_skus) == (pytest.approx(360), 1)
    assert (c.parado_valor, c.parado_skus) == (pytest.approx(140), 2)
    assert c.excesso_pct == pytest.approx(360 / 940 * 100) and c.parado_pct == pytest.approx(140 / 940 * 100)
    assert c.estoque_venda == pytest.approx(470 * 5.0)


def test_cards_de_um_modelo_vazio_sao_zero():
    c = de.cards(_prep([]))
    assert (c.skus, c.unidades, c.estoque_compra, c.excesso_valor, c.parado_valor) == (0, 0.0, 0.0, 0.0, 0.0)


def _fontes(linhas):
    """Universo das três fontes → (EAN → (categoria, origem), caminho na cascata), como o app monta (26/10/2026)."""
    from pedido import classificacao as cl
    u = pd.DataFrame(linhas, columns=["ean", "setor_nec", "febrafar", "cmed_tipo"])
    tabela = cl.tabela_por_ean(cl.classificar(u))
    return cl.mapa_da_loja(tabela, set(u["ean"])), cl.caminhos_da_loja(tabela, set(u["ean"]))


def _loja_classificada():
    skus = [_sku("A", 300, ean="111"), _sku("B", 100, ean="222"), _sku("C", 50, ean="333"), _sku("D", 20, ean="444")]
    vendas = [_vende("A"), _vende("B")]                                               # C e D: parados
    classes, caminhos = _fontes([
        ["111", "RX_GENERICO", "PRESCRIÇÃO GENÉRICO", "Genérico"],
        ["222", "RX_GENERICO", "MIP/OTC", None],
        ["333", None, "PRESCRIÇÃO TRADE", "Similar"],                                 # sem IQVIA: a CMED classifica
    ])                                                                                # 444: nenhuma fonte
    return de.preparar(_modelo(skus, vendas), CONFIG, None, classes, caminhos)


def test_tabela_por_categoria_e_pelas_5_categorias_com_total_e_cobertura_pela_soma():
    """26/10/2026: a tabela mostra as categorias da classificação de produtos (antes, as da base/FEBRAFAR)."""
    d = _loja_classificada()
    t = de.por_categoria(d)
    assert t.index.tolist() == [lc.GENERICOS, lc.SIMILARES, lc.SEM_LINHA]             # do maior estoque a preço de compra
    gen = t.loc[lc.GENERICOS]
    assert (gen["skus"], gen["unidades"], gen["estoque_compra"]) == (2, 400, pytest.approx(800))
    assert gen["cobertura"] == pytest.approx(400 / 4)                                 # 400 un. ÷ 4 un. por dia = 100 dias
    assert np.isnan(t.loc[lc.SIMILARES, "cobertura"])                                 # sem venda: sem cobertura
    assert t["pct_compra"].sum() == pytest.approx(100) and t["pct_venda"].sum() == pytest.approx(100)
    tot = de.total_por_categoria(d)
    assert (tot["skus"], tot["unidades"], tot["estoque_compra"]) == (4, 470, pytest.approx(940))
    assert tot["cobertura"] == pytest.approx(470 / 4)                                 # Σ unidades ÷ Σ vendidas por dia
    assert tot["estoque_venda"] == pytest.approx(t["estoque_venda"].sum())


def test_detalhe_por_categoria_encadeia_iqvia_febrafar_cmed_e_pula_o_que_falta():
    det = de.detalhe_por_categoria(_loja_classificada())
    gen = det[lc.GENERICOS]
    assert [(n["fonte"], n["chave"], n["metricas"]["skus"]) for n in gen] == [("iqvia", "RX_GENERICO", 2)]
    febrafar = gen[0]["filhos"]
    assert [(n["fonte"], n["chave"]) for n in febrafar] == [("febrafar", "PRESCRIÇÃO GENÉRICO"), ("febrafar", "MIP/OTC")]
    assert [(n["fonte"], n["chave"]) for n in febrafar[0]["filhos"]] == [("cmed", "Genérico")]
    assert febrafar[1]["filhos"] == []
    # Sem IQVIA: a FEBRAFAR vem direto abaixo da categoria (sem linha "Sem classificação" — decisão de 26/10/2026).
    sim = det[lc.SIMILARES]
    assert [(n["fonte"], n["chave"]) for n in sim] == [("febrafar", "PRESCRIÇÃO TRADE")]
    assert [(n["fonte"], n["chave"]) for n in sim[0]["filhos"]] == [("cmed", "Similar")]
    assert det[lc.SEM_LINHA] == []                                                    # nenhuma fonte: nada abre
    # Os % são do total da tabela, não do pai.
    assert gen[0]["metricas"]["pct_compra"] == pytest.approx(800 / 940 * 100)


def test_tabela_por_categoria_de_modelo_vazio():
    assert de.por_categoria(_prep([])).empty
    assert np.isnan(de.total_por_categoria(_prep([]))["cobertura"])


def test_estoque_por_linha_reusa_a_conta_da_ruptura():
    d = _loja()
    e = de.estoque_por_linha(d)
    assert e["total"].sum() == pytest.approx(d["valor_estoque"].sum())
    assert (e["excesso"].sum(), e["parado"].sum()) == (pytest.approx(360), pytest.approx(140))


# ---------------------------------------------------------------------------
# Produtos críticos
# ---------------------------------------------------------------------------

def _para_busca():
    skus = [_sku("A", 300, ean="7891111111111", nome="LENÇO UMEDECIDO BEBÊ"), _sku("B", 30, ean="7892222222222", nome="DIPIRONA 1G"),
            _sku("C", 1000, ean="7893333333333", nome="FRALDA JUMBO", custo=9.0), _sku("D", 8, ean="7894444444444", nome="SABONETE", custo=3.0),
            _sku("E", 15, ean="7895555555555", nome="CUSTO DUVIDOSO", revisar=True)]
    return _prep(skus, [_vende("A"), _vende("B"), _vende("D")], categorias={})


def test_criticos_sao_excesso_e_parado_pelo_valor_do_maior_para_o_menor():
    c = de.criticos(_para_busca())
    assert c["codigo_produto"].tolist() == ["C", "A", "E"]                              # C parado 9.000; A excesso 360; E sem R$
    assert "B" not in c["codigo_produto"].tolist() and "D" not in c["codigo_produto"].tolist()   # eficientes


def test_criticos_busca_sem_acento_e_maiuscula_ou_pelo_ean():
    d = _para_busca()
    assert de.criticos(d, "lenco bebe")["codigo_produto"].tolist() == ["A"]
    assert de.criticos(d, "FRALDA")["codigo_produto"].tolist() == ["C"]
    assert de.criticos(d, "7893333")["codigo_produto"].tolist() == ["C"]               # pedaço do EAN
    assert de.criticos(d, "bebe lenco")["codigo_produto"].tolist() == ["A"]            # todas as palavras, em qualquer ordem
    assert de.criticos(d, "lenco fralda").empty                                         # falta uma das palavras
    assert de.criticos(d, "nenhum nome assim").empty
    assert len(de.criticos(d, "   ")) == 3                                              # busca em branco = sem busca


def test_criticos_filtro_de_situacao():
    d = _para_busca()
    assert de.criticos(d, situacao="excesso")["codigo_produto"].tolist() == ["A"]
    assert set(de.criticos(d, situacao="parado")["codigo_produto"]) == {"C", "E"}
    assert len(de.criticos(d, situacao="todos")) == 3


def test_criticos_de_modelo_vazio():
    assert de.criticos(_prep([])).empty


# ---------------------------------------------------------------------------
# Janela guardada e configuração
# ---------------------------------------------------------------------------

def test_dias_de_venda_guardados_desconta_os_dias_que_nao_chegaram():
    assert de.dias_de_venda_guardados({"inicio": "2026-07-01", "fim": "2026-07-10", "dias_sem_vendas": []}) == 10
    assert de.dias_de_venda_guardados({"inicio": "2026-07-01", "fim": "2026-07-10", "dias_sem_vendas": ["2026-07-03", "2026-07-04"]}) == 8


def test_o_parametro_dias_parado_tem_padrao_e_limites():
    assert ConfigPedido.padrao().dias_parado == 90
    assert ConfigPedido.de_dict({"dias_parado": 3}).erros() and ConfigPedido.de_dict({"dias_parado": 400}).erros()
    assert ConfigPedido.de_dict({"dias_parado": 30}).erros() == []
    antigo = {k: v for k, v in ConfigPedido.padrao().para_dict().items() if k != "dias_parado"}   # config gravada antes do campo
    assert ConfigPedido.de_dict(antigo).dias_parado == 90


def test_excesso_e_em_unidades_inteiras_e_quantidade_vezes_custo_bate_com_o_valor():
    """Caso real (Domperidona, loja 60): 999 un., 1 venda em 90 dias, custo R$ 4,15 → limite 0,67 un.; o excesso
    é 998 un. (não 998,33) e o valor, 998 × 4,15."""
    a = _por(_prep([_sku("A", 999, 4.15)], [_vende("A", un=1, valor=5.0)]), "A")
    assert a["excesso_un"] == 998 and a["valor_excesso"] == pytest.approx(998 * 4.15)
    assert a["valor_excesso"] == pytest.approx(a["excesso_un"] * a["custo"])
