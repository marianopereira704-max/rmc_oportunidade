"""Fila de Resolução de EAN: gravar em lote (atômico), confirmar, cadastrar genérico novo, ignorar, listar e
somar — parte do antigo motor.py, dividido em 29/10/2026 (etapa 8 do plano de ação)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, bindparam, case, func, or_, select, update
from sqlalchemy.orm import Session

from core.models import (
    BaseGenerico,
    EanGenerico,
    FilaResolucaoEAN,
    OrigemFila,
    OrigemResolucao,
    StatusFila,
)
from core.sql import insert_ignorando_conflito

_TAMANHO_BLOCO_FILA = 5000  # itens (EANs distintos) por bloco de upsert atômico.


# Mora em core/sql.py desde que passou a ser usada também pelo controle de
# rotinas (core/rotinas.py) — o alias privado continua aqui só pra não mudar
# as chamadas deste módulo, que são a razão de ela existir.
_insert_ignorando_conflito = insert_ignorando_conflito


def upsert_fila_resolucao_em_lote(session: Session, itens: list[dict]) -> None:
    """Versão em lote de `upsert_fila_resolucao` (ver lá pro caso de 1 item):
    ITENS é uma lista de dicts, um por EAN DISTINTO pendente, já com
    valor/ocorrências agregados de uma passada (ver
    `integrations/gps.py::_processar_linhas`, fase de reconciliação — 1
    chamada por EAN distinto, nunca por linha).

    Cada dict aceita as mesmas chaves que os parâmetros de
    `upsert_fila_resolucao`: `ean`, `descricao_observada`, `origem`,
    `valor` (default 0), `aparece_em_estoque` (default False),
    `sugestao_base_generico_id`/`sugestao_score` (default None),
    `ocorrencias` (default 1).

    Atômica por construção — a corrida de duas sessões inserindo o MESMO
    EAN novo ao mesmo tempo deixa de existir, não fica só mais rara:

    1. Um único `INSERT ... ON CONFLICT (ean) DO NOTHING` para TODOS os
       itens do lote. Cria a linha se ainda não existe, com os campos
       ADITIVOS (valor/ocorrências/estoque) sempre ZERADOS — nunca com o
       valor desta chamada, que entraria em dobro se a linha já existisse
       (ver passo 2). O próprio banco resolve o conflito: nenhuma sessão
       recebe IntegrityError por chegar depois.
    2. Um único `UPDATE` incondicional, para TODOS os itens, que soma o
       delta desta chamada em SQL (`coluna = coluna + :delta`) — nunca lê o
       valor atual em Python pra escrever de volta. Roda pra linha
       recém-criada (0 + delta = delta, correto) e pra linha que já existia
       de antes (valor_antigo + delta, correto), sem precisar saber qual é
       qual. Cada UPDATE de linha é atômico no próprio banco: duas sessões
       somando no MESMO EAN ao mesmo tempo serializam pelo lock de linha, e
       os dois deltas somam certo — não é mais um "ler em Python, decidir,
       escrever de volta" onde a segunda escrita pode apagar a primeira.

    Descrição e sugestão são campos categóricos (substituem, não somam): o
    UPDATE só troca `descricao_observada` quando a chamada trouxe uma
    descrição não-vazia, e só troca a sugestão quando a chamada trouxe uma
    (`sugestao_base_generico_id is not None`) — mesma regra de sempre,
    aplicada via `CASE` em SQL em vez de `if` em Python.

    Roda em blocos de `_TAMANHO_BLOCO_FILA` pra uma reconciliação com muitos
    milhares de EANs pendentes de uma vez não montar uma única instrução
    com todos eles."""
    for inicio in range(0, len(itens), _TAMANHO_BLOCO_FILA):
        _gravar_bloco_fila(session, itens[inicio : inicio + _TAMANHO_BLOCO_FILA])


def _gravar_bloco_fila(session: Session, bloco: list[dict]) -> None:
    if not bloco:
        return

    valores_insercao = [
        {
            "ean": item["ean"],
            "descricao_observada": item.get("descricao_observada") or "",
            "origem": item["origem"],
            "sugestao_base_generico_id": item.get("sugestao_base_generico_id"),
            "sugestao_score": item.get("sugestao_score"),
            # Zerados de propósito — ver docstring de upsert_fila_resolucao_em_lote:
            # o UPDATE incondicional logo abaixo é quem grava o valor real,
            # tanto pra linha recém-criada quanto pra linha que já existia.
            "valor_total_acumulado": 0,
            "aparece_em_estoque": False,
            "qtd_ocorrencias": 0,
            "status": StatusFila.PENDENTE,
        }
        for item in bloco
    ]
    stmt_insercao = _insert_ignorando_conflito(session, FilaResolucaoEAN.__table__, ["ean"])
    session.execute(stmt_insercao, valores_insercao)

    stmt_atualizacao = (
        update(FilaResolucaoEAN)
        .where(FilaResolucaoEAN.ean == bindparam("_ean"))
        .values(
            valor_total_acumulado=FilaResolucaoEAN.valor_total_acumulado + bindparam("_delta_valor"),
            qtd_ocorrencias=FilaResolucaoEAN.qtd_ocorrencias + bindparam("_delta_ocorrencias"),
            aparece_em_estoque=or_(FilaResolucaoEAN.aparece_em_estoque, bindparam("_novo_estoque")),
            descricao_observada=case(
                (bindparam("_tem_descricao", type_=Boolean), bindparam("_nova_descricao")),
                else_=FilaResolucaoEAN.descricao_observada,
            ),
            sugestao_base_generico_id=case(
                (bindparam("_tem_sugestao", type_=Boolean), bindparam("_nova_sugestao_id")),
                else_=FilaResolucaoEAN.sugestao_base_generico_id,
            ),
            sugestao_score=case(
                (bindparam("_tem_sugestao", type_=Boolean), bindparam("_nova_sugestao_score")),
                else_=FilaResolucaoEAN.sugestao_score,
            ),
        )
        # Mesmo motivo de integrations/gps.py::_flush: sem isso o SQLAlchemy
        # 2.0 tenta sincronizar objetos ORM pra este UPDATE em massa com
        # WHERE que não é por PK, e recusa com InvalidRequestError.
        # `core_only` é seguro aqui pelo mesmo argumento — FilaResolucaoEAN
        # nunca é carregado como objeto ORM dentro deste laço.
        .execution_options(synchronize_session=False, dml_strategy="core_only")
    )
    valores_atualizacao = [
        {
            "_ean": item["ean"],
            "_delta_valor": float(item.get("valor") or 0),
            "_delta_ocorrencias": item.get("ocorrencias", 1),
            "_novo_estoque": bool(item.get("aparece_em_estoque", False)),
            "_tem_descricao": bool(item.get("descricao_observada")),
            "_nova_descricao": item.get("descricao_observada") or "",
            "_tem_sugestao": item.get("sugestao_base_generico_id") is not None,
            "_nova_sugestao_id": item.get("sugestao_base_generico_id"),
            "_nova_sugestao_score": item.get("sugestao_score"),
        }
        for item in bloco
    ]
    session.execute(stmt_atualizacao, valores_atualizacao)


def upsert_fila_resolucao(
    session: Session,
    ean: str,
    descricao_observada: str,
    origem: OrigemFila,
    valor: float = 0,
    aparece_em_estoque: bool = False,
    sugestao_base_generico_id: int | None = None,
    sugestao_score: float | None = None,
    cache_fila_pendente: dict[str, FilaResolucaoEAN] | None = None,
    ocorrencias: int = 1,
) -> FilaResolucaoEAN:
    """Idempotente por EAN (chave de dedup — ver nota em core/models.py):
    uma ocorrência nova do mesmo EAN não cria linha nova, só soma valor e
    incrementa ocorrências na linha existente. Item IGNORADA não é reaberto
    automaticamente — dispensar é uma decisão deliberada do admin.

    `ocorrencias` (default 1) é POR QUANTAS vezes esta chamada conta —
    existe pra quem já agregou várias linhas em memória ANTES de chamar
    (ver `integrations/gps.py::_processar_linhas`, que soma todas as
    ocorrências de um EAN pendente num arquivo inteiro e chama esta função
    UMA VEZ por EAN distinto, não uma vez por linha). Uma chamada com
    `valor=300, ocorrencias=3` deixa a fila EXATAMENTE como três chamadas
    de `valor=100, ocorrencias=1` (o default, que preserva o comportamento
    de sempre — uma chamada por ocorrência real) deixariam: mesma soma de
    valor, mesma contagem final.

    A gravação em si (fora do atalho de `cache_fila_pendente` logo abaixo)
    é feita por `upsert_fila_resolucao_em_lote` com 1 item — upsert atômico
    (`INSERT ... ON CONFLICT DO NOTHING` + `UPDATE` com soma em SQL, ver
    lá), não mais "SELECT pra ver se existe, decide em Python, grava de
    volta". Esse SELECT-então-decide tinha uma corrida real: duas sessões
    resolvendo o MESMO EAN novo ao mesmo tempo (ex.: upload de GPS e de
    Gruppy rodando em paralelo) podiam ambas ver "não existe" e ambas
    tentar INSERT — a segunda levava IntegrityError da UniqueConstraint em
    `ean` e o processamento inteiro daquele bloco quebrava. Havia uma
    segunda corrida, mais silenciosa, na atualização de uma linha que já
    existia: duas sessões lendo o mesmo `valor_total_acumulado` em Python,
    cada uma somando o próprio delta e escrevendo de volta, uma escrita
    apaga a contribuição da outra (soma final fica menor que as duas
    parcelas juntas, sem erro nenhum pra avisar). O upsert atômico fecha as
    duas por construção: o INSERT nunca colide (o próprio banco ignora), e
    o UPDATE soma em SQL (`coluna = coluna + :delta`), nunca lê um valor em
    Python pra escrever de volta.

    `cache_fila_pendente` (opcional, ean -> FilaResolucaoEAN) evita 1
    SELECT + 1 flush no SQLite por OCORRÊNCIA de um EAN que nunca resolve
    sozinho — sem isso, numa planilha real de 150 mil linhas onde só um
    punhado de EANs distintos fica preso na fila (mas cada um se repete em
    dezenas de milhares de linhas, ex: o mesmo genérico comprado por
    centenas de lojas), essa era a operação mais cara de todo o
    processamento, medida via profiling real — muito à frente do
    fuzzy-match em si. Passe o MESMO dict entre chamadas sucessivas dentro
    de um upload/reprocesso em massa (mesmo padrão de `cache_candidatos`/
    `cache_eans_resolvidos` em `resolver_ean`); o objeto ORM fica só
    mutado em memória a cada ocorrência seguinte, e o flush de todas as
    mutações acontece de uma vez só quando a sessão inteira dá flush/commit
    (a soma de valor/contagem de ocorrências fica idêntica a fazer 1
    flush por linha — só o COMO grava muda, nunca o total acumulado)."""
    if cache_fila_pendente is not None and ean in cache_fila_pendente:
        fila = cache_fila_pendente[ean]
        fila.valor_total_acumulado = float(fila.valor_total_acumulado or 0) + float(valor or 0)
        fila.qtd_ocorrencias = (fila.qtd_ocorrencias or 0) + ocorrencias
        fila.aparece_em_estoque = bool(fila.aparece_em_estoque or aparece_em_estoque)
        if sugestao_base_generico_id is not None:
            fila.sugestao_base_generico_id = sugestao_base_generico_id
            fila.sugestao_score = sugestao_score
        if descricao_observada:
            fila.descricao_observada = descricao_observada
        return fila

    upsert_fila_resolucao_em_lote(session, [{
        "ean": ean,
        "descricao_observada": descricao_observada,
        "origem": origem,
        "valor": valor,
        "aparece_em_estoque": aparece_em_estoque,
        "sugestao_base_generico_id": sugestao_base_generico_id,
        "sugestao_score": sugestao_score,
        "ocorrencias": ocorrencias,
    }])
    # `populate_existing`: se este EAN já foi carregado no identity map desta
    # sessão antes (ex.: `test_upsert_fila_resolucao_acumula_por_ean`, que
    # chama esta função duas vezes seguidas pro mesmo EAN e depois relê o
    # objeto), o SQLAlchemy por padrão devolveria o objeto Python JÁ
    # carregado, sem atualizar seus atributos com a linha que acabamos de
    # gravar via Core (UPDATE bruto não passa pelo ORM, então não atualiza
    # objetos já em memória sozinho) — daria pra parecer que o upsert não
    # gravou nada, quando na verdade gravou certo no banco. Isso força a
    # releitura de verdade.
    fila = session.execute(
        select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == ean).execution_options(populate_existing=True)
    ).scalar_one()

    if cache_fila_pendente is not None:
        cache_fila_pendente[ean] = fila
    return fila


def confirmar_resolucao_manual(session: Session, fila_id: int, base_generico_id: int, resolvido_por: str) -> None:
    """Admin escolheu (ou aceitou a sugestão de) um BaseGenerico existente
    pra este EAN da fila."""
    fila = session.get(FilaResolucaoEAN, fila_id)
    if fila is None:
        raise ValueError("Item da fila não encontrado.")

    existente = session.execute(select(EanGenerico).where(EanGenerico.ean == fila.ean)).scalar_one_or_none()
    if existente is not None:
        existente.base_generico_id = base_generico_id
        existente.origem_resolucao = OrigemResolucao.MANUAL
        existente.score_similaridade = None
        existente.descricao_origem_snapshot = fila.descricao_observada
        existente.resolvido_por = resolvido_por
        existente.resolvido_em = dt.datetime.utcnow()
    else:
        session.add(
            EanGenerico(
                ean=fila.ean,
                base_generico_id=base_generico_id,
                origem_resolucao=OrigemResolucao.MANUAL,
                score_similaridade=None,
                descricao_origem_snapshot=fila.descricao_observada,
                resolvido_por=resolvido_por,
            )
        )

    fila.status = StatusFila.RESOLVIDA
    session.flush()


def registrar_novo_generico(session: Session, fila_id: int, nome_canonico: str, criado_por: str) -> BaseGenerico:
    """Admin decidiu que este EAN é um genérico novo, ainda não catalogado —
    cria o BaseGenerico e já resolve o item da fila com ele."""
    nome_canonico = nome_canonico.strip()
    if not nome_canonico:
        raise ValueError("Nome do genérico não pode ser vazio.")

    novo = session.execute(
        select(BaseGenerico).where(BaseGenerico.nome_canonico == nome_canonico)
    ).scalar_one_or_none()
    if novo is None:
        novo = BaseGenerico(nome_canonico=nome_canonico, criado_por=criado_por)
        session.add(novo)
        session.flush()

    confirmar_resolucao_manual(session, fila_id, novo.id, criado_por)
    return novo


def ignorar_fila(session: Session, fila_id: int) -> None:
    """Dispensa um item da fila (ex: descrição de linha de lixo, EAN
    inválido) sem resolvê-lo — fica fora das próximas listagens priorizadas,
    mas nunca é excluído do banco."""
    fila = session.get(FilaResolucaoEAN, fila_id)
    if fila is None:
        raise ValueError("Item da fila não encontrado.")
    fila.status = StatusFila.IGNORADA
    session.flush()


def listar_fila_priorizada(
    session: Session,
    origem_filtro: OrigemFila | None = None,
    apenas_pendentes: bool = True,
    limite: int = 200,
    por_lojas: bool = False,
) -> list[FilaResolucaoEAN]:
    """Prioriza por valor acumulado (Pareto): o EAN com mais dinheiro de
    compra em jogo primeiro. O critério "aparece em estoque" saiu junto com o
    estoque da análise (09/2026).

    `por_lojas` (01/10/2026, itens Loja (API)): pareto pelo nº de lojas que
    vendem o EAN (`qtd_ocorrencias`) — cadastrar os do topo resolve a maior
    parte das aparições; desempate pelo valor vendido."""
    stmt = select(FilaResolucaoEAN)
    if apenas_pendentes:
        stmt = stmt.where(FilaResolucaoEAN.status == StatusFila.PENDENTE)
    if origem_filtro is not None:
        stmt = stmt.where(FilaResolucaoEAN.origem == origem_filtro)
    ordem = ([FilaResolucaoEAN.qtd_ocorrencias.desc()] if por_lojas else []) + [
        FilaResolucaoEAN.valor_total_acumulado.desc(), FilaResolucaoEAN.id]
    stmt = stmt.order_by(*ordem).limit(limite)
    return list(session.execute(stmt).scalars().all())


def soma_ocorrencias_pendentes(session: Session, origem: OrigemFila) -> int:
    """Total de aparições (lojas, na Loja (API)) dos itens pendentes — a base
    do % acumulado do pareto, inclusive dos que passam do limite da tela."""
    return int(session.scalar(select(func.coalesce(func.sum(FilaResolucaoEAN.qtd_ocorrencias), 0)).where(
        FilaResolucaoEAN.status == StatusFila.PENDENTE, FilaResolucaoEAN.origem == origem)) or 0)
