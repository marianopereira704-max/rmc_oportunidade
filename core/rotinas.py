"""Controle das rotinas automáticas (item 15/16 do plano de reforma).

O problema que isto resolve: o sistema tem trabalhos que precisam acontecer
"de vez em quando" (sincronizar lojas, reprocessar a fila de EAN, no futuro
buscar compras na API do GPS). Hoje cada um deles é um botão que alguém
precisa lembrar de clicar — e quando esquecem, o sistema fica com dado velho
sem avisar ninguém.

O padrão implementado aqui é sempre o mesmo, e é o que já foi decidido no
plano: ao abrir a tela, verifica se a rotina já rodou hoje; se não rodou,
roda; se falhar, registra a falha e devolve o resultado pra tela mostrar um
aviso — NUNCA derruba a tela nem apaga o dado que já estava lá. O botão
manual continua existindo como "forçar agora".

Duas decisões que valem explicar:

1. Concorrência é tratada por construção, não por sorte. Dois usuários
   abrindo a tela ao mesmo tempo (ou a mesma pessoa em duas abas) fariam a
   MESMA verificação ao mesmo tempo, os dois veriam "não rodou hoje", e os
   dois disparariam a rotina — uma sincronização dupla contra a API externa,
   e duas escritas concorrentes no banco. É o mesmo tipo de corrida que já
   apareceu na fila de EAN e foi corrigida com `ON CONFLICT` (ver
   reconciliation/motor.py). Aqui a solução é a mesma família: `reivindicar`
   faz um UPDATE condicional atômico e usa o `rowcount` como resposta — quem
   conseguiu alterar a linha ganhou o direito de rodar; quem não conseguiu
   simplesmente não roda. O banco desempata, não o Python.

2. Todos os horários são UTC, como o resto do projeto (`dt.datetime.utcnow`
   em todos os models). Isso significa que "hoje" é o dia em UTC, não no
   fuso de Brasília — uma rotina rodada às 22h de Brasília conta como o dia
   seguinte. Para o uso real (rodar uma vez por dia, em horário comercial)
   isso não muda nada; misturar fusos entre este módulo e as colunas de
   data do banco é que causaria confusão de verdade.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from core.db import get_session
from core.models import ControleRotina
from core.sql import insert_ignorando_conflito

# Nomes canônicos das rotinas. Use SEMPRE estas constantes em vez de digitar
# a string — o nome é a chave única da linha no banco, e um typo criaria uma
# segunda rotina silenciosamente, com o efeito de nunca mais considerar a
# original como "já rodada".
ROTINA_SINCRONIZACAO_LOJAS = "sincronizacao_lojas"
ROTINA_SINCRONIZACAO_GPS = "sincronizacao_gps"
ROTINA_REPROCESSAMENTO_FILA_EAN = "reprocessamento_fila_ean"
# Genéricos vendidos nas lojas (API do GPS) que faltam na Base Genéricos →
# fila de EAN "Loja (API)" (integrations/fila_loja.py), uma vez por dia.
ROTINA_FILA_EAN_LOJA_API = "fila_ean_loja_api"
# Genéricos COMPRADOS (compras da API, 05/10/2026) fora da Base Genéricos →
# fila de EAN origem GPS, e o valor da fila regravado (integrations/fila_compras.py).
ROTINA_FILA_EAN_COMPRAS = "fila_ean_compras"
# Não é rotina diária: é a trava de "um processamento de planilha GPS por vez"
# (ver `reivindicar_trava`/`liberar_trava`).
ROTINA_UPLOAD_GPS = "upload_gps"

# Por quanto tempo uma tentativa "segura" a rotina. Uma rotina que começou há
# menos que isso é considerada em andamento e não é reivindicada de novo.
# Precisa ser maior que a duração normal da rotina (a sincronização de lojas
# real já levou ~5 minutos) e pequeno o suficiente pra que um processo morto
# no meio do caminho não bloqueie a rotina pelo resto do dia.
JANELA_TENTATIVA_PADRAO_MINUTOS = 15


@dataclass
class ResultadoExecucao:
    """O que aconteceu numa chamada de `executar_se_necessario`.

    `executou=False` não é erro: é o caso normal de "já rodou hoje" ou "outro
    processo está rodando agora". A tela não deve mostrar nada nesse caso.
    """
    executou: bool
    sucesso: bool
    mensagem: str | None = None
    erro: str | None = None
    motivo_pulo: str | None = None


def _agora(agora: dt.datetime | None) -> dt.datetime:
    return agora if agora is not None else dt.datetime.utcnow()


def garantir(session: Session, nome: str) -> None:
    """Garante que a linha da rotina existe, sem corrida: dois processos
    chamando isto ao mesmo tempo não geram IntegrityError nem linha
    duplicada, porque o próprio banco ignora o conflito de `nome`."""
    stmt = insert_ignorando_conflito(session, ControleRotina.__table__, ["nome"])
    session.execute(stmt, [{"nome": nome, "atualizado_em": dt.datetime.utcnow()}])


def obter(session: Session, nome: str) -> ControleRotina | None:
    return session.execute(
        select(ControleRotina).where(ControleRotina.nome == nome)
    ).scalar_one_or_none()


def _inicio_do_dia(agora: dt.datetime) -> dt.datetime:
    return dt.datetime(agora.year, agora.month, agora.day)


def reivindicar(
    session: Session,
    nome: str,
    agora: dt.datetime | None = None,
    janela_tentativa_minutos: int = JANELA_TENTATIVA_PADRAO_MINUTOS,
) -> bool:
    """Tenta ganhar o direito de rodar a rotina agora. Devolve True pra UM
    chamador só, mesmo que vários tentem ao mesmo tempo.

    Como: um único UPDATE que só altera a linha se ela ainda satisfaz as duas
    condições (nenhum sucesso hoje E nenhuma tentativa recente), carimbando
    `ultima_tentativa_em` no mesmo golpe. O banco serializa os UPDATEs na
    mesma linha, então o segundo chamador encontra a linha já fora das
    condições e recebe `rowcount == 0`. Não existe janela entre "verificar" e
    "marcar" — é a mesma instrução.

    `dml_strategy="core_only"` é necessário porque o WHERE não é por chave
    primária: sem isso o SQLAlchemy 2.0 tenta a heurística de UPDATE em massa
    do ORM e recusa a instrução."""
    agora = _agora(agora)
    garantir(session, nome)
    session.flush()

    limite_tentativa = agora - dt.timedelta(minutes=janela_tentativa_minutos)
    stmt = (
        update(ControleRotina)
        .where(
            ControleRotina.nome == nome,
            or_(
                ControleRotina.ultimo_sucesso_em.is_(None),
                ControleRotina.ultimo_sucesso_em < _inicio_do_dia(agora),
            ),
            or_(
                ControleRotina.ultima_tentativa_em.is_(None),
                ControleRotina.ultima_tentativa_em < limite_tentativa,
            ),
        )
        .values(ultima_tentativa_em=agora, atualizado_em=agora)
        .execution_options(synchronize_session=False, dml_strategy="core_only")
    )
    return session.execute(stmt).rowcount == 1


def reivindicar_forcado(
    session: Session, nome: str, agora: dt.datetime | None = None
) -> None:
    """Carimba a tentativa sem nenhuma condição — é o "forçar agora" do botão
    manual, que roda mesmo que já tenha rodado hoje."""
    agora = _agora(agora)
    garantir(session, nome)
    session.flush()
    session.execute(
        update(ControleRotina)
        .where(ControleRotina.nome == nome)
        .values(ultima_tentativa_em=agora, atualizado_em=agora)
        .execution_options(synchronize_session=False, dml_strategy="core_only")
    )


def registrar_sucesso(
    session: Session, nome: str, mensagem: str | None = None, agora: dt.datetime | None = None
) -> None:
    """Marca sucesso e LIMPA o último erro — um erro antigo não pode continuar
    aparecendo na tela depois que a rotina voltou a funcionar."""
    agora = _agora(agora)
    garantir(session, nome)
    session.flush()
    session.execute(
        update(ControleRotina)
        .where(ControleRotina.nome == nome)
        .values(ultimo_sucesso_em=agora, ultima_mensagem=mensagem, ultimo_erro=None, atualizado_em=agora)
        .execution_options(synchronize_session=False, dml_strategy="core_only")
    )


def registrar_falha(
    session: Session, nome: str, erro: str, agora: dt.datetime | None = None
) -> None:
    """Marca a falha SEM tocar em `ultimo_sucesso_em` — ver a docstring do
    model: falha não pode ser confundida com "já rodou hoje"."""
    agora = _agora(agora)
    garantir(session, nome)
    session.flush()
    session.execute(
        update(ControleRotina)
        .where(ControleRotina.nome == nome)
        .values(ultimo_erro=erro, atualizado_em=agora)
        .execution_options(synchronize_session=False, dml_strategy="core_only")
    )


@contextmanager
def _sessao_padrao() -> Iterator[Session]:
    with get_session() as session:
        yield session


def executar_se_necessario(
    nome: str,
    funcao: Callable[[], str | None],
    agora: dt.datetime | None = None,
    janela_tentativa_minutos: int = JANELA_TENTATIVA_PADRAO_MINUTOS,
    fabrica_sessao: Callable[[], Iterator[Session]] | None = None,
) -> ResultadoExecucao:
    """Roda `funcao` no máximo uma vez por dia, registrando o resultado.

    NUNCA levanta exceção: qualquer erro da `funcao` é capturado, gravado em
    `ultimo_erro` e devolvido em `ResultadoExecucao.erro`. É o que permite
    chamar isto no topo de uma tela sem risco de a tela inteira quebrar por
    causa de uma API externa fora do ar — a falha vira um aviso, e o dado que
    já estava no banco continua visível.

    Cada etapa abre a sua PRÓPRIA sessão, de propósito: a reivindicação
    precisa ser efetivada (commit) antes de a rotina começar, senão outro
    processo não enxergaria a marca e rodaria em paralelo; e manter uma
    transação aberta durante uma chamada HTTP lenta seguraria conexão do pool
    à toa.

    `funcao` pode devolver uma string, que vira a mensagem registrada (ex: a
    mensagem de `ResultadoSincronizacao`). `fabrica_sessao` existe pros testes
    poderem usar um banco em memória sem depender do engine global."""
    fabrica = fabrica_sessao or _sessao_padrao

    with fabrica() as sessao_claim:
        ganhou = reivindicar(sessao_claim, nome, agora=agora, janela_tentativa_minutos=janela_tentativa_minutos)
    if not ganhou:
        return ResultadoExecucao(
            executou=False,
            sucesso=False,
            motivo_pulo="já rodou hoje ou está rodando agora em outro processo",
        )

    try:
        mensagem = funcao()
    except Exception as exc:  # noqa: BLE001 — a intenção é justamente não deixar vazar
        erro = f"{type(exc).__name__}: {exc}"
        with fabrica() as sessao_erro:
            registrar_falha(sessao_erro, nome, erro, agora=agora)
        return ResultadoExecucao(executou=True, sucesso=False, erro=erro)

    mensagem = mensagem if isinstance(mensagem, str) else None
    with fabrica() as sessao_ok:
        registrar_sucesso(sessao_ok, nome, mensagem=mensagem, agora=agora)
    return ResultadoExecucao(executou=True, sucesso=True, mensagem=mensagem)


def executar_forcado(
    nome: str,
    funcao: Callable[[], str | None],
    agora: dt.datetime | None = None,
    fabrica_sessao: Callable[[], Iterator[Session]] | None = None,
) -> ResultadoExecucao:
    """Mesma gravação de resultado de `executar_se_necessario`, mas sem a
    verificação de "já rodou hoje" — é o botão manual. Também não levanta
    exceção, pelo mesmo motivo."""
    fabrica = fabrica_sessao or _sessao_padrao

    with fabrica() as sessao_claim:
        reivindicar_forcado(sessao_claim, nome, agora=agora)

    try:
        mensagem = funcao()
    except Exception as exc:  # noqa: BLE001
        erro = f"{type(exc).__name__}: {exc}"
        with fabrica() as sessao_erro:
            registrar_falha(sessao_erro, nome, erro, agora=agora)
        return ResultadoExecucao(executou=True, sucesso=False, erro=erro)

    mensagem = mensagem if isinstance(mensagem, str) else None
    with fabrica() as sessao_ok:
        registrar_sucesso(sessao_ok, nome, mensagem=mensagem, agora=agora)
    return ResultadoExecucao(executou=True, sucesso=True, mensagem=mensagem)
