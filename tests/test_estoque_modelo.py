"""Modelo de estoque do Dashboard (22/10/2026): pedido/estoque_modelo.py — quem entra, o custo de cada SKU e a
origem dele, e o reaproveitamento pela assinatura."""
import numpy as np
import pandas as pd
import pytest

from pedido import calculo, estoque_modelo as em, pronto, transformar
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves
from tests.test_pedido_calculo import CH, _gravar_brutos, _pronto

PARAMS = em.parametros_padrao()


def _modelo(vendas, estoque, compras=(), assinatura="a1"):
    """estoque: (codigo, ean, nome, lab, qtd, custo_cadastro, preco_venda);
    compras: (codigo, ean, data, vlr_unitario, desconto, fracao, lab, fornecedor)."""
    p = _pronto(vendas, estoque, compras)
    p.meta["assinatura"] = assinatura
    return em.construir(p, PARAMS)


def _sku(m, codigo):
    return m.skus[m.skus["codigo_produto"] == codigo].iloc[0]


# ---------------------------------------------------------------------------
# Quem entra
# ---------------------------------------------------------------------------

def test_so_entra_sku_com_estoque_positivo_e_vendas_ficam_de_todos():
    m = _modelo(
        [("A", "2026-09-10", 3, 30.0), ("B", "2026-09-11", 1, 10.0), ("Z", "2026-09-12", 5, 50.0)],
        [("A", "1", "COM VENDA", "L", 5, 4.0, 10.0), ("B", "2", "ZERADO", "L", 0, 4.0, 10.0),
         ("C", "3", "NEGATIVO", "L", -3, 4.0, 10.0), ("D", "4", "SEM VENDA", "L", 2, 4.0, 10.0)])
    assert sorted(m.skus["codigo_produto"]) == ["A", "D"]                      # zerado e negativo ficam de fora
    assert set(m.vendas_dia["codigo_produto"]) == {"A", "B", "Z"}              # a curva ABC precisa de TODOS que venderam
    assert m.meta["skus"] == 2 and m.meta["versao"] == em.VERSAO
    assert m.meta["skus_negativos"] == 1                                      # o "C": a página avisa quantos ficam de fora
    assert _sku(m, "D")["ultima_venda"] is None or pd.isna(_sku(m, "D")["ultima_venda"])   # sem nenhuma venda: continua SKU
    assert _sku(m, "A")["ultima_venda"] == "2026-09-10"


def test_codigo_repetido_no_estoque_conta_uma_vez():
    m = _modelo([], [("A", "1", "X", "L", 5, 4.0, 10.0), ("A", "1", "X", "L", 7, 4.0, 10.0)])
    assert len(m.skus) == 1 and m.skus.iloc[0]["estoque"] == 5


def test_sem_nenhum_estoque_o_modelo_sai_vazio_mas_com_as_colunas():
    m = _modelo([("A", "2026-09-10", 3, 30.0)], [("A", "1", "X", "L", 0, 4.0, 10.0)])
    assert m.skus.empty and list(m.skus.columns) == em.COLUNAS_SKUS and len(m.vendas_dia) == 1


# ---------------------------------------------------------------------------
# Custo: a mesma lógica do Assistente, por código de produto
# ---------------------------------------------------------------------------

def test_com_compra_coerente_vale_o_preco_da_compra():
    m = _modelo([], [("A", "1", "X", "L", 5, 9.0, 20.0)], [("A", "1", "2026-08-10", 8.0, 0, 1, "LAB", "DIST")])
    s = _sku(m, "A")
    assert (s["custo"], s["custo_origem"], bool(s["custo_a_revisar"])) == (8.0, "compra", False)
    assert s["compra_fornecedor"] == "DIST" and s["compra_data"] == "2026-08-10" and s["ultima_compra"] == "2026-08-10"
    assert s["laboratorio"] == "LAB"                                          # o da compra, quando há


def test_sem_compra_vale_o_cadastro_se_for_coerente():
    s = _sku(_modelo([], [("B", "2", "X", "L", 5, 10.0, 25.0)]), "B")
    assert (s["custo"], s["custo_origem"], bool(s["custo_a_revisar"])) == (10.0, "cadastro", False)
    assert s["laboratorio"] == "L" and pd.isna(s["ultima_compra"])


def test_cadastro_fora_da_faixa_de_15_a_100_por_cento_do_preco_de_venda_vai_para_revisar():
    """O caso real da loja 60: lixa a R$ 30,97 de custo com preço de venda de R$ 0,50."""
    s = _sku(_modelo([], [("C", "3", "LIXA", "L", 289, 30.97, 0.5)]), "C")
    assert bool(s["custo_a_revisar"]) and s["custo_motivo"] == calculo.MOTIVO_CADASTRO
    assert s["custo_origem"] == "cadastro"                                    # o valor existe; a página é que o deixa fora do R$


