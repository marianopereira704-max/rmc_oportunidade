"""Limpeza de EAN sujo e reprocessamento da Fila de EAN contra a base atual — parte do antigo motor.py, dividido
em 29/10/2026 (etapa 8 do plano de ação). Chama a comparação de nomes pelo módulo (`fuzzy.buscar_candidatos`): os
testes trocam a função lá."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    EanGenerico,
    FilaResolucaoEAN,
    ItemTabelaGruppy,
    OrigemFila,
    OrigemResolucao,
    StatusFila,
)
from reconciliation.normalizador import normalizar_ean, normalizar_texto
from reconciliation import fuzzy

@dataclass
class ResultadoLimpezaEan:
    itens_tabela_gruppy_corrigidos: int
    fila_resolucao_ean_corrigidos: int
    fila_resolucao_ean_colisoes_mescladas: int


def limpar_eans_sujos(session: Session) -> ResultadoLimpezaEan:
    """Corrige em lote, via UPDATE direto (nunca recriando a linha), EAN
    gravado sujo (ex: sufixo ".0" de planilha lida como float, de antes da
    correção de `normalizar_ean` estar em uso naquele upload específico) em
    `ItemTabelaGruppy.ean` e `FilaResolucaoEAN.ean`. (As compras da planilha
    do GPS saíram em 05/10/2026: as da API já chegam limpas — pedido/transformar.py.)

    Pré-requisito pro reprocessamento da fila (ver `reprocessar_fila_resolucao`):
    um `EanGenerico` criado pra um EAN sujo nunca bateria contra um upload
    futuro, que já chega normalizado — resolver o fuzzy-match sem limpar a
    chave deixaria o resultado órfão de novo no próximo upload.

    `FilaResolucaoEAN` tem EAN único (chave de dedup, sem valor financeiro
    "de verdade" fora do acumulado) — se colidir, mescla a linha suja na já
    limpa (mesmo critério de soma de `upsert_fila_resolucao`) e remove a
    duplicata, contada em `fila_resolucao_ean_colisoes_mescladas`.

    `ItemTabelaGruppy` não tem constraint única em EAN (mesmo produto pode
    legitimamente repetir entre tabelas/vigências) — UPDATE direto, sem
    checagem de colisão.

    Os UPDATEs de `ItemTabelaGruppy` são Core (via
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
        melhor = fuzzy.buscar_candidatos(session, descricao_norm, cache=cache_candidatos)
        score = melhor[1] if melhor else 0.0
        decisao = fuzzy.classificar(score, settings.reconciliacao.limiar_auto_aceite, settings.reconciliacao.limiar_fila_media)
        # "Loja (API)" nunca resolve sozinho, nem com nota alta: vira só
        # sugestão pra alguém confirmar (integrations/fila_loja.py, Q23).
        if decisao == "auto" and item.origem == OrigemFila.LOJA_API:
            decisao = "fila_sugestao"

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
