"""Ambiente de execução das migrações Alembic.

Duas decisões importantes deste arquivo:

1. A URL do banco vem de `core.config.settings.db.url`, nunca de
   `alembic.ini`. É a mesma cadeia de configuração do app inteiro
   (st.secrets -> variável de ambiente -> default SQLite), então não existe
   uma segunda cópia da credencial num arquivo versionado, e rodar
   `alembic upgrade head` na raiz do projeto acerta o mesmo banco que o app
   acertaria naquele ambiente.

2. `target_metadata` aponta pro MESMO `Base.metadata` que os modelos
   declaram — é isso que faz `alembic revision --autogenerate` funcionar e
   que permite ao teste de deriva (tests/test_migracoes.py) provar que as
   migrações e os modelos não se separaram.

`render_as_batch=True` só tem efeito em SQLite: o SQLite não suporta
ALTER COLUMN / DROP COLUMN de verdade, e o modo batch faz o Alembic
recriar a tabela por baixo dos panos. Sem isso, uma migração futura que
altere uma coluna passaria no Postgres e quebraria no SQLite usado em
desenvolvimento e nos testes.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# A raiz do projeto precisa estar no sys.path pra `import core...` funcionar
# tanto pelo CLI (`alembic upgrade head`) quanto quando o app chama o Alembic
# programaticamente no start.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.config import settings  # noqa: E402
from core.models import Base  # noqa: E402

config = context.config

# Só reconfigura o logging quando o Alembic foi chamado pelo CLI. Quando é o
# app que chama (core/db.py), `configure_logger=False` evita que o
# fileConfig do alembic.ini derrube a configuração de log do Streamlit.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url_do_banco() -> str:
    """A URL efetiva: a que o chamador injetou (o app, ao rodar as migrações
    no start, passa a sua) ou, na falta dela, a mesma que o app usaria."""
    return config.attributes.get("url_banco") or settings.db.url


def _e_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Gera o SQL sem abrir conexão (`alembic upgrade head --sql`) — útil pra
    revisar ou aplicar as migrações à mão num banco de produção."""
    url = _url_do_banco()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=_e_sqlite(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo normal: conecta e aplica."""
    url = _url_do_banco()

    conexao_existente = config.attributes.get("connection")
    if conexao_existente is not None:
        # O app já tem um engine configurado (pool, pre_ping, connect_args) —
        # reaproveitar a conexão dele evita abrir uma segunda com parâmetros
        # diferentes dos que o resto do sistema usa.
        context.configure(
            connection=conexao_existente,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=_e_sqlite(url),
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    secao = config.get_section(config.config_ini_section, {})
    secao["sqlalchemy.url"] = url
    engine = engine_from_config(secao, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=_e_sqlite(url),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
