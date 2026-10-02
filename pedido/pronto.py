"""Arquivo "pronto" de uma loja: os ~60 arquivos brutos que a rotina grava
(3 meses fechados + um arquivo por dia do mês corrente, de vendas e de
compras, + o estoque) juntados em 3, para a tela Pedido abrir em segundos.

Quem monta: a própria tela, na primeira abertura depois que a rotina gravou
algo novo (e a rotina pode chamar o mesmo `atualizar` no fim). Como saber se
o pronto está velho: ele guarda a ASSINATURA dos arquivos brutos que usou
(a lista de chaves + a data da foto de estoque). Uma listagem da pasta da
loja (1 requisição) diz se mudou; se não mudou, a tela lê só os 3 arquivos.

Medido em 28/09/2026 com a Hudson (58 arquivos brutos): ler tudo do Spaces
em sequência levaria ~60 × 0,2 s; em paralelo (16 de cada vez) ~1–2 s, e o
pronto (3 arquivos) ~0,5 s.

    pronto/{empresa}/{loja}/vendas.parquet    unidades e valor por (produto, dia)
    pronto/{empresa}/{loja}/compras.parquet   as compras da janela, linha a linha
    pronto/{empresa}/{loja}/estoque.parquet   a foto do estoque (só o que importa)
    pronto/{empresa}/{loja}/meta.json         assinatura, janela, dias sem dado

Nada de regra de pedido aqui (dias de estoque, curva, preço…): isso é
configurável (Configurações de Pedidos) e fica em pedido/calculo.py, sobre
estes 3 quadros.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd

from pedido import plano, transformar
from pedido.armazenamento import Armazenamento
from pedido.plano import COMPRAS, VENDAS, Chaves

_LEITURAS_PARALELAS = 16


@dataclass
class Pronto:
    vendas: pd.DataFrame      # codigo_produto, data, unidades, valor
    compras: pd.DataFrame     # transformar.COLUNAS_COMPRAS
    estoque: pd.DataFrame     # transformar.COLUNAS_ESTOQUE
    meta: dict = field(default_factory=dict)


def _pasta(chaves: Chaves, empresa: str, loja: str) -> str:
    return f"{chaves.prefixo}/bruto/{empresa}/{loja}/"


def _chave_pronto(chaves: Chaves, empresa: str, loja: str, nome: str) -> str:
    return f"{chaves.prefixo}/pronto/{empresa}/{loja}/{nome}"


def data_da_foto(armaz: Armazenamento, chaves: Chaves, empresa: str) -> dt.date | None:
    """Dia em que a rotina tirou a última foto de estoque da empresa — é o
    "hoje" dos dados: a janela vai até a véspera dele. Usar a data do
    relógio erraria entre a meia-noite e o fim da rotina (ontem ainda não
    baixado) e em qualquer dia em que a rotina falhe."""
    chave = chaves.marcador_estoque(empresa)
    if not armaz.existe(chave):
        return None
    return dt.date.fromisoformat(armaz.ler_json(chave)["data"])


def _arquivos_da_janela(existentes: set[str], chaves: Chaves, empresa: str, loja: str, tipo: str,
                        hoje: dt.date, meses_fechados: int) -> tuple[list[str], list[dt.date]]:
    """Chaves a ler e os dias da janela SEM arquivo (a demanda desses dias
    fica de fora — a tela avisa). Mês fechado sem o arquivo do mês, mas com
    os arquivos por dia (virada de mês antes da rotina rebaixar o mês
    inteiro), usa os dias."""
    ler, faltando = [], []
    for p in plano.periodos(hoje, meses_fechados):
        chave_mes = chaves.mes(empresa, loja, tipo, p.mes)
        if p.fechado and chave_mes in existentes:
            ler.append(chave_mes)
            continue
        dia = p.inicio
        while dia <= p.fim:
            chave_dia = chaves.dia(empresa, loja, tipo, dia)
            if chave_dia in existentes:
                ler.append(chave_dia)
            else:
                faltando.append(dia)
            dia += dt.timedelta(days=1)
    return ler, faltando


def _assinatura(chaves_lidas: list[str], foto: dt.date | None) -> str:
    h = hashlib.sha1()
    for c in sorted(chaves_lidas):
        h.update(c.encode())
    h.update(str(foto).encode())
    return h.hexdigest()


def _ler_varios(armaz: Armazenamento, chaves_: list[str]) -> list[pd.DataFrame]:
    if not chaves_:
        return []
    with ThreadPoolExecutor(max_workers=_LEITURAS_PARALELAS) as ex:
        return list(ex.map(armaz.ler_df, chaves_))


def _vendas_por_dia(quadros: list[pd.DataFrame]) -> pd.DataFrame:
    """Vendas item a item → (produto, dia): Hudson 30 mil linhas → ~25 mil,
    e sem as colunas que o cálculo não usa."""
    quadros = [q for q in quadros if not q.empty]
    if not quadros:
        return pd.DataFrame({"codigo_produto": pd.Series(dtype="string"), "data": pd.Series(dtype="string"),
                             "unidades": pd.Series(dtype="float64"), "valor": pd.Series(dtype="float64")})
    v = pd.concat(quadros, ignore_index=True)
    return (v.groupby(["codigo_produto", "data"], as_index=False)
             .agg(unidades=("quantidade", "sum"), valor=("valor_liquido", "sum")))


def _juntar(quadros: list[pd.DataFrame], colunas: list[str]) -> pd.DataFrame:
    quadros = [q for q in quadros if not q.empty]
    if not quadros:
        return transformar._quadro([], colunas)
    return pd.concat(quadros, ignore_index=True)


def vendas_por_ean(p: Pronto) -> pd.DataFrame:
    """ean (chave), unidades, valor vendidos na janela — pra o catálogo da
    empresa dizer quais EANs a loja VENDEU (fila de EAN "Loja (API)")."""
    from pedido import categorias

    if p.vendas.empty:
        return pd.DataFrame({"ean": pd.Series(dtype="string"), "unidades": pd.Series(dtype="float64"),
                             "valor": pd.Series(dtype="float64")})
    ean_do_produto = (p.estoque.assign(ean=categorias.chaves_ean(p.estoque["ean"]))
                      .dropna(subset=["ean"]).drop_duplicates("codigo_produto")
                      .set_index("codigo_produto")["ean"])
    v = p.vendas.assign(ean=p.vendas["codigo_produto"].map(ean_do_produto)).dropna(subset=["ean"])
    return v.groupby("ean", as_index=False).agg(unidades=("unidades", "sum"), valor=("valor", "sum"))


def atualizar(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, meses_fechados: int) -> Pronto | None:
    """O pronto da loja, montado de novo só se os brutos mudaram. None se a
    rotina ainda não tirou nenhuma foto de estoque desta empresa."""
    foto = data_da_foto(armaz, chaves, empresa)
    if foto is None:
        return None
    existentes = set(armaz.listar(_pasta(chaves, empresa, loja)))
    k_vendas, falta_vendas = _arquivos_da_janela(existentes, chaves, empresa, loja, VENDAS, foto, meses_fechados)
    k_compras, falta_compras = _arquivos_da_janela(existentes, chaves, empresa, loja, COMPRAS, foto, meses_fechados)
    k_estoque = chaves.estoque(empresa, loja)
    tem_estoque = k_estoque in existentes
    assinatura = _assinatura(k_vendas + k_compras + ([k_estoque] if tem_estoque else []), foto)

    k_meta = _chave_pronto(chaves, empresa, loja, "meta.json")
    if armaz.existe(k_meta):
        meta = armaz.ler_json(k_meta)
        if meta.get("assinatura") == assinatura:
            nomes = ("vendas.parquet", "compras.parquet", "estoque.parquet")
            v, c, e = _ler_varios(armaz, [_chave_pronto(chaves, empresa, loja, n) for n in nomes])
            return Pronto(v, c, e, meta)

    lidos = _ler_varios(armaz, k_vendas + k_compras + ([k_estoque] if tem_estoque else []))
    vendas = _vendas_por_dia(lidos[:len(k_vendas)])
    compras = _juntar(lidos[len(k_vendas):len(k_vendas) + len(k_compras)], transformar.COLUNAS_COMPRAS)
    estoque = lidos[-1] if tem_estoque else transformar._quadro([], transformar.COLUNAS_ESTOQUE)
    # Do cadastro inteiro (Hudson: 49 mil produtos) só interessa o que tem
    # estoque, venda ou compra — o resto nunca vira linha do pedido.
    usados = set(vendas["codigo_produto"]) | set(compras["codigo_produto"])
    estoque = estoque[(estoque["qtd_estoque"].fillna(0) != 0) | estoque["codigo_produto"].isin(usados)]
    estoque = estoque.reset_index(drop=True)

    inicio, ontem = plano.janela(foto, meses_fechados)
    meta = {
        "assinatura": assinatura,
        "data_foto": foto.isoformat(),
        "inicio": inicio.isoformat(),
        "fim": ontem.isoformat(),
        "meses_fechados": meses_fechados,
        "dias_sem_vendas": [d.isoformat() for d in falta_vendas],
        "dias_sem_compras": [d.isoformat() for d in falta_compras],
        "tem_estoque": tem_estoque,
        "montado_em": dt.datetime.now().isoformat(timespec="seconds"),
    }
    armaz.salvar_df(_chave_pronto(chaves, empresa, loja, "vendas.parquet"), vendas)
    armaz.salvar_df(_chave_pronto(chaves, empresa, loja, "compras.parquet"), compras)
    armaz.salvar_df(_chave_pronto(chaves, empresa, loja, "estoque.parquet"), estoque)
    # O meta vai por último: sem ele, a próxima abertura monta de novo.
    armaz.salvar_json(k_meta, meta)
    return Pronto(vendas, compras, estoque, meta)
