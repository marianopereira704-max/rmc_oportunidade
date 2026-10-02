"""Loja FICTÍCIA pro teste visual da tela Pedido (29/09/2026): o `data.seed`
não tem loja com dados do GPS, e sem loja o botão Filtros fica desabilitado —
o teste de contrato não conseguia abrir o pop-up de filtros.

Cria, no banco e no armazenamento local DO TESTE (DATABASE_URL e
LOCAL_STORAGE_DIR do ambiente — nunca produção):
- uma loja "LOJA FICTÍCIA DE TESTE" ligada a uma loja do GPS inventada;
- 20 produtos inventados, com vendas de junho a setembro/2026, compras e
  estoque escolhidos pra cada pílula ter número: zerados (Ruptura),
  estoque baixo (Ruptura próxima), um negativo, uma bonificação depois da
  última compra, uma compra fora do padrão (a revisar), produtos sem
  compra e sem categoria;
- uma personalização dos parâmetros (medicamento 10 dias), pra tela mostrar
  as pílulas "Personalizado desta loja" / "Padrão" (01/10/2026).

Data fixa (foto de estoque de 27/09/2026): as capturas não mudam com o dia.

    python -m tests.visual.loja_ficticia   # chamado por tests/visual/rodar.py
"""
from __future__ import annotations

import datetime as dt
import os

EMPRESA = "EMPRESA-TESTE"
LOJA_GPS = "1"
FOTO = dt.date(2026, 9, 27)
NOME = "LOJA FICTÍCIA DE TESTE"


def _produtos() -> list[dict]:
    saida = []
    for i in range(20):
        saida.append({
            "codigo": str(100 + i),
            "ean": f"78900000{i:05d}",
            "nome": f"PRODUTO TESTE {i + 1:02d}",
            "lab": "LAB TESTE A" if i % 2 else "LAB TESTE B",
            # ~1 un./dia nos meses fechados; os pares vendem também em setembro.
            "venda_mes": 25 + i,
            "vende_setembro": i % 2 == 0,
            "estoque": -1 if i == 2 else (0 if i % 5 == 0 else (2 if i % 5 == 1 else 3)),
            "custo": 10.0 + i,
        })
    return saida


