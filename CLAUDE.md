# RMC Oportunidades — guia do projeto

Atualizado em 24/09/2026. Estado detalhado das pendências: `status_e_pendencias.md`.

## O que o sistema faz

Mostra, loja a loja e produto a produto, quanto cada farmácia da rede deixou de
economizar comprando genéricos de outro fornecedor em vez da RMC. Cruza:

- **o que a loja pagou** — planilha de compras do **GPS** (exportada do BI);
- **o menor preço RMC** — tabelas de preço da **Gruppy**, por laboratório e por UF;
- **qual produto é qual** — a **Base Genéricos**, que liga cada EAN a um nome
  canônico de genérico (EANs diferentes do mesmo genérico somam juntos).

Economia perdida, por (loja, genérico), contra **um laboratório Gruppy por vez**
(escolhido no filtro principal): `(menor preço pago − preço do laboratório) × quantidade da compra vencedora`,
só quando positiva. Detalhes da regra em `core/analise.py`.
Nunca é gravada: é sempre calculada na hora.

Stack: Streamlit 1.62 + SQLAlchemy 2 + Alembic + Postgres (produção) / SQLite
(testes e dev) + DigitalOcean Spaces (arquivos) + pandas + rapidfuzz.

## Onde roda (importante antes de qualquer comando)

- **App publicado:** Streamlit Community Cloud, a partir do `main` do GitHub
  (`marianopereira704-max/rmc_oportunidade`). Publicar = push no `main`.
  Em 23/09/2026 o `main` ainda tem o código de 02/09; todo o trabalho posterior
  existe só na máquina local, sem commit.
- **Banco:** Postgres num droplet DigitalOcean de 1 GB RAM / 1 vCPU em NY. O
  firewall libera os IPs de saída do Streamlit Cloud (`core/config.py`,
  `core/monitoramento.py` avisa se mudarem). Latência daqui até ele: ~138 ms por
  ida e volta — **toda consulta dentro de laço vira minutos**.
- **Dois bancos no mesmo servidor Postgres** (descoberto em 24/09/2026):
  `rmc_oportunidades_dev` é o do `.streamlit/secrets.toml` **local**;
  `rmc_oportunidades` é o do **app publicado** (Secrets do Streamlit Cloud, com
  outro usuário — o usuário do dev não tem permissão nele). Tudo que foi enviado
  pelo app local (GPS, Gruppy, Base) está no banco **dev**, não no publicado.
- **`.streamlit/secrets.toml` local aponta para o banco DEV e o Spaces real.**
  `streamlit run app.py` local escreve no banco dev compartilhado — e
  `alembic`, `data.seed` ou qualquer script também. Para NÃO tocar em
  produção: `RMC_IGNORAR_SECRETS=1` (o `_get` de `core/config.py` pula o
  secrets.toml; sem outras variáveis, cai em SQLite `data/app.db` + disco
  local). **Atenção:** sob `streamlit run` isso NÃO basta — ao subir, o
  Streamlit copia as chaves do secrets.toml para as variáveis de ambiente,
  por cima das suas (medido em 24/09/2026). Para abrir o app sem tocar no
  banco/bucket reais, use `streamlit run app.py --secrets.files=<secrets de teste>`.
  `data.seed` apaga lojas/genéricos/compras e por isso se recusa a rodar fora de SQLite.
- **Migrações rodam sozinhas** no start do app (`core/db.py::_preparar_esquema`).
  Depois das migrações, `_criar_tabelas_faltantes` cria qualquer tabela do
  modelo que ainda não exista (só cria, nunca altera): um banco "adotado" pode
  ter ficado sem tabelas da revisão inicial — foi o que quebrou o login do
  admin no app publicado em 24/09/2026.
  Como o app antigo publicado e o app local usam o mesmo banco, migração nunca
  pode apagar/renomear coluna nem tabela enquanto o `main` estiver atrasado —
  só acrescentar ou afrouxar (ex.: NOT NULL → NULL). Última: `0014_pedido_avisos_vistos`. A `0010_origem_fila_loja_api` acrescenta valor ao enum `origemfila` com `ALTER TYPE … ADD VALUE` dentro da transação (Postgres 16; nada pode USAR o valor novo na mesma transação).
- **Bucket `consultoria-interna` é compartilhado** com outros sistemas (backups
  `.sql.gz`, outros projetos). Nunca apagar objetos por padrão de data/nome
  sem conferir. A chave do app não tem permissão de CORS (Get/PutBucketCors).

## Regras de trabalho

- **Nunca fazer commit, branch ou push.** O Mariano versiona manualmente. Ao
  terminar, listar os arquivos alterados.
- Não publicar nada até ele pedir.
- Exclusão em massa em produção (banco ou bucket): preparar script com modo
  prévia (`--executar` para aplicar) e deixar ele rodar — ver `limpeza_gps_regra_antiga.py`.
- Código, comentários, mensagens de tela e commits em português. O estilo do
  projeto é comentário/docstring explicando o PORQUÊ, com o bug ou a medição
  que motivou a decisão — manter essa densidade.
- Testes: `python -m pytest -q -p no:warnings` (352 passando em 05/10, ~35–60s).
  `tests/conftest.py` força storage local temporário em todo teste — sem ele,
  a suíte gravava arquivos no bucket real. Testes usam SQLite; primitivas
  que dependem do banco (ON CONFLICT) ficam em `core/sql.py` pra valer igual
  nos dois.
