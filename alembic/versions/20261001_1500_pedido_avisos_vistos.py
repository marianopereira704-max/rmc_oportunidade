"""Assistente de pedido: avisos já vistos (estoque negativo, sem classificação)

Tabela nova `pedido_avisos_vistos` (01/10/2026): cada pop-up de alerta
aparece uma vez por foto do GPS, por usuário + loja. Puramente aditiva.

Revision ID: 0014_pedido_avisos_vistos
Revises: 0013_personalizacao_pedido_loja
Create Date: 2026-10-01 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0014_pedido_avisos_vistos'
down_revision: Union[str, None] = '0013_personalizacao_pedido_loja'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pedido_avisos_vistos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('data_foto', sa.String(length=10), nullable=False),
        sa.Column('resposta', sa.String(length=30), nullable=True),
        sa.Column('visto_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'loja_id', 'tipo', name='uq_pedido_avisos_vistos'),
    )
    op.create_index('ix_pedido_avisos_vistos_usuario_id', 'pedido_avisos_vistos', ['usuario_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pedido_avisos_vistos_usuario_id', table_name='pedido_avisos_vistos')
    op.drop_table('pedido_avisos_vistos')
