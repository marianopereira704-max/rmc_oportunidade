# RMC Oportunidades — status e pendências

Registro consolidado para retomar o trabalho sem precisar refazer a análise. Atualizado em 26/10/2026.

## Dashboard · Ruptura e histórico do estoque (27/10/2026, sem commit)

Página Ruptura redesenhada e aprovada (perda medida pelo histórico do estoque, projeção na transição, risco pela
frequência de venda, investimento pela sugestão do Assistente). Detalhes no `CLAUDE.md` e em
`docs/specs/2026-10-26-dashboard-ruptura-design.md`.

**Falta:**
- **A foto do estoque precisa rodar TODA noite** (o histórico depende disso). A 1ª coleta ficou agendada pelo Mariano
  no Windows para 00:05 de 28/10 (script `C:/Users/RMC - 0014/rmc_rotina/coleta_estoque.ps1`, modo `--so-estoque`, 8
  partes; logs em `rmc_rotina/logs`). Decidir: publicar só a rotina no GitHub (A) ou agendar nesta máquina toda noite (B).
- Conferir em 28/10 os logs e o 1º dia do histórico no Spaces (`pedido/historico_estoque/`).
- A API do GPS estava lenta em 27/10 (14,5 min para o estoque de uma empresa de 1 loja): medir a duração da coleta.
- Ao publicar o app: nada a carregar (o histórico vem da rotina).

## Classificação de produtos em cascata (25–26/10/2026, sem commit)

Feito (etapas C1–C7 do plano `docs/specs/2026-10-25-classificacao-produtos-plano.md`): 5 categorias fixas
(Propagado, Genérico, Similar, Perfumaria, Em classificação) pela ordem manual → IQVIA → CMED (só Genérico e
Similar); a FEBRAFAR não classifica mais, só identifica na cascata. Tabela nova `classificacao_ean` (migração
0019), seção "Classificação de produtos" nas Configurações (cascata + "Editar correspondência"), o Assistente
(Comprador) e o Dashboard usando a mesma classificação. Carregado só no banco **dev** e no Spaces
(`pedido/categorias/classificacao_ean.parquet`).

**Falta:**
- **Ao publicar:** a migração 0019 roda sozinha; depois, em Dados → Importar → Categorias, clicar em "Carregar
  fontes" no app publicado (o arquivo já está no Spaces). Sem isso, a classificação usa só a IQVIA e o manual.
- **Base inicial de categorias com a CMED nova:** a lista de 09/10/2026 é diferente da que está na base (que
  decide os dias de estoque). Atualizar é outra decisão — não foi feito (`--so-classificacao`).
- **MIP/OTC da FEBRAFAR:** analisar à parte (decisão de 25/10/2026).
- **1ª abertura depois de reiniciar o app:** 7–9 s para ler as fontes (uma vez por processo e por versão,
  compartilhado por todas as sessões). Se incomodar, ler a FEBRAFAR/CMED do parquet do Spaces em vez do banco.

## Guardado para depois (dependem de um resultado) — 07/10/2026

1. **Análise baixa o arquivo inteiro da rede** (o filtro por EAN só pula a
   decodificação): ~23 MB/mês estimado, ~140 MB no gráfico de 6 meses, tudo
   na memória do Streamlit Cloud. Fazer a leitura por faixas (pyarrow
   S3FileSystem: só o rodapé + blocos/colunas necessários) QUANDO o job
   `rede` tiver gerado os arquivos reais no Spaces — medir antes e depois.
2. **Carga inicial da rotina ~11 noites**: compras de 1 mês levam 1,7–4 min
   no GPS (medido 07/10, empresa de 1 loja); 4 partes ≈ 37 empresas/noite
   de 388. Opção: 8 partes (12 ≈ 4 noites), com risco de sobrecarregar o GPS
   (502 em 05/10). Decidir depois de ver o resultado da execução manual de
   07/10 e das primeiras noites com o código novo.

## Rotina do GPS — fases (08/10/2026, sem commit)

Fase 1 (carga de agosto e setembro para todas as empresas) → fase 2
(manutenção) sozinha; 8 partes; vínculo automático; código da loja sem
espaços. **Publicar os 8 arquivos da rotina** (`pedido/__main__.py`,
`coleta.py`, `plano.py`, `rede.py`, `transformar.py`, `armazenamento.py`,
`.github/workflows/rotina_pedido.yml`, `tests/test_pedido.py`) — conferido
contra o `main`: 365 testes passando. Os 40 arquivos vazios da empresa
690ba7e2… (código com espaços) ficam órfãos no bucket; a rotina baixa de
novo com o caminho certo.

## 00000. Rodada de 07/10/2026: login individual das lojas (etapas 1–5, concluído)

Feito (sem commit): (1) lojas da rede = status "Ativo" da T.I. (594) e grupo
econômico pela sigla; (2) login por e-mail das lojas, links de criar/recuperar
senha pelo Gmail; (3) aba **Cadastro** (ADM e Consultor) — lista pela loja da
barra, Novo/Editar, Atribuir Painéis, Atribuir Empresas (só do grupo),
e-mail de ativação com link pra copiar, Excluir/Reativar; a sincronização de
lojas tira sozinha o acesso a loja que saiu da rede ou do grupo (histórico em
`usuarios_lojas_historico`). Detalhes no `CLAUDE.md`.

