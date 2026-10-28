# Dashboard · página Estoque — spec de design

Data: 22/10/2026 · Estado: **para aprovação do Mariano** (sem commit). Complementa
`2026-10-21-dashboard-design.md`: a tela "Estoque e Ruptura" foi **dividida** em duas páginas, e esta
spec cobre a nova, **Estoque**. Decidida em rodadas de perguntas (grill-me) em 21 e 22/10/2026.

## 1. Menu e páginas

Dashboard → **Estoque** · **Ruptura** · **Compras**.

- **Ruptura** é a tela que já existe (cards de ruptura e de venda em risco, ruptura por curva, estoque por
  linha, produtos críticos, chips de filtro), **sem mudar o conteúdo**. Só muda o nome no menu, o
  título e os nomes internos (arquivo, chave da tela, capturas do teste visual). O Mariano redesenha
  depois.
- **Estoque** é nova: só o que está nesta spec. Reaproveita **apenas o design aprovado** (cards, blocos,
  barras clicáveis, tabela de 25 linhas, pop-up, cores, espaços, fonte). Nada da lógica da Ruptura.
- **Compras** continua "em construção".
- A tela inicial de quem só tem o painel Dashboard passa a ser **Estoque**.
- Uma loja por vez, a da barra lateral; com "Todas as lojas" a tela pede uma loja (como hoje).

## 2. Layout (largura inteira, de cima para baixo)

**Não há nenhum filtro nem texto acima da página.** Ela abre direto nos cards.

```
┌─ Unid. em estoque ──┬─ Estoque a preço de compra ─┬─ Valor em excesso ─┬─ Valor parado ─┐
└─────────────────────┴──────────────────────────────┴────────────────────┴────────────────┘
┌─ Análise por categoria (tabela, linha de total, clicável) ───────────────────────────────┐
┌─ Estoque por linha de compra (barras eficiente · excesso · parado, clicáveis) ────────────┐
┌─ Produtos críticos  [busca nome/EAN] [Situação: Todos | Excesso | Parado] ────────────────┐
```

Por que largura inteira: medido em 21/10/2026 com o componente de tabela real, em 1280 px (com a barra
lateral) meia página dá 366 px de tabela e **nem 3 colunas cabem** (em 1920 px, 552 px, cabem 4). O
app precisa funcionar em 1280 px. Poucas barras no gráfico por linha: as barras ficam mais largas.

## 3. Cards (4)

| Card | Valor | Subtítulo | Ao passar o mouse |
|---|---|---|---|
| Unid. em estoque | Σ unidades (SKUs com estoque > 0) | "N SKUs" | a conta |
| Estoque a preço de compra | Σ unidades × custo (R$) | — | **só** a observação do estoque negativo: "N SKUs com estoque negativo no sistema de vendas ficam de fora" |
| Valor em excesso | R$ | "X% do estoque · N SKUs" | a conta (acima de N dias de venda) |
| Valor parado | R$ | "X% do estoque · N SKUs" | a conta (sem venda em N dias) |

Cores: unidades e estoque a preço de compra em azul/neutro, excesso em âmbar, parado em azul (as do gráfico).
Os cards seguem os filtros da página (categoria e linha). O termo é **SKU**, não "produto".

## 4. Análise por categoria (tabela)

- Colunas: **Categoria · SKUs · Unid. em estoque · Estoque a preço de compra (R$ e % do total) · Estoque a
  preço de venda (R$ e % do total) · Cobertura (dias)**. Linha **Total** no fim (cobertura do total = Σ
  unidades ÷ Σ unidades vendidas por dia). Uma linha para "Sem classificação" (22% dos SKUs na loja 60).
- Ordem padrão: estoque a preço de compra, do maior para o menor; clicar no título ordena.
- "Estoque a preço de venda" é só informação complementar ("se eu vender o estoque todo, quanto dá").
- **Sem seletor de dimensão** (decidido em 22/10/2026: um seletor deixaria filtro escondido e duplicaria o
  gráfico por linha). A dimensão é sempre Categoria. Se um dia vier outra, o seletor volta.
