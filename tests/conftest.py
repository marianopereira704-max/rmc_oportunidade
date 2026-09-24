"""Isolamento global da suíte de testes.

Sem isto, `settings.spaces` lê o `.streamlit/secrets.toml` da máquina — e
quando ele tem as credenciais reais do DigitalOcean Spaces, todo teste que
chama `filesystem.salvar_arquivo` gravava um arquivo DE VERDADE no bucket de
produção (a cada execução da suíte). Aqui, todo teste roda com storage local
num diretório temporário próprio, e o singleton do backend é zerado antes e
depois, pra nenhum teste herdar o backend (ou a pasta) de outro.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _storage_local_isolado(tmp_path, monkeypatch):
    from core import config as core_config
    from storage import filesystem as fs

    destino = tmp_path / "_storage_teste"
    destino.mkdir(parents=True, exist_ok=True)
    # `fs.settings` e `core_config.settings` podem ser objetos DIFERENTES:
    # tests/test_engine_pool.py recarrega core.config (importlib.reload), e
    # storage/filesystem.py continua segurando a instância importada antes.
    for alvo in {id(core_config.settings): core_config.settings, id(fs.settings): fs.settings}.values():
        monkeypatch.setattr(alvo.spaces, "endpoint_url", None)
        monkeypatch.setattr(alvo, "local_storage_dir", destino)
    monkeypatch.setattr(fs, "_backend_instancia", None)
    yield
    fs._backend_instancia = None
