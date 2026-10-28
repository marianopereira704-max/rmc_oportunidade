"""Classificação de produtos em cascata: FEBRAFAR e tipo da CMED por EAN

25/10/2026 (docs/specs/2026-10-25-classificacao-produtos-design.md). Tabela nova
`classificacao_ean` (EAN → categoria da FEBRAFAR e tipo bruto da CMED). Hoje
`categorias_ean` guarda UMA categoria por EAN (a vencedora da regra de junção): quando a
CMED vence, a categoria da FEBRAFAR se perde, e o tipo original da CMED (Similar, Novo…)
nunca foi gravado. A cascata precisa dos dois por EAN. A IQVIA já está em
`catalogo_iqvia`. Só acrescenta; `categorias_ean` continua como está.

Revision ID: 0019_classificacao_ean
Revises: 0018_empresas_gps
Create Date: 2026-10-25 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0019_classificacao_ean'
down_revision: Union[str, None] = '0018_empresas_gps'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'classificacao_ean',
        sa.Column('ean', sa.String(length=20), nullable=False),
        sa.Column('febrafar', sa.String(length=60), nullable=True),
        sa.Column('cmed_tipo', sa.String(length=60), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('ean'),
    )


def downgrade() -> None:
    op.drop_table('classificacao_ean')
