# Dashboard — spec de design

Data: 21/10/2026 · Estado: **rascunho para revisão do Mariano** (sem commit, como combinado).
Substitui o placeholder `views/dashboard.py` ("Em construção").

> **Atualização de 22/10/2026:** a tela "Estoque e Ruptura" (seção 5) foi **dividida**. O que está na
> seção 5 virou a página **Ruptura** (sem mudar), e a página **Estoque** é nova, descrita em
> `2026-10-22-dashboard-estoque-design.md` (que manda no que for da Estoque). O menu passa a ter três
> páginas: Estoque, Ruptura e Compras.

## 1. Objetivo

Uma tela de relance para a loja ver: onde há **ruptura** (por curva ABC), onde há **estoque em
excesso e parado**, quanto **economizaria** comprando melhor, e como estão as **compras** frente
às vendas. Cada número leva a uma ação (Assistente de pedido ou Análise).

Os prints de BI enviados são **só inspiração de conteúdo**; o visual segue a identidade atual do
sistema. Nada é copiado.

Público (decisão de 21/10/2026): **o Dashboard não aparece para o perfil Comprador.** Ficam os
outros perfis com acesso (ver a confirmação pendente abaixo). Consequências: o Cadastro não
oferece o painel "Dashboard" ao atribuir painéis de um Comprador, e a restrição de "linhas de
compra do Comprador" (`pedido/linhas.py`) não se aplica ao Dashboard: quem o vê, vê todas as
linhas da loja.

*Confirmado pelo Mariano em 21/10/2026:* Proprietário, Consultor e ADM veem o Dashboard; o
Comprador não. O painel "dashboard" em `core/acesso.py` (`PAINEIS_DA_LOJA`) hoje vale para
Comprador e Proprietário e passa a valer só para o Proprietário.

**Parâmetros (decisão de 21/10/2026):** o Dashboard usa os parâmetros **da loja, automaticamente**:
com personalização gravada, ela; sem, o padrão. Não há pílula Padrão/Personalizado no Dashboard.
Por isso os números podem diferir dos do Assistente quando o usuário escolheu "Padrão" lá. As
correções de estoque negativo e a personalização das linhas de compra seguem as do Assistente.

## 2. Fora desta versão

- Dotação orçamentária (não existe orçamento no sistema; fase seguinte).
- "Perda por ruptura" em R$ estimada por dias de falta (não há histórico diário de estoque). No
  lugar: **venda em risco por dia**, calculada dos dados reais.
- Histórico do estoque ao longo do tempo e variação dos cards contra período anterior de estoque.
- Pré-cálculo noturno. Só entra se a medição (seção 9) mostrar lentidão.

## 3. Escopo e navegação

**Escopo = UMA loja, a da barra lateral** (`views/loja_barra.py`). **ADM e Consultor usam o
Dashboard como se fossem Comprador:** escolhem uma loja e veem os dados dela.

**"Todas as lojas" é bloqueado** (decisão de 21/10/2026, depois da medição): em qualquer perfil, se
a barra estiver em "Todas", a tela mostra um aviso pedindo para escolher uma loja, sem calcular
nada. Motivo medido: cada loja ocupa ~4 MB na memória; as 594 lojas da rede passariam de 2 GB.
Quem tem lojas do mesmo grupo econômico as escolhe uma a uma na barra. Sem coluna "Loja" nas
tabelas e sem agregação entre lojas nesta versão. Soma de várias lojas e visão da rede ficam
para uma fase com pré-cálculo noturno.

*Interpretação a confirmar:* o pedido original queria ver as lojas do grupo juntas, com coluna
"Loja". A resposta de 21/10 ("bloquear Todas e pedir para selecionar") foi lida como: uma loja por vez.

**Menu recolhível na barra lateral**, grupo Gestão:

```
GESTÃO
  Assistente de pedido
  Dashboard            ⌄    ← pai: só abre/fecha, não leva a tela
      Estoque e Ruptura      ← filho (dashboard_estoque)
      Compras                ← filho (dashboard_compras)
```

- Pai destacado quando um filho está ativo; abre sozinho nesse caso. Estado aberto/fechado na sessão.
- Acesso continua sendo **um painel só** ("dashboard"). `core/acesso.py` mapeia as duas chaves de
  seção para o painel; `pode_ver` e `secao_inicial` ganham o mapeamento.
- O filtro clicado (seção 4) vale nas duas telas e é limpo ao trocar a loja da barra.

## 4. Interação: filtro cruzado

