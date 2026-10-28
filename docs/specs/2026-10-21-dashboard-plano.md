# Dashboard — plano de implementação

Data: 21/10/2026 · Spec aprovada: `2026-10-21-dashboard-design.md` · Estado: **para revisão do Mariano**.

Regras que valem em TODAS as tarefas (CLAUDE.md): sem commit, branch ou push; comentários e textos
em português explicando o porquê; tokens do tema (nada de valor solto); seletores internos do
Streamlit só por `SELETORES_STREAMLIT`; nada grava no bucket/banco reais sem prévia e aprovação
(o `.streamlit/secrets.toml` local aponta para o banco dev e o Spaces reais: para testar sem tocar,
`RMC_IGNORAR_SECRETS=1`, e no `streamlit run`, `--secrets.files=<secrets de teste>`).
Ao fim de cada fase: `python -m pytest -q -p no:warnings` (325 passando em 07/10) e a lista dos
arquivos alterados.

Cada fase tem uma **parada para você**: eu não passo para a seguinte sem o seu "pode seguir".

---

## Fase 1 — Cálculo e acesso (sem tela)

**T1. Acesso e Cadastro** — `core/acesso.py`, `views/cadastro.py`, `integrations/usuarios.py`
- `core/acesso.py`: seções `dashboard_estoque` e `dashboard_compras` cobertas pelo painel
  `dashboard` (`PAINEL_DA_SECAO`); painéis por nível: Comprador = pedido + Por Produto;
  Proprietário = os 3. `pode_ver` recusa o Dashboard para o Comprador **mesmo que o painel
  esteja gravado** (usuário antigo). `secao_inicial` mapeia o painel `dashboard` para
  `dashboard_estoque`. Atualizar o docstring do módulo (hoje diz "Dashboard: ADM e Consultor").
- `views/cadastro.py` (multiselect "Painéis", linhas ~262 e ~405): só oferece os painéis do nível
  do usuário editado (Comprador não vê "Dashboard").
- Testes: `tests/test_usuarios.py` e `tests/test_pedido_fase5.py` já cobrem painéis; acrescentar
  casos: Comprador com painel `dashboard` gravado não vê; Proprietário vê as duas seções;
  Consultor e ADM veem; `secao_inicial`.

**T2. Parâmetros novos** — `pedido/configuracao.py`, `integrations/configuracao_pedido.py`,
`views/config_pedido.py`, `core/config.py`
- Três campos em `ConfigPedido`: `dias_cobertura_excesso` (60), `quantidade_comparavel` (0,5),
  `minimo_lojas_comparacao` (2). Entram em `diferencas`/`aplicar` (personalização por loja) e na
  tela de Configurações de Pedidos (seção nova "Dashboard"). Sem migração: a configuração é JSON
  com linha nova a cada gravação; ler config antiga sem os campos usa o padrão.
- Verificar ao implementar: como `ConfigPedido.parametros()` repassa campos ao `calculo.Parametros`
  (o excesso do Dashboard não precisa de `Parametros`; ele lê a `ConfigPedido`).
- Testes: ida e volta da gravação, personalização de um campo, config antiga sem os campos.

**T3. `core/dashboard.py` (funções puras, sem Streamlit nem banco)**
- Entrada: o DataFrame do `calculo.calcular` (todas as linhas, não só `listar`) + a config.
- `preparar(df, config, linha_por_produto)`: estoque negativo vale 0; custo = `preco`;
  `cobertura = estoque / demanda_dia`; colunas `em_ruptura` (**inclui giro baixo**), `em_excesso`
  (as unidades acima de `dias_cobertura_excesso` dias de venda; régua própria, nunca o ideal do
  Assistente; não conta parado nem ruptura), `parado` (estoque > 0 e giro baixo),
  `venda_risco_dia = demanda_dia × preco_venda` nos itens em ruptura, `linha` (via
  `pedido/linhas.coluna`).
- `cards_estoque(df)`: ruptura (itens, % do mix, subtítulo "N com giro baixo"), venda em risco/dia,
  excesso (R$ e % do estoque), parado (R$ e %), cobertura média.
- `ruptura_por_curva(df)` (A/B/C pela 1ª letra), `estoque_por_linha(df)` (eficiente / excesso /
  parado em R$ de custo), `criticos(df)` (ordem: venda em risco, depois valor parado).
