"""Análise de Oportunidade pelas compras da API (core/analise_api.py) —
regras de 05/10/2026, sem banco e sem Spaces."""
import pandas as pd
import pytest

from core import analise_api as aa
from pedido import rede

MESES = ["2026-08"]


def _compra(loja, ean, nf, preco, qtd, data="2026-08-03", cad=None, venda=None, lab="LAB X", nome=None):
    return {"empresa": "E1", "codigo_loja": loja, "data": data, "nro_nf": nf, "fornecedor": "DIST",
            "laboratorio": lab, "codigo_produto": ean, "ean": ean, "nome": nome or f"PRODUTO {ean}",
            "quantidade": qtd, "preco": preco, "vlr_unitario": preco, "vlr_desconto": 0.0, "fracao": 1.0,
            "custo_cadastro": cad, "preco_venda": venda}


def _cenario(compras):
    vinculos = pd.DataFrame([("E1", "1", 1), ("E1", "2", 2)], columns=["empresa", "codigo_loja", "loja_id"])
    lojas = pd.DataFrame([
        {"loja_id": 1, "cnpj": "111", "razao_social": "LOJA MG", "uf": "MG", "cidade": "BH", "atendente_comercial": None,
         "consultor_farma": None, "consultor_interno": None, "grupo_economico": None},
        {"loja_id": 2, "cnpj": "222", "razao_social": "LOJA SP", "uf": "SP", "cidade": "SP", "atendente_comercial": None,
         "consultor_farma": None, "consultor_interno": None, "grupo_economico": None},
    ])
    genericos = pd.DataFrame([("111", 5, "LOSARTANA 50MG 30CP"), ("222", 5, "LOSARTANA 50MG 30CP")],
                             columns=["ean", "base_generico_id", "nome_canonico"])
    # Gruppy só cobre MG: a Losartana (EAN 111, genérico) e três não genéricos.
    itens = pd.DataFrame([("MG", "0111", 10.0), ("MG", "999", 20.0), ("MG", "888", 5.0), ("MG", "777", 4.0)],
                         columns=["uf", "ean", "preco"])
    precos = aa.precos_por_chave(itens, genericos)
    categorias = pd.DataFrame([("222", "PRESCRIÇÃO GENÉRICO")], columns=["ean", "categoria"])
    df = pd.DataFrame(compras, columns=rede.COLUNAS)
    return aa.calcular(df, vinculos, lojas, precos, genericos, categorias, MESES)


def _linha(df, produto, nf):
    return df[(df["produto_id"] == produto) & (df["nro_nf"] == nf)].iloc[0]


def test_economia_soma_cada_compra_acima_da_tabela_por_nota():
    linhas, bonif = _cenario([
        _compra("1", "222", "NF1", 12.0, 10, lab="EMS"),      # genérico de outro EAN: compara com a Losartana
        _compra("1", "222", "NF1", 13.0, 5, lab="EMS"),       # mesma nota: soma (média ponderada)
        _compra("1", "111", "NF2", 9.0, 4),                   # abaixo da tabela: aparece, não desconta
        _compra("1", "999", "NF3", 25.0, 2),                  # não genérico: EAN a EAN
        _compra("1", "999", "NF4", 0.05, 100),                # bonificação: fora, só contada
        _compra("2", "111", "NF5", 50.0, 1),                  # loja de UF sem tabela: fora
        _compra("1", "333", "NF6", 50.0, 1),                  # produto fora da tabela: fora
        _compra("1", "999", "NF7", 30.0, 1, data="2026-07-10"),  # fora do período
    ])
    assert len(linhas) == 3 and set(linhas["nro_nf"]) == {"NF1", "NF2", "NF3"}
    nf1 = _linha(linhas, "G5", "NF1")
    assert nf1["quantidade"] == 15 and nf1["preco_pago"] == pytest.approx(185 / 15)
    assert nf1["economia"] == pytest.approx(20 + 15) and nf1["nome_canonico"] == "LOSARTANA 50MG 30CP"
    assert nf1["categoria"] == "PRESCRIÇÃO GENÉRICO" and nf1["laboratorio"] == "EMS"
    nf2 = _linha(linhas, "G5", "NF2")
    assert nf2["diferenca"] == pytest.approx(-1) and nf2["economia"] == 0
    assert _linha(linhas, "E999", "NF3")["economia"] == pytest.approx(10)
    assert bonif.to_dict() == {1: 1}

    lojas = aa.por_loja(linhas)
    assert lojas.loc[0, "economia"] == pytest.approx(45) and lojas.loc[0, "qtd_produtos"] == 2
    i = aa.indicadores(linhas)
    assert (i.produtos_com_oportunidade, i.produtos_analisados, i.lojas_com_oportunidade) == (2, 2, 1)


