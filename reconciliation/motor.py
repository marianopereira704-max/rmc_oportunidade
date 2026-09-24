"""Motor de reconciliação EAN -> Base Genéricos.

Tanto a Gruppy (`ItemTabelaGruppy.ean`) quanto o GPS (`RegistroCompraGPS.ean`)
passam por aqui. A ideia central: `EanGenerico` é a ÚNICA fonte de junção
EAN->genérico (ver core/models.py) — então resolver um EAN aqui, uma vez,
conserta retroativamente qualquer linha já importada com aquele EAN, sem
precisar de backfill.

Fluxo de `resolver_ean`:
1. EAN já resolvido (está em EanGenerico)? Atalho — retorna direto, sem
   refazer o fuzzy-match (a maioria das linhas de um upload recorrente cai
   aqui, já que o mesmo produto se repete mês a mês).
2. Senão, normaliza a descrição de origem e busca candidatos via rapidfuzz
   contra `BaseGenerico.ativo`.
3. Classifica o melhor score em 3 níveis (ver `classificar`):
   - >= limiar_auto_aceite: aceite automático, cria EanGenerico já resolvido
     (sempre rastreável como automático — origem_resolucao=AUTOMATICA, com
     snapshot da descrição e do score, pra auditoria).
   - entre limiar_fila_media e limiar_auto_aceite: vai pra fila COM sugestão
     pré-preenchida (o admin só confirma ou troca).
   - abaixo de limiar_fila_media: vai pra fila SEM sugestão (busca manual).
   Nos dois casos de fila, `resolver_ean` retorna None (EAN ainda não
   resolvido) — quem chamou trata a linha como pendente.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import Boolean, bindparam, case, func, or_, select, update
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    BaseGenerico,
    EanGenerico,
    FilaResolucaoEAN,
    ItemTabelaGruppy,
    OrigemFila,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusFila,
)
from core.sql import insert_ignorando_conflito
from reconciliation.normalizador import normalizar_ean, normalizar_texto

Decisao = str  # "auto" | "fila_sugestao" | "fila_manual"


def carregar_cache_eans_resolvidos(session: Session) -> dict[str, int]:
    """Pré-carrega TODOS os EANs já resolvidos (ean -> base_generico_id) num
    único SELECT — passe o dict resultante como `cache_eans_resolvidos` pra
    `resolver_ean` em processamento de planilha em massa (GPS/Gruppy), pra
    evitar uma SELECT em EanGenerico por linha (ver `resolver_ean`)."""
    linhas = session.execute(select(EanGenerico.ean, EanGenerico.base_generico_id)).all()
    return {ean: base_generico_id for ean, base_generico_id in linhas}


_TAMANHO_LOTE_EANS = 1000


def completar_cache_eans_resolvidos(
    session: Session, eans: set[str], cache: dict[str, int],
) -> dict[str, int]:
    """Mesma ideia de `carregar_cache_eans_resolvidos`, só que restrita aos
    EANs que a planilha em processamento realmente usa — e sem rebuscar o que
    o `cache` já sabe.

    Por que não carregar a tabela inteira: `ean_genericos` cresce com o
    catálogo (todo EAN já resolvido, de todos os fornecedores, de sempre),
    enquanto um arquivo GPS usa alguns milhares de EANs distintos. Trazer só
    o que vai ser consultado troca uma consulta que devolve a tabela toda por
    algumas que devolvem exatamente o necessário.

    Por que em lotes de `_TAMANHO_LOTE_EANS`: um `IN (...)` recebe um
    parâmetro por EAN, e todo banco tem teto pra isso (o do SQLite é o mais
    baixo). Mil por vez fica folgado em qualquer backend e ainda deixa o
    total em poucas dezenas de consultas para um arquivo grande — não uma por
    linha, que era o problema original.

    Escrever no `cache` que veio por parâmetro (em vez de devolver um dict
    novo) é o que permite reaproveitá-lo entre arquivos: em
    `_reprocessar_cnpjs`, o segundo arquivo só consulta os EANs que o
    primeiro ainda não tinha trazido."""
    faltando = sorted(ean for ean in eans if ean and ean not in cache)
    for inicio in range(0, len(faltando), _TAMANHO_LOTE_EANS):
        lote = faltando[inicio:inicio + _TAMANHO_LOTE_EANS]
        linhas = session.execute(
            select(EanGenerico.ean, EanGenerico.base_generico_id).where(EanGenerico.ean.in_(lote))
        ).all()
        for ean, base_generico_id in linhas:
            cache[ean] = base_generico_id
    return cache


def buscar_candidatos(
    session: Session, descricao_normalizada: str,
    cache: dict | None = None,
) -> tuple[BaseGenerico, float] | None:
    """Rapidfuzz contra o nome canônico (normalizado na hora) de todo
    BaseGenerico ativo — devolve só o MELHOR candidato (ou None, se não
    houver nenhum genérico ativo).

    Usa `process.extractOne` em vez de `process.extract(..., limit=5)`:
    medido via profiling real (planilha GPS de ~150 mil linhas), essa
    função sozinha é o maior gargalo do processamento em massa hoje — e em
    todo o projeto só o melhor candidato é lido (`resolver_ean` só olha
    `candidatos[0]`, nunca os outros 4). `extractOne` é a função do
    rapidfuzz feita pra esse caso e evita montar/ordenar uma lista de 5
    quando só 1 é necessário. Comportamento idêntico a `extract(...)[0]`,
    inclusive em empate de score (mesmo critério de desempate, verificado
    manualmente) — só a forma de retorno muda (tupla única, não lista).

    `cache` (opcional) evita rebuscar e renormalizar TODOS os genéricos
    ativos a cada chamada — medido via profiling real: sem cache, essa
    rebusca+renormalização repetida era o gargalo dominante do
    processamento em massa antes desta função existir. Passe um dict vazio
    `{}` e reaproveite o MESMO dict entre chamadas sucessivas de
    `resolver_ean` dentro do MESMO upload/reprocessamento — o catálogo de
    genéricos ativos não muda durante esse processamento (só EanGenerico é
    criado ao longo do caminho, nunca BaseGenerico), então cachear por toda
    a duração de um upload é seguro. `None` (default) preserva o
    comportamento antigo — sempre busca de novo —, usado por chamadas
    avulsas fora de um loop em massa."""
    if not descricao_normalizada:
        return None

    if cache is not None and "por_id" in cache:
        por_id, escolhas = cache["por_id"], cache["escolhas"]
    else:
        genericos = session.execute(select(BaseGenerico).where(BaseGenerico.ativo.is_(True))).scalars().all()
        por_id = {g.id: g for g in genericos}
        escolhas = {g.id: normalizar_texto(g.nome_canonico) for g in genericos}
        if cache is not None:
            cache["por_id"], cache["escolhas"] = por_id, escolhas

    if not escolhas:
        return None

    resultado = process.extractOne(descricao_normalizada, escolhas, scorer=fuzz.WRatio)
    if resultado is None:
        return None
    _valor, score, chave = resultado
    return (por_id[chave], float(score))


def classificar(score: float, limiar_auto: float, limiar_medio: float) -> Decisao:
    """Função pura — testável isolada do banco. 3 níveis de confiança."""
    if score >= limiar_auto:
        return "auto"
    if score >= limiar_medio:
        return "fila_sugestao"
    return "fila_manual"


def resolver_ean(
    session: Session,
    ean: str,
    descricao_origem: str,
    origem: OrigemFila,
    valor: float = 0,
    aparece_em_estoque: bool = False,
    criado_por: str = "sistema",
    cache_candidatos: dict | None = None,
    cache_eans_resolvidos: dict[str, int] | None = None,
    cache_fila_pendente: dict[str, FilaResolucaoEAN] | None = None,
    cache_descricao_pendente: dict[str, str] | None = None,
    ocorrencias: int = 1,
    buffer_fila: list[dict] | None = None,
    cache_eans_completo: bool = False,
) -> int | None:
    """Retorna o base_generico_id se o EAN já está (ou acabou de ficar)
    resolvido; None se caiu na fila (ainda pendente).

    `ocorrencias` (default 1, repassado direto pra `upsert_fila_resolucao`
    — ver lá) deixa quem já agregou várias linhas em memória fora desta
    função chamar UMA VEZ por EAN pendente, contando quantas ocorrências
    aquela chamada representa, em vez de chamar `resolver_ean` uma vez por
    linha só pra manter a contagem certa.

    `cache_candidatos` só repassa pra `buscar_candidatos` (ver lá) — passe
    o MESMO dict entre chamadas sucessivas dentro de um upload/reprocesso
    em massa pra evitar rebuscar o catálogo de genéricos a cada linha.

    `cache_eans_resolvidos` (opcional, ean -> base_generico_id) evita uma
    SELECT em EanGenerico por linha pra EAN já resolvido — medido via
    profiling real: numa planilha de ~150 mil linhas, a maioria repete EAN
    já resolvido (mesmo produto comprado por várias lojas/meses), e isso
    sozinho era ~2/3 do tempo restante depois de cachear buscar_candidatos.
    Só ajuda quem já está RESOLVIDO.

    `cache_fila_pendente` (opcional, ean -> FilaResolucaoEAN) é o
    equivalente pra EAN AINDA PENDENTE — repassado direto pra
    `upsert_fila_resolucao` (ver lá): evita 1 SELECT + 1 flush no SQLite
    por OCORRÊNCIA de um EAN que nunca resolve sozinho.

    `cache_descricao_pendente` (opcional, ean -> descrição normalizada da
    última chamada) é o que evita repetir o fuzzy-match em si (o `PONTO
    mais caro de todos, medido via profiling real numa planilha de 150 mil
    linhas: um EAN "preso" na fila pode se repetir em dezenas de milhares
    de linhas — ex: o mesmo genérico comprado por centenas de lojas — e sem
    isso `buscar_candidatos` reavaliava as ~2 mil opções ativas EM TODA
    ocorrência). Só pula o fuzzy-match quando a descrição normalizada desta
    ocorrência é IDÊNTICA à da última vez que resolvemos este EAN nesta
    mesma sessão — `buscar_candidatos` é função pura de
    (descrição normalizada, catálogo ativo), e o catálogo não muda durante
    um upload (ver docstring de `buscar_candidatos`), então mesma entrada
    SEMPRE dá a mesma saída: reaproveitar não muda nenhum resultado, só
    evita recalcular o que já calculamos. Descrição DIFERENTE (loja
    reportou o produto com texto ligeiramente distinto) ainda recalcula
    normalmente — preserva o comportamento de sempre reavaliar quando algo
    pode ter mudado.

    `buffer_fila` (opcional, lista de dicts): quando informado, os dois
    pontos que decidiriam gravar na fila (abaixo) NÃO chamam
    `upsert_fila_resolucao` (uma gravação por EAN) — em vez disso, só
    ACUMULAM um dict descrevendo a gravação nesta lista, e quem chamou
    `resolver_ean` é responsável por passar a lista pra
    `upsert_fila_resolucao_em_lote` (ver lá) depois do laço, uma única vez
    pra todos os EANs pendentes de uma passada. É o que permite o upsert
    atômico rodar em BLOCOS (poucas instruções SQL pra milhares de EANs
    distintos) em vez de uma instrução por EAN — mesmo ganho de fazer o
    INSERT dos EANs distintos novos em blocos de 5000 em vez de um a um.
    Incompatível com `cache_fila_pendente`/`cache_descricao_pendente` por
    construção: quem usa `buffer_fila` (a fase de reconciliação em massa do
    GPS, ver `integrations/gps.py`) já chama `resolver_ean` uma única vez
    por EAN distinto — não há ocorrência repetida dentro desta função pra
    aquelas caches evitarem."""
    ean = (ean or "").strip()
    if not ean:
        return None

    if cache_eans_resolvidos is not None and ean in cache_eans_resolvidos:
        return cache_eans_resolvidos[ean]

    # `cache_eans_completo`: quem chamou já consultou em lote TODOS os EANs
    # que vai passar aqui (`completar_cache_eans_resolvidos`), então "não está
    # no cache" já significa "não está resolvido" — a consulta abaixo seria
    # uma ida ao banco por EAN pendente só pra confirmar o que já se sabe
    # (milhares de idas, cada uma atravessando a rede até o Postgres).
    existente = None
    if not (cache_eans_completo and cache_eans_resolvidos is not None):
        existente = session.execute(select(EanGenerico).where(EanGenerico.ean == ean)).scalar_one_or_none()
    if existente is not None:
        if cache_eans_resolvidos is not None:
            cache_eans_resolvidos[ean] = existente.base_generico_id
        return existente.base_generico_id

    descricao_norm = normalizar_texto(descricao_origem)

    fila_cacheada = cache_fila_pendente.get(ean) if cache_fila_pendente is not None else None
    descricao_norm_anterior = cache_descricao_pendente.get(ean) if cache_descricao_pendente is not None else None

    if fila_cacheada is not None and descricao_norm_anterior == descricao_norm:
        # Mesmo EAN, mesma descrição normalizada já vista nesta sessão: o
        # score seria idêntico ao já calculado (função pura — ver acima).
        # Já sabemos que NÃO foi "auto" da vez anterior (senão a próxima
        # ocorrência teria batido em cache_eans_resolvidos, nem chegado
        # aqui) — então a decisão continua sendo fila, sem reclassificar.
        sugestao_id = fila_cacheada.sugestao_base_generico_id
        sugestao_score_raw = fila_cacheada.sugestao_score
        sugestao_score = float(sugestao_score_raw) if sugestao_score_raw is not None else None
        if buffer_fila is not None:
            buffer_fila.append({
                "ean": ean, "descricao_observada": descricao_origem, "origem": origem,
                "valor": valor, "aparece_em_estoque": aparece_em_estoque,
                "sugestao_base_generico_id": sugestao_id, "sugestao_score": sugestao_score,
                "ocorrencias": ocorrencias,
            })
        else:
            upsert_fila_resolucao(
                session, ean=ean, descricao_observada=descricao_origem, origem=origem,
                valor=valor, aparece_em_estoque=aparece_em_estoque,
                sugestao_base_generico_id=sugestao_id, sugestao_score=sugestao_score,
                cache_fila_pendente=cache_fila_pendente, ocorrencias=ocorrencias,
            )
        return None

    melhor = buscar_candidatos(session, descricao_norm, cache=cache_candidatos)
    if cache_descricao_pendente is not None:
        cache_descricao_pendente[ean] = descricao_norm
    score = melhor[1] if melhor else 0.0

    decisao = classificar(score, settings.reconciliacao.limiar_auto_aceite, settings.reconciliacao.limiar_fila_media)

    if decisao == "auto" and melhor is not None:
        base_generico, score_final = melhor
        session.add(
            EanGenerico(
                ean=ean,
                base_generico_id=base_generico.id,
                origem_resolucao=OrigemResolucao.AUTOMATICA,
                score_similaridade=score_final,
                descricao_origem_snapshot=descricao_origem,
                resolvido_por=criado_por or "sistema",
            )
        )
        session.flush()
        if cache_eans_resolvidos is not None:
            cache_eans_resolvidos[ean] = base_generico.id
        return base_generico.id

    sugestao_id = melhor[0].id if melhor else None
    sugestao_score = melhor[1] if melhor else None
    if buffer_fila is not None:
        buffer_fila.append({
            "ean": ean, "descricao_observada": descricao_origem, "origem": origem,
            "valor": valor, "aparece_em_estoque": aparece_em_estoque,
            "sugestao_base_generico_id": sugestao_id, "sugestao_score": sugestao_score,
            "ocorrencias": ocorrencias,
        })
    else:
        upsert_fila_resolucao(
            session,
            ean=ean,
            descricao_observada=descricao_origem,
            origem=origem,
            valor=valor,
            aparece_em_estoque=aparece_em_estoque,
            sugestao_base_generico_id=sugestao_id,
            sugestao_score=sugestao_score,
            cache_fila_pendente=cache_fila_pendente,
            ocorrencias=ocorrencias,
        )
    return None


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
    rastreável em separado de AUTOMATICA (fuzzy-match) e MANUAL (fila)."""
    genericos_criados = 0
    genericos_reaproveitados = 0
    eans_vinculados = 0
    eans_pulados = 0
    linhas_invalidas = 0

    eans_ja_resolvidos = {row[0] for row in session.execute(select(EanGenerico.ean)).all()}
    cache_generico_por_nome: dict[str, BaseGenerico] = {}

    # ver justificativa detalhada em integrations/gps.py::_processar_linhas —
    # mesmo raciocínio: toda SELECT abaixo (cache miss de `cache_generico_por_nome`)
    # já é seguida de `session.flush()` explícito quando cria algo novo, então
    # autoflush implícito a cada SELECT (disparando flush do que já foi
    # `session.add`ado no loop) é só custo redundante.
    with session.no_autoflush:
        for ean, descricao in linhas:
            ean = (ean or "").strip()
            descricao = (descricao or "").strip()
            if not ean or not descricao:
                linhas_invalidas += 1
                continue

            if ean in eans_ja_resolvidos:
                eans_pulados += 1
                continue

            generico = cache_generico_por_nome.get(descricao)
            if generico is None:
                generico = session.execute(
                    select(BaseGenerico).where(BaseGenerico.nome_canonico == descricao)
                ).scalar_one_or_none()
                if generico is None:
                    generico = BaseGenerico(nome_canonico=descricao, criado_por=criado_por)
                    session.add(generico)
                    session.flush()
                    genericos_criados += 1
                else:
                    genericos_reaproveitados += 1
                cache_generico_por_nome[descricao] = generico

            session.add(
                EanGenerico(
                    ean=ean,
                    base_generico_id=generico.id,
                    origem_resolucao=OrigemResolucao.IMPORTADA,
                    score_similaridade=None,
                    descricao_origem_snapshot=descricao,
                    resolvido_por=criado_por,
                )
            )
            eans_ja_resolvidos.add(ean)  # mesma planilha repetindo o EAN não deve duplicar
            eans_vinculados += 1

    session.flush()

    return ResultadoImportacaoBase(
        genericos_criados=genericos_criados,
        genericos_reaproveitados=genericos_reaproveitados,
        eans_vinculados=eans_vinculados,
        eans_pulados=eans_pulados,
        linhas_invalidas=linhas_invalidas,
    )


@dataclass
class ResultadoLimpezaEan:
    registros_compra_gps_corrigidos: int
    registros_compra_gps_colisoes_puladas: int
    itens_tabela_gruppy_corrigidos: int
    fila_resolucao_ean_corrigidos: int
    fila_resolucao_ean_colisoes_mescladas: int


def limpar_eans_sujos(session: Session) -> ResultadoLimpezaEan:
    """Corrige em lote, via UPDATE direto (nunca recriando a linha), EAN
    gravado sujo (ex: sufixo ".0" de planilha lida como float, de antes da
    correção de `normalizar_ean` estar em uso naquele upload específico) em
    `RegistroCompraGPS.ean`, `ItemTabelaGruppy.ean` e `FilaResolucaoEAN.ean`.

    Pré-requisito pro reprocessamento da fila (ver `reprocessar_fila_resolucao`):
    um `EanGenerico` criado pra um EAN sujo nunca bateria contra um upload
    futuro, que já chega normalizado — resolver o fuzzy-match sem limpar a
    chave deixaria o resultado órfão de novo no próximo upload.

    `RegistroCompraGPS` tem `UniqueConstraint(loja_id, ean, ano_mes)` — se a
    versão limpa colidir com uma linha JÁ existente pra essa loja+mês
    (dado financeiro real, não só chave de dedup), a linha suja é PULADA
    (nunca mescla/sobrescreve compra às cegas) e contada em
    `registros_compra_gps_colisoes_puladas`, pra revisão manual.

    `FilaResolucaoEAN` tem EAN único (chave de dedup, sem valor financeiro
    "de verdade" fora do acumulado) — se colidir, mescla a linha suja na já
    limpa (mesmo critério de soma de `upsert_fila_resolucao`) e remove a
    duplicata, contada em `fila_resolucao_ean_colisoes_mescladas`.

    `ItemTabelaGruppy` não tem constraint única em EAN (mesmo produto pode
    legitimamente repetir entre tabelas/vigências) — UPDATE direto, sem
    checagem de colisão.

    Os UPDATEs de `ItemTabelaGruppy`/`RegistroCompraGPS` são Core (via
    `update(...)`), não ORM `session.add` — não passam pelo unit-of-work,
    então uma instância ORM dessas linhas já carregada em memória ANTES
    desta chamada (na mesma session) não reflete o novo EAN sozinha; só
    reaparece limpa numa consulta nova. Não é problema no fluxo real (esta
    função sempre roda no início de uma session nova), mas fica registrado
    caso alguém reaproveite `limpar_eans_sujos` no meio de um processamento
    que já tenha essas linhas carregadas."""
    # --- ItemTabelaGruppy: sem constraint única, UPDATE direto ---
    itens_corrigidos = 0
    for item_id, ean_sujo in session.execute(select(ItemTabelaGruppy.id, ItemTabelaGruppy.ean)).all():
        ean_limpo = normalizar_ean(ean_sujo)
        if ean_limpo and ean_limpo != ean_sujo:
            session.execute(update(ItemTabelaGruppy).where(ItemTabelaGruppy.id == item_id).values(ean=ean_limpo))
            itens_corrigidos += 1

    # --- RegistroCompraGPS: UniqueConstraint(loja_id, ean, ano_mes) — pula colisão ---
    linhas_gps = session.execute(
        select(RegistroCompraGPS.id, RegistroCompraGPS.loja_id, RegistroCompraGPS.ean, RegistroCompraGPS.ano_mes)
    ).all()
    chaves_existentes = {(loja_id, ean, ano_mes) for _id, loja_id, ean, ano_mes in linhas_gps}
    gps_corrigidos = 0
    gps_colisoes = 0
    for reg_id, loja_id, ean_sujo, ano_mes in linhas_gps:
        ean_limpo = normalizar_ean(ean_sujo)
        if not ean_limpo or ean_limpo == ean_sujo:
            continue
        chave_origem = (loja_id, ean_sujo, ano_mes)
        chave_destino = (loja_id, ean_limpo, ano_mes)
        if chave_destino in chaves_existentes:
            gps_colisoes += 1
            continue
        session.execute(update(RegistroCompraGPS).where(RegistroCompraGPS.id == reg_id).values(ean=ean_limpo))
        chaves_existentes.discard(chave_origem)
        chaves_existentes.add(chave_destino)
        gps_corrigidos += 1

    # --- FilaResolucaoEAN: EAN único — mescla e remove duplicata em colisão ---
    itens_fila = session.execute(select(FilaResolucaoEAN)).scalars().all()
    por_ean = {f.ean: f for f in itens_fila}
    fila_corrigidos = 0
    fila_mesclados = 0
    for fila in list(itens_fila):
        ean_limpo = normalizar_ean(fila.ean)
        if not ean_limpo or ean_limpo == fila.ean:
            continue
        existente_limpo = por_ean.get(ean_limpo)
        if existente_limpo is not None and existente_limpo is not fila:
            existente_limpo.valor_total_acumulado = (
                float(existente_limpo.valor_total_acumulado or 0) + float(fila.valor_total_acumulado or 0)
            )
            existente_limpo.qtd_ocorrencias = (existente_limpo.qtd_ocorrencias or 0) + (fila.qtd_ocorrencias or 0)
            existente_limpo.aparece_em_estoque = bool(existente_limpo.aparece_em_estoque or fila.aparece_em_estoque)
            if existente_limpo.sugestao_base_generico_id is None and fila.sugestao_base_generico_id is not None:
                existente_limpo.sugestao_base_generico_id = fila.sugestao_base_generico_id
                existente_limpo.sugestao_score = fila.sugestao_score
            session.delete(fila)
            del por_ean[fila.ean]
            fila_mesclados += 1
        else:
            del por_ean[fila.ean]
            fila.ean = ean_limpo
            por_ean[ean_limpo] = fila
            fila_corrigidos += 1

    session.flush()

    return ResultadoLimpezaEan(
        registros_compra_gps_corrigidos=gps_corrigidos,
        registros_compra_gps_colisoes_puladas=gps_colisoes,
        itens_tabela_gruppy_corrigidos=itens_corrigidos,
        fila_resolucao_ean_corrigidos=fila_corrigidos,
        fila_resolucao_ean_colisoes_mescladas=fila_mesclados,
    )


@dataclass
class ResultadoReprocessamentoFila:
    limpeza: ResultadoLimpezaEan
    itens_avaliados: int
    resolvidos_por_ean_existente: int
    resolvidos_automaticamente: int
    sugestao_atualizada: int
    sem_mudanca: int


def reprocessar_fila_resolucao(session: Session) -> ResultadoReprocessamentoFila:
    """Reprocessa todo item PENDENTE de `FilaResolucaoEAN` contra a base de
    genéricos ATUAL — útil quando a Base Genéricos curada foi importada
    DEPOIS de uploads que já tinham caído na fila (o fuzzy-match não tinha o
    que comparar contra na hora). Mesmo espírito do reprocessamento de CNPJ
    órfão (`integrations/gps.py::_reprocessar_cnpjs`), aplicado à fila de EAN.

    Primeiro roda `limpar_eans_sujos` — senão um `EanGenerico` novo criado
    aqui ficaria com chave suja, nunca batendo contra upload futuro (já
    normalizado). Depois, pra cada item pendente, roda `buscar_candidatos`
    de novo, com o MESMO critério de 3 níveis que `resolver_ean` usa num
    upload novo:
    - score >= limiar_auto_aceite: resolve automaticamente (cria
      `EanGenerico` origem AUTOMATICA — `resolvido_por="sistema"`, mesmo
      critério do upload: é o algoritmo decidindo, não o admin que clicou
      pra disparar o reprocesso; marca o item da fila RESOLVIDA).
    - limiar_fila_media <= score < limiar_auto_aceite: NÃO resolve sozinho
      — só atualiza sugestão (candidato + score) pra quem revisar já ver a
      sugestão mais recente em vez de buscar do zero.
    - score < limiar_fila_media: não mexe.

    Idempotente: rodar duas vezes seguidas não piora nada — item já
    RESOLVIDA sai do filtro `status == PENDENTE` e nem entra na segunda
    passada; item que já tem a MESMA sugestão de antes conta como
    `sem_mudanca`, não como uma "nova" sugestão.

    ANTES de qualquer fuzzy-match, checa se o EAN do item já tem uma
    resolução EXATA em `EanGenerico` — mesma checagem que `resolver_ean` já
    faz num upload novo (linha por linha), só que aqui em lote pra fila
    inteira de uma vez. Sem isso, importar a planilha curada da Base
    Genéricos (`importar_base_genericos`, que grava `EanGenerico` direto
    pelo EAN, sem passar pela fila) depois que um item já tinha caído aqui
    NUNCA resolvia esse item sozinho — o reprocessamento ia direto pro
    fuzzy-match por descrição, que pode nem bater (a descrição observada no
    GPS/Gruppy raramente é igual ao nome canônico curado), mesmo com o EAN
    já perfeitamente resolvido. É exatamente o caminho "sobe a Base
    Genéricos, depois só reprocessa a fila" que essa função promete no
    parágrafo acima — sem essa checagem, ele não entregava o que promete."""
    limpeza = limpar_eans_sujos(session)

    itens = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.status == StatusFila.PENDENTE)).scalars().all()

    # Carregado uma vez, em lote (mesmo padrão de `importar_base_genericos`)
    # — não uma SELECT por item — pra saber quais EAN já têm resolução
    # exata antes de gastar fuzzy-match em quem nem precisa dele.
    eans_resolvidos = set(session.execute(select(EanGenerico.ean)).scalars().all())

    cache_candidatos: dict = {}
    resolvidos_por_ean_existente = 0
    resolvidos = 0
    sugestao_atualizada = 0
    sem_mudanca = 0

    for item in itens:
        if item.ean in eans_resolvidos:
            item.status = StatusFila.RESOLVIDA
            resolvidos_por_ean_existente += 1
            continue

        descricao_norm = normalizar_texto(item.descricao_observada)
        melhor = buscar_candidatos(session, descricao_norm, cache=cache_candidatos)
        score = melhor[1] if melhor else 0.0
        decisao = classificar(score, settings.reconciliacao.limiar_auto_aceite, settings.reconciliacao.limiar_fila_media)

        if decisao == "auto" and melhor is not None:
            base_generico, score_final = melhor
            existente = session.execute(select(EanGenerico).where(EanGenerico.ean == item.ean)).scalar_one_or_none()
            if existente is None:
                session.add(
                    EanGenerico(
                        ean=item.ean,
                        base_generico_id=base_generico.id,
                        origem_resolucao=OrigemResolucao.AUTOMATICA,
                        score_similaridade=score_final,
                        descricao_origem_snapshot=item.descricao_observada,
                        resolvido_por="sistema",
                    )
                )
            item.status = StatusFila.RESOLVIDA
            resolvidos += 1
        elif decisao == "fila_sugestao" and melhor is not None:
            base_generico, score_final = melhor
            if item.sugestao_base_generico_id != base_generico.id or item.sugestao_score is None or float(item.sugestao_score) != score_final:
                item.sugestao_base_generico_id = base_generico.id
                item.sugestao_score = score_final
                sugestao_atualizada += 1
            else:
                sem_mudanca += 1
        else:
            sem_mudanca += 1

    session.flush()

    return ResultadoReprocessamentoFila(
        limpeza=limpeza,
        itens_avaliados=len(itens),
        resolvidos_por_ean_existente=resolvidos_por_ean_existente,
        resolvidos_automaticamente=resolvidos,
        sugestao_atualizada=sugestao_atualizada,
        sem_mudanca=sem_mudanca,
    )


def listar_fila_priorizada(
    session: Session,
    origem_filtro: OrigemFila | None = None,
    apenas_pendentes: bool = True,
    limite: int = 200,
) -> list[FilaResolucaoEAN]:
    """Prioriza por valor acumulado (Pareto): o EAN com mais dinheiro de
    compra em jogo primeiro. O critério "aparece em estoque" saiu junto com o
    estoque da análise (09/2026)."""
    stmt = select(FilaResolucaoEAN)
    if apenas_pendentes:
        stmt = stmt.where(FilaResolucaoEAN.status == StatusFila.PENDENTE)
    if origem_filtro is not None:
        stmt = stmt.where(FilaResolucaoEAN.origem == origem_filtro)
    stmt = stmt.order_by(FilaResolucaoEAN.valor_total_acumulado.desc(), FilaResolucaoEAN.id).limit(limite)
    return list(session.execute(stmt).scalars().all())


def recalcular_valores_fila_ean(session: Session) -> int:
    """Recalcula valor e ocorrências de TODO item pendente da fila de EAN a
    partir das compras VIGENTES (`registros_compra_gps` + `compras_gps_orfas`),
    numa instrução só.

    Substitui a soma incremental envio a envio: reenviar o mesmo mês três
    vezes somava três vezes, e a prioridade da fila ficava errada justamente
    pra quem reenvia pra atualizar os dados. Agora o valor é sempre "quanto
    dinheiro de compra, hoje, depende deste EAN", independente de quantos
    envios aconteceram. `aparece_em_estoque` é zerado: o estoque saiu da
    análise. Devolve quantos itens foram recalculados."""
    from core.models import CompraGPSOrfa

    def _soma_valor(tabela):
        return (
            select(func.coalesce(func.sum(tabela.quantidade * tabela.custo_unitario), 0))
            .where(tabela.ean == FilaResolucaoEAN.ean)
            .scalar_subquery()
        )

    def _contagem(tabela):
        return select(func.count()).select_from(tabela).where(tabela.ean == FilaResolucaoEAN.ean).scalar_subquery()

    stmt = (
        update(FilaResolucaoEAN)
        .where(FilaResolucaoEAN.status == StatusFila.PENDENTE)
        .values(
            valor_total_acumulado=_soma_valor(RegistroCompraGPS) + _soma_valor(CompraGPSOrfa),
            qtd_ocorrencias=_contagem(RegistroCompraGPS) + _contagem(CompraGPSOrfa),
            aparece_em_estoque=False,
        )
        .execution_options(synchronize_session=False)
    )
    return session.execute(stmt).rowcount