- `filtrar(df, linhas, curvas)`: várias da mesma dimensão = OU; entre dimensões = E.
  As barras de uma dimensão ignoram o PRÓPRIO filtro (`sem_linha`, `sem_curva`), como no protótipo.
- `eixo(valores)`: devolve (mínimo, máximo, 5 marcas) com teto 1-2-5×10ⁿ; com negativo, estende
  para baixo (opção A da spec).
- Testes (`tests/test_dashboard.py`): cada card com um quadro pequeno à mão; ruptura inclui giro
  baixo; excesso por cobertura; estoque negativo como 0; filtros OU/E; eixo com e sem negativo,
  só zeros e vazio; **valores reais de 2 lojas como conferência manual** (script fora do pytest).
- **Parada 1:** você confere os números de uma loja real (rodo a função na Hudson e numa loja do
  grupo de 3 e te mostro os 4 cards) — a "venda em risco por dia" é definição minha.

## Fase 2 — Componente de barras e menu

**T4. `views/barras_filtro.py` (CCv2)** — a partir do spike (`scratchpad/spike/app_spike.py`)
- Barras empilhadas e simples, eixo Y com 5 marcas, grade, valor em cima, legenda acima, rótulos
  do eixo X sem rotação, barras selecionadas cheias e as outras a 35%, foco visível, tooltip único.
- Clique / Ctrl+clique / ⌘+clique; **Enter, Espaço e Ctrl/⌘ + Enter/Espaço tratados no
  `keydown`** (o Chrome não gera `click` com Ctrl+Enter — achado do spike).
- Barras abaixo do zero (eixo estendido). Altura fixa (224 px).
- Devolve a seleção por `setTriggerValue`; o Python guarda em `st.session_state` e passa de volta
  em `data` (seleção "controlada"). Estado limpo ao trocar de loja.
- Fonte única das cores: tokens novos em `core/theme.py` (eficiente, excesso, parado, ruptura,
  rampa A/B/C), validados contra o fundo e entre si (skill dataviz / script de validação).
- Seletores novos em `SELETORES_STREAMLIT`; teste visual do componente.
- **Parada 2 (decisão do plano B):** primeiro teste do componente DENTRO do app real
  (tema, barra lateral, latência de cada clique com as consultas da barra). Se ficar lento ou
  quebrar, trocamos barras clicáveis por filtros, como você combinou.

**T5. Menu recolhível** — `app.py`, `core/theme.py`
- `SECOES`: o item pai "Dashboard" (botão que só abre/fecha, sem tela) e os filhos recuados.
  Pai destacado e aberto quando um filho está ativo. Estado aberto/fechado na sessão.
- CSS do filho recuado dentro da camada de componentes; teste visual: aberto e recolhido,
  1280 e 1920 px.

## Fase 3 — Tela "Estoque e Ruptura"

**T6. `views/dashboard_comum.py` + `views/dashboard_estoque.py`**
- Reusa o `_calcular` do Assistente (mesmo cache `st.cache_data`; não cria outro). Hoje ele é
  privado em `views/pedido.py`: expor uma função pública pequena que resolve loja → (linhas, meta)
  com a configuração da loja (personalização automática) e as correções de estoque negativo,
  sem mexer no comportamento do Assistente.
- Estados antes da tela: "Todas as lojas" (pede uma loja, não calcula), loja sem vínculo com o
  GPS, loja sem dados coletados, **loja sem vendas na janela** (vista nos dados reais), usuário
  sem loja liberada.
- Cabeçalho, chips de filtro, 4 cards + faixa de cobertura (modelo `.rmc-kpi` da Análise),
  ruptura por curva, estoque por linha, tabela "Produtos críticos" com `views/tabela.py`
  (25 linhas, paginação, ordenação pelo título, clique abre o detalhe do produto).
- Larguras, vãos, alturas e tipografia exatamente como a seção 7 da spec.
- Testes: `tests/visual` ganha as telas `dashboard_estoque` (1280/1920), com e sem barra clicada
  e com Ctrl+clique em duas; a loja fictícia ganha produtos em ruptura, excesso e parado e um
  estoque negativo. Mede vãos e alturas (±8 px), rolagem horizontal e o menu.
