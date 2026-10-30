"""Preferências de e-mail dos usuários de loja (aviso de ruptura e relatório diário)

29/10/2026 (docs/specs/2026-10-29-emails-lojas-design.md, etapa 7). Duas colunas em `usuarios` que aceitam vazio:
vazio = o padrão decidido pelo Mariano (aviso de ruptura ligado, relatório diário desligado) — os usuários que já
existem passam a receber o aviso sem ninguém mexer. Só acrescenta (regra do projeto: o `main` antigo continua
funcionando no banco com as colunas novas).

Revision ID: 0020_preferencias_email
Revises: 0019_classificacao_ean
Create Date: 2026-10-29 22:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0020_preferencias_email'
down_revision: Union[str, None] = '0019_classificacao_ean'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('usuarios', sa.Column('email_ruptura', sa.Boolean(), nullable=True))
    op.add_column('usuarios', sa.Column('email_relatorio', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('usuarios', 'email_relatorio')
    op.drop_column('usuarios', 'email_ruptura')
