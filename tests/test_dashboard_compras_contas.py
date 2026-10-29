"""Contas da página Compras do Dashboard (core/dashboard_compras.py), com casos montados à mão — os números são os
das rodadas de perguntas de 27/10/2026 (exemplo da dotação do Mariano e a Dipirona do protótipo)."""
import datetime as dt

import pandas as pd
import pytest

from core import dashboard_compras as dc

D = dt.date
REGRAS = dc.Regras(fora_pct=0.40, fora_semanas=8, sem_demanda_dias=90, limite_bonificacao=0.10)


def _compras(linhas):
    """(data, código, quantidade, preço un., [fração])"""
    return pd.DataFrame([{"data": d, "codigo_produto": c, "quantidade": q, "fracao": (x[0] if x else 1),
                          "vlr_unitario": p * (x[0] if x else 1), "vlr_desconto": 0, "nro_nf": "1",
                          "fornecedor": "DISTRIBUIDORA"} for d, c, q, p, *x in linhas])


def _vendas(linhas):
    """(data, código, unidades, valor)"""
    return pd.DataFrame([{"data": d, "codigo_produto": c, "unidades": u, "valor": v} for d, c, u, v in linhas])


def _dias(inicio: dt.date, n: int):
    return [inicio + dt.timedelta(days=i) for i in range(n)]


def _quadro(compras, vendas, info, periodo, regras=REGRAS):
    mapa = {c: "P" + c for c in set(compras["codigo_produto"]) | set(vendas["codigo_produto"])}
    it = dc.itens(compras, mapa, regras.limite_bonificacao)
    ve = dc.vendas_por_linha(vendas, mapa)
    return dc.preparar(it, ve, info, periodo, regras), it, ve


def test_periodos_precisam_do_mes_anterior_para_a_dotacao():
    p = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))
    assert p[dc.ATUAL].meses == ((D(2026, 10, 1), D(2026, 10, 26)),)
    assert p[dc.PASSADO].meses == ((D(2026, 9, 1), D(2026, 9, 30)),)
    assert p[dc.TRES].inicio == D(2026, 8, 1) and p[dc.TRES].fim == D(2026, 10, 26)
    # Janela começando em agosto: os 3 meses (agosto a outubro) não têm julho, a base da dotação de agosto.
    assert dc.TRES not in dc.periodos(D(2026, 10, 26), D(2026, 8, 1))


def test_itens_fracao_e_bonificacao():
    c = _compras([(D(2026, 10, 1).isoformat(), "1", 2, 30.0, 10), (D(2026, 10, 1).isoformat(), "1", 5, 0.0)])
    it = dc.itens(c, {"1": "P1"}, 0.10)
    assert list(it["unidades"]) == [20, 5]
    assert it["preco"].iloc[0] == pytest.approx(30.0) and it["valor"].iloc[0] == pytest.approx(600.0)
    assert bool(it["bonificado"].iloc[1]) and it["valor"].iloc[1] == 0


def _exemplo_dotacao(ultimo: dt.date):
    """Setembro: perfumaria e genérico compraram R$ 50 mil cada. Outubro: a venda de perfumaria caiu 10% e a de
    genérico subiu 15%, contra as mesmas semanas de setembro."""
    compras = _compras([("2026-09-10", "A", 1000, 50.0), ("2026-09-10", "B", 1000, 50.0)])
    vendas = []
    for dia in _dias(D(2026, 9, 1), 30):
        vendas += [(dia.isoformat(), "A", 10, 1000.0), (dia.isoformat(), "B", 10, 1000.0)]
    for dia in _dias(D(2026, 10, 1), ultimo.day):
        vendas += [(dia.isoformat(), "A", 9, 900.0), (dia.isoformat(), "B", 11.5, 1150.0)]
    info = pd.DataFrame({"categoria": ["Perfumaria", "Genérico"], "custo": [40.0, 40.0]}, index=["PA", "PB"])
    periodo = dc.periodos(ultimo, D(2026, 7, 1))[dc.ATUAL]
    d, it, ve = _quadro(compras, _vendas(vendas), info, periodo)
    return d, it, ve, dc.Contexto(periodo, REGRAS)