- Clique numa barra (categoria ou curva) filtra **todos os blocos da tela**: cards, gráficos,
  tabelas.
- **Ctrl/⌘ + clique** soma ou remove da seleção (2 ou mais). Clique numa barra já selecionada
  desmarca. Clique simples com outras barras selecionadas substitui a seleção.
- Chip por filtro ativo no topo ("Genéricos ✕"), mais "Limpar". Categoria e curva se combinam (E);
  várias da mesma dimensão se somam (OU).
- Implementação: componente CCv2 próprio (`views/barras_filtro.py`, padrão do `copiar_link.py`),
  porque os gráficos nativos do Streamlit não têm Ctrl+clique. Devolve a seleção ao Python.
  **Spike feito em 21/10/2026 (app mínimo fora do projeto, Chromium + Playwright): FUNCIONA.**
  Clique, Ctrl+clique, ⌘+clique, desmarcar, substituir, Enter, Ctrl+Enter e Ctrl+Espaço
  atualizam a tela toda em 40 a 150 ms (mediana 99 ms em 6 Ctrl+cliques seguidos). Achado: o
  Chrome não gera `click` com Enter + Ctrl; o componente trata o `keydown`. Não testado:
  Firefox/Safari, dentro do tema e da barra lateral reais, e com a latência de rede da nuvem
  (cada clique roda o script inteiro, incluindo as consultas da barra lateral). **Plano B**
  (filtros em vez de barras clicáveis) só entra se o teste dentro do app real falhar.
  **Teste dentro do app real feito em 21/10/2026 (Fase 2, Parada 2): PASSOU, 26 de 26.**
  Banco de teste + loja fictícia + login de administrador, Chromium: menu pai/filhas, clique,
  Ctrl+clique, desmarcar, E entre linha e curva, cada gráfico ignorando o próprio filtro, Enter/
  Ctrl+Enter/Ctrl+Espaço com o foco mantido depois do redesenho, o filtro valendo ao trocar de
  tela, mesma altura nos dois gráficos e nenhuma rolagem horizontal (1280, 1920 e 390 px).
  Cada clique custa 200 a 600 ms (SQLite local, máquina de desenvolvimento) e **0 consultas ao
  banco** (os dados da loja, a configuração e as correções ficam em cache na sessão): contra o
  banco em NY não acrescenta tempo de consulta. **Não testado:** Firefox e Safari; latência de
  rede entre o navegador e o Streamlit Cloud; a loja real com os 4,4 s da 1ª abertura.
- Teclado: as barras são botões focáveis (Enter/Espaço = clique, Ctrl+Enter = soma).

## 5. Tela "Estoque e Ruptura"

Fonte: o cálculo do Assistente (`pedido/calculo.py`): `estoque`, `estoque_max`, `demanda_dia`,
`ruptura`, `ruptura_proxima`, `giro_baixo`, `curva`, `preco` (custo), `preco_venda`, `categoria`,
`grupo`. Números batem com o Assistente. Entram **todas** as linhas, não só as que o Assistente
lista (`listar`).

**Cards (4) + 1 secundário:**

| Card | Cálculo |
|---|---|
| Ruptura | **TODOS** os itens com `ruptura` (estoque ≤ N un.), inclusive os de giro baixo; e % do mix. Subtítulo informativo: "N com giro baixo" |
| Venda em risco / dia | Σ `demanda_dia` × `preco_venda` dos itens em ruptura (R$/dia) |
| Estoque em excesso | as unidades que passam de **N dias de venda** (config `dias_cobertura_excesso`, **padrão 60, decidido pelo Mariano em 21/10/2026**, ajustável por loja): (estoque − demanda/dia × N)⁺ × custo; R$ e % do estoque. **Não usa o "ideal" do Assistente.** Não conta o que já é "parado" nem ruptura |
| Estoque parado | estoque > 0 e sem venda na janela do giro baixo (90 d) × custo; R$ e % |
| Cobertura média (secundário) | estoque total ÷ Σ demanda/dia, em dias |

"Venda em risco" e não "perda": não medimos dias de falta.

**Decisões de 21/10/2026 (depois da medição em duas lojas reais):**
- **Ruptura completa.** No Assistente o giro baixo é filtrado porque lá o objetivo é sugerir compra;
  no Dashboard o objetivo é análise, então todo item sem estoque conta. Medido: 1.220 itens em
  ruptura numa loja de 3.915 (31%), dos quais 702 têm giro baixo; só 73 vendem ≥ 0,1 un./dia.
  A tabela e os cards deixam isso visível (subtítulo e a tag "giro baixo" na lista).
