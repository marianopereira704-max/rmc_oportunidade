"""Loja FICTÍCIA pro teste visual da tela Pedido (29/09/2026): o `data.seed`
não tem loja com dados do GPS, e sem loja o botão Filtros fica desabilitado —
o teste de contrato não conseguia abrir o pop-up de filtros.

Cria, no banco e no armazenamento local DO TESTE (DATABASE_URL e
LOCAL_STORAGE_DIR do ambiente — nunca produção):
- uma loja "LOJA FICTÍCIA DE TESTE" ligada a uma loja do GPS inventada;
- 22 produtos inventados (os 2 últimos, de 21/10/2026, são o excesso e o
  parado do Dashboard), com vendas de junho a setembro/2026, compras e
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

import pandas as pd
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
    # Dashboard (21/10/2026): um produto em EXCESSO (estoque para ~400 dias de
    # venda) e um PARADO (só vendeu em junho: nada nos últimos 90 dias). Os dois
    # ficam acima do ideal do Assistente, então não entram na lista dele; o
    # custo é baixo, para a curva ABC dos demais produtos não mudar.
    saida.append({"codigo": "120", "ean": "7890000009920", "nome": "TESTE EXCESSO", "lab": "LAB TESTE A",
                  "venda_mes": 3, "vende_setembro": False, "estoque": 40, "custo": 2.0})
    saida.append({"codigo": "121", "ean": "7890000009921", "nome": "TESTE PARADO", "lab": "LAB TESTE B",
                  "venda_mes": 5, "vende_setembro": False, "estoque": 25, "custo": 3.0, "meses": ("2026-06",)})
    return saida


def main() -> None:
    assert os.environ.get("RMC_IGNORAR_SECRETS") == "1", "só roda no ambiente do teste visual"
    from core.db import get_session, init_db
    from core.models import (CatalogoIqvia, CategoriaEan, ClassificacaoEan, Loja, OrigemCategoria,
                             PersonalizacaoPedidoLoja, SituacaoVinculoGps, VinculoLojaGps)
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
        # Aba Cadastro (07/10/2026): um usuário "Ativo" e um "Aguardando".
        from core.models import Papel, Usuario
        from core.security import hash_senha

        s.add_all([
            Usuario(login="ana.compras@ficticia.com.br", senha_hash=hash_senha("senha-teste-1"), papel=Papel.CONSULTOR,
                    nome_exibicao="Ana Lima", cargo="Compradora", nivel="COMPRADOR",
                    linhas_compra="genericos,similares", paineis="pedido", loja_principal_id=loja.id,
                    criado_em=dt.datetime(2026, 9, 27), criado_por="teste visual"),
            Usuario(login="carlos@ficticia.com.br", senha_hash="", papel=Papel.CONSULTOR, nome_exibicao="Carlos Souza",
                    cargo="Proprietário", telefone="(31) 99999-0000", nivel="PROPRIETARIO",
                    paineis="oportunidade_produto,pedido", loja_principal_id=loja.id,
                    criado_em=dt.datetime(2026, 9, 27), criado_por="teste visual"),
        ])
        # Metade com categoria (MIP/OTC e Higiene), o resto "Sem Classificação".
        for i, p in enumerate(produtos[:14]):
            s.add(CategoriaEan(ean=p["ean"].lstrip("0"), categoria="MIP/OTC" if i < 10 else "HIGIENE",
                               origem=OrigemCategoria.FEBRAFAR, atualizado_em=dt.datetime(2026, 9, 27)))
        # Cascata de classificação (25/10/2026): IQVIA para os 12 primeiros (genérico, propagado, similar e
        # perfumaria, em rodízio), CMED para 3 que a IQVIA não conhece (Genérico e Similar classificam, Novo não) e a
        # FEBRAFAR igual à da base. O resto fica "Em classificação".
        quando = dt.datetime(2026, 9, 27)
        setores = ("RX_GENERICO", "RX_PROMOVIDO", "RX_TRADE", "NAO_MEDICAMENTO_PEC")
        for i, p in enumerate(produtos[:12]):
            s.add(CatalogoIqvia(ean=p["ean"].lstrip("0"), setor_nec=setores[i % 4], gmrs=None, atualizado_em=quando))
        for i, p in enumerate(produtos[:16]):
            febrafar = "MIP/OTC" if i < 10 else ("HIGIENE" if i < 14 else None)
            cmed = {12: "Genérico", 13: "Similar", 14: "Novo"}.get(i)
            if febrafar or cmed:
                s.add(ClassificacaoEan(ean=p["ean"].lstrip("0"), febrafar=febrafar, cmed_tipo=cmed, atualizado_em=quando))

    # Vendas: um arquivo por mês fechado + um por dia de setembro (1 a 26).
    for mes in ("2026-06", "2026-07", "2026-08"):
        armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, VENDAS, mes), transformar.vendas([
            {"DataVenda": f"{mes}-15", "CodigoLoja": LOJA_GPS, "CodigoProduto": p["codigo"],
             "Quantidade": p["venda_mes"], "VlrLiquido": p["venda_mes"] * p["custo"] * 1.6, "TipoVenda": "Venda"}
            for p in produtos if mes in p.get("meses", ("2026-06", "2026-07", "2026-08"))]))
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
    # Página Compras (27/10/2026): julho é a base da dotação de agosto — as mesmas compras de agosto, um mês antes
    # (mais antigas que a de agosto, não mudam a "última compra" do Assistente nem a mediana dos preços). Em setembro,
    # um EXCESSO (o "TESTE EXCESSO", que o Assistente não lista) e um SEM DEMANDA: um produto que só existe na
    # nota (não está no estoque nem vendeu), então não aparece em nenhuma outra tela.
    julho = [compra(p, "2026-07-10", p["custo"]) for i, p in enumerate(produtos) if i % 3]
    armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, COMPRAS, "2026-07"), transformar.compras(julho))
    armaz.salvar_df(ch.mes(EMPRESA, LOJA_GPS, COMPRAS, "2026-06"), transformar.compras([]))
    sem_demanda = {"codigo": "130", "ean": "7890000009930", "lab": "LAB TESTE A"}
    nota_sem_demanda = {**compra(sem_demanda, "2026-09-12", 8.0, 10), "ProdutoRelacionado_NomeProduto": "TESTE SEM DEMANDA"}
    setembro = {dt.date(2026, 9, 5): [compra(produtos[4], "2026-09-05", 0.01, 2)],
                dt.date(2026, 9, 8): [compra(produtos[7], "2026-09-08", produtos[7]["custo"] * 10)],
                dt.date(2026, 9, 12): [nota_sem_demanda],
                dt.date(2026, 9, 15): [compra(produtos[20], "2026-09-15", produtos[20]["custo"], 60)]}
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
    # Histórico do estoque (Ruptura, 27/10/2026): fotos de 20 a 27/09, SEM a do dia 23. Quem está zerado hoje zerou
    # no dia 24 (o 1º produto já estava zerado desde o dia 20): faltas medidas de 4 e 8 dias, uma com dia sem foto.
    from pedido import historico_estoque
    for dia in range(20, 28):
        if dia == 23:
            continue
        data = dt.date(2026, 9, dia)
        foto = []
        for i, p in enumerate(produtos):
            zerado = p["estoque"] <= 0 and (i == 0 or dia >= 24)
            foto.append({"codigo_produto": p["codigo"], "qtd_estoque": 0 if zerado else max(p["estoque"], 0) + 3})
        historico_estoque.registrar(armaz, ch, EMPRESA, LOJA_GPS, pd.DataFrame(foto), data)
    print(f"Loja fictícia criada: {NOME} ({len(produtos)} produtos).")


if __name__ == "__main__":
    main()
