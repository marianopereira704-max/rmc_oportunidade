"""Base de categorias do Pedido, do lado do banco (`categorias_ean`).

Três caminhos de entrada, todos na aba Dados → Categorias de produtos:
- carga inicial FEBRAFAR + CMED: arquivo montado por
  `python -m pedido.carga_categorias` e guardado no Spaces;
- planilha manual do admin (EAN + CATEGORIA) — passa por cima de tudo e
  vale na hora;
- (Fase 3 em diante) o Pedido só LÊ.

Regras e nomes das categorias: pedido/categorias.py.
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.models import CategoriaEan, OrigemCategoria
from core.sql import _insert_do_dialeto
from pedido import categorias
from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves

_LOTE_SQLITE = 5_000


# ---------------------------------------------------------------------------
# Carga inicial (FEBRAFAR + CMED)
# ---------------------------------------------------------------------------

@dataclass
class ResultadoCarga:
    eans_no_arquivo: int
    manuais_mantidas: int
    segundos: float


def carregar_base_inicial(session: Session, base: pd.DataFrame, usuario: str) -> ResultadoCarga:
    """Grava a base FEBRAFAR + CMED: insere o que falta e atualiza o que veio
    de uma carga anterior, mas NUNCA toca numa categoria MANUAL (o `WHERE` do
    ON CONFLICT). Não apaga nada: EAN que saiu da lista nova continua com a
    categoria que tinha — a base só cresce.

    No Postgres vai por COPY numa tabela temporária + um INSERT…SELECT: são
    235 mil linhas, e em lotes (mesmo de 500) seriam ~470 idas e voltas até
    NY. No SQLite (testes), lotes com o mesmo ON CONFLICT."""
    comeco = dt.datetime.now()
    agora = dt.datetime.utcnow()
    dados = pd.DataFrame({
        "ean": base["ean"].astype(str),
        "categoria": base["categoria"].astype(str),
        "origem": base["origem"].map(lambda o: OrigemCategoria[o].name),
        "descricao": base["descricao"].astype("string").str.slice(0, 200),
    }).drop_duplicates("ean")
    manuais = session.scalar(
        select(func.count()).select_from(CategoriaEan).where(CategoriaEan.origem == OrigemCategoria.MANUAL)
    ) or 0

    if session.get_bind().dialect.name == "postgresql":
        _copiar_postgres(session, dados, usuario, agora)
    else:
        tabela = CategoriaEan.__table__
        registros = dados.assign(atualizado_por=usuario, atualizado_em=agora)
        registros["origem"] = registros["origem"].map(lambda n: OrigemCategoria[n])
        registros = registros.astype(object).where(registros.notna(), None).to_dict("records")
        for i in range(0, len(registros), _LOTE_SQLITE):
            stmt = _insert_do_dialeto(session, tabela)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ean"],
                set_={c: getattr(stmt.excluded, c) for c in ("categoria", "origem", "descricao", "atualizado_por", "atualizado_em")},
                where=tabela.c.origem != OrigemCategoria.MANUAL,
            )
            session.execute(stmt, registros[i:i + _LOTE_SQLITE])
    session.flush()
    return ResultadoCarga(len(dados), int(manuais), (dt.datetime.now() - comeco).total_seconds())


def _copiar_postgres(session: Session, dados: pd.DataFrame, usuario: str, agora: dt.datetime) -> None:
    buffer = io.StringIO()
    dados.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    conexao = session.connection()
    conexao.execute(text(
        "CREATE TEMP TABLE _carga_categorias (ean varchar(20), categoria varchar(60), "
        "origem varchar(20), descricao varchar(200)) ON COMMIT DROP"
    ))
    cursor = conexao.connection.dbapi_connection.cursor()
    try:
        cursor.copy_expert("COPY _carga_categorias FROM STDIN WITH (FORMAT csv)", buffer)
    finally:
        cursor.close()
    conexao.execute(text(
        "INSERT INTO categorias_ean (ean, categoria, origem, descricao, atualizado_por, atualizado_em) "
        "SELECT ean, categoria, origem::origemcategoria, NULLIF(descricao, ''), :usuario, :agora "
        "FROM _carga_categorias "
        "ON CONFLICT (ean) DO UPDATE SET categoria = EXCLUDED.categoria, origem = EXCLUDED.origem, "
        "descricao = EXCLUDED.descricao, atualizado_por = EXCLUDED.atualizado_por, "
        "atualizado_em = EXCLUDED.atualizado_em "
        "WHERE categorias_ean.origem <> 'MANUAL'"
    ), {"usuario": usuario, "agora": agora})
    conexao.execute(text("DROP TABLE IF EXISTS _carga_categorias"))


def base_inicial_disponivel(armaz: Armazenamento, chaves: Chaves) -> dict | None:
    """Metadados do arquivo da carga inicial no Spaces, ou None se ainda não
    foi gerado (`python -m pedido.carga_categorias --executar`)."""
    if not armaz.existe(chaves.base_categorias()):
        return None
    try:
        return armaz.ler_json(chaves.base_categorias_info())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Planilha manual do admin
# ---------------------------------------------------------------------------

@dataclass
class PlanilhaManual:
    validas: pd.DataFrame                     # ean, categoria, descricao
    invalidas: list[tuple[int, str]] = field(default_factory=list)   # (linha da planilha, motivo)


def _coluna(df: pd.DataFrame, nomes: tuple[str, ...]) -> str | None:
    por_chave = {categorias._chave_texto(c): c for c in df.columns}
    return next((por_chave[n] for n in nomes if n in por_chave), None)


def ler_planilha_manual(df: pd.DataFrame) -> PlanilhaManual:
    """Planilha com EAN e CATEGORIA (é o próprio relatório "Sem
    Classificação" exportado, com a coluna CATEGORIA preenchida). Linha com
    categoria vazia é só ignorada (o admin não precisa classificar tudo de uma
    vez); categoria desconhecida vira erro, com a linha — nada de adivinhar,
    ela muda os dias de estoque de todas as lojas."""
    col_ean = _coluna(df, ("EAN", "CDEAN", "CODIGODEBARRAS", "CODIGOBARRAS"))
    col_cat = _coluna(df, ("CATEGORIA",))
    if col_ean is None or col_cat is None:
        raise ValueError(
            "A planilha precisa ter as colunas EAN e CATEGORIA. "
            f"Colunas encontradas: {', '.join(str(c) for c in df.columns)}"
        )
    col_desc = _coluna(df, ("PRODUTO", "DESCRICAO", "NOME", "DSPRODUTO"))

    i_ean, i_cat = df.columns.get_loc(col_ean), df.columns.get_loc(col_cat)
    i_desc = df.columns.get_loc(col_desc) if col_desc else None
    validas, invalidas = [], []
    for i, linha in enumerate(df.itertuples(index=False), start=2):  # linha 1 = cabeçalho
        valor = linha[i_cat]
        bruto_cat = "" if pd.isna(valor) else str(valor).strip()
        if not bruto_cat:
            continue
        ean = categorias.chave_ean(linha[i_ean])
        if not ean:
            invalidas.append((i, "EAN vazio ou inválido"))
            continue
        categoria = categorias.categoria_oficial(bruto_cat)
        if categoria is None or categoria not in categorias.CATEGORIAS_MANUAIS:
            invalidas.append((i, f"categoria desconhecida: \"{bruto_cat}\""))
            continue
        descricao = linha[i_desc] if i_desc is not None else None
        descricao = None if descricao is None or pd.isna(descricao) else str(descricao).strip()[:200] or None
        validas.append({"ean": ean, "categoria": categoria, "descricao": descricao})
    # EAN repetido na planilha: vale a última linha (quem classifica corrige embaixo).
    quadro = pd.DataFrame(validas, columns=["ean", "categoria", "descricao"]).drop_duplicates("ean", keep="last")
    return PlanilhaManual(quadro.reset_index(drop=True), invalidas)


def aplicar_manual(session: Session, validas: pd.DataFrame, usuario: str) -> int:
    """Grava as categorias MANUAIS — sobrescreve qualquer origem, inclusive
    outra manual anterior. Vale na hora (Q17 de 27/09)."""
    if validas.empty:
        return 0
    agora = dt.datetime.utcnow()
    registros = [
        {"ean": r.ean, "categoria": r.categoria, "origem": OrigemCategoria.MANUAL, "descricao": r.descricao,
         "atualizado_por": usuario, "atualizado_em": agora}
        for r in validas.itertuples(index=False)
    ]
    tabela = CategoriaEan.__table__
    for i in range(0, len(registros), _LOTE_SQLITE):
        stmt = _insert_do_dialeto(session, tabela)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ean"],
            set_={c: getattr(stmt.excluded, c) for c in ("categoria", "origem", "descricao", "atualizado_por", "atualizado_em")},
        )
        session.execute(stmt, registros[i:i + _LOTE_SQLITE])
    session.flush()
    return len(registros)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def versao(session: Session) -> tuple[int, str]:
    """Muda sempre que a base muda — chave do cache da tabela na tela."""
    total, ultima = session.execute(select(func.count(), func.max(CategoriaEan.atualizado_em))).one()
    return int(total or 0), str(ultima or "")


def tabela(session: Session, com_origem: bool = True) -> pd.DataFrame:
    """A base inteira: ean, categoria, origem (categorias como `category`:
    ~235 mil linhas ficam em poucos MB). `com_origem=False` (Assistente de
    pedido, que só usa ean + categoria): 825 ms em vez de 1.470 na carga
    (medido em 02/10/2026 — a conversão da origem linha a linha era o grosso)."""
    if not com_origem:
        linhas = session.execute(select(CategoriaEan.ean, CategoriaEan.categoria)).all()
        return pd.DataFrame(linhas, columns=["ean", "categoria"]).astype({"categoria": "category"})
    linhas = session.execute(select(CategoriaEan.ean, CategoriaEan.categoria, CategoriaEan.origem)).all()
    # O nome (FEBRAFAR), não o valor (febrafar) — convertido ANTES do
    # DataFrame: o pandas transforma o enum (subclasse de str) no valor.
    df = pd.DataFrame([(e, c, OrigemCategoria(o).name) for e, c, o in linhas], columns=["ean", "categoria", "origem"])
    return df.astype({"categoria": "category", "origem": "category"})


def contagens(session: Session) -> dict:
    por_origem = dict(session.execute(
        select(CategoriaEan.origem, func.count()).group_by(CategoriaEan.origem)
    ).all())
    por_categoria = dict(session.execute(
        select(CategoriaEan.categoria, func.count()).group_by(CategoriaEan.categoria)
    ).all())
    por_grupo: dict[str, int] = {}
    for cat, n in por_categoria.items():
        g = categorias.grupo(cat)
        por_grupo[g] = por_grupo.get(g, 0) + n
    return {
        "total": sum(por_categoria.values()),
        "por_origem": {o.name if isinstance(o, OrigemCategoria) else str(o): n for o, n in por_origem.items()},
        "por_grupo": por_grupo,
    }


def catalogos(armaz: Armazenamento, chaves: Chaves) -> pd.DataFrame:
    """Produtos de todas as lojas vinculadas (um arquivo por empresa, gravado
    pela rotina junto com o estoque), somados por EAN."""
    quadros = [armaz.ler_df(c) for c in armaz.listar(chaves.prefixo_catalogo()) if c.endswith(".parquet")]
    quadros = [q for q in quadros if not q.empty]
    if not quadros:
        return pd.DataFrame(columns=categorias.COLUNAS_CATALOGO)
    todos = pd.concat(quadros, ignore_index=True)
    for coluna in ("lojas_com_venda", "valor_venda"):  # catálogo gravado antes de 29/09/2026 não tem
        if coluna not in todos.columns:
            todos[coluna] = 0.0
        todos[coluna] = todos[coluna].fillna(0)
    return todos.groupby("ean", sort=False).agg(
        nome=("nome", "first"), laboratorio=("laboratorio", "first"),
        grupo_gps=("grupo_gps", "first"), categoria_gps=("categoria_gps", "first"),
        lojas=("lojas", "sum"), lojas_com_estoque=("lojas_com_estoque", "sum"),
        lojas_com_venda=("lojas_com_venda", "sum"), valor_venda=("valor_venda", "sum"),
    ).reset_index()


COLUNAS_RELATORIO = [
    "EAN", "PRODUTO", "LABORATORIO", "GRUPO NO GPS", "CATEGORIA NO GPS", "LOJAS", "LOJAS COM ESTOQUE",
    "LOJAS COM VENDA", "SITUACAO", "CATEGORIA",
]


def sem_classificacao(catalogo: pd.DataFrame, base: pd.DataFrame, so_com_estoque: bool = False) -> pd.DataFrame:
    """Produtos das lojas que não têm categoria (ou estão "EM CLASSIFICAÇÃO"
    na FEBRAFAR) — não recebem sugestão no Pedido até alguém classificar.
    A coluna CATEGORIA sai vazia: o admin preenche e envia de volta pela
    mesma tela. Ordem de pareto (01/10/2026): o EAN vendido em mais lojas
    primeiro — classificar os do topo resolve a maior parte das aparições
    (desempate: lojas com estoque)."""
    if catalogo.empty:
        return pd.DataFrame(columns=COLUNAS_RELATORIO)
    juntos = catalogo.merge(base[["ean", "categoria"]], on="ean", how="left")
    categoria = juntos["categoria"].astype("object")
    pendente = categoria.isna() | (categoria == categorias.EM_CLASSIFICACAO)
    if so_com_estoque:
        pendente &= juntos["lojas_com_estoque"].fillna(0) > 0
    p = juntos[pendente]
    saida = pd.DataFrame({
        "EAN": p["ean"],
        "PRODUTO": p["nome"],
        "LABORATORIO": p["laboratorio"],
        "GRUPO NO GPS": p["grupo_gps"],
        "CATEGORIA NO GPS": p["categoria_gps"],
        "LOJAS": p["lojas"].fillna(0).astype(int),
        "LOJAS COM ESTOQUE": p["lojas_com_estoque"].fillna(0).astype(int),
        "LOJAS COM VENDA": p["lojas_com_venda"].fillna(0).astype(int),
        "SITUACAO": categoria[pendente].map(
            lambda c: "Em classificação na FEBRAFAR" if c == categorias.EM_CLASSIFICACAO else "Não encontrado"),
        "CATEGORIA": "",
    })
    return saida.sort_values(["LOJAS COM VENDA", "LOJAS COM ESTOQUE", "PRODUTO"],
                             ascending=[False, False, True]).reset_index(drop=True)


def pareto_acumulado(valores: pd.Series) -> pd.Series:
    """% acumulado da coluna, na ordem em que ela está (já do maior pro
    menor): "classificando até aqui, resolvo X% das aparições"."""
    total = float(valores.sum())
    if total <= 0:
        return pd.Series(0.0, index=valores.index)
    return (valores.cumsum() / total * 100).round(1)
