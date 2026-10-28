"""Linhas de compra do Comprador no Assistente (pedido/linhas.py, 07/10/2026)."""
import pandas as pd

from pedido import classificacao as cl
from pedido import linhas


def _tabela() -> pd.DataFrame:
    """Produtos do Assistente: a linha sai da cascata de classificação (25/10/2026), pelo EAN."""
    return pd.DataFrame({"produto": ["gen", "sabonete", "etico", "sem", "mip"],
                         "eans": [["1"], ["2"], ["3"], ["4"], ["5"]], "ean_principal": ["1", "2", "3", "4", "5"]})


MAPA = {"1": (linhas.GENERICOS, cl.ORIGEM_IQVIA), "2": (linhas.PERFUMARIA, cl.ORIGEM_IQVIA),
        "3": (linhas.PROPAGADOS, cl.ORIGEM_IQVIA), "5": (linhas.SIMILARES, cl.ORIGEM_CMED)}   # o 4: nenhuma fonte


def test_linha_pela_cascata_de_classificacao():
    assert list(cl.coluna(_tabela(), MAPA)) == [linhas.GENERICOS, linhas.PERFUMARIA, linhas.PROPAGADOS,
                                                linhas.SEM_LINHA, linhas.SIMILARES]


def test_comprador_ve_so_as_linhas_dele_mais_em_classificacao():
    t = _tabela()
    assert list(cl.filtrar(t, ["genericos"], MAPA)["produto"]) == ["gen", "sem"]
    assert list(cl.filtrar(t, ["perfumaria", "similares"], MAPA)["produto"]) == ["sabonete", "sem", "mip"]
    # Sem linha gravada (Proprietário, ou Comprador antigo): tudo.
    assert len(cl.filtrar(t, [], MAPA)) == len(cl.filtrar(t, None, MAPA)) == 5
    assert cl.filtrar(t.iloc[0:0], ["genericos"], MAPA).empty


def test_mapa_da_loja_pega_so_os_eans_pedidos():
    universo = pd.DataFrame({"ean": ["1", "2", "9"], "setor_nec": ["RX_GENERICO", None, "RX_TRADE"],
                             "febrafar": [None, "HIGIENE", None], "cmed_tipo": [None, None, None], "manual": [None] * 3})
    tabela = cl.tabela_por_ean(cl.classificar(universo))
    assert list(tabela.index) == ["1", "2", "9"]                              # todos que alguma fonte conhece
    assert cl.mapa_da_loja(tabela, {"1", "2", "x", None}) == {"1": (linhas.GENERICOS, cl.ORIGEM_IQVIA),
                                                              "2": (linhas.SEM_LINHA, cl.ORIGEM_NENHUMA)}
    # O 2 (só a FEBRAFAR) não classifica: o produto dele fica "Em classificação".
    assert cl.categoria_do_produto(["2"], "2", cl.mapa_da_loja(tabela, {"2"})) == linhas.SEM_LINHA
    caminho = cl.caminhos_da_loja(tabela, {"1", "2", "x"})
    assert set(caminho.index) == {"1", "2"}
    assert caminho.loc["1", "setor"] == "RX_GENERICO" and caminho.loc["2", "febrafar"] == "HIGIENE"
    assert pd.isna(caminho.loc["2", "setor"])
    assert cl.mapa_da_loja(tabela.iloc[0:0], {"1"}) == {}
    assert cl.eans_da_tabela(_tabela()) == {"1", "2", "3", "4", "5"}


# ---------------------------------------------------------------------------
# Catálogo IQVIA (etapa 5)
# ---------------------------------------------------------------------------

def test_montar_catalogo_limpa_o_arquivo():
    bruto = pd.DataFrame({"EAN": ["07891234567890", None, "7890000000001", "7891234567890", "7899"],
                          "SETOR_NEC": ["rx_generico", "RX_TRADE", "OUTRO", "RX_PROMOVIDO", "NAO_MEDICAMENTO_PEC"],
                          "GMRS": ["G", "S", None, "M", None]})
    c = linhas.montar_catalogo(bruto)
    # sem EAN e setor desconhecido saem; o repetido (mesma chave sem o zero) fica com a 1ª ocorrência
    assert list(c["ean"]) == ["7891234567890", "7899"]
    assert list(c["setor_nec"]) == ["RX_GENERICO", "NAO_MEDICAMENTO_PEC"]
    assert c["gmrs"].iloc[0] == "G" and c["gmrs"].iloc[1] is None


def test_correspondencia_de_linhas_e_so_do_padrao_nao_da_loja():
    """25/10/2026 (D8 do design): a classificação é do produto, igual em todas as lojas. O que uma loja tinha gravado
    de `linhas_por_setor`/`linhas_por_cmed` fica inerte e não conta como personalização."""
    from pedido import configuracao as cfg

    padrao = cfg.ConfigPedido()
    assert padrao.linhas_por_setor == linhas.PADRAO_POR_SETOR and padrao.erros() == []
    assert padrao.linhas_por_cmed == {"Genérico": "genericos", "Similar": "similares"}
    loja = cfg.aplicar(padrao, {"linhas_por_setor": {"MIP_TRADE": "propagados"}, "linhas_por_cmed": {"Novo": "propagados"},
                                "dias_medicamento": 9})
    assert loja.linhas_por_setor == padrao.linhas_por_setor and loja.linhas_por_cmed == padrao.linhas_por_cmed
    assert loja.dias_medicamento == 9                                            # o resto da personalização segue valendo
    assert cfg.diferencas(padrao, loja) == {"dias_medicamento": 9}
    # Quantos campos a loja personaliza: o valor antigo de linhas não conta.
    assert cfg.quantos_campos({"linhas_por_setor": {"MIP_TRADE": "propagados"}, "dias_medicamento": 9}) == 1
    # Config gravada antes do campo existir: herda o padrão.
    assert cfg.ConfigPedido.de_dict({"dias_medicamento": 9}).linhas_por_setor == linhas.PADRAO_POR_SETOR
    assert cfg.ConfigPedido.de_dict({"dias_medicamento": 9}).linhas_por_cmed == padrao.linhas_por_cmed
    # O padrão editado pelo admin valida o setor, o tipo e a linha.
    errada = cfg.ConfigPedido(linhas_por_setor={"RX_TRADE": "inventada", "XPTO": "genericos"},
                              linhas_por_cmed={"Novo": "inventada", " ": "genericos"})
    assert len([e for e in errada.erros() if "desconhecid" in e or "sem nome" in e]) == 4


def test_rotulos_novos_no_singular_com_os_mesmos_codigos():
    assert linhas.ROTULOS == {"genericos": "Genérico", "similares": "Similar", "propagados": "Propagado",
                              "perfumaria": "Perfumaria", "sem_linha": "Em classificação"}
