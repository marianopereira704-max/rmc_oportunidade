# Classificação de produtos em cascata — design

Data: 25/10/2026. Origem: conversa com o Mariano (grill de 23–25/10/2026). Substitui a tabela "Linhas de compra" das
Configurações de Pedidos e a regra de reserva da Q36 A (`pedido/linhas.py`).

## 1. O que é e para quê

Hoje a classificação do produto está "solta": a IQVIA decide a linha de compra pelo setor, a base de categorias
(FEBRAFAR + CMED, com origem misturada) serve de reserva, e ninguém consegue ver de onde cada produto veio.

O objetivo é uma **organização em cascata** que mostre, de cima para baixo, como cada produto chega a uma de **5
categorias fixas**, e que seja a **mesma classificação** usada nas linhas de compra do Comprador e nos gráficos do
Dashboard.

Resultado visível: na seção de Configurações de Pedidos, 5 barras (uma por categoria). Cada barra tem uma setinha no
fim; ao clicar abre as categorias da IQVIA que a compõem; cada uma delas abre as categorias da FEBRAFAR; e cada uma
abre os tipos da CMED. Só aparece o que existe.

## 2. Decisões já tomadas

| # | Decisão |
|---|---|
| D1 | 5 categorias fixas: **Propagado, Genérico, Similar, Perfumaria e Em classificação** (o resto). O nome é "Propagado", não "Ético". |
| D2 | A categoria do produto sai da **IQVIA** primeiro; se a IQVIA não conhece, da **CMED** (só os tipos Genérico e Similar); senão **Em classificação**. |
| D3 | A **FEBRAFAR não classifica ninguém**, nem o MIP/OTC nem as demais categorias. Ela aparece só como informação de identificação na cascata. O MIP/OTC será analisado à parte, em outra etapa. |
| D4 | A cascata é **encadeada**: Categoria → IQVIA → FEBRAFAR → CMED. Um nível sem valor é pulado ("direto na FEBRAFAR" quando o produto não tem IQVIA). |
| D5 | A FEBRAFAR só aparece para produto que tenha categoria pela **IQVIA ou pela CMED**. Produto que só a FEBRAFAR conhece não é considerado na classificação. |
| D6 | O último nível mostra **só os tipos da CMED que classificam** (Genérico e Similar). Os outros tipos (Novo, Biológico, Específico, Fitoterápico, Radiofármaco, Dinamizado) não aparecem e o produto, se só a CMED o conhece, fica em Em classificação. |
| D7 | **Em classificação** também tem cascata: mostra a contagem de EANs e a lista da FEBRAFAR dos produtos que só ela conhece, para o admin enxergar o que falta. Ela não classifica, só mostra. |
| D8 | A correspondência (setor IQVIA → categoria, tipo CMED → categoria) é **editável pelo admin**, vale para **todas as lojas** e não tem personalização por loja. |
| D9 | A classificação **manual** do admin (planilha de categorias) continua passando por cima de tudo, quando a categoria manual tem correspondência clara (ver 4.3). |
| D10 | A mesma categoria vale no **Assistente (linhas do Comprador)** e no **Dashboard** ("Estoque por linha de compra"). Não existem duas classificações paralelas. |
| D11 | **Os dias de estoque não mudam**: seguem o grupo (medicamento/perfumaria) da base de categorias atual. |

## 3. Impacto medido (loja 60, 25/10/2026)

Estoque a custo por categoria, regra de hoje contra a regra nova (FEBRAFAR deixa de classificar):

| Categoria | Hoje | Regra nova |
|---|---|---|
| Perfumaria | 49,4% | 49,3% |
| Propagado | 22,1% | 22,1% |
| Genérico | 8,8% | 8,5% |
| Similar | 3,0% | 3,0% |
| Em classificação | 16,7% | 17,1% |

Motivo: a IQVIA já conhece mais de 98% dos EANs da FEBRAFAR (ex.: 89.943 de 90.375 em "Beleza pele cabelos"). A medição
não inclui o ganho da CMED (genéricos e similares fora da IQVIA), que só melhora o resultado. Existe 1 personalização
por loja gravada (loja 77) e ela não mexe nas linhas: nada se perde ao tirar a personalização de linhas.

