"""Pedido: rascunho, correção de estoque negativo, exportações; níveis de acesso

Tabelas novas `rascunhos_pedido`, `correcoes_estoque`, `exportacoes_pedido`
e `usuarios_lojas`; colunas novas (nulas) `usuarios.nivel` e
`usuarios.login`. Puramente aditiva — o app publicado (código anterior)
continua funcionando no mesmo banco.

Revision ID: 0009_pedido_rascunho_acesso
Revises: 0008_configuracoes_pedido
Create Date: 2026-09-28 22:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0009_pedido_rascunho_acesso'
down_revision: Union[str, None] = '0008_configuracoes_pedido'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.add_column(sa.Column('nivel', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('login', sa.String(length=120), nullable=True))
        batch_op.create_index('ix_usuarios_login', ['login'], unique=False)

    op.create_table(
        'usuarios_lojas',
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('usuario_id', 'loja_id'),
    )
    op.create_table(
        'rascunhos_pedido',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('linha', sa.String(length=40), nullable=False),
        sa.Column('quantidade', sa.Integer(), nullable=False),
        sa.Column('sugestao_calculada', sa.Integer(), nullable=True),
        sa.Column('alterado_por', sa.String(length=120), nullable=False),
        sa.Column('alterado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loja_id', 'linha', name='uq_rascunho_loja_linha'),
    )
    op.create_index('ix_rascunhos_pedido_loja_id', 'rascunhos_pedido', ['loja_id'], unique=False)
    op.create_table(
        'correcoes_estoque',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('linha', sa.String(length=40), nullable=False),
        sa.Column('estoque_corrigido', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('estoque_gps', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('data_foto', sa.String(length=10), nullable=True),
        sa.Column('corrigido_por', sa.String(length=120), nullable=False),
        sa.Column('corrigido_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loja_id', 'linha', name='uq_correcao_loja_linha'),
    )
    op.create_index('ix_correcoes_estoque_loja_id', 'correcoes_estoque', ['loja_id'], unique=False)
    op.create_table(
        'exportacoes_pedido',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('formato', sa.String(length=10), nullable=False),
        sa.Column('itens', sa.Integer(), nullable=False),
        sa.Column('unidades', sa.Integer(), nullable=False),
        sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('exportado_por', sa.String(length=120), nullable=False),
        sa.Column('exportado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_exportacoes_pedido_loja_id', 'exportacoes_pedido', ['loja_id'], unique=False)


def downgrade() -> None:
    for tabela in ('exportacoes_pedido', 'correcoes_estoque', 'rascunhos_pedido'):
        op.drop_index(f'ix_{tabela}_loja_id', table_name=tabela)
        op.drop_table(tabela)
    op.drop_table('usuarios_lojas')
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.drop_index('ix_usuarios_login')
        batch_op.drop_column('login')
        batch_op.drop_column('nivel')
