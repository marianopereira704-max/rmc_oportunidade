"""Tela Pedido: o "pronto" da loja (pedido/pronto.py) e as regras da
sugestão (pedido/calculo.py)."""
import datetime as dt

import pandas as pd
import pytest

from pedido import calculo, pronto, transformar
from pedido import categorias as cat
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import COMPRAS, VENDAS, Chaves
from pedido.pronto import Pronto

CH = Chaves("pedido")
FOTO = dt.date(2026, 9, 27)          # janela: 01/06 a 26/09 (118 dias)
DIAS = 118


# ---------------------------------------------------------------------------
# Pronto
# ---------------------------------------------------------------------------

def _gravar_brutos(armaz, dias_faltando=()):
    for mes in ("2026-06", "2026-07", "2026-08"):
        armaz.salvar_df(CH.mes("E", "L", VENDAS, mes), transformar.vendas(
            [{"DataVenda": f"{mes}-10", "CodigoLoja": "L", "CodigoProduto": "1", "Quantidade": 2, "VlrLiquido": 10}]))
        armaz.salvar_df(CH.mes("E", "L", COMPRAS, mes), transformar.compras([]))
    dia = dt.date(2026, 9, 1)
    while dia <= dt.date(2026, 9, 26):
        if dia not in dias_faltando:
            armaz.salvar_df(CH.dia("E", "L", VENDAS, dia), transformar.vendas(
                [{"DataVenda": dia.isoformat(), "CodigoLoja": "L", "CodigoProduto": "1", "Quantidade": 1, "VlrLiquido": 5}]))
            armaz.salvar_df(CH.dia("E", "L", COMPRAS, dia), transformar.compras([]))
        dia += dt.timedelta(days=1)
    armaz.salvar_df(CH.estoque("E", "L"), transformar.estoque([
        {"CodigoLoja": "L", "CodigoProduto": "1", "ProdutoRelacionado_CodigoBarras": "789", "QtdEstoque": 3},
        {"CodigoLoja": "L", "CodigoProduto": "2", "QtdEstoque": 0},     # cadastro sem uso: sai do pronto
    ]))
    armaz.salvar_json(CH.marcador_estoque("E"), {"data": FOTO.isoformat()})


def test_pronto_sem_foto_de_estoque_e_none(tmp_path):
    assert pronto.atualizar(ArmazenamentoLocal(tmp_path), CH, "E", "L", 3) is None


def test_pronto_junta_os_brutos_e_depois_so_relê(tmp_path, monkeypatch):
    armaz = ArmazenamentoLocal(tmp_path)
    _gravar_brutos(armaz, dias_faltando={dt.date(2026, 9, 5)})
    p = pronto.atualizar(armaz, CH, "E", "L", 3)
    assert p.meta["inicio"] == "2026-06-01" and p.meta["fim"] == "2026-09-26"
    assert p.meta["dias_sem_vendas"] == ["2026-09-05"]
    assert p.vendas["unidades"].sum() == 3 * 2 + 25                  # 3 meses + 25 dias
    assert p.estoque["codigo_produto"].tolist() == ["1"]
    # Segunda abertura: nada mudou, então lê só o pronto (3 arquivos + meta).
    lidos = []
    original = armaz.ler_df
    monkeypatch.setattr(armaz, "ler_df", lambda c: (lidos.append(c), original(c))[1])
    p2 = pronto.atualizar(armaz, CH, "E", "L", 3)
    assert len(lidos) == 3 and all("/pronto/" in c for c in lidos)
    assert p2.meta["assinatura"] == p.meta["assinatura"]
    # Chegou o dia que faltava: monta de novo.
    armaz.salvar_df(CH.dia("E", "L", VENDAS, dt.date(2026, 9, 5)), transformar.vendas([]))
    p3 = pronto.atualizar(armaz, CH, "E", "L", 3)
    assert p3.meta["dias_sem_vendas"] == [] and p3.meta["assinatura"] != p.meta["assinatura"]


def test_mes_fechado_sem_arquivo_do_mes_usa_os_dias(tmp_path):
    """Virada de mês: setembro fechou, mas a rotina ainda não baixou o mês
    inteiro — os arquivos por dia valem."""
    armaz = ArmazenamentoLocal(tmp_path)
    _gravar_brutos(armaz)
    armaz.salvar_json(CH.marcador_estoque("E"), {"data": "2026-10-02"})   # janela: jul–set + 01/10
    p = pronto.atualizar(armaz, CH, "E", "L", 3)
    assert "2026-09-10" in set(p.vendas["data"])
    assert "2026-09-30" in p.meta["dias_sem_vendas"] and "2026-10-01" in p.meta["dias_sem_vendas"]


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------