**Falta:**
- (Feito na etapa 4: painéis atribuídos valendo na barra lateral e linhas de
  compra do Comprador no Assistente — por enquanto só Genéricos e
  Perfumaria se distinguem; Similares/Propagados dependem do catálogo.)
- (Feito na etapa 5: catálogo IQVIA — linhas por SETOR_NEC, personalizáveis
  por loja em Configurações de Pedidos; genéricos pelo GMRS. Carga única: o
  CD_PERIODO antigo não importa, não chegam catálogos novos.)
- Carregar o catálogo no banco real: `python -m pedido.carga_catalogo
  --catalogo <xlsx do Downloads> --executar` (grava no Spaces) e o botão
  Dados → Importar → Categorias de produtos → "Carregar catálogo".
- Ao publicar: `EMAIL_REMETENTE`, `EMAIL_SENHA_APP` e `APP_URL` nos Secrets do
  Streamlit Cloud.

## 0000. Rodada de 05–06/10/2026: Análise pela API do GPS

A Análise de Oportunidade (Por Loja e Por Produto) passou a ler as compras da
API do GPS (rotina da madrugada → arquivo da rede por mês, `pedido/rede.py`),
com todo produto da tabela Gruppy e as regras de preço do Assistente compra a
compra. Saíram: o envio da planilha do GPS, a fila de CNPJ órfão, a regra da
planilha (menor preço, recuo CMV/custo médio, preço médio 3m) e o script
`limpeza_gps_regra_antiga.py` (que também apagava a Gruppy — perigoso hoje).
Detalhes no `CLAUDE.md`.

**Pendente, depende do Mariano:**
1. Publicar (push no main) — a rotina nova só roda no GitHub depois disso; a
   primeira execução baixa 3 meses a mais de compras de cada empresa.
2. Depois de publicar: `python limpeza_compras_planilha.py` (prévia) e
   `--executar` — apaga as compras da planilha, a fila de CNPJ órfão e os
   registros de envio. Substitui a pendência 1 da rodada de 23/09 abaixo.
3. Com a rede online: conferir a cobertura de lojas (planilha de agosto = 336
   lojas), reanalisar as notas da MILLENIUM (~2,2× o cadastro, lojas Reis) e a
   questão de embalagem/fardo.

## 000. Rodada de 24/09/2026: colunas novas e ordenação nas análises

Regra nova da Análise de Oportunidade (detalhes no `CLAUDE.md`): filtro
**Laboratório** obrigatório; referência = **menor preço pago** (não mais a média
ponderada); quantidade = só da compra vencedora; recuo de preço
`VlrUnitario → Fat×%CMV÷QTD → R$ Custo médio` quando o VlrUnitario destoa mais
de ±50% do laboratório; bonificação (< R$ 0,10) fora; "a revisar" sem
economia; coluna **Preço médio (3m)**; ordenação clicando no cabeçalho.

- Código: `core/analise.py` (novo, substitui o cálculo antigo de
  `core/queries.py`), `views/analise_comum.py` (novo), as duas telas
  reescritas, `core/theme.py` (faixa, cabeçalho, borda de foco navy).
- Upload GPS volta a guardar Fat. líquido, % CMV, QTD, custo CMV por unidade e
  R$ Custo médio. **Migração `0005_campos_recuo_preco_gps`** (só acrescenta
  colunas) — aplica sozinha no próximo start do app, inclusive no banco de
  produção quando o app local subir.
- **Agosto precisa ser reenviado** (depois da limpeza) pra preencher esses
  campos; sem eles o recuo não acontece e mais linhas ficam "a revisar".
- Medido com agosto real (Ranbaxy): cálculo ~0,3 s por (laboratório,
  período), ordenar/filtrar em memória ~5 ms; o clique no cabeçalho leva
  ~1,3 s na tela (é o Streamlit redesenhando 50 linhas × 9 colunas — o banco
  não é consultado). Economia total de agosto contra Ranbaxy: R$ 31,5 mil
  (749 linhas "a revisar" não somam).
- Pendente/decisão futura: o gráfico de histórico do Detalhes do produto ainda
  mostra a média do VlrUnitario por mês, sem o recuo de preço.

## 00. Rodada de 22–23/09/2026: redesenho do upload do GPS

**Diagnóstico medido (não suposto):** o upload travava por três motivos somados. (1) O parse do .xlsx no servidor (openpyxl) levava 25s e +230 MB, antes de qualquer barra de progresso — com o Streamlit + Postgres em pouca memória, o processo morria (tela "Connecting…", nada gravado). (2) Rodando local, cada ida ao Postgres em NY custa 138 ms, e o código fazia 2 idas por linha de CNPJ órfão (~6 min só nisso) e 1 por EAN pendente. (3) A aba Dados redesenhava as 4 abas a cada clique, e a fila de EAN montava um seletor com todos os genéricos (2.210) em cada um dos até 200 itens.