## 4. Regras

### 4.1 Mapa de correspondência (padrão inicial)

IQVIA (SETOR_NEC) → categoria:

| Setor | Categoria |
|---|---|
| RX_GENERICO, MIP_GENERICO | Genérico |
| RX_PROMOVIDO, MIP_MARCA | Propagado |
| RX_TRADE, MIP_TRADE | Similar |
| NAO_MEDICAMENTO_PEC, PAC, NTR, OTC | Perfumaria |

CMED (tipo de produto) → categoria: **Genérico → Genérico**; **Similar → Similar**; qualquer outro tipo → Em
classificação. É o mesmo padrão de hoje (`PADRAO_POR_SETOR`), só renomeado e acrescido do mapa da CMED.

### 4.2 Categoria do produto

Para cada EAN, em ordem, vale o primeiro que existir:

1. categoria **manual** (4.3);
2. setor da **IQVIA** do EAN → categoria pelo mapa;
3. tipo da **CMED** do EAN → categoria pelo mapa (só Genérico e Similar resultam em categoria);
4. **Em classificação**.

Produto com vários EANs (genérico da Base Genéricos): vale o primeiro EAN que a IQVIA conhece, começando pelo
principal; se nenhum, o primeiro que a CMED classifica (mesma regra de hoje, `_setor_do_produto`).

### 4.3 Classificação manual

As categorias manuais usam os nomes da base (`categorias_ean`, origem MANUAL). Só têm correspondência clara:
Prescrição genérico → Genérico; Prescrição propagado → Propagado; Prescrição trade → Similar; categorias do grupo
perfumaria → Perfumaria. MIP/OTC e "Medicamento (CMED)" não sobrescrevem nada (D3).

### 4.4 A cascata (o que cada nível mostra)

- **Nível 1:** as 5 categorias, com o total de EANs.
- **Nível 2:** setores da IQVIA que o mapa leva àquela categoria, com o nome ("Genérico de prescrição"...) e a
  contagem de EANs. Em **Em classificação**, não existe nível 2.
- **Nível 3:** categorias da FEBRAFAR dos EANs daquele caminho. Quando o EAN não tem IQVIA, a FEBRAFAR vem direto
  abaixo da categoria, ao lado dos setores da IQVIA, sem grupo intermediário (D4). Só entra EAN que tenha categoria
  pela IQVIA ou pela CMED (D5);
  dentro de **Em classificação** entram os que só a FEBRAFAR conhece (D7).
- **Nível 4:** tipos da CMED que classificam (D6) dos EANs daquele caminho.
- Contagem = EANs distintos do caminho. Cada nível mostra só o que existe.

## 5. Dados

### 5.1 Nova tabela `classificacao_ean`

Migração **0019** (só acrescenta; nunca apaga nem renomeia): `ean` (PK, texto), `febrafar` (categoria oficial da
FEBRAFAR, nulo se não consta), `cmed_tipo` (tipo bruto da CMED, nulo se não consta), `atualizado_em`.

Por quê: hoje `categorias_ean` guarda só **uma** categoria por EAN (a vencedora da regra de junção). Quando a CMED
vence, a categoria da FEBRAFAR se perde, e o tipo original da CMED (Similar, Novo...) nunca foi gravado. A cascata
precisa dos dois por EAN. A IQVIA já está em `catalogo_iqvia`. A `categorias_ean` continua como está: ela decide os
dias de estoque (D11).

### 5.2 Carga

`python -m pedido.carga_categorias` (hoje FEBRAFAR + CMED) passa a gravar também o arquivo `classificacao_ean` no
Spaces. O botão de **Dados → Importar → Categorias de produtos** carrega no banco, como já é feito com a base e com o
catálogo IQVIA (COPY no Postgres; só acrescenta/atualiza). **Precisa recarregar as duas planilhas uma vez**, com
prévia (sem `--executar`) antes de gravar. As planilhas são de terceiros e nunca vão para o git.

### 5.3 Configuração

