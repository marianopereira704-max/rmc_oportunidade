"""Testes do controle de rotinas automáticas (core/rotinas.py).

O que precisa ficar travado aqui, em ordem de importância:

1. Duas execuções simultâneas da mesma rotina não podem acontecer. É o mesmo
   tipo de corrida que já apareceu na fila de EAN; a defesa é um UPDATE
   condicional atômico, e o teste contra Postgres real (no fim do arquivo)
   prova isso com concorrência de verdade, não simulada.
2. Falha não pode ser confundida com sucesso. Uma rotina que falhou hoje tem
   que continuar tentando; se contasse como "já rodou", uma indisponibilidade
   de 1 minuto da API externa deixaria o dado velho por 24h.
3. A execução nunca pode levantar exceção pra quem chamou — é chamada no topo
   de uma tela, e uma exceção ali derruba a tela inteira.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core import rotinas
from core.models import Base, ControleRotina

ROTINA = "rotina_de_teste"


@pytest.fixture()
def fabrica(tmp_path):
    """SQLite em ARQUIVO (não `:memory:`): `executar_se_necessario` abre uma
    sessão por etapa de propósito (ver a docstring dela), e sessões
    independentes precisam enxergar o mesmo banco."""
    engine = create_engine(f"sqlite:///{tmp_path / 'rotinas.db'}", future=True)
    Base.metadata.create_all(engine)
    Sessao = sessionmaker(bind=engine, future=True)

    @contextmanager
    def _fabrica():
        sessao = Sessao()
        try:
            yield sessao
            sessao.commit()
        except Exception:
            sessao.rollback()
            raise
        finally:
            sessao.close()

    _fabrica.engine = engine
    _fabrica.Sessao = Sessao
    return _fabrica


# ---------------------------------------------------------------------------
# precisa_rodar / registro de resultado
# ---------------------------------------------------------------------------

def test_precisa_rodar_quando_a_rotina_nunca_rodou(fabrica):
    with fabrica() as sessao:
        assert rotinas.precisa_rodar(sessao, ROTINA) is True


def test_nao_precisa_rodar_depois_de_sucesso_no_mesmo_dia(fabrica):
    agora = dt.datetime(2026, 9, 15, 9, 0)
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, mensagem="ok", agora=agora)

    with fabrica() as sessao:
        mais_tarde = dt.datetime(2026, 9, 15, 23, 59)
        assert rotinas.precisa_rodar(sessao, ROTINA, agora=mais_tarde) is False


def test_precisa_rodar_de_novo_no_dia_seguinte(fabrica):
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 23, 59))

    with fabrica() as sessao:
        assert rotinas.precisa_rodar(sessao, ROTINA, agora=dt.datetime(2026, 9, 16, 0, 1)) is True


def test_falha_nao_conta_como_ja_rodou_hoje(fabrica):
    """O ponto mais importante do módulo: erro transitório da API externa não
    pode congelar a rotina até o dia seguinte."""
    agora = dt.datetime(2026, 9, 15, 9, 0)
    with fabrica() as sessao:
        rotinas.registrar_falha(sessao, ROTINA, "API fora do ar", agora=agora)

    with fabrica() as sessao:
        assert rotinas.precisa_rodar(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 9, 30)) is True


def test_registrar_sucesso_limpa_o_erro_anterior(fabrica):
    with fabrica() as sessao:
        rotinas.registrar_falha(sessao, ROTINA, "caiu", agora=dt.datetime(2026, 9, 15, 9, 0))
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, mensagem="805 lojas", agora=dt.datetime(2026, 9, 15, 10, 0))

    with fabrica() as sessao:
        controle = rotinas.obter(sessao, ROTINA)
        assert controle.ultimo_erro is None
        assert controle.ultima_mensagem == "805 lojas"


def test_registrar_falha_nao_apaga_o_ultimo_sucesso(fabrica):
    sucesso_em = dt.datetime(2026, 9, 14, 9, 0)
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, agora=sucesso_em)
    with fabrica() as sessao:
        rotinas.registrar_falha(sessao, ROTINA, "caiu", agora=dt.datetime(2026, 9, 15, 9, 0))

    with fabrica() as sessao:
        controle = rotinas.obter(sessao, ROTINA)
        assert controle.ultimo_sucesso_em == sucesso_em
        assert controle.ultimo_erro == "caiu"


def test_garantir_nao_duplica_a_linha_da_rotina(fabrica):
    with fabrica() as sessao:
        rotinas.garantir(sessao, ROTINA)
        rotinas.garantir(sessao, ROTINA)
    with fabrica() as sessao:
        rotinas.garantir(sessao, ROTINA)

    with fabrica() as sessao:
        assert sessao.query(ControleRotina).filter_by(nome=ROTINA).count() == 1


# ---------------------------------------------------------------------------
# reivindicar — a trava contra execução dupla
# ---------------------------------------------------------------------------

def test_reivindicar_vence_na_primeira_e_perde_na_segunda(fabrica):
    agora = dt.datetime(2026, 9, 15, 9, 0)
    with fabrica() as sessao:
        assert rotinas.reivindicar(sessao, ROTINA, agora=agora) is True

    # Segundo processo, logo em seguida: a rotina está reivindicada há pouco.
    with fabrica() as sessao:
        assert rotinas.reivindicar(sessao, ROTINA, agora=agora + dt.timedelta(seconds=2)) is False


def test_reivindicar_perde_se_ja_houve_sucesso_hoje(fabrica):
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 8, 0))

    with fabrica() as sessao:
        # Bem depois da janela de tentativa, mas ainda no mesmo dia.
        assert rotinas.reivindicar(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 20, 0)) is False


def test_reivindicar_volta_a_ser_possivel_depois_da_janela_de_tentativa(fabrica):
    """Processo que morreu no meio da rotina não pode travá-la pelo resto do
    dia: passada a janela, outro processo pode assumir."""
    agora = dt.datetime(2026, 9, 15, 9, 0)
    with fabrica() as sessao:
        assert rotinas.reivindicar(sessao, ROTINA, agora=agora, janela_tentativa_minutos=15) is True

    with fabrica() as sessao:
        depois = agora + dt.timedelta(minutes=16)
        assert rotinas.reivindicar(sessao, ROTINA, agora=depois, janela_tentativa_minutos=15) is True


def test_reivindicar_forcado_ignora_o_ja_rodou_hoje(fabrica):
    with fabrica() as sessao:
        rotinas.registrar_sucesso(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 8, 0))

    with fabrica() as sessao:
        rotinas.reivindicar_forcado(sessao, ROTINA, agora=dt.datetime(2026, 9, 15, 8, 30))

    with fabrica() as sessao:
        controle = rotinas.obter(sessao, ROTINA)
        assert controle.ultima_tentativa_em == dt.datetime(2026, 9, 15, 8, 30)


# ---------------------------------------------------------------------------
# executar_se_necessario / executar_forcado
# ---------------------------------------------------------------------------

def test_executar_se_necessario_roda_uma_vez_e_pula_na_segunda(fabrica):
    chamadas = []

    def _funcao():
        chamadas.append(1)
        return "sincronizou"

    agora = dt.datetime(2026, 9, 15, 9, 0)
    primeiro = rotinas.executar_se_necessario(ROTINA, _funcao, agora=agora, fabrica_sessao=fabrica)
    segundo = rotinas.executar_se_necessario(
        ROTINA, _funcao, agora=agora + dt.timedelta(hours=2), fabrica_sessao=fabrica
    )

    assert primeiro.executou is True and primeiro.sucesso is True
    assert primeiro.mensagem == "sincronizou"
    assert segundo.executou is False and segundo.motivo_pulo
    assert len(chamadas) == 1, "a rotina não pode rodar duas vezes no mesmo dia"


def test_executar_se_necessario_nao_levanta_quando_a_funcao_falha(fabrica):
    """Chamado no topo de uma tela: uma exceção aqui derrubaria a aba inteira."""
    def _funcao():
        raise RuntimeError("API fora do ar")

    resultado = rotinas.executar_se_necessario(
        ROTINA, _funcao, agora=dt.datetime(2026, 9, 15, 9, 0), fabrica_sessao=fabrica
    )

    assert resultado.executou is True
    assert resultado.sucesso is False
    assert "API fora do ar" in resultado.erro
    with fabrica() as sessao:
        controle = rotinas.obter(sessao, ROTINA)
        assert "API fora do ar" in controle.ultimo_erro
        assert controle.ultimo_sucesso_em is None


def test_depois_de_falhar_a_rotina_tenta_de_novo_no_mesmo_dia(fabrica):
    tentativas = []

    def _falha():
        tentativas.append("erro")
        raise RuntimeError("caiu")

    def _sucesso():
        tentativas.append("ok")
        return "deu certo"

    agora = dt.datetime(2026, 9, 15, 9, 0)
    rotinas.executar_se_necessario(ROTINA, _falha, agora=agora, fabrica_sessao=fabrica)
    # Passada a janela de tentativa, no mesmo dia, tem que tentar de novo.
    resultado = rotinas.executar_se_necessario(
        ROTINA, _sucesso, agora=agora + dt.timedelta(minutes=20), fabrica_sessao=fabrica
    )

    assert tentativas == ["erro", "ok"]
    assert resultado.sucesso is True


def test_executar_forcado_roda_mesmo_tendo_rodado_hoje(fabrica):
    chamadas = []
    agora = dt.datetime(2026, 9, 15, 9, 0)

    rotinas.executar_se_necessario(
        ROTINA, lambda: chamadas.append("auto") or "auto", agora=agora, fabrica_sessao=fabrica
    )
    resultado = rotinas.executar_forcado(
        ROTINA, lambda: chamadas.append("manual") or "manual",
        agora=agora + dt.timedelta(minutes=1), fabrica_sessao=fabrica,
    )

    assert chamadas == ["auto", "manual"]
    assert resultado.executou is True and resultado.sucesso is True


def test_executar_forcado_tambem_nao_levanta(fabrica):
    def _funcao():
        raise ValueError("token inválido")

    resultado = rotinas.executar_forcado(ROTINA, _funcao, fabrica_sessao=fabrica)

    assert resultado.sucesso is False
    assert "token inválido" in resultado.erro


# ---------------------------------------------------------------------------
# Concorrência real (Postgres) — mesmo padrão já usado no item 6
# ---------------------------------------------------------------------------

_DSN_POSTGRES = os.environ.get("RMC_TESTE_POSTGRES_DSN")


@pytest.mark.skipif(not _DSN_POSTGRES, reason="defina RMC_TESTE_POSTGRES_DSN para rodar contra Postgres real")
def test_race_real_contra_postgres_so_um_processo_ganha_a_rotina():
    """Vários processos disparando a MESMA rotina no mesmo instante: exatamente
    um pode ganhar. Com SQLite isso não se prova de verdade (o banco serializa
    tudo); aqui são conexões independentes contra um Postgres real, soltas ao
    mesmo tempo por uma barreira."""
    engine = create_engine(_DSN_POSTGRES, future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as sessao:
        sessao.query(ControleRotina).filter_by(nome=ROTINA).delete()
        sessao.commit()

    total = 20
    barreira = threading.Barrier(total)
    vitorias: list[bool] = []
    trava = threading.Lock()
    agora = dt.datetime(2026, 9, 15, 9, 0)

    def _tentar():
        engine_local = create_engine(_DSN_POSTGRES, future=True)
        with Session(engine_local) as sessao:
            barreira.wait()
            ganhou = rotinas.reivindicar(sessao, ROTINA, agora=agora)
            sessao.commit()
        with trava:
            vitorias.append(ganhou)

    threads = [threading.Thread(target=_tentar) for _ in range(total)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for v in vitorias if v) == 1, (
        f"exatamente um processo deveria ganhar a rotina, ganharam {sum(1 for v in vitorias if v)}"
    )


def test_trava_renovada_nao_expira_e_trava_abandonada_expira(tmp_path):
    """Envio vivo renova e segura a trava; envio que morreu sem liberar para
    de renovar e, passada a janela, outro envio consegue a trava."""
    import datetime as _dt

    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import Session as _S

    from core.models import Base as _Base

    engine = _ce(f"sqlite:///{tmp_path / 'trava.db'}", future=True)
    _Base.metadata.create_all(engine)
    t0 = _dt.datetime(2026, 9, 24, 12, 0)
    with _S(engine) as s:
        assert rotinas.reivindicar_trava(s, "upload_gps", agora=t0, janela_tentativa_minutos=5)
        rotinas.renovar_trava(s, "upload_gps", agora=t0 + _dt.timedelta(minutes=4))
        assert not rotinas.reivindicar_trava(s, "upload_gps", agora=t0 + _dt.timedelta(minutes=8), janela_tentativa_minutos=5)
        assert rotinas.reivindicar_trava(s, "upload_gps", agora=t0 + _dt.timedelta(minutes=10), janela_tentativa_minutos=5)