def _pronto(vendas, estoque, compras=(), dias_sem_vendas=()):
    v = pd.DataFrame(vendas, columns=["codigo_produto", "data", "unidades", "valor"])
    e = transformar._quadro([dict(zip(["codigo_produto", "ean", "nome", "laboratorio", "qtd_estoque", "vlr_preco_compra",
                                       "vlr_preco_venda"], x))
                             for x in estoque], transformar.COLUNAS_ESTOQUE)
    c = transformar._quadro([dict(zip(["codigo_produto", "ean", "data", "vlr_unitario", "vlr_desconto", "fracao",
                                       "laboratorio", "fornecedor"], x)) for x in compras], transformar.COLUNAS_COMPRAS)
    return Pronto(v, c, e, {"inicio": "2026-06-01", "fim": "2026-09-26", "data_foto": "2026-09-27",
                            "dias_sem_vendas": list(dias_sem_vendas)})


BASE = pd.DataFrame([("100", "HIGIENE"), ("200", "MIP/OTC"), ("300", cat.EM_CLASSIFICACAO)], columns=["ean", "categoria"])
SEM_GENERICOS = pd.DataFrame(columns=["ean", "base_generico_id", "nome_canonico"])


def _linha(df, nome):
    return df[df["nome"] == nome].iloc[0]


def test_maximo_sugestao_e_dias_por_grupo():
    # 118 un. em 118 dias = 1/dia: medicamento 7 dias, perfumaria 15.
    p = _pronto([("1", "2026-09-20", 118, 100), ("2", "2026-09-20", 118, 100)],
                [("1", "200", "REMEDIO", "LAB", 4, 1), ("2", "100", "SABONETE", "LAB", 20, 1)])
    df = calculo.calcular(p, BASE, SEM_GENERICOS)
    r = _linha(df, "REMEDIO")
    assert r["demanda_dia"] == pytest.approx(1) and r["estoque_max"] == 7 and r["estoque_min"] == 3
    assert r["sugestao"] == 3 and r["listar"]                         # 7 − 4
    s = _linha(df, "SABONETE")
    assert s["estoque_max"] == 15 and not s["listar"] and s["sugestao"] == 0   # 20 ≥ 15: não aparece


def test_dias_sem_vendas_saem_da_conta():
    p = _pronto([("1", "2026-09-20", 100, 1)], [("1", "200", "REMEDIO", "LAB", 0, 1)],
                dias_sem_vendas=[f"2026-09-{d:02d}" for d in range(1, 19)])
    assert _linha(calculo.calcular(p, BASE, SEM_GENERICOS), "REMEDIO")["demanda_dia"] == pytest.approx(1)


def test_negativo_conta_como_zero_e_sem_classificacao_usa_dias_padrao():
    """29/09/2026: estoque negativo não trava mais (conta como 0, com a tag,
    e fica na lista de correção); Sem Classificação recebe sugestão com os
    dias padrão (7), também com a tag."""
    p = _pronto([("1", "2026-09-20", 118, 1), ("2", "2026-09-20", 118, 1), ("3", "2026-09-20", 118, 1)],
                [("1", "200", "NEG", "L", -2, 1), ("2", "300", "PENDENTE", "L", 0, 1), ("3", "999", "SEM", "L", 0, 1)])
    df = calculo.calcular(p, BASE, SEM_GENERICOS)
    neg = _linha(df, "NEG")
    assert neg["estoque"] == 0 and neg["estoque_gps"] == -2 and neg["sugestao"] == 7
    assert calculo.NEGATIVO in neg["status"] and calculo.RUPTURA in neg["status"]
    for nome in ("PENDENTE", "SEM"):                                  # "EM CLASSIFICAÇÃO" = sem categoria
        r = _linha(df, nome)
        assert r["sugestao"] == 7 and r["grupo"] == cat.SEM_CLASSIFICACAO and calculo.SEM_CLASSIFICACAO in r["status"]


