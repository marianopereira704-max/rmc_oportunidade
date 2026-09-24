# RMC Oportunidades — status e pendências

Registro consolidado para retomar o trabalho sem precisar refazer a análise. Atualizado em 15/09/2026 (segunda rodada do dia).

## 0. Última rodada: infraestrutura de rotinas automáticas + cliente da API do GPS

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
