"""Testes da sincronização da base de lojas (integrations/sistema_interno.py).

Este fluxo não tinha teste nenhum até agora, e acabou de ser reescrito para
matar o N+1 (item 17 do plano): antes era um SELECT por loja recebida da API
— com ~800 lojas ativas, ~800 idas ao banco, e minutos de espera. Agora é uma
instrução por bloco, com ON CONFLICT (cnpj) DO UPDATE.

O que os testes travam, em ordem de importância:

1. **Custo.** O número de instruções enviadas ao banco não pode crescer com o
   número de lojas. É o bug que estamos corrigindo; sem esse teste ele volta
   na primeira refatoração distraída.
2. **Loja da rede e grupo econômico (07/10/2026).** Só status "Ativo" é da
   rede; quem sai fica marcado (`na_rede` False), nunca apagado; resposta sem
   nenhuma loja "Ativo" não mexe em nada. O grupo econômico passou a vir
   desta origem (sigla `acronym_economic_group`).
3. **O comportamento antigo, campo a campo.** A reescrita é de desempenho; o
   resultado gravado tem que ser idêntico ao do laço anterior.
4. **CNPJ repetido no mesmo lote.** O laço antigo aguentava naturalmente (a
   segunda ocorrência sobrescrevia a primeira); um lote de ON CONFLICT DO
   UPDATE com a chave repetida é recusado pelo Postgres. Precisa deduplicar.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from core.models import Base, Loja
from integrations import sistema_interno
from integrations.base import StatusIntegracao


@pytest.fixture()
def banco(tmp_path, monkeypatch):
    """Banco descartável + `get_session` do módulo apontando pra ele, para o
    adapter rodar de verdade sem tocar no banco configurado."""
    engine = create_engine(f"sqlite:///{tmp_path / 'lojas.db'}", future=True)
    Base.metadata.create_all(engine)
    Sessao = sessionmaker(bind=engine, future=True)

    instrucoes: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _contar(conn, cursor, statement, parameters, context, executemany):
        instrucoes.append(statement.strip().split()[0].upper())

    from contextlib import contextmanager

    @contextmanager
    def _get_session():
        sessao = Sessao()
        try:
            yield sessao
            sessao.commit()
        except Exception:
            sessao.rollback()
            raise
        finally:
            sessao.close()

    monkeypatch.setattr(sistema_interno, "get_session", _get_session)
    monkeypatch.setattr(sistema_interno.settings.sistema_interno, "base_url", "http://api-falsa")
    monkeypatch.setattr(sistema_interno.settings.sistema_interno, "token", "token-falso")

    engine.instrucoes = instrucoes
    engine.Sessao = Sessao
    return engine


def _loja_api(cnpj: str, nome: str = "DROGARIA TESTE", uf: str = "MG", cidade: str = "BH", **extra) -> dict:
    base = {
        "cnpj": cnpj,
        "businessName": nome,
        "active": True,
        "status_title": "Ativo",
        "address": {"state": uf, "city": cidade},
        "team": [],
    }
    base.update(extra)
    return base


def _sincronizar(banco, registros: list[dict], monkeypatch):
    adaptador = sistema_interno.SistemaInternoLojasAdapter()
    monkeypatch.setattr(adaptador, "_buscar_lojas_api", lambda: registros)
    return adaptador.sincronizar()


# ---------------------------------------------------------------------------
# 1. Custo: o N+1 não pode voltar
# ---------------------------------------------------------------------------

def test_numero_de_instrucoes_nao_cresce_com_a_quantidade_de_lojas(banco, monkeypatch):
    """O teste que existe por causa do bug: antes, 200 lojas = 200 SELECTs."""
    registros = [_loja_api(f"{i:014d}", nome=f"DROGARIA {i}") for i in range(200)]

    banco.instrucoes.clear()
    _sincronizar(banco, registros, monkeypatch)

    selects = [i for i in banco.instrucoes if i == "SELECT"]
    inserts = [i for i in banco.instrucoes if i == "INSERT"]
    # Desde 07/10/2026 a sincronização confere os acessos dos usuários de loja
    # (integrations/usuarios.py::ajustar_acessos): consultas FIXAS, nunca uma por loja.
    assert len(selects) <= 3, f"nenhuma consulta por loja deveria sobrar, vieram {len(selects)}"
    assert len(inserts) <= 2, f"200 lojas deveriam caber em 1 bloco, vieram {len(inserts)} instruções"


def test_mil_lojas_continuam_em_poucas_instrucoes(banco, monkeypatch):
    registros = [_loja_api(f"{i:014d}") for i in range(1000)]

    banco.instrucoes.clear()
    _sincronizar(banco, registros, monkeypatch)

    inserts = [i for i in banco.instrucoes if i == "INSERT"]
    assert len(inserts) == 2, f"1000 lojas em blocos de 500 = 2 instruções, vieram {len(inserts)}"
    with Session(banco) as sessao:
        assert sessao.query(Loja).count() == 1000


# ---------------------------------------------------------------------------
# 2. Loja da rede e grupo econômico (07/10/2026)
# ---------------------------------------------------------------------------

def test_grupo_economico_vem_da_sigla_e_e_atualizado(banco, monkeypatch):
    with Session(banco) as sessao:
        sessao.add(Loja(cnpj="123", razao_social="ANTIGA", uf="MG", cidade="BH", grupo_economico="REDE ALFA",
                        fonte="sistema_interno"))
        sessao.commit()

    _sincronizar(banco, [_loja_api("123", nome="NOVA", acronym_economic_group="GE787"),
                         _loja_api("456", acronym_economic_group=None)], monkeypatch)

    with Session(banco) as sessao:
        assert sessao.query(Loja).filter_by(cnpj="123").one().grupo_economico == "GE787"
        assert sessao.query(Loja).filter_by(cnpj="456").one().grupo_economico is None


def test_so_status_ativo_e_da_rede_e_quem_sai_fica_marcado(banco, monkeypatch):
    """O `active` do sistema interno também vale pra cadastros do Portal do PBM
    e Institucional — 813 "ativas", 594 da rede (07/10/2026)."""
    with Session(banco) as sessao:
        sessao.add(Loja(cnpj="999", razao_social="SAIU DA REDE", uf="MG", cidade="BH", fonte="sistema_interno"))
        sessao.commit()

    resultado = _sincronizar(banco, [
        _loja_api("111", nome="DA REDE"),
        _loja_api("222", nome="PBM", status_title="Portal do PBM"),
        _loja_api("333", nome="INSTITUCIONAL", status_title="Institucional"),
    ], monkeypatch)

    with Session(banco) as sessao:
        lojas = {l.cnpj: l.na_rede for l in sessao.query(Loja).all()}
    assert lojas == {"111": True, "999": False}          # PBM/Institucional nem entram; a antiga sai
    assert "1 lojas da rede" in resultado.mensagem and "1 loja(s) saíram da rede" in resultado.mensagem

    # Voltou pra rede: volta a ficar True.
    _sincronizar(banco, [_loja_api("111"), _loja_api("999")], monkeypatch)
    with Session(banco) as sessao:
        assert {l.cnpj: l.na_rede for l in sessao.query(Loja).all()} == {"111": True, "999": True}


def test_resposta_sem_nenhuma_loja_ativa_nao_mexe_em_nada(banco, monkeypatch):
    with Session(banco) as sessao:
        sessao.add(Loja(cnpj="111", razao_social="X", uf="MG", cidade="BH", fonte="sistema_interno", na_rede=True))
        sessao.commit()
    with pytest.raises(RuntimeError, match="nada foi alterado"):
        _sincronizar(banco, [_loja_api("222", status_title="Portal do PBM")], monkeypatch)
    with Session(banco) as sessao:
        assert sessao.query(Loja).one().na_rede is True


# ---------------------------------------------------------------------------
# 3. Comportamento preservado, campo a campo
# ---------------------------------------------------------------------------

def test_insere_loja_nova_com_todos_os_campos_da_origem(banco, monkeypatch):
    registro = _loja_api(
        "999", nome="DROGARIA SAO JOSE LTDA", uf="RN", cidade="GOIANINHA",
        team=[
            {"sector": "Consultoria Interna", "name": "Ana"},
            {"sector": "Consultoria Farma", "name": "Bruno"},
            {"sector": "Negócios", "name": "Carla"},
            {"sector": "PBM", "name": "Ignorado"},
        ],
    )

    _sincronizar(banco, [registro], monkeypatch)

    with Session(banco) as sessao:
        loja = sessao.query(Loja).filter_by(cnpj="999").one()
        assert loja.razao_social == "DROGARIA SAO JOSE LTDA"
        assert loja.uf == "RN" and loja.cidade == "GOIANINHA"
        assert loja.consultor_interno == "Ana"
        assert loja.consultor_farma == "Bruno"
        assert loja.atendente_comercial == "Carla"
        assert loja.fonte == "sistema_interno"
        assert loja.grupo_economico is None, "sem sigla na origem, sem grupo"
        assert loja.na_rede is True
        assert isinstance(loja.atualizado_em, dt.datetime)


def test_atualiza_loja_existente_em_vez_de_duplicar(banco, monkeypatch):
    with Session(banco) as sessao:
        sessao.add(Loja(cnpj="123", razao_social="ANTIGA", uf="SP", cidade="SP", fonte="sistema_interno"))
        sessao.commit()

    _sincronizar(banco, [_loja_api("123", nome="ATUAL", uf="MG", cidade="BH")], monkeypatch)

    with Session(banco) as sessao:
        assert sessao.query(Loja).count() == 1
        loja = sessao.query(Loja).one()
        assert loja.razao_social == "ATUAL" and loja.uf == "MG" and loja.cidade == "BH"


def test_registros_inativos_sao_ignorados(banco, monkeypatch):
    registros = [
        _loja_api("111", nome="ATIVA"),
        _loja_api("222", nome="INATIVA", active=False),
        _loja_api("333", nome="SEM CAMPO ACTIVE"),
    ]
    registros[2].pop("active")

    resultado = _sincronizar(banco, registros, monkeypatch)

    with Session(banco) as sessao:
        assert [l.cnpj for l in sessao.query(Loja).all()] == ["111"]
    assert "1 lojas da rede" in resultado.mensagem
    assert "de 3 recebidas" in resultado.mensagem


def test_setor_repetido_usa_o_primeiro_e_e_relatado(banco, monkeypatch):
    registro = _loja_api("555", team=[
        {"sector": "Consultoria Interna", "name": "Primeira"},
        {"sector": "Consultoria Interna", "name": "Segunda"},
    ])

    resultado = _sincronizar(banco, [registro], monkeypatch)

    with Session(banco) as sessao:
        assert sessao.query(Loja).one().consultor_interno == "Primeira"
    assert "1 loja(s) tinham mais de uma pessoa no mesmo setor" in resultado.mensagem


# ---------------------------------------------------------------------------
# 4. CNPJ repetido no mesmo lote
# ---------------------------------------------------------------------------

def test_cnpj_repetido_no_mesmo_lote_nao_quebra_e_vale_o_ultimo(banco, monkeypatch):
    """Um lote de ON CONFLICT DO UPDATE com a mesma chave duas vezes é
    recusado pelo Postgres. O laço antigo aguentava naturalmente, então a
    reescrita precisa deduplicar — mantendo a última ocorrência, que é o que
    o laço antigo deixava gravado."""
    registros = [
        _loja_api("777", nome="PRIMEIRA VERSAO", cidade="BH"),
        _loja_api("777", nome="SEGUNDA VERSAO", cidade="CONTAGEM"),
    ]

    resultado = _sincronizar(banco, registros, monkeypatch)

    with Session(banco) as sessao:
        assert sessao.query(Loja).count() == 1
        loja = sessao.query(Loja).one()
        assert loja.razao_social == "SEGUNDA VERSAO"
        assert loja.cidade == "CONTAGEM"
    assert "repetiam um CNPJ" in resultado.mensagem


def test_lista_vazia_nao_quebra(banco, monkeypatch):
    resultado = _sincronizar(banco, [], monkeypatch)

    assert resultado.status == StatusIntegracao.DISPONIVEL
    assert resultado.registros_processados == 0
    with Session(banco) as sessao:
        assert sessao.query(Loja).count() == 0


def test_sem_credenciais_nao_chama_a_api_nem_escreve(banco, monkeypatch):
    monkeypatch.setattr(sistema_interno.settings.sistema_interno, "token", None)
    adaptador = sistema_interno.SistemaInternoLojasAdapter()

    def _nao_deveria_chamar():
        raise AssertionError("não pode chamar a API sem credencial configurada")

    monkeypatch.setattr(adaptador, "_buscar_lojas_api", _nao_deveria_chamar)

    resultado = adaptador.sincronizar()

    assert resultado.status == StatusIntegracao.INDISPONIVEL
    assert resultado.registros_processados == 0


# ---------------------------------------------------------------------------
# Listas das telas: só lojas da rede; rótulo do grupo econômico
# ---------------------------------------------------------------------------

def test_listas_so_da_rede_e_rotulo_do_grupo(banco):
    from core import queries

    with Session(banco) as sessao:
        sessao.add_all([
            Loja(cnpj="1", razao_social="DROGARIA NOVA NACIONAL LTDA", uf="ES", cidade="X", legacy_id=787,
                 grupo_economico="GE787", na_rede=True),
            Loja(cnpj="2", razao_social="DROGARIAS REIS F2 LTDA", uf="ES", cidade="X", legacy_id=789,
                 grupo_economico="GE787", na_rede=True),
            Loja(cnpj="3", razao_social="SO UMA", uf="MG", cidade="Y", legacy_id=60, grupo_economico="GE60"),
            Loja(cnpj="4", razao_social="FORA", uf="SP", cidade="Z", legacy_id=5, grupo_economico="GE5", na_rede=False),
        ])
        sessao.commit()
        assert [l["cnpj"] for l in queries.lista_lojas(sessao)] == ["1", "2", "3"]   # vazio conta como da rede
        assert queries.lista_ufs(sessao) == ["ES", "MG"]
        assert queries.lista_grupos_economicos(sessao) == ["GE60", "GE787"]
        assert queries.rotulos_grupos(sessao) == {"GE60": "GE60 · SO UMA", "GE787": "GE787 · DROGARIA NOVA NACIONAL LTDA"}

