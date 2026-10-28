"""Empresas do GPS com o código que a equipe usa (08/10/2026)

A API do GPS só identifica a empresa pelo `idEmpresa` (ex.:
6901107ed4a5e05f99b7f4ab); o número que a equipe conhece (Mega Farma =
2089) não sai por ela, nem pelo sistema da T.I. Vem de uma planilha que o
Mariano exporta do GPS (Empresa "2089 - Mega Farma", Cnpj, idEmp), enviada
em Dados → Pendências → Vínculo de lojas GPS. Tabela nova `empresas_gps`.
Só acrescenta.

Revision ID: 0018_empresas_gps
Revises: 0017_catalogo_iqvia
Create Date: 2026-10-08 17:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0018_empresas_gps'
down_revision: Union[str, None] = '0017_catalogo_iqvia'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'empresas_gps',
        sa.Column('id_empresa_gps', sa.String(length=40), nullable=False),
        sa.Column('codigo_gps', sa.String(length=20), nullable=False),
        sa.Column('nome', sa.String(length=200), nullable=True),
        sa.Column('cnpjs', sa.Text(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.Column('atualizado_por', sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint('id_empresa_gps'),
    )


def downgrade() -> None:
    op.drop_table('empresas_gps')
