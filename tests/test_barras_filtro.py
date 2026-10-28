"""Barras clicáveis do Dashboard (21/10/2026): as regras que não dependem de
navegador — seleção, abreviação dos números e os dados que o gráfico recebe.
O clique de verdade (mouse, Ctrl, teclado) é conferido no teste visual."""
import pytest

from core.theme import GRAFICO
from views import barras_filtro as bf


# ---------------------------------------------------------------------------
# Seleção
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("atual, chave, soma, esperado", [
    ([], "A", False, ["A"]),                 # clique simples numa barra: só ela
    (["A"], "A", False, []),                 # clique na única selecionada: limpa
    (["A"], "B", False, ["B"]),              # clique simples noutra: substitui
    (["A", "B"], "A", False, ["A"]),         # com duas selecionadas, clique simples deixa só a clicada
    (["A"], "B", True, ["A", "B"]),          # Ctrl+clique soma
    (["A", "B"], "A", True, ["B"]),          # Ctrl+clique numa já selecionada tira
    ([], "A", True, ["A"]),                  # Ctrl+clique sem nada selecionado
    (["A"], "A", True, []),                  # Ctrl+clique na única tira (e o filtro some)
])
def test_nova_selecao(atual, chave, soma, esperado):
    assert bf.nova_selecao(atual, chave, soma) == esperado


def test_nova_selecao_nao_altera_a_lista_recebida():
    atual = ["A"]
    bf.nova_selecao(atual, "B", True)
    assert atual == ["A"]


# ---------------------------------------------------------------------------
# Números abreviados (eixo e valor em cima da barra; tabelas e cards nunca abreviam)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (0, "0"), (7, "7"), (850, "850"), (999, "999"),
    (1000, "1 mil"), (2500, "2,5 mil"), (150000, "150 mil"), (372835, "373 mil"), (999999, "1000 mil"),
    (1_000_000, "1 mi"), (1_500_000, "1,5 mi"), (12_300_000, "12,3 mi"), (250_000_000, "250 mi"),
    (-1500, "-1,5 mil"), (-50, "-50"),
])
def test_abreviar(valor, esperado):
    assert bf.abreviar(valor) == esperado


def test_moeda_curta():
    assert bf.moeda_curta(372835) == "R$ 373 mil" and bf.moeda_curta(850) == "R$ 850" and bf.moeda_curta(-2000) == "R$ -2 mil"


# ---------------------------------------------------------------------------
# Dados do gráfico
# ---------------------------------------------------------------------------

def _item(chave, *valores, cores=("#1", "#2", "#3")):
    return {"chave": chave, "rotulo": chave.title(), "texto": str(sum(valores)), "dica": "",
            "partes": [{"valor": v, "cor": c, "nome": f"p{i}"} for i, (v, c) in enumerate(zip(valores, cores))]}


def test_eixo_cobre_a_barra_mais_alta_e_tem_sempre_5_marcas():
    d = bf.montar_dados([_item("a", 100, 30, 20), _item("b", 40, 0, 0)], [])
    # a barra mais alta soma 150 → 150 ÷ 4 = 37,5 → passo 50 → 0 a 200
    assert (d["eixo"]["min"], d["eixo"]["max"]) == (0, 200)
    assert [m["valor"] for m in d["eixo"]["marcas"]] == [0, 50, 100, 150, 200]
    assert [m["texto"] for m in d["eixo"]["marcas"]] == ["0", "50", "100", "150", "200"]
    assert len(d["eixo"]["marcas"]) == 5


def test_eixo_usa_o_formato_recebido():
    d = bf.montar_dados([_item("a", 372835)], [], formato=bf.moeda_curta)
    assert d["eixo"]["marcas"][-1] == {"valor": 400000, "texto": "R$ 400 mil"}


def test_grafico_de_contagem_tem_eixo_inteiro():
    d = bf.montar_dados([_item("a", 2), _item("b", 1)], [], inteiro=True)
    assert [m["valor"] for m in d["eixo"]["marcas"]] == [0, 1, 2, 3, 4]
    assert [m["valor"] for m in bf.montar_dados([_item("a", 2)], [])["eixo"]["marcas"]] == [0, 0.5, 1, 1.5, 2]


def test_valor_negativo_desce_o_eixo_e_o_zero_continua_uma_das_marcas():
    """Opção A (21/10/2026): quase impossível, mas o gráfico não pode quebrar."""
    d = bf.montar_dados([_item("a", 100, -30), _item("b", -10)], [])
    assert d["eixo"]["min"] < 0 < d["eixo"]["max"]
    valores = [m["valor"] for m in d["eixo"]["marcas"]]
    assert 0 in valores and len(valores) == 5
    assert d["eixo"]["min"] <= -30 and d["eixo"]["max"] >= 100


def test_so_negativo_e_so_zero_e_sem_barras_nao_quebram():
    assert bf.montar_dados([_item("a", -130)], [])["eixo"]["max"] == 0
    assert bf.montar_dados([_item("a", 0)], [])["eixo"]["marcas"][-1]["valor"] == 4
    sem = bf.montar_dados([], [])
    assert sem["itens"] == [] and len(sem["eixo"]["marcas"]) == 5


def test_selecao_e_legenda_vao_para_o_javascript():
    d = bf.montar_dados([_item("a", 1)], ["a"], legenda=[(GRAFICO["eficiente"], "Eficiente")])
    assert d["selecionadas"] == ["a"] and d["legenda"] == [{"cor": GRAFICO["eficiente"], "nome": "Eficiente"}]
    assert bf.montar_dados([_item("a", 1)], None)["selecionadas"] == []


def test_medidas_fixas_sao_as_mesmas_em_todo_grafico():
    a = bf.montar_dados([_item("a", 5)], [])["layout"]
    b = bf.montar_dados([_item("a", 5000, 10), _item("b", 1)], ["b"])["layout"]
    assert a == b == {"plot": 200, "topo": 28, "eixo": 72, "rotulo": 38, "barra_max": 88}


def test_cores_do_grafico_vem_do_tema_e_estao_validadas():
    """As cores do estoque são as que passaram no validador de paleta (21/10/2026)."""
    assert (GRAFICO["eficiente"], GRAFICO["excesso"], GRAFICO["parado"], GRAFICO["ruptura"]) == (
        "#3F7D20", "#C98A12", "#3B73B9", "#B3261E")
    # a mesma cor para a mesma situação em toda a tela
    assert GRAFICO["aproveitada"] == GRAFICO["eficiente"] and GRAFICO["sem_demanda"] == GRAFICO["ruptura"]
