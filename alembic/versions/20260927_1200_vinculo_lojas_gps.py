"""vínculo de lojas GPS (Pedido): endereço/legacyId nas lojas + tabela de vínculos

Acrescenta em `lojas` os campos do sistema interno que o vínculo com a loja
do GPS usa (legacyId, número do endereço, bairro, CEP, nome fantasia) e cria
`vinculos_loja_gps`. Ver pedido/vinculo.py e docs/mapa_api_gps.md.

Puramente aditiva: colunas novas nulas e uma tabela nova. O app publicado
(código anterior) continua funcionando no mesmo banco.

Revision ID: 0006_vinculo_lojas_gps
Revises: 0005_campos_recuo_preco_gps
Create Date: 2026-09-27 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0006_vinculo_lojas_gps'
down_revision: Union[str, None] = '0005_campos_recuo_preco_gps'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SITUACOES = ('AUTOMATICO', 'CONFIRMAR', 'CONFIRMADO', 'NAO_CLIENTE', 'SEM_CANDIDATO')


def upgrade() -> None:
    with op.batch_alter_table('lojas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('legacy_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('endereco_numero', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('bairro', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('cep', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('nome_fantasia', sa.String(length=200), nullable=True))
        batch_op.create_index('ix_lojas_legacy_id', ['legacy_id'], unique=False)

    op.create_table(
        'vinculos_loja_gps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_empresa_gps', sa.String(length=40), nullable=False),
        sa.Column('codigo_loja_gps', sa.String(length=40), nullable=False),
        sa.Column('nome_loja_gps', sa.String(length=200), nullable=True),
        sa.Column('cnpj_gps', sa.String(length=18), nullable=True),
        sa.Column('numero_gps', sa.String(length=20), nullable=True),
        sa.Column('cidade_gps', sa.String(length=120), nullable=True),
        sa.Column('uf_gps', sa.String(length=2), nullable=True),
        sa.Column('loja_id', sa.Integer(), nullable=True),
        sa.Column('situacao', sa.Enum(*_SITUACOES, name='situacaovinculogps'), nullable=False),
        sa.Column('metodo', sa.String(length=20), nullable=True),
        sa.Column('pontuacao', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('motivo', sa.String(length=200), nullable=True),
        sa.Column('decidido_por', sa.String(length=120), nullable=True),
        sa.Column('decidido_em', sa.DateTime(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_empresa_gps', 'codigo_loja_gps', name='uq_vinculo_loja_gps'),
    )
    op.create_index('ix_vinculos_loja_gps_loja_id', 'vinculos_loja_gps', ['loja_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_vinculos_loja_gps_loja_id', table_name='vinculos_loja_gps')
    op.drop_table('vinculos_loja_gps')
    sa.Enum(name='situacaovinculogps').drop(op.get_bind(), checkfirst=True)
    with op.batch_alter_table('lojas', schema=None) as batch_op:
        batch_op.drop_index('ix_lojas_legacy_id')
        for coluna in ('nome_fantasia', 'cep', 'bairro', 'endereco_numero', 'legacy_id'):
            batch_op.drop_column(coluna)
