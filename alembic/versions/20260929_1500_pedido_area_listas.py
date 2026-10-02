"""Pedido: área de trabalho por usuário + loja e listas salvas com nome

Tabelas novas `pedido_area`, `pedido_area_estado`, `pedido_listas` e
`pedido_listas_itens` (novo layout da aba Pedido, 29/09/2026). Puramente
aditiva: `rascunhos_pedido` (Fase 5) fica no banco, sem uso.

Revision ID: 0011_pedido_area_listas
Revises: 0010_origem_fila_loja_api
Create Date: 2026-09-29 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0011_pedido_area_listas'
down_revision: Union[str, None] = '0010_origem_fila_loja_api'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pedido_area',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('linha', sa.String(length=40), nullable=False),
        sa.Column('quantidade', sa.Integer(), nullable=True),
        sa.Column('selecionado', sa.Boolean(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'loja_id', 'linha', name='uq_pedido_area'),
    )
    op.create_index('ix_pedido_area_usuario_id', 'pedido_area', ['usuario_id'], unique=False)
    op.create_index('ix_pedido_area_loja_id', 'pedido_area', ['loja_id'], unique=False)
    op.create_table(
        'pedido_area_estado',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('lista_aberta_id', sa.Integer(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'loja_id', name='uq_pedido_area_estado'),
    )
    op.create_index('ix_pedido_area_estado_usuario_id', 'pedido_area_estado', ['usuario_id'], unique=False)
    op.create_table(
        'pedido_listas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=120), nullable=False),
        sa.Column('criado_por', sa.String(length=120), nullable=False),
        sa.Column('criado_por_id', sa.Integer(), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('itens', sa.Integer(), nullable=False),
        sa.Column('unidades', sa.Integer(), nullable=False),
        sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pedido_listas_loja_id', 'pedido_listas', ['loja_id'], unique=False)
    op.create_table(
        'pedido_listas_itens',
        sa.Column('lista_id', sa.Integer(), nullable=False),
        sa.Column('linha', sa.String(length=40), nullable=False),
        sa.Column('quantidade', sa.Integer(), nullable=False),
        sa.Column('selecionado', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['lista_id'], ['pedido_listas.id']),
        sa.PrimaryKeyConstraint('lista_id', 'linha'),
    )


def downgrade() -> None:
    op.drop_table('pedido_listas_itens')
    op.drop_index('ix_pedido_listas_loja_id', table_name='pedido_listas')
    op.drop_table('pedido_listas')
    op.drop_index('ix_pedido_area_estado_usuario_id', table_name='pedido_area_estado')
    op.drop_table('pedido_area_estado')
    op.drop_index('ix_pedido_area_loja_id', table_name='pedido_area')
    op.drop_index('ix_pedido_area_usuario_id', table_name='pedido_area')
    op.drop_table('pedido_area')
