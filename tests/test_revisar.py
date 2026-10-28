"""O que fazer com um preço "A revisar" (core/revisar.py, 28/10/2026)."""
from core import analise_api, dicas, revisar
from pedido import calculo, estoque_modelo


def test_todo_motivo_das_tres_telas_tem_acao_propria():
    codigos = {calculo.MOTIVO_COMPRA, calculo.MOTIVO_DIVERGENCIA, calculo.MOTIVO_CADASTRO, calculo.MOTIVO_ACIMA_VENDA,
               analise_api.MOTIVO_DIVERGENCIA, analise_api.MOTIVO_ACIMA_VENDA, analise_api.MOTIVO_MEDIANA,
               estoque_modelo.MOTIVO_SEM_CUSTO, estoque_modelo.MOTIVO_FAIXA}
    assert codigos <= revisar.motivos()
    textos = {revisar.acao(c) for c in codigos}
    assert len(textos) == len(revisar.motivos())          # nenhum motivo repete a ação de outro
    assert revisar.acao("codigo-novo") not in textos      # desconhecido cai no texto geral


def test_acao_segue_a_regra_das_dicas():
    for motivo in [*revisar.motivos(), None]:
        texto = revisar.acao(motivo)
        assert texto.startswith("O que fazer: ") and not dicas.problemas(dicas.dica(texto)), motivo
