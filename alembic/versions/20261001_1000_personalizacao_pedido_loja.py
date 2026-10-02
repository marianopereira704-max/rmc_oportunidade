"""Pedido: personalização dos parâmetros por loja

Tabela nova `personalizacoes_pedido_loja` (01/10/2026): cada loja pode ter
os seus dias de estoque etc.; o que não for personalizado segue o padrão
(`configuracoes_pedido`). Puramente aditiva.

Revision ID: 0013_personalizacao_pedido_loja
Revises: 0012_pedido_filtro_salvo
Create Date: 2026-10-01 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0013_personalizacao_pedido_loja'
down_revision: Union[str, None] = '0012_pedido_filtro_salvo'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'personalizacoes_pedido_loja',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('valores', sa.Text(), nullable=False),
        sa.Column('criado_por', sa.String(length=120), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_personalizacoes_pedido_loja_loja_id', 'personalizacoes_pedido_loja', ['loja_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_personalizacoes_pedido_loja_loja_id', table_name='personalizacoes_pedido_loja')
    op.drop_table('personalizacoes_pedido_loja')
