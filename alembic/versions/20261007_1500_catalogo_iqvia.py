"""Catálogo IQVIA: linha de compra de cada EAN (e genérico pelo GMRS)

07/10/2026, etapa 5 do login individual. Tabela nova `catalogo_iqvia` (EAN →
SETOR_NEC e GMRS), carregada pelo botão em Dados a partir do arquivo que
`python -m pedido.carga_catalogo` monta no Spaces. E o valor 'IQVIA' no tipo
enum `origemcategoria`: o EAN que o catálogo diz ser genérico (GMRS = G) e
que a base de categorias não conhece entra nela como "Prescrição genérico"
com essa origem (Q38 — complementa FEBRAFAR/CMED, nunca passa por cima).

`ALTER TYPE … ADD VALUE` dentro da transação: mesmo caso da
0010_origem_fila_loja_api (Postgres 16; nada usa o valor novo aqui). No
SQLite o enum é texto do tamanho do maior nome (FEBRAFAR = 8), e IQVIA cabe.
Só acrescenta.

Revision ID: 0017_catalogo_iqvia
Revises: 0016_usuarios_individuais
Create Date: 2026-10-07 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0017_catalogo_iqvia'
down_revision: Union[str, None] = '0016_usuarios_individuais'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'catalogo_iqvia',
        sa.Column('ean', sa.String(length=20), nullable=False),
        sa.Column('setor_nec', sa.String(length=40), nullable=False),
        sa.Column('gmrs', sa.String(length=2), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('ean'),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE origemcategoria ADD VALUE IF NOT EXISTS 'IQVIA'")


def downgrade() -> None:
    # Postgres não remove valor de enum; fica (inofensivo).
    op.drop_table('catalogo_iqvia')