- Medição final da tela: 1ª abertura, clique no filtro, memória do processo (registrar em
  `status_e_pendencias.md`).
- **Parada 3:** você abre a tela com uma loja real e dá o aceite visual e dos números.

## Fase 4 — Arquivo da rede do Dashboard e tela "Compras"

**T7. Montagem do arquivo da rede só do Dashboard** — arquivo novo em `pedido/` (nome no momento,
sugestão `pedido/rede_dashboard.py`), **sem alterar** `pedido/rede.py` nem os arquivos da Análise
- Lê os arquivos por loja já existentes (`bruto/…/compras/{mes}.parquet`) e grava em prefixo
  próprio, por mês fechado, ordenado por EAN em blocos (leitura filtrada como a da Análise).
- Limpa com a lógica do Assistente (`pedido/calculo.py`): descarta quantidade ≤ 0, bonificação
  (< R$ 0,10) e linhas "a revisar"; leva a UF da loja (`lojas.uf` pela loja vinculada).
- **Script com prévia e `--executar`**, como `limpeza_compras_planilha.py` e `pedido.carga_catalogo`.
  A prévia mostra por mês: lojas, linhas lidas, linhas descartadas por motivo, tamanho do arquivo.
  Só grava depois do seu "executar". Execução à mão; a versão na rotina do GitHub fica para
  quando o workflow for publicado (hoje nem o job `rede` existe no `main`).
- Testes: pytest com compras fictícias em disco local (`ArmazenamentoLocal`): cada motivo de
  descarte, quantidade negativa, UF ausente, mês sem compras.
- **Parada 4:** você vê a prévia no seu ambiente e autoriza gravar no bucket.

**T8. Regra do "comprou mais barato"** em `core/dashboard.py` (função pura)
- Mesmo produto (EAN, ou genérico da Base Genéricos); mesma UF; só o período escolhido;
  melhor compra com quantidade ≥ `quantidade_comparavel` × a da loja; só quando a melhor é
  menor; só se ≥ `minimo_lojas_comparacao` lojas diferentes compraram o produto na UF.
- **Anonimato por construção:** a função devolve só preço, quantidade, data, distribuidor e UF da
  melhor compra, nunca empresa nem código de loja. Teste que garante que nenhuma coluna de
  identificação sai.
- Testes com casos de borda: 1 loja só, quantidade abaixo do limite, UF diferente, preço igual.

**T9. `views/dashboard_compras.py`**
- Seletor de período da Análise; 5 cards; compra × venda por mês (só os meses presentes nos dois
  lados; as vendas guardam 3); compras por linha (aproveitadas / excesso / sem demanda);
  tabela "Comprou mais barato" (25 linhas, duas linhas por célula) e "Compras a melhorar".
- Leitura do arquivo novo só para os EANs da loja, com cache por (loja, período, versão do arquivo).
- Testes visuais: `dashboard_compras` com compras da rede fictícias (estende
  `tests/visual/compras_rede.py`).
- **Parada 5:** aceite visual e dos números com uma loja real e a rede já montada.

## Fase 5 — Fechamento

**T10.** Rodar a suíte inteira e o teste visual (`--atualizar` só para as telas novas, com você
vendo as imagens); atualizar `CLAUDE.md` (mapa do código, regras e números medidos) e
`status_e_pendencias.md`; listar todos os arquivos alterados. Sem commit, sem push, sem publicar.

---

## Andamento

- **Fase 1 (cálculo e acesso): concluída em 21/10/2026.** Excesso = o que passa de N dias de venda
  (N = 60, por loja), régua própria do Dashboard; ruptura completa; catálogo IQVIA carregado no
  banco dev.
- **Fase 2 (componente e menu): construída em 21/10/2026; Parada 2 passou (26 de 26 no app real).**
  `views/barras_filtro.py`, `views/menu_lateral.py`, tokens `GRAFICO` validados, CSS do menu.
  Adiantado da T6, para o teste no app real: `views/dashboard_comum.py` (carregamento dos dados da
  loja com a configuração dela) e `views/pedido.calculo_da_loja`. A tela `views/dashboard.py` é
  PROVISÓRIA (dois gráficos e uma linha de texto) e a Fase 3 a substitui.

