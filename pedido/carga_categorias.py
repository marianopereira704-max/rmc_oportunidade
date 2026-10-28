"""Monta a base inicial de categorias (FEBRAFAR + CMED) e guarda no Spaces,
de onde o app a carrega no banco (Dados → Categorias de produtos →
"Carregar base inicial").

    python -m pedido.carga_categorias --febrafar CATEGORIAS_FEBRAFAR.xlsx --cmed cmed.xlsx
    python -m pedido.carga_categorias ... --executar        # grava no Spaces
    python -m pedido.carga_categorias ... --destino local:C:/tmp/pedido --executar

Sem `--executar` só mostra a prévia (contagens) e não grava nada.

Por que o arquivo passa pelo Spaces em vez de ir direto pro banco: o
secrets.toml local aponta pro banco de DEV, e o usuário dele não tem
permissão no banco do app publicado — um script daqui não alcançaria
produção. O botão no app alcança o banco em que o app roda, seja qual for.
E a planilha FEBRAFAR é dado de terceiro: não pode ir pro repositório
(público).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

from core.config import settings
from pedido import categorias
from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
from pedido.plano import Chaves


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pedido.carga_categorias", description=__doc__.split("\n")[0])
    p.add_argument("--febrafar", required=True, help="CATEGORIAS_FEBRAFAR.xlsx")
    p.add_argument("--cmed", required=True, help="lista de preços da CMED (.xlsx do gov.br)")
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--executar", action="store_true", help="grava o arquivo (sem isso, só a prévia)")
    p.add_argument("--so-classificacao", action="store_true",
                   help="grava só o arquivo da classificação por EAN (cascata) e NÃO refaz a base inicial: a base "
                        "decide os dias de estoque, e atualizar a CMED nela é outra decisão")
    a = p.parse_args(argv)

    comeco = time.monotonic()
    febrafar = categorias.ler_febrafar(a.febrafar)
    cmed = categorias.ler_cmed(a.cmed)
    base = categorias.montar_base_inicial(febrafar, cmed)
    resumo = categorias.resumo(base)
    classificacao = categorias.montar_classificacao(febrafar, cmed)
    resumo_class = categorias.resumo_classificacao(classificacao)
    print(f"lidos: FEBRAFAR {len(febrafar)} EANs, CMED {len(cmed)} EANs ({time.monotonic() - comeco:.0f}s)")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    # Os tipos da CMED que o arquivo realmente traz (design de 25/10/2026: só Genérico e Similar classificam; os
    # demais vão para "Em classificação"). Conferir esta lista antes de gravar.
    print("Classificação por EAN (cascata):")
    print(json.dumps(resumo_class, ensure_ascii=False, indent=1))

    chaves = Chaves(settings.pedido.prefixo)
    if not a.executar:
        alvo = chaves.classificacao_ean() if a.so_classificacao else f"{chaves.base_categorias()} e {chaves.classificacao_ean()}"
        print(f"PRÉVIA — nada gravado. Com --executar grava {alvo} em {a.destino}.")
        return 0

    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    if not a.so_classificacao:
        armaz.salvar_df(chaves.base_categorias(), base)
        armaz.salvar_json(chaves.base_categorias_info(), {
            **resumo,
            "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
            "fontes": {"febrafar": Path(a.febrafar).name, "cmed": Path(a.cmed).name},
        })
    armaz.salvar_df(chaves.classificacao_ean(), classificacao)
    armaz.salvar_json(chaves.classificacao_ean_info(), {
        **resumo_class,
        "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "fontes": {"febrafar": Path(a.febrafar).name, "cmed": Path(a.cmed).name},
    })
    gravados = chaves.classificacao_ean() if a.so_classificacao else f"{chaves.base_categorias()} e {chaves.classificacao_ean()}"
    print(f"gravado: {gravados} ({a.destino})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