**Achado de dados:** o arquivo de agosto veio **cortado pelo BI** no limite de 150 mil linhas ("Exported data exceeded the allowed volume") — e só ~65 mil linhas eram compra; o resto é linha só de venda.

**Regra de negócio nova (decidida):** linha válida = `VlrUnitario > 0` e `Quantidade > 0`; custo = `VlrUnitario` (sem ST); estoque saiu do upload, das telas e da prioridade da fila. Envio substitui, no mês, só os CNPJs presentes no arquivo — tudo ou nada.

**O que mudou no código:**
- `views/leitor_planilha.py` (novo): componente que lê o .xlsx NO NAVEGADOR (SheetJS), manda CSV compactado; o original vai direto ao Spaces por URL assinada, com recuo automático pro envio via app se o bucket recusar.
- `integrations/planilha_navegador.py` (novo): decodifica o que o navegador manda e lê o rodapé do BI (mês e aviso de corte).
- `integrations/gps.py` (reescrito): `preparar_compras` (puro) + `aplicar_compras` (uma transação, tudo em lote), filas recalculadas, órfãos em tabela própria (`compras_gps_orfas`), vínculo de órfão vale pros envios seguintes. Mapeamento automático prioriza `Quantidade` sobre `QTD` (vendida).
- `integrations/gps_processamento.py` (novo): processamento em thread sob demanda, trava de "um por vez" (`rotinas.reivindicar_trava`), resultado em `controle_rotinas`.
- Migração `0004_compras_gps_vlrunitario` (aditiva: colunas antigas viram opcionais + tabela nova) — **já aplicada no Postgres real** em 23/09.
- Tela Dados: abas e itens das filas preguiçosos; popup com divergência de mês (exige confirmação) e aviso de exportação cortada.
- Gruppy e Base Genéricos também passam a ler a planilha no navegador (processamento delas igual).
- `tests/conftest.py`: a suíte gravava arquivos de teste no bucket REAL do Spaces a cada execução; agora usa storage local temporário.

**Medido com o arquivo real de agosto:** leitura no navegador + envio ~15–18s (antes: 25s só de parse, no servidor); processamento em ~3–5s; 63.382 compras, 336 lojas, 6 CNPJs órfãos, 441 EANs na fila. Reenvio do mesmo mês não duplica nem infla filas (testado 3x).

**Pendente, depende do Mariano:**
1. Rodar `python limpeza_gps_regra_antiga.py --executar` (limpeza combinada dos meses da regra antiga — bloqueada pro Claude por ser exclusão em massa em produção). Depois, reenviar agosto (e janeiro, se quiser) pela tela.
2. Regra de CORS no bucket (painel da DigitalOcean) pro envio direto do original: origens `http://localhost:8501` e o endereço do app no Streamlit Cloud, método PUT, header `content-type`. Sem ela funciona igual, só o original passa pelo app.
3. ~159 arquivos de teste criados em 23/09 no bucket (e possivelmente mais de rodadas anteriores): `gps.xlsx`, `t.xlsx`, `Lab Um.xlsx` etc.
4. Testes antigos da regra anterior movidos para `Claude outputs/testes_regra_antiga_gps/` (não rodam mais).

## 0. Rodada de 15/09: infraestrutura de rotinas automáticas + cliente da API do GPS

Entregue e já aplicado no projeto (11 arquivos, 181 testes passando, 2 pulados — os dois de concorrência real contra Postgres, que só rodam com `RMC_TESTE_POSTGRES_DSN` definida).

**Arquivos novos:** `core/sql.py` (primitiva de upsert atômico, movida de `reconciliation/motor.py` para ser compartilhada), `core/rotinas.py` (controle de rotinas), `integrations/gps_api.py` (cliente da API do GPS), `alembic/versions/20260915_2100_controle_rotinas.py`, `tests/test_rotinas.py`, `tests/test_gps_api.py`.
**Arquivos alterados:** `core/models.py` (modelo `ControleRotina`), `core/config.py` (`GpsApiConfig`), `views/dados.py` (sincronização automática de lojas), `reconciliation/motor.py` (passa a importar a primitiva de `core/sql.py`), `tests/test_migracoes.py` (a simulação de "banco legado" usava os modelos de hoje para representar o esquema de ontem — corrigida para aplicar a migração de adoção e remover o carimbo, o que a mantém fiel a cada migração nova).

**ATENÇÃO — migração de banco:** a migração `0002_controle_rotinas` cria a tabela `controle_rotinas`. Ela é puramente aditiva (cria tabela nova, não toca em nenhuma existente), mas **é aplicada automaticamente na próxima vez que o app subir**, porque `core/db.py::_preparar_esquema` roda `upgrade head` no start. Não precisa rodar nada à mão; é só saber que vai acontecer.

**Aviso de comportamento novo:** ao abrir a aba Dados pela primeira vez no dia, a sincronização de lojas roda sozinha. Enquanto o item 17 (N+1 da sincronização) não for corrigido, isso leva alguns minutos — tem um aviso de carregamento na tela, mas é o motivo mais forte hoje para priorizar o item 17.