- **Assistente e Dashboard são duas visões** (Mariano, 21/10/2026): o ideal do Assistente (7 a 15
  dias) é meta de COMPRA; o Dashboard analisa o estoque com régua própria. **Excesso = a parte do
  estoque que passa de N dias de venda** (N configurável por loja). Estoque, parado e ruptura
  usam só o dado de base do Assistente (estoque, venda por dia, custo, curva), nunca o ideal.
  A 1ª versão usava o ideal como régua e marcava 60% do estoque da loja 60 como excesso.
  Loja 60 com a régua nova: N = 60 → eficiente 25,3%, excesso 48,9%, parado 25,9%;
  N = 30 → eficiente 14,7%, excesso 59,5%, parado 25,9%. Cada real do estoque numa classe só.

**Blocos:**
1. **Ruptura por curva ABC** — barras A, B e C (1ª letra da curva de valor, como o Assistente):
   itens em ruptura e % de cada curva em ruptura. Clicável. Medido: A 39, B 268, C 913 itens.
2. **Estoque por linha de compra** — barras empilhadas *eficiente / excesso / parado* em R$ de
   custo. Clicável (Ctrl+clique soma). **Dimensão = linha de compra** (Genéricos, Propagados,
   Similares, Perfumaria, Sem linha; `pedido/linhas.py`, catálogo IQVIA + base de categorias), a
   que mais se parece com os "grupos" do BI. As 12 categorias da base ficam para um nível de
   detalhe futuro: 24% dos produtos não têm categoria.
3. **Produtos críticos** — tabela, máx. 25 linhas por página: produto, categoria, curva (2
   letras), estoque, cobertura (dias), venda em risco/dia, situação (ruptura, ruptura próxima,
   excesso, parado). Ordem padrão: venda em risco. Clique na linha abre o detalhe do produto.

## 6. Tela "Compras"

Fonte: compras da rede por mês fechado (`pedido/rede.py`, as mesmas da Análise), vendas da loja,
resultado do Assistente. Período: o da Análise (1, 2 ou 3 meses fechados — mesmo seletor).

**Pré-requisito (decisão de 21/10/2026): um arquivo da rede SÓ do Dashboard, já limpo, no
bucket.** Não se mexe no arquivo da Análise (`analise/compras/`): ela usa as linhas "a revisar"
para avisar o usuário, e limpá-las ali mudaria a tela dela. O arquivo novo fica em outro prefixo
(nome a definir no plano) e é montado a partir dos mesmos arquivos por loja.
- **Verificado (21/10/2026):** `pedido/analise/compras/` está vazio porque `pedido/rede.py` nunca
  foi commitado (aparece como não rastreado no git) e o workflow publicado no `main` não tem o
  job `rede`. A rede nunca rodou no GitHub.
- **Limpeza = a lógica do Assistente de pedido** (`pedido/calculo.py`), aplicada antes de gravar.
  Descarta: quantidade ≤ 0 (devolução; medido: uma loja com "comprado" de −R$ 223 mil), preço
  < R$ 0,10 (bonificação) e notas marcadas "a revisar" (cadastro × nota divergentes, acima do
  preço de venda, fora de 3× ou 1/3 da mediana da loja). Medido sem limpeza: 980 de 6.480
  comparações tinham razão de preço acima de 3× (embalagem/fração).
- **UF verificada:** pela loja vinculada (`lojas.uf`), 247 das 249 lojas com compras em set/26
  (99%) têm UF; o `uf_gps` do vínculo cobre só 83 de 325. Usar `lojas.uf`. 190 das 247 são MG.
- **Como é montado (decisão de 21/10/2026):** o destino final é a rotina do GitHub, mas ela ainda
  não está publicada; por enquanto, **à mão**, por um script com prévia e `--executar`, como as
  demais cargas de hoje. Gravar no bucket real só com prévia e aprovação.

**Cards (5):**

| Card | Cálculo |
|---|---|
| Economia possível | Σ (preço pago − melhor preço da rede) × qtd, onde alguém pagou menos (regra abaixo) |
| Comprado | Σ notas da loja no período, sem bonificação |
| Compra × venda | comprado ÷ vendido (R$), em % |
| Compras em excesso | compras do período de produtos que **hoje** estão em excesso (estoque acima de N dias de venda, mesma régua da tela de estoque); R$ e % |
| Compras sem demanda | compras do período de produtos **hoje** parados (sem venda em 90 d); R$ e % |

