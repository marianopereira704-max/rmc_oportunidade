"""Compras da REDE por mês fechado — o arquivo que a Análise de Oportunidade
lê (decisões de 05/10/2026, quando a Análise trocou a planilha do BI pela
API do GPS).

Por que um arquivo da rede: a Análise junta TODAS as lojas (Por Loja, Por
Produto com "Todas"); ler os ~6 arquivos de compras de cada uma das ~370
lojas a cada consulta seria lento. A rotina, que já tem os arquivos por
loja, junta tudo num arquivo por mês — mês fechado não muda, então é
montado uma vez (e de novo só se algum arquivo de loja mudar).

    analise/compras/2026-09.parquet       uma linha por item de nota, todas as lojas
    analise/compras/2026-09_eans.parquet  uma linha por EAN: valor comprado, compras, lojas
    analise/compras/2026-09.json          manifesto (que arquivos de loja entraram) e data

O resumo por EAN é o que a fila de EAN lê no app (integrations/fila_compras.py):
carregar as compras inteiras da rede (~550 mil linhas por mês) só pra somar
por EAN pesaria no Streamlit Cloud.

Volume medido em 05/10/2026: 1,2 a 2 mil itens por loja por mês (todos os
produtos) → ~550 mil linhas e ~14 MB por mês na rede. Por isso o arquivo
sai ORDENADO POR EAN, em blocos de `LINHAS_POR_GRUPO`: o app lê só os EANs
da tabela Gruppy escolhida e pula os blocos que não têm nenhum deles — sem
isso, carregar 3 meses inteiros (~1,6 mi de linhas, ~300 MB) derrubaria o
app no Streamlit Cloud.

Cada linha leva também o custo de cadastro e o preço de venda do produto na
loja, da foto de estoque do dia em que o arquivo é montado: as regras de
preço da Análise (cadastro × nota, acima do preço de venda) precisam deles,
e a API não guarda histórico do cadastro (Q19 de 05/10/2026 — para erro de
fração, que é de 10× ou mais, a foto de hoje basta).

O mês só aparece depois do dia 6 do mês seguinte (`plano.dia_definitivo`):
até lá a rotina ainda baixa o mês de novo (notas lançadas com atraso), e o
número não deve mudar depois de alguém ver.

    python -m pedido.rede                 # monta o que falta (GitHub Actions, depois da coleta)
    python -m pedido.rede --hoje 2026-10-06
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

from pedido import categorias, plano, transformar
from pedido.armazenamento import Armazenamento, do_ambiente
from pedido.plano import COMPRAS, Chaves

COLUNAS = [
    "empresa", "codigo_loja", "data", "nro_nf", "fornecedor", "laboratorio", "codigo_produto", "ean", "nome",
    "quantidade", "preco", "vlr_unitario", "vlr_desconto", "fracao", "custo_cadastro", "preco_venda",
]
LINHAS_POR_GRUPO = 20_000
_LEITURAS_PARALELAS = 16
_COLUNAS_ESTOQUE = ["codigo_produto", "nome", "vlr_preco_compra", "vlr_preco_venda"]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def meses_visiveis(hoje: dt.date, meses: int = plano.COMPRAS_MESES_FECHADOS) -> list[str]:
    """Meses fechados da janela de compras que já podem aparecer (a partir do
    dia 6 do mês seguinte), do mais antigo ao mais recente."""
    return [p.mes for p in plano.periodos(hoje, meses) if p.fechado and plano.dia_definitivo(p.mes) <= hoje]


def _arquivos_do_mes(chaves: Chaves, todas: list[str], mes: str) -> list[tuple[str, str, str]]:
    """(empresa, loja, chave) dos arquivos de compras do mês, por loja."""
    sufixo = f"/{COMPRAS}/{mes}.parquet"
    saida = []
    for k in todas:
        if k.endswith(sufixo):
            partes = k[len(f"{chaves.prefixo}/bruto/"):].split("/")
            saida.append((partes[0], partes[1], k))
    return sorted(saida)


def _manifesto(arquivos: list[tuple[str, str, str]], baixados: dict[str, dict], mes: str) -> dict[str, str]:
    """"empresa/loja" → dia da última baixa do mês. Muda quando entra loja
    nova ou quando a rotina baixa o mês de novo — aí o arquivo é refeito."""
    return {f"{e}/{l}": baixados.get(e, {}).get(mes, "") for e, l, _ in arquivos}


def preparar(compras: pd.DataFrame, estoque: pd.DataFrame, empresa: str) -> pd.DataFrame:
    """Compras de uma loja → linhas do arquivo da rede: quantidade em
    unidades e preço por unidade sem ST (mesma conta do Assistente —
    docs/mapa_api_gps.md §7 e §7a), EAN como chave, cadastro e preço de
    venda da foto de estoque."""
    if compras.empty:
        return pd.DataFrame(columns=COLUNAS)
    fracao = compras["fracao"].where(compras["fracao"] > 0, 1).fillna(1)
    est = estoque.drop_duplicates("codigo_produto").set_index("codigo_produto")
    nome = compras["nome"] if "nome" in compras.columns else pd.Series(pd.NA, index=compras.index, dtype="string")
    df = pd.DataFrame({
        "empresa": empresa,
        "codigo_loja": compras["codigo_loja"],
        "data": compras["data"],
        "nro_nf": compras["nro_nf"],
        "fornecedor": compras["fornecedor"],
        "laboratorio": compras["laboratorio"],
        "codigo_produto": compras["codigo_produto"],
        "ean": categorias.chaves_ean(compras["ean"]),
        # Arquivos baixados antes de 05/10/2026 não têm o nome: vem do cadastro.
        "nome": nome.fillna(compras["codigo_produto"].map(est["nome"]) if "nome" in est else pd.NA),
        "quantidade": compras["quantidade"].fillna(0) * fracao,
        "preco": compras["vlr_unitario"].fillna(0) * (1 - compras["vlr_desconto"].fillna(0) / 100) / fracao,
        "vlr_unitario": compras["vlr_unitario"],
        "vlr_desconto": compras["vlr_desconto"],
        "fracao": fracao,
        "custo_cadastro": pd.to_numeric(compras["codigo_produto"].map(est["vlr_preco_compra"]), errors="coerce")
        if "vlr_preco_compra" in est else float("nan"),
        "preco_venda": pd.to_numeric(compras["codigo_produto"].map(est["vlr_preco_venda"]), errors="coerce")
        if "vlr_preco_venda" in est else float("nan"),
    })
    return df[COLUNAS]


def _ler_estoque(armaz: Armazenamento, chaves: Chaves, empresa: str, loja: str, existentes: set[str]) -> pd.DataFrame:
    chave = chaves.estoque(empresa, loja)
    if chave not in existentes:
        return transformar._quadro([], _COLUNAS_ESTOQUE + ["ean"])[_COLUNAS_ESTOQUE]
    return armaz.ler_df(chave, colunas=_COLUNAS_ESTOQUE)


def montar_mes(armaz: Armazenamento, chaves: Chaves, mes: str, arquivos: list[tuple[str, str, str]],
               existentes: set[str], estoques: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    """Junta os arquivos de compras do mês de todas as lojas. `estoques`:
    cache (empresa, loja) → foto, reaproveitado entre os meses."""
    faltam = [(e, l) for e, l, _ in arquivos if (e, l) not in estoques]
    with ThreadPoolExecutor(max_workers=_LEITURAS_PARALELAS) as ex:
        lidos_est = list(ex.map(lambda el: _ler_estoque(armaz, chaves, el[0], el[1], existentes), faltam))
        estoques.update(zip(faltam, lidos_est))
        compras = list(ex.map(lambda a: armaz.ler_df(a[2]), arquivos))
    partes = [preparar(c, estoques[(e, l)], e) for (e, l, _), c in zip(arquivos, compras) if not c.empty]
    if not partes:
        return pd.DataFrame(columns=COLUNAS)
    df = pd.concat(partes, ignore_index=True)
    for c in COLUNAS:
        if c in ("quantidade", "preco", "vlr_unitario", "vlr_desconto", "fracao", "custo_cadastro", "preco_venda"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        else:
            df[c] = df[c].astype("string")
    return df.sort_values(["ean", "empresa", "codigo_loja", "data"], kind="stable").reset_index(drop=True)


def resumo_por_ean(df: pd.DataFrame, limite_bonificacao: float = 0.10) -> pd.DataFrame:
    """ean, nome, valor (quantidade × preço, sem bonificação), compras (itens
    de nota) e lojas (distintas)."""
    pagas = df[pd.to_numeric(df["preco"], errors="coerce") >= limite_bonificacao]
    if pagas.empty:
        return pd.DataFrame({"ean": pd.Series(dtype="string"), "nome": pd.Series(dtype="string"),
                             "valor": pd.Series(dtype="float64"), "compras": pd.Series(dtype="int64"),
                             "lojas": pd.Series(dtype="int64")})
    pagas = pagas.assign(_valor=pagas["quantidade"] * pagas["preco"], _loja=pagas["empresa"] + "/" + pagas["codigo_loja"])
    return (pagas.dropna(subset=["ean"]).groupby("ean", as_index=False)
            .agg(nome=("nome", "first"), valor=("_valor", "sum"), compras=("ean", "size"), lojas=("_loja", "nunique")))


def atualizar(armaz: Armazenamento, chaves: Chaves, hoje: dt.date,
              meses: int = plano.COMPRAS_MESES_FECHADOS) -> list[dict]:
    """Monta (ou refaz) o arquivo de cada mês visível cujo manifesto mudou.
    Devolve um resumo por mês (só números — o log do GitHub é público)."""
    todas = armaz.listar(f"{chaves.prefixo}/bruto/")
    existentes = set(todas)
    empresas = sorted({k.split("/")[2] for k in todas if k.count("/") >= 3})
    baixados = {}
    for e in empresas:
        k = chaves.marcador_compras(e)
        if k in existentes:
            baixados[e] = armaz.ler_json(k).get("meses", {})
    ja_montados = set(armaz.listar(chaves.prefixo_compras_rede()))

    estoques: dict[tuple[str, str], pd.DataFrame] = {}
    resumo = []
    for mes in meses_visiveis(hoje, meses):
        arquivos = _arquivos_do_mes(chaves, todas, mes)
        manifesto = _manifesto(arquivos, baixados, mes)
        k_meta = chaves.compras_rede_meta(mes)
        if (k_meta in ja_montados and chaves.compras_rede_resumo(mes) in ja_montados
                and armaz.ler_json(k_meta).get("manifesto") == manifesto):
            resumo.append({"mes": mes, "situacao": "em dia", "lojas": len(arquivos)})
            continue
        comeco = time.monotonic()
        df = montar_mes(armaz, chaves, mes, arquivos, existentes, estoques)
        armaz.salvar_df(chaves.compras_rede(mes), df, linhas_por_grupo=LINHAS_POR_GRUPO)
        armaz.salvar_df(chaves.compras_rede_resumo(mes), resumo_por_ean(df))
        # O manifesto vai por último: sem ele, a próxima execução monta de novo.
        armaz.salvar_json(k_meta, {"mes": mes, "manifesto": manifesto, "linhas": len(df), "lojas": len(arquivos),
                                   "gerado_em": dt.datetime.now().isoformat(timespec="seconds")})
        segundos = round(time.monotonic() - comeco, 1)
        resumo.append({"mes": mes, "situacao": "montado", "lojas": len(arquivos), "linhas": len(df), "segundos": segundos})
        _log(f"{mes}: {len(arquivos)} loja(s), {len(df)} linha(s), {segundos}s")
    return resumo


def main(argv: list[str] | None = None) -> int:
    from core.config import settings

    p = argparse.ArgumentParser(description="Compras da rede por mês (Análise de Oportunidade).")
    p.add_argument("--hoje", help="AAAA-MM-DD (padrão: hoje no fuso do Brasil)")
    a = p.parse_args(argv)
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()
    resumo = atualizar(do_ambiente(), Chaves(settings.pedido.prefixo), hoje)
    for r in resumo:
        if r["situacao"] == "em dia":
            _log(f"{r['mes']}: em dia ({r['lojas']} loja(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
