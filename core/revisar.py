"""O que fazer com um preço "A revisar" (28/10/2026).

As três telas que marcam preço "A revisar" (Assistente de pedido, Dashboard e
Análise de Oportunidade) diziam o porquê, mas terminavam com o mesmo
"Confira as notas no sistema de vendas" para todos os motivos — o dono da
loja ficava sem saber O QUE conferir. Aqui fica uma ação por motivo, a mesma
nas três telas. O porquê continua em cada tela (cada uma tem os valores dela).

As chaves são os códigos de `pedido/calculo.py` (MOTIVO_*), de
`core/analise_api.py` (MOTIVO_*) e os textos curtos de
`pedido/estoque_modelo.py` (MOTIVO_SEM_CUSTO, MOTIVO_FAIXA) — escritos à mão
pra este módulo não importar o pacote `pedido`; `tests/test_revisar.py`
confere que nenhum código ficou de fora.
"""
from __future__ import annotations

_ACAO = {
    # Cadastro × nota divergem e o preço de venda não decide qual está certo.
    "divergencia": "Compare, no sistema de vendas, o custo de cadastro com a última nota e corrija o que estiver "
                   "errado. Quase sempre é a fração da embalagem.",
    # A nota passa do preço de venda.
    "acima_venda": "Abra a nota no sistema de vendas e confira a quantidade. Uma caixa com 10 unidades lançada como "
                   "1 unidade deixa o preço 10 vezes maior.",
    # Sem compra recente e o cadastro não combina com o preço de venda.
    "cadastro": "Corrija o custo de cadastro do produto no sistema de vendas. Ele vale até a loja comprar o produto "
                "de novo.",
    # Compra muito diferente das outras compras do produto na loja (Assistente "compra", Análise "mediana").
    "compra": "Abra a última nota no sistema de vendas e confira quantidade, fração e preço. Um deles foi lançado "
              "errado.",
    "mediana": "Abra a nota no sistema de vendas e confira quantidade, fração e preço. Um deles foi lançado errado.",
    # Modelo de estoque do Dashboard.
    "Sem custo no cadastro nem em compra": "Cadastre o custo do produto no sistema de vendas.",
    "Custo fora da faixa do preço de venda": "Confira, no sistema de vendas, o custo e o preço de venda do produto. "
                                             "Um dos dois está errado.",
}
_PADRAO = "Confira, no sistema de vendas, o custo do produto e a fração da embalagem."


def acao(motivo) -> str:
    """O parágrafo "O que fazer" da dica do "A revisar". Motivo desconhecido cai no texto geral."""
    return f"O que fazer: {_ACAO.get(str(motivo), _PADRAO)}"


def motivos() -> set[str]:
    return set(_ACAO)