- Até 14 categorias + "Sem classificação": sem paginação (o limite de 25 linhas vale).

## 5. Estoque por linha de compra

O mesmo gráfico de hoje (eficiente / excesso / parado em R$ a custo, por linha de compra), com a base nova
(todos os SKUs com estoque). Barras mais largas quando há poucas.

## 6. Produtos críticos (tabela)

- **Quem entra:** SKUs em excesso ou parados (os "eficientes" não entram).
- **Colunas:** Produto (nome, categoria embaixo e, quando houver, a marca "compra recente" com a data) ·
  Curva · Estoque · Und. em excesso · Estoque a preço de compra · Vendidas em N dias · Cobertura (dias).
  O título da penúltima mostra o N configurado (padrão 90).
- **Estoque a preço de compra** mostra "a revisar" no lugar do valor quando o custo de cadastro é
  incoerente (ver seção 8); o pop-up explica o motivo.
- **Ordem padrão:** valor (excesso + parado, a custo), do maior para o menor; clicar no título ordena.
- **Busca** (nome ou EAN) e **Situação** (Todos / Excesso / Parado) ficam no cabeçalho do bloco e mudam só
  esta tabela (não os cards, a tabela por categoria nem o gráfico).
- 25 linhas por página, com paginação. Botão "Ver detalhes" por linha.
- **Pop-up de detalhe**, no mesmo padrão dos outros (tipo "curto", mesma fonte, espaços e rodapé): produto,
  categoria, linha de compra, curva, situação, estoque, und. em excesso, custo e a origem dele (última
  compra / cadastro / a revisar), preço de venda, última compra (data e fornecedor), última venda,
  vendidas em N dias e cobertura.

## 7. Interação

- **Clique simples** numa barra ou numa linha da tabela por categoria: o filtro passa a ser **só aquilo**
  (substitui o que havia). **Ctrl/⌘+clique:** soma ao filtro (ou tira, se já estava). **Clicar de novo** na
  única selecionada: tira o filtro. Sem chips e sem botão "Limpar".
- **O que não está filtrado fica escurecido** (como no BI), nas barras e nas linhas da tabela por categoria.
  A dica "Clique para filtrar a página. Ctrl+clique soma mais de uma." fica no subtítulo desses dois blocos.
- Categoria e linha de compra se combinam (E); várias da mesma dimensão somam (OU). Cada bloco ignora o
  próprio filtro (as outras barras/linhas não somem). Os cards e a tabela de críticos seguem os dois.
- O filtro é **desta página** (Ruptura e Compras têm os seus) e trocar de loja limpa tudo.

## 8. Regras de cálculo

- **Universo:** só SKU com estoque > 0 no sistema de vendas, mesmo sem nenhuma venda. Negativo e zerado
  ficam de fora; as correções manuais do Assistente são ignoradas aqui. SKU = **código do produto** (sem
  agrupar genéricos). Medido na loja 60: 16.413 SKUs, 167.880 unidades (o Dashboard de antes só via 4.889,
  porque o cálculo do Assistente só devolve quem vendeu).
- **Parado:** SKU com estoque e **nenhuma venda nos últimos N dias** (N = parâmetro, padrão 90).
- **Excesso:** o que passa de 60 dias de venda (parâmetro existente), nos SKUs que não são parados. O ritmo
  é unidades vendidas em N dias ÷ N. **Parado fica fora do excesso**: cada real está em uma classe só
  (eficiente, excesso ou parado), e os dois cards fecham com o total.
- **Cobertura (dias):** unidades em estoque ÷ unidades vendidas por dia (em unidades, não em valor). Sem
  venda: "—". Na linha de total: Σ unidades ÷ Σ unidades por dia.
- **Curva:** AA a CC, a do Assistente (valor e unidades vendidos), **fixa em 90 dias**, para quem vendeu;
  **D** para quem não vendeu em N dias.