**Dois pontos da API do GPS que ficaram deliberadamente em aberto**, por não existir nenhuma resposta autenticada para conferir (a chave disponível em 15/09/2026 era recusada com 401). Os dois estão isolados em funções de uma linha em `integrations/gps_api.py` e **falham alto** em vez de adivinhar:
1. O envelope da resposta de listagem (lista pura? objeto com os itens em alguma chave?) — `_extrair_itens`.
2. O nome do campo de id da empresa em `/api/v1/empresas` — `_extrair_id_empresa`.
São a primeira coisa a confirmar quando a chave funcionar.

**O que o cliente do GPS ainda NÃO faz, de propósito:** converter o registro da API em `RegistroCompraGPS`. O ponto exato onde esse mapeamento entra está marcado em `GpsApiComprasAdapter.sincronizar`. Hoje ele coleta e relata (`registros_processados=0`, status MANUAL), o que prova autenticação/paginação/janela de datas ponta a ponta sem gravar nada. Há um teste que trava esse contrato.

**Segurança:** a API do GPS responde em HTTP puro (sem TLS), em endereço IP. A chave viaja em texto claro em toda requisição. Vale levar ao TI junto com a questão da chave inválida.

## 1. O que foi resolvido nesta rodada (12–15/09/2026)

**Sintoma relatado:** telas de análise ("Oportunidade por Loja" etc.) mostrando "Nenhuma loja encontrada com os filtros atuais", mesmo com GPS e Gruppy já carregados.

**Diagnóstico (via leitura direta do Postgres de produção/dev, só `SELECT`, script `diagnostico_producao_leitura.py` que ficou na raiz do projeto):**
- `lojas`: 805 registros — ok.
- `registros_compra_gps`: 22.587 linhas (967 EANs distintos em 2026-01, 1.932 em 2026-08) — ok.
- `base_genericos`: **0 registros**. Era a causa raiz — sem nenhum genérico canônico cadastrado, `resolver_ean` nunca tem contra o que casar (nem exato, nem fuzzy), então **zero** EANs foram resolvidos dos dois lados (GPS e Gruppy).
- `ean_genericos`: 0 (consequência direta do acima).
- `fila_resolucao_ean`: 2.028 pendentes (1.896 origem GPS, 132 origem Gruppy).
- `tabelas_gruppy_cobertura`: só **MG** tem cobertura (1 ATIVA, 1 INATIVA — a inativa é de um upload anterior da mesma planilha "Ranbaxy", corretamente superado pelo upload seguinte; não é bug).
- **Lojas por UF:** MG tem 432 das 805 lojas. As outras 373 (BA, ES, RJ, SP, PE, GO, PB, ...) não têm cobertura Gruppy nenhuma — nem ativa nem inativa. Ou seja, **mesmo com tudo resolvido, essas 373 lojas não vão aparecer nas análises até subir tabela Gruppy pra UF delas.** Isso não foi corrigido nem é bug — é uma decisão pendente (subir mais cobertura, ou aceitar que só MG está coberto por enquanto).

**Bug de código encontrado e corrigido:** `reconciliation/motor.py::reprocessar_fila_resolucao` (o botão "Reprocessar fila contra a base atual") ia direto pro fuzzy-match por descrição e **nunca checava se o EAN já tinha uma resolução exata em `EanGenerico`** — diferente de `resolver_ean` (usado no upload novo), que checa isso primeiro. Na prática: importar a planilha curada da Base Genéricos com o EAN exato de um item já pendente na fila não resolvia esse item sozinho ao clicar "Reprocessar fila", a não ser que o fuzzy-match batesse por coincidência no texto da descrição.

**Correção aplicada e já publicada no projeto real** (arquivos alterados, testados — 134 testes passando, 1 pulado — e escritos byte a byte iguais entre sandbox e o projeto real):
- `reconciliation/motor.py`: `reprocessar_fila_resolucao` agora carrega em lote o conjunto de EANs já resolvidos e resolve na hora qualquer item da fila cujo EAN já bate, antes de tentar fuzzy-match. Novo campo `resolvidos_por_ean_existente` no resultado.
- `views/dados.py`: mensagem do botão "Reprocessar fila" agora mostra esse número separado.
- `tests/test_reconciliation.py`: dois testes novos provando o cenário (EAN resolvido depois de cair na fila → reprocessa sem fuzzy-match) e a idempotência. Regressão provada manualmente (revertida a correção, os dois testes novos falharam como esperado, depois reaplicada).

**Fluxo confirmado e já usado pelo usuário com sucesso:** subir a planilha curada de Base Genéricos em Dados → Importar Planilhas, depois "Reprocessar fila contra a base atual" na Fila de Resolução de EAN — sem precisar apagar ou reenviar GPS/Gruppy.

## 2. Segurança — ainda pendente

