"""Linhas de compra do Comprador no Assistente de pedido (07/10/2026, etapas
4 e 5 do login individual) — os CÓDIGOS, os rótulos e os setores da IQVIA.

O Comprador vê SÓ as linhas dele (Q9 B do grill de 06/10/2026: o resto não
existe pra ele — nem na lista, nem nos cards, nem no orçamento, nem na
exportação). O Proprietário vê a loja inteira (Q22). Produto "Em
classificação" aparece para todos os compradores, pra nenhum produto sumir
de todo mundo.

De onde vem a linha de cada produto (desde 25/10/2026): da cascata de
classificação, `pedido/classificacao.py` — manual do admin, setor da IQVIA,
tipo da CMED (só Genérico e Similar), senão "Em classificação". A FEBRAFAR
não classifica mais (a reserva pela base de categorias da Q36 A saiu), e a
correspondência não se personaliza por loja. Aqui ficaram os códigos (que
estão gravados nos usuários), os rótulos, os setores da IQVIA e o mapa
padrão setor → linha.
"""
from __future__ import annotations

import pandas as pd

from pedido import categorias as cat

GENERICOS = "genericos"
SIMILARES = "similares"
PROPAGADOS = "propagados"
PERFUMARIA = "perfumaria"
SEM_LINHA = "sem_linha"
# Rótulos no singular e "Em classificação" para o resto (25/10/2026, cascata de classificação). Os CÓDIGOS não mudam:
# estão gravados nos usuários (Comprador) e nas configurações.
ROTULOS = {GENERICOS: "Genérico", SIMILARES: "Similar", PROPAGADOS: "Propagado", PERFUMARIA: "Perfumaria",
           SEM_LINHA: "Em classificação"}

# SETOR_NEC do catálogo → o que é (conferido no próprio catálogo pela área da
# farmácia, 07/10/2026). Os NAO_MEDICAMENTO_OTC ficam quase todos na área
# "MIP" do catálogo (22.931 de 24.305): são produtos de balcão sem registro
# de medicamento.
SETORES = {
    "RX_GENERICO": "Genérico de prescrição",
    "MIP_GENERICO": "Genérico isento de prescrição (MIP)",
    "RX_PROMOVIDO": "Prescrição de marca (ético)",
    "MIP_MARCA": "MIP de marca",
    "RX_TRADE": "Prescrição de trade (similar)",
    "MIP_TRADE": "MIP de trade (similar)",
    "NAO_MEDICAMENTO_PEC": "Higiene pessoal, perfumaria e cosméticos",
    "NAO_MEDICAMENTO_PAC": "Cuidados ao paciente e ao bebê",
    "NAO_MEDICAMENTO_NTR": "Alimentos, bebidas e conveniência",
    "NAO_MEDICAMENTO_OTC": "MIP",   # nome curto pedido pelo Mariano (26/10/2026)
}
# O que cada linha inclui (Q35, corrigida pelo Mariano em 06/10/2026).
PADRAO_POR_SETOR = {
    "RX_GENERICO": GENERICOS, "MIP_GENERICO": GENERICOS,
    "RX_PROMOVIDO": PROPAGADOS, "MIP_MARCA": PROPAGADOS,
    "RX_TRADE": SIMILARES, "MIP_TRADE": SIMILARES,
    "NAO_MEDICAMENTO_PEC": PERFUMARIA, "NAO_MEDICAMENTO_PAC": PERFUMARIA,
    "NAO_MEDICAMENTO_NTR": PERFUMARIA, "NAO_MEDICAMENTO_OTC": PERFUMARIA,
}


def montar_catalogo(bruto: pd.DataFrame) -> pd.DataFrame:
    """Planilha do catálogo (colunas EAN, SETOR_NEC, GMRS) → ean (chave
    normalizada), setor_nec, gmrs. Sai: EAN vazio (357 no arquivo de 2026),
    setor desconhecido e EAN repetido (fica a 1ª ocorrência)."""
    df = pd.DataFrame({
        "ean": cat.chaves_ean(bruto["EAN"]),
        "setor_nec": bruto["SETOR_NEC"].astype("string").str.strip().str.upper(),
        "gmrs": bruto["GMRS"].astype("string").str.strip().str.upper().str.slice(0, 2),
    })
    df = df[df["ean"].notna() & df["setor_nec"].isin(list(SETORES))]
    vazio = df["gmrs"].isna() | (df["gmrs"] == "")
    df["gmrs"] = df["gmrs"].astype(object).where(~vazio.fillna(True), None)
    return df.drop_duplicates("ean").reset_index(drop=True)
