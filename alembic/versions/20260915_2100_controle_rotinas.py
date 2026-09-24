"""controle de rotinas automaticas

Cria `controle_rotinas` (item 15 do plano de reforma): uma linha por rotina
automática, com os carimbos de última tentativa / último sucesso / último
erro. É o que permite disparar rotina por tempo ("já rodou hoje?") e o que
impede duas execuções simultâneas da mesma rotina.

Migração puramente aditiva: cria uma tabela nova e não toca em nenhuma
existente. Nenhum dado é lido, alterado ou removido.

Revision ID: 0002_controle_rotinas
Revises: 0001_esquema_inicial
Create Date: 2026-09-15 21:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0002_controle_rotinas'
down_revision: Union[str, None] = '0001_esquema_inicial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'controle_rotinas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=80), nullable=False),
        sa.Column('ultima_tentativa_em', sa.DateTime(), nullable=True),
        sa.Column('ultimo_sucesso_em', sa.DateTime(), nullable=True),
        sa.Column('ultima_mensagem', sa.Text(), nullable=True),
        sa.Column('ultimo_erro', sa.Text(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('controle_rotinas', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_controle_rotinas_nome'), ['nome'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('controle_rotinas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_controle_rotinas_nome'))

    op.drop_table('controle_rotinas')
