"""Dashboard (21/10/2026): as contas de `core/dashboard.py` sobre um quadro do
cálculo do Assistente montado à mão — cada número dá para conferir de cabeça."""
import pandas as pd
import pytest

from core import dashboard as dash
from pedido import linhas as lc
from pedido.configuracao import ConfigPedido

CONFIG = ConfigPedido.padrao()   # ruptura 0 un., 3 dias de ruptura próxima, giro baixo ≤ 1 un., excesso = o que passa de 60 dias de venda


def _quadro(*linhas) -> pd.DataFrame:
    """(nome, estoque, demanda_dia, preco, preco_venda, estoque_max, unidades_90d, curva, linha, categoria, grupo)"""
    cols = ["nome", "estoque", "demanda_dia", "preco", "preco_venda", "estoque_max", "unidades_90d", "curva",
            "linha", "categoria", "grupo"]
    return pd.DataFrame(linhas, columns=cols)


def _preparar(df, config=CONFIG) -> pd.DataFrame:
    return dash.preparar(df, config, linha=df["linha"])


# Um produto de cada situação, com contas redondas.
RUPTURA = ("RUPTURA", 0, 2.0, 5.0, 10.0, 14, 180, "AA", lc.GENERICOS, "PRESCRIÇÃO GENÉRICO", "medicamento")
RUPTURA_POUCO_GIRO = ("RUPTURA GIRO BAIXO", 0, 0.01, 8.0, 20.0, 1, 1, "AB", lc.PROPAGADOS, "PRESCRIÇÃO PROPAGADO", "medicamento")
PROXIMA = ("PROXIMA", 4, 2.0, 5.0, 10.0, 14, 180, "BB", lc.GENERICOS, "PRESCRIÇÃO GENÉRICO", "medicamento")
EXCESSO = ("EXCESSO", 300, 2.0, 1.0, 3.0, 14, 180, "BC", lc.SIMILARES, "PRESCRIÇÃO TRADE", "medicamento")
FORA_DO_LIMITE = ("ACIMA DO IDEAL MAS COBERTURA 40 DIAS", 80, 2.0, 1.0, 3.0, 14, 180, "CC", lc.SIMILARES, "MIP/OTC", "medicamento")
PARADO = ("PARADO", 20, 0.01, 10.0, 25.0, 1, 1, "CC", lc.PERFUMARIA, "HIGIENE", "perfumaria")
EFICIENTE = ("EFICIENTE", 10, 1.0, 4.0, 9.0, 14, 90, "AA", lc.GENERICOS, "PRESCRIÇÃO GENÉRICO", "medicamento")


def test_cada_produto_cai_na_situacao_certa():
    d = _preparar(_quadro(RUPTURA, RUPTURA_POUCO_GIRO, PROXIMA, EXCESSO, FORA_DO_LIMITE, PARADO, EFICIENTE))
    assert dict(zip(d["nome"], d["situacao"])) == {
        "RUPTURA": "ruptura", "RUPTURA GIRO BAIXO": "ruptura", "PROXIMA": "ruptura_proxima", "EXCESSO": "excesso",
        "ACIMA DO IDEAL MAS COBERTURA 40 DIAS": "eficiente",   # 80 un. ÷ 2 por dia = 40 dias: abaixo do limite de 60 (o ideal do Assistente, 14, não conta)
        "PARADO": "parado", "EFICIENTE": "eficiente"}


def test_ruptura_e_completa_inclui_giro_baixo_e_venda_pontual():
    """O Assistente tira a "venda pontual" (giro baixo de curva A/B) da ruptura. No Dashboard, sem estoque é ruptura."""
    d = _preparar(_quadro(RUPTURA, RUPTURA_POUCO_GIRO, EFICIENTE))
    pontual = d[d["nome"] == "RUPTURA GIRO BAIXO"].iloc[0]
    assert pontual["em_ruptura"] and pontual["giro_baixo_real"]
    assert int(d["em_ruptura"].sum()) == 2 and int((d["em_ruptura"] & d["giro_baixo_real"]).sum()) == 1