def test_dotacao_acompanha_a_venda_de_cada_categoria():
    d, _, _, ctx = _exemplo_dotacao(D(2026, 10, 26))
    assert dc.dotacao(d, ctx) == pytest.approx(45000 + 57500)          # Q5 B: o total acompanha, 102,5 mil
    cats = dict(dc.por_categoria(d, ctx))
    assert cats["Perfumaria"]["dotacao"] == pytest.approx(45000)
    assert cats["Perfumaria"]["variacao"] == pytest.approx(-0.10)
    assert cats["Genérico"]["variacao"] == pytest.approx(0.15)


def test_dotacao_nao_ajusta_antes_de_fechar_a_primeira_semana():
    d, _, _, ctx = _exemplo_dotacao(D(2026, 10, 6))
    assert dc.dotacao(d, ctx) == pytest.approx(100000)
    assert dc.variacao(d, ctx) is None
    d, _, _, ctx = _exemplo_dotacao(D(2026, 10, 7))
    assert dc.dotacao(d, ctx) == pytest.approx(102500)


def _venda_da_loja(inicio: dt.date, fim: dt.date, por_dia=None):
    """Venda de uma linha só: R$ 1.000 em dia útil, R$ 500 no sábado, domingo fechado; `por_dia` troca dias."""
    linhas = []
    for dia in _dias(inicio, (fim - inicio).days + 1):
        valor = (por_dia or {}).get(dia, {5: 500.0, 6: 0.0}.get(dia.weekday(), 1000.0))
        if valor:
            linhas.append((dia.isoformat(), "A", 1, valor))
    return _vendas(linhas)


def test_dia_fora_da_conta_pela_mediana_do_mesmo_dia_da_semana():
    """Dia útil que veio quase vazio e feriado saem; domingo fechado fica (é o normal do domingo), mesmo com um
    domingo aberto nas semanas de referência — a média dos domingos tiraria todos (loja 60, 28/10/2026)."""
    especiais = {D(2026, 9, 20): 400.0,             # um domingo aberto
                 D(2026, 10, 6): 60.0,              # terça quase vazia
                 D(2026, 10, 12): 0.0}              # feriado (segunda sem venda)
    ve = dc.vendas_por_linha(_venda_da_loja(D(2026, 8, 1), D(2026, 10, 20), especiais), {"A": "PA"})
    fora = dc.dias_fora(ve, REGRAS)
    assert pd.Timestamp("2026-10-06") in fora and pd.Timestamp("2026-10-12") in fora
    assert pd.Timestamp("2026-10-04") not in fora and pd.Timestamp("2026-10-11") not in fora   # domingos fechados
    assert pd.Timestamp("2026-10-03") not in fora                                              # sábado normal
    assert pd.Timestamp("2026-08-04") not in fora        # sem 3 semanas de referência na janela, nunca sai


def test_dia_fora_nao_muda_a_variacao_da_semana():
    """A mesma venda em setembro e outubro, com uma terça quase vazia em outubro: a variação fica 0."""
    compras = _compras([("2026-09-10", "A", 100, 10.0)])
    vendas = _venda_da_loja(D(2026, 8, 1), D(2026, 10, 14), {D(2026, 10, 6): 60.0})
    periodo = dc.periodos(D(2026, 10, 14), D(2026, 8, 1))[dc.ATUAL]
    info = pd.DataFrame({"categoria": ["Genérico"], "custo": [5.0]}, index=["PA"])
    d, _, _ = _quadro(compras, vendas, info, periodo)
    ctx = dc.Contexto(periodo, REGRAS)
    # Sem tirar o dia (fora_pct 0), as 2 semanas cairiam ~8,5%. Tirando, sobra ~2%: a semana fica com um dia útil a
    # menos na média — a imprecisão que a regra aceita.
    sem_regra = dc.Regras(fora_pct=0.0, fora_semanas=8, sem_demanda_dias=90, limite_bonificacao=0.10)
    d0, _, _ = _quadro(compras, vendas, info, periodo, sem_regra)
    assert dc.variacao(d0, dc.Contexto(periodo, sem_regra)) == pytest.approx(-0.085, abs=0.005)
    assert abs(dc.variacao(d, ctx)) < 0.03
    assert dc.dotacao(d, ctx) == pytest.approx(1000, rel=0.03)


