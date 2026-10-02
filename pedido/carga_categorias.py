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
    a = p.parse_args(argv)

    comeco = time.monotonic()
    febrafar = categorias.ler_febrafar(a.febrafar)
    cmed = categorias.ler_cmed(a.cmed)
    base = categorias.montar_base_inicial(febrafar, cmed)
    resumo = categorias.resumo(base)
    print(f"lidos: FEBRAFAR {len(febrafar)} EANs, CMED {len(cmed)} EANs ({time.monotonic() - comeco:.0f}s)")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))

    chaves = Chaves(settings.pedido.prefixo)
    if not a.executar:
        print(f"PRÉVIA — nada gravado. Com --executar grava {chaves.base_categorias()} em {a.destino}.")
        return 0

    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    armaz.salvar_df(chaves.base_categorias(), base)
    armaz.salvar_json(chaves.base_categorias_info(), {
        **resumo,
        "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "fontes": {"febrafar": Path(a.febrafar).name, "cmed": Path(a.cmed).name},
    })
    print(f"gravado: {chaves.base_categorias()} ({a.destino})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