def test_generico_com_um_ean_negativo_ignora_o_negativo():
    """Caso real da Hudson (Tinidazol): Medley −1, Eurofarma +1. O −1 conta
    como 0: estoque 1 = máximo 1, sem sugestão, com a tag pra conferir."""
    gen = pd.DataFrame([("111", 5, "TINIDAZOL"), ("222", 5, "TINIDAZOL")], columns=["ean", "base_generico_id", "nome_canonico"])
    p = _pronto([("2", "2026-09-20", 3, 1)], [("1", "111", "MEDLEY", "L", -1, 1), ("2", "222", "EUROFARMA", "L", 1, 1)])
    r = calculo.calcular(p, BASE, gen).iloc[0]
    assert r["estoque"] == 1 and r["estoque_gps"] == 0 and r["estoque_max"] == 1 and r["sugestao"] == 0
    assert calculo.NEGATIVO in r["status"] and "111 (-1)" in r["eans_negativos"]


def test_ruptura_e_ruptura_proxima():
    # 118 un. em 118 dias = 1/dia. ZERO: ruptura; DOIS: 2 ≤ 3 dias → próxima; DEZ: nenhuma.
    vendas = [(c, "2026-09-20", 118, 1) for c in ("1", "2", "3")]
    estoque = [("1", "200", "ZERO", "L", 0, 1), ("2", "200", "DOIS", "L", 2, 1), ("3", "100", "DEZ", "L", 10, 1)]
    df = calculo.calcular(_pronto(vendas, estoque), BASE, SEM_GENERICOS)
    assert _linha(df, "ZERO")["status"][:1] == [calculo.RUPTURA]
    assert calculo.RUPTURA_PROXIMA in _linha(df, "DOIS")["status"] and calculo.RUPTURA not in _linha(df, "DOIS")["status"]
    assert not {calculo.RUPTURA, calculo.RUPTURA_PROXIMA} & set(_linha(df, "DEZ")["status"])
    # Configurável: ruptura até 2 unidades.
    df2 = calculo.calcular(_pronto(vendas, estoque), BASE, SEM_GENERICOS, calculo.Parametros(ruptura_unidades=2))
    assert calculo.RUPTURA in _linha(df2, "DOIS")["status"]


def test_generico_unifica_eans_soma_venda_e_estoque():
    gen = pd.DataFrame([("111", 7, "DIPIRONA 500MG"), ("222", 7, "DIPIRONA 500MG")],
                       columns=["ean", "base_generico_id", "nome_canonico"])
    p = _pronto([("1", "2026-09-20", 59, 1), ("2", "2026-09-21", 59, 1)],
                [("1", "111", "DIPI MEDLEY", "MEDLEY", 2, 1), ("2", "0222", "DIPI EMS", "EMS", 1, 1),
                 ("3", "222", "DIPI EMS CX", "EMS", 1, 1)])           # mesmo EAN-chave, outro código, sem venda
    df = calculo.calcular(p, BASE, gen)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["nome"] == "DIPIRONA 500MG" and r["categoria"] == cat.GENERICO and r["generico"]
    assert r["eans"] == ["111", "222"] and r["estoque"] == 4 and r["unidades"] == 118


def test_compra_de_referencia_e_a_ultima_paga_e_coerente():
    """No Pedido o preço é a ÚLTIMA compra não bonificada (29/09/2026), e
    "a revisar" é medido contra as compras da própria loja (> 3× ou < 1/3 da
    mediana), não contra o custo de cadastro."""
    vendas = [(c, "2026-09-20", 118, 1) for c in ("1", "2", "3", "4")]
    estoque = [(c, f"20{c}", f"P{c}", "L", 0, 10) for c in ("1", "2", "3", "4")]
    base = pd.DataFrame([(f"20{c}", "MIP/OTC") for c in ("1", "2", "3", "4")], columns=["ean", "categoria"])
    compras = [
        ("1", "201", "2026-07-01", 120, 0, 12, "A", "F"),      # 10,00
        ("1", "201", "2026-08-01", 100, 10, 10, "B", "F"),     # 9,00
        ("1", "201", "2026-09-01", 11, 0, 1, "C", "F"),        # 11,00 — a mais recente → vale
        ("1", "201", "2026-09-10", 0.01, 0, 1, "D", "F"),      # bonificação depois: não conta, mas avisa
        ("2", "202", "2026-07-01", 9, 0, 1, "A", "F"),
        ("2", "202", "2026-08-01", 10, 0, 1, "B", "F"),
        ("2", "202", "2026-09-01", 990, 0, 1, "C", "F"),       # 990 = caixa lançada sem fração: fora do padrão
        ("3", "203", "2026-08-01", 0.01, 0, 1, "X", "F"),      # só bonificação
    ]
    df = calculo.calcular(_pronto(vendas, estoque, compras), base, SEM_GENERICOS)
    r1 = _linha(df, "P1")
    assert r1["preco"] == pytest.approx(11) and r1["laboratorio"] == "C" and r1["compra_bonificado"]
    assert calculo.BONIFICADO in r1["tags_preco"]
    r2 = _linha(df, "P2")
    assert r2["preco"] == pytest.approx(10) and r2["compra_a_revisar"] and r2["compra_ignorada_preco"] == pytest.approx(990)
    assert calculo.A_REVISAR in r2["tags_preco"]
    r3 = _linha(df, "P3")
    assert r3["preco_origem"] == "cadastro" and r3["preco"] == 10
    assert {calculo.BONIFICADO, calculo.CUSTO_CADASTRO} <= set(r3["tags_preco"])
    r4 = _linha(df, "P4")
    assert r4["preco_origem"] == "cadastro" and calculo.CUSTO_CADASTRO in r4["tags_preco"]


