"""Textos e linhas da página Estoque (views/dashboard_estoque.py): as partes puras, sem Streamlit."""
import numpy as np
import pandas as pd

from views import dashboard_estoque as v


def test_cobertura_texto():
    assert v.cobertura_texto(None) == "—"
    assert v.cobertura_texto(np.nan) == "—"
    assert v.cobertura_texto(np.inf) == "—"          # sem venda não há cobertura
    assert v.cobertura_texto(0.4) == "menos de 1 dia"
    assert v.cobertura_texto(1) == "1 dia"
    assert v.cobertura_texto(2624) == "2.624 dias"


def test_data_curta():
    assert v.data_curta("2026-10-05") == "05/10/2026"
    assert v.data_curta("2026-10-05T10:00:00") == "05/10/2026"
    for vazio in (None, "", "NaT", pd.NaT, np.nan):
        assert v.data_curta(vazio) == "—"


def test_pct_usa_virgula():
    assert v.pct(30.123) == "30,1%"


def test_md_escapa_o_que_viraria_formatacao():
    assert "\*" in v.md("PRODUTO *PROMO* $5")
    assert "\$" in v.md("PRODUTO *PROMO* $5")


def _sku(**extra):
    base = {"codigo_produto": 7, "nome": "DIPIRONA 500MG", "categoria_rotulo": "Prescrição genérico", "linha_compra": "genericos", "curva": "AB",
            "situacao": "excesso", "estoque": 40, "excesso_un": 12.0, "valor_estoque": 123.4, "custo_ok": True, "custo_motivo": "",
            "vendidas_n": 30, "cobertura": 80.0, "compra_recente": False, "ultima_compra": "2026-10-05"}
    return {**base, **extra}


def test_linha_critico_com_custo_confiavel():
    linha = v.linha_critico(_sku())
    assert linha["id"] == "7"
    assert linha["celulas"]["valor_estoque"][0][0]["t"].startswith("R$")
    assert linha["celulas"]["excesso_un"][0][0]["t"] == "12"


def test_linha_critico_a_revisar_e_compra_recente():
    linha = v.linha_critico(_sku(custo_ok=False, custo_motivo="Custo fora da faixa do preço de venda", compra_recente=True, excesso_un=0.0))
    custo = linha["celulas"]["valor_estoque"][0][0]
    assert custo["t"] == "a revisar" and "não combina com o preço de venda" in custo["dica"]
    assert linha["celulas"]["excesso_un"][0][0]["t"] == "—"
    assert "compra recente 05/10" in linha["celulas"]["nome"][1][0]["t"]


def test_linha_total_nao_e_selecionavel():
    t = {"skus": 3, "unidades": 10, "estoque_compra": 100.0, "estoque_venda": 150.0, "cobertura": 12.0}
    linha = v.linha_total(t)
    assert linha["selecionavel"] is False and linha["classe"] == "total"


def test_linha_critico_tem_status_em_pilula():
    excesso = v.linha_critico(_sku())["celulas"]["situacao"][0][0]
    parado = v.linha_critico(_sku(situacao="parado"))["celulas"]["situacao"][0][0]
    assert excesso["t"] == "Excesso" and parado["t"] == "Parado"
    assert excesso["c"] != parado["c"]


# --- Detalhe do SKU: diagnóstico, sugestão e cards (22/10/2026) ---------------------------------------------

import dataclasses

from core import dashboard_estoque as de
from pedido.configuracao import ConfigPedido

HOJE = "2026-10-20"


def _r(**extra):
    base = {"nome": "TESTE", "ean": "789", "categoria_rotulo": "MIP/OTC", "linha_compra": "propagados", "curva": "AB", "situacao": de.EXCESSO,
            "estoque": 100, "excesso_un": 40.0, "valor_estoque": 500.0, "valor_excesso": 200.0, "valor_parado": 0.0,
            "custo": 5.0, "custo_origem": "compra", "custo_ok": True, "custo_motivo": "", "vendidas_n": 30,
            "cobertura": 120.0, "compra_recente": False, "ultima_compra": "2026-09-01"}
    return {**base, **extra}


def test_diagnostico_excesso_usa_os_limites_da_config():
    padrao = ConfigPedido()
    personalizada = dataclasses.replace(padrao, dias_cobertura_excesso=45)
    assert "meta de 60" in de.diagnostico(_r(), padrao, HOJE)
    texto = de.diagnostico(_r(), personalizada, HOJE)
    assert "meta de 45" in texto and "R$ 200,00" in texto and "120 dias" in texto


def test_diagnostico_parado_usa_os_dias_da_config():
    config = dataclasses.replace(ConfigPedido(), dias_parado=120)
    texto = de.diagnostico(_r(situacao=de.PARADO, valor_parado=500.0, vendidas_n=0, cobertura=np.nan), config, HOJE)
    assert "últimos 120 dias" in texto and "todo parado" in texto


def test_diagnostico_compra_recente_e_custo_a_revisar():
    texto = de.diagnostico(_r(compra_recente=True, ultima_compra="2026-10-10", custo_ok=False), ConfigPedido(), HOJE)
    assert "Comprado há 10 dias." in texto and "custo deste produto não é confiável" in texto
    assert "R$ 200" not in texto                        # sem custo confiável, o excesso não vira valor em R$


