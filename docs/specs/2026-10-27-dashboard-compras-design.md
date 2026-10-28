# Dashboard · Compras — design

Rodadas de perguntas de 27/10/2026 (Q1–Q17). Protótipo visual aprovado em 27/10/2026 (artifact privado "Protótipo
Dashboard Compras", https://claude.ai/artifact/96Qb9h6ZM8qPTvf5wbrePC). Substitui a seção 6 de
`2026-10-21-dashboard-design.md` (lá o excesso era "o produto HOJE em excesso"; aqui é pela entrada da nota).

## 1. O que é e para quê

A página Compras mostra se a loja está comprando bem: quanto da verba do mês (dotação) já foi usada, se compra
mais ou menos do que vende, quanto do que comprou foi para produto sem demanda ou acima do necessário, e quais
produtos causaram isso. Tudo pela ENTRADA (a nota fiscal), comparada com a SAÍDA (a venda).

## 2. Estrutura (a do protótipo)

1. **Período** (pílulas no topo da página, Q1 C): Mês atual · Mês passado · Últimos 3 meses. À direita, discreto:
   "Outubro/2026 até 26/10, pela data de emissão da nota fiscal".
2. **Cards**:
   - **Dotação**: "80,0% consumido", "Saldo de R$ 20,6 mil" (ou "Passou R$ X") e a barra de progresso (vermelha
     quando passa de 100%). Sem os valores "R$ X de R$ Y" (pedido de 27/10: poluía o card).
   - **Compra × Venda**: "96,4%" e "Comprou menos do que vendeu" / "Comprou mais do que vendeu".
   - **Compras não otimizadas**: R$ e "14,1% do comprado".
   - **Eficiência em compras**: % e "Do comprado foi compra certa".
3. **Compras por categoria**: as 5 categorias da classificação com a setinha IQVIA › FEBRAFAR › CMED (igual ao
   Estoque e à Ruptura) e o Total. Colunas: Dotação (no Mês atual, a variação da venda embaixo, "venda +12,0%") |
   Consumido | Compra × Venda | Sem demanda (R$ e %) | Excesso (R$ e %) | Eficiência (ponto colorido). Clicar numa
   categoria filtra a página; Ctrl+clique soma.
4. Lado a lado:
   - **Distribuição das compras por categoria**: barras 100% horizontais (compra certa, excesso, sem demanda);
     clicar filtra como a tabela (mesmo filtro).
   - **Dotação × Consumido** (Q11 A): no Mês atual e no Mês passado, o consumido acumulado dia a dia (linha) e a
     dotação de cada dia (tracejada); em Últimos 3 meses, barras por mês (dotação final × comprado).
5. **Produtos com compra não otimizada**: busca, pílulas Todos / Excesso / Sem demanda. Colunas: Produto (EAN e
   categoria embaixo) | Comprado | Vendido | Limite (un.) | Não otimizado (R$ com barra na célula) | Classificação
   (pílula âmbar "Excesso" ou vermelha "Sem demanda"). Do maior R$ para o menor, 25 por página, ordenação pelo título.
6. **Detalhe do produto** (pop-up no padrão do Estoque e da Ruptura): nome, categoria | EAN, pílula da
   classificação; 4 cards (Comprado com "N bonificadas", Vendido, Limite, Não otimizado com "N un. pagas" quando
   houve bonificação); Diagnóstico; Sugestão de ação; NOTAS FISCAIS DO PERÍODO (data, fornecedor, qtd., preço un.,
   total; a bonificação com pílula).

## 3. Regras

| # | Decisão |
|---|---|
| R1 | **Compra** = item de nota do sistema de vendas da loja (o "pronto" do Assistente; transferências entre lojas já ficam fora na rotina). Data = emissão. Unidades = quantidade × fração. Preço un. = `VlrUnitario × (1 − desconto%) ÷ Fração`, a regra da casa (sem ST). Valor = unidades × preço. |
| R2 | **Bonificação** (preço < limite de bonificação da Análise, R$ 0,10) ENTRA nas unidades (Q14: influencia o que a loja tem para vender) e vale R$ 0. |
| R3 | **Produto** = a linha do Assistente: genérico da Base Genéricos = uma linha só; senão, o código do produto. Inclui produto comprado que nunca vendeu (o cálculo do Assistente não lista esses). **Categoria** = a cascata de classificação (as 5 fixas). |
| R4 | **Períodos**: Mês atual = do dia 1 ao último dia com venda baixada (a véspera da foto); Mês passado = o mês fechado anterior; Últimos 3 meses = os dois fechados anteriores + o atual. Só aparece o período que a janela de dados da loja cobre. |
| R5 | **Compra × Venda** (Q4 A) = consumido ÷ venda a custo do período. Venda a custo = unidades vendidas × custo do Assistente (última compra paga; cadastro na falta). Linha sem custo nenhum fica fora da venda a custo. 100% = repôs exatamente o que vendeu. |
| R6 | **Sem demanda** = a linha comprou no período e não vendeu nada de 90 dias antes da 1ª compra do período até o fim do período. Valor = tudo o que pagou. 90 = parâmetro. |
| R7 | **Limite** = unidades vendidas no período + estoque ideal do Assistente (config da loja). **Excesso** (un.) = parte inteira de (comprado − limite), quando positiva. Valor do excesso (Q17 B) = as unidades acima do limite são primeiro as bonificadas (valem zero): (excesso − bonificadas, no mínimo 0) × custo médio das unidades pagas. |
| R8 | **Compra certa** = consumido − excesso (R$) − sem demanda (R$). **Eficiência em compras** = compra certa ÷ consumido. **Não otimizado** = excesso + sem demanda. |
| R9 | **Dotação** (Q3, Q5 B, Q7; revista em 28/10/2026, Q18–Q21): por categoria, no mês M: base = consumido da categoria em M−1; fator = a variação da venda da categoria (R$ líquido) **por semana**. Semanas fixas: 1–7, 8–14, 15–21 e 22 ao fim do mês (7 a 10 dias). Em cada semana COMPLETA de M (com venda baixada até o último dia dela), a venda média por dia contra a da mesma semana de M−1; fator = Σ médias de M ÷ Σ médias de M−1. Saem da conta, nos dois meses, os **dias fora da conta**: a loja inteira vendeu menos de 40% da **mediana** do mesmo dia da semana nas 8 semanas anteriores (dia que veio incompleto do sistema de vendas, feriado). Sem semana completa (dias 1 a 6) ou venda zero em M−1 → fator 1. Sem limite para o fator por enquanto (Q19: na loja 60 nenhuma categoria passou de −41%). Dotação = Σ base × fator. Nada se grava: é refeita a cada atualização dos dados. |
| R9a | Por que semanas e mediana (medido na loja 60, 28/10/2026). A 1ª versão comparava os dias 1 a N com os mesmos dias de M−1: outubro misturava dias da semana diferentes e dois dias úteis quase vazios (06/10 R$ 893, 07/10 R$ 1.223, contra R$ 9–15 mil), e a dotação de outubro saía R$ 150,0 mil. Só semanas: R$ 141,8 mil (os dois dias ficam na 1ª semana e o feriado de 12/10 cai na 2ª, o de 07/09 na 1ª de setembro). Semanas sem os dias fora da conta: R$ 164,3 mil. Com a MÉDIA no lugar da mediana, os poucos domingos abertos puxavam a média dos domingos e todos os domingos fechados saíam: agosto passava a "subir" 6–14% contra julho, quando todas as outras versões mostram queda. A queda de outubro que sobra (−15% a −41% por categoria) é real: setembro foi um mês forte. |
| R10 | Por que a variação é pela venda em R$ e não a custo: toda venda tem valor; o custo falta em parte dos produtos, e a razão M ÷ M−1 da mesma categoria quase não muda entre as duas réguas. |
| R11 | **Dotação nos períodos** (Q9): Mês atual = a recalculada com as semanas completas até a véspera; Mês passado = a final do mês (as 4 semanas); Últimos 3 meses = a soma das finais de cada mês. O gráfico do mês: no dia d, a dotação com as semanas completas até d (degraus, Q21) e o consumido acumulado até d. |
| R12 | **Comprador** (Q15): o Comprador não vê o Dashboard (decisão de 21/10/2026, `core/acesso.py`). Se um dia passar a ver, cards e dotação são só das categorias dele + Em classificação, como no Assistente. O Proprietário vê a loja inteira. |
| R13 | **Parâmetros** (Q16) na seção Dashboard das Configurações, personalizáveis por loja: `compras_dia_fora_pct` (40%) e `compras_dia_fora_semanas` (8) — o dia fora da conta da R9 — e `compras_sem_demanda_dias` (90). O limite segue o estoque ideal do Assistente. O "dia de início do ajuste" (3) saiu em 28/10/2026 (Q21): com semanas, a dotação só muda depois que a 1ª fecha. |
| R14 | **Diagnóstico** — excesso: "Comprou X un. (B bonificadas) e vendeu Y no período. O limite era L un.: E un. acima do necessário, P delas pagas (R$)."; sem demanda: "Comprou X un. (R$) e não vendeu nenhuma nos 90 dias antes da compra nem depois dela." **Sugestão** — excesso: não comprar até o estoque baixar para o ideal (cerca de D dias no ritmo atual) e conferir se a quantidade não veio de promoção do fornecedor; sem demanda: suspender a compra e avaliar promoção, kit ou exposição no balcão. |
| R15 | Textos de apoio curtos, sem repetir o que a tabela já mostra (pedido de 27/10/2026). Dicas pela `core/dicas.py`. |
| R16 | Cor da eficiência na tabela, a do protótipo: vermelho abaixo de 80%, âmbar até 90%, verde a partir de 90% (fixa; vira parâmetro se pedirem). A distribuição usa as barras clicáveis do Dashboard, na vertical (o protótipo era horizontal): o mesmo componente do Estoque e da Ruptura, com Ctrl+clique. |

## 4. Dados

Nada novo na rotina. A página usa o "pronto" da loja (`pedido/pronto.py`: compras linha a linha e vendas por
produto e dia da janela de `meses_fechados` + o mês corrente), o cálculo do Assistente (custo e estoque ideal por
linha) e a classificação — os três já em cache no app. Com a janela padrão (3 meses fechados), os 3 meses da página e
o mês anterior a eles (base da dotação do mais antigo) estão cobertos.

## 5. Fora do escopo

- "Onde outra loja comprou mais barato" (Q10): fica para depois.
- Dotação digitada pela loja ou % do faturamento: a dotação é sempre a da R9.
- Gravar a dotação dia a dia: é refeita a cada atualização.

## 6. Riscos

- **Janela curta para "sem demanda"**: uma compra no começo do período mais antigo pode não ter os 90 dias de venda
  antes dela dentro da janela; conta só o que existe (dita na dica do card).
- **Nota com fração errada** (caixa lançada como unidade): infla o consumido; a Análise e o Assistente marcam esses
  casos como "a revisar", aqui o valor é o da nota. Reavaliar com a rede online.
- **Notas lançadas com atraso**: a data é a emissão; as compras dos últimos 5 dias são baixadas de novo toda noite,
  então o Mês atual pode crescer para trás nesses dias.
- **Mês anterior atípico** (Q9): uma compra fora do comum em M−1 infla a dotação de M. Fica a regra do Mariano;
  revisar depois.