def test_ultima_semana_compara_a_venda_media_por_dia():
    """A 4ª semana tem 7 dias em fevereiro (22–28) e 10 em março (22–31): a mesma venda por dia dá variação 0."""
    compras = _compras([("2026-02-10", "A", 100, 10.0)])
    vendas = _vendas([(d.isoformat(), "A", 1, 100.0) for d in _dias(D(2026, 2, 1), 59)])
    periodo = dc.periodos(D(2026, 3, 31), D(2026, 1, 1))[dc.ATUAL]
    info = pd.DataFrame({"categoria": ["Genérico"], "custo": [5.0]}, index=["PA"])
    d, _, _ = _quadro(compras, vendas, info, periodo)
    assert dc.variacao(d, dc.Contexto(periodo, REGRAS)) == pytest.approx(0.0)


def test_curva_do_mes_termina_na_dotacao_do_periodo():
    d, it, ve, ctx = _exemplo_dotacao(D(2026, 10, 26))
    curva = dc.curva_do_mes(it, ve, d, D(2026, 10, 1), D(2026, 10, 26), REGRAS)
    assert len(curva) == 31
    assert curva["dotacao"].iloc[0] == pytest.approx(100000)          # dia 1: antes do ajuste
    assert curva["dotacao"].iloc[5] == pytest.approx(100000)          # dia 6: a 1ª semana ainda não fechou
    assert curva["dotacao"].iloc[6] == pytest.approx(102500)          # dia 7: fechou
    assert curva["dotacao"].iloc[25] == pytest.approx(dc.dotacao(d, ctx))
    assert curva["dotacao"].iloc[30] == pytest.approx(dc.dotacao(d, ctx))  # depois do último dia, fica a dele
    assert curva["consumido"].iloc[30] is None or pd.isna(curva["consumido"].iloc[30])


def test_excesso_conta_primeiro_as_bonificadas():
    """A Dipirona do protótipo: 250 pagas a R$ 3,20 + 50 bonificadas, vendeu 110, ideal 30 → limite 140, 160 acima
    do limite, 110 pagas = R$ 352 (Q17 B)."""
    compras = _compras([("2026-10-02", "1", 150, 3.20), ("2026-10-14", "1", 100, 3.20), ("2026-10-14", "1", 50, 0.0)])
    vendas = _vendas([(dia.isoformat(), "1", 10, 50.0) for dia in _dias(D(2026, 10, 1), 11)])
    info = pd.DataFrame({"categoria": ["Genérico"], "custo": [3.20], "ideal": [30.0], "estoque": [190.0],
                         "demanda_dia": [4.0]}, index=["P1"])
    periodo = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))[dc.ATUAL]
    d, _, _ = _quadro(compras, vendas, info, periodo)
    r = d.loc["P1"]
    assert r["classe"] == dc.EXCESSO
    assert (r["comprado_un"], r["bonif_un"], r["limite"], r["excesso_un"], r["excesso_pagas"]) == (300, 50, 140, 160, 110)
    assert r["excesso_rs"] == pytest.approx(352.0)
    assert r["certa"] == pytest.approx(800 - 352)
    texto = dc.diagnostico(r, REGRAS)
    assert "160 un. acima do necessário, 110 delas pagas (R$ 352,00)" in texto
    assert "(50 bonificadas)" in texto
    acao, complemento = dc.sugestoes(r)[0]
    assert acao == "Não comprar" and "cerca de 40 dias" in complemento    # (190 − 30) ÷ 4


