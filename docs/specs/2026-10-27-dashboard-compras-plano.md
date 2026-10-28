# Dashboard · Compras — plano de implementação

Design aprovado em 27/10/2026: `2026-10-27-dashboard-compras-design.md`. Regras do `CLAUDE.md`: sem commit/push; não
publicar; textos em português; dicas pela `core/dicas.py`; suíte ao fim de cada etapa e lista de arquivos.

## Etapas

**K1 — Contas puras** (`core/dashboard_compras.py`, sem tela): compras e vendas por linha do Assistente (inclusive
produto comprado que nunca vendeu), períodos (R4), sem demanda (R6), limite e excesso com a bonificação primeiro
(R7), compra certa e eficiência (R8), venda a custo e Compra × Venda (R5), dotação por categoria e a curva do mês
(R9, R11), agregações por categoria e por caminho da cascata, produtos não otimizados, diagnóstico e sugestão (R14).
Testes com quadros pequenos montados à mão.

**K2 — Configurações.** `compras_dotacao_dia_inicio` e `compras_sem_demanda_dias` na seção Dashboard,
personalizáveis por loja, validados (R13).

**K3 — Dados no app.** `views/dashboard_comum.carregar_compras`: o pronto da loja, o cálculo do Assistente, a
classificação e o caminho da cascata, em cache por loja, foto, versões e config.

**K4 — Tela.** `views/dashboard_compras.py` como o protótipo: pílulas de período, cards, compras por categoria com
setinha, distribuição e dotação × consumido, produtos e o detalhe.

**K5 — Teste visual e conferência.** Compras na loja fictícia (com bonificação, excesso e sem demanda); capturas
`dashboard_compras*`. **Parada:** conferir com a loja 60.

**K6 — Fechamento.** Medições, suíte, `CLAUDE.md`, `status_e_pendencias.md`, lista de arquivos.

## Andamento

- **K1** (27/10/2026): `core/dashboard_compras.py` (itens, vendas por linha, períodos, quadro com sem demanda, limite,
  excesso com as bonificadas primeiro, compra certa, venda a custo e a dotação por mês; métricas, por categoria,
  cascata, distribuição, curva do mês, por mês, produtos, notas, diagnóstico e sugestão). `calculo.produtos_da_loja`
  separado do `calcular` (a linha do pedido de TODO produto, inclusive o que só foi comprado). 9 testes.
- **K2** (27/10/2026): `compras_dotacao_dia_inicio` (3) e `compras_sem_demanda_dias` (90) na seção Dashboard, com
  validação e dica.
- **K3** (27/10/2026): `views/dashboard_comum.carregar_compras` + `_compras_preparadas` (pronto, cálculo do
  Assistente, classificação e caminho da cascata; os 3 períodos de uma vez, em cache). Loja 60 (banco dev, foto de
  21/10, janela de 01/07 a 20/10): carga com cache frio 31 s; 7.343 itens de nota. Mês atual: dotação R$ 150,0 mil,
  consumido R$ 58,2 mil (38,8%), Compra × Venda 68,2%, não otimizado R$ 23,4 mil (40,3%), eficiência 59,7%, 383
  produtos. Mês passado: dotação R$ 182,8 mil, consumido R$ 219,1 mil (119,8%), Compra × Venda 124,0%, eficiência
  63,3%. **Achado:** a venda de outubro da loja 60 tem dias muito baixos (R$ 893 em 05/10, R$ 1.223 em 06/10, contra
  R$ 8–20 mil nos outros dias) — a variação da venda de todas as categorias sai de −22% a −50% e a dotação cai junto.
  Conferir se esses dias vieram incompletos da API.
- **K4** (27/10/2026): `views/dashboard_compras.py` como o protótipo (pílulas de período com a legenda das notas,
  cards com a barra de progresso — `theme.kpis` aceita `progresso` —, compras por categoria com setinha, distribuição
  em barras 100% (o componente de barras clicáveis, na vertical), Dotação × Consumido no plotly (linhas no mês, barras
  agrupadas nos 3 meses), produtos e detalhe com as notas). Eficiência colorida: vermelho abaixo de 80%, verde a partir
  de 90% (os números do protótipo). 6 testes.
- **K5** (27/10/2026): loja fictícia com compras de julho (base da dotação de agosto), um excesso e um sem demanda em
  setembro; capturas `dashboard_compras`, `_produtos`, `_detalhe` e `_tres_meses`.
- **Conferência da loja 60 → regra da dotação revista** (28/10/2026, Q18–Q21; design R9/R9a): variação por semana
  completa, sem os dias fora da conta (abaixo de 40% da mediana do mesmo dia da semana nas 8 semanas anteriores).
  `dias_fora`, `_pesos`, `_ponderado`; saiu `compras_dotacao_dia_inicio`, entraram `compras_dia_fora_pct` e
  `compras_dia_fora_semanas`. Loja 60: dotação de outubro (até 20/10) R$ 150,0 → 164,3 mil (35,4% consumido);
  setembro R$ 182,8 → 186,7 mil (117,4%); 3 meses R$ 498,0 → 518,0 mil. Dias fora: 07/09, 06/10, 07/10, 12/10, 20/10.
  4 testes novos.
