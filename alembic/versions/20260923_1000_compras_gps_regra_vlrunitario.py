"""compras GPS: regra VlrUnitario x Quantidade e tabela de compras orfas

Duas mudanças, ambas sem apagar nada:

1. `registros_compra_gps.fat_liquido`, `pct_cmv` e `estoque` passam a aceitar
   NULL. A regra de custo mudou (custo = VlrUnitario, sem ST; estoque saiu da
   análise) e essas colunas deixam de ser preenchidas. Não são removidas
   porque o app publicado — código anterior a esta mudança — lê o mesmo
   banco; afrouxar NOT NULL não quebra quem só lê ou só grava valor.
2. Tabela nova `compras_gps_orfas`: compras cujo CNPJ ainda não bate com
   nenhuma loja, com a mesma regra de substituição por (CNPJ, mês) das compras
   normais. Substitui o "atalho" em arquivo do Spaces
   (`uploads_gps.storage_key_orfaos`, que continua existindo e só deixa de ser
   escrito) e passa a ser a fonte da fila de CNPJ órfão.

Revision ID: 0004_compras_gps_vlrunitario
Revises: 0003_atalho_orfaos_gps
Create Date: 2026-09-23 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0004_compras_gps_vlrunitario'
down_revision: Union[str, None] = '0003_atalho_orfaos_gps'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('registros_compra_gps', schema=None) as batch_op:
        batch_op.alter_column('fat_liquido', existing_type=sa.Numeric(precision=14, scale=4), nullable=True)
        batch_op.alter_column('pct_cmv', existing_type=sa.Numeric(precision=7, scale=4), nullable=True)
        batch_op.alter_column('estoque', existing_type=sa.Numeric(precision=14, scale=3), nullable=True)

    op.create_table(
        'compras_gps_orfas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cnpj', sa.String(length=18), nullable=False),
        sa.Column('razao_social', sa.String(length=200), nullable=True),
        sa.Column('ean', sa.String(length=40), nullable=False),
        sa.Column('descricao_origem', sa.String(length=250), nullable=False),
        sa.Column('laboratorio_compra', sa.String(length=150), nullable=True),
        sa.Column('ano_mes', sa.String(length=7), nullable=False),
        sa.Column('quantidade', sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column('custo_unitario', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('upload_fs_node_id', sa.Integer(), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['upload_fs_node_id'], ['fs_nodes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cnpj', 'ean', 'ano_mes', name='uq_compra_orfa_cnpj_ean_mes'),
    )
    op.create_index('ix_compras_gps_orfas_ano_mes_cnpj', 'compras_gps_orfas', ['ano_mes', 'cnpj'], unique=False)
    op.create_index('ix_compras_gps_orfas_ean', 'compras_gps_orfas', ['ean'], unique=False)
    op.create_index(
        op.f('ix_compras_gps_orfas_upload_fs_node_id'), 'compras_gps_orfas', ['upload_fs_node_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_compras_gps_orfas_upload_fs_node_id'), table_name='compras_gps_orfas')
    op.drop_index('ix_compras_gps_orfas_ean', table_name='compras_gps_orfas')
    op.drop_index('ix_compras_gps_orfas_ano_mes_cnpj', table_name='compras_gps_orfas')
    op.drop_table('compras_gps_orfas')
    # Voltar a NOT NULL só funciona se nenhuma linha tiver NULL nessas colunas
    # — o que deixa de ser verdade assim que a regra nova grava a primeira
    # compra. É o esperado: a regra antiga não tem como ser reconstruída.
    with op.batch_alter_table('registros_compra_gps', schema=None) as batch_op:
        batch_op.alter_column('estoque', existing_type=sa.Numeric(precision=14, scale=3), nullable=False)
        batch_op.alter_column('pct_cmv', existing_type=sa.Numeric(precision=7, scale=4), nullable=False)
        batch_op.alter_column('fat_liquido', existing_type=sa.Numeric(precision=14, scale=4), nullable=False)
