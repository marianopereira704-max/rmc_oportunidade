"""Login individual: e-mail + senha para as lojas, cadastro de usuários

07/10/2026: usuários de loja (Comprador/Proprietário) entram por e-mail; a
equipe RMC continua no CNPJ. Colunas novas em `usuarios` (cargo, telefone,
loja principal, linhas de compra, painéis, criação/exclusão), `cnpj_login`
passa a aceitar vazio (usuário de loja não tem), `login` (e-mail) fica
único, e duas tabelas novas: `tokens_senha` (links de criar/recuperar
senha) e `usuarios_lojas_historico` (acessos a loja que saíram).

Só acrescenta ou afrouxa — o app antigo, no mesmo banco, continua
funcionando (os usuários dele têm CNPJ).

Revision ID: 0016_usuarios_individuais
Revises: 0015_lojas_na_rede
Create Date: 2026-10-07 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0016_usuarios_individuais'
down_revision: Union[str, None] = '0015_lojas_na_rede'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.alter_column('cnpj_login', existing_type=sa.String(length=18), nullable=True)
        batch_op.drop_index('ix_usuarios_login')
        batch_op.create_index('ix_usuarios_login', ['login'], unique=True)
        batch_op.add_column(sa.Column('cargo', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('telefone', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('loja_principal_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('linhas_compra', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('paineis', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('criado_em', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('criado_por', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('excluido_em', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('excluido_por', sa.String(length=120), nullable=True))
        batch_op.create_index('ix_usuarios_loja_principal_id', ['loja_principal_id'], unique=False)
        batch_op.create_foreign_key('fk_usuarios_loja_principal', 'lojas', ['loja_principal_id'], ['id'])

    op.create_table(
        'tokens_senha',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('expira_em', sa.DateTime(), nullable=False),
        sa.Column('usado_em', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_tokens_senha_usuario_id', 'tokens_senha', ['usuario_id'], unique=False)

    op.create_table(
        'usuarios_lojas_historico',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('loja_id', sa.Integer(), nullable=False),
        sa.Column('motivo', sa.String(length=200), nullable=False),
        sa.Column('registrado_em', sa.DateTime(), nullable=False),
        sa.Column('registrado_por', sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(['loja_id'], ['lojas.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_usuarios_lojas_historico_usuario_id', 'usuarios_lojas_historico', ['usuario_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_usuarios_lojas_historico_usuario_id', table_name='usuarios_lojas_historico')
    op.drop_table('usuarios_lojas_historico')
    op.drop_index('ix_tokens_senha_usuario_id', table_name='tokens_senha')
    op.drop_table('tokens_senha')
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.drop_constraint('fk_usuarios_loja_principal', type_='foreignkey')
        batch_op.drop_index('ix_usuarios_loja_principal_id')
        for coluna in ('excluido_por', 'excluido_em', 'criado_por', 'criado_em', 'paineis', 'linhas_compra',
                       'loja_principal_id', 'telefone', 'cargo'):
            batch_op.drop_column(coluna)
        batch_op.drop_index('ix_usuarios_login')
        batch_op.create_index('ix_usuarios_login', ['login'], unique=False)
        batch_op.alter_column('cnpj_login', existing_type=sa.String(length=18), nullable=False)