def main() -> None:
    assert os.environ.get("RMC_IGNORAR_SECRETS") == "1", "só roda no ambiente do teste visual"
    from core.db import get_session, init_db
    from core.models import (CategoriaEan, Loja, OrigemCategoria, PersonalizacaoPedidoLoja, SituacaoVinculoGps,
                             VinculoLojaGps)
    from pedido import transformar
    from pedido.armazenamento import ArmazenamentoLocal
    from pedido.plano import COMPRAS, VENDAS, Chaves
    from core.config import settings

    url = settings.db.url
    assert url.startswith("sqlite"), f"loja fictícia só em SQLite de teste, não em {url[:12]}…"
    init_db()
    armaz = ArmazenamentoLocal(settings.local_storage_dir)
    ch = Chaves(settings.pedido.prefixo)
    produtos = _produtos()

    with get_session() as s:
        loja = Loja(cnpj="00000000000191", razao_social=NOME, uf="MG", cidade="CIDADE TESTE", legacy_id=9999,
                    atualizado_em=dt.datetime(2026, 9, 27))
        s.add(loja)
        s.flush()
        s.add(VinculoLojaGps(id_empresa_gps=EMPRESA, codigo_loja_gps=LOJA_GPS, nome_loja_gps=NOME, loja_id=loja.id,
                             situacao=SituacaoVinculoGps.AUTOMATICO, metodo="cnpj", atualizado_em=dt.datetime(2026, 9, 27)))
        s.add(PersonalizacaoPedidoLoja(loja_id=loja.id, valores='{"dias_medicamento": 10}', criado_por="teste visual",
                                       criado_em=dt.datetime(2026, 9, 27)))
        # Metade com categoria (MIP/OTC e Higiene), o resto "Sem Classificação".
        for i, p in enumerate(produtos[:14]):
            s.add(CategoriaEan(ean=p["ean"].lstrip("0"), categoria="MIP/OTC" if i < 10 else "HIGIENE",
                               origem=OrigemCategoria.FEBRAFAR, atualizado_em=dt.datetime(2026, 9, 27)))

    # Vendas: um arquivo por mês fechado + um por dia de setembro (1 a 26).
    for mes in ("2026-06", "2026-07", "2026-08"):
        armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, VENDAS, mes), transformar.vendas([
            {"DataVenda": f"{mes}-15", "CodigoLoja": LOJA_GPS, "CodigoProduto": p["codigo"],
             "Quantidade": p["venda_mes"], "VlrLiquido": p["venda_mes"] * p["custo"] * 1.6, "TipoVenda": "Venda"}
            for p in produtos]))
    dia = dt.date(2026, 9, 1)
    while dia < FOTO:
        armaz.salvar_df(ch.dia(EMPRESA, LOJA_GPS, VENDAS, dia), transformar.vendas([
            {"DataVenda": dia.isoformat(), "CodigoLoja": LOJA_GPS, "CodigoProduto": p["codigo"], "Quantidade": 1,
             "VlrLiquido": p["custo"] * 1.6, "TipoVenda": "Venda"}
            for p in produtos if p["vende_setembro"]]))
        dia += dt.timedelta(days=1)

    # Compras: agosto pra quem não é múltiplo de 3; o nº 5 (i=4) tem uma
    # bonificação DEPOIS; o nº 8 (i=7) tem a última compra 10× acima (a revisar).
    def compra(p, data, preco, qtd=12):
        return {"TipoCompra": "0", "CodigoLoja": LOJA_GPS, "CodigoProduto": p["codigo"],
                "ProdutoRelacionado_CodigoBarras": p["ean"], "DataEmissaoNF": data, "Quantidade": qtd, "Fracao": 1,
                "VlrUnitario": preco, "VlrDesconto": 0, "NomeRazaoSocialFornecedor": "DISTRIBUIDORA TESTE",
                "ProdutoRelacionado_NomeLaboratorio": p["lab"]}

    agosto = [compra(p, "2026-08-10", p["custo"]) for i, p in enumerate(produtos) if i % 3]
    agosto += [compra(produtos[7], "2026-08-20", produtos[7]["custo"] * 1.02)]
    armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, COMPRAS, "2026-08"), transformar.compras(agosto))
    for mes in ("2026-06", "2026-07"):
        armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, COMPRAS, mes), transformar.compras([]))
    setembro = {dt.date(2026, 9, 5): [compra(produtos[4], "2026-09-05", 0.01, 2)],
                dt.date(2026, 9, 8): [compra(produtos[7], "2026-09-08", produtos[7]["custo"] * 10)]}
    dia = dt.date(2026, 9, 1)
    while dia < FOTO:
        armaz.salvar_df(ch.dia(EMPRESA, LOJA_GPS, COMPRAS, dia), transformar.compras(setembro.get(dia, [])))
        dia += dt.timedelta(days=1)

    armaz.salvar_df(ch.estoque(EMPRESA, LOJA_GPS), transformar.estoque([
        {"CodigoLoja": LOJA_GPS, "CodigoProduto": p["codigo"], "ProdutoRelacionado_CodigoBarras": p["ean"],
         "NomeProduto": p["nome"], "ProdutoRelacionado_NomeLaboratorio": p["lab"], "QtdEstoque": p["estoque"],
         "VlrPrecoCompra": p["custo"]}
        for p in produtos]))
    armaz.salvar_json(ch.marcador_estoque(EMPRESA), {"data": FOTO.isoformat()})
    print(f"Loja fictícia criada: {NOME} ({len(produtos)} produtos).")


if __name__ == "__main__":
    main()
