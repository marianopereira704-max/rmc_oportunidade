"""Registros brutos da API → só as colunas que o Pedido usa (Q15 de
26/09/2026). Nada de cliente, vendedor, forma de pagamento ou caixa: além
de não serem usados, o repositório e os logs da rotina são públicos.

Limpezas que vêm de defeitos vistos no dado real (docs/mapa_api_gps.md §6.9):
nome de produto com espaço à esquerda, UF com espaço à direita, EAN com
caracteres não numéricos. E o filtro `tipoCompra=0` da API devolve 0
linhas — por isso transferências (tipo 1) saem AQUI, não na consulta.
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

COLUNAS_VENDAS = ["data", "codigo_loja", "codigo_produto", "quantidade", "fracao", "valor_liquido", "tipo_venda"]
COLUNAS_COMPRAS = [
    "data", "codigo_loja", "codigo_produto", "ean", "quantidade", "fracao", "vlr_unitario", "vlr_desconto",
    "vlr_total_liquido", "nro_nf", "fornecedor", "laboratorio",
]
COLUNAS_ESTOQUE = [
    "codigo_loja", "codigo_produto", "ean", "nome", "grupo", "categoria_gps", "laboratorio", "principio_ativo",
    "qtd_estoque", "qtd_estoque_minimo", "vlr_preco_compra", "vlr_preco_venda", "permite_compra", "ativo",
]
_NUMERICAS = {
    "quantidade", "fracao", "valor_liquido", "vlr_unitario", "vlr_desconto", "vlr_total_liquido",
    "qtd_estoque", "qtd_estoque_minimo", "vlr_preco_compra", "vlr_preco_venda",
}
_TIPO_COMPRA = "0"  # 0 = compra de fornecedor; 1 = transferência entre lojas (fica de fora — decisão de 16/09)


def _texto(valor) -> str | None:
    if valor is None:
        return None
    t = str(valor).strip()
    return t or None


def _ean(valor) -> str | None:
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos or None


def _data(valor) -> str | None:
    """'2026-09-24T00:00:00' → '2026-09-24'."""
    t = _texto(valor)
    return t[:10] if t else None


def _quadro(linhas: list[dict], colunas: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(linhas, columns=colunas)
    for c in colunas:
        if c in _NUMERICAS:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        else:
            df[c] = df[c].astype("string")
    return df


def vendas(registros: list[dict]) -> pd.DataFrame:
    return _quadro([
        {
            "data": _data(r.get("DataVenda")),
            "codigo_loja": _texto(r.get("CodigoLoja")),
            "codigo_produto": _texto(r.get("CodigoProduto")),
            "quantidade": r.get("Quantidade"),
            "fracao": r.get("Fracao"),
            "valor_liquido": r.get("VlrLiquido"),
            "tipo_venda": _texto(r.get("TipoVenda")),
        }
        for r in registros
    ], COLUNAS_VENDAS)


def compras(registros: list[dict]) -> pd.DataFrame:
    return _quadro([
        {
            "data": _data(r.get("DataEmissaoNF")),
            "codigo_loja": _texto(r.get("CodigoLoja")),
            "codigo_produto": _texto(r.get("CodigoProduto")),
            "ean": _ean(r.get("ProdutoRelacionado_CodigoBarras")),
            "quantidade": r.get("Quantidade"),
            "fracao": r.get("Fracao"),
            "vlr_unitario": r.get("VlrUnitario"),
            "vlr_desconto": r.get("VlrDesconto"),
            "vlr_total_liquido": r.get("VlrTotalLiquido"),
            "nro_nf": _texto(r.get("NroNF")),
            "fornecedor": _texto(r.get("NomeRazaoSocialFornecedor")),
            "laboratorio": _texto(r.get("ProdutoRelacionado_NomeLaboratorio")),
        }
        for r in registros
        if _texto(r.get("TipoCompra")) == _TIPO_COMPRA
    ], COLUNAS_COMPRAS)


def estoque(registros: list[dict]) -> pd.DataFrame:
    return _quadro([
        {
            "codigo_loja": _texto(r.get("CodigoLoja")),
            "codigo_produto": _texto(r.get("CodigoProduto")),
            "ean": _ean(r.get("ProdutoRelacionado_CodigoBarras")),
            "nome": _texto(r.get("NomeProduto")),
            "grupo": _texto(r.get("ProdutoRelacionado_NomeGrupo")),
            "categoria_gps": _texto(r.get("ProdutoRelacionado_NomeCategoria")),
            "laboratorio": _texto(r.get("ProdutoRelacionado_NomeLaboratorio")),
            "principio_ativo": _texto(r.get("ProdutoRelacionado_PrincipioAtivo")),
            "qtd_estoque": r.get("QtdEstoque"),
            "qtd_estoque_minimo": r.get("QtdEstoqueMinimo"),
            "vlr_preco_compra": r.get("VlrPrecoCompra"),
            "vlr_preco_venda": r.get("VlrPrecoVenda"),
            "permite_compra": _texto(r.get("PermiteCompra")),
            "ativo": _texto(r.get("ProdutoRelacionado_Ativo")),
        }
        for r in registros
    ], COLUNAS_ESTOQUE)


def por_loja(df: pd.DataFrame, lojas: list[str]) -> dict[str, pd.DataFrame]:
    """Uma fatia por loja — inclusive VAZIA para loja sem linha no período:
    o arquivo vazio é o que marca "esse período dessa loja já foi baixado"."""
    return {loja: df[df["codigo_loja"] == loja].reset_index(drop=True) for loja in lojas}


def por_dia(df: pd.DataFrame, inicio: dt.date, fim: dt.date) -> dict[dt.date, pd.DataFrame]:
    """Uma fatia por dia do intervalo, vazia nos dias sem movimento."""
    saida = {}
    dia = inicio
    while dia <= fim:
        saida[dia] = df[df["data"] == dia.isoformat()].reset_index(drop=True)
        dia += dt.timedelta(days=1)
    return saida
