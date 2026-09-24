"""Testes das opções de pool do engine (item 2 da reforma).

Contexto: app e banco rodam em droplets diferentes. Sem `pool_pre_ping`,
uma conexão derrubada em silêncio pelo firewall ou por um restart do
Postgres só é descoberta quando o app tenta USAR ela — e aparece pro
usuário como "server closed the connection unexpectedly" numa tela
qualquer, sem relação com o que ele estava fazendo.

Estes testes garantem duas coisas: que o Postgres (produção) recebe as
opções de pool corretas, e que o SQLite (dev/teste) continua funcionando —
`sqlite:///:memory:` usa `SingletonThreadPool`, que REJEITA
`pool_size`/`max_overflow`/`pool_recycle` com erro na criação do engine, então
é fácil quebrar um sem perceber o outro.
"""
from __future__ import annotations

import importlib

import pytest


def _recarregar_core_db(monkeypatch, database_url: str):
    """`core/db.py` calcula as opções de pool e cria o engine no import do
    módulo (module-level), não dentro de uma função — por isso o teste
    precisa forçar um reimport com `DATABASE_URL` já no ambiente, em vez de
    só trocar `settings.db.url` depois que o módulo já carregou."""
    monkeypatch.setenv("DATABASE_URL", database_url)

    import core.config as core_config
    import core.db as core_db

    importlib.reload(core_config)
    return importlib.reload(core_db)


@pytest.fixture()
def restaurar_core_db():
    """Garante que os módulos voltam ao estado normal (banco de teste
    padrão) depois de cada teste, mesmo que ele falhe no meio — outros
    arquivos de teste importam `core.db` esperando o engine SQLite padrão."""
    yield
    import core.config as core_config
    import core.db as core_db

    importlib.reload(core_config)
    importlib.reload(core_db)


def test_postgres_recebe_pre_ping_recycle_e_pool_dimensionado(monkeypatch, restaurar_core_db):
    db = _recarregar_core_db(
        monkeypatch, "postgresql+psycopg2://usuario:senha@host-fake:5432/banco_fake"
    )

    assert db.engine.pool._pre_ping is True
    assert db.engine.pool._recycle == 1800
    assert db.engine.pool.size() == 5
    assert db.engine.pool._max_overflow == 10


def test_sqlite_arquivo_continua_funcionando_sem_erro(monkeypatch, tmp_path, restaurar_core_db):
    """SQLite em arquivo (o default de desenvolvimento) usa QueuePool, que
    ACEITARIA as opções de Postgres — mas elas não fazem sentido pra um
    arquivo local sem servidor do outro lado, então não devem ser aplicadas
    de qualquer forma."""
    db = _recarregar_core_db(monkeypatch, f"sqlite:///{tmp_path / 'teste.db'}")

    with db.get_session() as session:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))


def test_sqlite_memoria_nao_quebra_a_criacao_do_engine(monkeypatch, restaurar_core_db):
    """A prova concreta do risco deste item: `sqlite:///:memory:` usa
    SingletonThreadPool, que rejeita pool_size/max_overflow/pool_recycle na
    hora de criar o engine. Se alguém remover a guarda `is_sqlite` de
    core/db.py, é ESTE teste que estoura — não silenciosamente em produção,
    mas aqui, na hora."""
    db = _recarregar_core_db(monkeypatch, "sqlite:///:memory:")

    with db.get_session() as session:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