def test_mesmo_generico_de_fabricantes_diferentes_na_nota_vira_uma_linha_por_fabricante():
    """29/10/2026, Fexofenadina da nota 343497: itens a 6,11, 7,84 e 5,89 contra 6,73 viravam UMA linha com o preço
    médio (6,61, diferença negativa) e economia R$ 6,66 — a conta da tela não fechava."""
    linhas, _ = _cenario([
        _compra("1", "111", "NF1", 9.0, 6, lab="MEDLEY"),     # abaixo da tabela (10)
        _compra("1", "222", "NF1", 12.0, 6, lab="EMS"),       # acima: a economia é toda daqui
        _compra("1", "222", "NF2", 11.0, 2, lab="EMS"),
    ])
    nf1 = linhas[linhas["nro_nf"] == "NF1"].set_index("laboratorio")
    assert len(nf1) == 2 and nf1.loc["EMS", "economia"] == pytest.approx(12)
    assert nf1.loc["MEDLEY", "economia"] == 0 and nf1.loc["MEDLEY", "diferenca"] == pytest.approx(-1)
    for _, r in linhas.iterrows():                         # Diferença × Qtd. = Economia em toda linha acima
        if r["economia"] > 0:
            assert r["diferenca"] * r["quantidade"] == pytest.approx(r["economia"])
    p = aa.por_produto(linhas).set_index("produto_id").loc["G5"]
    assert (p["notas"], p["notas_acima"]) == (2, 2)          # a NF1 conta uma vez, não duas
    assert p["economia"] == pytest.approx(14) and p["quantidade"] == 8


def test_regras_de_preco_na_ordem_cadastro_nota_venda_mediana():
    linhas, _ = _cenario([
        # 1. Nota pela caixa (R$ 50) e cadastro coerente com a venda (R$ 5,50 de R$ 9): vale o cadastro.
        _compra("1", "888", "NF1", 50.0, 2, cad=5.5, venda=9.0),
        # 1. Divergem 2,7× e os dois são coerentes com a venda (caso MILLENIUM): a revisar.
        _compra("1", "777", "NF2", 8.0, 3, cad=3.0, venda=10.0),
        # 2. Acima do preço de venda e cadastro também fora: a revisar.
        _compra("1", "999", "NF3", 40.0, 1, cad=38.0, venda=30.0),
    ])
    nf1 = _linha(linhas, "E888", "NF1")
    assert nf1["ajustado"] and not nf1["a_revisar"] and nf1["preco_pago"] == pytest.approx(5.5)
    assert nf1["preco_nota"] == pytest.approx(50) and nf1["economia"] == pytest.approx(0.5 * 2)
    nf2 = _linha(linhas, "E777", "NF2")
    assert nf2["a_revisar"] and nf2["revisar_motivo"] == aa.MOTIVO_DIVERGENCIA and nf2["economia"] == 0
    nf3 = _linha(linhas, "E999", "NF3")
    assert nf3["a_revisar"] and nf3["revisar_motivo"] == aa.MOTIVO_ACIMA_VENDA and nf3["economia"] == 0