def test_compra_unica_nunca_e_a_revisar():
    compras = [("1", "201", "2026-08-01", 50, 0, 1, "A", "F")]        # 50 contra cadastro 10: não importa mais
    df = calculo.calcular(_pronto([("1", "2026-09-20", 118, 1)], [("1", "201", "P1", "L", 0, 10)], compras),
                          pd.DataFrame([("201", "MIP/OTC")], columns=["ean", "categoria"]), SEM_GENERICOS)
    assert df.iloc[0]["preco"] == 50 and not df.iloc[0]["compra_a_revisar"]
    # Mas cadastro × nota divergem 5× e, sem preço de venda, não dá pra saber
    # quem está certo: "a revisar" pela divergência (01/10/2026).
    assert df.iloc[0]["a_revisar"] and df.iloc[0]["revisar_motivo"] == calculo.MOTIVO_DIVERGENCIA


def test_cadastro_x_nota_o_preco_de_venda_decide():
    """Regra de 01/10/2026, com os casos reais da Hudson: custo coerente =
    entre 15% e 100% do preço de venda."""
    produtos = {
        # código: (cadastro, venda, preço da nota)
        "1": (2.65, 4.00, 79.49),    # Maskaclets: nota é o display de 30 sem fração → usa o cadastro
        "2": (79.90, 2.00, 0.799),   # Seringa: cadastro é a caixa de 100 → a nota está certa
        "3": (9.23, 35.41, 28.02),   # Labirin: os dois coerentes → a revisar, fica a nota
        "4": (10.00, 20.00, 11.00),  # divergência pequena → nada muda
        "5": (50.00, 20.00, None),   # sem compra e cadastro acima do preço de venda → a revisar
        "6": (8.00, 20.00, None),    # sem compra, cadastro coerente → só "custo de cadastro"
    }
    vendas = [(c, "2026-09-20", 118, 1) for c in produtos]
    estoque = [(c, f"20{c}", f"P{c}", "L", 0, cad, venda) for c, (cad, venda, _) in produtos.items()]
    compras = [(c, f"20{c}", "2026-09-01", nota, 0, 1, "LAB", "F") for c, (_, _, nota) in produtos.items() if nota]
    base = pd.DataFrame([(f"20{c}", "MIP/OTC") for c in produtos], columns=["ean", "categoria"])
    df = calculo.calcular(_pronto(vendas, estoque, compras), base, SEM_GENERICOS)

    r = _linha(df, "P1")
    assert r["preco"] == pytest.approx(2.65) and r["preco_nota"] == pytest.approx(79.49)
    assert r["custo_ajustado"] == "cadastro" and not r["a_revisar"] and calculo.CUSTO_AJUSTADO in r["tags_preco"]
    assert r["orcamento"] == pytest.approx(r["sugestao"] * 2.65)              # o orçamento já sai certo
    r = _linha(df, "P2")
    assert r["preco"] == pytest.approx(0.799) and r["custo_ajustado"] == "nota" and not r["a_revisar"]
    r = _linha(df, "P3")
    assert r["preco"] == pytest.approx(28.02) and r["custo_ajustado"] is None
    assert r["a_revisar"] and r["revisar_motivo"] == calculo.MOTIVO_DIVERGENCIA and calculo.A_REVISAR in r["tags_preco"]
    r = _linha(df, "P4")
    assert r["preco"] == pytest.approx(11) and r["custo_ajustado"] is None and not r["a_revisar"]
    r = _linha(df, "P5")
    assert r["preco_origem"] == "cadastro" and r["a_revisar"] and r["revisar_motivo"] == calculo.MOTIVO_CADASTRO
    r = _linha(df, "P6")
    assert not r["a_revisar"] and r["tags_preco"] == [calculo.CUSTO_CADASTRO]

    # A faixa e o limite da divergência são parâmetros.
    largo = calculo.Parametros(custo_faixa_min=0.01, custo_faixa_max=50)
    r = _linha(calculo.calcular(_pronto(vendas, estoque, compras), base, SEM_GENERICOS, largo), "P1")
    assert r["a_revisar"] and r["custo_ajustado"] is None                    # os dois "coerentes": não decide
    tolerante = calculo.Parametros(fator_cadastro_nota=100)
    r = _linha(calculo.calcular(_pronto(vendas, estoque, compras), base, SEM_GENERICOS, tolerante), "P1")
    assert r["preco"] == pytest.approx(79.49) and not r["a_revisar"]          # 30× não passa de 100×


