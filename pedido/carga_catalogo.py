"""Monta o catálogo IQVIA (EAN → SETOR_NEC + GMRS) e guarda no Spaces, de
onde o app o carrega no banco (Dados → Rotinas → "Catálogo IQVIA").

    python -m pedido.carga_catalogo --catalogo CATALOGO_DE_PRODUTOS_202602.xlsx
    python -m pedido.carga_catalogo ... --executar        # grava no Spaces
    python -m pedido.carga_catalogo ... --destino local:C:/tmp/pedido --executar

Sem `--executar` só mostra a prévia (contagens) e não grava nada.

Mesmo caminho da base de categorias (pedido/carga_categorias.py) e pelas
mesmas razões: o secrets.toml local só alcança o banco de DEV, e o catálogo
é dado de terceiro — não pode ir pro repositório (público). Carga única: o
arquivo de 2026 traz período antigo (CD_PERIODO 20220807) e não chegam
catálogos novos (Mariano, 07/10/2026). Ler os 68 MB leva alguns minutos.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd

from core.config import settings
from pedido import linhas
from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
from pedido.plano import Chaves

ABA = "Catalogo_Produtos"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pedido.carga_catalogo", description=__doc__.split("\n")[0])
    p.add_argument("--catalogo", required=True, help="CATALOGO_DE_PRODUTOS_*.xlsx (aba Catalogo_Produtos)")
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--executar", action="store_true", help="grava o arquivo (sem isso, só a prévia)")
    a = p.parse_args(argv)

    comeco = time.monotonic()
    bruto = pd.read_excel(a.catalogo, sheet_name=ABA, usecols=["EAN", "SETOR_NEC", "GMRS"], dtype=str,
                          engine="openpyxl")
    catalogo = linhas.montar_catalogo(bruto)
    resumo = {
        "linhas_no_arquivo": len(bruto),
        "eans": len(catalogo),
        "por_setor": {k: int(v) for k, v in catalogo["setor_nec"].value_counts().items()},
        "genericos_gmrs": int((catalogo["gmrs"] == "G").sum()),
    }
    print(f"lido em {time.monotonic() - comeco:.0f}s")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))

    chaves = Chaves(settings.pedido.prefixo)
    if not a.executar:
        print(f"PRÉVIA — nada gravado. Com --executar grava {chaves.catalogo_iqvia()} em {a.destino}.")
        return 0
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    armaz.salvar_df(chaves.catalogo_iqvia(), catalogo)
    armaz.salvar_json(chaves.catalogo_iqvia_info(), {
        **resumo, "gerado_em": dt.datetime.now().isoformat(timespec="seconds"), "fonte": Path(a.catalogo).name,
    })
    print(f"gravado: {chaves.catalogo_iqvia()} ({a.destino})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
