"""Apaga as tabelas que ficaram sem uso — NÃO ESTÁ ATIVA (pasta fora de alembic/versions; o Alembic não lê).

Preparada em 29/10/2026 (etapa 8 do plano de ação). Só entra DEPOIS da publicação e da limpeza — passo a passo no
README.md desta pasta. Antes disso, apagar estas tabelas quebraria o app da equipe (o `main` antigo ainda lê as
compras da planilha no banco de produção) e impediria voltar ao `main` antigo se a publicação der errado (regra do
CLAUDE.md: migração só acrescenta enquanto o `main` estiver atrasado).

- `rascunhos_pedido` — o "rascunho da loja" da Fase 5, trocado pela área de trabalho (`pedido_area`) em 29/09/2026;
- `pedido_filtro_salvo` — os filtros salvos do pop-up de filtros do Pedido, que saiu em 01/10/2026;
- `registros_compra_gps`, `compras_gps_orfas`, `fila_cnpj_orfao`, `uploads_gps` — o envio da planilha do GPS, que saiu
  em 05/10/2026 (a Análise passou a ler a API). O `limpeza_compras_planilha.py --executar` esvazia as quatro antes.

Revision ID: (ao ativar: a próxima da sequência, ex. 0021_apaga_tabelas_sem_uso)
Revises: (ao ativar: a última de alembic/versions)
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = '0021_apaga_tabelas_sem_uso'
down_revision: Union[str, None] = '0020_preferencias_email'   # conferir ao ativar: tem que ser a última de versions/
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# As filhas antes das mães (chaves estrangeiras para uploads_gps).
TABELAS = ("registros_compra_gps", "compras_gps_orfas", "fila_cnpj_orfao", "uploads_gps", "rascunhos_pedido",
           "pedido_filtro_salvo")


def upgrade() -> None:
    for tabela in TABELAS:
        op.execute(f"DROP TABLE IF EXISTS {tabela}")


def downgrade() -> None:
    raise NotImplementedError("As tabelas apagadas não voltam (os dados já tinham sido limpos). Para recriar vazias, "
                              "rode as migrações antigas que as criaram num banco novo.")