def test_mediana_das_compras_da_loja():
    compras = [_compra("1", "999", f"NF{i}", 21.0, 1, data=f"2026-08-0{i}") for i in range(1, 5)]
    compras.append(_compra("1", "999", "NF9", 210.0, 1, data="2026-08-09"))   # caixa de 10 lançada como unidade
    linhas, _ = _cenario(compras)
    fora = _linha(linhas, "E999", "NF9")
    assert fora["a_revisar"] and fora["revisar_motivo"] == aa.MOTIVO_MEDIANA and fora["economia"] == 0
    assert linhas["economia"].sum() == pytest.approx(4)


def test_eans_de_interesse_inclui_os_irmaos_genericos():
    genericos = pd.DataFrame([("111", 5, "X"), ("222", 5, "X"), ("444", 6, "Y")],
                             columns=["ean", "base_generico_id", "nome_canonico"])
    itens = pd.DataFrame([("MG", "111", 1.0), ("MG", "999", 1.0)], columns=["uf", "ean", "preco"])
    assert aa.eans_de_interesse(itens, genericos) == ["111", "222", "999"]


def test_meses_do_periodo():
    assert aa.meses_do_periodo(["2026-06", "2026-07", "2026-08"], 2) == ["2026-07", "2026-08"]
    assert aa.meses_do_periodo([], 3) == []


def test_por_produto_uma_linha_por_loja_e_produto_e_a_conta_fecha():
    """Q7–Q10 de 05/10/2026: média só das notas acima da tabela; sem nota
    acima, média de todas e economia zero; "a revisar" fora da conta."""
    linhas, _ = _cenario([
        _compra("1", "222", "NF1", 12.0, 10, lab="EMS"),           # acima
        _compra("1", "111", "NF2", 9.0, 30, lab="MEDLEY"),         # abaixo: fora da média
        _compra("1", "999", "NF3", 18.0, 2),                       # abaixo, sem nenhuma acima
        _compra("1", "999", "NF4", 19.0, 2),
        _compra("1", "777", "NF5", 8.0, 3, cad=3.0, venda=10.0),   # só "a revisar"
        _compra("1", "888", "NF6", 6.0, 1),                        # acima
        _compra("1", "888", "NF7", 60.0, 1, cad=6.0, venda=9.0),   # nota pela caixa: vale o cadastro (6), acima
        _compra("1", "888", "NF8", 7.0, 2, cad=2.0, venda=9.0),    # a revisar (os dois coerentes)
    ])
    p = aa.por_produto(linhas).set_index("produto_id")
    g = p.loc["G5"]
    assert g["preco_pago"] == pytest.approx(12) and g["quantidade"] == 10 and g["economia"] == pytest.approx(20)
    assert g["diferenca"] * g["quantidade"] == pytest.approx(g["economia"])
    assert (g["notas"], g["notas_acima"], g["laboratorio"]) == (2, 1, "EMS")
    e = p.loc["E999"]
    assert e["preco_pago"] == pytest.approx(18.5) and e["economia"] == 0 and e["diferenca"] < 0
    r = p.loc["E777"]
    assert r["so_revisar"] and r["economia"] == 0 and r["notas_revisar"] == 1
    m = p.loc["E888"]
    assert (m["notas"], m["notas_acima"], m["notas_revisar"]) == (3, 2, 1) and not m["so_revisar"]
    assert m["preco_pago"] == pytest.approx(6) and m["economia"] == pytest.approx(2)


def test_historico_mensal_sem_a_revisar():
    linhas, _ = _cenario([_compra("1", "999", "NF1", 25.0, 2, data="2026-08-03"),
                          _compra("1", "999", "NF2", 21.0, 2, data="2026-08-20"),
                          _compra("1", "777", "NF3", 8.0, 3, cad=3.0, venda=10.0)])
    h = aa.historico_mensal(linhas)
    assert h["ano_mes"].tolist() == ["2026-08"] and h["preco"].iloc[0] == pytest.approx(23)