def test_ruptura_respeita_o_limite_de_unidades_da_configuracao():
    d = _preparar(_quadro(PROXIMA), ConfigPedido.de_dict({"ruptura_unidades": 5}))
    assert bool(d["em_ruptura"].iloc[0]) and d["situacao"].iloc[0] == "ruptura"     # 4 un. ≤ 5


def test_valores_em_reais_somam_o_estoque_e_cada_real_esta_em_uma_classe_so():
    d = _preparar(_quadro(RUPTURA, PROXIMA, EXCESSO, FORA_DO_LIMITE, PARADO, EFICIENTE))
    por_nome = d.set_index("nome")
    # EXCESSO: 300 un. a R$ 1, vende 2/dia → 60 dias = 120 un. eficientes e 180 em excesso.
    assert por_nome.loc["EXCESSO", ["valor_excesso", "valor_eficiente", "valor_parado"]].tolist() == [180.0, 120.0, 0.0]
    # PARADO: 20 un. a R$ 10, tudo parado, nada em excesso.
    assert por_nome.loc["PARADO", ["valor_parado", "valor_excesso", "valor_eficiente"]].tolist() == [200.0, 0.0, 0.0]
    # RUPTURA não tem estoque, logo não tem valor.
    assert por_nome.loc["RUPTURA", "valor_estoque"] == 0
    assert (d["valor_eficiente"] + d["valor_excesso"] + d["valor_parado"]).tolist() == pytest.approx(d["valor_estoque"].tolist())
    assert (d["valor_eficiente"] >= 0).all()


def test_estoque_negativo_nunca_vira_valor_negativo():
    """O cálculo do Assistente já trata negativo como 0, mas uma linha estranha não pode entortar os cards."""
    estranha = ("NEGATIVO", -5, 1.0, 4.0, 9.0, 7, 90, "AA", lc.GENERICOS, "PRESCRIÇÃO GENÉRICO", "medicamento")
    d = _preparar(_quadro(estranha, EFICIENTE))
    assert d.loc[d["nome"] == "NEGATIVO", "estoque_pos"].iloc[0] == 0
    assert d["valor_estoque"].sum() == pytest.approx(40) and int(d["em_ruptura"].sum()) == 1


def test_sem_preco_o_produto_nao_entra_nos_reais_mas_continua_contado():
    sem_custo = ("SEM CUSTO", 50, 1.0, None, None, 7, 90, "BB", lc.GENERICOS, "PRESCRIÇÃO GENÉRICO", "medicamento")
    d = _preparar(_quadro(sem_custo, RUPTURA))
    assert len(d) == 2 and d["valor_estoque"].sum() == 0 and d["venda_risco_dia"].sum() == pytest.approx(20)


def test_estoque_por_linha_soma_o_estoque_e_vem_do_maior_para_o_menor():
    d = _preparar(_quadro(RUPTURA, PROXIMA, EXCESSO, PARADO, EFICIENTE))
    e = dash.estoque_por_linha(d)
    assert e.index.tolist() == [lc.SIMILARES, lc.PERFUMARIA, lc.GENERICOS]
    assert e["total"].tolist() == pytest.approx([300, 200, 60])        # genéricos: 4×5 + 10×4
    assert e.loc[lc.SIMILARES, ["eficiente", "excesso", "parado"]].tolist() == [120, 180, 0]
    assert e["total"].sum() == pytest.approx(d["valor_estoque"].sum())
    assert dash.estoque_por_linha(_preparar(_quadro())).empty


def test_criticos_exclui_eficiente_e_ordena_pela_venda_em_risco():
    d = _preparar(_quadro(EFICIENTE, PARADO, EXCESSO, RUPTURA_POUCO_GIRO, RUPTURA, PROXIMA))
    c = dash.criticos(d)
    assert "EFICIENTE" not in c["nome"].tolist()
    # Rupturas primeiro (a que mais custa por dia na frente); depois pelo valor parado/em excesso.
    # (parado R$ 200 vem antes do excesso R$ 180; a ruptura próxima não tem valor parado nem em excesso)
    assert c["nome"].tolist() == ["RUPTURA", "RUPTURA GIRO BAIXO", "PARADO", "EXCESSO", "PROXIMA"]


