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
from sqlalchemy import select, update
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
from reconciliation.normalizador import normalizar_ean, normalizar_texto

Decisao = str  # "auto" | "fila_sugestao" | "fila_manual"


def carregar_cache_eans_resolvidos(session: Session) -> dict[str, int]:
    """Pré-carrega TODOS os EANs já resolvidos (ean -> base_generico_id) num
    único SELECT — passe o dict resultante como `cache_eans_resolvidos` pra
    `resolver_ean` em processamento de planilha em massa (GPS/Gruppy), pra
    evitar uma SELECT em EanGenerico por linha (ver `resolver_ean`)."""
    linhas = session.execute(select(EanGenerico.ean, EanGenerico.base_generico_id)).all()
    return {ean: base_generico_id for ean, base_generico_id in linhas}


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
) -> int | None:
    """Retorna o base_generico_id se o EAN já está (ou acabou de ficar)
    resolvido; None se caiu na fila (ainda pendente).

    `cache_candidatos` só repassa pra `buscar_candidatos` (ver lá) — passe
    o MESMO dict entre chamadas sucessivas dentro de um upload/reprocesso
    em massa pra evitar rebuscar o catálogo de genéricos a cada linha.

    `cache_eans_resolvidos` (opcional, ean -> base_generico_id) evita uma
    SELECT em EanGenerico por linha pra EAN já resolvido — medido via
    profiling real: numa planilha de ~150 mil linhas, a maioria repete EAN
    já resolvido (mesmo produto comprado por várias lojas/meses), e isso
    sozinho era ~2/3 do tempo restante depois de cachear buscar_candidatos.
    Só ajuda quem já está RESOLVIDO — EAN pendente (fila) sempre reconsulta
    de propósito, porque cada ocorrência pode trazer uma descrição
    ligeiramente diferente e a fila precisa acumular valor/ocorrências a
    cada uma mesmo assim (ver upsert_fila_resolucao)."""
    ean = (ean or "").strip()
    if not ean:
        return None

    if cache_eans_resolvidos is not None and ean in cache_eans_resolvidos:
        return cache_eans_resolvidos[ean]

    existente = session.execute(select(EanGenerico).where(EanGenerico.ean == ean)).scalar_one_or_none()
    if existente is not None:
        if cache_eans_resolvidos is not None:
            cache_eans_resolvidos[ean] = existente.base_generico_id
        return existente.base_generico_id

    descricao_norm = normalizar_texto(descricao_origem)
    melhor = buscar_candidatos(session, descricao_norm, cache=cache_candidatos)
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
    upsert_fila_resolucao(
        session,
        ean=ean,
        descricao_observada=descricao_origem,
        origem=origem,
        valor=valor,
        aparece_em_estoque=aparece_em_estoque,
        sugestao_base_generico_id=sugestao_id,
        sugestao_score=sugestao_score,
    )
    return None


def upsert_fila_resolucao(
    session: Session,
    ean: str,
    descricao_observada: str,
    origem: OrigemFila,
    valor: float = 0,
    aparece_em_estoque: bool = False,
    sugestao_base_generico_id: int | None = None,
    sugestao_score: float | None = None,
) -> FilaResolucaoEAN:
    """Idempotente por EAN (chave de dedup — ver nota em core/models.py):
    uma ocorrência nova do mesmo EAN não cria linha nova, só soma valor e
    incrementa ocorrências na linha existente. Item IGNORADA não é reaberto
    automaticamente — dispensar é uma decisão deliberada do admin."""
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == ean)).scalar_one_or_none()
    if fila is None:
        fila = FilaResolucaoEAN(
            ean=ean,
            descricao_observada=descricao_observada,
            origem=origem,
            sugestao_base_generico_id=sugestao_base_generico_id,
            sugestao_score=sugestao_score,
            valor_total_acumulado=valor or 0,
            aparece_em_estoque=bool(aparece_em_estoque),
            qtd_ocorrencias=1,
            status=StatusFila.PENDENTE,
        )
        session.add(fila)
    else:
        fila.valor_total_acumulado = float(fila.valor_total_acumulado or 0) + float(valor or 0)
        fila.qtd_ocorrencias = (fila.qtd_ocorrencias or 0) + 1
        fila.aparece_em_estoque = bool(fila.aparece_em_estoque or aparece_em_estoque)
        if sugestao_base_generico_id is not None:
            fila.sugestao_base_generico_id = sugestao_base_generico_id
            fila.sugestao_score = sugestao_score
        if descricao_observada:
            fila.descricao_observada = descricao_observada
    session.flush()
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
    `sem_mudanca`, não como uma "nova" sugestão."""
    limpeza = limpar_eans_sujos(session)

    itens = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.status == StatusFila.PENDENTE)).scalars().all()

    cache_candidatos: dict = {}
    resolvidos = 0
    sugestao_atualizada = 0
    sem_mudanca = 0

    for item in itens:
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
    """Prioriza primeiro quem aparece em estoque (risco de contradição:
    dizer pra loja que ela não tem o produto quando na verdade tem, só não
    foi reconciliado ainda) e, dentro disso, por valor acumulado (Pareto)."""
    stmt = select(FilaResolucaoEAN)
    if apenas_pendentes:
        stmt = stmt.where(FilaResolucaoEAN.status == StatusFila.PENDENTE)
    if origem_filtro is not None:
        stmt = stmt.where(FilaResolucaoEAN.origem == origem_filtro)
    stmt = stmt.order_by(
        FilaResolucaoEAN.aparece_em_estoque.desc(),
        FilaResolucaoEAN.valor_total_acumulado.desc(),
    ).limit(limite)
    return list(session.execute(stmt).scalars().all())
