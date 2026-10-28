"""Compras da rede FICTÍCIAS pro teste visual da Por Loja (05/10/2026): desde
que a Análise passou a ler as compras da API (core/analise_api.py), a Por
Loja precisa do arquivo de compras da rede por mês (pedido/rede.py), que o
`data.seed` não cria.

Cria, no banco e no armazenamento local DO TESTE (nunca produção):
- vínculo GPS ("SEED", <id da loja>) para as 12 primeiras lojas do seed;
- o arquivo da rede de agosto/2026 com compras dos genéricos das tabelas
  Gruppy do seed: preços acima e abaixo da tabela, duas notas do mesmo
  produto, uma bonificação e uma compra "a revisar" (cadastro × nota).

Tudo determinístico (sem sorteio, sem data de hoje): o teste visual fixa o
"hoje" da Análise em 06/10/2026 (`RMC_HOJE`), então "Último mês" é sempre
agosto/2026.

    python -m tests.visual.compras_rede   # chamado por tests/visual/rodar.py
"""
from __future__ import annotations

import datetime as dt
import os

MES = "2026-08"
EMPRESA = "SEED"


def main() -> None:
    assert os.environ.get("RMC_IGNORAR_SECRETS") == "1", "só roda no ambiente do teste visual"
    import pandas as pd
    from sqlalchemy import select

    from core.config import settings
    from core.db import get_session
    from core.models import BaseGenerico, EanGenerico, Loja, SituacaoVinculoGps, VinculoLojaGps
    from pedido import rede
    from pedido.armazenamento import ArmazenamentoLocal
    from pedido.plano import Chaves

    assert settings.db.url.startswith("sqlite"), "compras fictícias só em SQLite de teste"
    armaz = ArmazenamentoLocal(settings.local_storage_dir)
    ch = Chaves(settings.pedido.prefixo)
    linhas = []
    with get_session() as s:
        lojas = s.scalars(select(Loja).where(Loja.razao_social.like("Farmácia%")).order_by(Loja.id).limit(12)).all()
        genericos = s.execute(
            select(BaseGenerico.id, BaseGenerico.nome_canonico, EanGenerico.ean)
            .join(EanGenerico, EanGenerico.base_generico_id == BaseGenerico.id).order_by(BaseGenerico.id, EanGenerico.ean)
        ).all()
        primeiro_ean = {}
        for gid, nome, ean in genericos:
            primeiro_ean.setdefault(gid, (nome, ean))
        ids = sorted(primeiro_ean)[:30]
        for n, loja in enumerate(lojas):
            s.add(VinculoLojaGps(id_empresa_gps=EMPRESA, codigo_loja_gps=str(loja.id), nome_loja_gps=loja.razao_social,
                                 loja_id=loja.id, situacao=SituacaoVinculoGps.AUTOMATICO, metodo="cnpj",
                                 atualizado_em=dt.datetime(2026, 9, 27)))
            for k, gid in enumerate(ids):
                if (n + k) % 3:
                    continue
                nome, ean = primeiro_ean[gid]
                base = 8 + gid % 40                     # mesmo preço-base das tabelas do seed
                fator = (0.8, 1.05, 1.2, 1.4)[(n + k) % 4]
                linhas.append(_linha(loja.id, ean, nome, f"{1000 + n * 100 + k}", "2026-08-10", 6 + k % 5,
                                     round(base * fator, 2), base * 0.9, base * 2.5))
                if k % 4 == 0:                          # segunda nota do mesmo produto, mais cara
                    linhas.append(_linha(loja.id, ean, nome, f"{2000 + n * 100 + k}", "2026-08-24", 3,
                                         round(base * 1.3, 2), base * 0.9, base * 2.5))
            nome, ean = primeiro_ean[ids[1]]
            linhas.append(_linha(loja.id, ean, nome, f"{3000 + n}", "2026-08-15", 12, 0.01, None, None))  # bonificação
            nome, ean = primeiro_ean[ids[2]]
            base = 8 + ids[2] % 40
            linhas.append(_linha(loja.id, ean, nome, f"{4000 + n}", "2026-08-18", 2, base * 3, base, base * 4))  # a revisar

    df = pd.DataFrame(linhas, columns=rede.COLUNAS)
    for c in rede.COLUNAS:
        if c in ("quantidade", "preco", "vlr_unitario", "vlr_desconto", "fracao", "custo_cadastro", "preco_venda"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        else:
            df[c] = df[c].astype("string")
    armaz.salvar_df(ch.compras_rede(MES), df.sort_values("ean", kind="stable"), linhas_por_grupo=rede.LINHAS_POR_GRUPO)
    armaz.salvar_df(ch.compras_rede_resumo(MES), rede.resumo_por_ean(df))
    armaz.salvar_json(ch.compras_rede_meta(MES), {"mes": MES, "manifesto": {}, "linhas": len(df), "lojas": len(lojas),
                                                   "gerado_em": "2026-09-06T03:00:00"})
    print(f"Compras da rede fictícias: {len(df)} linhas, {len(lojas)} lojas ({MES}).")


def _linha(loja_id, ean, nome, nf, data, qtd, preco, cadastro, venda) -> dict:
    return {"empresa": EMPRESA, "codigo_loja": str(loja_id), "data": data, "nro_nf": nf,
            "fornecedor": "DISTRIBUIDORA TESTE", "laboratorio": "LAB COMPRA", "codigo_produto": ean, "ean": ean,
            "nome": nome, "quantidade": qtd, "preco": preco, "vlr_unitario": preco, "vlr_desconto": 0.0, "fracao": 1.0,
            "custo_cadastro": cadastro, "preco_venda": venda}


if __name__ == "__main__":
    main()
