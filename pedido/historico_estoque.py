"""Histórico do estoque das lojas (27/10/2026, design em docs/specs/2026-10-26-dashboard-ruptura-design.md, R1).

Por que existe: a perda por ruptura do Dashboard é MEDIDA (dias reais em falta), e para isso é preciso saber quando o
estoque de cada produto zerou. A API do GPS só tem o estoque de agora (nenhuma rota aceita data; conferido em
27/10/2026 na especificação), e a foto de cada noite substituía a anterior. A partir daqui, a rotina guarda o que
MUDOU a cada foto.

Formato, por loja, no Spaces:
- `historico_estoque/{empresa}/{loja}/AAAA-MM.parquet`: linhas (codigo_produto, data, estoque). Cada mês ABRE com a
  foto inteira do primeiro dia em que houve foto naquele mês, e depois só tem as mudanças — assim um mês é lido
  sozinho, sem precisar do anterior. Medido na loja 60: ~326 produtos mudam por dia; 13 meses ≈ 0,6 MB só de
  mudanças (+ ~16 mil linhas de abertura por mês).
- `historico_estoque/{empresa}/{loja}/_fotos.json`: os dias em que houve foto. Sem isso não daria para distinguir
  "não mudou" de "não teve foto" (regra R9 do design).

Aqui não se decide o que é ruptura: a rotina roda sem banco e não conhece o N de cada loja nem o agrupamento dos
genéricos. Ela guarda o estoque; o app aplica as regras ao ler.
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves

RETENCAO_MESES = 13
COLUNAS = ["codigo_produto", "data", "estoque"]
_ARQUIVO_MES = re.compile(r"^(\d{4}-\d{2})\.parquet$")


def _mes(dia: dt.date) -> str:
    return f"{dia.year:04d}-{dia.month:02d}"


def _vazio() -> pd.DataFrame:
    return pd.DataFrame({"codigo_produto": pd.Series(dtype="string"), "data": pd.Series(dtype="string"),
                         "estoque": pd.Series(dtype="float64")})


def _foto(estoque: pd.DataFrame) -> pd.Series:
    """A foto do dia como código do produto → estoque. Estoque vazio conta como 0; código repetido (não deveria
    acontecer numa loja) fica com o primeiro."""
    codigos = estoque["codigo_produto"].astype("string").str.strip()
    valores = pd.to_numeric(estoque["qtd_estoque"], errors="coerce").fillna(0.0).astype("float64")
    serie = pd.Series(valores.to_numpy(), index=codigos.to_numpy())
    serie = serie[serie.index.notna() & (serie.index != "")]
    return serie[~serie.index.duplicated(keep="first")]


def _estado_antes(mes_df: pd.DataFrame, dia: str) -> pd.Series:
    """O último valor conhecido de cada produto ANTES do dia, dentro do mês (o mês abre com a foto inteira)."""
    antes = mes_df[mes_df["data"] < dia]
    if antes.empty:
        return pd.Series(dtype="float64")
    ultimo = antes.sort_values(["codigo_produto", "data"]).drop_duplicates("codigo_produto", keep="last")
    return pd.Series(ultimo["estoque"].to_numpy(dtype="float64"), index=ultimo["codigo_produto"].to_numpy())


def registrar(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, estoque: pd.DataFrame,
              dia: dt.date) -> int:
    """Grava a foto `estoque` (colunas `codigo_produto` e `qtd_estoque`, a fatia da loja que a rotina acabou de
    baixar) como a do `dia`. Guarda só o que mudou desde a última foto do mês; no 1º dia com foto do mês, a foto
    inteira. Rodar de novo no mesmo dia SUBSTITUI o dia (a rotina pode baixar a mesma empresa duas vezes). Devolve
    quantas linhas o dia gravou."""
    texto = dia.isoformat()
    chave = chaves.historico_estoque(empresa, loja, _mes(dia))
    mes_df = armaz.ler_df(chave) if armaz.existe(chave) else _vazio()
    mes_df = mes_df.astype({"codigo_produto": "string", "data": "string", "estoque": "float64"})
    mes_df = mes_df[mes_df["data"] != texto]                         # o dia é refeito por inteiro

    foto = _foto(estoque)
    antes = _estado_antes(mes_df, texto)
    if antes.empty:
        mudou = foto                                                # abertura do mês: a foto inteira
    else:
        anterior = antes.reindex(foto.index)
        mudou = foto[anterior.isna() | (anterior != foto)]
    novas = pd.DataFrame({"codigo_produto": pd.Series(mudou.index, dtype="string"),
                          "data": pd.Series([texto] * len(mudou), dtype="string"),
                          "estoque": mudou.to_numpy(dtype="float64")})
    # Os dias DEPOIS deste (rodar um dia antigo de novo é raro, mas possível) continuam como estavam.
    saida = pd.concat([mes_df, novas], ignore_index=True).sort_values(["data", "codigo_produto"], kind="stable")
    armaz.salvar_df(chave, saida.reset_index(drop=True)[COLUNAS])

    fotos = _ler_fotos(armaz, chaves, empresa, loja)
    fotos.add(texto)
    limite = _limite(dia)
    armaz.salvar_json(chaves.historico_estoque_fotos(empresa, loja), {"dias": sorted(d for d in fotos if d >= limite)})
    _aplicar_retencao(armaz, chaves, empresa, loja, dia)
    return len(novas)


def _ler_fotos(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str) -> set[str]:
    chave = chaves.historico_estoque_fotos(empresa, loja)
    if not armaz.existe(chave):
        return set()
    return set((armaz.ler_json(chave) or {}).get("dias", []))


def _limite(dia: dt.date) -> str:
    """O 1º dia do mês mais antigo que fica: 13 meses contando o atual."""
    ano, mes = dia.year, dia.month - (RETENCAO_MESES - 1)
    while mes <= 0:
        ano, mes = ano - 1, mes + 12
    return f"{ano:04d}-{mes:02d}-01"


def _aplicar_retencao(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, dia: dt.date) -> int:
    """Apaga os meses mais velhos que 13. Só arquivos com o nome exato `AAAA-MM.parquet` DENTRO da pasta desta loja
    no histórico: o bucket é compartilhado com outros sistemas e nada se apaga por padrão de nome largo."""
    prefixo = chaves.prefixo_historico_estoque(empresa, loja)
    corte = _limite(dia)[:7]
    apagados = 0
    for chave in armaz.listar(prefixo):
        nome = chave[len(prefixo):]
        achado = _ARQUIVO_MES.match(nome)
        if achado and achado.group(1) < corte:
            armaz.apagar(chave)
            apagados += 1
    return apagados


def ler(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, desde: dt.date,
        ate: dt.date) -> tuple[pd.DataFrame, list[str]]:
    """(linhas do histórico de `desde` a `ate`, dias com foto nesse intervalo). As linhas vêm dos meses inteiros que
    tocam o intervalo (cada mês abre com a foto inteira): quem lê sabe o estoque de cada produto em qualquer dia com
    foto. Mês sem arquivo simplesmente não aparece."""
    meses = []
    atual = dt.date(desde.year, desde.month, 1)
    while atual <= ate:
        meses.append(_mes(atual))
        atual = dt.date(atual.year + (atual.month == 12), atual.month % 12 + 1, 1)
    chaves_mes = [c for c in (chaves.historico_estoque(empresa, loja, m) for m in meses) if armaz.existe(c)]
    partes = armaz.ler_varios(chaves_mes, ler=armaz.ler_df) if chaves_mes else []
    linhas = pd.concat(partes, ignore_index=True) if partes else _vazio()
    linhas = linhas.astype({"codigo_produto": "string", "data": "string", "estoque": "float64"})
    linhas = linhas[linhas["data"] <= ate.isoformat()].reset_index(drop=True)
    fotos = sorted(d for d in _ler_fotos(armaz, chaves, empresa, loja) if desde.isoformat() <= d <= ate.isoformat())
    return linhas, fotos
