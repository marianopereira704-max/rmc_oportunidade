# RMC Oportunidades — guia do projeto

Atualizado em 29/10/2026. Estado detalhado das pendências: `status_e_pendencias.md`; plano em andamento:
`docs/specs/2026-10-29-plano-de-acao.md`.

## O que o sistema faz

Mostra, loja a loja e produto a produto, quanto cada farmácia da rede deixou de
economizar comprando de outro fornecedor em vez da RMC (Análise de
Oportunidade), e monta a sugestão de compra de cada loja (Assistente de
pedido). Cruza:

- **o que a loja pagou e vendeu** — a **API do GPS**, baixada toda madrugada
  pela rotina do GitHub Actions para o Spaces (pacote `pedido/`). Desde
  05/10/2026 a Análise também lê dali; a planilha do BI saiu;
- **o preço RMC** — tabelas de preço da **Gruppy**, por laboratório e por UF;
- **qual produto é qual** — a **Base Genéricos** (EANs diferentes do mesmo
  genérico somam juntos) e a **base de categorias** (genérico, ético,
  perfumaria…: FEBRAFAR + CMED + classificação manual).

Economia perdida, por loja e produto da tabela Gruppy, contra **um laboratório
por vez** (escolhido no filtro principal): a soma de `(preço pago −
preço do laboratório) × quantidade` de cada compra ACIMA da tabela no período.
Regras em `core/analise_api.py`. Nunca é gravada: é sempre calculada na hora.

Stack: Streamlit 1.62 + SQLAlchemy 2 + Alembic + Postgres (produção) / SQLite
(testes e dev) + DigitalOcean Spaces (arquivos) + pandas + rapidfuzz.

## Onde roda (importante antes de qualquer comando)

- **App publicado:** Streamlit Community Cloud, a partir do `main` do GitHub
  (`marianopereira704-max/rmc_oportunidade`). Publicar = juntar no `main`.
  **Ramos (28/10/2026):** o `main` tem o app de 05/10, que a equipe usa; o trabalho novo vai para o ramo
  `desenvolvimento`, que é o ramo PADRÃO do GitHub (é dele que saem os agendamentos da rotina). Nada vai para o
  `main` até a publicação (Fase 8, que espera a API do GPS estar 100% confiável). O repositório é PÚBLICO.
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
  Migração nunca pode apagar/renomear coluna nem tabela enquanto o `main` estiver atrasado — só acrescentar ou
  afrouxar (ex.: NOT NULL → NULL): na publicação, o banco de produção recebe as migrações novas, e se for preciso
  voltar para o `main` antigo ele tem que continuar funcionando nesse banco. (O app antigo e o local usam bancos
  DIFERENTES no mesmo servidor — ver acima.) Última: `0019_classificacao_ean` (tabela `classificacao_ean`, 25/10/2026). Antes: `0018_empresas_gps` (tabela `empresas_gps`). Antes: `0017_catalogo_iqvia` (tabela `catalogo_iqvia` + valor `IQVIA` no enum `origemcategoria`, mesmo caso da 0010). A `0010_origem_fila_loja_api` acrescenta valor ao enum `origemfila` com `ALTER TYPE … ADD VALUE` dentro da transação (Postgres 16; nada pode USAR o valor novo na mesma transação).
- **Bucket `consultoria-interna` é compartilhado** com outros sistemas (backups
  `.sql.gz`, outros projetos). Nunca apagar objetos por padrão de data/nome
  sem conferir. A chave do app não tem permissão de CORS (Get/PutBucketCors).

## Regras de trabalho

- **Nunca fazer commit, branch ou push.** O Mariano versiona manualmente. Ao
  terminar, listar os arquivos alterados.
- Não publicar nada até ele pedir.
- Exclusão em massa em produção (banco ou bucket): preparar script com modo
  prévia (`--executar` para aplicar) e deixar ele rodar — ver `limpeza_compras_planilha.py`.
- Código, comentários, mensagens de tela e commits em português. O estilo do
  projeto é comentário/docstring explicando o PORQUÊ, com o bug ou a medição
  que motivou a decisão — manter essa densidade.
- Testes: `python -m pytest -q -p no:warnings` (630 passando em 29/10, ~55 s).
  `tests/conftest.py` força storage local temporário em todo teste — sem ele,
  a suíte gravava arquivos no bucket real. Testes usam SQLite; primitivas
  que dependem do banco (ON CONFLICT) ficam em `core/sql.py` pra valer igual
  nos dois.
- Teste visual e de contrato (fora do pytest, precisa de Playwright):
  `python -m tests.visual.rodar` — sobe o app num SQLite com `data.seed` + uma loja fictícia do Pedido (`tests/visual/loja_ficticia.py`, 20 produtos inventados, data fixa 27/09/2026, com personalização) + compras da rede fictícias pra Por Loja (`tests/visual/compras_rede.py`, agosto/2026, com o "hoje" da Análise fixo em 06/10/2026 por `RMC_HOJE`; notificações saem da página antes de cada captura),
  percorre as telas em 1280/1920 px, confere os seletores internos do
  Streamlit (`SELETORES_STREAMLIT`), os vãos iguais das tabelas, os EIXOS e o ESPAÇO (29/10/2026: sem rolagem horizontal, tabela até `LARGURA_MAXIMA_PX`, célula que não vaza, título alinhado com o conteúdo pelo lado da coluna e linha com o mesmo centro vertical, ±3 px; etiqueta numa linha; título de card em até 2 linhas), o fluxo do Assistente de pedido (pop-up de alerta, rótulo "Regras desta loja" — a loja fictícia tem personalização —, lista e uma categoria filtrada) e compara as capturas com
  `tests/visual/referencia/` (`RMC_AGORA` fixa a hora da prévia do pop-up Listas, 29/10/2026). Mudança visual de propósito:
  `--atualizar` e versionar as imagens novas. **Celular (29/10/2026):** uma volta em 390 × 844 (`_telas_celular`,
  fotos `*_390.png`: login, Criar senha, Por Produto, Assistente com filtros, rodapé que some, detalhe e "Mais",
  Dashboard) com as mesmas conferências; nos cartões, nada pode vazar e o botão não cobre o nome. A navegação é
  feita em 1280 px (a barra lateral fica recolhida no celular). Rodar antes de atualizar o
  Streamlit (versão fixada em `requirements.txt`). **Só quando muda o que aparece na tela** (29/10/2026, decisão do
  Mariano: leva ~10 min): `python -m tests.visual.precisa` compara com o último commit e lista os arquivos da tela
  que mudaram (telas, tema, dicas, contas exibidas, versão do Streamlit); rotina, alerta, workflow, integrações e
  documentos ficam só no pytest.

## Mapa do código