Excesso/sem demanda são pelo estoque de hoje (não há histórico diário); a dica diz isso.

**Blocos:**
1. **Compra × venda por mês** — linhas (comprado, vendido) + relação. Só meses presentes nos dois
   lados; as vendas guardam 3 meses.
2. **Compras por linha de compra** — barras empilhadas *aproveitadas / em excesso / sem demanda*,
   clicáveis (mesma regra do filtro cruzado).
3. **Comprou mais barato (anônimo)** — uma linha por produto: o que a loja pagou (preço,
   quantidade, data, distribuidor) ao lado da melhor compra da rede (preço, quantidade, data,
   distribuidor), diferença por unidade, economia. **Nunca o nome da loja**, nem do mesmo grupo.
4. **Compras a melhorar** — produtos com compra em excesso ou sem demanda, por valor.

**Regra do "comprou mais barato"** (reaproveita bonificação fora e "a revisar" de
`core/analise_api.py`):
- mesmo produto: mesmo EAN, ou o mesmo genérico quando o EAN está na Base Genéricos;
- mesma UF da loja (a tributação muda por UF);
- só compras dentro do período escolhido;
- quantidade comparável: a compra mais barata vale se a quantidade ≥ **50%** da que a loja
  comprou (config `quantidade_comparavel`, ajustável nas Configurações de Pedidos);
- só conta quando a melhor compra é **menor** que a da loja;
- privacidade: só mostra a comparação se **≥ 2 lojas diferentes** da rede compraram o produto
  na UF no período (config `minimo_lojas_comparacao`), para não identificar a loja por exclusão.

## 7. Padronização visual (vale para as duas telas)

Regra geral: **nenhum valor solto.** Tudo sai dos tokens de `core/theme.py` (`TOKENS`, `PALETA`,
`STATUS`); se faltar um valor, ele entra nos tokens ANTES de ser usado. Seletores internos do
Streamlit só via `SELETORES_STREAMLIT`. CSS do Dashboard em bloco próprio ("DASHBOARD") na camada 3
(`theme.tela("dashboard")`).

**Layout e espaços**
- Largura do conteúdo: `LARGURA_MAXIMA_PX` (1220), igual às demais telas. Grade de 12 colunas
  conceituais: cards 4×3; blocos de gráfico 6+6 ou 12; tabela 12.
- Vãos: 16 px (`esp-4`) entre blocos e entre cards; 24 px (`esp-5`) de padding interno dos
  cartões; 8 px (`esp-2`) entre elementos de um mesmo grupo. Mesmos vãos nas duas telas.
- Cartões iguais: fundo `--fundo-card`, borda `--borda`, mesmos raios dos cards da Análise
  (`.rmc-kpis .rmc-kpi`). Cards de uma fileira têm a **mesma altura**.
- Altura fixa por tipo de bloco: gráfico 280 px, cada linha de tabela de altura única (25 linhas
  = altura máxima da tabela), para a tela não "pular" ao filtrar. Estado vazio ocupa a mesma altura.
- Nada de largura em `fr` irregular nas tabelas: mesma regra de colunas pela largura do conteúdo
  e vãos iguais (grid + subgrid) já usada em `views/tabela.py`.

**Tipografia** (escala fechada, `fs-*`): título da tela 28/700; título de bloco 20/700 (navy);
número do card 28 (`fs-titulo`) em `tabular-nums`; rótulo do card 12 (`fs-min`) em maiúsculas;
corpo e tabelas 14; legendas e dicas 13 em `--texto-suporte`. Números sempre alinhados à direita
e com `font-variant-numeric: tabular-nums`; moeda `R$ 1.234,56`; % com 1 casa; abreviar (mil, mi)
só em eixo, nunca em tabela nem card.

**Cores** (semânticas, nunca decorativas)
- Neutros: `--navy`, `--navy-claro`, `--texto-suporte`, `--borda`, `--fundo-card`.
- Situação do estoque (as mesmas nos dois gráficos e nas tags), **validadas em 21/10/2026** com o
  script do skill de dataviz contra o fundo do cartão (`GRAFICO` em `core/theme.py`):
  **eficiente** `#3F7D20`; **excesso** `#C98A12`; **parado** `#3B73B9`; **ruptura / sem demanda**
  `#B3261E`. Mudaram em relação ao rascunho (verde da marca `#639922`, âmbar `#B9771A`, parado
  `#4A6E90`): o verde e o âmbar ficavam quase iguais na deuteranopia (ΔE 1,5; o piso é 8) e o
  azul-acinzentado "lia como cinza" (croma abaixo do piso). Separação de daltonismo agora
  9,5 a 18; visão normal 20,9. **Aviso aceito:** o âmbar tem contraste 2,77:1 com o fundo (< 3:1),
  então toda barra mostra o valor escrito e a dica, nunca só a cor. Verde e vermelho se confundem
  entre si na deuteranopia (ΔE 1,8): por isso nunca ficam lado a lado numa pilha.