def test_filtro_cruzado_ou_dentro_da_dimensao_e_entre_dimensoes():
    d = _preparar(_quadro(RUPTURA, RUPTURA_POUCO_GIRO, PROXIMA, EXCESSO, PARADO, EFICIENTE))
    assert len(dash.filtrar(d)) == 6
    assert set(dash.filtrar(d, linhas=[lc.GENERICOS])["nome"]) == {"RUPTURA", "PROXIMA", "EFICIENTE"}
    assert len(dash.filtrar(d, linhas=[lc.GENERICOS, lc.PERFUMARIA])) == 4                    # OU
    assert set(dash.filtrar(d, linhas=[lc.GENERICOS], curvas=["B"])["nome"]) == {"PROXIMA"}   # E
    assert dash.filtrar(d, linhas=["nao_existe"]).empty


def test_as_barras_ignoram_o_proprio_filtro():
    """Se a barra de Genéricos filtrasse o próprio gráfico, as outras sumiriam e não haveria como somar com Ctrl+clique."""
    d = _preparar(_quadro(RUPTURA, EXCESSO, PARADO))
    so_curva = dash.sem_filtro_de(d, "linha", linhas=[lc.GENERICOS], curvas=["A"])
    assert set(so_curva["nome"]) == {"RUPTURA"}                  # a curva A vale; a linha escolhida não
    so_linha = dash.sem_filtro_de(d, "curva", linhas=[lc.SIMILARES], curvas=["A"])
    assert set(so_linha["nome"]) == {"EXCESSO"}


def test_quadro_vazio_nao_quebra_nada():
    """Loja sem vendas na janela volta do cálculo sem linhas (visto em 3 de 21 lojas reais)."""
    d = dash.preparar(pd.DataFrame(columns=["estoque"]), CONFIG)
    assert d.empty
    assert dash.criticos(d).empty and dash.filtrar(d, linhas=["x"]).empty


def test_excesso_usa_o_limite_da_configuracao():
    d = _preparar(_quadro(FORA_DO_LIMITE), ConfigPedido.de_dict({"dias_cobertura_excesso": 30}))
    assert d["situacao"].iloc[0] == "excesso"                    # 40 dias > 30
    assert d["valor_excesso"].iloc[0] == pytest.approx((80 - 2 * 30) * 1.0)       # 20 un. acima de 30 dias de venda


def test_excesso_nao_depende_do_ideal_do_assistente():
    """Assistente e Dashboard são duas visões: o ideal (7 a 15 dias) é meta de compra, não régua de análise.
    Mesmo produto com ideal 14 ou 140: o excesso do Dashboard é o mesmo."""
    base = ("X", 300, 2.0, 1.0, 3.0, 14, 180, "BC", lc.SIMILARES, "PRESCRIÇÃO TRADE", "medicamento")
    com_ideal_alto = base[:5] + (140,) + base[6:]
    a = _preparar(_quadro(base)).iloc[0]
    b = _preparar(_quadro(com_ideal_alto)).iloc[0]
    assert a["valor_excesso"] == b["valor_excesso"] == pytest.approx(180) and a["situacao"] == b["situacao"] == "excesso"
    # Acima do ideal do Assistente mas dentro de 60 dias de venda: NÃO é excesso.
    assert _preparar(_quadro(("Y", 100, 2.0, 1.0, 3.0, 14, 180, "BC", lc.SIMILARES, "MIP/OTC", "medicamento")))["situacao"].iloc[0] == "eficiente"
    # Só a parte que passa do limite conta: 150 un. a 2/dia, limite 60 dias → 30 em excesso.
    z = _preparar(_quadro(("Z", 150, 2.0, 1.0, 3.0, 14, 180, "BC", lc.SIMILARES, "MIP/OTC", "medicamento"))).iloc[0]
    assert z["valor_excesso"] == pytest.approx(30) and z["valor_eficiente"] == pytest.approx(120)


