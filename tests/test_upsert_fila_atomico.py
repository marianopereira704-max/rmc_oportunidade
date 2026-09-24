"""Testes do item 6 da reforma: a gravação em `fila_resolucao_ean` deixa de
ser "SELECT pra ver se existe, decide em Python, grava de volta" e passa a
ser um upsert atômico — `INSERT ... ON CONFLICT (ean) DO NOTHING` seguido de
um `UPDATE` que soma o delta em SQL (`coluna = coluna + :delta`), nunca lendo
o valor atual em Python pra escrever de volta.

O problema tinha DUAS faces, e este arquivo prova as duas:

1. Corrida de INSERT: duas sessões resolvendo o MESMO EAN NOVO ao mesmo
   tempo viam ambas "não existe" e ambas tentavam INSERT — a segunda levava
   IntegrityError da UniqueConstraint em `ean`, derrubando aquele bloco do
   processamento. `test_race_real_contra_postgres_sem_excecao_e_sem_perda`
   (marcado, roda só se houver Postgres disponível) prova isso com threads
   e conexões de verdade — SQLite não serializa escritas concorrentes do
   jeito que Postgres faz, então não reproduziria a corrida original.

2. Leitura perdida (lost update): mesmo sem a corrida de INSERT, duas
   chamadas somando no mesmo EAN em sequência (o caso comum, dentro de uma
   única sessão) tinham que continuar batendo o total exatamente igual a
   antes — é o que os testes de equivalência abaixo garantem, rodando em
   SQLite (mais rápido, sem precisar de um Postgres de verdade pra provar
   que a ARITMÉTICA está certa).

Os testes de `upsert_fila_resolucao_em_lote` provam o mecanismo isolado
(sem passar por `resolver_ean`); os de `resolver_ean(..., buffer_fila=...)`
provam que a fase de reconciliação em massa do GPS (que usa esse parâmetro)
aciona o mecanismo certo.
"""
from __future__ import annotations

import os
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from core.models import Base, FilaResolucaoEAN, OrigemFila, StatusFila
from reconciliation import motor


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# upsert_fila_resolucao_em_lote — mecanismo isolado
# ---------------------------------------------------------------------------

def test_lote_cria_linha_nova_com_os_valores_da_chamada(session):
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "111", "descricao_observada": "Produto A", "origem": OrigemFila.GPS,
        "valor": 100.0, "aparece_em_estoque": True, "ocorrencias": 3,
        "sugestao_base_generico_id": None, "sugestao_score": None,
    }])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "111")).scalar_one()
    assert fila.descricao_observada == "Produto A"
    assert fila.origem == OrigemFila.GPS
    assert float(fila.valor_total_acumulado) == 100.0
    assert fila.qtd_ocorrencias == 3
    assert fila.aparece_em_estoque is True
    assert fila.status == StatusFila.PENDENTE


def test_lote_nao_levanta_ao_reprocessar_o_mesmo_ean_no_mesmo_lote(session):
    """Duas entradas do MESMO EAN dentro de um único lote — ex.: um
    reprocesso de CNPJ órfão que releu dois arquivos e agregou por arquivo,
    não por EAN global. `ON CONFLICT DO NOTHING` faz a segunda ocorrência
    dentro do próprio INSERT em massa ser ignorada silenciosamente (nunca
    IntegrityError), e o UPDATE que segue soma as DUAS entradas — nenhuma
    das duas contribuições se perde."""
    motor.upsert_fila_resolucao_em_lote(session, [
        {"ean": "222", "descricao_observada": "A", "origem": OrigemFila.GPS, "valor": 10.0, "ocorrencias": 1},
        {"ean": "222", "descricao_observada": "B", "origem": OrigemFila.GPS, "valor": 20.0, "ocorrencias": 1},
    ])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "222")).scalar_one()
    assert float(fila.valor_total_acumulado) == 30.0
    assert fila.qtd_ocorrencias == 2


def test_lote_soma_em_cima_de_linha_que_ja_existia_de_upload_anterior(session):
    """Linha criada por um upload anterior (fora deste lote) — o UPDATE tem
    que ADICIONAR ao valor antigo, não substituir."""
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "333", "descricao_observada": "Mês 1", "origem": OrigemFila.GPS,
        "valor": 100.0, "ocorrencias": 5,
    }])
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "333", "descricao_observada": "Mês 2", "origem": OrigemFila.GPS,
        "valor": 50.0, "ocorrencias": 2,
    }])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "333")).scalar_one()
    assert float(fila.valor_total_acumulado) == 150.0
    assert fila.qtd_ocorrencias == 7
    assert fila.descricao_observada == "Mês 2"  # última descrição não-vazia vence


def test_lote_descricao_vazia_nao_apaga_a_descricao_existente(session):
    motor.upsert_fila_resolucao_em_lote(session, [
        {"ean": "444", "descricao_observada": "Descrição real", "origem": OrigemFila.GPS, "valor": 10.0},
    ])
    motor.upsert_fila_resolucao_em_lote(session, [
        {"ean": "444", "descricao_observada": "", "origem": OrigemFila.GPS, "valor": 5.0},
    ])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "444")).scalar_one()
    assert fila.descricao_observada == "Descrição real"
    assert float(fila.valor_total_acumulado) == 15.0


