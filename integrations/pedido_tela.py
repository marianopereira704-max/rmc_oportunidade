"""O que a tela Pedido lê do banco: as lojas (com o vínculo GPS de cada uma)
e a Base Genéricos. Duas consultas, cada uma de uma vez — o banco está a
~138 ms daqui; nada por loja ou por produto.

A categoria vem de integrations/categorias.py; vendas, compras e estoque
vêm do Spaces (pedido/pronto.py)."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import BaseGenerico, EanGenerico, Loja, SituacaoVinculoGps, VinculoLojaGps
from pedido import categorias as cat

_VALIDOS = (SituacaoVinculoGps.AUTOMATICO, SituacaoVinculoGps.CONFIRMADO)


def lojas(session: Session) -> list[dict]:
    """Todas as lojas do cadastro, com (empresa, loja) do GPS quando há
    vínculo válido. Loja sem vínculo continua na lista — a tela diz por que
    ela não tem pedido, em vez de sumir com ela."""
    vinculos = {
        v.loja_id: (v.id_empresa_gps, v.codigo_loja_gps)
        for v in session.scalars(select(VinculoLojaGps).where(
            VinculoLojaGps.situacao.in_(_VALIDOS), VinculoLojaGps.loja_id.is_not(None)))
    }
    saida = []
    for l in session.scalars(select(Loja).order_by(Loja.razao_social)):
        gps = vinculos.get(l.id)
        saida.append({
            "id": l.id, "razao_social": l.razao_social, "nome_fantasia": l.nome_fantasia, "cnpj": l.cnpj,
            "cidade": l.cidade, "uf": l.uf, "legacy_id": l.legacy_id,
            "empresa_gps": gps[0] if gps else None, "loja_gps": gps[1] if gps else None,
        })
    return saida


def versao_genericos(session: Session) -> tuple:
    total, ultima = session.execute(select(func.count(), func.max(EanGenerico.resolvido_em))).one()
    return int(total or 0), str(ultima or "")


def genericos(session: Session) -> pd.DataFrame:
    """ean (chave de pedido/categorias.chave_ean), base_generico_id,
    nome_canonico — a unificação dos genéricos no pedido."""
    linhas = session.execute(
        select(EanGenerico.ean, EanGenerico.base_generico_id, BaseGenerico.nome_canonico)
        .join(BaseGenerico, BaseGenerico.id == EanGenerico.base_generico_id)
        .where(BaseGenerico.ativo.is_(True))
    ).all()
    df = pd.DataFrame(linhas, columns=["ean", "base_generico_id", "nome_canonico"])
    df["ean"] = cat.chaves_ean(df["ean"])
    return df.dropna(subset=["ean"]).drop_duplicates("ean").reset_index(drop=True)