def test_venda_90_dias_e_fixa_e_so_exibicao():
    vendas = [("1", "2026-06-05", 50, 1), ("1", "2026-07-10", 5, 1), ("1", "2026-09-20", 10, 1)]
    df = calculo.calcular(_pronto(vendas, [("1", "201", "P1", "L", 0, 10)]),
                          pd.DataFrame([("201", "MIP/OTC")], columns=["ean", "categoria"]), SEM_GENERICOS,
                          calculo.Parametros(giro_baixo_dias=30))
    r = df.iloc[0]
    assert r["venda_90d"] == 15                          # 29/06 em diante: julho + setembro, não junho
    assert r["unidades_90d"] == 10                       # o giro baixo segue os dias dele (30)
    assert r["demanda_dia"] == pytest.approx(65 / DIAS)  # a demanda continua na janela inteira


def test_giro_baixo_ruptura_e_aviso_de_embalagem():
    # LENTO: 1 unidade em junho e nada nos últimos 90 dias.
    vendas = [("1", "2026-06-02", 5, 1), ("2", "2026-09-20", 1, 1), ("3", "2026-09-20", 118, 1)]
    estoque = [("1", "201", "LENTO", "L", 0, 1), ("2", "201", "UMA", "L", 0, 1), ("3", "201", "RAPIDO", "L", 4, 1)]
    compras = [("3", "201", "2026-09-01", 12, 0, 12, "L", "F")]
    df = calculo.calcular(_pronto(vendas, estoque, compras), pd.DataFrame([("201", "MIP/OTC")], columns=["ean", "categoria"]),
                          SEM_GENERICOS)
    assert _linha(df, "LENTO")["giro_baixo"] and _linha(df, "UMA")["giro_baixo"]      # ≤ 1 un. em 90 dias
    rapido = _linha(df, "RAPIDO")
    assert not rapido["giro_baixo"] and not rapido["ruptura"]         # 4 un. = 4 dias de venda > 3
    assert rapido["sugestao"] == 3 and rapido["aviso_embalagem"]       # 3 não fecha a embalagem de 12
    assert len(calculo.filtrar(df)) == 1 and len(calculo.filtrar(df, ocultar_giro_baixo=False)) == 3


def test_curva_abc_em_duas_letras():
    # Valor: A=60, B=30, C=10 → A, B, C. Unidades ao contrário.
    vendas = [("1", "2026-09-20", 10, 60), ("2", "2026-09-20", 30, 30), ("3", "2026-09-20", 60, 10)]
    estoque = [(c, "201", f"P{c}", "L", 0, 1) for c in ("1", "2", "3")]
    df = calculo.calcular(_pronto(vendas, estoque), pd.DataFrame([("201", "MIP/OTC")], columns=["ean", "categoria"]),
                          SEM_GENERICOS)
    assert [_linha(df, f"P{c}")["curva"] for c in ("1", "2", "3")] == ["AC", "BB", "CA"]


def test_filtrar_por_busca_ean_e_categoria():
    p = _pronto([("1", "2026-09-20", 118, 1), ("2", "2026-09-20", 118, 1)],
                [("1", "200", "REMEDIO", "NEO", 0, 1), ("2", "100", "SABONETE", "LAB", 0, 1)])
    df = calculo.calcular(p, BASE, SEM_GENERICOS)
    assert calculo.filtrar(df, busca="20")["nome"].tolist() == ["REMEDIO"]      # parte do EAN
    assert calculo.filtrar(df, busca="neo")["nome"].tolist() == ["REMEDIO"]     # laboratório
    assert calculo.filtrar(df, categoria="HIGIENE")["nome"].tolist() == ["SABONETE"]
    i = calculo.indicadores(calculo.filtrar(df))
    assert i.itens == 2 and i.unidades == 7 + 15 and i.ruptura == 2