- Teste visual e de contrato (fora do pytest, precisa de Playwright):
  `python -m tests.visual.rodar` — sobe o app num SQLite com `data.seed` + uma loja fictícia do Pedido (`tests/visual/loja_ficticia.py`, 20 produtos inventados, data fixa 27/09/2026, com personalização),
  percorre as telas em 1280/1920 px, confere os seletores internos do
  Streamlit (`SELETORES_STREAMLIT`), os vãos iguais das tabelas, o fluxo do Assistente de pedido (pop-up de alerta, pílulas de parâmetros — a loja fictícia tem personalização —, lista e uma categoria filtrada) e compara as capturas com
  `tests/visual/referencia/`. Mudança visual de propósito:
  `--atualizar` e versionar as imagens novas. Rodar antes de atualizar o
  Streamlit (versão fixada em `requirements.txt`).

## Mapa do código

| Caminho | Papel |
|---|---|
| `app.py` | Entrada: `init_db`, login (cookie "lembrar-me"), sidebar, roteamento. |
| `core/config.py` | Toda configuração (secrets → env → default): banco, Spaces, sinônimos de coluna, limiares. |
| `core/models.py` | Modelos. `EanGenerico` é a ÚNICA junção EAN → genérico. |
| `core/db.py` | Engine/pool, `get_session` (commit/rollback), migrações no start, usuários fixos. |
| `core/analise.py` | Cálculo da oportunidade (menor preço, recuo de preço, bonificação, vencedor, preço médio, economia), filtros/ordenação em memória. |
| `core/queries.py` | Consultas de apoio: meses carregados, histórico mensal, listas dos filtros. |
| `views/tabela.py` | Tabela das telas de análise (CCv2): um grid só pra cabeçalho e linhas, ordenação pelo título, botão por linha, dica, estado vazio. Mudou o JS/CSS: reiniciar o `streamlit run`. |
| `views/loja_barra.py` | **Loja escolhida na barra lateral** (02/10/2026), vale pra todas as telas: grupo LOJA no topo da barra ("60 · RAZÃO SOCIAL", sem "cód." e sem "?"; ADM/Consultor com "Todas as lojas", Proprietário com várias lojas com "Todas" (só as dele), Comprador e quem tem 1 loja sem "Todas" — loja única vira texto fixo; "Ver todas as lojas" embaixo da caixa). Só na sessão (login ainda é único). No pé da barra, discreto: "Atualização dos dados · Pedido (GPS): dd/mm/aa às hh:mm (`gerado_em` do marcador de estoque da empresa; com Todas, a execução mais recente da rotina) · Análise (compras): até mmm/aaaa". Efeitos: Por Loja (só ADM) mostra sempre a rede, com a loja destacada (classe `destaque`); Por Produto com uma loja = só busca e período, card "Preço acima do laboratório" no lugar do de lojas, sem a loja embaixo do produto; com Todas, UF/Atendente/Grupo/lojas específicas limitados às lojas do usuário (`analise.Filtros.lojas_permitidas` corta o dado); Assistente sem faixa Loja (com Todas pede uma loja); Configurações → Personalização abre com a loja da barra. |
| `views/analise_comum.py` | Faixa do filtro Laboratório, filtros, cabeçalho ordenável, cache do resultado, formatação das células. |
| `core/rotinas.py` | `controle_rotinas`: rotina diária (`reivindicar`) e trava "um por vez" (`reivindicar_trava`/`liberar_trava`). |
| `core/sql.py` | Upsert atômico portátil (`insert_com_atualizacao`, `insert_ignorando_conflito`). |
| `integrations/gps.py` | Regra de compra, preparação, gravação por substituição, órfãos, filas. |
| `integrations/gps_processamento.py` | Processamento do GPS em thread, trava, tudo-ou-nada, estado pra tela. |
| `integrations/planilha_navegador.py` | Decodifica a planilha lida no navegador; lê o rodapé do BI. |
| `integrations/gruppy.py` | Tabela de preço por laboratório, vigência por UF. |
| `integrations/base_genericos.py` | Importação da planilha curada EAN → nome canônico. |
| `integrations/sistema_interno.py` | Sincronização de lojas (API interna, upsert em lote, diária). |
| `integrations/gps_api.py` | Cliente da API do GPS (empresas, lojas, vendas por loja, compras e estoque da empresa). Mapa da API e medições: `docs/mapa_api_gps.md`. |
| `pedido/` | Rotina de dados da aba Pedido (roda no GitHub Actions, fora do app): API do GPS → Spaces em Parquet (`pedido/bruto/…`). `plano` (o que falta — carga inicial e diária são o mesmo comando), `coleta` (corte de 8 min por consulta, fila de tentativas, orçamento de tempo), `vinculo` (loja GPS ↔ RMC), `transformar`, `armazenamento`. Passo a passo: `docs/rotina_pedido.md`. |
| `pedido/pronto.py` + `pedido/calculo.py` + `views/pedido.py` | Tela **Pedido**. `pronto` junta os ~60 brutos da loja em 3 arquivos (`pedido/pronto/{empresa}/{loja}/`, com assinatura; a rotina monta de madrugada, a tela monta o que faltar). `calculo` é função pura. Regras revistas em 29/09/2026: demanda/dia (dias sem dado saem da conta); máx. = ⌈demanda × dias⌉ (medicamento 7, perfumaria 15, **Sem Classificação 7 estimados**, com a tag); sugestão = máx. − estoque; só lista abaixo do máx.; **EAN negativo conta como 0** (não trava; tag + lista de correção); **Ruptura = estoque ≤ N un. (padrão 0)**, **Ruptura próxima = acima disso e cobertura ≤ D dias (3)**; giro baixo ≤ 1 un. em 90 dias; curva ABC 2 letras, **no mesmo período do giro baixo** (05/10/2026 — antes a janela inteira: o AAS Protect da Hudson, venda única de 80 un. fora dos 90 dias, saía AA e giro baixo; AA/AB/BA/BB em giro baixo caíram de 52/52/11/10 pra 0 nas 4 lojas). **Venda pontual (05/10/2026):** giro baixo com a 1ª letra da curva A ou B (vendeu 1 un., mas caro) ganha a tag verde "Venda pontual" (`selo-sucesso`) no lugar de "Giro baixo", aparece mesmo com "Ocultar giro baixo", vem DESMARCADO (marcar ou digitar a quantidade põe no pedido), sem tag/card Ruptura, no fim da ordem padrão; ~90–100 por loja, estoque zero. Liga/desliga em Configurações → Giro baixo (personalizável por loja). **Filtro Status** depois de Categoria (tags que existem na lista, OU): escolher "Giro baixo" desliga o interruptor, tirar religa, religar tira do filtro — o campo tem chave com versão (`pedido_status_<loja>_<n>`), porque escrever na chave dele de dentro do callback do interruptor deixava o campo vazio na escolha seguinte. **Preço no Pedido = última compra não bonificada** (a Análise continua no menor preço); "a revisar" = > 3× ou < 1/3 da mediana das compras da própria loja (o custo de cadastro do GPS bate com a última compra em só ~40–48% dos produtos). **Cadastro × nota (01/10/2026):** divergem mais de 2× → o preço de venda decide (custo coerente = 15–100% da venda): só um coerente → vale ele, tag "custo ajustado"; os dois/nenhum → nota + "a revisar"; sem compra e cadastro fora da faixa → "a revisar" (`revisar_motivo` diz por quê). Hudson: resolveu 9 dos 10 que divergiam; "a revisar" listados 1 → 26 (cadastro com fração errada, ex. Sonrisal R$ 0,05 vendido a R$ 3,00). **Acima do preço de venda (02/10/2026):** se depois disso o preço ainda passa do preço de venda, vale a nota paga mais recente dentro da faixa coerente, "A revisar" com motivo `acima_venda` (Hudson: 9 correções, ex. Kinder Joy R$ 128,11 → R$ 8,01; versões mais largas erraram casos certos). Coluna **Venda 90 dias** (total, fixo em 90) no lugar de Demanda/dia — só exibição, a sugestão segue a demanda da janela. Caso real: seringa com caixa de 100 lançada sem fração (R$ 79,90/un.) inflava o orçamento da Hudson em ~R$ 4,7 mil na regra antiga. Genéricos da Base Genéricos viram uma linha. **Assistente de pedido** (nome na tela desde 01/10/2026; por dentro continua "pedido"). Ao abrir a loja, em ordem: pop-up Estoque negativo (corrigir manualmente / considerar todos zero) → pop-up Sem classificação (informativo) — cada um UMA vez por foto do GPS por usuário+loja (`pedido_avisos_vistos`) → loja com personalização: pílulas "Personalizado desta loja"/"Padrão", nenhuma marcada, lista só depois da escolha. Topo (02/10/2026): a faixa Loja vem PRIMEIRO; abaixo, só depois da escolha, "Assistente de pedido" + data, sem botões — o "Estoque negativo" saiu (corrigir é pelo pop-up) e "Listas" virou botão do rodapé ("Listas (N)", pop-up com abas "Salvar como lista" e "Listas salvas"). Cards (pedido inteiro): Orçamento (unidades no subtítulo), Itens com sugestão, Ruptura, Sem classificação (card inteiro clicável → "Saiba mais": botão transparente por cima, `st-key-pedido_saiba_mais`). Filtros SEM pop-up, numa linha acima da tabela: busca, Categoria, Fabricante (= laboratório), Ocultar giro baixo. Ordem padrão: "a revisar" primeiro, depois valor vendido. Lapidação de 02/10/2026: "A revisar" é tag do Status (a 1ª, contorno âmbar, `selo-revisar`) e a linha só ganha a barra âmbar à esquerda (sem fundo); a Última compra mostra preço e data, "· cadastro"/"· bonificado depois" em texto miúdo e o "custo ajustado" na dica; quantidades centralizadas (`ColunaTabela(centro=True)`), dinheiro à direita; divisória das linhas #EEF0F3 (`--borda-linha`); ✓ discreto no "Salvo automaticamente"; lupa na busca, lixeira no Descartar. **Desempenho (02/10/2026):** o que a tela lê do banco sobre a loja (correções, área de trabalho, listas, avisos, avisos vistos, personalização) fica na sessão (`_EstadoLoja`, relido a cada 30 s ou depois de uma ação — `_concluir`/`_invalidar_estado`); a área é atualizada na cópia depois de gravar (`pedido_area.com_quantidade`/`com_marcacoes`, testadas contra o banco). Medido: clique de checkbox 11 → 3 consultas (só a gravação), trocar de página 8 → 0. O cálculo da loja tem como chave o marcador da foto de estoque (`loja_barra.marcador_estoque`, 5 min) e expira em 1 h, em vez de recalcular a cada 5 min. A base de categorias do Assistente carrega sem a origem (1.470 → 825 ms). Limpeza do mesmo dia: a classe `Filtros` do pop-up, as pílulas, `contar_opcoes`, `aplicar_rascunho`, `tolerancia_preco` e as funções do rascunho da Fase 5 foram apagadas; `filtrar(busca, categorias, fabricantes)` e `opcoes_filtros`. Textos de 02/10/2026: "Mín./Ideal" (e "ideal" na exportação e nas Configurações); dicas com parágrafos (`

