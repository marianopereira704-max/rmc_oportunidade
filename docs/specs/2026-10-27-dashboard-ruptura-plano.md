# Dashboard · Ruptura — plano de implementação

Design aprovado em 27/10/2026: `2026-10-26-dashboard-ruptura-design.md`. Regras do `CLAUDE.md`: sem commit/push; não
publicar; gravar no Spaces ou no banco real só com prévia e OK; textos em português; dicas pela `core/dicas.py`; suíte
ao fim de cada etapa e lista de arquivos.

## Etapas

**H1 — Histórico do estoque (primeiro, para começar a medir já).** `pedido/historico_estoque.py`: `registrar` (a foto
do dia de uma loja → só o que mudou, num arquivo por mês; cada mês abre com a foto inteira, para ser lido sozinho;
gravar de novo no mesmo dia substitui o dia), `ler` (os meses pedidos + os dias com foto) e a retenção de 13 meses
(apaga só arquivos `historico_estoque/{empresa}/{loja}/AAAA-MM.parquet` mais velhos). Ligado na rotina logo depois da
foto de estoque da empresa; falha no histórico vira falha no relatório e não derruba a foto. Testes com
armazenamento local. **Parada A:** rodar a coleta de estoque hoje daqui da máquina (Spaces real) e conferir o
primeiro dia gravado; decidir como roda toda noite (publicar só a rotina, ou agendar nesta máquina).

**H2 — Contas puras** (`core/dashboard_ruptura.py`, sem tela): períodos de falta por produto do Assistente a partir
do histórico (N da loja; genérico em falta só no dia em que todos os EANs estavam; R9 para dias sem foto), venda
esperada (R1a), perda medida (R8) e projeção (R1b), risco (R2), nível de serviço (R6), investimento (R4), ordem dos
críticos (R12) e o modo da página (transição × medida).

**H3 — Configurações.** Parâmetros novos na seção Dashboard (período da perda, limites do risco e do nível de
serviço), personalizáveis por loja (R13).

**H4 — Dados no app.** Ler o histórico da loja (últimos 4 meses) em cache pela foto do dia; juntar ao cálculo do
Assistente e à classificação de produtos.

**H5 — Tela.** A página do protótipo: cards, ruptura por categoria (tabela com setinha), curva e risco, críticos e o
detalhe. Saem os blocos de estoque.

**H6 — Teste visual e conferência.** Loja fictícia com histórico; capturas novas. **Parada B:** conferir com a loja
60.

**H7 — Fechamento.** Medições, suíte, `CLAUDE.md`, `status_e_pendencias.md`, lista de arquivos.

## Andamento

- **H1** (27/10/2026): `pedido/historico_estoque.py` (registrar, ler, retenção de 13 meses), ligado na rotina depois da
  foto de estoque (falha isolada, vai para o relatório), `Armazenamento.apagar`, chaves novas e o modo `--so-estoque`
  (`python -m pedido --so-estoque --parte i --partes 8`). Ensaio com a empresa da loja 60 (gravando local): 49.077
  produtos, 144 KB no mês, mas 14,5 min (a API do GPS falhou em 6 min e respondeu em 8,5 min na 2ª tentativa).
  Primeira coleta real: agendada pelo Mariano para 00:05 de 28/10 (script `C:\Users\RMC - 0014\rmc_rotina\coleta_estoque.ps1`).
- **H2** (27/10/2026): `core/dashboard_ruptura.py` (estoque por dia e períodos de falta pelo histórico, venda esperada,
  perda medida, projeção, modo transição × medida, risco, investimento, nível de serviço, agregações e críticos) e a
  coluna `codigos` no cálculo do Assistente. 15 testes.
- **H3** (27/10/2026): `ConfigPedido.ruptura_perda_dias`, `ruptura_risco_alto`/`_medio`, `ruptura_sla_vermelho`/`_verde`
  na seção Dashboard das Configurações (personalizáveis por loja, validados). Q15: limites do risco trocados para
  14% (1 vez por semana) e 3% (1 vez por mês) — na loja 60 o produto típico vende em 2% dos dias e, com 50%/20%,
  todos os 901 em ruptura saíam "baixo". A frequência do risco usa a mesma base da venda esperada (sem os dias de
  falta conhecida).
- **H4** (27/10/2026): `views/dashboard_comum.carregar_ruptura` + `_ruptura_preparada` (cache por loja, foto,
  versões, config e correções): cálculo do Assistente, vendas por dia do modelo de estoque, histórico (até o dia
  seguinte ao último dia de venda — fixo no teste visual), classificação e caminho da cascata por produto. Loja 60,
  27/10 (sem histórico ainda, projeção): 4.889 produtos, 901 em ruptura (18,4%), venda em risco R$ 1.190/dia,
  projeção R$ 35,7 mil em 30 dias, investimento R$ 37,4 mil (1.084 un.). 1ª abertura com cache frio: 32 s
  (classificação, cálculo e modelo de estoque — os três já em cache no uso normal). Saiu a carga da Ruptura antiga.
- **H5** (27/10/2026): `views/dashboard_ruptura.py` reescrita como o protótipo (cards, ruptura por categoria com
  setinha, curva e risco, críticos, detalhe com diagnóstico e sugestão); `.txt-verde` na tabela e
  `.rmc-badge-perigo` no tema.
- **H6** (27/10/2026): loja fictícia com histórico (fotos de 20 a 27/09 sem o dia 23); roteiro visual sem os chips
  (barra marcada por `aria-pressed`) e com a captura `dashboard_ruptura_detalhamento`. Corrigidos pelo teste visual:
  "R$ nan" no diagnóstico de produto sem preço de venda e a "Última compra" sem data.