- **Fase 3 (tela Estoque e Ruptura): construída em 21/10/2026; falta a Parada 3** (o Mariano abrir com
  uma loja real e dar o aceite visual e dos números). `views/dashboard_estoque.py` (cards, dois gráficos,
  chips de filtro, tabela de 25 linhas com ordenação e pop-up de detalhe), `views/dashboard_compras.py`
  (só o aviso até a Fase 4), `views/dashboard_comum.py` (dados da loja e chips). A tela provisória
  `views/dashboard.py` foi apagada. Teste no app real 29 de 29; suíte 437 passando; teste visual com 6
  capturas novas do Dashboard. Ajuste de eixo: passo 1-2-2,5-5 (a barra de R$ 829 usava 41% da altura).

## Fase 3b — dividir Estoque e Ruptura e construir a página Estoque (22/10/2026)

Spec: `2026-10-22-dashboard-estoque-design.md`. A tela de hoje vira **Ruptura** (sem mudar o conteúdo); a
**Estoque** é nova. Cada tarefa termina com os testes passando; paradas para o Mariano nos pontos marcados.

**E1. Renomear a tela de hoje para Ruptura** — `core/acesso.py`, `app.py`, `views/`, `core/theme.py`,
`tests/visual/rodar.py`
- `SECOES_DO_PAINEL["dashboard"] = ("dashboard_estoque", "dashboard_ruptura", "dashboard_compras")` (a
  1ª é a tela inicial); o arquivo `views/dashboard_estoque.py` passa a `views/dashboard_ruptura.py` com a
  chave de tela `dashboard-ruptura`; as capturas `dashboard_estoque*` viram `dashboard_ruptura*`. Menu com
  três filhas. Os nomes de estado do filtro continuam (a Ruptura mantém os seus).
- `dashboard_estoque` fica por enquanto com um aviso "em construção" até E6. Testes de acesso e visuais.

**E2. Modelo de madrugada** — `pedido/estoque_modelo.py` (novo), `pedido/plano.py` (chaves), `pedido/coleta.py`
- Construtor puro `modelo(p: Pronto) -> (skus, vendas_dia, meta)`: universo estoque > 0; custo com as regras
  do Assistente (`calculo._compra_de_referencia`, `_ajustar_custo`, `_corrigir_acima_da_venda`) sobre cada
  código de produto, parâmetros do padrão; origem do custo (compra / cadastro / a revisar); última compra e
  última venda. Gravar/ler no Spaces com assinatura (como o `pronto`).
- Gancho em `_montar_prontos` (mesma passada, falha não derruba) e função de leitura que monta na hora se
  faltar (Q18 A).
- Testes com `ArmazenamentoLocal` (cada origem de custo, estoque negativo, SKU sem venda, assinatura que
  só refaz quando muda, falha do modelo não derruba o pronto).
- **Parada A:** rodo na loja 60 (somente leitura) e mostro os números dos cards para você conferir.

**E3. Parâmetro novo e cálculos de leitura** — `pedido/configuracao.py`, `views/config_pedido.py`,
`core/dashboard_estoque.py` (novo)
- `dias_parado` (padrão 90, por loja, validação 7 a 365) com os testes de configuração.
- Funções puras: classificação (parado, excesso, eficiente, compra recente), curva AA a CC (90 dias) e D,
  cobertura, cards, tabela por categoria (com total), estoque por linha, críticos (busca, situação, ordem),
  filtros E/OU. Testes com quadros pequenos à mão + conferência com a loja 60.

**E4. Tabela com linha selecionável e escurecimento** — `views/tabela.py`, `core/theme.py`
- Parâmetro opcional `selecionaveis`: clique/Ctrl+clique numa linha avisa o Python; as não selecionadas ficam
  a ~35% quando há seleção; a linha de total não é selecionável. Sem mudar as tabelas atuais. Teste visual.

**E5. Barras: escurecimento já existe** — só conferir que o comportamento (clique substitui, Ctrl+clique soma,
clicar de novo tira) é o mesmo da tabela, com teste.

**E6. A página Estoque** — `views/dashboard_estoque.py` (novo), `views/dashboard_comum.py`
- Cards, tabela por categoria, gráfico por linha, tabela de críticos com busca, situação, paginação e o
  pop-up de detalhe; estado de filtro próprio da página (limpo ao trocar de loja); sem chips.
