"""Classificação de produtos em cascata (25/10/2026, design em docs/specs/2026-10-25-classificacao-produtos-design.md).

Cada produto cai em UMA de 5 categorias fixas (as mesmas da linha de compra: propagados, genéricos, similares,
perfumaria e "sem_linha", o resto, rotulado "Em classificação" na tela). De onde vem a categoria, em ordem:

1. a classificação MANUAL do admin, quando a categoria dela tem correspondência clara (`manual_para_categoria`);
2. o setor da IQVIA do EAN, pelo mapa `por_setor`;
3. o tipo da CMED do EAN, pelo mapa `por_cmed` (só Genérico e Similar classificam);
4. nenhuma das anteriores: "sem_linha".

A FEBRAFAR NÃO classifica ninguém (decisão de 25/10/2026: o MIP/OTC dela se analisa à parte e a IQVIA já conhece mais
de 98% dos EANs dela — medido na loja 60: a divisão do estoque a custo mudou 0,4 ponto). Ela só entra na cascata,
como informação de identificação. Por isso a FEBRAFAR fica fora das funções de decisão deste módulo.

Sem banco, sem Streamlit: recebe DataFrames e devolve DataFrames/dicts, para testar com tabelas pequenas. Quem lê as
tabelas (IQVIA, FEBRAFAR/CMED por EAN, categorias manuais) é a camada de integração.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pedido import categorias as cat
from pedido import linhas as lc

# As 5 categorias, na ordem em que a cascata as mostra.
CATEGORIAS = (lc.PROPAGADOS, lc.GENERICOS, lc.SIMILARES, lc.PERFUMARIA, lc.SEM_LINHA)
# As que de fato classificam: "sem_linha" é o que sobra, não se escolhe como destino de um setor ou tipo.
CATEGORIAS_REAIS = CATEGORIAS[:-1]

ORIGEM_MANUAL = "manual"
ORIGEM_IQVIA = "iqvia"
ORIGEM_CMED = "cmed"
ORIGEM_NENHUMA = "nenhuma"
# Quanto menor, mais forte: o produto com vários EANs vale pelo EAN de origem mais forte.
_FORCA = {ORIGEM_MANUAL: 0, ORIGEM_IQVIA: 1, ORIGEM_CMED: 2}

# Tipo de produto da CMED → categoria, pela chave do texto (sem acento, só letras e números). Os outros tipos
# (Novo, Biológico, Específico, Fitoterápico, Radiofármaco, Dinamizado) não classificam: o produto, se só a CMED o
# conhece, fica em "Em classificação" (D6 do design).
# O padrão do mapa editável, com o nome do tipo como a tela o escreve (a configuração guarda assim); a comparação
# usa a chave sem acento e sem caixa (`chave_tipo`).
PADRAO_CMED_TELA = {"Genérico": lc.GENERICOS, "Similar": lc.SIMILARES}
PADRAO_POR_CMED = {cat._chave_texto(k): v for k, v in PADRAO_CMED_TELA.items()}

# Categoria manual (nomes da base de categorias) → categoria. MIP/OTC e "Medicamento (CMED)" ficam de fora de
# propósito: MIP/OTC mistura marca e genérico e se analisa à parte; "Medicamento (CMED)" é rótulo provisório.
_MANUAL_FIXA = {
    cat.GENERICO: lc.GENERICOS,
    "PRESCRIÇÃO PROPAGADO": lc.PROPAGADOS,
    "PRESCRIÇÃO TRADE": lc.SIMILARES,
}


def chave_tipo(tipo) -> str:
    """O tipo da CMED como chave de comparação ("Genérico", "GENERICO " e "genérico" são o mesmo)."""
    return cat._chave_texto(tipo)


def manual_para_categoria(categoria: str | None) -> str | None:
    """A categoria da planilha manual do admin → categoria fixa, ou None se ela não tem correspondência clara."""
    if not categoria:
        return None
    if categoria in _MANUAL_FIXA:
        return _MANUAL_FIXA[categoria]
    if cat.grupo(categoria) == cat.PERFUMARIA:
        return lc.PERFUMARIA
    return None


def _mapa_setor(por_setor: dict | None) -> dict:
    return {**lc.PADRAO_POR_SETOR, **(por_setor or {})}


def _mapa_cmed(por_cmed: dict | None) -> dict:
    return {chave_tipo(k): v for k, v in {**PADRAO_POR_CMED, **(por_cmed or {})}.items()}


def categoria_do_ean(setor: str | None = None, cmed_tipo: str | None = None, manual: str | None = None,
                     por_setor: dict | None = None, por_cmed: dict | None = None) -> tuple[str, str]:
    """(categoria, origem) de um EAN pela ordem do módulo. Um setor ou tipo que o mapa leva para "sem_linha" não
    classifica: o EAN segue para a próxima fonte."""
    categoria = manual_para_categoria(manual)
    if categoria in CATEGORIAS_REAIS:
        return categoria, ORIGEM_MANUAL
    categoria = _mapa_setor(por_setor).get(setor) if isinstance(setor, str) else None
    if categoria in CATEGORIAS_REAIS:
        return categoria, ORIGEM_IQVIA
    categoria = _mapa_cmed(por_cmed).get(chave_tipo(cmed_tipo)) if isinstance(cmed_tipo, str) else None
    if categoria in CATEGORIAS_REAIS:
        return categoria, ORIGEM_CMED
    return lc.SEM_LINHA, ORIGEM_NENHUMA


def classificar(df: pd.DataFrame, por_setor: dict | None = None, por_cmed: dict | None = None) -> pd.DataFrame:
    """`categoria_do_ean` para a tabela inteira (~550 mil EANs). Colunas esperadas: `ean`, `setor_nec`,
    `cmed_tipo`, `manual`; as que faltarem contam como vazias. Devolve uma cópia com `categoria` e `origem`."""
    saida = df.copy()
    n = len(saida)
    vazio = pd.Series([None] * n, index=saida.index, dtype=object)
    setor = saida["setor_nec"] if "setor_nec" in saida else vazio
    tipo = saida["cmed_tipo"] if "cmed_tipo" in saida else vazio
    manual = saida["manual"] if "manual" in saida else vazio

    por_manual = manual.map(manual_para_categoria).where(lambda s: s.isin(CATEGORIAS_REAIS))
    por_iqvia = setor.map(_mapa_setor(por_setor)).where(lambda s: s.isin(CATEGORIAS_REAIS))
    por_cmed_ = tipo.map(lambda t: chave_tipo(t) if isinstance(t, str) else None).map(_mapa_cmed(por_cmed)) \
        .where(lambda s: s.isin(CATEGORIAS_REAIS))

    saida["categoria"] = np.select([por_manual.notna(), por_iqvia.notna(), por_cmed_.notna()],
                                   [por_manual, por_iqvia, por_cmed_], default=lc.SEM_LINHA)
    saida["origem"] = np.select([por_manual.notna(), por_iqvia.notna(), por_cmed_.notna()],
                                [ORIGEM_MANUAL, ORIGEM_IQVIA, ORIGEM_CMED], default=ORIGEM_NENHUMA)
    return saida


def categoria_do_produto(eans, principal, mapa: dict) -> str:
    """A categoria de um produto com um ou vários EANs (genérico da Base Genéricos). `mapa` = EAN → (categoria,
    origem) dos EANs que `classificar` deixou classificados. Vale o EAN de origem mais forte (manual, depois IQVIA,
    depois CMED); entre EANs da mesma origem, o primeiro, começando pelo principal. Nenhum classificado: "sem_linha"."""
    candidatos = ([principal] if isinstance(principal, str) else []) + list(eans if isinstance(eans, (list, tuple)) else [])
    melhor = None
    for e in candidatos:
        item = mapa.get(e)
        if item is None or item[1] not in _FORCA:
            continue
        if melhor is None or _FORCA[item[1]] < _FORCA[melhor[1]]:
            melhor = item
    return melhor[0] if melhor else lc.SEM_LINHA


def mapa_por_ean(classificado: pd.DataFrame) -> dict:
    """EAN → (categoria, origem) só dos EANs classificados (origem diferente de "nenhuma"): é o que o Assistente e o
    Dashboard guardam em memória no lugar do antigo EAN → setor."""
    c = classificado[classificado["origem"] != ORIGEM_NENHUMA]
    return dict(zip(c["ean"], zip(c["categoria"], c["origem"])))


def caminhos(classificado: pd.DataFrame, por_cmed: dict | None = None) -> pd.DataFrame:
    """O caminho de cada EAN na cascata, com as mesmas regras de `arvore`: `setor` (só quando a categoria veio da
    IQVIA), `febrafar` e `cmed` (o nome do tipo, só dos tipos que classificam). Vazio = o nível não existe para o
    EAN e a cascata o pula. Alinhado ao índice de `classificado`."""
    tipos_que_classificam = {k for k, v in _mapa_cmed(por_cmed).items() if v in CATEGORIAS_REAIS}
    vazio = pd.Series([None] * len(classificado), index=classificado.index, dtype=object)
    tipo = classificado["cmed_tipo"] if "cmed_tipo" in classificado else vazio
    setor = classificado["setor_nec"] if "setor_nec" in classificado else vazio
    return pd.DataFrame({
        "setor": setor.astype(object).where(classificado["origem"] == ORIGEM_IQVIA),
        "febrafar": classificado["febrafar"] if "febrafar" in classificado else vazio,
        "cmed": tipo.map(lambda t: str(t).strip().capitalize()
                         if isinstance(t, str) and chave_tipo(t) in tipos_que_classificam else None),
    }, index=classificado.index)


def tabela_por_ean(classificado: pd.DataFrame, por_cmed: dict | None = None) -> pd.DataFrame:
    """Todos os EANs que alguma fonte conhece, indexados pelo EAN, com `categoria`, `origem` e o caminho na cascata
    (`setor`, `febrafar`, `cmed` — `caminhos`). É o que o app guarda em cache, uma vez por versão das fontes e por
    mapa: o Assistente e o Dashboard tiram dela a categoria; a tabela "Análise por categoria" do Estoque, também o
    caminho (26/10/2026). Tudo como categorias do pandas: poucos valores distintos em ~270 mil linhas."""
    c = pd.concat([classificado[["ean", "categoria", "origem"]], caminhos(classificado, por_cmed)], axis=1)
    c = c.drop_duplicates("ean").set_index("ean")
    return c.astype({k: "category" for k in ("categoria", "origem", "setor", "febrafar", "cmed")})


def mapa_da_loja(tabela: pd.DataFrame, eans) -> dict:
    """Só os EANs pedidos (os da loja), em dicionário EAN → (categoria, origem): é o formato de
    `categoria_do_produto` (que ignora a origem "nenhuma"). A busca por produto é EAN a EAN, e um dicionário
    pequeno é mais rápido que o índice de ~270 mil linhas."""
    if tabela is None or tabela.empty:
        return {}
    sub = tabela.reindex(list(set(e for e in eans if isinstance(e, str))))
    sub = sub[sub["categoria"].notna()]
    return dict(zip(sub.index, zip(sub["categoria"].astype(str), sub["origem"].astype(str))))


def caminhos_da_loja(tabela: pd.DataFrame, eans) -> pd.DataFrame:
    """O caminho na cascata (`setor`, `febrafar`, `cmed`) só dos EANs pedidos; EAN que nenhuma fonte conhece não
    aparece."""
    colunas = ["setor", "febrafar", "cmed"]
    if tabela is None or tabela.empty or not {*colunas} <= set(tabela.columns):
        return pd.DataFrame(columns=colunas)
    sub = tabela.reindex(list(set(e for e in eans if isinstance(e, str))))
    return sub.loc[sub["categoria"].notna(), colunas].astype(object)


def eans_da_tabela(df: pd.DataFrame) -> set:
    """Todos os EANs de uma tabela de produtos do Assistente (colunas `eans` e `ean_principal`)."""
    if df.empty:
        return set()
    eans: set = set()
    if "eans" in df:
        eans = {e for lista in df["eans"] for e in (lista if isinstance(lista, (list, tuple)) else [])}
    if "ean_principal" in df:
        eans |= set(df["ean_principal"].dropna())
    return eans


def filtrar(df: pd.DataFrame, linhas, mapa: dict | None = None) -> pd.DataFrame:
    """Só as categorias (linhas de compra) do Comprador + "Em classificação" (`sem_linha`), que aparece para todos
    os compradores para nenhum produto sumir de todo mundo. `linhas` vazio = sem corte: é o Proprietário, ou um
    Comprador antigo sem linha gravada (continua vendo tudo, em vez de uma tela vazia). Substitui `linhas.filtrar`."""
    escolhidas = set(linhas or ())
    if not escolhidas or df.empty:
        return df
    return df[coluna(df, mapa).isin(escolhidas | {lc.SEM_LINHA})]


def coluna(df: pd.DataFrame, mapa: dict | None = None) -> pd.Series:
    """A categoria de cada produto da tabela do Assistente (colunas `eans` e `ean_principal` do cálculo). Mesmo
    papel de `linhas.coluna`, que esta função substitui na C5 do plano."""
    if df.empty:
        return pd.Series([], index=df.index, dtype=object)
    mapa = mapa or {}
    eans = df["eans"] if "eans" in df else [None] * len(df)
    principais = df["ean_principal"] if "ean_principal" in df else [None] * len(df)
    return pd.Series([categoria_do_produto(e, p, mapa) for e, p in zip(eans, principais)], index=df.index)


# ---------------------------------------------------------------------------
# A cascata
# ---------------------------------------------------------------------------
# Categoria → setor da IQVIA → categoria da FEBRAFAR → tipo da CMED. Cada nível é opcional por EAN: o que o EAN não
# tem, o caminho pula (um EAN sem IQVIA desce direto à FEBRAFAR, sob a categoria). Cada nó conta EANs distintos.

NIVEL_CATEGORIA, NIVEL_IQVIA, NIVEL_FEBRAFAR, NIVEL_CMED = "categoria", "iqvia", "febrafar", "cmed"


def _no(nivel: str, chave: str, rotulo: str) -> dict:
    return {"nivel": nivel, "chave": chave, "rotulo": rotulo, "eans": 0, "filhos": {}}


def _inserir(raiz: dict, caminho: list[tuple[str, str, str]], n: int) -> None:
    no = raiz
    no["eans"] += n
    for nivel, chave, rotulo in caminho:
        filho = no["filhos"].setdefault((nivel, chave), _no(nivel, chave, rotulo))
        filho["eans"] += n
        no = filho


def _finalizar(no: dict) -> dict:
    """Os filhos viram lista, do maior para o menor (empate: pelo nome)."""
    filhos = [_finalizar(f) for f in no["filhos"].values()]
    filhos.sort(key=lambda f: (-f["eans"], f["rotulo"]))
    return {**no, "filhos": filhos}


def arvore(classificado: pd.DataFrame, por_cmed: dict | None = None) -> list[dict]:
    """A cascata das 5 categorias. `classificado` = saída de `classificar`, com as colunas `setor_nec`, `febrafar`
    e `cmed_tipo` (o universo é a união dos EANs de todas as fontes). Regras (design, seção 4.4):

    - nível 2 (IQVIA): só o EAN cuja categoria veio da IQVIA, sob o setor que o mapa leva àquela categoria;
    - nível 3 (FEBRAFAR): qualquer EAN que tenha categoria da FEBRAFAR; sem IQVIA, sob a própria categoria. Em
      "sem_linha" entram os que só a FEBRAFAR conhece (é a lista que o admin precisa ver);
    - nível 4 (CMED): só os tipos que classificam (Genérico e Similar, pelo mapa).

    Agrupa antes de montar (a base tem ~550 mil EANs e só alguns milhares de caminhos diferentes)."""
    tipos_que_classificam = {k for k, v in _mapa_cmed(por_cmed).items() if v in CATEGORIAS_REAIS}
    vazio = pd.Series([None] * len(classificado), index=classificado.index, dtype=object)
    d = pd.DataFrame({
        "categoria": classificado["categoria"],
        "setor": classificado["setor_nec"].where(classificado["origem"] == ORIGEM_IQVIA) if "setor_nec" in classificado else vazio,
        "febrafar": classificado["febrafar"] if "febrafar" in classificado else vazio,
        "cmed": (classificado["cmed_tipo"] if "cmed_tipo" in classificado else vazio)
        .map(lambda t: chave_tipo(t) if isinstance(t, str) else None).where(lambda s: s.isin(tipos_que_classificam)),
    })
    # O nome do tipo na tela: o 1º texto que apareceu para cada chave ("Genérico", não "GENERICO").
    nome_tipo = {}
    if "cmed_tipo" in classificado:
        for t in classificado["cmed_tipo"].dropna().unique():
            nome_tipo.setdefault(chave_tipo(t), str(t).strip().capitalize())
    raizes = {c: _no(NIVEL_CATEGORIA, c, lc.ROTULOS.get(c, c)) for c in CATEGORIAS}
    grupos = d.groupby(["categoria", "setor", "febrafar", "cmed"], dropna=False, sort=False).size()
    for (categoria, setor, febrafar, cmed), n in grupos.items():
        caminho: list[tuple[str, str, str]] = []
        if isinstance(setor, str):
            caminho.append((NIVEL_IQVIA, setor, lc.SETORES.get(setor, setor)))
        if isinstance(febrafar, str) and febrafar:
            caminho.append((NIVEL_FEBRAFAR, febrafar, febrafar))
        if isinstance(cmed, str):
            caminho.append((NIVEL_CMED, cmed, nome_tipo.get(cmed, cmed.capitalize())))
        _inserir(raizes[categoria], caminho, int(n))
    return [_finalizar(raizes[c]) for c in CATEGORIAS]