`, `white-space: pre-line` na dica da tabela), frases curtas e "sistema de vendas" no lugar de "GPS" ("PDV" onde não cabe, ex. "PDV: -1") — só nas dicas; o "· cadastro" virou "-"; mensagens do Assistente viram notificação (`st.toast`, 5 s, ✅/❌/⚠️/ℹ️ por tipo; avisos de estado uma vez por loja na sessão); pop-up Listas na largura média, loja como "RAZÃO - CNPJ". Barra lateral: inativos sem contorno (bloco isolado "BARRA LATERAL SEM CONTORNO" em core/theme.py — apagar o bloco volta ao visual anterior). Rodapé fixo (`st-key-rodape-pedido`, sticky na camada `envoltorio_layout`). Em 1280 px a tabela cabe no limite (nome do produto quebra dentro da palavra, classe `quebra`). |
| `pedido/configuracao.py` + `integrations/configuracao_pedido.py` + `views/config_pedido.py` | **Configurações de Pedidos** (só admin, sidebar em Administração): dias de estoque por grupo e por categoria, piso do máximo, ruptura, giro baixo, curva ABC, janela de meses, faixa de custo coerente. Duas seções (01/10/2026): **Padrão de todas as lojas** e **Personalização por loja** (`personalizacoes_pedido_loja`: só o que difere do padrão — `diferencas`/`aplicar`; o resto segue o padrão mesmo se ele mudar; "Copiar personalização de…" copia, não liga; "Remover" grava `{}`; janela de meses fica de fora; a loja não edita nada). A tela Pedido ainda usa só o padrão: a escolha Padrão × Personalizado (pílulas) é a Etapa 2 do Assistente de pedido. Cada gravação é uma linha nova em `configuracoes_pedido` (histórico com quem/quando); a vigente é a de maior id; sem nenhuma, valem os padrões de `settings.pedido`. A janela também vai pro Spaces (`pedido/controle/configuracao.json`) porque a rotina roda sem banco. Botões da sidebar no teste visual: pela chave `st-key-nav_<seção>` — o nome acessível inclui o ícone e "Pedido" casava com "Configurações de Pedidos". |
| `integrations/pedido_area.py` | Pedido (29/09/2026): **área de trabalho por usuário + loja** (`pedido_area`: só o que difere do padrão — quantidade digitada, marcação feita à mão; grava a cada clique, volta igual no reload) e **listas com nome** (`pedido_listas`, visíveis pra todos, com autor; "Abrir" traz pra área; exportar a partir dela apaga). Avisos contra pedido duplicado (lista de outra pessoa, exportação de hoje de outra pessoa). Substitui o rascunho da loja da Fase 5 (`rascunhos_pedido` fica no banco sem uso). |
| ~~`integrations/pedido_filtros.py`~~ | Apagado em 01/10/2026 com o pop-up de filtros (Assistente de pedido). A tabela `pedido_filtro_salvo` fica no banco sem uso (migração não apaga tabela).
| `integrations/pedido_rascunho.py` | Correções de estoque negativo (`correcoes_estoque`; só valem enquanto o sistema de vendas continuar mostrando negativo) e registro das exportações (`exportacoes_pedido`), com usuário e data/hora. O nome é da Fase 5: o "rascunho da loja" virou a área de trabalho (`pedido_area.py`) e as funções dele foram apagadas em 02/10/2026 (a tabela `rascunhos_pedido` e o modelo ficam: o teste de migrações compara modelo × tabela). Exportação: ver o pop-up "Exportar" (formato Gruppy, tabela completa, só giro baixo). |
| `core/acesso.py` | Níveis de acesso (revistos em 02/10/2026): ADM (tudo; Por Loja é só dele), CONSULTOR (tudo menos Administração e Por Loja; começa na Por Produto), COMPRADOR/PROPRIETARIO (Por Produto e Assistente das lojas em `usuarios_lojas`; sem loja ligada, nenhuma; "Gerente" virou "Comprador" em 01/10/2026 — `GERENTE` gravado vale como Comprador). Nível em `usuarios.nivel` (texto, não enum do Postgres); vazio = deduzido do papel do login único (admin/consultor). O login individual (`usuarios.login`) é a próxima etapa. |
| `integrations/fila_loja.py` | Fila de EAN origem **Loja (API)**: genérico (pela base de categorias) VENDIDO nas lojas (catálogo da empresa, `lojas_com_venda`, gravado pela rotina) e fora da Base Genéricos. Valor = vendido na janela, regravado (não soma); fica fora de `recalcular_valores_fila_ean`; nunca resolve sozinho, nem no reprocessamento. Roda 1×/dia na primeira abertura da aba Dados (`ROTINA_FILA_EAN_LOJA_API`) e pelo botão na fila. Teste real (Hudson + 3 Reis): 1.398 genéricos vendidos, 1.328 já na base, 26 novos. |
| `integrations/gps_vinculo.py` | Vínculo loja GPS ↔ loja RMC no banco (`vinculos_loja_gps`); decisão humana nunca é desfeita pelo recálculo. Tela: Dados → Vínculo de lojas GPS. |
| `pedido/categorias.py` + `integrations/categorias.py` | Base de categorias do Pedido (`categorias_ean`, EAN → categoria → grupo medicamento/perfumaria/Sem Classificação). Nasce com FEBRAFAR + CMED (`python -m pedido.carga_categorias` monta o arquivo no Spaces; o botão em Dados → Categorias de produtos carrega no banco — o secrets local só alcança o banco dev). Categoria MANUAL nunca é sobrescrita. A planilha FEBRAFAR é de terceiro: nunca no git. Chave do EAN sem zeros à esquerda (`chave_ean`). |
| `.github/workflows/rotina_pedido.yml` | Agendamento da rotina do Pedido: 00:00 de Brasília, 4 partes paralelas, disparo manual permitido. Chaves em Secrets do GitHub (repositório público: nada de dado em log). |
| `integrations/gps_cache_orfaos.py` | Legado: atalho de órfãos em arquivo. Não é mais escrito; só usado pra purgar em exclusão de envios antigos. |
| `reconciliation/motor.py` | Fuzzy-match EAN → genérico, filas de EAN, reprocessamento. |
| `storage/filesystem.py` | Explorador de Arquivos virtual (FSNode) sobre Spaces/disco; nada se apaga, "inativar" move pra `_Inativos`. |
| `views/leitor_planilha.py` | Componente (CCv2) que lê o .xlsx NO NAVEGADOR. |
| `views/dados.py` | Aba Dados (só admin), organizada pelo que o admin está fazendo (Q35, 29/09/2026): **Pendências** (seletor com contadores: Fila de EAN, CNPJ órfão, Vínculo de lojas GPS, Sem Classificação) · **Importar** (Base Genéricos, Gruppy, GPS, Categorias) · **Rotinas** (sincronização de lojas, rotina do GPS com o último relatório de cada parte, listas FEBRAFAR/CMED) · **Explorador**. Todo item começa com "O que é / De onde vem / Como alterar" (`_explicacao`). Aviso de reorganização até 31/10/2026. |
| `core/theme.py` | CSS em camadas: 1 tokens (cores, escala de texto 28/20/16/14/13/12, espaçamento 4–32, raios); 2 adaptação do Streamlit — única camada com seletor interno, sempre via `SELETORES_STREAMLIT`; 3 componentes `.rmc-*`/`st-key-*`; 4 telas (`theme.tela("nome")` → `st-key-tela-nome`). |
| `.streamlit/config.toml` | Tema oficial (primaryColor navy, fonte base 14, raios) e `toolbarMode = "viewer"`. O que der pra resolver aqui não vai pra CSS. Largura máxima do conteúdo: `LARGURA_MAXIMA_PX` (1220). |

## Perfis

Login único pelo CNPJ da rede; a senha define o papel. Os níveis de acesso (Gerente/Proprietário só com o Pedido da própria loja) já existem em `core/acesso.py`, esperando o login individual. **Admin** vê tudo,
inclusive a aba **Dados** e **Configurações de Pedidos**. **Consultor** vê Análise de Oportunidade, Assistente de pedido e
Dashboard (Dashboard ainda é tela de "próxima fase"; o Pedido existe desde 28/09/2026, sem edição/exportação — Fase 5).

## Como os dados entram — passo a passo

Tudo fica em **Dados → Importar** (admin); a base de lojas é sincronizada em **Dados → Rotinas**. Ordem recomendada: lojas →
Base Genéricos → Gruppy → GPS → filas. A ordem não é obrigatória (nada se
perde), mas seguir ela evita trabalho manual nas filas.

### 1. Base de lojas (automática)

- Roda sozinha na primeira abertura da aba Dados de cada dia (API do sistema
  interno); falha vira pop-up, os dados anteriores continuam.
- Botão **Forçar sincronização agora** para rodar na hora.
- Chave da loja: CNPJ. É contra ela que o GPS casa cada compra.

### 2. Base Genéricos (planilha curada por gente)

1. **Selecionar planilha Base Genéricos (.xlsx)** — o navegador lê o arquivo.
2. Aparece "*arquivo* — N linhas lidas no seu navegador".
3. **Processar planilha Base Genéricos**.

Regras: colunas `EAN` e descrição (`DESCRIÇÃO MARCOS`/`descricao`/`produto`);
demais colunas ignoradas. A descrição JÁ É o nome canônico (sem fuzzy-match).
Mesma descrição = mesmo genérico. EAN que já tem resolução nunca é
sobrescrito (só "pulado"). Vínculos ficam com origem `IMPORTADA`.
**Trava de planilha errada:** planilha com coluna de preço, custo, desconto,
quantidade ou CNPJ é recusada na Base Genéricos (é Gruppy ou GPS — em
24/09/2026 uma tabela Gruppy subiu aqui e virou 22 genéricos falsos, já
removidos). Termos em `COLUNAS_PROIBIDAS_BASE_GENERICOS`. Da mesma forma, a
Gruppy recusa planilha com coluna de CNPJ (é GPS), e o GPS recusa envio sem
nenhuma linha de compra.
**Depois de importar**, vá na Fila de Resolução de EAN e use **Reprocessar fila
contra a base atual** — resolve de uma vez os itens pendentes cujo EAN acabou de
entrar na base.

### 3. Tabela de preços Gruppy (uma por laboratório)

1. Escolha o **Laboratório** (existente ou digite um novo).
2. Escolha as **UFs cobertas por esta tabela** (a tributação muda por UF, por
   isso a cobertura é informada por quem envia — não vem da planilha).
3. Escolha **como a planilha traz o custo**: custo líquido pronto, ou preço
   bruto + % de desconto (custo = bruto × (1 − desconto); desconto aceito em
   fração 0,15 ou percentual 15).
4. **Selecionar planilha Gruppy (.xlsx)** → **Processar planilha Gruppy** →
   popup de mapeamento de colunas (sugestão = último mapeamento confirmado,
   senão sinônimo) → **Confirmar e processar**.

Vigência por UF: a tabela nova do MESMO laboratório inativa só as UFs que se
repetem; UFs que a antiga cobria e a nova não continuam valendo pela antiga.
Hoje só MG tem cobertura — lojas de outras UFs não aparecem nas análises até
subir tabela para a UF delas.

### 4. Compras GPS (planilha do BI, uma ou mais por mês)

**Exportação no BI:** o Power BI corta em 150 mil linhas (.xlsx) / 30 mil
(.csv), e só ~40% das linhas são compra (o resto é linha só de venda). Divida
a exportação **por atributo de loja** (UF, cidade, grupo) — nunca por produto
— para cada arquivo ficar abaixo do limite. Motivo: o envio substitui os
CNPJs presentes no arquivo; dois arquivos com a mesma loja no mesmo mês
fariam o segundo apagar o primeiro.

Na tela:

1. Escolha **Mês de referência** e **Ano**.
2. **Selecionar planilha GPS (.xlsx)** — o navegador lê (≈15 s para 150 mil
   linhas) e mostra "N linhas lidas no seu navegador".
3. **Processar planilha GPS** → popup:
   - se o rodapé do arquivo indicar outro mês (`AnoMesFiltro`): erro vermelho +
     caixa **"Confirmo que este arquivo é de …"** obrigatória;
   - se o rodapé disser que a exportação foi cortada: aviso amarelo (não bloqueia);
   - se o mês já tem envios: aviso de que os CNPJs do arquivo serão substituídos;
   - mapeamento de colunas: CNPJ, EAN, Descrição, **Quantidade comprada**
     (`Quantidade` — NÃO `QTD`, que é a vendida) e **Valor unitário sem ST**
     (`VlrUnitario`); laboratório e razão social são opcionais e automáticos;
   - prévia: "N compras de M CNPJs serão gravadas… Ignoradas: …".
4. **Confirmar e processar**. O popup fecha e aparece a barra de progresso.
   **Pode fechar a aba**: o processamento continua em segundo plano. O
   resultado fica na tela ("Ok, entendi") e em "Último envio concluído".

Regras do GPS (decididas em 09/2026):

- Linha válida = `VlrUnitario > 0` e `Quantidade > 0`. Custo unitário =
  `VlrUnitario` (sem ST, comparável à Gruppy, que não tem imposto). Linhas só
  de venda, com quantidade/valor ≤ 0 ou sem CNPJ/EAN (rodapé, total) são
  ignoradas e contadas na mensagem. Estoque não é mais usado.
- Mesmo (loja, EAN) repetido no arquivo: soma a quantidade, custo = média
  ponderada pela quantidade.
- **Substituição por (mês, CNPJ):** o envio apaga e regrava, naquele mês, as
  compras de todos os CNPJs que aparecem no arquivo (inclusive os que só
  tinham linha de venda — ficam sem compra no mês). CNPJ ausente do arquivo não
  é tocado. Outros meses não são tocados.
- **Tudo ou nada:** arquivo no Explorador, registro do envio, substituição e
  filas numa transação só. Falhou em qualquer ponto → nada muda, o original
  enviado é apagado, a trava é liberada, e o erro aparece na tela.
- **Um processamento por vez** no sistema inteiro (trava `upload_gps`, renovada
  enquanto o envio está vivo; expira sozinha em 5 min se o processo morrer ou o
  app for parado no meio).
- Para desfazer um envio: Explorador de Arquivos → o arquivo → **Excluir
  definitivamente** (apaga as compras que ainda pertencem a ele e recalcula as filas).

Por que a planilha é lida no navegador: o parse do .xlsx no servidor custava
25 s e centenas de MB, e derrubava o processo por falta de memória. O navegador
converte para CSV compactado (~5 MB) e o servidor só decodifica (~0,5 s). O
original vai direto ao Spaces por URL assinada; se o bucket recusar (sem
regra de CORS), o original segue pelo app — funciona igual.

## As filas (Dados → Pendências)

### Fila de CNPJ Órfão — só existe para o GPS

CNPJ que aparece numa compra do GPS e não bate com nenhuma loja cadastrada.

- As compras dele NÃO se perdem: ficam em `compras_gps_orfas`, com a mesma
  regra de substituição por (mês, CNPJ).
- Valor e ocorrências da fila = soma das compras órfãs vigentes (recalculado a
  cada envio, nunca somado — reenviar não infla).
- **Vincular à loja**: move na hora as compras do CNPJ para a loja escolhida e
  o vínculo passa a valer para os próximos envios (o CNPJ não volta a ser órfão).
- **Resolver automaticamente (CNPJ idêntico)**: resolve de uma vez os órfãos
  cujos dígitos batem com uma loja que entrou na base depois do envio.
- **Ignorar**: some da fila; as compras continuam guardadas como órfãs.

### Fila de Resolução de EAN — mesma fila para Gruppy e GPS

Um EAN que ainda não está ligado a nenhum genérico em `EanGenerico`. **Uma
linha por EAN**, qualquer que seja a origem. Como o EAN é resolvido uma vez e
vale para os dois lados, resolver um item conserta Gruppy e GPS ao mesmo tempo.

Como um EAN entra: no upload, a descrição é comparada (rapidfuzz, WRatio)
com os genéricos ativos:

- score ≥ 92 (`RECON_LIMIAR_AUTO`): resolvido automaticamente (origem `AUTOMATICA`), não vai pra fila;
- 75 ≤ score < 92 (`RECON_LIMIAR_MEDIA`): entra na fila **com sugestão** (é só confirmar);
- score < 75: entra na fila **sem sugestão** (vincular manualmente ou cadastrar genérico novo).

Diferença entre as origens:

| | Origem **GPS** | Origem **GRUPPY** | Origem **Loja (API)** |
|---|---|---|---|
| De onde vem | EAN de uma compra | EAN de uma tabela de preço | genérico vendido nas lojas (API do GPS) e fora da Base Genéricos |
| O que falta enquanto não resolve | a compra não entra na análise (sem genérico, não há com o que comparar) | o preço não entra no "menor preço RMC" (oportunidade some ou sai subestimada) | no Pedido, o EAN não soma com os outros EANs do genérico |
| Valor na fila | quantidade × VlrUnitario de todas as compras vigentes com esse EAN (normais + órfãs) | 0 ao entrar (tabela de preço é catálogo, não dinheiro gasto); passa a ter valor se/quando o EAN aparecer em compras do GPS | valor VENDIDO nas lojas na janela; qtd = nº de lojas |
| Quando o valor é recalculado | a cada envio GPS, exclusão de envio e vínculo de CNPJ órfão | idem (o recálculo cobre TODOS os pendentes); o upload da Gruppy em si não recalcula | 1×/dia (primeira abertura de Dados) ou pelo botão; nunca resolve sozinho |

- `origem` registra onde o EAN foi visto primeiro; se o mesmo EAN depois
  aparece no outro lado, continua sendo UM item (a origem não muda, o valor sim).
- Prioridade da lista: **valor de compra em jogo** (maior primeiro) — o
  critério antigo "aparece em estoque" saiu junto com o estoque. A tela mostra
  até 200 itens; o filtro **Origem** separa GPS/Gruppy/Loja (API). Com
  **Loja (API)** a ordem é de pareto pelo nº de lojas que vendem, com o "%
  acumulado" no título de cada item (02/10/2026). O relatório **Sem
  Classificação** segue o mesmo pareto (coluna LOJAS COM VENDA + % ACUMULADO).
- Visual padronizado em 02/10/2026: texto digitado nos campos 14 px no app
  inteiro (era 12,25 — `campo_entrada`), etiquetas do multiselect como chips
  (`etiqueta_multiselect`), botões do topo/rodapé do Assistente com 40 px e,
  no rodapé, só "Exportar" azul-marinho cheio.
- Ações por item: **Confirmar sugestão**, **Vincular** a um genérico existente,
  **Cadastrar e vincular** como genérico novo, **Ignorar**.
- **Reprocessar fila contra a base atual**: limpa EAN sujo (sufixo ".0"),
  resolve na hora quem já tem EAN exato na Base Genéricos, refaz o fuzzy-match
  dos demais (≥ 92 resolve, 75–92 atualiza sugestão).
- Os itens das filas e as abas da tela Dados só são montados quando abertos
  (`on_change="rerun"` + `.open`) — montar tudo a cada clique travava a tela.

## Análise de Oportunidade (telas do consultor) — regra de 24/09/2026

**Filtro Laboratório é obrigatório** (faixa acima dos filtros). Sem ele, nada
aparece e os demais filtros ficam desabilitados. A análise é sempre contra
UM laboratório Gruppy; o cabeçalho da coluna de preço é o nome dele (o que
foi digitado no upload da Gruppy). Só entram genéricos da tabela dele e lojas
de UF que ela cobre. A escolha fica lembrada entre Por Loja e Por Produto.

Layout (redesenho de 24–25/09/2026, etapas 0–5): conteúdo com largura
máxima de 1220 px (`LARGURA_MAXIMA_PX`), todas as seções com as mesmas bordas
e 16 px entre elas (28 px dos cards até os
rótulos dos filtros); sem cabeçalho de página (a tela abre na faixa —
pedido de 25/09/2026); faixa Laboratório de 76 px com ícone de frasco. Nas
telas de análise o `margin-bottom: -1rem` do Markdown é zerado — sem isso os
vãos visíveis ficavam 14 px menores que os do CSS. Sidebar: logo + nome, "usuário | papel", divisória entre
os grupos e antes do Sair, 8 px entre botões, ícone e texto à esquerda.
Streamlit 1.62 põe `margin-bottom: -1rem` em todo Markdown e centraliza o
conteúdo do botão num `<div>` interno — os dois estão na lista
`SELETORES_STREAMLIT` (`markdown`, `botao_conteudo`).

Cards de indicadores (modelo do print de 25/09/2026: ícone num círculo,
TÍTULO em maiúsculas em cima, número e subtítulo; fundo tingido — azul,
verde na Economia, roxo em Produtos; com card estreito, < 250 px, o ícone
sobe e o título reserva 2 linhas pros números alinharem. Ficam entre a faixa
Laboratório e os filtros e seguem todos os filtros): **Por Loja** — Lojas com oportunidade (de X analisadas) |
Economia potencial | Produtos com oportunidade (de X analisados) | Economia
média por loja; **Por Produto** — os mesmos, abrindo por Produtos e com
Economia média por produto. "Produto" = genérico distinto; "com
oportunidade" = economia > 0. Seta de tendência (verde sobe, âmbar desce)
contra os N meses de CALENDÁRIO imediatamente anteriores, com os mesmos
filtros — só aparece se todos esses meses estiverem carregados
(`analise.meses_periodo_anterior`); a dica diz com quais meses comparou.
Os cards de status da aba Dados também usam `.rmc-kpi`: estilo novo de card
vai em `.rmc-kpis .rmc-kpi`, nunca no `.rmc-kpi` sozinho.

Filtros (linha única): busca sem rótulo à vista, com texto de exemplo (acha
razão social, CNPJ — com ou sem pontuação —, produto, laboratório e a
descrição do GPS) | UF | Atendente comercial | Grupo econômico | Período sem
rótulo ("Último mês", "Últimos 2/3/6 meses" — `PERIODOS_MESES_OPCOES`), e o
"Selecionar lojas específicas" embaixo. Sem "filtros avançados" (decidido em
24/09/2026). Os rótulos escondidos usam `label_visibility="hidden"` pra os
cinco campos ficarem na mesma altura.

Para cada (loja, genérico) no período (N últimos `ano_mes` carregados):
1. **Bonificação** (preço < R$ 0,10, `ANALISE_LIMITE_BONIFICACAO`) sai de tudo; só é contada.
2. **Preço efetivo** de cada compra = o primeiro a até ±50% (`ANALISE_TOLERANCIA_PRECO`)
   do preço do laboratório: `VlrUnitario` → `Fat × %CMV ÷ QTD` (custo CMV,
   calculado no upload) → `R$ Custo médio`. Nenhum coerente: fica o
   `VlrUnitario` marcado **"a revisar"** (erro de cadastro), com **economia zero**.
   Preço vindo de recuo aparece com o marcador **"ajustado"** (dica mostra o VlrUnitario).
3. **Vencedor** = compra de menor preço efetivo. "A revisar" só vence se não
   houver nenhuma coerente. Empate: soma as linhas e junta os laboratórios.
4. **Qtd.** = só a quantidade da(s) linha(s) vencedora(s) (mesmo com período de
   vários meses). **Diferença un.** = preço pago − laboratório, com sinal
   (negativo em vermelho). **Economia** = diferença positiva × Qtd.
5. **Preço médio (3m)** = média ponderada dos preços efetivos, todos os
   laboratórios, sem bonificação e sem "a revisar", nos 3 últimos meses
   carregados (`ANALISE_MESES_PRECO_MEDIO`). Mostra "(1m)/(2m)" enquanto houver
   menos meses. Só informativo.

Colunas: **Por Produto** — Produto (loja embaixo) | Laboratório (da compra, GPS) |
Preço pago | *laboratório escolhido* | Diferença un. | Qtd. | Preço médio (3m) |
Economia. **Por Loja** (tabela `views/tabela.py`, desde 25/09/2026) — Loja
(razão social em destaque, CNPJ embaixo) | Localização (Cidade/UF, ícone de pino) |
Responsável (ícone de pessoa; 3 linhas em ordem fixa: consultor interno, consultor farma,
atendente comercial; nome + sobrenome, pulando "de/da/dos" — o nome inteiro
fica na dica; vazio = "—"; "?" no título explica a ordem) | Produtos |
Economia | botão de seta ("Ver detalhes"), que abre a tabela de produtos da
loja com as colunas do Por Produto (sem a loja embaixo). **Por Produto** e os
dois Detalhes usam a mesma tabela (`analise_comum.colunas_produto`/`linha_produto`);
os Detalhes mostram os três responsáveis com nome inteiro e papel. Sem
resultado: "Nenhuma oportunidade encontrada / Tente alterar os filtros".

**Largura das colunas (regra de 25/09/2026):** cada coluna tem a largura do
próprio conteúdo (`auto`; texto longo em `fit-content(Npx)` — Loja 340,
Produto 210 — que quebra linha acima disso) e a sobra vira vãos IGUAIS
entre todas as colunas (grid + subgrid, `justify-content: space-between`).
Nunca largura fixa ou em pesos (`fr`): foi o que deixou um buraco entre
Responsável e Produtos. O teste visual mede o vão VISÍVEL entre as colunas de
toda tabela (±8 px) e falha também se um título quebrar linha.

**Ordenação:** clicar no título de qualquer coluna (inclusive no Detalhes);
de novo inverte. Vazios sempre no fim. Padrão: Economia decrescente.

**Desempenho:** uma consulta ao banco por (laboratório, período, versão dos
dados), guardada em memória (`views/analise_comum.py`); ordenar, paginar,
buscar e abrir detalhe não vão ao banco. Medido com agosto real (63 mil
compras): cálculo ~0,3 s, ordenar/filtrar em memória ~5 ms. A aba Dados limpa
esse cache (toda ação que muda dados está lá); além disso a versão dos dados é
conferida a cada 30 s. O histórico mensal do Detalhes do produto
(`historico_compras`) ainda mostra a média do VlrUnitario por mês, sem recuo.

## Mudanças de 22–23/09/2026 (resumo)

- GPS: regra `VlrUnitario × Quantidade`, substituição por (mês, CNPJ), tudo ou
  nada, processamento em segundo plano com trava, leitura no navegador,
  mapeamento que não confunde `QTD` com `Quantidade`, rodapé do BI (mês e corte).
- Órfãos em tabela própria (`compras_gps_orfas`); vínculo reaproveitado nos envios seguintes.
- Filas recalculadas a partir dos dados vigentes (antes somavam a cada envio).
- Estoque removido do upload, das telas e da prioridade da fila (colunas
  continuam no banco como opcionais, sem uso).
- Gruppy e Base Genéricos também leem a planilha no navegador.
- Tela Dados: abas e itens de fila preguiçosos.
- Testes isolados do Spaces real (`tests/conftest.py`); testes da regra antiga
  movidos para `Claude outputs/testes_regra_antiga_gps/`.
- Medido com o arquivo real de agosto (150.003 linhas): leitura ≈ 15–18 s no
  navegador, gravação ≈ 3–5 s; 63.382 compras / 336 lojas / 6 CNPJs órfãos.

## Pendências abertas (detalhe em `status_e_pendencias.md`)

1. Rodar `python limpeza_gps_regra_antiga.py --executar` (apaga os meses
   2026-01 e 2026-08 da regra antiga, mantém EANs resolvidos e Base Genéricos) e
   reenviar os meses pela tela.
2. CORS do bucket para PUT a partir do endereço do app (painel da DigitalOcean) — opcional.
3. Arquivos de teste deixados no bucket pelas execuções antigas da suíte (~159 só em 23/09).
4. Trocar as credenciais expostas (Spaces e senha do Postgres) antes de publicar.
5. Depois do upload: velocidade das telas de análise (cache / tabela resumo),
   itens 12–14 do plano (aceite automático, reprocessamento incremental), API do GPS.
