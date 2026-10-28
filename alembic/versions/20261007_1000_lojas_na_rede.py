"""Lojas: coluna `na_rede` (loja da rede = status "Ativo" no sistema interno)

07/10/2026: o sistema interno marca como ativos também os cadastros do
"Portal do PBM" e "Institucional"; a rede são as 594 com status "Ativo".
Coluna nova, que aceita vazio (vazio = da rede até a primeira sincronização
pela regra nova) — puramente aditiva: o app antigo, no mesmo banco, não a vê.

Revision ID: 0015_lojas_na_rede
Revises: 0014_pedido_avisos_vistos
Create Date: 2026-10-07 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0015_lojas_na_rede'
down_revision: Union[str, None] = '0014_pedido_avisos_vistos'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lojas', sa.Column('na_rede', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('lojas', 'na_rede')