def test_lote_sugestao_ausente_nao_apaga_sugestao_existente(session):
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "555", "descricao_observada": "X", "origem": OrigemFila.GPS, "valor": 10.0,
        "sugestao_base_generico_id": 42, "sugestao_score": 88.5,
    }])
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "555", "descricao_observada": "X de novo", "origem": OrigemFila.GPS, "valor": 5.0,
        "sugestao_base_generico_id": None, "sugestao_score": None,
    }])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "555")).scalar_one()
    assert fila.sugestao_base_generico_id == 42
    assert float(fila.sugestao_score) == pytest.approx(88.5)


def test_lote_sugestao_nova_substitui_a_anterior(session):
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "666", "descricao_observada": "X", "origem": OrigemFila.GPS, "valor": 10.0,
        "sugestao_base_generico_id": 1, "sugestao_score": 60.0,
    }])
    motor.upsert_fila_resolucao_em_lote(session, [{
        "ean": "666", "descricao_observada": "X", "origem": OrigemFila.GPS, "valor": 10.0,
        "sugestao_base_generico_id": 2, "sugestao_score": 91.0,
    }])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "666")).scalar_one()
    assert fila.sugestao_base_generico_id == 2
    assert float(fila.sugestao_score) == pytest.approx(91.0)


def test_lote_aparece_em_estoque_uma_vez_verdadeiro_nunca_mais_falso(session):
    motor.upsert_fila_resolucao_em_lote(session, [
        {"ean": "777", "descricao_observada": "X", "origem": OrigemFila.GPS, "valor": 1.0, "aparece_em_estoque": True},
    ])
    motor.upsert_fila_resolucao_em_lote(session, [
        {"ean": "777", "descricao_observada": "X", "origem": OrigemFila.GPS, "valor": 1.0, "aparece_em_estoque": False},
    ])
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "777")).scalar_one()
    assert fila.aparece_em_estoque is True


def test_lote_grava_varios_eans_distintos_em_uma_unica_passada(session):
    itens = [
        {"ean": f"EAN-{i}", "descricao_observada": f"Produto {i}", "origem": OrigemFila.GPS, "valor": float(i)}
        for i in range(50)
    ]
    motor.upsert_fila_resolucao_em_lote(session, itens)
    total = session.execute(select(FilaResolucaoEAN)).scalars().all()
    assert len(total) == 50
    assert {f.ean for f in total} == {f"EAN-{i}" for i in range(50)}


def test_lote_respeita_o_tamanho_do_bloco(session, monkeypatch):
    """Com um bloco pequeno de propósito, um lote maior que ele tem que
    disparar mais de uma chamada de gravação — prova que o chunking
    realmente particiona, não só aceita uma lista grande de uma vez."""
    chamadas = {"total": 0}
    original = motor._gravar_bloco_fila

    def espiao(session, bloco):
        chamadas["total"] += 1
        assert len(bloco) <= 3
        return original(session, bloco)

    monkeypatch.setattr(motor, "_gravar_bloco_fila", espiao)
    monkeypatch.setattr(motor, "_TAMANHO_BLOCO_FILA", 3)

    itens = [{"ean": f"B-{i}", "descricao_observada": "x", "origem": OrigemFila.GPS, "valor": 1.0} for i in range(10)]
    motor.upsert_fila_resolucao_em_lote(session, itens)

    assert chamadas["total"] == 4  # ceil(10/3)
    assert len(session.execute(select(FilaResolucaoEAN)).scalars().all()) == 10


def test_lote_com_lista_vazia_nao_faz_nada(session):
    motor.upsert_fila_resolucao_em_lote(session, [])
    assert session.execute(select(FilaResolucaoEAN)).scalars().all() == []


# ---------------------------------------------------------------------------
# upsert_fila_resolucao (chamada única) continua com o MESMO contrato
# ---------------------------------------------------------------------------

def test_upsert_unico_ainda_devolve_o_objeto_com_id_populado(session):
    fila = motor.upsert_fila_resolucao(
        session, ean="888", descricao_observada="Produto", origem=OrigemFila.GPS, valor=10.0,
    )
    assert fila.id is not None
    assert fila.ean == "888"