Duas credenciais reais foram digitadas em texto puro nesta conversa e **ainda precisam ser trocadas** (não fiz a rotação, só usei uma delas uma vez pra ler o banco, com autorização, e depois nunca mais):
- Chave de acesso do DigitalOcean Spaces (bucket `consultoria-interna`, região `nyc3`) — trocar em DigitalOcean → API → Spaces Keys, depois atualizar `.streamlit/secrets.toml`.
- Senha do usuário `rmc_oportunidades_dev_user` do Postgres (`157.230.215.66:5432/rmc_oportunidades_dev`) — trocar no painel do banco (ou via `ALTER USER ... WITH PASSWORD ...` se for Postgres em droplet próprio), depois atualizar a `DATABASE_URL` em `.streamlit/secrets.toml`.

## 3. Status dos 22 itens do plano de reforma original

### Fase 0 — Fundação
- **01. Alembic (controle de migração)** — ✅ feito.
- **02. `pool_pre_ping`/`pool_recycle`/`pool_size`/`max_overflow`** — ✅ feito (`core/db.py`).
- **03. Banco local pro ciclo de dev** — ⏸️ adiado de propósito, por decisão sua. Retomar quando quiser.

### Fase 1 — Ingestão (parar de conversar com o banco linha a linha)
- **04. N+1 em `resolver_ean`** — ✅ feito (absorvido pelo item 05).
- **05. Reescrita vetorizada da ingestão GPS** (normalização em pandas, EANs distintos, cache em lote) — ✅ feito.
- **06. Inserção atômica em bloco na fila** (`INSERT ... ON CONFLICT DO NOTHING` + `UPDATE` atômico) — ✅ feito, inclusive provado contra Postgres real com concorrência de verdade.
- **07. Fila parar de acumular por soma → virar recalculado a partir de `registros_compra_gps`** — ❌ pendente. Hoje ainda soma incrementalmente (atômico e sem corrida desde o item 06, mas ainda soma — reenviar o mesmo arquivo GPS duas vezes ainda infla `valor_total_acumulado`/`qtd_ocorrencias` da fila, embora **não afete o cálculo final de economia**, que lê direto de `registros_compra_gps`).
- **08. Reconciliação disparando também no ramo de UPDATE de linha já existente** — ❌ pendente (empacotado junto do 07).
- **09. Mesma reescrita vetorizada pro Gruppy** (`integrations/gruppy.py`) — ❌ pendente. Ainda deve estar linha a linha, com `session.add_all` no fim (mesmo padrão que o GPS tinha antes do item 05).
- **10. Trocar `pd.read_excel` por engine `calamine`** — ❌ pendente. **Se o GPS virar API, este item some do escopo do GPS** (continua valendo só pro Gruppy, se ele continuar manual).
- **11. Cópia Parquet ao lado do xlsx original** (acelera reprocessamento de CNPJ órfão) — ❌ pendente. **Também some do escopo do GPS se ele virar API.**

### Fase 2 — Reconciliação como processo próprio
- **12. Fim do aceite automático em todo lugar** (`classificar` perder o nível "auto", tudo virar sugestão pendente de confirmação humana) — ❌ pendente. Decisão sua, registrada no plano original — **confirmar se ainda quer isso**, já que muda a experiência (fila cresce mais).
- **13. `limpar_eans_sujos` sair do caminho quente do reprocessamento** (hoje carrega 3 tabelas inteiras toda vez que roda) — ❌ pendente.
- **14. Reprocessamento incremental** (só reavaliar o que mudou, não a fila inteira) — ❌ pendente.
- **15. Tabela de controle de rotinas** (última tentativa / último sucesso / último erro) — ✅ feito (`core/models.py::ControleRotina` + `core/rotinas.py`). Inclui trava atômica contra execução dupla (`reivindicar`), provada com teste de concorrência real contra Postgres.
- **16. Gatilho automático do reprocessamento da fila de EAN, com botão de forçar** — ⚠️ **parcial: a infraestrutura existe, o gatilho NÃO foi ligado — de propósito.** Ligar o reprocessamento para rodar sozinho todo dia hoje seria criar um gargalo diário, porque ele ainda depende de `limpar_eans_sujos` (item 13: carrega três tabelas inteiras) e ainda é varredura total (item 14). Fazer os itens 13 e 14 primeiro, e só então ligar o gatilho — que é uma chamada de `rotinas.executar_se_necessario`, igual à que já está funcionando para as lojas.

### Fase 3 — Base de lojas
- **17. N+1 na sincronização de lojas** (`integrations/sistema_interno.py` linha ~109, um SELECT por loja dentro do loop) — ❌ pendente. Confirmado no código — foi a causa dos 5 minutos de sincronização já sentidos na prática. Risco baixo. **Ficou mais urgente depois do item 18:** agora essa espera acontece sozinha, na primeira abertura da aba Dados de cada dia.
- **18. Sincronização automática diária de lojas, com falha em pop-up sem derrubar a tela** — ✅ feito (`views/dados.py::_sincronizar_lojas_automatico`). Roda na primeira abertura da aba Dados do dia; falha vira pop-up (`@st.dialog`) mantendo os dados anteriores visíveis; o botão manual virou "Forçar sincronização agora" e a tela mostra data do último sucesso e da última falha. Se as credenciais não estiverem configuradas, nem tenta (e não marca o dia como feito).