def test_custo_de_compra_tambem_precisa_estar_na_faixa_do_preco_de_venda():
    """A Lixa da loja 60: cadastro E nota em R$ 30,97 para um preço de venda de R$ 0,50. O Assistente deixa passar
    (não há outra compra boa para trocar); o modelo não pode somar R$ 8.950 desse SKU."""
    s = _sku(_modelo([], [("L", "9", "LIXA", "L", 289, 30.97, 0.5)], [("L", "9", "2026-08-10", 30.97, 0, 1, "L", "D")]), "L")
    assert s["custo_origem"] == "compra" and bool(s["custo_a_revisar"]) and s["custo_motivo"] == em.MOTIVO_FAIXA
    # custo abaixo de 15% do preço de venda também (3,00 de custo para 50,00 de venda = 6%)
    s = _sku(_modelo([], [("M", "8", "X", "L", 5, 3.0, 50.0)]), "M")
    assert bool(s["custo_a_revisar"]) and s["custo_motivo"] in (em.MOTIVO_FAIXA, calculo.MOTIVO_CADASTRO)
    # custo coerente não é marcado, e SEM preço de venda não há como julgar (fica como está)
    assert not bool(_sku(_modelo([], [("N", "7", "X", "L", 5, 10.0, 25.0)]), "N")["custo_a_revisar"])
    assert not bool(_sku(_modelo([], [("O", "6", "X", "L", 5, 10.0, None)]), "O")["custo_a_revisar"])


def test_sem_custo_nenhum_vai_para_revisar_com_motivo_proprio():
    s = _sku(_modelo([], [("D", "4", "X", "L", 5, None, 10.0)]), "D")
    assert bool(s["custo_a_revisar"]) and s["custo_origem"] == "sem" and pd.isna(s["custo"])
    assert s["custo_motivo"] == em.MOTIVO_SEM_CUSTO


def test_compra_mais_recente_fora_do_padrao_e_ignorada_e_marca_revisar():
    compras = [("E", "5", "2026-07-10", 10.0, 0, 1, "L", "D"), ("E", "5", "2026-08-10", 10.0, 0, 1, "L", "D"),
               ("E", "5", "2026-09-10", 100.0, 0, 1, "L", "D")]                # a última tem fração errada (10×)
    s = _sku(_modelo([], [("E", "5", "X", "L", 5, 10.0, 25.0)], compras), "E")
    assert s["custo"] == 10.0 and bool(s["custo_a_revisar"]) and s["custo_motivo"] == calculo.MOTIVO_COMPRA
    assert s["ultima_compra"] == "2026-09-10"                                  # "compra recente" olha a última de verdade


def test_bonificacao_mais_nova_nao_vira_o_custo():
    compras = [("F", "6", "2026-08-10", 8.0, 0, 1, "L", "D"), ("F", "6", "2026-09-10", 0.01, 0, 1, "L", "D")]
    s = _sku(_modelo([], [("F", "6", "X", "L", 5, 9.0, 20.0)], compras), "F")
    assert s["custo"] == 8.0 and not bool(s["custo_a_revisar"]) and s["ultima_compra"] == "2026-09-10"


def test_cadastro_e_nota_divergentes_o_preco_de_venda_decide():
    # cadastro 50 (acima do preço de venda 20: errado) × nota 10 (coerente) → vale a nota
    s = _sku(_modelo([], [("G", "7", "X", "L", 5, 50.0, 20.0)], [("G", "7", "2026-08-10", 10.0, 0, 1, "L", "D")]), "G")
    assert (s["custo"], s["custo_origem"], bool(s["custo_a_revisar"])) == (10.0, "compra", False)
    # cadastro 10 (coerente) × nota 50 (acima do preço de venda) → vale o cadastro
    s = _sku(_modelo([], [("H", "8", "X", "L", 5, 10.0, 20.0)], [("H", "8", "2026-08-10", 50.0, 0, 1, "L", "D")]), "H")
    assert (s["custo"], s["custo_origem"]) == (10.0, "cadastro")


def test_fracao_e_desconto_entram_no_preco_da_compra():
    # caixa com 10 por R$ 50,00 e 10% de desconto → R$ 4,50 por unidade
    s = _sku(_modelo([], [("I", "9", "X", "L", 5, 4.0, 10.0)], [("I", "9", "2026-08-10", 50.0, 10, 10, "L", "D")]), "I")
    assert s["custo"] == pytest.approx(4.5) and s["custo_origem"] == "compra"


def test_custo_so_considera_compras_do_proprio_codigo():
    """O Assistente agrupa genéricos; o estoque não: a compra de outro código não vale."""
    compras = [("OUTRO", "9", "2026-08-10", 3.0, 0, 1, "L", "D")]
    s = _sku(_modelo([], [("J", "9", "X", "L", 5, 10.0, 25.0)], compras), "J")
    assert s["custo"] == 10.0 and s["custo_origem"] == "cadastro"


