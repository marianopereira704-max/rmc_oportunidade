"""fila de EAN: origem nova LOJA_API (genérico vendido pela loja, fora da Base Genéricos)

Acrescenta o valor 'LOJA_API' ao tipo enum `origemfila` do Postgres. No
SQLite (testes/dev) o enum é texto, nada a fazer.

`ALTER TYPE … ADD VALUE` roda dentro de transação a partir do Postgres 12
(o servidor é 16.15, conferido em 29/09/2026); só não se pode USAR o valor
novo na mesma transação, e esta migração não usa. Sem `autocommit_block`:
as migrações rodam dentro do `engine.begin()` de core/db.py, e interromper
essa transação no meio seria frágil. Só acrescenta — nenhum valor antigo muda, e o
app publicado com o código anterior usa OUTRO banco (rmc_oportunidades),
que só recebe esta migração junto com o código novo.

Revision ID: 0010_origem_fila_loja_api
Revises: 0009_pedido_rascunho_acesso
Create Date: 2026-09-29 09:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0010_origem_fila_loja_api'
down_revision: Union[str, None] = '0009_pedido_rascunho_acesso'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ANTIGO = sa.Enum('GPS', 'GRUPPY', name='origemfila')
_NOVO = sa.Enum('GPS', 'GRUPPY', 'LOJA_API', name='origemfila')


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE origemfila ADD VALUE IF NOT EXISTS 'LOJA_API'")
        return
    # SQLite: o enum é VARCHAR do tamanho do maior nome (GRUPPY = 6); LOJA_API
    # tem 8. O SQLite não corta, mas o esquema precisa bater com o modelo.
    for tabela, coluna in (("fila_resolucao_ean", "origem"), ("ultimos_mapeamentos_coluna", "fornecedor")):
        with op.batch_alter_table(tabela, schema=None) as batch_op:
            batch_op.alter_column(coluna, existing_type=_ANTIGO, type_=_NOVO, existing_nullable=False)


def downgrade() -> None:
    # Postgres não remove valor de enum; fica (inofensivo).
    pass