### Fase 4 — Consistência e verificação
- **19. Base Genéricos não depender de reenvio pra valer** — ✅ **feito por completo agora.** Já estava verificado pro caminho de upload novo (`resolver_ean` checa EAN exato primeiro); o que faltava — o caminho de reprocessamento da fila — foi a correção aplicada nesta rodada (ver seção 1).
- **20. Remover o aviso de dependência de ordem GPS/Gruppy na tela** — ❌ pendente. O texto ainda está em `views/dados.py` ("precisa rodar antes do upload do GPS pra ter loja cadastrada pra bater o CNPJ").
- **21. Reescrever a suíte de testes pro contrato novo** — ❌ pendente, e travado pelo item 12 (os testes atuais, inclusive os que adicionei agora, ainda assumem que o aceite automático existe).
- **22. Medir antes/depois com log de tempo por etapa do upload** — ❌ pendente.

**Contagem: 8 de 22 feitos** (01, 02, 04, 05, 06, 15, 18, 19), 1 parcial de propósito (16 — infraestrutura pronta, gatilho esperando os itens 13/14), 1 adiado por escolha (03), **12 pendentes** (07, 08, 09, 10, 11, 12, 13, 14, 17, 20, 21, 22).

### Fora de escopo, por decisão já registrada (não contam nos 22)
- **23.** Tabela resumo pré-agregada pras telas de Oportunidade (hoje ~13s de carregamento) — projeto próprio, só faz sentido depois da ingestão estar correta.
- **24.** `COPY` do Postgres pra carga em massa — ganho pequeno a mais depois da Fase 1, custo de manutenção maior (só Postgres, quebra paridade com SQLite dos testes).
- **25.** Processamento em segundo plano (worker) — reavaliar só quando o upload não couber mais numa requisição do Streamlit.
- **26.** Desnormalizar `base_generico_id` nas tabelas de compra/Gruppy — descartado, quebraria a propriedade central de `EanGenerico` como fonte única de junção.

## 4. Se o GPS virar API — o que muda nessa lista

- **Somem do escopo do GPS:** itens 10 e 11 (eram só sobre ler/cachear arquivo xlsx). Continuam valendo pro Gruppy se ele seguir manual.
- **Viram pré-requisito de arquitetura, não "quando sobrar tempo":** itens 15 e 16 (tabela de controle de rotina + gatilho automático) — é literalmente o padrão que a sincronização de lojas (item 18) já usa, e que uma API de GPS precisaria copiar: checa se já rodou hoje, roda sozinho se não rodou, erro vira pop-up sem apagar dado anterior, botão de forçar continua existindo.
- **Ficam mais importantes, não menos:** itens 12, 13, 14 — hoje existe um humano olhando a planilha antes dela virar dado; numa API esse humano some do meio do caminho, e as redes de segurança da reconciliação passam a ser a única proteção contra erro silencioso.
- **Risco novo, não estava nos 22 itens** (porque eles assumiam um humano clicando "enviar" uma vez de cada vez): a gravação de `registros_compra_gps` em `integrations/gps.py` (~linha 610-644) carrega as chaves existentes em memória e decide inserir/atualizar — seguro pra um upload manual por vez, mas não atômico do jeito que a fila ficou depois do item 06. Uma sincronização automática por API (cron rodando sozinho, sobreposto a alguém clicando "forçar agora", ou duas rodadas se sobrepondo) pode colidir na mesma chave (loja, EAN, mês). Precisaria do mesmo tratamento `ON CONFLICT ... DO UPDATE` atômico aplicado à tabela de compras, mais um travamento simples pra impedir duas sincronizações concorrentes.
- **Pontos novos a decidir, fora da lista de 22:** onde fica a credencial da API do GPS (mesmo tratamento de `secrets.toml` que `DATABASE_URL`/Spaces — nunca hardcoded); e o que substitui o arquivo original como registro de auditoria (hoje o xlsx fica guardado no Spaces; sem arquivo, precisa decidir se guarda a resposta bruta da API ou confia só nos campos de auditoria que já existem no banco).

## 5. Sugestão de próxima ordem (não decidido, só recomendação)

1. **Item 17 (N+1 da sincronização de lojas)** — subiu para primeiro lugar: agora que a sincronização roda sozinha na primeira abertura do dia, aqueles ~5 minutos viraram espera automática na cara do usuário. Risco baixo, ganho imediato e visível.
2. **Itens 13 e 14 (reprocessamento leve e incremental)** — depois deles, ligar o gatilho do item 16 é uma linha de código, usando a infraestrutura que já existe.
3. **Item 09 (reescrever a ingestão do Gruppy)** — continuação natural do padrão já validado no GPS (itens 05/06).
4. **Itens 07/08 (fila deixar de somar incrementalmente)** — antes que o mesmo arquivo GPS seja reenviado muitas vezes.