`ConfigPedido` ganha `linhas_por_cmed` (tipo CMED → categoria) ao lado de `linhas_por_setor`. Vale o padrão da 4.1; a
gravação segue o histórico atual de `configuracoes_pedido` (uma linha nova a cada gravação). `linhas_por_setor` e
`linhas_por_cmed` saem da personalização por loja (D8); valores já gravados em lojas ficam inertes.

## 6. Onde isso aparece

- **Configurações de Pedidos:** a seção "Linhas de compra" (tabela editável) é substituída por **"Classificação de
  produtos"**: a cascata (somente leitura) e um botão **"Editar correspondência"** que abre um pop-up "curto" com
  duas listas pequenas (setores da IQVIA e tipos da CMED), cada uma com uma escolha entre as 5 categorias.
- **Assistente (Comprador):** o corte por linha usa a nova classificação.
- **Dashboard (Estoque e Ruptura):** "Estoque por linha de compra" e o filtro por linha usam a nova classificação.
- **Cadastro:** o campo "Linhas de compra" lista as 5 categorias, com os rótulos novos. Os códigos gravados nos
  usuários (`genericos`, `similares`, `propagados`, `perfumaria`, `sem_linha`) **não mudam**; muda só o rótulo.

## 7. Implementação (esboço, o plano detalhado vem depois da aprovação)

1. `pedido/classificacao.py` (novo, puro): `categoria_do_ean`, `categoria_do_produto`, `arvore(...)` (agregação da
   cascata). Substitui `pedido/linhas.py::coluna/pela_categoria/_setor_do_produto`; `linhas.py` mantém só os
   códigos, rótulos e os setores. Testes com tabelas pequenas.
2. Migração 0019 + modelo `ClassificacaoEan` + `integrations/classificacao.py` (carga, leitura por versão).
3. `pedido/categorias.py` e `carga_categorias`: gerar `classificacao_ean`; botão de carga em Dados.
4. `ConfigPedido.linhas_por_cmed`; tirar linhas da personalização por loja.
5. Cache em `views/pedido.py` e `dashboard_comum`: o mapa EAN → categoria (hoje `_catalogo`, EAN → setor) passa a ser
   EAN → (origem, categoria), com a versão conferida a cada 5 minutos como já é.
6. Seção "Classificação de produtos" em `views/config_pedido.py` (cascata em `<details>` aninhados, sem rerun) e o
   pop-up de edição.
7. Rótulos (Cadastro, Dashboard, Assistente), dicas pela regra de `core/dicas.py`, teste visual e documentação.

## 8. Fora do escopo

- Análise do MIP/OTC da FEBRAFAR (etapa à parte, D3).
- Dias de estoque por categoria fixa (D11).
- Personalização por loja da classificação (D8).
- Novos catálogos da IQVIA (só existe o de 2022).

## 9. Riscos e pontos para confirmar na revisão

1. **Nome do resto:** usei **"Em classificação"** (sua primeira lista). Hoje existe também o grupo "Sem
   Classificação" do Assistente (produto sem categoria, com a tag e 7 dias estimados), que é outra coisa. Se quiser
   evitar a confusão, o resto pode se chamar "Sem linha", como hoje.
2. **Singular ou plural:** os rótulos que você escreveu são no singular (Propagado, Genérico, Similar); hoje o
   Cadastro e o Dashboard usam plural (Propagados, Genéricos, Similares). Vou usar o singular nos dois lugares.
3. **Tipos da CMED:** não conferi os tipos reais no seu arquivo (ele não está nesta máquina). A prévia da carga lista
   os tipos encontrados e quantos EANs cada um tem, antes de gravar.
4. **Desempenho:** a cascata junta ~250 mil EANs da IQVIA com ~300 mil da FEBRAFAR/CMED. A conta fica em cache por
   versão das duas tabelas (como o catálogo hoje) e será medida com o banco em Nova York.
5. **Pop-up de edição:** mudar o mapa muda a linha de todos os produtos e o que os Compradores veem. A gravação pede
   confirmação e entra no histórico de `configuracoes_pedido` (quem e quando).
