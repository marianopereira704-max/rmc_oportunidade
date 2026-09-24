"""Primitivas de SQL que precisam saber qual banco está por trás.

O projeto roda em SQLite (desenvolvimento e testes) e Postgres (produção) —
ver core/config.py. A quase totalidade do código não precisa saber a
diferença, porque o SQLAlchemy abstrai. As poucas exceções moram aqui, pra
não ficarem duplicadas em cada módulo que precisa delas.
"""
from __future__ import annotations

from sqlalchemy.orm import Session


def _insert_do_dialeto(session: Session, tabela):
    dialeto = session.get_bind().dialect.name
    if dialeto == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as insert_dialeto
    elif dialeto == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as insert_dialeto
    else:
        raise NotImplementedError(
            f"upsert atômico de {tabela.name} não implementado para o dialeto {dialeto!r} "
            "(só Postgres e SQLite têm ON CONFLICT usado aqui)."
        )
    return insert_dialeto(tabela)


def insert_com_atualizacao(
    session: Session, tabela, index_elements: list[str], colunas_atualizadas: list[str]
):
    """`INSERT ... ON CONFLICT (chave) DO UPDATE SET ...` — insere o que não
    existe e atualiza o que já existe, numa instrução só.

    Substitui o padrão "para cada linha: consulta se existe, insere ou
    atualiza", que custa uma ida ao banco POR LINHA (e ainda deixa uma janela
    de corrida entre a consulta e a escrita).

    `colunas_atualizadas` é explícita, e isso é essencial: só as colunas
    listadas são sobrescritas quando a linha já existe. Colunas que a origem
    não conhece (ex: `lojas.grupo_economico`, preenchido por outra decisão)
    precisam ficar de FORA da lista, senão cada sincronização apagaria um dado
    que não é dela.

    Atenção ao chamar com uma lista de linhas: a mesma chave não pode aparecer
    duas vezes no mesmo lote — o Postgres recusa com "ON CONFLICT DO UPDATE
    command cannot affect row a second time". Quem monta o lote precisa
    deduplicar antes (ver `integrations/sistema_interno.py`)."""
    stmt = _insert_do_dialeto(session, tabela)
    return stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_={coluna: getattr(stmt.excluded, coluna) for coluna in colunas_atualizadas},
    )


def insert_ignorando_conflito(session: Session, tabela, index_elements: list[str]):
    """`on_conflict_do_nothing()` não é genérico no Core do SQLAlchemy — cada
    dialeto expõe sua PRÓPRIA função `insert()` com esse método (Postgres e
    SQLite; os dois únicos bancos que este projeto roda — ver core/config.py).
    Esta função escolhe a `insert()` certa a partir do dialeto da conexão em
    uso, pra todo o resto do código chamar sempre a mesma API sem saber qual
    banco está por trás — é o que permite os MESMOS testes (SQLite) validarem
    o comportamento que roda em produção (Postgres).

    Usada em toda inserção que precisa ser ATÔMICA contra corrida: em vez de
    "consulta, se não existe insere" (duas idas ao banco, com uma janela no
    meio em que outro processo pode inserir a mesma chave e provocar
    IntegrityError), o próprio banco resolve o conflito numa instrução só.
    """
    dialeto = session.get_bind().dialect.name
    if dialeto == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as insert_dialeto
    elif dialeto == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as insert_dialeto
    else:
        raise NotImplementedError(
            f"upsert atômico de {tabela.name} não implementado para o dialeto {dialeto!r} "
            "(só Postgres e SQLite têm on_conflict_do_nothing usado aqui)."
        )
    return insert_dialeto(tabela).on_conflict_do_nothing(index_elements=index_elements)
