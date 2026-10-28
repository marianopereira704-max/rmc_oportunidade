"""Fila de EAN pelas compras da API (05/10/2026), no lugar do envio da
planilha do GPS: genérico COMPRADO pelas lojas e fora da Base Genéricos.
Sem isso, a compra de um genérico de outro laboratório (EAN que ainda não
está na base) nunca é comparada com a Gruppy na Análise (Q33 de 05/10/2026).

- Só entra quem a base de categorias diz ser GENÉRICO (mesmo critério da
  "Loja (API)" — integrations/fila_loja.py): o resto compara pelo próprio
  EAN, não precisa de ligação.
- Mesma origem e mesmas regras da fila do GPS de antes (Q11 de 05/10/2026):
  origem GPS, nota ≥ 92 resolve sozinho, 75–92 entra com sugestão.
- Valor do item = dinheiro comprado com aquele EAN nos últimos 3 meses
  fechados (o período da Análise), regravado a cada execução — nunca
  somado. Vale também pros itens de origem Gruppy (que entram com 0): passam
  a ter valor quando aparecem nas compras. "Loja (API)" tem valor próprio
  (o vendido) e não é tocado aqui.

Lê o resumo por EAN que a rotina grava junto com as compras da rede
(pedido/rede.py), não as compras inteiras. Roda uma vez por dia na
primeira abertura da aba Dados e pelo botão na fila.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from core.models import EanGenerico, FilaResolucaoEAN, OrigemFila, StatusFila
from pedido import categorias as cat
from reconciliation import motor

MESES_DO_VALOR = 3


@dataclass
class ResultadoFilaCompras:
    genericos_comprados: int     # EANs genéricos comprados no período
    ja_na_base: int              # desses, os que já estão na Base Genéricos
    novos_na_fila: int
    resolvidos_automaticamente: int
    valores_regravados: int      # itens GPS/Gruppy pendentes com o valor regravado


def resumo_dos_meses(armaz, chaves, meses: list[str]) -> pd.DataFrame:
    """ean, nome, valor, compras, lojas — somados nos `meses`."""
    partes = []
    for m in meses:
        k = chaves.compras_rede_resumo(m)
        if armaz.existe(k):
            partes.append(armaz.ler_df(k))
    partes = [p for p in partes if not p.empty]
    if not partes:
        return pd.DataFrame(columns=["ean", "nome", "valor", "compras", "lojas"])
    todos = pd.concat(partes, ignore_index=True)
    return todos.groupby("ean", as_index=False).agg(nome=("nome", "first"), valor=("valor", "sum"),
                                                    compras=("compras", "sum"), lojas=("lojas", "max"))


def atualizar(session: Session, resumo: pd.DataFrame, base_categorias: pd.DataFrame) -> ResultadoFilaCompras:
    """Uma leitura dos EANs resolvidos e da fila; gravação em lote."""
    categoria = resumo["ean"].map(base_categorias.drop_duplicates("ean").set_index("ean")["categoria"].astype("object"))
    genericos = resumo[categoria == cat.GENERICO]
    resolvidos = {cat.chave_ean(e) for e in session.scalars(select(EanGenerico.ean))}
    fora = genericos[~genericos["ean"].isin(resolvidos)]
    na_fila = {cat.chave_ean(e) for e in session.scalars(select(FilaResolucaoEAN.ean))}
    novos = fora[~fora["ean"].isin(na_fila)]

    buffer_fila: list[dict] = []
    cache_candidatos: dict = {}
    cache_resolvidos: dict[str, int] = {}
    automaticos = 0
    with session.no_autoflush:
        for r in novos.itertuples(index=False):
            descricao = (r.nome or "").strip()[:250] if isinstance(r.nome, str) else ""
            base_id = motor.resolver_ean(
                session, ean=r.ean, descricao_origem=descricao or f"EAN {r.ean}", origem=OrigemFila.GPS,
                valor=0, criado_por="sistema", cache_candidatos=cache_candidatos,
                cache_eans_resolvidos=cache_resolvidos, cache_eans_completo=True, ocorrencias=0,
                buffer_fila=buffer_fila,
            )
            if base_id is not None:
                automaticos += 1
    motor.upsert_fila_resolucao_em_lote(session, buffer_fila)
    session.flush()
    regravados = _regravar_valores(session, resumo)
    return ResultadoFilaCompras(len(genericos), len(genericos) - len(fora), len(buffer_fila), automaticos, regravados)


def _regravar_valores(session: Session, resumo: pd.DataFrame) -> int:
    """Valor e ocorrências dos itens pendentes de origem GPS/Gruppy = o
    comprado no período (zero pra quem não foi comprado). A fila guarda o
    EAN como veio; o resumo, pela chave sem zeros à esquerda."""
    pendentes = session.execute(
        select(FilaResolucaoEAN.ean).where(FilaResolucaoEAN.status == StatusFila.PENDENTE,
                                           FilaResolucaoEAN.origem.in_([OrigemFila.GPS, OrigemFila.GRUPPY]))
    ).scalars().all()
    if not pendentes:
        return 0
    por_chave = resumo.set_index("ean")
    valores = []
    for ean in pendentes:
        chave = cat.chave_ean(ean)
        achado = chave is not None and chave in por_chave.index
        valores.append({
            "_ean": ean,
            "_valor": round(float(por_chave.at[chave, "valor"]), 2) if achado else 0.0,
            "_qtd": int(por_chave.at[chave, "compras"]) if achado else 0,
        })
    session.execute(
        update(FilaResolucaoEAN)
        .where(FilaResolucaoEAN.ean == bindparam("_ean"))
        .values(valor_total_acumulado=bindparam("_valor"), qtd_ocorrencias=bindparam("_qtd"), aparece_em_estoque=False)
        .execution_options(synchronize_session=False, dml_strategy="core_only"),
        valores,
    )
    return len(valores)