def test_upsert_unico_chamado_duas_vezes_seguidas_nao_devolve_objeto_desatualizado(session):
    """A prova direta do detalhe de `populate_existing`. O identity map do
    SQLAlchemy guarda REFERÊNCIA FRACA aos objetos — se ninguém segurar o
    retorno da primeira chamada, o objeto some do identity map sozinho (o
    coletor de lixo recolhe) e a segunda SELECT naturalmente vem fresca do
    banco, mascarando o bug. Por isso este teste GUARDA `fila_primeira_chamada`
    numa variável — é exatamente o padrão real de quem chama
    `upsert_fila_resolucao` e guarda `fila.id` pra usar depois (ver
    `test_confirmar_resolucao_manual`/`test_registrar_novo_generico` em
    test_reconciliation.py). Com a referência viva, sem `populate_existing`
    a segunda chamada devolveria o MESMO objeto Python da primeira, com o
    valor ANTIGO (100) — mesmo o banco já tendo 150 gravado."""
    fila_primeira_chamada = motor.upsert_fila_resolucao(
        session, ean="999", descricao_observada="X", origem=OrigemFila.GPS, valor=100.0,
    )
    fila_segunda_chamada = motor.upsert_fila_resolucao(
        session, ean="999", descricao_observada="X", origem=OrigemFila.GPS, valor=50.0,
    )
    assert fila_primeira_chamada is fila_segunda_chamada, (
        "mesma sessão, mesmo EAN -> o SQLAlchemy reaproveita o MESMO objeto Python "
        "(identity map); o teste depende disso pra testar o cenário certo"
    )
    assert float(fila_segunda_chamada.valor_total_acumulado) == 150.0
    assert fila_segunda_chamada.qtd_ocorrencias == 2

    # E uma releitura independente (fora da função) tem que ver o mesmo —
    # confirma que não é só o valor de retorno que está certo, é a sessão
    # inteira que enxerga o dado atualizado.
    releitura = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "999")).scalar_one()
    assert float(releitura.valor_total_acumulado) == 150.0


# ---------------------------------------------------------------------------
# resolver_ean(..., buffer_fila=...) — o ponto de entrada usado pelo GPS
# ---------------------------------------------------------------------------

def test_resolver_ean_com_buffer_fila_nao_grava_direto_so_acumula(session):
    buffer_fila: list[dict] = []
    resultado = motor.resolver_ean(
        session, ean="AAA", descricao_origem="Produto sem match", origem=OrigemFila.GPS,
        valor=10.0, ocorrencias=4, buffer_fila=buffer_fila,
    )
    assert resultado is None
    assert session.execute(select(FilaResolucaoEAN)).scalars().all() == [], (
        "nada deveria estar gravado ainda — buffer_fila só acumula em memória"
    )
    assert len(buffer_fila) == 1
    assert buffer_fila[0]["ean"] == "AAA"
    assert buffer_fila[0]["ocorrencias"] == 4

    motor.upsert_fila_resolucao_em_lote(session, buffer_fila)
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "AAA")).scalar_one()
    assert float(fila.valor_total_acumulado) == 10.0
    assert fila.qtd_ocorrencias == 4


def test_resolver_ean_ean_ja_resolvido_no_cache_nao_usa_buffer(session):
    """EAN já resolvido nunca chega perto de `buffer_fila` — sai pelo atalho
    de `cache_eans_resolvidos` antes de qualquer decisão de fila."""
    buffer_fila: list[dict] = []
    resultado = motor.resolver_ean(
        session, ean="BBB", descricao_origem="qualquer coisa", origem=OrigemFila.GPS,
        cache_eans_resolvidos={"BBB": 7}, buffer_fila=buffer_fila,
    )
    assert resultado == 7
    assert buffer_fila == []


# ---------------------------------------------------------------------------
# Prova de concorrência real — só roda com um Postgres disponível (a mesma
# instância descartável usada pra validar os itens 1 e 2 desta reforma).
# SQLite não serializa escritas concorrentes entre conexões do jeito que
# Postgres faz (fica esperando lock de arquivo/trava o banco inteiro), então
# não reproduz a corrida original de forma confiável — só Postgres prova.
# ---------------------------------------------------------------------------

_DSN_POSTGRES_TESTE = os.environ.get("RMC_TESTE_POSTGRES_DSN")


@pytest.mark.skipif(not _DSN_POSTGRES_TESTE, reason="defina RMC_TESTE_POSTGRES_DSN para rodar a prova de corrida real")
def test_race_real_contra_postgres_sem_excecao_e_sem_perda():
    engine = create_engine(_DSN_POSTGRES_TESTE, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    n_threads = 20
    valor_por_thread = 17.5
    ean_alvo = "7899999990000"
    erros = []
    barreira = threading.Barrier(n_threads)

    def trabalhar(indice):
        try:
            barreira.wait()
            with SessionLocal() as session:
                motor.upsert_fila_resolucao(
                    session, ean=ean_alvo, descricao_observada=f"THREAD-{indice}",
                    origem=OrigemFila.GPS, valor=valor_por_thread, ocorrencias=1,
                )
                session.commit()
        except Exception as exc:  # nunca deveria acontecer — é isso que o teste prova
            erros.append((indice, repr(exc)))

    threads = [threading.Thread(target=trabalhar, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not erros, f"threads levantaram exceção — a corrida de INSERT não foi eliminada: {erros}"

    with SessionLocal() as session:
        linhas = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == ean_alvo)).scalars().all()
        assert len(linhas) == 1
        assert float(linhas[0].valor_total_acumulado) == n_threads * valor_por_thread
        assert linhas[0].qtd_ocorrencias == n_threads