Do lado do GPS-API, o próximo passo não é código: é a chave funcionar. Com ela, a ordem é (a) confirmar os dois pontos em aberto (envelope e id da empresa), (b) ver uma resposta real de `/compras` e escrever o mapeamento de campos, (c) ligar a sincronização na rotina automática, (d) tratar a corrida de escrita em `registros_compra_gps` descrita na seção 4.

---

# 6. API do GPS — investigação com a chave funcionando (16/09/2026)

A chave passou a funcionar (o problema era o valor colado no campo Authorize, não a chave em si). Tudo abaixo é dado REAL, lido da API, não suposição.

## 6.1 Os dois pontos que estavam em aberto: confirmados

- **Envelope:** `{"data": [...], "meta": {"total": N, "limit": N, "offset": N}}`. O `_extrair_itens` já aceita `data` — nada a mudar. **Melhoria possível:** existe `meta.total`, que é um sinal de fim de paginação melhor que o atual ("página veio menor que o limite").
- **Id da empresa:** o campo é `idEmpresa` (primeiro candidato da lista do `_extrair_id_empresa`) — nada a mudar. O valor é um ObjectId de Mongo, ex: `6901107ed4a5e05f99b7f4ab`.

## 6.2 Formato real de uma compra (`/api/v1/empresas/{idEmpresa}/compras`)

38 campos por linha. Os que interessam para `RegistroCompraGPS`:

| Campo do nosso modelo | Campo da API | Situação |
|---|---|---|
| `ean` | `ProdutoRelacionado_CodigoBarras` | ✅ ex: `7898040321420` |
| `descricao_origem` | `ProdutoRelacionado_NomeProduto` | ✅ ex: `ANNITA 20MG 45ML` |
| `laboratorio_compra` | `ProdutoRelacionado_NomeLaboratorio` | ✅ ex: `FQM - FARMOQUIMICA` |
| `ano_mes` | derivado de `DataEmissaoNF` | ✅ ex: `2026-08-05T00:00:00` |
| `quantidade` | `Quantidade` | ⚠️ ver `Fracao` abaixo |
| `custo_unitario` | `VlrTotalLiquido` ÷ `Quantidade` | ✅ decidido (ver 6.4) |
| `estoque` | **não existe nesta rota** | ❌ ver 6.5 |
| `loja_id` | **não há CNPJ** | ❌ ver 6.3 — é o bloqueio principal |

Campos extras úteis que hoje não temos: `NroNF`, `CodigoCompra` (nota fiscal — dá rastreabilidade real), `ProdutoRelacionado_PrincipioAtivo`, `ProdutoRelacionado_NomeGrupo`/`NomeCategoria`, e todo o bloco `FornecedorRelacionado_*` (permitiria saber de QUEM a loja comprou, coisa que a planilha não dava).

## 6.3 BLOQUEIO PRINCIPAL: não há CNPJ de loja

`LojaRelacionada_CNPJ` vem `null`, e o CNPJ do fornecedor vem mascarado/criptografado (`9QEIocDlSbWW5kh9859kEQ==`). Confirmado com o cliente: **a origem dos dados da API não tem CNPJ — a identificação é por razão social.**

Isso é grave porque a ingestão inteira do GPS liga compra → loja por CNPJ (é a chave de `Loja`, vinda do sistema interno), e existe até uma fila de CNPJ órfão para o que não bate.

Complicação adicional observada no dado real: `LojaRelacionada_RazaoSocial` veio como string VAZIA na amostra, e no nível de empresa o `RazaoSocial` às vezes vem preenchido, às vezes vazio (com `NomeFantasia` preenchido no lugar). Ou seja, nem a razão social é um campo confiável em toda linha.

**Proposta (não implementada, precisa de decisão):** resolver isso com o mesmo padrão que o projeto já usa e já provou para EAN — uma fila de reconciliação de LOJA: casar `(idEmpresa, CodigoLoja)` da API com a `Loja` do RMC, com sugestão automática por similaridade de nome/cidade/UF, confirmação humana, e o vínculo gravado uma vez e reaproveitado para sempre (igual `EanGenerico` faz com EAN). Nunca casar sozinho por nome: atribuir a compra de uma farmácia a outra é pior que não atribuir.

## 6.4 Decisões tomadas pelo cliente (16/09/2026)

- **Transferências entre lojas ficam de fora.** Só `tipoCompra=0` (compra de fornecedor). Transferência interna não é uma compra que poderia ter sido feita na RMC. Observação: as 3 linhas da amostra eram todas transferências (`TipoCompra: "1"`, com `OrigemTransferencia`/`DestinoTransfencia` preenchidos e o "fornecedor" sendo outra loja da própria rede) — então esse filtro também corta bastante volume.
- **O custo vem direto da API:** `VlrTotalLiquido ÷ Quantidade`, já líquido de desconto. Não se tenta mais reproduzir a conta antiga da planilha (faturamento × %CMV), que aliás seria impossível: a API não traz nem faturamento nem %CMV.

## 6.5 Pontos ainda em aberto (NÃO inferir — perguntar)

