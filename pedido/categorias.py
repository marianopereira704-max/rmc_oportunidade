"""Categorias de produto do Pedido: nomes, grupos e a montagem da base
inicial (FEBRAFAR + CMED). Sem banco aqui — a gravação fica em
integrations/categorias.py.

Por que existe (decisões de 26–27/09/2026, docs/mapa_api_gps.md §4): a API
do GPS não traz categoria padronizada (NomeGrupo/NomeCategoria são livres,
cada rede cadastra de um jeito). A categoria decide os dias de estoque da
sugestão — medicamento 7, perfumaria 15 — e se o produto entra nela: "Sem
Classificação" (inclui o "EM CLASSIFICAÇÃO" da FEBRAFAR) não recebe
sugestão, até o admin classificar.

Fontes:
- FEBRAFAR (planilha CATEGORIAS_FEBRAFAR.xlsx, 221.564 EANs, coluna
  CATEGORIA) — dá os nomes das categorias usados em todo o sistema;
- CMED/ANVISA (lista de preços de medicamentos, gov.br) — coluna "TIPO DE
  PRODUTO": Genérico, Similar, Novo…

Regra de junção (por EAN):
1. CMED "Genérico" → PRESCRIÇÃO GENÉRICO, mesmo que a FEBRAFAR diga outra
   coisa (genérico = genérico na CMED OU na FEBRAFAR — Q17 de 27/09).
2. Senão, a categoria da FEBRAFAR, se não for "EM CLASSIFICAÇÃO".
3. Senão, outro tipo da CMED (Similar, Novo, Biológico…) → MEDICAMENTO
   (CMED): sabemos que é medicamento — os dias de estoque saem certos —,
   mas não se é de prescrição ou MIP, e inventar uma das categorias da
   FEBRAFAR seria afirmar o que a fonte não diz.
4. Senão, "EM CLASSIFICAÇÃO" da FEBRAFAR fica gravado como está (conta como
   Sem Classificação) — pro relatório mostrar que a FEBRAFAR conhece o EAN.
Categoria MANUAL (envio do admin) passa por cima de tudo, sempre.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

MEDICAMENTO = "medicamento"
PERFUMARIA = "perfumaria"
SEM_CLASSIFICACAO = "sem_classificacao"

ROTULO_GRUPO = {
    MEDICAMENTO: "Medicamento",
    PERFUMARIA: "Perfumaria",
    SEM_CLASSIFICACAO: "Sem Classificação",
}

GENERICO = "PRESCRIÇÃO GENÉRICO"
MEDICAMENTO_CMED = "MEDICAMENTO (CMED)"
EM_CLASSIFICACAO = "EM CLASSIFICAÇÃO"

# Nome oficial → grupo. Os nomes são os da FEBRAFAR, só com acento e
# pontuação consertados ("MIP l OTC", "CONVENIENCIA" vêm assim no arquivo).
# Nutrição e Infantil ficam em perfumaria por enquanto (Q26 de 27/09).
CATEGORIAS: dict[str, str] = {
    GENERICO: MEDICAMENTO,
    "PRESCRIÇÃO PROPAGADO": MEDICAMENTO,
    "PRESCRIÇÃO TRADE": MEDICAMENTO,
    "MIP/OTC": MEDICAMENTO,
    MEDICAMENTO_CMED: MEDICAMENTO,
    "BELEZA PELE CABELOS": PERFUMARIA,
    "HIGIENE": PERFUMARIA,
    "CUIDADOS": PERFUMARIA,
    "NUTRIÇÃO E SUPLEMENTOS ALIMENTARES": PERFUMARIA,
    "INFANTIL": PERFUMARIA,
    "ALIMENTOS E BEBIDAS": PERFUMARIA,
    "CONVENIÊNCIA": PERFUMARIA,
    EM_CLASSIFICACAO: SEM_CLASSIFICACAO,
}

# Categorias que o admin pode enviar na planilha manual: todas menos "EM
# CLASSIFICAÇÃO" (enviar isso não classificaria nada).
CATEGORIAS_MANUAIS = [c for c in CATEGORIAS if c != EM_CLASSIFICACAO]


def _chave_texto(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", t.upper())


# Grafias aceitas na planilha manual e nas fontes. "MIPLOTC" é o "MIP l OTC"
# da FEBRAFAR (um "l" no lugar da barra).
_APELIDOS = {
    "MIPLOTC": "MIP/OTC", "MIP": "MIP/OTC", "OTC": "MIP/OTC",
    "GENERICO": GENERICO, "MEDICAMENTOGENERICO": GENERICO,
    "MEDICAMENTO": MEDICAMENTO_CMED,
}
_POR_CHAVE = {_chave_texto(c): c for c in CATEGORIAS}


def categoria_oficial(texto) -> str | None:
    """Texto livre ("mip l otc", "Conveniencia", "PRESCRIÇÃO GENERICO") → nome
    oficial, ou None se não for categoria conhecida. Sem fuzzy de propósito:
    categoria errada muda os dias de estoque de todas as lojas."""
    chave = _chave_texto(texto)
    if not chave:
        return None
    if chave in _APELIDOS:
        return _APELIDOS[chave]
    return _POR_CHAVE.get(chave)


def grupo(categoria: str | None) -> str:
    return CATEGORIAS.get(categoria or "", SEM_CLASSIFICACAO)


def e_generico(categoria: str | None) -> bool:
    return categoria == GENERICO


def chave_ean(valor) -> str | None:
    """Só dígitos, sem zeros à esquerda. A lista da CMED e algumas planilhas
    trazem GTIN-14 ("07896…") onde o GPS tem o EAN-13 ("7896…"); sem tirar o
    zero, o mesmo produto não se encontrava. Aceita float do Excel
    (7896422507295.0)."""
    if valor is None:
        return None
    if isinstance(valor, float):
        if valor != valor:  # NaN
            return None
        valor = int(round(valor))
    texto = str(valor).strip()
    if re.fullmatch(r"\d+\.0+", texto):
        texto = texto.split(".")[0]
    chave = re.sub(r"\D", "", texto).lstrip("0")
    return chave or None


def chaves_ean(serie: pd.Series) -> pd.Series:
    """`chave_ean` vetorizado (a base tem 250 mil linhas)."""
    s = serie.astype("string").str.strip().str.replace(r"\.0+$", "", regex=True)
    s = s.str.replace(r"\D", "", regex=True).str.lstrip("0")
    return s.mask(s == "")


# ---------------------------------------------------------------------------
# Leitura das fontes
# ---------------------------------------------------------------------------

def ler_febrafar(caminho: str | Path) -> pd.DataFrame:
    """CATEGORIAS_FEBRAFAR.xlsx → ean, categoria, descricao (categoria já no
    nome oficial; linha sem EAN ou com categoria desconhecida sai)."""
    bruto = pd.read_excel(caminho, dtype=str)
    colunas = {_chave_texto(c): c for c in bruto.columns}
    for obrigatoria in ("CDEAN", "CATEGORIA"):
        if obrigatoria not in colunas:
            raise ValueError(f"FEBRAFAR: coluna {obrigatoria} não encontrada (colunas: {list(bruto.columns)})")
    df = pd.DataFrame({
        "ean": chaves_ean(bruto[colunas["CDEAN"]]),
        "categoria": bruto[colunas["CATEGORIA"]].map(categoria_oficial),
        "descricao": bruto[colunas["DSPRODUTO"]].str.strip() if "DSPRODUTO" in colunas else None,
    })
    return df.dropna(subset=["ean", "categoria"]).reset_index(drop=True)


def ler_cmed(caminho: str | Path) -> pd.DataFrame:
    """Lista de preços da CMED (xlsx do gov.br) → ean, tipo, descricao. O
    arquivo tem ~40 linhas de apresentação antes do cabeçalho; procura a
    linha que tem "EAN 1". Cada apresentação tem até 3 EANs."""
    topo = pd.read_excel(caminho, header=None, nrows=80, dtype=str)
    linha = next((i for i in range(len(topo))
                  if any(_chave_texto(v) == "EAN1" for v in topo.iloc[i].tolist())), None)
    if linha is None:
        raise ValueError("CMED: cabeçalho com a coluna 'EAN 1' não encontrado nas 80 primeiras linhas")
    bruto = pd.read_excel(caminho, header=linha, dtype=str)
    colunas = {_chave_texto(c): c for c in bruto.columns}
    tipo = next((c for k, c in colunas.items() if k.startswith("TIPODEPRODUTO")), None)
    if tipo is None:
        raise ValueError("CMED: coluna 'TIPO DE PRODUTO' não encontrada")
    descricao = bruto[colunas["PRODUTO"]].fillna("").str.strip()
    if "APRESENTACAO" in colunas:
        descricao = descricao + " " + bruto[colunas["APRESENTACAO"]].fillna("").str.strip()
    partes = []
    for col in ("EAN1", "EAN2", "EAN3"):
        if col in colunas:
            partes.append(pd.DataFrame({
                "ean": chaves_ean(bruto[colunas[col]]),
                "tipo": bruto[tipo].fillna("").str.strip(),
                "descricao": descricao.str.strip(),
            }))
    df = pd.concat(partes, ignore_index=True).dropna(subset=["ean"])
    return df[df["tipo"].map(_chave_texto) != ""].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Junção
# ---------------------------------------------------------------------------

def montar_base_inicial(febrafar: pd.DataFrame, cmed: pd.DataFrame) -> pd.DataFrame:
    """Aplica a regra de junção do topo do módulo. Devolve uma linha por EAN:
    ean, categoria, origem ("FEBRAFAR"/"CMED"), descricao."""
    # EAN repetido na FEBRAFAR (225 casos): fica a categoria de verdade antes
    # do "EM CLASSIFICAÇÃO"; entre duas de verdade, a primeira do arquivo.
    f = febrafar.assign(_pendente=febrafar["categoria"] == EM_CLASSIFICACAO)
    f = f.sort_values("_pendente", kind="stable").drop_duplicates("ean").drop(columns="_pendente")
    f = f.assign(origem="FEBRAFAR")

    c = cmed.assign(_generico=cmed["tipo"].map(_chave_texto) == "GENERICO")
    c = c.sort_values("_generico", ascending=False, kind="stable").drop_duplicates("ean")
    c = c.assign(categoria=c["_generico"].map({True: GENERICO, False: MEDICAMENTO_CMED}), origem="CMED")
    c = c[["ean", "categoria", "origem", "descricao"]]

    juntos = f.merge(c, on="ean", how="outer", suffixes=("_f", "_c"))
    f_ok = juntos["categoria_f"].notna() & (juntos["categoria_f"] != EM_CLASSIFICACAO)
    usa_cmed = (juntos["categoria_c"] == GENERICO) | (juntos["categoria_c"].notna() & ~f_ok)
    base = pd.DataFrame({
        "ean": juntos["ean"],
        "categoria": juntos["categoria_c"].where(usa_cmed, juntos["categoria_f"]),
        "origem": juntos["origem_c"].where(usa_cmed, juntos["origem_f"]),
        "descricao": juntos["descricao_c"].where(usa_cmed, juntos["descricao_f"]),
    })
    base["descricao"] = base["descricao"].astype("string").str.slice(0, 200)
    return base.sort_values("ean").reset_index(drop=True)


def resumo(base: pd.DataFrame) -> dict:
    """Contagens pra prévia e pro arquivo de metadados (sem nome de produto:
    o log da rotina é público)."""
    return {
        "eans": int(len(base)),
        "por_origem": {k: int(v) for k, v in base["origem"].value_counts().items()},
        "por_categoria": {k: int(v) for k, v in base["categoria"].value_counts().items()},
        "por_grupo": {k: int(v) for k, v in base["categoria"].map(grupo).value_counts().items()},
    }


# ---------------------------------------------------------------------------
# Catálogo de produtos das lojas (gravado pela rotina, lido no relatório)
# ---------------------------------------------------------------------------

COLUNAS_CATALOGO = ["ean", "nome", "laboratorio", "grupo_gps", "categoria_gps", "lojas", "lojas_com_estoque",
                    "lojas_com_venda", "valor_venda"]


def catalogo(estoque: pd.DataFrame, lojas: list[str]) -> pd.DataFrame:
    """Estoque da empresa (todas as lojas) → um produto por EAN, só das lojas
    vinculadas: quantas lojas têm o produto no cadastro e quantas têm estoque
    positivo. É o que o relatório "Sem Classificação" lista — ler 1 arquivo
    por empresa em vez de 1 por loja (341) deixa o relatório em segundos."""
    df = estoque[estoque["codigo_loja"].isin(lojas)].copy()
    df["ean"] = chaves_ean(df["ean"])
    df = df.dropna(subset=["ean"])
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64" if c.startswith("lojas") else "string")
                             for c in COLUNAS_CATALOGO})
    df["_com_estoque"] = df["qtd_estoque"].fillna(0) > 0
    agrupado = df.groupby("ean", sort=True).agg(
        nome=("nome", "first"),
        laboratorio=("laboratorio", "first"),
        grupo_gps=("grupo", "first"),
        categoria_gps=("categoria_gps", "first"),
        lojas=("codigo_loja", "nunique"),
        lojas_com_estoque=("_com_estoque", "sum"),
    ).reset_index()
    agrupado["lojas_com_venda"] = 0.0      # preenchidas por completar_com_vendas (fim da rotina)
    agrupado["valor_venda"] = 0.0
    agrupado["lojas"] = agrupado["lojas"].astype("float64")
    agrupado["lojas_com_estoque"] = agrupado["lojas_com_estoque"].astype("float64")
    for c in ("ean", "nome", "laboratorio", "grupo_gps", "categoria_gps"):
        agrupado[c] = agrupado[c].astype("string")
    return agrupado[COLUNAS_CATALOGO]


def completar_com_vendas(catalogo_df: pd.DataFrame, vendas_das_lojas: list[pd.DataFrame]) -> pd.DataFrame:
    """Acrescenta ao catálogo da empresa, por EAN, em quantas lojas ele
    VENDEU na janela e o valor vendido somado (`pronto.vendas_por_ean` de
    cada loja). É o que separa "genérico que a loja vende" (vai pra fila de
    EAN como "Loja (API)") de "genérico só cadastrado"."""
    df = catalogo_df.drop(columns=["lojas_com_venda", "valor_venda"], errors="ignore")
    vendidos = [v[v["unidades"] > 0] for v in vendas_das_lojas if not v.empty]
    if vendidos:
        todas = pd.concat(vendidos, ignore_index=True)
        resumo = todas.groupby("ean").agg(lojas_com_venda=("ean", "size"), valor_venda=("valor", "sum")).reset_index()
        df = df.merge(resumo, on="ean", how="left")
    df["lojas_com_venda"] = df.get("lojas_com_venda", pd.Series(0.0, index=df.index)).fillna(0).astype("float64")
    df["valor_venda"] = df.get("valor_venda", pd.Series(0.0, index=df.index)).fillna(0).astype("float64")
    return df[COLUNAS_CATALOGO]