- Testes no app real (como na Fase 3) e capturas novas no teste visual.
- **Parada B:** você abre com a loja 60 e dá o aceite visual e dos números.

**E7. Fechamento** — medição de tempo e memória, suíte completa, `CLAUDE.md` e `status_e_pendencias.md`.

Ordem: E1 → E2 → E3 → (Parada A) → E4 → E5 → E6 → (Parada B) → E7. E1 e E2 não dependem um do outro.

- **Fase 3b, E1 e E2 feitos em 22/10/2026.** E1: a tela de hoje virou `views/dashboard_ruptura.py` (conteúdo
  intacto), o menu tem Estoque (aviso), Ruptura e Compras, capturas renomeadas. E2: `pedido/estoque_modelo.py`
  (construtor, gravar/ler com assinatura, `obter` que monta na hora), `Chaves.estoque_modelo`,
  `pronto.assinatura_em_dia`, gancho em `coleta._montar_prontos` (relatório ganhou `modelos_montados`).
  Medido na loja 60: 16.413 SKUs, 1,3 s para construir, 667 KB + 118 KB; custo a revisar em 3,0% dos SKUs.
  **Falta a Parada A** (o Mariano conferir os números).
- **Fase 3b, E3 feito em 22/10/2026.** `dias_parado` (padrão 90, por loja, 7 a 365) e `core/dashboard_estoque.py`
  (parado, excesso, cobertura, curva AA a CC fixa em 90 dias e D, cards, tabela por categoria com total, estoque
  por linha, críticos com busca por todas as palavras e filtro de situação, filtros E/OU). Conferido com a loja 60:
  338 ms para preparar, 12 ms a tabela por categoria, 34 ms os críticos. A conferência achou um defeito do E2
  (custo de compra absurdo passava sem marca, ex. a Lixa a R$ 30,97 para venda de R$ 0,50): o modelo passou a
  aplicar a faixa de 15% a 100% do preço de venda a QUALQUER custo (versão 3 do modelo; 82 SKUs a mais "a revisar",
  todos erros de embalagem).

## Riscos e o que fazemos se acontecerem

| Risco | Onde aparece | Resposta |
|---|---|---|
| Ctrl+clique fica lento ou quebra no app real | Parada 2 | Plano B: filtros em vez de barras clicáveis |
| Memória do app estoura (≈ 12 MB por loja, limite da nuvem não confirmado) | Parada 3 | Reusar o cache do Assistente (já limitado a 30 lojas); se não bastar, reduzir colunas do DataFrame do Dashboard |
| "Venda em risco" e "parado" não fazem sentido para o lojista | Parada 1 | Ajustar a definição antes de desenhar a tela |
| Arquivo da rede com pouca cobertura de lojas (249 de 594 hoje) | Parada 4 | A comparação mostra só o que existe; aviso na tela com o nº de lojas na base |
| 99% das comparações em MG (190 de 247 lojas) | Parada 5 | Esperado; as outras UFs só terão a comparação quando houver ≥ 2 lojas |

## O que fica fora (já na spec)

Dotação orçamentária, perda em R$ por ruptura, visão da rede ("Todas as lojas"), soma de várias
lojas, histórico de estoque no tempo, pré-cálculo noturno, rotina do GitHub para o arquivo novo.

### Andamento — E4 a E6 (22/10/2026)
- **E4** (linhas selecionáveis em `views/tabela.py`) e **E5** (`barras_filtro.nova_selecao` compartilhada: tabela e barras filtram igual) prontos.
- **E6** pronta: `views/dashboard_estoque.py` (4 cards, análise por categoria com total, estoque por linha, críticos com busca/situação/ordem/25 por página e detalhe). Testes: `tests/test_dashboard_estoque_tela.py`; suíte 493 passando. Teste visual com 4 capturas novas (padrão, filtrado, críticos, detalhe) e vãos OK em 1280/1920. Para caber em 1280 px os títulos da tabela de críticos ficaram "Excesso (un.)" e "Cobertura" (a dica diz "dias"); os produtos fictícios ganharam nomes mais curtos.
- Próximo: **Parada B** (Mariano confere com a loja 60) → E7 (fechamento).
