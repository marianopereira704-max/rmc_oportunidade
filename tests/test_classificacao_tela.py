"""A seção "Classificação de produtos" das Configurações (views/classificacao_produtos.py, 25/10/2026): o HTML da
cascata e o resumo do que muda na correspondência."""
import dataclasses

import pandas as pd

from pedido import classificacao as cl
from pedido import linhas as lc
from pedido.configuracao import ConfigPedido
from views import classificacao_produtos as tela


def _arvore():
    d = pd.DataFrame([
        ["1", "RX_GENERICO", "PRESCRIÇÃO GENÉRICO", "Genérico", None],
        ["2", "RX_GENERICO", "MIP/OTC", None, None],
        ["3", None, "HIGIENE <b>", None, None],
    ], columns=["ean", "setor_nec", "febrafar", "cmed_tipo", "manual"])
    return cl.arvore(cl.classificar(d))


def test_cascata_abre_so_quem_tem_filhos_e_mostra_a_fonte():
    h = tela.html_cascata(_arvore())
    assert h.startswith('<div class="rmc-cascata">') and "\n" not in h          # uma peça só, sem linha em branco
    # Genérico, o setor RX_GENERICO, a FEBRAFAR "PRESCRIÇÃO GENÉRICO" (abre a CMED) e "Em classificação" (abre a
    # FEBRAFAR de quem só ela conhece).
    assert h.count("<details") == 4
    assert '<span class="fonte iqvia">IQVIA</span>' in h and '<span class="fonte cmed">CMED</span>' in h
    assert '<span class="fonte febrafar">FEBRAFAR</span>' in h
    # Categoria sem nada: barra sem setinha.
    assert '<div class="folha nivel-categoria"><span class="nome">Propagado</span><span class="qtd">0 EANs' in h


def test_cascata_quantidade_no_singular_e_plural_e_percentual_so_no_nivel_1():
    h = tela.html_cascata(_arvore())
    assert "2 EANs <span class=\"pct\">(66,7%)</span>" in h                     # Genérico: 2 de 3
    assert "1 EAN <span class=\"pct\">(33,3%)</span>" in h                      # Em classificação: 1 de 3
    assert h.count('class="pct"') == 5                                         # só as 5 categorias


def test_cascata_escapa_os_nomes():
    h = tela.html_cascata(_arvore())
    assert "HIGIENE &lt;b&gt;" in h and "HIGIENE <b>" not in h


def test_categoria_do_tipo_compara_sem_acento_nem_caixa():
    c = ConfigPedido()
    assert tela.categoria_do_tipo(c, "GENERICO") == lc.GENERICOS
    assert tela.categoria_do_tipo(c, "similar") == lc.SIMILARES
    assert tela.categoria_do_tipo(c, "Novo") == lc.SEM_LINHA


def test_mudancas_lista_so_o_que_mudou_em_frases():
    antes = ConfigPedido()
    depois = dataclasses.replace(antes, linhas_por_setor={**antes.linhas_por_setor, "RX_TRADE": lc.PROPAGADOS},
                                 linhas_por_cmed={"Genérico": lc.GENERICOS, "Similar": lc.SIMILARES, "Novo": lc.PROPAGADOS})
    assert tela.mudancas(antes, depois, ["Genérico", "Similar", "Novo"]) == [
        "IQVIA, Prescrição de trade (similar): Similar → Propagado",
        "CMED, Novo: Em classificação → Propagado",
    ]
    assert tela.mudancas(antes, antes, ["Novo"]) == []