def test_sugestoes_por_situacao():
    config = dataclasses.replace(ConfigPedido(), dias_cobertura_excesso=45)
    excesso = dict(de.sugestoes(_r(), config, HOJE))
    assert "45 dias" in excesso["Suspender a compra"] and "Kit ou brinde" in excesso
    parado = dict(de.sugestoes(_r(situacao=de.PARADO), config, HOJE))
    assert {"Kit ou combo", "Brinde ou sorteio", "Fornecedor"} <= set(parado)
    assert not any("balão" in t.lower() or "balão" in x.lower() for t, x in de.sugestoes(_r(situacao=de.PARADO), config, HOJE))


def test_sugestoes_conferir_custo_e_pedido_vem_primeiro():
    itens = de.sugestoes(_r(custo_ok=False, compra_recente=True, ultima_compra="2026-10-18"), ConfigPedido(), HOJE)
    assert [t for t, _ in itens[:2]] == ["Conferir o custo", "Conferir o pedido"]


def test_cards_detalhe_excesso_e_vermelhos():
    cards = {c["rotulo"]: c for c in v.cards_detalhe(_r(), ConfigPedido(), HOJE)}
    assert len(cards) == 6
    assert cards["Excesso"]["valor"] == "40 un." and cards["Excesso"]["alerta"]
    assert cards["Cobertura"]["alerta"]                       # 120 dias > 60
    assert cards["Valor de compra"]["aux"].startswith("R$ 5,00/un.")
    assert cards["Última compra"]["aux"] == "há 49 dias"


def test_cards_detalhe_parado_troca_excesso_por_valor_parado():
    r = _r(situacao=de.PARADO, valor_parado=500.0, vendidas_n=0, cobertura=np.nan, excesso_un=0.0, ultima_compra=None)
    cards = {c["rotulo"]: c for c in v.cards_detalhe(r, ConfigPedido(), HOJE)}
    assert "Excesso" not in cards and cards["Valor parado"]["valor"] == "R$ 500,00"
    assert cards["Cobertura"]["valor"] == "—" and cards["Última compra"]["valor"] == "Sem compra"
    assert cards["Vendas 90 dias"]["aux"] == "sem venda"


def test_html_detalhe_escapa_nome_e_cifrao():
    html = v._html_detalhe(_r(nome="<b>X</b> $5"), ConfigPedido(), HOJE)
    assert "<b>X" not in html and "&lt;b&gt;" in html and "$" not in html


def test_criticos_e_detalhe_mostram_a_categoria_da_classificacao_nova():
    """26/10/2026: embaixo do nome vai uma das 5 categorias (a da linha de compra), não a da base (FEBRAFAR)."""
    linha = v.linha_critico(_sku())
    assert linha["celulas"]["nome"][1][0]["t"].startswith("Genérico")
    assert v.categoria_do_sku({"linha_compra": None}) == "Em classificação"          # nunca vazio
    assert v.categoria_do_sku({}) == "Em classificação"
    html = v._html_detalhe(_r(), ConfigPedido(), HOJE)
    assert "Propagado | EAN: 789" in html and "MIP/OTC" not in html


# --- Análise por categoria: as 5 categorias e o detalhamento (26/10/2026) -------------------------------------

def test_linha_de_categoria_tem_setinha_so_com_detalhe():
    r = {"codigo": "genericos", "categoria": "Genérico", "skus": 2, "unidades": 400, "estoque_compra": 800.0,
         "pct_compra": 85.1, "estoque_venda": 2000.0, "pct_venda": 80.0, "cobertura": 100.0}
    assert v.linha_categoria(r, True)["filhos"] is True and v.linha_categoria(r)["filhos"] is False
    assert v.linha_categoria(r)["id"] == "genericos"                       # o código, que é o do filtro do gráfico


def test_linhas_de_detalhe_encadeadas_so_informam():
    m = {"skus": 1, "unidades": 10, "estoque_compra": 50.0, "pct_compra": 5.0, "estoque_venda": 90.0,
         "pct_venda": 4.0, "cobertura": 12.0}
    no = {"fonte": "iqvia", "chave": "RX_GENERICO", "metricas": m, "filhos": [
        {"fonte": "febrafar", "chave": "MIP/OTC", "metricas": m, "filhos": [
            {"fonte": "cmed", "chave": "Genérico", "metricas": m, "filhos": []}]}]}
    linhas = v.linhas_detalhe(no, "genericos", "genericos")
    assert [l["nivel"] for l in linhas] == [1, 2, 3]
    assert [l["pai"] for l in linhas] == ["genericos", linhas[0]["id"], linhas[1]["id"]]
    assert all(l["selecionavel"] is False and l["grupo"] == "genericos" for l in linhas)
    assert [l["filhos"] for l in linhas] == [True, True, False]
    nome = linhas[0]["celulas"]["categoria"][0]
    assert nome == [{"t": "Genérico de prescrição"}]                      # sem etiqueta da fonte: só o nome
    assert linhas[1]["celulas"]["categoria"][0] == [{"t": "MIP/OTC"}]
