"""Cópia no Spaces de tabelas grandes lidas do banco (28/10/2026, Fase 7 do plano de publicação).

Medido em 28/10/2026: as duas bases que TODA tela do Assistente e do Dashboard usa levam, frias, 6,9 s (base de
categorias) e 16 s (as três fontes da classificação, 273 mil EANs) para vir do banco em NY — é a transferência das
linhas, não a conta. O cache do Streamlit vale só enquanto o processo vive, e o Community Cloud reinicia o app
(deploy, hibernação, falta de memória): o 1º usuário depois de cada reinício pagava os ~23 s. Aqui, quem monta
grava um Parquet no Spaces com a VERSÃO das fontes no nome; o próximo processo lê o arquivo em vez do banco.

- A versão é a mesma que já decide o cache do Streamlit (contagens e datas de atualização das tabelas): mudou a
  fonte, muda o nome do arquivo e ninguém lê dado velho.
- A pasta leva o nome do BANCO: o app local (banco dev) e o publicado não leem a cópia um do outro.
- Ficam as 2 versões mais recentes de cada tabela (`_indice.json`); as outras são apagadas UMA A UMA, só
  chaves desta pasta que este módulo criou (o bucket é compartilhado com outros sistemas — nunca apagar por padrão).
- Qualquer falha do Spaces cai no banco, como antes: a cópia é só um atalho.
"""
from __future__ import annotations

import hashlib
import logging

import pandas as pd

from core.config import settings

logger = logging.getLogger(__name__)

MANTER = 2


def _pasta(nome: str) -> str:
    from sqlalchemy.engine import make_url

    banco = make_url(settings.db.url).database or "banco"
    banco = banco.replace("\\", "/").rsplit("/", 1)[-1]          # SQLite: só o nome do arquivo
    return f"{settings.pedido.prefixo}/cache_app/{banco}/{nome}/"


def _assinatura(versao) -> str:
    return hashlib.sha1(repr(versao).encode("utf-8")).hexdigest()[:16]


def ler_ou_montar(nome: str, versao, montar, armaz=None) -> pd.DataFrame:
    """A tabela `nome` na `versao`: do Spaces se já houver cópia; senão `montar()` (o banco) e grava a cópia."""
    from pedido.armazenamento import do_ambiente

    pasta = _pasta(nome)
    chave = f"{pasta}{_assinatura(versao)}.parquet"
    try:
        armaz = armaz or do_ambiente()
        if armaz.existe(chave):
            return armaz.ler_df(chave)
    except Exception:  # noqa: BLE001 — sem Spaces: lê do banco, como antes
        logger.warning("cópia de %s não lida; lendo do banco", nome, exc_info=True)
        return montar()
    df = montar()
    try:
        armaz.salvar_df(chave, df)
        _manter_recentes(armaz, pasta, chave)
    except Exception:  # noqa: BLE001 — a cópia é só um atalho
        logger.warning("cópia de %s não gravada", nome, exc_info=True)
    return df


def _manter_recentes(armaz, pasta: str, chave: str) -> None:
    k_indice = f"{pasta}_indice.json"
    indice = armaz.ler_json(k_indice) if armaz.existe(k_indice) else []
    indice = [chave] + [c for c in indice if c != chave]
    for velha in indice[MANTER:]:
        if velha.startswith(pasta) and velha.endswith(".parquet"):
            armaz.apagar(velha)
    armaz.salvar_json(k_indice, indice[:MANTER])