# ---------------------------------------------------------------------------
# Spaces: gravar, reaproveitar pela assinatura, montar na hora
# ---------------------------------------------------------------------------

def _gravacoes(armaz, monkeypatch):
    feitas = []
    original = armaz.salvar
    monkeypatch.setattr(armaz, "salvar", lambda chave, conteudo: (feitas.append(chave), original(chave, conteudo))[1])
    return feitas


def test_atualizar_grava_tres_arquivos_e_depois_so_refaz_se_a_assinatura_mudou(tmp_path, monkeypatch):
    armaz = ArmazenamentoLocal(tmp_path)
    feitas = _gravacoes(armaz, monkeypatch)
    p = _pronto([("A", "2026-09-10", 3, 30.0)], [("A", "1", "X", "L", 5, 4.0, 10.0)])
    p.meta["assinatura"] = "p1"
    assert em.atualizar(armaz, CH, p, "E", "L") is True
    assert [k.rsplit("/", 1)[1] for k in feitas] == ["skus.parquet", "vendas_dia.parquet", "meta.json"]   # o meta vai por último
    assert all(k.startswith("pedido/estoque/E/L/") for k in feitas)
    assert em.atualizar(armaz, CH, p, "E", "L") is False and len(feitas) == 3                           # em dia: nada gravado
    p.meta["assinatura"] = "p2"                                                                          # dado novo no pronto
    assert em.atualizar(armaz, CH, p, "E", "L") is True and len(feitas) == 6
    m = em.ler(armaz, CH, "E", "L")
    assert m.meta["pronto"] == "p2" and len(m.skus) == 1 and len(m.vendas_dia) == 1


def test_mudar_a_versao_ou_os_parametros_do_custo_refaz(tmp_path, monkeypatch):
    armaz = ArmazenamentoLocal(tmp_path)
    p = _pronto([], [("A", "1", "X", "L", 5, 4.0, 10.0)])
    p.meta["assinatura"] = "p1"
    assert em.atualizar(armaz, CH, p, "E", "L") is True
    import dataclasses
    outros = dataclasses.replace(PARAMS, fator_preco_fora=5.0)
    assert em.atualizar(armaz, CH, p, "E", "L", outros) is True                  # parâmetro do custo mudou
    assert em.atualizar(armaz, CH, p, "E", "L", outros) is False
    monkeypatch.setattr(em, "VERSAO", em.VERSAO + 1)                              # a lógica mudou
    assert em.atualizar(armaz, CH, p, "E", "L", outros) is True


def test_ler_sem_modelo_e_none(tmp_path):
    assert em.ler(ArmazenamentoLocal(tmp_path), CH, "E", "L") is None


def test_obter_monta_na_hora_e_depois_nao_toca_no_pronto(tmp_path, monkeypatch):
    armaz = ArmazenamentoLocal(tmp_path)
    _gravar_brutos(armaz)
    m = em.obter(armaz, CH, "E", "L", 3)
    assert m is not None and m.skus["codigo_produto"].tolist() == ["1"] and m.skus.iloc[0]["estoque"] == 3
    assert armaz.existe(CH.estoque_modelo("E", "L", "meta.json"))
    # Segunda abertura: tudo em dia — só lê o modelo (não relê o pronto nem os brutos).
    monkeypatch.setattr(pronto, "atualizar", lambda *a, **k: pytest.fail("não deveria remontar o pronto"))
    lidos = []
    original = armaz.ler_df
    monkeypatch.setattr(armaz, "ler_df", lambda c, *a, **k: (lidos.append(c), original(c, *a, **k))[1])
    m2 = em.obter(armaz, CH, "E", "L", 3)
    assert lidos and all("/estoque/E/L/" in c for c in lidos) and len(lidos) == 2
    assert m2.meta["assinatura"] == m.meta["assinatura"]


def test_obter_refaz_quando_chega_dado_novo(tmp_path):
    import datetime as dt
    armaz = ArmazenamentoLocal(tmp_path)
    _gravar_brutos(armaz, dias_faltando={dt.date(2026, 9, 5)})
    antes = em.obter(armaz, CH, "E", "L", 3)
    assert antes.meta["dias_sem_vendas"] == ["2026-09-05"]
    armaz.salvar_df(CH.dia("E", "L", "vendas", dt.date(2026, 9, 5)), transformar.vendas([]))   # chegou o dia que faltava
    depois = em.obter(armaz, CH, "E", "L", 3)
    assert depois.meta["dias_sem_vendas"] == [] and depois.meta["assinatura"] != antes.meta["assinatura"]


def test_obter_sem_foto_de_estoque_e_none(tmp_path):
    assert em.obter(ArmazenamentoLocal(tmp_path), CH, "E", "L", 3) is None