- Curva ABC: rampa de uma cor só, validada (A `#17375E`, B `#4A6E90`, C `#86A0BF`; a mais clara
  precisa de 2:1 com o fundo). O B é parecido com o azul do "parado" (`#3B73B9`), mas são
  gráficos diferentes, cada um com legenda; se atrapalhar na tela, trocamos a rampa.
- Cor sozinha nunca carrega o significado: toda barra tem rótulo e valor; as tags têm texto.
- Contraste mínimo AA (4,5:1 para texto). O tema é claro fixo (como no resto do sistema).

**Gráficos**
- Mesmo modelo de gráfico em toda a tela: barras (verticais, empilhadas quando houver partes)
  e uma linha (compra × venda). Sem pizza, sem rosca, sem 3D, sem gradiente, sem sombra.
- **Eixos fixos e previsíveis:** eixo Y sempre começa em zero; teto arredondado para um passo
  "bonito" (1-2-5 × 10ⁿ) e com **o mesmo número de marcas (5)** em todos os gráficos; gráficos de
  % têm teto fixo em 100%. Eixo X na mesma posição e com a mesma fonte (12 px, `--texto-suporte`)
  em todos; rótulos de categoria nunca rotacionados (se não couberem, quebra em 2 linhas).
- Linhas de grade horizontais finas (`--borda`), sem grade vertical, sem moldura. Valor em cima da
  barra (12 px) quando couber; legenda sempre acima do gráfico, à esquerda, na mesma ordem das
  cores empilhadas.
- Mesma ordem das categorias nos dois gráficos de categoria (por valor, decrescente), e **mesma
  cor para a mesma categoria/situação** em toda a tela.
- Barra selecionada com cor cheia; as não selecionadas a 35% de opacidade; foco visível no teclado.
- Tooltip padrão único: nome, valor, % do total.

**Tabelas**
- `views/tabela.py` (CCv2) em ambas; máx. **25 linhas por página**, paginação fixa embaixo,
  ordenação pelo título como nas telas de Análise; vazios sempre no fim.
- Cabeçalho único, número à direita, texto à esquerda.

**Cards**
- Estrutura fixa: ícone em círculo + rótulo em maiúsculas; número; subtítulo (%, contexto); dica
  "?" com a fórmula. Mesmo modelo dos cards `.rmc-kpi` da Análise (modelo do print de 25/09/2026).
  Card estreito (< 250 px): mesmo comportamento já resolvido (ícone sobe, título reserva 2 linhas).

**Estados e textos**
- Carregando: esqueleto com a mesma altura do bloco. Vazio: mensagem curta e o que fazer.
  Erro: notificação padrão (`popups.notificar`); nenhuma tela quebra por falta de dado de uma loja.
- Textos em português, frases curtas, "sistema de vendas" nas dicas (não "GPS"), como no resto.
- **Responsivo:** 1280 e 1920 px medidos no teste visual; abaixo de 640 px, cards em 1 coluna e
  tabelas com rolagem interna, sem rolagem horizontal da página.

## 8. Arquitetura

- `core/dashboard.py` — funções puras (pandas) → indicadores, agregações, tabelas, comparação de
  preço. Sem Streamlit, sem banco.
- `views/dashboard_estoque.py`, `views/dashboard_compras.py` — as telas.
- `views/dashboard_comum.py` — escopo, chips, cards, tabela de 25 linhas.
- `views/barras_filtro.py` — componente CCv2 das barras clicáveis.
- Alterados: `app.py` (menu pai/filhos), `core/acesso.py` (chaves sob o painel), `core/theme.py`
  (CSS do menu e do Dashboard + tokens de gráfico), `core/config.py` e `config_pedido`
  (`quantidade_comparavel`, `minimo_lojas_comparacao`, `dias_cobertura_excesso`) — **personalizáveis
  por loja**, como os demais parâmetros do Assistente (decisão de 21/10/2026; reaproveita
  `personalizacoes_pedido_loja`); `pedido/rede.py` NÃO muda para a Análise: o arquivo da rede do
  Dashboard é montado à parte (seção 6).
