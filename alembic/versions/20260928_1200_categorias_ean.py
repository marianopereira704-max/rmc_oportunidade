"""base de categorias do Pedido: tabela categorias_ean (EAN → categoria)

Carga inicial FEBRAFAR + CMED e categorias manuais do admin. Ver
pedido/categorias.py.

Puramente aditiva: uma tabela nova. O app publicado (código anterior)
continua funcionando no mesmo banco.

Revision ID: 0007_categorias_ean
Revises: 0006_vinculo_lojas_gps
Create Date: 2026-09-28 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0007_categorias_ean'
down_revision: Union[str, None] = '0006_vinculo_lojas_gps'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'categorias_ean',
        sa.Column('ean', sa.String(length=20), nullable=False),
        sa.Column('categoria', sa.String(length=60), nullable=False),
        sa.Column('origem', sa.Enum('FEBRAFAR', 'CMED', 'MANUAL', name='origemcategoria'), nullable=False),
        sa.Column('descricao', sa.String(length=200), nullable=True),
        sa.Column('atualizado_por', sa.String(length=120), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('ean'),
    )


def downgrade() -> None:
    op.drop_table('categorias_ean')
    sa.Enum(name='origemcategoria').drop(op.get_bind(), checkfirst=True)
