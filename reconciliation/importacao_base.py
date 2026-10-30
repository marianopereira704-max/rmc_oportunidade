"""Importação da planilha Base Genéricos (curada por gente: a descrição JÁ É o nome canônico, sem fuzzy-match) —
parte do antigo motor.py, dividido em 29/10/2026 (etapa 8 do plano de ação)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import (
    BaseGenerico,
    EanGenerico,
    OrigemResolucao,
)
from core.sql import insert_ignorando_conflito
from reconciliation.fuzzy import _TAMANHO_LOTE_EANS

@dataclass
class ResultadoImportacaoBase:
    genericos_criados: int
    genericos_reaproveitados: int
    eans_vinculados: int
    eans_pulados: int
    linhas_invalidas: int


def importar_base_genericos(
    session: Session, linhas: list[tuple[str, str]], criado_por: str
) -> ResultadoImportacaoBase:
    """Importação em massa da Base Genéricos a partir de uma planilha CURADA
    por humano (EAN + descrição já revisada) — bem diferente de `resolver_ean`:
    aqui NÃO tem fuzzy-match nenhum, a descrição da planilha É o nome
    canônico direto, porque já veio decidida por gente.

    Duas regras centrais:
    - Agrupamento por descrição idêntica: todas as linhas com a mesma
      descrição apontam pro MESMO BaseGenerico — criado uma vez só na
      primeira ocorrência (nas próximas, reaproveitado, tanto se já existia
      no banco antes desta importação quanto se foi criado agora mesmo,
      nesta mesma chamada).
    - Idempotência por EAN: um EAN que já tem QUALQUER resolução em
      EanGenerico (automática, manual, ou de uma importação anterior) nunca
      é sobrescrito — só contado como pulado. `linhas` já deve vir com o EAN
      normalizado (ver `reconciliation.normalizador.normalizar_ean`).

    Cada vínculo novo criado aqui fica com origem_resolucao=IMPORTADA —
    rastreável em separado de AUTOMATICA (fuzzy-match) e MANUAL (fila).

    Tudo em LOTE: antes era 1 SELECT por descrição nova + 1 INSERT com flush
    por genérico criado — numa base vazia (2.210 genéricos, 7.093 EANs), ~4.400
    idas e voltas ao Postgres em NY, vários minutos com a tela parada (medido
    em 24/09/2026 ao carregar a base no banco do app publicado). Agora: a
    decisão do que é novo é feita em memória, os genéricos existentes vêm em
    blocos de nomes, e genéricos e vínculos são gravados em blocos com
    `ON CONFLICT DO NOTHING` (seguro mesmo com dois envios ao mesmo tempo)."""
    session.flush()  # o que já estava pendente na sessão entra antes das leituras
    linhas_invalidas = 0
    eans_pulados = 0
    eans_ja_resolvidos = set(session.execute(select(EanGenerico.ean)).scalars())

    # 1. Decide em memória: o que é inválido, o que já está resolvido e o
    #    que é vínculo novo (a primeira ocorrência de cada EAN na planilha).
    novos: dict[str, str] = {}
    for ean, descricao in linhas:
        ean = (ean or "").strip()
        descricao = (descricao or "").strip()
        if not ean or not descricao:
            linhas_invalidas += 1
            continue
        if ean in eans_ja_resolvidos or ean in novos:
            eans_pulados += 1
            continue
        novos[ean] = descricao

    # 2. Genéricos: busca os que já existem (em blocos de nomes) e cria só os
    #    que faltam — mesma descrição = mesmo genérico.
    nomes = sorted(set(novos.values()))
    id_por_nome = _ids_de_genericos(session, nomes)
    faltando = [nome for nome in nomes if nome not in id_por_nome]
    genericos_reaproveitados = len(id_por_nome)
    for inicio in range(0, len(faltando), _TAMANHO_LOTE_EANS):
        bloco = faltando[inicio:inicio + _TAMANHO_LOTE_EANS]
        session.execute(
            insert_ignorando_conflito(session, BaseGenerico.__table__, ["nome_canonico"]),
            [{"nome_canonico": nome, "ativo": True, "criado_por": criado_por} for nome in bloco],
        )
    id_por_nome.update(_ids_de_genericos(session, faltando))

    # 3. Vínculos EAN -> genérico, em blocos.
    agora = dt.datetime.utcnow()
    vinculos = [
        {
            "ean": ean, "base_generico_id": id_por_nome[descricao],
            "origem_resolucao": OrigemResolucao.IMPORTADA, "score_similaridade": None,
            "descricao_origem_snapshot": descricao, "resolvido_por": criado_por, "resolvido_em": agora,
        }
        for ean, descricao in novos.items()
    ]
    for inicio in range(0, len(vinculos), _TAMANHO_LOTE_EANS):
        session.execute(
            insert_ignorando_conflito(session, EanGenerico.__table__, ["ean"]),
            vinculos[inicio:inicio + _TAMANHO_LOTE_EANS],
        )

    return ResultadoImportacaoBase(
        genericos_criados=len(faltando),
        genericos_reaproveitados=genericos_reaproveitados,
        eans_vinculados=len(vinculos),
        eans_pulados=eans_pulados,
        linhas_invalidas=linhas_invalidas,
    )


def _ids_de_genericos(session: Session, nomes: list[str]) -> dict[str, int]:
    """nome canônico -> id, em blocos (nunca uma consulta por nome)."""
    ids: dict[str, int] = {}
    for inicio in range(0, len(nomes), _TAMANHO_LOTE_EANS):
        bloco = nomes[inicio:inicio + _TAMANHO_LOTE_EANS]
        ids.update(session.execute(
            select(BaseGenerico.nome_canonico, BaseGenerico.id).where(BaseGenerico.nome_canonico.in_(bloco))
        ).tuples().all())
    return ids