def test_sem_demanda_olha_os_dias_antes_da_compra():
    compras = _compras([("2026-10-05", "C", 10, 5.0), ("2026-10-05", "D", 10, 5.0)])
    vendas = _vendas([("2026-08-01", "D", 1, 9.0)])          # D vendeu 65 dias antes da compra; C nunca vendeu
    info = pd.DataFrame({"categoria": ["Perfumaria", "Perfumaria"], "custo": [5.0, 5.0]}, index=["PC", "PD"])
    periodo = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))[dc.ATUAL]
    d, _, _ = _quadro(compras, vendas, info, periodo)
    assert d.loc["PC", "classe"] == dc.SEM_DEMANDA and d.loc["PC", "sem_demanda_rs"] == pytest.approx(50.0)
    assert d.loc["PD", "classe"] == dc.EXCESSO                 # vendeu antes, mas 10 > limite (0 vendidas + 0 ideal)
    assert "não vendeu nenhuma nos 90 dias" in dc.diagnostico(d.loc["PC"], REGRAS)
    assert list(dc.produtos(d)["nome"].index) == ["PC", "PD"]
    assert list(dc.produtos(d, classe=dc.SEM_DEMANDA).index) == ["PC"]


def test_compra_x_venda_e_eficiencia():
    compras = _compras([("2026-10-05", "1", 10, 10.0)])
    vendas = _vendas([("2026-10-06", "1", 8, 160.0), ("2026-10-06", "2", 4, 80.0)])
    info = pd.DataFrame({"categoria": ["Similar", "Similar"], "custo": [10.0, 12.5], "ideal": [5.0, 0.0]},
                        index=["P1", "P2"])
    periodo = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))[dc.ATUAL]
    d, _, _ = _quadro(compras, vendas, info, periodo)
    m = dc.metricas(d, dc.Contexto(periodo, REGRAS))
    assert m["venda_custo"] == pytest.approx(8 * 10 + 4 * 12.5)
    assert m["compra_venda"] == pytest.approx(100 / 130 * 100)
    assert m["eficiencia"] == pytest.approx(100.0)               # 10 ≤ 8 vendidas + 5 de ideal
    assert pd.isna(d.loc["P2", "classe"])                         # só vendeu: entra na venda, não nos produtos
    assert list(dc.produtos(d).index) == []


def test_tres_meses_soma_as_dotacoes_de_cada_mes():
    compras = _compras([("2026-07-10", "A", 100, 10.0), ("2026-08-10", "A", 200, 10.0), ("2026-09-10", "A", 300, 10.0),
                        ("2026-10-10", "A", 50, 10.0)])
    info = pd.DataFrame({"categoria": ["Genérico"], "custo": [10.0]}, index=["PA"])
    periodo = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))[dc.TRES]
    d, _, _ = _quadro(compras, _vendas([]).reindex(columns=["data", "codigo_produto", "unidades", "valor"]), info, periodo)
    ctx = dc.Contexto(periodo, REGRAS)
    assert dc.dotacao(d, ctx) == pytest.approx(1000 + 2000 + 3000)   # sem venda: fator 1, a base de cada mês
    meses = dc.por_mes(d, ctx)
    assert [m["dotacao"] for m in meses] == pytest.approx([1000, 2000, 3000])
    assert [m["consumido"] for m in meses] == pytest.approx([2000, 3000, 500])


def test_loja_sem_nenhuma_compra_baixada():
    """29/10/2026: sem nenhuma compra, a coluna `bonificado` vem vazia e sem tipo, e a página dava KeyError 'linha'
    (3 lojas do banco dev, inclusive ao abrir no app)."""
    vazias = _compras([("2026-10-05", "1", 1, 1.0)]).iloc[:0].astype(object)
    vendas = _vendas([("2026-10-06", "1", 8, 160.0)])
    info = pd.DataFrame({"categoria": ["Similar"], "custo": [10.0], "ideal": [5.0]}, index=["P1"])
    periodo = dc.periodos(D(2026, 10, 26), D(2026, 7, 1))[dc.ATUAL]
    d, it, _ = _quadro(vazias, vendas, info, periodo)
    assert it.empty and d.loc["P1", "comprado_un"] == 0
