"""Pedido: filtros salvos ("Meus filtros"), pessoais e de cada loja

Tabela nova `pedido_filtro_salvo` (pop-up de filtros de 29/09/2026).
Puramente aditiva.

Revision ID: 0012_pedido_filtro_salvo
Revises: 0011_pedido_area_listas
Create Date: 2026-09-29 20:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0012_pedido_filtro_salvo'
down_revision: Union[str, None] = '0011_pedido_area_listas'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pedido_filtro_salvo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('filtros', sa.Text(), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'loja_id', 'nome', name='uq_pedido_filtro_salvo'),
    )
    op.create_index('ix_pedido_filtro_salvo_usuario_id', 'pedido_filtro_salvo', ['usuario_id'], unique=False)
    op.create_index('ix_pedido_filtro_salvo_loja_id', 'pedido_filtro_salvo', ['loja_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pedido_filtro_salvo_loja_id', table_name='pedido_filtro_salvo')
    op.drop_index('ix_pedido_filtro_salvo_usuario_id', table_name='pedido_filtro_salvo')
    op.drop_table('pedido_filtro_salvo')