| Caminho | Papel |
|---|---|
| `app.py` | Entrada: `init_db`, login (cookie "lembrar-me"), sidebar, roteamento. |
| `core/config.py` | Toda configuração (secrets → env → default): banco, Spaces, sinônimos de coluna, limiares. |
| `core/models.py` | Modelos. `EanGenerico` é a ÚNICA junção EAN → genérico. |
| `core/db.py` | Engine/pool, `get_session` (commit/rollback), migrações no start, usuários fixos. |
| `core/analise.py` | Peças comuns das telas de análise: `Filtros`/`filtrar` (inclusive Categoria), `ordenar`, `Indicadores`, `variacao`, `meses_periodo_anterior`, lojas e laboratórios com tabela ativa. A regra da planilha (menor preço, recuo CMV/custo médio, preço médio 3m) saiu em 05/10/2026. |
| `core/queries.py` | Listas dos filtros (UF, atendentes, grupos, lojas) e a paginação. |
| `views/tabela.py` | Tabela das telas de análise (CCv2): um grid só pra cabeçalho e linhas, ordenação pelo título, botão por linha, dica, estado vazio. Mudou o JS/CSS: reiniciar o `streamlit run`. **Celular (29/10/2026, etapa 6):** com `celular="cartao"` ou `"tabela"` e a tabela abaixo de 600 px (classe `estreita`, pela largura da própria tabela), vira um cartão por linha ou fica só com as colunas que têm papel; cada `ColunaTabela` diz o `celular` dela (titulo, etiquetas, dado, dado_largo, acao, destaque ou None = só no PC), o `rotulo_celular` e a `ordem_celular`. Pedaço "so-celular"/"so-pc" aparece só num dos dois. − / + na quantidade e `detalhe_celular` (botão de detalhe só no cartão) nascem escondidos no PC — elemento escondido não ocupa coluna, e o PC fica idêntico (teste visual). |
| `views/enviar_pedido.py` | **"Enviar pedido" do celular (29/10/2026):** abre a janela de compartilhar do aparelho (WhatsApp, e-mail…) com o Excel de EAN, Produto e Quantidade (`calculo.exportacao_envio`); sem ela, baixa. Conta como exportação (formato "Enviado"). Também põe `rmc-rolando-baixo` no `<body>`: o rodapé do Assistente some ao rolar para baixo e volta ao rolar para cima (só no celular, CSS em `core/theme.py`, bloco "CELULAR"). |
| `views/loja_barra.py` | **Loja escolhida na barra lateral** (02/10/2026), vale pra todas as telas: grupo LOJA no topo da barra ("60 · RAZÃO SOCIAL", sem "cód." e sem "?"; ADM/Consultor com "Todas as lojas", Proprietário com várias lojas com "Todas" (só as dele), Comprador e quem tem 1 loja sem "Todas" — loja única vira texto fixo; "Ver todas as lojas" embaixo da caixa). Só na sessão (login ainda é único). No pé da barra, discreto: "Atualização dos dados · Pedido (GPS): dd/mm/aa às hh:mm (`gerado_em` do marcador de estoque da empresa; com Todas, a execução mais recente da rotina) · Análise (compras): até mmm/aaaa". Efeitos: Por Loja (só ADM) mostra sempre a rede, com a loja destacada (classe `destaque`); Por Produto com uma loja = só busca e período, card "Preço acima do laboratório" no lugar do de lojas, sem a loja embaixo do produto; com Todas, UF/Atendente/Grupo/lojas específicas limitados às lojas do usuário (`analise.Filtros.lojas_permitidas` corta o dado); Assistente sem faixa Loja (com Todas pede uma loja); Configurações → Personalização abre com a loja da barra. |
| `views/analise_comum.py` | Faixa do filtro Laboratório, filtros, cabeçalho ordenável, cache do resultado (`resultado_api`, por laboratório + meses + versão do banco + data de montagem dos arquivos da rede; `historico_api` = os 6 meses pro gráfico), tabela de notas fiscais dos dois detalhes (`colunas_notas`/`linha_nota`), filtro de Categoria, formatação das células. |
| `core/analise_api.py` | **Análise pelas compras da API** (05/10/2026, Por Loja e Por Produto; `por_produto` agrupa por loja × produto, `historico_mensal` pro gráfico). Todo produto da tabela Gruppy (EAN na Base Genéricos → soma pelo genérico, `chave` "G<id>"; senão EAN a EAN, "E<ean>"); economia = SOMA das compras acima da tabela no período; uma linha por loja × produto × nota fiscal × FABRICANTE (EAN) × situação (acima/abaixo/a revisar — 29/10/2026: com o mesmo genérico de dois fabricantes na nota, a linha única mostrava o preço médio e a conta Diferença × Qtd. não fechava; a Por Produto conta notas de verdade, não linhas); regras de preço compra a compra na ordem cadastro × nota → acima do preço de venda → mediana (números das Configurações de Pedidos), "A revisar" sem economia; bonificação fora. Período = 1, 2 ou 3 meses FECHADOS com arquivo da rede. Medido em 05/10/2026 (4 lojas): 589 compras corrigidas pelo cadastro, 662 "a revisar" — 600 nas lojas da Reis, notas da MILLENIUM a ~2,2× o cadastro (reanalisar com a rede online). |
| `pedido/rede.py` | Compras da REDE por mês fechado (`analise/compras/{mes}.parquet`, ordenado por EAN em blocos de 20 mil — o app lê só os EANs do laboratório; + `{mes}_eans.parquet`, resumo por EAN pra fila; + `{mes}.json` com o manifesto). Cada linha leva cadastro e preço de venda da foto de estoque. Refeito quando o manifesto (lojas × data da baixa) muda. Mês aparece a partir do dia 6 do mês seguinte. Roda no GitHub depois das 8 partes (job `rede`). Medido em 29/10/2026: ~330 mil linhas e ~11 MB por mês (agosto: 335 lojas com compra, contra 336 da planilha do BI). |
| `integrations/fila_compras.py` | Fila de EAN pelas compras da API (05/10/2026, no lugar do envio da planilha): genérico (base de categorias) COMPRADO e fora da Base Genéricos → origem GPS (≥ 92 resolve sozinho); valor dos itens GPS/Gruppy pendentes = comprado nos últimos 3 meses fechados, regravado. Lê o resumo por EAN. 1×/dia na primeira abertura de Dados (`ROTINA_FILA_EAN_COMPRAS`) e pelo botão. A Gruppy só manda pra fila EAN que a base de categorias diz ser genérico (o resto compara pelo próprio EAN). |
| `pedido/armazenamento.py` | Spaces/disco por chave. **Revisão de desempenho (07/10/2026):** `do_ambiente()` devolve UM cliente do Spaces por processo (antes um boto3 novo a cada chamada: ~720 ms por consulta contra 136 ms reaproveitado); `ler_varios` lê muitos arquivos ao mesmo tempo (16) — usado nos catálogos por empresa (contador Sem Classificação), nas lojas do GPS (vínculo) e nos manifestos de meses da Análise. O Cadastro guarda a lista da loja por 60 s (`_usuarios_da_loja`, descartada em toda gravação). `st.cache_resource` não é usado de propósito: a cópia do `st.cache_data` custa ~10 ms no maior objeto (catálogo IQVIA) e protege o objeto de ser alterado por uma sessão. |
| `views/popups.py` | **Padrão dos pop-ups (grill de 07/10/2026).** Todo pop-up é `@popups.popup(titulo, tipo)` (nunca `st.dialog` direto — o teste visual acusa "feito sem a fábrica"). Tipos e larguras (`theme.POPUP_LARGURAS`): **aviso** 440 (confirmação, alerta, resultado), **curto** 560 (Atribuir Painéis/Empresas, E-mail de ativação, Listas, Exportar), **longo** 760 (Novo/Editar usuário, Mapeamento da Gruppy, Corrigir estoque negativo), **detalhe** 1000 (Detalhes da loja/do produto); largura = a do tipo ou a área do conteúdo − 32 px, a menor; celular (< 640 px) = tela − 32. O 1.62 só tem small/medium/large: a largura vem do CSS pelo marcador `st-key-popup-<tipo>`. Centralizado na vertical (margin auto na moldura) e na ÁREA DO CONTEÚDO (o fundo ganha recuo da barra lateral aberta). Máx. 85% da altura: o corpo rola e o rodapé (`popups.botoes(principal, secundario="Cancelar", destrutivo=…)`) fica preso embaixo (sticky na camada `envoltorio_layout`); erro de formulário vai num `st.empty()` ACIMA do rodapé. Botões lado a lado ocupando a largura, secundário com contorno à esquerda, principal à direita, sempre com verbo; destrutivo vermelho (`--negativo`). Formulário longo não fecha por X/ESC/clique fora (perderia o digitado) — só pelo Cancelar, inclusive nas saídas antecipadas. Título navy 20/700; seções `popups.secao` ("IDENTIFICAÇÃO ———", 12 px); 24 px nas bordas, 16 entre elementos. `popups.resultado` (ícone num círculo, modelo do convite) e `popups.notificar` (padrão das notificações; as antigas não foram mexidas). CSS num bloco só ("POP-UPS" em core/theme.py), seletores do st.dialog no contrato (`dialogo_*`, `barra_lateral_aberta`). O tema é claro fixo — mesmo com o navegador em modo escuro (medido), sem correção de dark mode. Corrigido junto: o "Aviso de infraestrutura" reabria a cada clique se fechado pelo X. O teste visual mede todo pop-up capturado (largura do tipo, centro na área e na vertical ±8 px, rodapé visível) e fotografa 15 deles, + Novo/Excluir em 390 px. Ficam fora do roteiro (precisam de planilha/erro/aviso real): Mapeamento e Excluir da Gruppy, Sincronização falhou, Aviso de infraestrutura, Corrigir estoque negativo, Descartar. |
| `core/rotinas.py` | `controle_rotinas`: rotina diária (`reivindicar`, `executar_se_necessario`). A trava "um por vez" e o `precisa_rodar` saíram em 29/10/2026 (sem uso desde o envio da planilha do GPS). |
| `core/sql.py` | Upsert atômico portátil (`insert_com_atualizacao`, `insert_ignorando_conflito`). |
| `integrations/planilha_navegador.py` | Decodifica a planilha lida no navegador (Gruppy, Base Genéricos, categorias). O envio da planilha do GPS, o processamento dele (`integrations/gps.py`, `gps_processamento.py`, `gps_cache_orfaos.py`) e a leitura do rodapé do BI saíram em 05/10/2026. |
| `integrations/gruppy.py` | Tabela de preço por laboratório, vigência por UF. |
| `integrations/base_genericos.py` | Importação da planilha curada EAN → nome canônico. |
| `integrations/sistema_interno.py` | Sincronização de lojas (API interna, upsert em lote, diária). **Loja da rede (07/10/2026) = `active` E status "Ativo"** (`STATUS_DA_REDE`): o `active` sozinho trazia também "Portal do PBM" e "Institucional" (813 → 594). Quem sai fica com `lojas.na_rede` = False (nada se apaga); `loja_da_rede()` (core/models.py, vazio conta como sim) filtra barra lateral, filtros, vínculos e Análise. Resposta sem nenhuma loja "Ativo" não altera nada. **Grupo econômico** = `acronym_economic_group` ("GE787" = GE + código da loja principal); rótulo "GE787 · RAZÃO" (`queries.rotulos_grupos`, `analise_comum.rotulo_grupo`) no filtro da Análise e no detalhe da loja. 209 lojas em grupos de 2+ (multi-lojas). A rotina do GPS usa a mesma regra (`pedido/__main__.py`). |
| `integrations/gps_api.py` | Cliente da API do GPS (empresas, lojas, vendas por loja, compras e estoque da empresa), usado pela rotina. Mapa da API e medições: `docs/mapa_api_gps.md` (fora do git: tem IP e CNPJs). |
| `pedido/` | Rotina de dados da aba Pedido (roda no GitHub Actions, fora do app): API do GPS → Spaces em Parquet (`pedido/bruto/…`). `plano` (o que falta — carga inicial e diária são o mesmo comando; compras: 6 meses fechados, o último mês fechado é baixado de novo até o dia 6 do mês seguinte e as compras dos últimos 5 dias toda noite — a API filtra pela EMISSÃO da nota e não tem data de entrada; `bruto/{empresa}/_compras.json` guarda a data de cada baixa), `coleta` (corte de 8 min por consulta, fila de tentativas, orçamento de tempo; desde 07/10/2026 a empresa cuja LISTA DE LOJAS falha — 502/500 do GPS em 366 de 388 empresas no dia 05/10 — volta numa fila própria depois de uma pausa de 2 min, e o relatório conta `empresas_sem_lista_de_lojas`); **FASES (08/10/2026):** fase 1 = CARGA — toda empresa só com vendas e compras dos meses fechados `PEDIDO_CARGA_MESES` (padrão 2026-08,2026-09), sem estoque nem mês corrente (5 consultas por empresa de 1 loja em vez de 12; mês fechado não é refeito); as pendentes são divididas entre as partes, nunca tentadas primeiro; marcador `controle/carga/{empresa}.json` (completa, ou +1 tentativa quando falha — 3 tentativas e a empresa deixa de segurar; parar pelo tempo não conta). Quando todas concluem, fase 2 = MANUTENÇÃO, sozinha: o dia a dia (mês corrente, estoque, compras recentes, mês que fecha) de TODAS primeiro, os meses antigos (3 de vendas, 6 de compras) depois — `plano.separar_por_fase`. Motivo: na noturna de 08/10, 1,7 h passou revisitando 20 empresas antigas e nenhuma nova; a cobertura estacionava em ~60. Workflow com **8 partes**. Código da loja do GPS SEM espaços em todo lugar (08/10/2026: uma empresa manda "1"+49 espaços na lista de lojas e "1" nos dados — os 40 arquivos dela saíram vazios e o recálculo do vínculo caía na chave única). Vínculo automático: `gps_vinculo.recalcular_se_houver_novas`, em segundo plano no 1º acesso de cada sessão, no máx. a cada 10 min por processo (`loja_barra.conferir_vinculos_em_segundo_plano`), só quando há empresa nova. `__main__` (07/10/2026: as chamadas iniciais — empresas do GPS, lojas da T.I. — têm 3 novas tentativas com espera de 1, 5 e 10 min, e erro que derruba a execução vira relatório no Spaces com `erro_geral`, mostrado em Dados → Rotinas; no log público só o tipo do erro. Motivo: as execuções agendadas de 03 a 07/10 morreram em ~30 s sem relatório nenhum), `vinculo` (loja GPS ↔ RMC), `transformar`, `armazenamento`. Passo a passo: `docs/rotina_pedido.md`. |
| `pedido/pronto.py` + `pedido/calculo.py` + `views/pedido/` | Tela **Pedido**. **`views/pedido/` é pasta desde 28/10/2026** (o pedido.py tinha 1.130 linhas; só mudou de lugar): `__init__` = a tela (título, fluxo, rodapé), `dados_loja` = configuração, versões, classificação e cálculo com cache (o Dashboard, as Configurações e a aba Dados importam DAQUI, como `dados_pedido`), `estado`, `formato`, `tabela_pedido` (colunas, células, dicas, cards), `avisos` (pop-ups ao abrir a loja, pílulas de parâmetros), `filtros`, `dialogos` (Estoque negativo, Listas, Descartar, Exportar). `pronto` junta os ~60 brutos da loja em 3 arquivos (`pedido/pronto/{empresa}/{loja}/`, com assinatura; a rotina monta de madrugada, a tela monta o que faltar). `calculo` é função pura. Regras revistas em 29/09/2026: demanda/dia (dias sem dado saem da conta); máx. = ⌈demanda × dias⌉ (medicamento 7, perfumaria 15, **Sem Classificação 7 estimados**, com a tag); sugestão = máx. − estoque; só lista abaixo do máx.; **EAN negativo conta como 0** (não trava; tag + lista de correção); **Ruptura = estoque ≤ N un. (padrão 0)**, **Ruptura próxima = acima disso e cobertura ≤ D dias (3)**; giro baixo ≤ 1 un. em 90 dias; curva ABC 2 letras, **no mesmo período do giro baixo** (05/10/2026 — antes a janela inteira: o AAS Protect da Hudson, venda única de 80 un. fora dos 90 dias, saía AA e giro baixo; AA/AB/BA/BB em giro baixo caíram de 52/52/11/10 pra 0 nas 4 lojas). **Venda pontual (05/10/2026):** giro baixo com a 1ª letra da curva A ou B (vendeu 1 un., mas caro) ganha a tag verde "Venda pontual" (`selo-sucesso`) no lugar de "Giro baixo", aparece mesmo com "Ocultar giro baixo", vem DESMARCADO (marcar ou digitar a quantidade põe no pedido), sem tag/card Ruptura, no fim da ordem padrão; ~90–100 por loja, estoque zero. Liga/desliga em Configurações → Giro baixo (personalizável por loja). **Filtro Status** depois de Categoria (tags que existem na lista, OU): escolher "Giro baixo" desliga o interruptor, tirar religa, religar tira do filtro — o campo tem chave com versão (`pedido_status_<loja>_<n>`), porque escrever na chave dele de dentro do callback do interruptor deixava o campo vazio na escolha seguinte. **Preço no Pedido = última compra não bonificada** (a Análise continua no menor preço); "a revisar" = > 3× ou < 1/3 da mediana das compras da própria loja (o custo de cadastro do GPS bate com a última compra em só ~40–48% dos produtos). **Cadastro × nota (01/10/2026):** divergem mais de 2× → o preço de venda decide (custo coerente = 15–100% da venda): só um coerente → vale ele, tag "custo ajustado"; os dois/nenhum → nota + "a revisar"; sem compra e cadastro fora da faixa → "a revisar" (`revisar_motivo` diz por quê). Hudson: resolveu 9 dos 10 que divergiam; "a revisar" listados 1 → 26 (cadastro com fração errada, ex. Sonrisal R$ 0,05 vendido a R$ 3,00). **Acima do preço de venda (02/10/2026):** se depois disso o preço ainda passa do preço de venda, vale a nota paga mais recente dentro da faixa coerente, "A revisar" com motivo `acima_venda` (Hudson: 9 correções, ex. Kinder Joy R$ 128,11 → R$ 8,01; versões mais largas erraram casos certos). Coluna **Venda 90 dias** (total, fixo em 90) no lugar de Demanda/dia — só exibição, a sugestão segue a demanda da janela. Caso real: seringa com caixa de 100 lançada sem fração (R$ 79,90/un.) inflava o orçamento da Hudson em ~R$ 4,7 mil na regra antiga. Genéricos da Base Genéricos viram uma linha. **Assistente de pedido** (nome na tela desde 01/10/2026; por dentro continua "pedido"). Ao abrir a loja, em ordem: pop-up Estoque negativo (corrigir manualmente / considerar todos zero) → pop-up Sem classificação (informativo) — cada um UMA vez por foto do GPS por usuário+loja (`pedido_avisos_vistos`). Loja com personalização: valem as regras dela, sempre (29/10/2026; antes, pílulas Personalizado × Padrão), com o rótulo "Regras desta loja"/"Regras padrão" no topo. **Textos em linguagem de dono (29/10/2026, aprovados pelo Mariano):** cards "Produtos para comprar", "Ruptura · N ainda fora do pedido", "Sem categoria"; etiquetas "Conferir preço", "Sem categoria", "Mudado por você" só na TELA e no filtro Status (`tabela_pedido.ROTULO_STATUS`; os valores internos não mudaram); coluna "Estoque"; "Mínimo / Ideal" por extenso e o "?" da Curva não couberam em 1280 px (o nome do produto quebrava) — ficaram "Mín./Ideal" e a dica da Curva na própria letra. Topo (02/10/2026): a faixa Loja vem PRIMEIRO; abaixo, só depois da escolha, "Assistente de pedido" + data, sem botões — o "Estoque negativo" saiu (corrigir é pelo pop-up) e "Listas" virou botão do rodapé ("Listas (N)", pop-up com abas "Salvar como lista" e "Listas salvas"). Cards (pedido inteiro): Orçamento (unidades no subtítulo), Itens com sugestão, Ruptura, Sem classificação (card inteiro clicável → "Saiba mais": botão transparente por cima, `st-key-pedido_saiba_mais`). Filtros SEM pop-up, numa linha acima da tabela: busca, Categoria, Fabricante (= laboratório), Ocultar giro baixo. Ordem padrão: "a revisar" primeiro, depois valor vendido. Lapidação de 02/10/2026: "A revisar" é tag do Status (a 1ª, contorno âmbar, `selo-revisar`) e a linha só ganha a barra âmbar à esquerda (sem fundo); a Última compra mostra preço e data, "· cadastro"/"· bonificado depois" em texto miúdo e o "custo ajustado" na dica; quantidades centralizadas (`ColunaTabela(centro=True)`), dinheiro à direita; divisória das linhas #EEF0F3 (`--borda-linha`); ✓ discreto no "Salvo automaticamente"; lupa na busca, lixeira no Descartar. **Desempenho (02/10/2026):** o que a tela lê do banco sobre a loja (correções, área de trabalho, listas, avisos, avisos vistos, personalização) fica na sessão (`_EstadoLoja`, relido a cada 30 s ou depois de uma ação — `_concluir`/`_invalidar_estado`); a área é atualizada na cópia depois de gravar (`pedido_area.com_quantidade`/`com_marcacoes`, testadas contra o banco). Medido: clique de checkbox 11 → 3 consultas (só a gravação), trocar de página 8 → 0. O cálculo da loja tem como chave o marcador da foto de estoque (`loja_barra.marcador_estoque`, 5 min) e expira em 1 h, em vez de recalcular a cada 5 min. A base de categorias do Assistente carrega sem a origem (1.470 → 825 ms). Limpeza do mesmo dia: a classe `Filtros` do pop-up, as pílulas, `contar_opcoes`, `aplicar_rascunho`, `tolerancia_preco` e as funções do rascunho da Fase 5 foram apagadas; `filtrar(busca, categorias, fabricantes)` e `opcoes_filtros`. Textos de 02/10/2026: "Mín./Ideal" (e "ideal" na exportação e nas Configurações); dicas com parágrafos (`

