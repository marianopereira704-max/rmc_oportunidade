"""Modelo de estoque do Dashboard (22/10/2026) — a base da página Estoque.

Spec: docs/specs/2026-10-22-dashboard-estoque-design.md (seção 9).

O que é: um arquivo por loja com UM SKU por linha (só quem tem estoque > 0 no sistema de vendas, vendendo
ou não), já com o custo resolvido, e outro com as vendas por dia. É montado de MADRUGADA, logo depois do
"pronto" da loja (`pedido/coleta.py::_montar_prontos`), a partir do que já está no Spaces — sem consultar a
API de novo. Se a loja ainda não tem o modelo (loja nova, ou a rotina nova ainda não publicada), o app monta
na hora (`obter`), do mesmo jeito que o `pronto`.

Por que não reaproveitar o `calculo.calcular` do Assistente: ele só devolve quem VENDEU na janela, e o
estoque parado de verdade (sem nenhuma venda) fica fora — na loja 60, 55% do valor do estoque (medido em
21/10/2026). Aqui o ponto de partida é o estoque, e a venda é um atributo de cada SKU.

A rotina roda SEM banco: o modelo só usa o que o Spaces tem. Categoria, linha de compra e a personalização da
loja o app junta na hora de ler (milissegundos). Por isso o custo é calculado com os parâmetros do PADRÃO de
todas as lojas (decisão de 22/10/2026): a personalização por loja das regras de custo não vale aqui.

Custo (a mesma lógica do Assistente, por código de produto, não por genérico agrupado):
- último preço de compra não bonificado e dentro do padrão da loja (mediana ± fator), quando há compra na
  janela; senão o custo de cadastro;
- cadastro × nota divergindo, ou preço acima do de venda: o preço de venda decide quem está certo;
- `custo_a_revisar`: o custo não é confiável (compra fora do padrão, custo — de compra OU de cadastro — fora da
  faixa de 15% a 100% do preço de venda, divergência sem solução, ou nenhum custo). A página Estoque NÃO soma esse SKU no R$ (as
  unidades contam) e a tabela mostra "a revisar".
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.config import settings
from pedido import calculo
from pedido import categorias as cat
from pedido import pronto as pronto_mod
from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves
from pedido.pronto import Pronto

# Muda quando a lógica do modelo muda: o app e a rotina refazem tudo na próxima passada.
VERSAO = 3          # 2: a meta ganhou `skus_negativos`; 3: custo fora da faixa do preço de venda sempre vai para revisar (22/10/2026)
MOTIVO_SEM_CUSTO = "Sem custo no cadastro nem em compra"
# O Assistente só corrige o preço acima do de venda quando existe OUTRA compra boa; sem ela, deixa passar sem
# marca (a Lixa Santa Clara da loja 60: R$ 30,97 de custo, em cadastro e em nota, para um preço de venda de
# R$ 0,50 — R$ 8.950 no topo dos críticos). Para valorizar estoque isso não basta: a faixa vale para qualquer custo.
MOTIVO_FAIXA = "Custo fora da faixa do preço de venda"

ARQ_SKUS = "skus.parquet"
ARQ_VENDAS = "vendas_dia.parquet"
ARQ_META = "meta.json"

COLUNAS_SKUS = [
    "codigo_produto", "ean", "nome", "laboratorio", "estoque", "custo", "custo_origem", "custo_a_revisar",
    "custo_motivo", "custo_cadastro", "preco_venda", "compra_data", "compra_fornecedor", "compra_preco",
    "ultima_compra", "ultima_venda",
]
COLUNAS_VENDAS = ["codigo_produto", "data", "unidades", "valor"]


@dataclass
class Modelo:
    skus: pd.DataFrame          # um SKU por linha (COLUNAS_SKUS)
    vendas_dia: pd.DataFrame    # (produto, dia): unidades e valor — de TODOS os produtos que venderam na janela
    meta: dict


def parametros_padrao() -> calculo.Parametros:
    """Os parâmetros do padrão de todas as lojas (a rotina não tem banco)."""
    from pedido.configuracao import ConfigPedido

    return ConfigPedido.padrao().parametros(settings.analise.limite_bonificacao)


def assinatura(assinatura_pronto: str, params: calculo.Parametros) -> str:
    """Muda quando o pronto da loja muda, quando a lógica muda ou quando os parâmetros do custo mudam."""
    h = hashlib.sha1()
    h.update(f"{assinatura_pronto}|{VERSAO}|".encode())
    h.update(json.dumps(dataclasses.asdict(params), sort_keys=True, default=str).encode())
    return h.hexdigest()


def _vazio() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUNAS_SKUS})


def construir(p: Pronto, params: calculo.Parametros | None = None) -> Modelo:
    """O modelo da loja a partir do "pronto" dela. Puro: não lê nem grava nada."""
    params = params or parametros_padrao()
    est = p.estoque.copy()
    est["estoque"] = pd.to_numeric(est["qtd_estoque"], errors="coerce").fillna(0)
    # Só quem tem saldo positivo: negativo e zerado não são estoque (decisão de 22/10/2026).
    est = est.drop_duplicates("codigo_produto", keep="first")
    negativos = int((est["estoque"] < 0).sum())     # a página avisa quantos ficam de fora
    est = est[est["estoque"] > 0].reset_index(drop=True)
    vendas = p.vendas[["codigo_produto", "data", "unidades", "valor"]].copy()
    meta = {
        "versao": VERSAO, "pronto": p.meta.get("assinatura", ""), "assinatura": assinatura(p.meta.get("assinatura", ""), params),
        "data_foto": p.meta.get("data_foto"), "inicio": p.meta.get("inicio"), "fim": p.meta.get("fim"),
        "dias_sem_vendas": list(p.meta.get("dias_sem_vendas", [])), "skus": int(len(est)), "skus_negativos": negativos,
        "montado_em": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if est.empty:
        return Modelo(_vazio(), vendas, meta)

    codigos = pd.Index(est["codigo_produto"], name="codigo_produto")
    por = _custo_por_produto(est, p.compras, params)

    custo = pd.to_numeric(por["preco"], errors="coerce")
    sem_custo = custo.isna() | (custo <= 0)
    venda = pd.to_numeric(por["preco_venda"], errors="coerce")
    fora_da_faixa = (venda > 0) & ~sem_custo & ((custo < venda * params.custo_faixa_min) | (custo > venda * params.custo_faixa_max))
    a_revisar = por["a_revisar"].astype(bool) | sem_custo | fora_da_faixa
    motivo = por["revisar_motivo"].astype("object")
    motivo = motivo.where(~(sem_custo & motivo.isna()), MOTIVO_SEM_CUSTO)
    motivo = motivo.where(~(fora_da_faixa & motivo.isna()), MOTIVO_FAIXA)
    origem = np.where(sem_custo, "sem", np.where((por["preco_origem"] == "compra") & (por["custo_ajustado"] != "cadastro"),
                                                 "compra", "cadastro"))
    e = est.set_index("codigo_produto")
    compras = p.compras[p.compras["codigo_produto"].isin(codigos)]
    ultima_compra = compras.groupby("codigo_produto")["data"].max()
    ultima_venda = vendas.groupby("codigo_produto")["data"].max()
    lab_compra = por["compra_laboratorio"]
    saida = pd.DataFrame({
        "ean": cat.chaves_ean(e["ean"]),
        "nome": e["nome"].where(e["nome"].notna(), "Produto " + e.index.astype(str)),
        "laboratorio": lab_compra.where(lab_compra.notna(), e["laboratorio"]),
        "estoque": e["estoque"],
        "custo": custo.where(~sem_custo),
        "custo_origem": pd.Series(origem, index=por.index),
        "custo_a_revisar": a_revisar,
        "custo_motivo": motivo,
        "custo_cadastro": pd.to_numeric(por["custo_cadastro"], errors="coerce"),
        "preco_venda": pd.to_numeric(por["preco_venda"], errors="coerce"),
        "compra_data": por["compra_data"],
        "compra_fornecedor": por["compra_fornecedor"],
        "compra_preco": pd.to_numeric(por["compra_preco"], errors="coerce"),
        "ultima_compra": ultima_compra.reindex(por.index),
        "ultima_venda": ultima_venda.reindex(por.index),
    }, index=por.index).reset_index()
    return Modelo(saida[COLUNAS_SKUS], vendas, meta)


def _custo_por_produto(est: pd.DataFrame, compras: pd.DataFrame, params: calculo.Parametros) -> pd.DataFrame:
    """O custo de cada código de produto pelas regras do Assistente (calculo.py): mesma sequência, mas com o
    CÓDIGO como "linha" (o Assistente agrupa genéricos; o estoque não)."""
    codigos = est["codigo_produto"]
    c = compras[compras["codigo_produto"].isin(codigos)].copy()
    c["linha"] = c["codigo_produto"]
    fracao = c["fracao"].where(c["fracao"] > 0, 1).fillna(1)
    c = c.assign(fracao=fracao, preco=c["vlr_unitario"].fillna(0) * (1 - c["vlr_desconto"].fillna(0) / 100) / fracao)
    c = c.assign(bonificado=c["preco"] < params.limite_bonificacao)
    ref = calculo._compra_de_referencia(c, params.fator_preco_fora)

    por = pd.DataFrame(index=pd.Index(codigos, name="codigo_produto"))
    for origem in calculo._COLUNAS_COMPRA:
        por["compra_" + origem] = ref[origem].reindex(por.index) if origem in ref else None
    por["compra_a_revisar"] = ref["a_revisar"].reindex(por.index).astype("boolean").fillna(False).astype(bool)
    por["compra_ignorada_preco"] = ref["ignorada_preco"].reindex(por.index)
    por["compra_ignorada_data"] = ref["ignorada_data"].reindex(por.index)
    por["compra_preco"] = pd.to_numeric(por["compra_preco"], errors="coerce")
    sem_compra = por["compra_preco"].isna()
    e = est.set_index("codigo_produto")
    por["custo_cadastro"] = pd.to_numeric(e["vlr_preco_compra"], errors="coerce").reindex(por.index)
    venda = e["vlr_preco_venda"] if "vlr_preco_venda" in e.columns else pd.Series(np.nan, index=e.index)
    por["preco_venda"] = pd.to_numeric(venda, errors="coerce").reindex(por.index)
    por["preco"] = por["compra_preco"].where(~sem_compra, por["custo_cadastro"])
    por["preco_origem"] = np.where(sem_compra, np.where(por["custo_cadastro"] > 0, "cadastro", "sem"), "compra")
    calculo._ajustar_custo(por, params)
    calculo._corrigir_acima_da_venda(por, c, params)
    return por


# ---------------------------------------------------------------------------
# Spaces: gravar, ler, reaproveitar
# ---------------------------------------------------------------------------

def _chave(chaves: Chaves, empresa: str, loja: str, nome: str) -> str:
    return chaves.estoque_modelo(empresa, loja, nome)


def ler(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str) -> Modelo | None:
    k_meta = _chave(chaves, empresa, loja, ARQ_META)
    if not armaz.existe(k_meta):
        return None
    meta = armaz.ler_json(k_meta)
    skus, vendas = armaz.ler_varios([_chave(chaves, empresa, loja, ARQ_SKUS), _chave(chaves, empresa, loja, ARQ_VENDAS)],
                                    ler=armaz.ler_df)
    return Modelo(skus, vendas, meta)


def _meta_gravada(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str) -> dict | None:
    k = _chave(chaves, empresa, loja, ARQ_META)
    try:
        return armaz.ler_json(k) if armaz.existe(k) else None
    except Exception:  # noqa: BLE001 — meta ilegível: refaz
        return None


def atualizar(armaz: Armazenamento, chaves: Chaves, p: Pronto, empresa: str, loja: str,
              params: calculo.Parametros | None = None) -> bool:
    """Monta e grava o modelo se ele não está em dia com este "pronto". True = montou; False = já estava em
    dia (a assinatura é a do pronto + a versão da lógica + os parâmetros do custo). O meta vai por último:
    sem ele, a próxima passada monta de novo."""
    params = params or parametros_padrao()
    esperada = assinatura(p.meta.get("assinatura", ""), params)
    atual = _meta_gravada(armaz, chaves, empresa, loja)
    if atual is not None and atual.get("assinatura") == esperada and atual.get("versao") == VERSAO:
        return False
    m = construir(p, params)
    armaz.salvar_df(_chave(chaves, empresa, loja, ARQ_SKUS), m.skus)
    armaz.salvar_df(_chave(chaves, empresa, loja, ARQ_VENDAS), m.vendas_dia)
    armaz.salvar_json(_chave(chaves, empresa, loja, ARQ_META), m.meta)
    return True


def obter(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, meses_fechados: int,
          params: calculo.Parametros | None = None) -> Modelo | None:
    """O modelo da loja para o app. Em dia (o pronto não mudou): só lê os 3 arquivos dele, sem tocar no
    "pronto". Senão (loja nova, rotina ainda não publicada, dado novo): monta o pronto e o modelo na hora e
    grava — como a tela do Assistente já faz com o pronto. None = a rotina ainda não tirou foto de estoque."""
    params = params or parametros_padrao()
    sig = pronto_mod.assinatura_em_dia(armaz, chaves, empresa, loja, meses_fechados)
    if sig is not None:
        atual = _meta_gravada(armaz, chaves, empresa, loja)
        if atual is not None and atual.get("assinatura") == assinatura(sig, params) and atual.get("versao") == VERSAO:
            return ler(armaz, chaves, empresa, loja)
    p = pronto_mod.atualizar(armaz, chaves, empresa, loja, meses_fechados)
    if p is None:
        return None
    atualizar(armaz, chaves, p, empresa, loja, params)
    return ler(armaz, chaves, empresa, loja)
