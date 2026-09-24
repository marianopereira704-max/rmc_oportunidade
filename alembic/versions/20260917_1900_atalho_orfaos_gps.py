"""atalho de linhas orfas do upload GPS

Adiciona `uploads_gps.storage_key_orfaos`: a chave, no mesmo storage do
.xlsx, do arquivo lateral com as linhas daquele upload que caíram em CNPJ
órfão (ver integrations/gps_cache_orfaos.py). Existe pra a resolução de um
CNPJ órfão não precisar reabrir a planilha inteira no Excel (~7s por arquivo
de 142 mil linhas) só pra recuperar algumas centenas de linhas.

Migração puramente aditiva: uma coluna NOVA e NULA em uma tabela existente.
Nenhum dado é lido, alterado ou removido, e NULL é um estado válido e
esperado — todo upload já existente fica com NULL, que o código lê como
"não tem atalho, relê o Excel", exatamente o comportamento de hoje.

Revision ID: 0003_atalho_orfaos_gps
Revises: 0002_controle_rotinas
Create Date: 2026-09-17 19:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0003_atalho_orfaos_gps'
down_revision: Union[str, None] = '0002_controle_rotinas'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('uploads_gps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('storage_key_orfaos', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('uploads_gps', schema=None) as batch_op:
        batch_op.drop_column('storage_key_orfaos')
