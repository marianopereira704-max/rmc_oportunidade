"""Configurações de Pedidos: tabela configuracoes_pedido (histórico em JSON)

Ver pedido/configuracao.py. Puramente aditiva: uma tabela nova. O app
publicado (código anterior) continua funcionando no mesmo banco.

Revision ID: 0008_configuracoes_pedido
Revises: 0007_categorias_ean
Create Date: 2026-09-28 18:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0008_configuracoes_pedido'
down_revision: Union[str, None] = '0007_categorias_ean'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'configuracoes_pedido',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('valores', sa.Text(), nullable=False),
        sa.Column('criado_por', sa.String(length=120), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('configuracoes_pedido')
