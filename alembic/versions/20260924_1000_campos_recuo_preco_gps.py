"""campos de recuo de preço nas compras GPS

Acrescenta, em `registros_compra_gps` e `compras_gps_orfas`, os campos que a
Análise de Oportunidade usa quando o VlrUnitario de uma compra destoa mais de
±50% do preço do laboratório escolhido: quantidade vendida (QTD), custo CMV
por unidade (Fat × %CMV ÷ QTD, calculado no upload) e o "R$ Custo médio" da
planilha. Em `compras_gps_orfas` entram também Fat. líquido e % CMV (em
`registros_compra_gps` essas duas já existiam).

Puramente aditiva: colunas novas e nulas. O app publicado (código anterior)
continua funcionando no mesmo banco.

Revision ID: 0005_campos_recuo_preco_gps
Revises: 0004_compras_gps_vlrunitario
Create Date: 2026-09-24 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0005_campos_recuo_preco_gps'
down_revision: Union[str, None] = '0004_compras_gps_vlrunitario'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('registros_compra_gps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('qtd_vendida', sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column('custo_cmv_unitario', sa.Numeric(precision=14, scale=4), nullable=True))
        batch_op.add_column(sa.Column('custo_medio_planilha', sa.Numeric(precision=14, scale=4), nullable=True))
    with op.batch_alter_table('compras_gps_orfas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fat_liquido', sa.Numeric(precision=14, scale=4), nullable=True))
        batch_op.add_column(sa.Column('pct_cmv', sa.Numeric(precision=7, scale=4), nullable=True))
        batch_op.add_column(sa.Column('qtd_vendida', sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column('custo_cmv_unitario', sa.Numeric(precision=14, scale=4), nullable=True))
        batch_op.add_column(sa.Column('custo_medio_planilha', sa.Numeric(precision=14, scale=4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('compras_gps_orfas', schema=None) as batch_op:
        for coluna in ('custo_medio_planilha', 'custo_cmv_unitario', 'qtd_vendida', 'pct_cmv', 'fat_liquido'):
            batch_op.drop_column(coluna)
    with op.batch_alter_table('registros_compra_gps', schema=None) as batch_op:
        for coluna in ('custo_medio_planilha', 'custo_cmv_unitario', 'qtd_vendida'):
            batch_op.drop_column(coluna)