1. **Estoque.** A planilha trazia estoque na mesma linha da compra. A API não: existe uma rota separada (`/empresas/{idEmpresa}/estoque`). Decidir se o estoque passa a vir de uma segunda chamada (e com que frequência), ou se deixa de existir nessa análise.
2. **`Fracao`.** Na amostra vale 1, então não deu para inferir o significado. Se for "unidades por caixa", o custo unitário precisa levá-la em conta para comparar com o preço da Gruppy — senão compara caixa com unidade.
3. **Quais das 384 empresas interessam.** A API tem 384 empresas; o RMC tem 805 lojas. Provavelmente só um subconjunto é cliente RMC. Sincronizar as 384 seria trabalho jogado fora (ver 6.6).

## 6.6 Volume e desempenho — muda o desenho da sincronização

Medido de verdade: **uma única empresa (6 lojas) tem 19.460 linhas de compra em 45 dias.** E a chamada demorou dezenas de segundos mesmo pedindo `limit=3` — ou seja, o custo está na consulta do lado do servidor, não no tamanho da resposta.

Extrapolando para as 384 empresas, uma varredura completa da janela de 45 dias seria da ordem de milhões de linhas e milhares de chamadas lentas. **A sincronização "itera todas as empresas todo dia" não é viável como desenhada.** Caminhos possíveis, em ordem de preferência:

1. Sincronizar só as empresas que têm loja cliente do RMC (depende de resolver o 6.3).
2. Janela de sobreposição menor no dia a dia (ex: 7 dias) e uma carga histórica separada, feita uma vez.
3. Aplicar o filtro `tipoCompra=0` na própria API (já decidido) — corta volume na origem.

## 6.7 Restrições e decisões confirmadas em 17/09/2026

- **A API trava se um processamento passar de 10 minutos.** Isso praticamente decide o desenho: a sincronização tem que ser feita de muitas chamadas pequenas, nunca de uma varredura grande. O script de medição já foi escrito assim (uma chamada por empresa, com cache retomável a cada uma).
- **Não existe relação IdEmpresa → CNPJ, e não vai existir.** O modelo é: identificar o `idEmpresa` UMA vez pela razão social, gravar, e usar só o id em toda consulta seguinte.
- **Granularidade decidida:** uma loja do RMC (uma linha de `lojas`, com CNPJ) corresponde a um par **(idEmpresa, CodigoLoja)** da API — não à empresa inteira.
- **Confirmado no dado real:** no detalhe da empresa, as lojas vêm com `CNPJ: null` e `RazaoSocial: ""` (todas as 6 lojas da MEGA FARMA). Só há `NomeLoja`/`NomeFantasia`, cidade, UF e CEP. Já na busca por "reis", a razão social das EMPRESAS vinha preenchida. Ou seja: razão social existe em parte da base e falta em outra — o casamento precisa usar o melhor texto disponível, com confirmação humana.
- **Envelope das rotas de detalhe é diferente:** `{"data": {...}}` (objeto), contra `{"data": [...]}` (lista) nas listagens. Tratado em `ClienteGpsApi.detalhe_empresa`.

## 6.8 Medição do casamento de lojas (entregue, aguardando execução)

`medir_casamento_lojas.py` na raiz do projeto. Só leitura dos dois lados (SELECT no banco, GET na API). Puxa as 384 empresas, busca as lojas de cada uma, compara com as 805 lojas do RMC e classifica cada loja em: ALTA CONFIANCA, AMBIGUO, SUGESTAO FRACA, SEM CORRESPONDENCIA, UF SEM LOJA RMC, SEM NOME NA API. Gera `relatorio_casamento_lojas.csv` com o detalhe linha a linha.

Para rodar, precisa acrescentar em `.streamlit/secrets.toml`:

    GPS_API_BASE_URL = "http://143.244.153.213"
    GPS_API_KEY      = "<a chave>"

O número que decide o desenho da sincronização é o último que ele imprime: **quantas das 384 empresas têm ao menos uma loja casada com alta confiança**. Só essas precisariam entrar na sincronização diária de compras — se forem poucas, o problema de volume descrito em 6.6 deixa de existir.

Comparação de nomes: normalização própria (sem acento, sem pontuação, sem sufixo societário tipo LTDA/ME/EPP) — de propósito NÃO reaproveita o normalizador de medicamentos, que expande abreviações farmacêuticas e distorceria nome de empresa. Casa só dentro da mesma UF, com `strip()` na UF (a API devolve `'RN            '` em parte dos registros). Caso com segundo candidato a menos de 5 pontos do primeiro é marcado AMBIGUO e nunca alta confiança — há teste provando isso, porque casar errado significa lançar a compra de uma farmácia na conta de outra.

## 6.9 Qualidade do dado (tratar na ingestão)

- `Estado` vem com preenchimento à direita em parte dos registros (`"RN                "` vs `"RN"`). Comparar UF sem `strip()` produziria erro silencioso — inclusive na cobertura Gruppy, que é por UF.
- `ProdutoRelacionado_PrincipioAtivo` vem com espaço à esquerda (`" NITAZOXANIDA"`).
- `RazaoSocial` vem como string vazia em vez de `null` em parte dos registros.