- **Compra recente:** última compra nos últimos 30 dias (fixo). O SKU continua parado; a tabela só marca.
- **Custo (estoque a preço de compra):** último preço de compra do Assistente, com as regras dele, quando há
  compra na janela; senão o custo de cadastro quando coerente (entre 15% e 100% do preço de venda). Custo
  **fora disso não entra no R$** (as unidades contam) e a tabela marca "a revisar". Na loja 60, só 23% dos SKUs
  têm compra na janela e cerca de 8% do valor a custo tem cadastro suspeito.
- **Estoque a preço de venda:** unidades × preço de venda.

## 9. O modelo de madrugada

Preparado **depois da coleta**, na `_montar_prontos` da rotina (`pedido/coleta.py`), logo após o "pronto"
de cada loja, a partir do que **já está no Spaces** (sem consultar a API de novo). A rotina **não tem banco**:
o modelo só usa o que o Spaces tem, e o app junta o resto na hora de ler.

- **Grava** em `pedido/estoque/{empresa}/{loja}/`: um arquivo com **um SKU por linha** (código, EAN, nome,
  laboratório, estoque, custo e a origem dele, preço de venda, data e fornecedor da última compra, data da
  última venda) e outro com as **vendas por dia** por SKU (assim a janela de N dias não tem teto de 90 no
  código: hoje o Spaces guarda cerca de 3 meses, e quando guardar mais a janela cresce junto), mais um
  `meta.json` com a assinatura (só refaz quando estoque ou vendas mudam) e a data da foto.
- **Parâmetros do custo:** os do padrão de todas as lojas. A personalização por loja das regras de custo
  **não vale** aqui (limite conhecido; se uma loja precisar, a gente publica a configuração no Spaces).
- **Se a loja ainda não tem o modelo** (loja nova, ou a rotina nova ainda não publicada no GitHub): o app
  monta na hora e grava, como já faz com o "pronto" (1 a 3 s na primeira abertura). Funciona hoje.
- **Na leitura (app):** junta a **categoria** (base de categorias), a **linha de compra** (catálogo IQVIA e o
  mapa da loja) e aplica os parâmetros da loja (N do parado, dias do excesso). Agregar leva milissegundos.
  Memória medida: ~5 MB por loja (Hudson, 16 mil SKUs), a montagem leva ~0,6 s.

## 10. Parâmetros (por loja, nas Configurações de Pedidos)

- **Dias sem venda para ser parado** (N): **novo**, padrão 90, usado no parado, na coluna "Vendidas em N
  dias" e no ritmo de venda da cobertura e do excesso. Sem teto fixo de 90 no código; a tela avisa quando o
  Spaces tem menos dias de venda que o pedido.
- **Dias de venda do excesso:** o que já existe (padrão 60).

## 11. Estados

Sem loja / "Todas as lojas" / sem vínculo / sem dados coletados: os mesmos avisos da Ruptura. Loja sem nenhum
SKU com estoque: aviso próprio. Filtros que não deixam SKU nenhum: estados vazios das tabelas e do gráfico.

## 12. Fora do escopo

Laboratório e seletor de dimensão, "Todas as lojas", personalização do custo na madrugada, correções
manuais de estoque negativo, a página Compras, e qualquer mudança na página Ruptura.

## 13. Testes e aceite

- pytest: construtor do modelo (cada origem de custo, "a revisar", estoque negativo, SKU sem venda, venda
  por dia), cálculos de leitura (parado, excesso, cobertura, curva D, compra recente, total da tabela),
  filtros E/OU, parâmetro novo.
- Teste visual: a página nos dois tamanhos, com clique, Ctrl+clique, o escurecimento e o pop-up; vãos da
  tabela; o menu com as três páginas.
- Conferência com a loja 60 (Mariano): unidades, SKUs, estoque a preço de compra, excesso e parado. Os
  valores finais serão **menores** que os de referência desta conversa (R$ 1,75 mi a custo de cadastro),
  porque o custo passa a ser o da última compra e o custo incoerente sai do R$.