`, `white-space: pre-line` na dica da tabela), frases curtas e "sistema de vendas" no lugar de "GPS" ("PDV" onde não cabe, ex. "PDV: -1") — só nas dicas; o "· cadastro" virou "-"; mensagens do Assistente viram notificação (`st.toast`, 5 s, ✅/❌/⚠️/ℹ️ por tipo; avisos de estado uma vez por loja na sessão); pop-up Listas na largura média, loja como "RAZÃO - CNPJ". Barra lateral: inativos sem contorno (bloco isolado "BARRA LATERAL SEM CONTORNO" em core/theme.py — apagar o bloco volta ao visual anterior). Rodapé fixo (`st-key-rodape-pedido`, sticky na camada `envoltorio_layout`). Em 1280 px a tabela cabe no limite (nome do produto quebra dentro da palavra, classe `quebra`). |
| `pedido/configuracao.py` + `integrations/configuracao_pedido.py` + `views/config_pedido.py` | **Configurações de Pedidos** (só admin, sidebar em Administração): dias de estoque por grupo e por categoria, piso do máximo, ruptura, giro baixo, curva ABC, janela de meses, faixa de custo coerente. Duas seções (01/10/2026): **Padrão de todas as lojas** e **Personalização por loja** (`personalizacoes_pedido_loja`: só o que difere do padrão — `diferencas`/`aplicar`; o resto segue o padrão mesmo se ele mudar; "Copiar personalização de…" copia, não liga; "Remover" grava `{}`; janela de meses fica de fora; a loja não edita nada). A tela Pedido ainda usa só o padrão: a escolha Padrão × Personalizado (pílulas) é a Etapa 2 do Assistente de pedido. Cada gravação é uma linha nova em `configuracoes_pedido` (histórico com quem/quando); a vigente é a de maior id; sem nenhuma, valem os padrões de `settings.pedido`. A janela também vai pro Spaces (`pedido/controle/configuracao.json`) porque a rotina roda sem banco. Botões da sidebar no teste visual: pela chave `st-key-nav_<seção>` — o nome acessível inclui o ícone e "Pedido" casava com "Configurações de Pedidos". |
| `integrations/pedido_area.py` | Pedido (29/09/2026): **área de trabalho por usuário + loja** (`pedido_area`: só o que difere do padrão — quantidade digitada, marcação feita à mão; grava a cada clique, volta igual no reload) e **listas com nome** (`pedido_listas`, visíveis pra todos, com autor; "Abrir" traz pra área; exportar a partir dela apaga). Avisos contra pedido duplicado (lista de outra pessoa, exportação de hoje de outra pessoa). Substitui o rascunho da loja da Fase 5 (`rascunhos_pedido` fica no banco sem uso). |
| ~~`integrations/pedido_filtros.py`~~ | Apagado em 01/10/2026 com o pop-up de filtros (Assistente de pedido). A tabela `pedido_filtro_salvo` fica no banco sem uso (migração não apaga tabela).
| `integrations/pedido_registros.py` | Correções de estoque negativo (`correcoes_estoque`; só valem enquanto o sistema de vendas continuar mostrando negativo) e registro das exportações (`exportacoes_pedido`), com usuário e data/hora. Até 29/10/2026 se chamava `pedido_rascunho.py` (nome da Fase 5; o "rascunho da loja" virou a área de trabalho, `pedido_area.py`). A tabela `rascunhos_pedido` e o modelo ficam até a migração de `alembic/depois_da_publicacao/`. Exportação: ver o pop-up "Exportar" (formato Gruppy, tabela completa, só giro baixo). |
| `integrations/usuarios.py` + `views/login.py` | **Login individual (07/10/2026).** Um campo "E-mail ou CNPJ": equipe RMC (ADM/Consultor) no CNPJ da rede como sempre; lojas (Comprador/Proprietário) por e-mail (`usuarios.login`, minúsculas, único) + senha que o PRÓPRIO usuário cria pelo link do e-mail (ativação 48 h, "Esqueci minha senha" 1 h — `tokens_senha` guarda só o hash; cada link vale uma vez; link novo invalida o anterior; a mensagem do "esqueci" é a mesma exista o e-mail ou não). E-mail pelo Gmail `suporte.assistente.pedidos@gmail.com` com SENHA DE APP (`EMAIL_REMETENTE`/`EMAIL_SENHA_APP`/`APP_URL` só no secrets — nunca no código; local `APP_URL = http://localhost:8501`, no Streamlit Cloud o endereço publicado). `?token=` abre `render_criar_senha` antes de tudo (app.py). Cookie "lembrar-me" novo `u|id|assinatura` (assinatura leva o hash da senha: trocar a senha derruba os lembrar-me); o formato antigo segue aceito. Usuário de loja: `cnpj_login` vazio, `papel` = CONSULTOR só porque a coluna é obrigatória (vale o `nivel`); `loja_principal_id` + adicionais em `usuarios_lojas`; `linhas_compra`/`paineis` = códigos separados por vírgula; status Aguardando (sem senha) / Ativo / Excluído (`ativo` False). `usuarios_lojas_historico` registra acessos que saíram. Testado com e-mail real (07/10/2026). |
| `views/cadastro.py` | **Aba Cadastro (07/10/2026, etapa 3)** — grupo Administração; ADM e Consultor (o Consultor vê a Administração só com ela — `acesso.CADASTRO`). Só usuários de LOJA (a equipe RMC entra pelo CNPJ e não é cadastrada aqui; `usuarios._do_cadastro` recusa ADM/Consultor). A lista segue a loja da barra (com "Todas", pede uma loja) e mostra quem entra nela como principal OU adicional; a coluna Loja traz todas as lojas do usuário. Ações por linha (botões só com ícone e dica): Editar (troca a loja principal: sai a antiga e as adicionais fora do grupo da nova, com histórico), Atribuir Painéis (obrigatório, sem padrão), Atribuir Empresas (só lojas da rede do MESMO grupo econômico; a principal travada; sem grupo → aviso e opção desabilitada), E-mail de ativação (só "Aguardando"; "Enviar e-mail" ou "Só gerar o link", sempre com o link pra copiar), Excluir (desativa, com confirmação; invalida os links abertos) / Reativar em "Mostrar excluídos". Novo usuário: loja da barra só pra conferir; ao cadastrar o e-mail já sai. Resultado do envio (07/10/2026, modelo do print do Mariano): pop-up curto com ícone num círculo (verde "Convite enviado", âmbar "E-mail não enviado", azul "Link gerado"), uma frase, a pílula "Copiar link" (`views/copiar_link.py`, CCv2: copia no navegador e vira "Link copiado"; o link não aparece mais inteiro; se o navegador bloquear, mostra o link pra copiar à mão) e "Concluir"; largura máxima de 440 px também no pop-up largo do Novo. Campos dos pop-ups limpos ao abrir (`_abrir`). Sincronização de lojas chama `usuarios.ajustar_acessos` (Q32): loja principal fora da rede → usuário excluído; adicional fora da rede/do grupo → sai; tudo em `usuarios_lojas_historico`. Tabela de colunas do Streamlit (5 botões por linha, poucos usuários) — com `help`, o botão fica dentro das camadas da dica: CSS por `botao_terciario`/`icone_material` (SELETORES_STREAMLIT). Teste visual: `cadastro`, `cadastro_novo` e `config_pedido_classificacao` (Configurações rolada até a classificação de produtos) (a loja fictícia tem um usuário Ativo e um Aguardando). |
| `core/acesso.py` | Níveis de acesso (revistos em 02/10/2026): ADM (tudo; Por Loja é só dele), CONSULTOR (tudo menos Administração e Por Loja; começa na Por Produto), COMPRADOR/PROPRIETARIO (Por Produto e Assistente das lojas em `usuarios_lojas`; sem loja ligada, nenhuma; "Gerente" virou "Comprador" em 01/10/2026 — `GERENTE` gravado vale como Comprador). Nível em `usuarios.nivel` (texto, não enum do Postgres); vazio = deduzido do papel do login único (admin/consultor). Login individual por e-mail desde 07/10/2026 (`integrations/usuarios.py`); Cadastro = ADM e Consultor. **Painéis (etapa 4, 07/10/2026):** Comprador/Proprietário só veem as telas ATRIBUÍDAS no Cadastro (`pode_ver(nivel, secao, paineis)`, `PAINEIS_DA_LOJA` = pedido, Por Produto, Dashboard; nunca Por Loja nem Administração); a tela inicial é o 1º painel atribuído; sem painel nenhum, um aviso no lugar da tela. Os painéis são lidos no login (mudou no Cadastro: vale no próximo login). |
| `pedido/classificacao.py` + `integrations/classificacao.py` + `views/classificacao_produtos.py` + `pedido/linhas.py` | **Classificação de produtos em cascata (25–26/10/2026; design e plano em `docs/specs/2026-10-25-classificacao-produtos-*`).** Todo produto cai em UMA de 5 categorias fixas, que são a linha de compra do Comprador e a do Dashboard: **Propagado, Genérico, Similar, Perfumaria e Em classificação** (rótulos de `linhas.ROTULOS`; os CÓDIGOS gravados nos usuários — `genericos`, `similares`, `propagados`, `perfumaria`, `sem_linha` — não mudaram). Ordem: categoria MANUAL com correspondência clara (Prescrição genérico/propagado/trade, grupo perfumaria; MIP/OTC e "Medicamento (CMED)" não) → setor da IQVIA (`linhas_por_setor`) → tipo da CMED (`linhas_por_cmed`: só Genérico e Similar classificam; Novo, Específico, Biológico, Fitoterápico, Terapia Avançada e Radiofármaco não) → "Em classificação" (aparece para todos os compradores). **A FEBRAFAR não classifica** (o MIP/OTC dela se analisa à parte); aparece só na cascata, para identificar. Produto com vários EANs: o EAN de origem mais forte, começando pelo principal. As correspondências são do PADRÃO, não se personalizam por loja (`FORA_DA_PERSONALIZACAO`). Saíram `linhas.coluna`, `linhas.filtrar` e a reserva da Q36 A. Dados: tabela `classificacao_ean` (EAN → categoria da FEBRAFAR e tipo bruto da CMED, sem decidir nada; migração 0019) + `catalogo_iqvia` + manuais de `categorias_ean` = o "universo" (`integrations/classificacao.universo`, 273 mil EANs). Carga: `python -m pedido.carga_categorias --febrafar <xlsx> --cmed <xlsx> [--executar] [--so-classificacao]` (a prévia lista os tipos da CMED; `--so-classificacao` grava só `categorias/classificacao_ean.parquet` e NÃO refaz a base inicial) e o botão "Carregar fontes" em Dados → Importar → Categorias. Carregado no banco dev em 26/10/2026 (lista da CMED de 09/10/2026, gov.br; o leitor procura o cabeçalho em todas as abas). App: cache global `views/pedido._classificacao` (EAN → categoria e origem, por versão das fontes + mapa; 6 MB) e cada tela pega só os EANs da loja (`classificacao.mapa_da_loja`, ~0,1 s). Medido no banco dev: ler as fontes 7–9 s na 1ª vez de cada versão (a leitura antiga, só da IQVIA, já levava 2,5–6 s), classificar 0,6 s, cascata 0,3 s. Loja 60: 79 de 16.413 SKUs mudaram de linha (70 de Em classificação para Similar pela CMED; 9 de Perfumaria para Em classificação). Tela: Configurações de Pedidos → Padrão → "Classificação de produtos" (no lugar da tabela "Linhas de compra"): cascata em `<details>` aninhados (5 categorias → IQVIA → FEBRAFAR → CMED, setinha no fim da barra, só o que existe; em Em classificação, a FEBRAFAR de quem só ela conhece) e pop-up "Editar correspondência" (1º clique mostra o que muda, 2º grava no histórico). Comprador no Assistente: vê só as categorias dele + Em classificação (corte logo depois da área de trabalho, Q9 B). Teste visual: `config_pedido_classificacao`, `_aberta`, `config_pedido_correspondencia`, `pedido_comprador` (Ana, Compradora de Genérico e Similar). |
| `core/dashboard_ruptura.py` + `views/dashboard_ruptura.py` + `pedido/historico_estoque.py` | **Dashboard · Ruptura (redesenho de 27/10/2026; design e plano em `docs/specs/2026-10-26-dashboard-ruptura-design.md` e `2026-10-27-dashboard-ruptura-plano.md`, aprovado pelo Mariano).** Só o que FALTA e quanto custa (excesso e parado ficam no Estoque). Cards: Ruptura | Venda em risco por dia | Perda | Investimento para repor; Ruptura por categoria (as 5 da classificação, com setinha IQVIA › FEBRAFAR › CMED; Produtos, Em ruptura, Nível de serviço colorido, Perda, Investimento); Ruptura por curva e Perda por risco (barras); Produtos críticos (em ruptura e ruptura próxima) e detalhe com diagnóstico e sugestão. Um item = um produto do Assistente (genérico com vários EANs = um só; a coluna `codigos` do cálculo liga ao histórico). **A perda é MEDIDA**: a rotina guarda o histórico do estoque por código (`historico_estoque/{empresa}/{loja}/AAAA-MM.parquet`, só o que muda + a abertura do mês, e `_fotos.json`; 13 meses; a API do GPS não tem estoque passado e o Spaces não guarda versões — conferido em 27/10/2026). O app aplica o N da ruptura e o agrupamento dos genéricos ao ler; a falta entre duas fotos em falta conta (R9). Perda = venda esperada (média dos dias SEM falta conhecida, 90 dias antes da falta) × preço de venda × dias em falta; período de 30 dias (configurável), contando faltas que já acabaram. **Transição**: até o histórico ter o período, a página mostra a projeção "Perda em 30 dias se não repor" e "Medida até agora". Risco pela frequência de venda (mesma base): alto = 1 vez por semana (14%), médio = 1 vez por mês (3%) — os 50%/20% iniciais deixavam todos os 901 em ruptura da loja 60 em "baixo". Investimento = sugestão do Assistente × custo ("a revisar" fora do R$). Parâmetros na seção Dashboard das Configurações (`ruptura_perda_dias`, `ruptura_risco_alto/medio`, `ruptura_sla_vermelho/verde`), personalizáveis por loja. Dados: `views/dashboard_comum.carregar_ruptura` (cache por loja, foto, versões, config e correções; 1ª abertura com cache frio ~30 s na loja 60). Rotina: `python -m pedido --so-estoque --parte i --partes 8` tira só a foto e grava o histórico. Teste visual: `dashboard_ruptura*` (loja fictícia com histórico de 20 a 27/09, sem o dia 23). |
| `pedido/carga_catalogo.py` + `integrations/catalogo.py` | **Catálogo IQVIA (07/10/2026, etapa 5).** Carga ÚNICA (o arquivo `CATALOGO_DE_PRODUTOS_202602.xlsx`, aba `Catalogo_Produtos`, traz CD_PERIODO 2022 e não chegam catálogos novos). Dado de terceiro: nunca no git. `python -m pedido.carga_catalogo --catalogo <xlsx> [--executar]` (prévia sem `--executar`; ~80 s pra ler) monta EAN → SETOR_NEC + GMRS (253.124 EANs) no Spaces; Dados → Importar → Categorias de produtos → "Carregar catálogo" grava em `catalogo_iqvia` (COPY no Postgres; 4 s no SQLite; só acrescenta/atualiza) e, pela Q38, põe na base de categorias como "Prescrição genérico" (origem IQVIA) o EAN com GMRS = G que ela NÃO conhece — nunca troca categoria existente (Q37: complementa). Medido contra a base FEBRAFAR+CMED: 7.118 genéricos no catálogo, 514 novos; 270 que o catálogo diz genérico estão como MIP/OTC na FEBRAFAR e ficam assim. |
| `integrations/fila_loja.py` | Fila de EAN origem **Loja (API)**: genérico (pela base de categorias) VENDIDO nas lojas (catálogo da empresa, `lojas_com_venda`, gravado pela rotina) e fora da Base Genéricos. Valor = vendido na janela, regravado (não soma); fica fora de `recalcular_valores_fila_ean`; nunca resolve sozinho, nem no reprocessamento. Roda 1×/dia na primeira abertura da aba Dados (`ROTINA_FILA_EAN_LOJA_API`) e pelo botão na fila. Teste real (Hudson + 3 Reis): 1.398 genéricos vendidos, 1.328 já na base, 26 novos. |
| `integrations/gps_vinculo.py` | Vínculo loja GPS ↔ loja RMC no banco (`vinculos_loja_gps`); decisão humana nunca é desfeita pelo recálculo. Tela: Dados → Vínculo de lojas GPS. **Código da empresa no GPS (08/10/2026):** a API só tem o `idEmpresa` longo; o número que a equipe usa (Mega Farma = 2089) não sai pela API nem pelo sistema da T.I. — vem da planilha exportada do GPS (Empresa "2089 - Mega Farma", Cnpj, idEmp; o código = 1º número da coluna Empresa), enviada no painel "Códigos do GPS" da tela de vínculo → tabela `empresas_gps` (migração `0018`; reenviar atualiza e não apaga). Carregada em 08/10/2026: 475 empresas, 342 das 388 da API. **CEP no vínculo automático (08/10/2026, `pedido/vinculo.py`):** depois do CNPJ, CEP DE RUA igual + mesmo número = automático (mesmo endereço, ainda que o nome não bata); o CEP também desempata número repetido na UF e, sem número batendo, CEP de rua igual em uma loja vira sugestão pra confirmar. CEP terminado em 000 é o da cidade inteira e nunca decide sozinho (383 das 583 lojas da rede; a API só manda CEP em 108 das 409 lojas, 76 deles de cidade). Simulado nas 409 lojas: 2 mudanças, as duas conferidas pela planilha do GPS (Otimafarma → 1818 Paradela, de confirmar pra automático; Kokoto Matriz → 1154, de sem candidato pra sugestão). A tela agrupa as pendências por empresa com o código no título, lista as lojas do RMC com o código da T.I. na frente (as da empresa pelos CNPJs da planilha e as da mesma cidade no topo) e tem a conferência de todos os vínculos. |
| `pedido/categorias.py` + `integrations/categorias.py` | Base de categorias do Pedido (`categorias_ean`, EAN → categoria → grupo medicamento/perfumaria/Sem Classificação). Nasce com FEBRAFAR + CMED (`python -m pedido.carga_categorias` monta o arquivo no Spaces; o botão em Dados → Categorias de produtos carrega no banco — o secrets local só alcança o banco dev). Categoria MANUAL nunca é sobrescrita. A planilha FEBRAFAR é de terceiro: nunca no git. Chave do EAN sem zeros à esquerda (`chave_ean`). |
| `.github/workflows/rotina_pedido.yml` | Agendamento da rotina do Pedido: 00:47 de Brasília (minuto quebrado desde 29/10/2026 — o GitHub começou a de 00:00 às 06:03), 8 partes paralelas que param aos 310 min (com 330, 4 partes foram cortadas pelo limite de 355 do GitHub sem relatório), depois o job `rede` (compras da rede por mês — `pedido/rede.py`), o job `precalculo` (cálculo de madrugada, só com a variável `PRECALCULO_AGENDADO = 1`; no botão, "O que rodar" = `so_precalculo`) e o job `alerta` (recebe o resultado da coleta: parte cortada vira e-mail), disparo manual permitido. Versões das bibliotecas de conta FIXAS no `requirements.txt` desde 29/10/2026 (o pandas 3.1 quebrou o cálculo do Assistente no GitHub). Chaves em Secrets do GitHub (repositório público: nada de dado em log). **Desde 28/10/2026 roda do ramo `desenvolvimento`** (ramo padrão do GitHub; o Streamlit Cloud continua publicando o `main`) — conferido que a rotina nova convive com o app antigo (`docs/specs/2026-10-28-plano-publicacao-e-melhorias.md`, Fase 2). |
| `pedido/emails.py` + `pedido/emails_conteudo.py` + `integrations/destinatarios.py` + `views/notificacoes.py` | **E-mails para as lojas (29/10/2026, etapa 7; desenho e plano em `docs/specs/2026-10-29-emails-lojas-*`).** Job "E-mails" depois do pré-cálculo: um e-mail por pessoa (Comprador/Proprietário ativo com senha), todas as lojas dela, aviso de ruptura (curva A/B pela 1ª letra que zerou desde a foto anterior, estado em `controle/emails/<banco>/ruptura/`) no topo e relatório diário embaixo, com o Excel do pedido (EAN, produto, quantidade — `pedido/planilha.py`, o mesmo do "Enviar pedido"). Preferências em `usuarios.email_ruptura`/`email_relatorio` (migração 0020; vazio = aviso ligado, relatório desligado), na tela "Minhas notificações" (pé da barra, só usuário de loja; `?pagina=notificacoes`) e no Editar usuário. A rotina não alcança o banco: o app publica `insumos/<banco>/destinatarios.json` junto com as fotos dos insumos. Nunca duas vezes no dia; limite de 400; `--so-para` (conferência) e `--previa` (arquivos). Agendado só com `EMAILS_AGENDADO = 1`; precisa de `APP_URL` nos Secrets. |
| `pedido/alerta.py` | **Alerta por e-mail (28/10/2026)**, job `alerta`: lê o último relatório de cada parte do dia mais recente, compara com `controle/alerta/<data>.json` da noite anterior e manda e-mail (Gmail do sistema, `ALERTA_DESTINATARIOS`) só com problema — parte sem relatório, `erro_geral`, nenhuma loja com foto de estoque, queda > 5% de lojas com foto, ou nenhum relatório há mais de 1 dia. Motivo: de 25 a 28/10 as partes terminavam "verdes", presas na carga, sem foto nenhuma. Junto: a parte que termina a sua fatia da carga com ≥ 10 min sobrando segue para a manutenção (`MINIMO_PARA_MANUTENCAO_SEG`) — a API passou a listar 150 "empresas" sem cadastro (lojas "derivada de tb_estoque", sem nome/CNPJ; as reais seguem 388). |
| `core/limite.py` + `core/copia_spaces.py` | **Escala sem custo novo (28/10/2026, Fase 7 do plano):** `@limite.pesado` (sempre ABAIXO do `st.cache_data`) deixa no máximo `APP_CALCULOS_SIMULTANEOS` (2) cálculos pesados ao mesmo tempo — Assistente, Dashboard, Análise e as bases compartilhadas; medido: pico de 50–100 MB ao montar uma loja grande, teto de ~2,7 GB no Community Cloud. `copia_spaces.ler_ou_montar` guarda no Spaces (`pedido/cache_app/<banco>/<tabela>/<versão>.parquet`, 2 versões) as tabelas grandes do banco: fontes da classificação 16 s → 1,1 s, base de categorias 6,9 s → 0,6 s, resultado idêntico conferido. |
| `core/revisar.py` | **O que fazer com o "A revisar" (28/10/2026):** uma ação por motivo (códigos do `calculo`, da `analise_api` e do `estoque_modelo`), parágrafo "O que fazer: …" nas dicas do Assistente, da Análise e do Dashboard (antes todas diziam só "Confira as notas no sistema de vendas"). O porquê continua em cada tela. |
| `scripts/backup_banco.sh` + `docs/backup.md` | **Backup diário dos DOIS bancos (29/10/2026)**, no cron do droplet às 02:00 de Brasília (não no GitHub: o firewall só libera o Streamlit Cloud). `pg_dump` → `backups/rmc_oportunidades/AAAA-MM-DD/<banco>.sql.gz` no Spaces; 14 dias + o dia 1 de 12 meses; apaga uma chave por vez e só as do padrão exato (bucket compartilhado). Prévia por padrão, `--executar`, `--testar-restauracao` (banco temporário `rmc_teste_restauracao`). Postgres em contêiner: `RMC_BACKUP_DOCKER`. O `pedido/alerta.py` avisa quando falta o backup de hoje ou de ontem. `.gitattributes` força LF nos `.sh`. |
| `limpeza_compras_planilha.py` | Script com prévia (`--executar` aplica) que apaga as compras da planilha (`registros_compra_gps`, `compras_gps_orfas`), a fila de CNPJ órfão, os registros de envio e o mapeamento de colunas do GPS. Mantém os arquivos no Explorador, a fila de EAN e tudo o mais. Rodar só DEPOIS de publicar (o app antigo ainda lê essas tabelas). As tabelas ficam vazias no banco (migração nunca apaga tabela). |
| `reconciliation/` | **Dividido em 29/10/2026 (etapa 8; o motor.py tinha 947 linhas):** `fuzzy.py` (comparação de nomes, `resolver_ean`), `fila.py` (Fila de EAN: gravar em lote, confirmar, cadastrar, ignorar, listar), `importacao_base.py` (planilha Base Genéricos), `reprocessamento.py` (EAN sujo e reprocessar a fila). `motor.py` só repassa os nomes; código novo importa do arquivo certo, e teste troca função no módulo onde ela mora (ex.: `fuzzy.buscar_candidatos`). |
| `alembic/depois_da_publicacao/` | Migração **parada** (o Alembic só lê `versions/`) que apaga as tabelas sem uso — rascunhos, filtros salvos e o envio da planilha do GPS. Entra só depois da publicação e do `limpeza_compras_planilha.py --executar`; passo a passo no README da pasta. |
| `storage/filesystem.py` | Explorador de Arquivos virtual (FSNode) sobre Spaces/disco; nada se apaga, "inativar" move pra `_Inativos`. |
| `views/leitor_planilha.py` | Componente (CCv2) que lê o .xlsx NO NAVEGADOR. |
| `views/dados/` | **Pasta desde 28/10/2026** (o dados.py tinha 1.496 linhas; só mudou de lugar): `__init__` (navegação e contadores), `fila_ean`, `vinculo_gps`, `sem_classificacao` (as Pendências), `importar`, `rotinas`, `explorador`, `comum` (peças de mais de uma seção). `tests/test_dicas.py` varre subpastas (`rglob`). Aba Dados (só admin), organizada pelo que o admin está fazendo (Q35, 29/09/2026): **Pendências** (seletor com contadores: Fila de EAN, Vínculo de lojas GPS, Sem Classificação — com sub-abas "Vendidos nas lojas" e "Análise de Oportunidade") · **Importar** (Base Genéricos, Gruppy, Categorias) · **Rotinas** (sincronização de lojas, rotina do GPS com o último relatório de cada parte, listas FEBRAFAR/CMED) · **Explorador**. Todo item começa com "O que é / De onde vem / Como alterar" (`_explicacao`). |
| `core/dicas.py` + `tests/test_dicas.py` | **Regra das dicas (balões ao passar o mouse), 22/10/2026.** Texto: parágrafos separados por linha em branco (`dicas.dica("…", "…")` monta e põe o ponto final), até 4 parágrafos de no máximo 220 caracteres, frase completa que começa em maiúscula e termina em ponto, sem "·"/"—" como separador, sem "(s)" (use `dicas.qtd(n, "unidade")`), sem a sigla GPS ("sistema de vendas"); lista curta de "Nome: valor." uma por linha; dica de botão só com ícone é o nome da ação (até 4 palavras, sem ponto). Visual: UM balão (`theme.BALAO_DICA`: fundo #1F2A37, 12 px, até 300 px) em `[data-dica]` (tela, cards, tabela — `theme.CSS_DICA`), no gráfico (`barras_filtro`) e no `help=` do Streamlit (seletor `dica_help`); `title=` de HTML não se usa (balão do navegador, sem parágrafos) — dentro de HTML do `st.markdown` use `data-dica="{dicas.atributo(texto)}"` com a classe `dica-abaixo`. O balão de dentro vence o de fora. `tests/test_dicas.py` varre `views/`, `core/` e `integrations/` (`dica=`, `help=`, chave "dica" dos cards e do gráfico, `_DICA_*`, `tb.pedaco(..., dica)`, funções `_dica*`) e QUEBRA se alguma fugir da regra ou se aparecer `title=`; dica nova = passar por `dicas.dica`. O motivo "a revisar" do custo vira frase por `de.motivo_custo` (nunca o código cru). Teste visual: `dashboard_estoque_dica` e `config_pedido_dica`. |
| `core/theme.py` | CSS em camadas (legendas `st.caption` em `--texto-suporte` #3F4750 no app inteiro desde 07/10/2026 — o cinza do Streamlit ficava apagado; seletor `legenda`; o 1.62 apaga a legenda com `opacity: 0.6`, então a regra também volta a opacidade a 1 — só a cor não mudava nada na tela; na barra lateral segue como era): 1 tokens (cores, escala de texto 28/20/16/14/13/12, espaçamento 4–32, raios); 2 adaptação do Streamlit — única camada com seletor interno, sempre via `SELETORES_STREAMLIT`; 3 componentes `.rmc-*`/`st-key-*`; 4 telas (`theme.tela("nome")` → `st-key-tela-nome`). |
| `.streamlit/config.toml` | Tema oficial (primaryColor navy, fonte base 14, raios) e `toolbarMode = "viewer"`. O que der pra resolver aqui não vai pra CSS. Largura máxima do conteúdo: `LARGURA_MAXIMA_PX` (1220). |

## Perfis

Equipe RMC: login pelo CNPJ da rede, a senha define o papel. Lojas (Comprador/Proprietário): login individual por e-mail, cadastrados na aba **Cadastro** (07/10/2026). **Admin** vê tudo,
inclusive a aba **Dados**, **Configurações de Pedidos** e **Cadastro**. **Consultor** vê Análise de Oportunidade (Por Produto), Assistente de pedido,
o **Cadastro** (só usuários de loja) e o Dashboard (Estoque, Ruptura, Compras).

## Como os dados entram — passo a passo

As planilhas ficam em **Dados → Importar** (admin); a base de lojas é
sincronizada em **Dados → Rotinas**; vendas, compras e estoque vêm da API do
GPS pela rotina da madrugada (passo a passo: `docs/rotina_pedido.md`). Ordem
recomendada: lojas → Base Genéricos → Gruppy → filas. A ordem não é
obrigatória (nada se perde), mas seguir ela evita trabalho manual nas filas.

### 1. Base de lojas (automática)

- Roda sozinha na primeira abertura da aba Dados de cada dia (API do sistema
  interno); falha vira pop-up, os dados anteriores continuam.
- Botão **Forçar sincronização agora** para rodar na hora.
- Chave da loja: CNPJ. A loja do GPS é ligada à loja RMC pelo vínculo (Dados → Pendências → Vínculo de lojas GPS; automático pelo CNPJ quase sempre).

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
Gruppy recusa planilha com coluna de CNPJ (é compra do GPS).
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
Lojas de UF sem tabela não aparecem nas análises daquele laboratório.
A tabela pode ter qualquer produto (genérico, ético, fralda, leite…): só os
EANs que a base de categorias diz serem genéricos passam pela fila de EAN; o
resto compara pelo próprio EAN (05/10/2026).

### 4. Compras e vendas das lojas (API do GPS, automático)

Não há mais envio de planilha do GPS (saiu em 05/10/2026 — tirava o limite
de 150 mil linhas do BI e o trabalho manual de exportar). A rotina da
madrugada baixa da API vendas, compras e estoque de cada loja vinculada e,
depois, monta as compras da rede por mês fechado para a Análise
(`pedido/rede.py`). Regras das compras: data = EMISSÃO da nota (a API não tem
data de entrada); o último mês fechado é baixado de novo até o dia 6 do mês
seguinte (pega notas lançadas até o dia 5) e só então aparece na Análise; as
compras dos últimos 5 dias são baixadas de novo toda noite. Transferências
entre lojas ficam de fora. Preço por unidade = `VlrUnitario × (1 −
desconto%) ÷ Fracao`, sem ST.

As compras antigas da planilha (agosto/2026) ficam no banco sem uso até
alguém rodar `python limpeza_compras_planilha.py --executar` (depois de
publicar).

## As filas (Dados → Pendências)

### Fila de Resolução de EAN — genéricos da Gruppy, das compras e das vendas

Um EAN GENÉRICO (pela base de categorias) que ainda não está ligado a nenhum
genérico em `EanGenerico`. **Uma linha por EAN**, qualquer que seja a origem.
Resolver um item conserta a Análise (soma com os outros EANs do genérico) e o
Assistente ao mesmo tempo. Produto que não é genérico não passa pela fila:
compara pelo próprio EAN.

Como um EAN entra (Gruppy no upload; compras pela rotina diária
`integrations/fila_compras.py`), a descrição é comparada (rapidfuzz, WRatio)
com os genéricos ativos:

- score ≥ 92 (`RECON_LIMIAR_AUTO`): resolvido automaticamente (origem `AUTOMATICA`), não vai pra fila;
- 75 ≤ score < 92 (`RECON_LIMIAR_MEDIA`): entra na fila **com sugestão** (é só confirmar);
- score < 75: entra na fila **sem sugestão** (vincular manualmente ou cadastrar genérico novo).

Diferença entre as origens:

| | Origem **GPS** | Origem **GRUPPY** | Origem **Loja (API)** |
|---|---|---|---|
| De onde vem | genérico COMPRADO pelas lojas (API, arquivo da rede) e fora da Base Genéricos | genérico de uma tabela de preço | genérico VENDIDO nas lojas (API) e fora da Base Genéricos |
| O que falta enquanto não resolve | a compra de outro laboratório não é comparada com o genérico da Gruppy | o preço só compara com o mesmo EAN | no Pedido, o EAN não soma com os outros EANs do genérico |
| Valor na fila | comprado nos últimos 3 meses fechados (quantidade × preço); qtd = nº de compras | 0 ao entrar; passa a ter valor quando o EAN aparece nas compras | valor VENDIDO nas lojas na janela; qtd = nº de lojas |
| Quando o valor é regravado | 1×/dia (primeira abertura de Dados, `ROTINA_FILA_EAN_COMPRAS`) ou pelo botão | idem | 1×/dia ou pelo botão; nunca resolve sozinho |

- `origem` registra onde o EAN foi visto primeiro; se o mesmo EAN depois
  aparece no outro lado, continua sendo UM item (a origem não muda, o valor sim).
- Prioridade da lista: **valor de compra em jogo** (maior primeiro) — o
  critério antigo "aparece em estoque" saiu junto com o estoque. A tela mostra
  até 200 itens; o filtro **Origem** separa GPS/Gruppy/Loja (API). Com
  **Loja (API)** a ordem é de pareto pelo nº de lojas que vendem, com o "%
  acumulado" no título de cada item (02/10/2026). O relatório **Sem
  Classificação** tem duas sub-abas (05/10/2026): "Vendidos nas lojas" (pareto
  por LOJAS COM VENDA) e "Análise de Oportunidade" (EANs das tabelas Gruppy
  sem categoria, pareto por LOJAS QUE COMPRARAM); o envio da lista preenchida
  é um só, embaixo das duas.
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

## Análise de Oportunidade (telas do consultor) — regra de 05/10/2026

Pelas compras da API (`core/analise_api.py`, arquivos da rede de
`pedido/rede.py`). Período: 1, 2 ou 3 meses FECHADOS (`PERIODOS_MESES_OPCOES`)
que já têm arquivo; o cabeçalho diz "Compras de jul–ago/2026, pela data da
nota fiscal". A seta dos cards compara com os 3 meses anteriores (a rotina
guarda 6 meses de compras).

Para cada compra (item de nota) do período, de produto da tabela Gruppy do
laboratório escolhido, na UF da loja:
1. **Bonificação** (preço < R$ 0,10) sai de tudo; só é contada.
2. **Regras de preço, nessa ordem** (números das Configurações de Pedidos):
   cadastro × nota divergem mais de 2× → o preço de venda decide (custo
   coerente = 15–100% da venda): só o cadastro coerente → vale o cadastro;
   os dois ou nenhum → **"A revisar"**; nota acima do preço de venda →
   cadastro se coerente, senão "A revisar"; fora de 3× ou 1/3 da mediana das
   compras do produto na loja → "A revisar". "A revisar" não soma economia
   (caso das notas da MILLENIUM nas lojas Reis — reanalisar com a rede online).
3. **Economia** = soma de (preço − laboratório) × quantidade das compras
   ACIMA da tabela; compras abaixo não descontam.

**Por Loja** (só ADM): Loja | Localização | Responsável | Produtos (DISTINTOS
com economia) | Economia | detalhe = uma linha por produto e nota fiscal
(Produto + categoria, Laboratório, Compra = preço da nota + data, dica com
nota/fornecedor/quantidade, preço do laboratório, Diferença un., Qtd.,
Economia; "A revisar" com o motivo na dica).

**Por Produto** (ADM, Consultor, Comprador e Proprietário — estes, só das
suas lojas): uma linha por (loja, produto). Produto (nome, categoria, loja
embaixo com "Todas") | Laboratório (das notas acima) | Preço pago = média
ponderada SÓ das notas acima da tabela, com "N de M notas acima" | preço do
laboratório | Diferença un. | Qtd. (das notas acima) | Economia — Diferença ×
Qtd. = Economia. Sem nota acima: média de todas e "Já otimizada". Notas "a
revisar" fora da conta, com aviso "N nota(s) a revisar" (só elas: tag "A
revisar"). Filtro **Categoria** (base de categorias; vazio = "Sem
Classificação"). Detalhe (uma tela só): loja e responsáveis, a conta da
linha, gráfico de preço por mês nos meses guardados (até 6) contra a linha
tracejada do laboratório, e as notas fiscais da maior diferença para a menor
(sem a coluna Produto — é sempre o mesmo). Ideia guardada: um agrupamento
extra "por produto da rede".

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
rótulo ("Último mês", "Últimos 2/3 meses" — `PERIODOS_MESES_OPCOES`; o 6 saiu em 05/10/2026), e o
"Selecionar lojas específicas" embaixo. Sem "filtros avançados" (decidido em
24/09/2026). Os rótulos escondidos usam `label_visibility="hidden"` pra os
cinco campos ficarem na mesma altura.

**Largura das colunas (regra de 25/09/2026):** cada coluna tem a largura do
próprio conteúdo (`auto`; texto longo em `fit-content(Npx)` — Loja 340,
Produto 210 — que quebra linha acima disso) e a sobra vira vãos IGUAIS
entre todas as colunas (grid + subgrid, `justify-content: space-between`).
Nunca largura fixa ou em pesos (`fr`): foi o que deixou um buraco entre
Responsável e Produtos. O teste visual mede o vão VISÍVEL entre as colunas de
toda tabela (±8 px) e falha também se um título quebrar linha.

**Ordenação:** clicar no título de qualquer coluna (inclusive no Detalhes);
de novo inverte. Vazios sempre no fim. Padrão: Economia decrescente.

**Desempenho:** o resultado fica em memória por (laboratório, meses,
versão do banco, data de montagem dos arquivos da rede) — `views/analise_comum.py`;
ordenar, paginar, buscar e abrir detalhe não leem nada de novo. Os arquivos
da rede são lidos filtrados pelos EANs do laboratório (a leitura pula os
blocos sem nenhum deles — atenção, revisão de 07/10/2026: só a DECODIFICAÇÃO pula; o download do Spaces ainda é do arquivo inteiro — medido em 29/10/2026: ~11 MB/mês, ~31 MB em 3 meses, ~60 MB no gráfico de 6 meses; 12 s para a Ranbaxy em 3 meses (19,9 mil linhas, 239 lojas); a leitura por faixas (pyarrow S3FileSystem) fica pra quando os arquivos reais existirem): carregar a rede inteira (~1,6 mi de linhas em 3
meses) derrubaria o app no Streamlit Cloud. Na cópia das 4 lojas, a lista
abre em ~4 s com o login. A aba Dados limpa esse cache; a versão do banco é
conferida a cada 30 s e os arquivos da rede a cada 5 min.

## Pendências abertas (detalhe em `status_e_pendencias.md`)

1. Depois de publicar: rodar `python limpeza_compras_planilha.py --executar`
   (apaga as compras da planilha, a fila de CNPJ órfão e os registros de
   envio; mantém arquivos, fila de EAN, Base Genéricos). Conferir a
   cobertura de lojas da API (planilha de agosto = 336 lojas) e reanalisar
   as notas da MILLENIUM e a questão de embalagem com a rede online.
2. CORS do bucket para PUT a partir do endereço do app (painel da DigitalOcean) — opcional.
3. Arquivos de teste deixados no bucket pelas execuções antigas da suíte (~159 só em 23/09).
4. Trocar as credenciais expostas (Spaces e senha do Postgres) — última etapa do plano, por decisão do Mariano.
5. Itens 12–14 do plano (aceite automático, reprocessamento incremental).
