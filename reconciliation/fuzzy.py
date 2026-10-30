"""Comparação de nomes (fuzzy-match) EAN -> Base Genéricos — parte do antigo motor.py, dividido em
29/10/2026 (etapa 8 do plano de ação; fila em fila.py, importação da Base em importacao_base.py, limpeza e
reprocessamento em reprocessamento.py; motor.py segue repassando os nomes).

Passam por aqui os EANs genéricos da Gruppy (`ItemTabelaGruppy.ean`), das
compras e das vendas das lojas pela API do GPS (integrations/fila_compras.py,
integrations/fila_loja.py). A ideia central: `EanGenerico` é a ÚNICA fonte de junção
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


from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    BaseGenerico,
    EanGenerico,
    FilaResolucaoEAN,
    OrigemFila,
    OrigemResolucao,
)
from reconciliation.normalizador import normalizar_texto
from reconciliation import fila

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
            fila.upsert_fila_resolucao(
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
        fila.upsert_fila_resolucao(
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