- **Sem migração de banco.** Tudo sai do que já existe.

## 9. Desempenho e segurança do app

- Uma loja por vez: o cálculo é o do Assistente, em cache pela mesma chave (foto do estoque).
  Clique no filtro recalcula apenas as agregações (dezenas de números).
- Compras: lê o arquivo da rede só para os EANs comprados pela loja no período, nunca o mês inteiro.
- Gráficos recebem agregados, nunca linhas. Tabelas: 25 por página.
- **Medido em 21/10/2026** (dados reais, daqui até NY, somente leitura; scripts fora do projeto):
  - **Amostra de 18 lojas com "pronto" (de 21; 3 voltaram vazias):** atualizar o pronto 2,4 s
    (mediana; máx. 3,1 s) + calcular 0,43 s (máx. 0,9 s) ≈ **3 s na 1ª abertura de uma loja**.
    Agregar e refiltrar: 8 a 22 ms.
  - **Memória: ≈ 11 a 13 MB por loja calculada** no processo (+206 MB para 18 lojas, pico
    466 MB). O DataFrame sozinho tem ~4 MB; o resto é cópia e cache. O Dashboard deve reusar o
    `_calcular` do Assistente (`st.cache_data`, máx. 30 lojas ≈ 350 MB) em vez de criar outro
    cache. 594 lojas passariam de 7 GB: "Todas" bloqueado.
  - **Não verificado:** o limite de memória do Streamlit Community Cloud (não confirmei o valor
    atual); só medi um processo, sem várias sessões simultâneas.
  - Compras: ler as 249 lojas de um mês 4,7 s e +119 MB (pico 365 MB), por isso a leitura
    filtrada por EAN; menor preço por EAN 0,12 s.
- **Estados de borda vistos nos dados reais:** 3 das 21 lojas voltam sem linhas (0 vendas na
  janela; uma delas com 4.412 itens em estoque). 14 das 18 têm estoque negativo no sistema de
  vendas (738 linhas). A tela precisa de um estado "sem dados para esta loja" e trata estoque
  negativo como 0, como o Assistente.
- **Valores negativos nos gráficos (decisão de 21/10/2026, opção A):** "podem ser negativos;
  quase impossível, mas o gráfico não pode quebrar". A barra desce abaixo do zero e o eixo Y se
  estende para baixo (mesmas 5 marcas, teto arredondado nos dois lados). Sem negativo, o eixo
  começa em zero como na seção 7.
- **Valores em cima das barras:** sem mudança pedida; fica como no protótipo. O Mariano avisa se
  quiser tirar ou acrescentar algo depois.

## 10. Testes

- pytest em `core/dashboard.py`: cada card; excesso e parado; venda em risco; regra de 50% e de
  mínimo de 2 lojas; **anonimato** (nenhum nome/CNPJ de loja sai na tabela "mais barato");
  ruptura inclui giro baixo; excesso por cobertura > 60 dias; "Todas as lojas" não calcula nada;
  limpeza do arquivo da rede (quantidade ≤ 0, bonificação, "a revisar" ficam de fora).
- Teste de acesso: painel "dashboard" libera as duas chaves; sem painel, nenhuma.
- Teste visual (`tests.visual.rodar`): menu aberto/recolhido; as duas telas em 1280 e 1920 px; com
  e sem categoria clicada; Ctrl+clique com duas; vãos e alturas medidos (±8 px); nenhuma rolagem
  horizontal; novos seletores em `SELETORES_STREAMLIT`. A loja fictícia ganha dados de excesso,
  parado e compra mais barata.

## 11. Ordem de execução (depois da aprovação da spec)

1. **Protótipo visual em HTML** (feito; precisa ser refeito com as decisões de 21/10: uma loja só,
   estado "selecione uma loja", linhas de compra no lugar das 4 categorias, ruptura completa,
   sem coluna Loja) — aprovação do Mariano antes de código.
2. Spike do componente de barras com Ctrl+clique.
3. `core/dashboard.py` + testes.
4. Menu e acesso.
5. **Tela Estoque e Ruptura** (não depende da rede).
6. **Arquivo da rede no bucket, limpo** (prévia → aprovação → execução) → depois a tela **Compras**.
7. Medições finais, teste visual, atualização de `CLAUDE.md` e `status_e_pendencias.md`.

Sem commit, branch ou push; ao final, lista dos arquivos alterados.
