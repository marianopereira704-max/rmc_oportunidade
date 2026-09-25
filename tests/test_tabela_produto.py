"""Células da tabela de produtos (Por Produto e Detalhes da loja —
views/analise_comum.py → views/tabela.py)."""
from core import analise
from views import analise_comum as comum


def _produto(**campos) -> dict:
    base = {
        "loja_id": 3, "base_generico_id": 9, "nome_canonico": "LOSARTANA 50MG", "razao_social": " DROGARIA X ",
        "cnpj": "12345678000190", "laboratorio": "EMS", "preco_pago": 10.0, "preco_laboratorio": 8.0,
        "diferenca": 2.0, "quantidade": 5, "preco_medio": 9.5, "meses_preco_medio": 3, "economia": 10.0,
        "fonte": analise.FONTE_VLR, "vlr_unitario_original": 10.0,
    }
    base.update(campos)
    return base


def _celulas(**campos) -> dict:
    return comum.linha_produto(_produto(**campos), com_loja=True)["celulas"]


def test_oito_colunas_com_o_laboratorio_escolhido_no_titulo():
    colunas = comum.colunas_produto("Ranbaxy")
    assert [c.rotulo for c in colunas][:4] == ["Produto", "Laboratório", "Preço pago", "Ranbaxy"]
    assert len(colunas) == 8
    assert set(comum.linha_produto(_produto(), True)["celulas"]) == {c.id for c in colunas}


def test_produto_com_loja_embaixo_so_no_por_produto():
    loja = comum.linha_produto(_produto(), com_loja=True)["celulas"]["nome_canonico"][1]
    assert [p["t"] for p in loja] == ["DROGARIA X ·", "12.345.678/0001-90"]
    assert "inteiro" in loja[1]["c"]  # CNPJ não quebra no meio
    assert len(comum.linha_produto(_produto(), com_loja=False)["celulas"]["nome_canonico"]) == 1


def test_preco_ajustado_leva_selo_e_dica_com_o_vlrunitario_original():
    celula = _celulas(fonte=analise.FONTE_CMV, vlr_unitario_original=99.0)["preco_pago"]
    selo = celula[1][0]
    assert (selo["t"], selo["c"]) == ("ajustado", "selo-sugestao")
    assert "R$ 99,00" in selo["dica"] and "CMV" in selo["dica"]
    assert _celulas(fonte=analise.FONTE_CUSTO_MEDIO)["preco_pago"][1][0]["t"] == "ajustado"


def test_preco_a_revisar_nao_contabiliza_economia():
    celulas = _celulas(fonte=analise.FONTE_FORA, economia=0.0)
    assert celulas["preco_pago"][1][0]["t"] == "a revisar"
    assert celulas["economia"][0][0]["t"] == "Não contabilizada"


def test_preco_direto_do_vlrunitario_sem_selo():
    assert len(_celulas()["preco_pago"]) == 1


def test_diferenca_negativa_em_vermelho_com_sinal():
    pedaco = _celulas(diferenca=-1.5)["diferenca"][0][0]
    assert pedaco == {"t": "−R$ 1,50", "c": "negativo"}
    assert "c" not in _celulas(diferenca=2.0)["diferenca"][0][0]


def test_preco_medio_mostra_meses_so_quando_faltam():
    assert len(_celulas(meses_preco_medio=3)["preco_medio"][0]) == 1
    assert _celulas(meses_preco_medio=1)["preco_medio"][0][1] == {"t": "(1m)", "c": "aux"}
    assert _celulas(preco_medio=None)["preco_medio"][0][0]["t"] == "—"


def test_economia_zero_ja_otimizada():
    assert _celulas(economia=0.0)["economia"][0][0]["t"] == "Já otimizada"
    assert _celulas()["economia"][0][0]["c"] == "selo-sucesso"


def test_id_da_linha_junta_loja_e_generico():
    assert comum.linha_produto(_produto(), True)["id"] == "3-9"
