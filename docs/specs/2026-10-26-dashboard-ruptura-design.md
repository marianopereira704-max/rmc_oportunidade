# Dashboard · Ruptura — design

Rodadas de perguntas de 26 e 27/10/2026. Protótipo visual aprovado em 26/10/2026 (artifact privado "Protótipo
Dashboard Ruptura", https://claude.ai/artifact/Ne6MspKZqQrkHpeqg7vRYS).

## 1. O que é e para quê

A página Ruptura passa a falar só do que FALTA na loja e de quanto isso custa: quantos produtos estão sem estoque,
quanto se deixa de vender por dia, quanto já se perdeu (medido, não estimado) e quanto custa repor. O estoque
(excesso, parado) fica só na página Estoque.

## 2. Estrutura (a do protótipo)

1. **Cards**: Ruptura | Venda em risco por dia | **Perda** (novo) | **Investimento para repor** (novo).
2. **Ruptura por categoria**: as 5 categorias da classificação de produtos (Propagado, Genérico, Similar,
   Perfumaria, Em classificação), com a setinha IQVIA › FEBRAFAR › CMED, igual à tabela do Estoque. Colunas:
   Produtos | Em ruptura | Nível de serviço | Perda | Investimento necessário (as duas últimas com barra na célula).
3. Lado a lado: **Ruptura por curva** (itens e % da curva) e **Perda por risco** (Alto, Médio, Baixo), em barras.
4. **Produtos críticos**: busca, filtro Em ruptura / Ruptura próxima e filtro por risco. Colunas: Produto (EAN e
   categoria embaixo) | Situação | Curva | Última venda | Última compra | Vendidas 90 dias | Perda | Risco | Repor
   (unidades e R$). 25 por página, ordenação pelo título.
5. **Detalhe do produto** (pop-up no padrão do Estoque): cabeçalho com nome, categoria | EAN e pílulas (curva,
   situação, risco); 6 cards (Estoque atual, Venda por dia, Dias sem estoque, Perda, Última venda, Última compra com
   fornecedor e custo); Diagnóstico e Sugestão de ação (comprar N un. e R$ pela sugestão do Assistente; fornecedor e
   custo da última compra; conferir se não há pedido a caminho).
6. **Saem**: os cards de excesso e parado e o gráfico "Estoque por linha de compra" (estão no Estoque).

## 3. Regras

| # | Decisão |
|---|---|
| Já valia | "Em ruptura" = estoque de até N unidades (Configurações, padrão 0). Giro baixo conta na ruptura ("todos contam"). |
| R5 | **Um item = um produto do Assistente**: o genérico com vários EANs (Base Genéricos) é um produto só e só está em ruptura se a soma do estoque dos EANs zerou. A coluna se chama "Produtos". |
| R6 | **Nível de serviço** = 1 − (em ruptura ÷ produtos do mix), na base do "% do mix" do card Ruptura (todos os produtos do cálculo). Por categoria, a base é a da categoria. Ponto vermelho abaixo de 65%, âmbar de 65% a 75%, verde acima (limites configuráveis). |
| R2 | **Risco pela frequência de venda**: em quantos dos últimos 90 dias o produto vendeu ao menos 1 unidade. **Alto**: vende pelo menos **1 vez por semana** (14% dos dias); **Médio**: pelo menos **1 vez por mês** (3%); **Baixo**: menos que isso (limites configuráveis). A base é a da venda esperada: os 90 dias antes da falta, sem os dias de falta conhecida. Os 50%/20% da 1ª versão foram trocados em 27/10/2026 (Q15): na loja 60 o produto típico vende em 2% dos dias, e todos os 901 em ruptura saíam "baixo". |
| R3 | A **ruptura próxima** (a do Assistente: estoque para até N dias) entra só na tabela de críticos. Cards, tabela por categoria e gráficos contam só quem já está em ruptura. Na ruptura próxima, a perda é "—"; risco e "Repor" aparecem. |
| R4 | **Investimento para repor** = sugestão do Assistente (até o estoque ideal) × custo da última compra. Custo "a revisar" conta nas unidades e fica fora do R$. Bate com o que o Assistente sugere comprar. |
| R1 | **A perda é medida**, não estimada: a rotina guarda o histórico do estoque (seção 4) e dele saem os períodos de falta de cada produto. Sem estimativa para o passado: no dia da implantação tudo começa com 1 dia medido e a tela mostra "medindo desde dd/mm". Reconstruir o passado por vendas e compras foi descartado (a API não traz transferências, ajustes de inventário nem a data de entrada da nota). |
| R1a | **Perda = venda esperada por dia × preço de venda × dias em falta medidos.** Venda esperada = média do produto nos dias em que ele TINHA estoque, nos 90 dias antes do início da falta. |
| R1a+ | Detalhe da venda esperada (H2, 27/10/2026): antes de o histórico existir não se sabe em quais dias o produto tinha estoque, então só sai da base a falta MEDIDA (e os dias sem dado de venda); os dias anteriores ao histórico entram como dias normais. A conta fica exata conforme o histórico cresce. A "Venda em risco por dia" do card passa a usar a mesma venda esperada (antes, a média comum, puxada para baixo pela própria falta). |
| R8 | **Período**: card, tabela por categoria e gráfico de risco mostram a perda dos **últimos 30 dias** (configurável), contando também as faltas que já acabaram. Nos críticos, por produto, a perda da falta aberta. |
| R9 | **Noite sem foto**: faltava antes e continua faltando depois → o dia sem foto conta como falta; voltou a ter estoque → conta até a última foto em que faltava. A tela mostra "N dias sem foto" quando houver. |
| R10 | Faltou nos 90 dias inteiros (nenhum dia com estoque para a média) → venda esperada zero e perda "sem base". Continua nos críticos pelo risco e pelo "Repor". |
| R1b | **Transição (até o histórico ter 30 dias)**, decidida em 27/10/2026: a página mostra a **projeção** "Perda em 30 dias se não repor" = venda esperada por dia (R1a) × preço de venda × 30, só dos produtos em ruptura hoje. Subtítulo do card: "Medida até agora: R$ X em N dias (desde dd/mm)". Tabela por categoria, gráfico de risco e críticos usam a projeção, com a legenda do bloco "projeção de 30 dias; a perda medida entra em dd/mm". O detalhe do produto mostra os dois números. Com 30 dias de histórico, a página troca sozinha: o card vira "Perda" (medida, R8) e a projeção fica no subtítulo para sempre (ao lado do Investimento para repor, responde "gasto X para não perder Y no mês"). Descartados: estimar o passado pela última venda (gambiarra) e mostrar só a medida desde o 1º dia (números pequenos que subestimam o problema). |
| R12 | **Ordem padrão dos críticos**: primeiro os em ruptura, da maior perda para a menor; depois a ruptura próxima, pela maior venda em risco por dia. Clicar num título reordena. |
| R13 | Parâmetros novos na seção **Dashboard** das Configurações de Pedidos, **personalizáveis por loja** como os demais da seção: período da perda (30 dias), limites do risco (50% e 20%), limites do nível de serviço (65% e 75%). |
| Filtros | Como no Estoque: clicar numa categoria, numa barra de curva ou numa barra de risco filtra a página; Ctrl/⌘+clique soma; clicar de novo no único escolhido tira. Cada bloco não se filtra por si mesmo (o resto fica escurecido). Os filtros de risco da tabela de críticos e as barras de risco são o mesmo filtro. Trocar de loja limpa tudo. |
| Dicas | Todo texto de dica segue `core/dicas.py`. |

## 4. Histórico do estoque (dados novos)

- **Quem grava**: a rotina da madrugada, logo depois de baixar a foto de estoque de cada empresa (só as que recebem
  estoque toda noite: fase 2 da coleta).
- **O quê**: por loja e por **código de produto**, só os dias em que o estoque MUDOU (código, data, estoque), mais a
  data de cada foto (para saber os dias sem foto). A rotina roda sem banco: não conhece o N da ruptura de cada loja
  nem o agrupamento dos genéricos; o app aplica os dois ao ler (um genérico está em falta no dia em que TODOS os seus
  EANs estavam).
- **Onde**: no Spaces, um arquivo por loja e por mês (`pedido/historico_estoque/{empresa}/{loja}/{AAAA-MM}.parquet`);
  a rotina regrava só o mês corrente e apaga o mês que passar de **13 meses** (só do histórico).
- **Custo medido (loja 60, 27/10/2026)**: ~326 produtos mudam de estoque por dia; 13 meses ≈ 145 mil linhas ≈ 0,6 MB
  por loja; as 363 lojas vinculadas ≈ 230 MB (folga até ~0,5 GB). No plano do Spaces (250 GB inclusos; US$ 0,02/GB
  acima) é desprezível. Tempo da rotina: ~1 s a mais por loja, dividido entre as 8 partes (estimado; medir).
- **No app**: ao abrir a Ruptura de uma loja, lê os últimos 4 meses (90 dias da venda esperada + 30 da perda),
  ~200 KB, em cache pela foto de estoque do dia.

### Fatos levantados em 27/10/2026 (por que o histórico só começa agora)

- A API do GPS não tem estoque de datas passadas: as três rotas de estoque (`/empresas/{id}/estoque`,
  `/lojas/{codigo}/estoque`, `/produtos/{codigo}/estoque`) não aceitam data, e o registro não traz data de última
  entrada nem de movimentação. O "histórico por produto" da API é de compras e vendas.
- O Spaces não tem versionamento: cada foto de estoque substituiu a anterior.
- A rotina não está rodando toda noite (execuções em 21, 22, 23 e 25/10; nenhuma em 24, 26 e 27); a foto de estoque
  mais recente era de 23/10, em 215 empresas. O histórico depende de uma foto TODA noite.

## 5. Fora do escopo

- Reconstruir o estoque do passado (R1).
- Comparação com o mesmo mês do ano anterior (o histórico de 13 meses permite; fica para depois).
- Mudanças na página Estoque, no Assistente e na Compras.

## 6. Riscos

1. **O histórico só começa quando o workflow novo for publicado** e só para empresas em fase 2. Até lá, a perda
   fica em "medindo desde dd/mm" com poucos dias; os outros blocos funcionam normalmente.
2. **Tempo da rotina**: medir na primeira execução; se pesar, gravar o histórico numa parte separada do workflow.
3. **Foto de estoque atrasada ou faltando** numa noite: tratada pela R9 e mostrada na tela.
4. **Mudar o N da ruptura** nas Configurações muda os períodos de falta medidos para trás: é o certo (o histórico
   guarda o estoque, não a ruptura), mas os números do passado mudam junto.