def test_linha_de_compra_sem_a_linha_da_tela_fica_em_classificacao():
    """Desde 25/10/2026 a linha vem da cascata de classificação, que a tela monta; a categoria da base (FEBRAFAR)
    não classifica mais. Sem a linha da tela, tudo fica "Em classificação"."""
    df = _quadro(RUPTURA, PARADO)
    d = dash.preparar(df, CONFIG)                                # sem `linha` explícita
    assert d["linha_compra"].tolist() == [lc.SEM_LINHA, lc.SEM_LINHA]


# ---------------------------------------------------------------------------
# Eixo dos gráficos: sempre 5 marcas, passo 1-2-2,5-5, zero sempre presente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valores, esperado", [
    ([], (0, 4, [0, 1, 2, 3, 4])),
    ([0, 0], (0, 4, [0, 1, 2, 3, 4])),
    ([7], (0, 8, [0, 2, 4, 6, 8])),                    # 7 ÷ 4 = 1,75 → passo 2
    ([130], (0, 200, [0, 50, 100, 150, 200])),         # 130 ÷ 4 = 32,5 → passo 50
    ([1000, 400], (0, 1000, [0, 250, 500, 750, 1000])),     # 1000 ÷ 4 = 250: o 2,5 evita sobrar metade do gráfico vazia
    ([829], (0, 1000, [0, 250, 500, 750, 1000])),            # o caso real da loja fictícia: R$ 829 caía num eixo até R$ 2 mil
    ([-130], (-200, 0, [-200, -150, -100, -50, 0])),   # só negativo: o eixo desce
    ([-30, 100], (-50, 150, [-50, 0, 50, 100, 150])),  # os dois lados: o zero é uma das marcas
    ([-10, 10], (-10, 10, [-10, -5, 0, 5, 10])),
])
def test_eixo(valores, esperado):
    minimo, maximo, marcas = dash.eixo(valores)
    assert (minimo, maximo) == esperado[:2] and marcas == esperado[2]
    assert len(marcas) == 5 and 0 in marcas


@pytest.mark.parametrize("valores, esperado", [
    ([2], (0, 4, [0, 1, 2, 3, 4])),                    # contagem: passo 1, nunca 0,5 ("1,5 item")
    ([1], (0, 4, [0, 1, 2, 3, 4])),
    ([0], (0, 4, [0, 1, 2, 3, 4])),
    ([7], (0, 8, [0, 2, 4, 6, 8])),                    # a partir de 4 o passo normal já é inteiro
    ([-2, 3], (-2, 6, [-2, 0, 2, 4, 6])),
    ([9], (0, 20, [0, 5, 10, 15, 20])),                # 9 ÷ 4 = 2,25: o 2,5 não é inteiro, então vai para 5
    ([90], (0, 100, [0, 25, 50, 75, 100])),            # a partir de 25 o 2,5 é inteiro e vale
])
def test_eixo_de_contagem_nunca_tem_casas_decimais(valores, esperado):
    minimo, maximo, marcas = dash.eixo(valores, inteiro=True)
    assert (minimo, maximo, marcas) == esperado[:2] + (esperado[2],)
    assert all(float(m).is_integer() for m in marcas)
    assert dash.eixo([2])[2] == [0, 0.5, 1, 1.5, 2]    # sem `inteiro`, o passo é 0,5: por isso o parâmetro existe


def test_eixo_ignora_nan_e_sempre_cobre_os_valores():
    assert dash.eixo([float("nan"), 3]) == (0, 4, [0, 1, 2, 3, 4])
    for valores in ([1], [99], [101], [123456], [-3, 4], [-1e6, 5e5], [0.4], [-0.02, 0.03]):
        minimo, maximo, marcas = dash.eixo(valores)
        assert minimo <= min(valores + [0]) and maximo >= max(valores + [0]) and len(marcas) == 5
