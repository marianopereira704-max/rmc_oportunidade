"""As contas das 4 telas de uma loja — Assistente de pedido e Dashboard (Estoque, Ruptura, Compras) — sem Streamlit
nem banco (29/10/2026, tarefa 1 do `docs/specs/2026-10-29-calculo-madrugada-plano.md`).

Moradas antes em `views/pedido/dados_loja.py` (`_calcular`) e `views/dashboard_comum.py` (`_estoque_preparado`,
`_ruptura_preparada`, `_compras_preparadas`), presas aos caches do Streamlit e às consultas ao banco. Aqui recebem
TUDO pronto (armazenamento, bases, configuração, correções): o app chama com o que tem em cache; a rotina da
madrugada (`pedido/precalculo.py`), com as fotos dos insumos publicadas no Spaces — as duas pelas MESMAS contas, para
o resultado pronto sair idêntico ao calculado na hora. Só mudaram de lugar: conferido com a loja fictícia, as 4 telas
iguais às de antes, campo a campo.

Cada função devolve o mesmo objeto que a tela guardava no cache; None = a rotina ainda não tirou foto de estoque.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from pedido import calculo, classificacao
from pedido.configuracao import ConfigPedido

# Quanto do histórico ler: 90 dias da base da venda esperada + o período da perda + folga para o início da falta.
DIAS_HISTORICO = 160


def assistente(armaz, chaves, empresa: str, loja: str, meses: int, params: calculo.Parametros,
               correcoes: dict[str, float], base_categorias: pd.DataFrame,
               genericos: pd.DataFrame) -> tuple[pd.DataFrame, dict] | None:
    """O cálculo do Assistente de pedido: (todas as linhas, meta do pronto)."""
    from pedido import pronto

    p = pronto.atualizar(armaz, chaves, empresa, loja, meses)
    if p is None:
        return None
    linhas = calculo.calcular(p, base_categorias, genericos, params, dict(correcoes))
    return linhas, p.meta


def estoque(armaz, chaves, empresa: str, loja: str, meses: int, config: ConfigPedido, base_categorias: pd.DataFrame,
            tabela_classificacao: pd.DataFrame):
    """O quadro da página Estoque (modelo de madrugada + categoria + linha + parâmetros da loja): (quadro, meta)."""
    from core import dashboard_estoque as de
    from pedido import estoque_modelo

    modelo = estoque_modelo.obter(armaz, chaves, empresa, loja, meses)
    if modelo is None:
        return None
    categorias = base_categorias.drop_duplicates("ean").set_index("ean")["categoria"]
    eans = set(modelo.skus["ean"].dropna()) if not modelo.skus.empty else set()
    # A linha de compra: a categoria fixa da cascata de classificação (25/10/2026), só dos EANs desta loja.
    classes = classificacao.mapa_da_loja(tabela_classificacao, eans) if eans else {}
    caminhos = classificacao.caminhos_da_loja(tabela_classificacao, eans) if eans else None
    return de.preparar(modelo, config, categorias, classes, caminhos), modelo.meta


def caminhos_por_linha(todas: pd.DataFrame, caminhos: pd.DataFrame) -> pd.DataFrame:
    """O caminho na cascata (setor da IQVIA, FEBRAFAR, CMED) de cada produto do Assistente: o do 1º EAN que alguma
    fonte conhece, começando pelo principal (o genérico tem vários EANs)."""
    vazio = {"fonte_setor": None, "fonte_febrafar": None, "fonte_cmed": None}
    saida = {}
    for linha, principal, eans in zip(todas["linha"], todas["ean_principal"], todas["eans"]):
        candidatos = ([principal] if isinstance(principal, str) else []) + list(eans if isinstance(eans, list) else [])
        achado = next((e for e in candidatos if e in caminhos.index), None)
        if achado is None:
            saida[str(linha)] = vazio
        else:
            r = caminhos.loc[achado]
            saida[str(linha)] = {"fonte_setor": r["setor"], "fonte_febrafar": r["febrafar"], "fonte_cmed": r["cmed"]}
    return pd.DataFrame.from_dict(saida, orient="index")


def ruptura(armaz, chaves, empresa: str, loja: str, meses: int, config: ConfigPedido,
            calc: tuple[pd.DataFrame, dict] | None, tabela_classificacao):
    """O quadro da página Ruptura: ((quadro, resumo do histórico), meta). `calc` = o cálculo do Assistente COM as
    correções de estoque negativo (o app passa o do cache dele; a rotina, o que acabou de calcular).
    `tabela_classificacao` pode ser uma função sem argumentos: só é montada se a loja tiver linhas."""
    from core import dashboard_ruptura as dr
    from pedido import estoque_modelo, historico_estoque

    if calc is None:
        return None
    todas, meta = calc
    regras = dr.Regras.da_config(config)
    if todas.empty:
        return dr.preparar(todas, pd.DataFrame(), pd.DataFrame(), [], regras, meta), meta
    modelo = estoque_modelo.obter(armaz, chaves, empresa, loja, meses)
    vendas = (modelo.vendas_dia[["codigo_produto", "data", "unidades"]] if modelo is not None
              else pd.DataFrame(columns=["codigo_produto", "data", "unidades"]))
    # Até o dia seguinte ao último dia de venda do cálculo (a foto da noite é de hoje; as vendas, até ontem): o
    # mesmo resultado do relógio em produção, e fixo no teste visual, que tem as datas de setembro de 2026.
    ate = dt.date.fromisoformat(meta["fim"]) + dt.timedelta(days=1)
    historico, fotos = historico_estoque.ler(armaz, chaves, empresa, loja, ate - dt.timedelta(days=DIAS_HISTORICO), ate)
    tabela = tabela_classificacao() if callable(tabela_classificacao) else tabela_classificacao
    eans = classificacao.eans_da_tabela(todas)
    linha = pd.Series(classificacao.coluna(todas, classificacao.mapa_da_loja(tabela, eans)).to_numpy(),
                      index=todas["linha"].astype(str).to_numpy())
    d, resumo = dr.preparar(todas, vendas, historico, fotos, regras, meta, linha)
    fontes = caminhos_por_linha(todas, classificacao.caminhos_da_loja(tabela, eans))
    d = d.join(fontes)
    return (d, resumo), meta


def info_das_linhas(produtos: pd.DataFrame, todas: pd.DataFrame) -> pd.DataFrame:
    """Uma linha do Assistente por linha: nome, EANs e o que vem do cálculo (custo, estoque ideal, estoque, demanda).
    Produto comprado que nunca vendeu não está no cálculo: nome e EANs vêm do cadastro (`produtos_da_loja`)."""
    com_ean = produtos.dropna(subset=["ean"]).drop_duplicates(["linha", "ean"])
    base = pd.DataFrame({
        "nome": produtos.drop_duplicates("linha").set_index("linha")["nome_linha"],
        "eans": com_ean.groupby("linha")["ean"].agg(list),
        "ean_principal": com_ean.drop_duplicates("linha").set_index("linha")["ean"],
    })
    base["eans"] = [e if isinstance(e, list) else [] for e in base["eans"]]
    if not todas.empty:
        t = todas.set_index(todas["linha"].astype(str))
        base.loc[t.index.intersection(base.index), ["nome", "ean_principal"]] = t[["nome", "ean_principal"]]
        base["eans"] = [t.at[l, "eans"] if l in t.index and isinstance(t.at[l, "eans"], list) else e
                        for l, e in zip(base.index, base["eans"])]
        base["custo"] = pd.to_numeric(t["preco"], errors="coerce").reindex(base.index)
        base["ideal"] = pd.to_numeric(t["estoque_max"], errors="coerce").reindex(base.index)
        base["estoque"] = pd.to_numeric(t["estoque"], errors="coerce").reindex(base.index)
        base["demanda_dia"] = pd.to_numeric(t["demanda_dia"], errors="coerce").reindex(base.index)
    base.index.name = "linha"
    return base


def compras(armaz, chaves, empresa: str, loja: str, meses: int, config: ConfigPedido,
            calc_sem_correcoes, genericos: pd.DataFrame, tabela_classificacao: pd.DataFrame,
            limite_bonificacao: float):
    """Os quadros dos 3 períodos da página Compras: (quadros, períodos, itens, vendas, regras, meta).
    `calc_sem_correcoes` = o cálculo do Assistente SEM as correções de estoque negativo (aqui o estoque só entra na
    sugestão "cerca de N dias"); pode ser uma função sem argumentos — só é chamada se a loja tiver pronto."""
    from core import dashboard_compras as dc
    from pedido import categorias as cat
    from pedido import pronto

    p = pronto.atualizar(armaz, chaves, empresa, loja, meses)
    if p is None:
        return None
    calc = calc_sem_correcoes() if callable(calc_sem_correcoes) else calc_sem_correcoes
    todas = calc[0] if calc is not None else pd.DataFrame()
    est = p.estoque.assign(ean=cat.chaves_ean(p.estoque["ean"]))
    compras_ = p.compras.assign(ean=cat.chaves_ean(p.compras["ean"]))
    produtos = calculo.produtos_da_loja(est, compras_, p.vendas, genericos)
    linha_do_codigo = dict(zip(produtos["codigo_produto"].astype(str), produtos["linha"]))
    regras = dc.Regras.da_config(config, limite_bonificacao)
    itens = dc.itens(p.compras, linha_do_codigo, regras.limite_bonificacao)
    vendas = dc.vendas_por_linha(p.vendas, linha_do_codigo)

    info = info_das_linhas(produtos[produtos["linha"].isin(set(itens["linha"]) | set(vendas["linha"]))], todas)
    linhas_df = info.reset_index()[["linha", "ean_principal", "eans"]]
    eans = classificacao.eans_da_tabela(linhas_df)
    info["categoria"] = classificacao.coluna(linhas_df, classificacao.mapa_da_loja(tabela_classificacao, eans)).to_numpy()
    info = info.join(caminhos_por_linha(linhas_df, classificacao.caminhos_da_loja(tabela_classificacao, eans)))

    fim, inicio = dt.date.fromisoformat(p.meta["fim"]), dt.date.fromisoformat(p.meta["inicio"])
    periodos = dc.periodos(fim, inicio)
    quadros = {k: dc.preparar(itens, vendas, info, per, regras) for k, per in periodos.items()}
    return quadros, periodos, itens, vendas, regras, p.meta
