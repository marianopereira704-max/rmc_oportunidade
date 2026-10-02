"""Fila de EAN com a origem "Loja (API)" (Fase 6 do Pedido, decisão de
27/09/2026, Q23): genérico que alguma loja VENDEU na janela e que ainda
não está na Base Genéricos. Sem isso, ele aparece no Pedido como produto
solto (um EAN), em vez de somar com os outros EANs do mesmo genérico.

Só entra quem a base de categorias diz ser genérico (CMED "Genérico",
FEBRAFAR "PRESCRIÇÃO GENERICO" ou categoria manual) — mandar todo EAN
vendido e fora da base encheria a fila de perfumaria e referência.

Diferente da fila do GPS/Gruppy:
- nunca resolve sozinho, mesmo com nota alta: vai pra fila com a sugestão
  e alguém confirma;
- o valor do item é o VENDIDO nas lojas na janela (não há compra
  associada) e é regravado a cada atualização (não soma), a partir dos
  catálogos que a rotina grava (`pedido/catalogo/`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from core.models import EanGenerico, FilaResolucaoEAN, OrigemFila, StatusFila
from core.sql import insert_ignorando_conflito
from pedido import categorias as cat
from reconciliation import motor
from reconciliation.normalizador import normalizar_texto


@dataclass
class ResultadoFilaLoja:
    genericos_vendidos: int      # EANs genéricos vendidos nas lojas
    ja_na_base: int              # desses, os que já estão na Base Genéricos
    novos_na_fila: int
    atualizados: int             # já estavam na fila como Loja (API): valor regravado
    em_outra_origem: int         # já estavam na fila vindos do GPS/Gruppy (não muda a origem)


def candidatos(catalogo: pd.DataFrame, base_categorias: pd.DataFrame) -> pd.DataFrame:
    """Genéricos vendidos (lojas_com_venda > 0): ean, nome, laboratorio,
    lojas_com_venda, valor_venda."""
    if catalogo.empty or "lojas_com_venda" not in catalogo.columns:
        return catalogo.iloc[0:0]
    categoria = catalogo["ean"].map(base_categorias.drop_duplicates("ean").set_index("ean")["categoria"].astype("object"))
    vendidos = catalogo[(catalogo["lojas_com_venda"].fillna(0) > 0) & (categoria == cat.GENERICO)]
    return vendidos.reset_index(drop=True)


def atualizar(session: Session, catalogo: pd.DataFrame, base_categorias: pd.DataFrame) -> ResultadoFilaLoja:
    """Uma leitura dos EANs resolvidos e da fila; gravação em lote."""
    c = candidatos(catalogo, base_categorias)
    resolvidos = {cat.chave_ean(e) for e in session.scalars(select(EanGenerico.ean))}
    fora = c[~c["ean"].isin(resolvidos)]
    na_fila = {cat.chave_ean(e): o for e, o in session.execute(select(FilaResolucaoEAN.ean, FilaResolucaoEAN.origem))}

    novos = fora[~fora["ean"].isin(na_fila)]
    cache: dict = {}
    registros = []
    for r in novos.itertuples(index=False):
        descricao = (r.nome or "").strip()[:250] or f"EAN {r.ean}"
        melhor = motor.buscar_candidatos(session, normalizar_texto(descricao), cache=cache)
        registros.append({
            "ean": r.ean, "descricao_observada": descricao, "origem": OrigemFila.LOJA_API,
            # Sugestão sempre que houver candidato razoável — mas nunca resolve
            # sozinho (Q23): até nota 100 espera alguém confirmar.
            "sugestao_base_generico_id": melhor[0].id if melhor and melhor[1] >= _limiar_sugestao() else None,
            "sugestao_score": round(melhor[1], 2) if melhor and melhor[1] >= _limiar_sugestao() else None,
            "valor_total_acumulado": round(float(r.valor_venda or 0), 2), "aparece_em_estoque": False,
            "qtd_ocorrencias": int(r.lojas_com_venda or 0), "status": StatusFila.PENDENTE,
            "criado_em": dt.datetime.utcnow(), "atualizado_em": dt.datetime.utcnow(),
        })
    for i in range(0, len(registros), 500):
        session.execute(insert_ignorando_conflito(session, FilaResolucaoEAN.__table__, ["ean"]), registros[i:i + 500])

    # Os que já eram "Loja (API)": valor e nº de lojas regravados (não somados).
    ja_loja = fora[fora["ean"].map(na_fila).eq(OrigemFila.LOJA_API)]
    if not ja_loja.empty:
        session.execute(
            update(FilaResolucaoEAN)
            .where(FilaResolucaoEAN.ean == bindparam("_ean"), FilaResolucaoEAN.origem == OrigemFila.LOJA_API)
            .values(valor_total_acumulado=bindparam("_valor"), qtd_ocorrencias=bindparam("_lojas"))
            .execution_options(synchronize_session=False, dml_strategy="core_only"),
            [{"_ean": r.ean, "_valor": round(float(r.valor_venda or 0), 2), "_lojas": int(r.lojas_com_venda or 0)}
             for r in ja_loja.itertuples(index=False)],
        )
    session.flush()
    em_outra = int(fora["ean"].map(na_fila).isin([OrigemFila.GPS, OrigemFila.GRUPPY]).sum())
    return ResultadoFilaLoja(len(c), len(c) - len(fora), len(registros), len(ja_loja), em_outra)


def _limiar_sugestao() -> float:
    from core.config import settings

    return settings.reconciliacao.limiar_fila_media
