# RMC Oportunidades

Sistema de comparativo de preço de compra e de sugestão de pedido das
farmácias da Rede: mostra, loja a loja e produto a produto, quanto cada uma
deixou de economizar comprando de outro fornecedor em vez da RMC, e monta a
sugestão de compra de cada loja.

O sistema cruza quatro fontes:

- **API do GPS** — o que cada loja vendeu, comprou (nota a nota) e tem em
  estoque, baixado toda madrugada pela rotina do GitHub Actions para o
  Spaces (pacote `pedido/`);
- **Gruppy** — as tabelas de preço da RMC, por laboratório e por UF;
- **Base Genéricos** — qual genérico cada EAN é (EANs diferentes do mesmo
  genérico somam juntos);
- **base de categorias** — que tipo de produto cada EAN é (FEBRAFAR + CMED +
  classificação manual).

Economia perdida, por loja e produto da tabela Gruppy, contra **um laboratório
por vez** (escolhido no filtro principal): a soma de `(preço pago − preço do
laboratório) × quantidade` de cada compra acima da tabela no período (1 a 3
meses fechados). Regras em `core/analise_api.py`; sempre calculada na hora.

Telas: login, **Análise de Oportunidade** (Por Loja e Por Produto),
**Assistente de pedido**, **Configurações de Pedidos** e **Dados** (só admin:
pendências, importação de planilhas, rotinas e Explorador de Arquivos).
`Dashboard` ainda é tela de "próxima fase".

Guia detalhado de funcionamento (passo a passo de cada envio, filas, regras):
**`CLAUDE.md`**. Pendências e histórico de decisões: **`status_e_pendencias.md`**.

## Onde o sistema roda

| Peça | Onde |
|---|---|
| App publicado | Streamlit Community Cloud, publicado a partir do branch `main` deste repositório (push no `main` = nova versão no ar) |
| Banco de dados | Postgres num droplet DigitalOcean (1 GB RAM / 1 vCPU, Nova York). O firewall libera os IPs de saída do Streamlit Cloud; o app avisa o admin se a lista oficial desses IPs mudar |
| Arquivos (planilhas originais) | DigitalOcean Spaces, bucket `consultoria-interna` (compartilhado com outros sistemas) |
| Lojas | API do sistema interno, sincronizada automaticamente uma vez por dia |

> **Atenção:** o `.streamlit/secrets.toml` da máquina de desenvolvimento aponta
> para o banco e o bucket de **produção**. `streamlit run app.py` local grava
> nos mesmos dados que o cliente vê. Para testar sem risco, veja
> "Rodar sem tocar em produção" abaixo.

## Como rodar localmente

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows (opcional)
pip install -r requirements.txt
streamlit run app.py
```

Acesse http://localhost:8501 e entre com:

| Papel | CNPJ (login) | Senha |
|---|---|---|
| Admin | `30.208.213/0001-74` | `adm123` |
| Consultor | `30.208.213/0001-74` | `consultor` |

O admin vê a aba extra **Dados**; o consultor vê Análise de Oportunidade,
Pedido e Dashboard.

### Rodar sem tocar em produção

Com `RMC_IGNORAR_SECRETS=1`, o sistema ignora o `secrets.toml` e usa só
variáveis de ambiente — sem nenhuma, cai no modo dev: SQLite em `data/app.db`
e arquivos em `data/local_storage/`.

```bash
# PowerShell
$env:RMC_IGNORAR_SECRETS = "1"
python -m data.seed        # gera dados de exemplo (lojas, genéricos, preços, ~100k compras)
streamlit run app.py

# Git Bash
RMC_IGNORAR_SECRETS=1 python -m data.seed
RMC_IGNORAR_SECRETS=1 streamlit run app.py
```

`data.seed` **apaga** lojas, genéricos, tabelas Gruppy e compras antes de gerar
o exemplo — por isso ele se recusa a rodar se o banco não for SQLite.

Alternativa para a tela: `streamlit run app.py --secrets.files=<caminho de um secrets de teste>`.

## Credenciais (`.streamlit/secrets.toml` ou variáveis de ambiente de mesmo nome)

- **`DATABASE_URL`** — Postgres (`postgresql+psycopg2://usuario:senha@host:5432/banco`). Sem ela, SQLite local.
- **`DO_SPACES_ENDPOINT`, `DO_SPACES_REGION`, `DO_SPACES_BUCKET`, `DO_SPACES_ACCESS_KEY`, `DO_SPACES_SECRET_KEY`** —
  DigitalOcean Spaces. Sem elas, os arquivos ficam em disco local (só dev).
- **`SISTEMA_INTERNO_BASE_URL`, `SISTEMA_INTERNO_TOKEN`** — API de lojas
  (`GET` na URL, header `X-API-KEY`; só lojas com `active=true` entram).
- **`GPS_API_BASE_URL`, `GPS_API_KEY`** — API do GPS, usada pela rotina da
  madrugada (`python -m pedido`, GitHub Actions — passo a passo em
  `docs/rotina_pedido.md`).
- Opcionais: limiares da reconciliação (`RECON_LIMIAR_AUTO`, padrão 92;
  `RECON_LIMIAR_MEDIA`, padrão 75), sinônimos de coluna (`COLUNAS_GRUPPY`,
  `COLUNAS_BASE_GENERICOS`) — ver `core/config.py`.

## Como os dados entram

Ordem recomendada: **lojas → Base Genéricos → Gruppy → filas**. Nenhum dado
se perde se a ordem mudar; ela só evita trabalho manual nas filas.

1. **Lojas** — automático na primeira abertura da aba Dados de cada dia. Botão
   "Forçar sincronização agora" para rodar na hora.
2. **Base Genéricos** — planilha curada (EAN + nome canônico). A descrição já é
   o nome do genérico; EAN já resolvido nunca é sobrescrito. Depois de subir,
   use **Reprocessar fila contra a base atual** na fila de EAN.
3. **Gruppy** — uma tabela por laboratório, com qualquer produto: escolha o
   laboratório, as UFs que a tabela cobre e se o custo vem pronto ou como
   preço bruto + % de desconto. Tabela nova do mesmo laboratório substitui só
   as UFs repetidas.
4. **Vendas, compras e estoque** — automáticos, pela API do GPS (rotina da
   madrugada). A planilha do GPS saiu em 05/10/2026. Compras pela data de
   emissão da nota; o mês fechado só aparece na Análise a partir do dia 6 do
   mês seguinte (notas lançadas com atraso).

As planilhas são **lidas no navegador** de quem envia (componente em
`views/leitor_planilha.py`): o servidor recebe só os dados compactados, e o
.xlsx original vai para o Spaces.

## Análise de Oportunidade

- **Filtro Laboratório obrigatório:** a análise é sempre contra uma tabela
  Gruppy por vez, e a coluna de preço leva o nome do laboratório.
- **Por Loja** (só admin): Loja | Localização | Responsável | Produtos |
  Economia; o detalhe mostra as compras da loja, uma linha por produto e nota
  fiscal.
- **Por Produto:** uma linha por loja e produto — preço médio das notas acima
  da tabela, Qtd. e Economia dessas notas; filtro de Categoria; o detalhe tem
  o gráfico de preço por mês e as notas da maior diferença para a menor.
- Clique no título de qualquer coluna para ordenar; de novo, inverte.
- "A revisar" = preço com cara de erro (embalagem ou fração), pelas regras de
  cadastro × nota, preço de venda e mediana; não soma economia. Regras
  completas no `CLAUDE.md`.

## Filas de pendência (aba Dados → Pendências)

- **Fila de Resolução de EAN:** EAN genérico (pela base de categorias) ainda
  sem genérico na Base Genéricos — de tabelas Gruppy, compras e vendas das
  lojas. O fuzzy-match resolve sozinho com score ≥ 92, sugere entre 75 e 92, e
  deixa manual abaixo de 75 (itens "Loja (API)" sempre pedem confirmação). O
  valor de cada item é o dinheiro em jogo, regravado uma vez por dia.
- **Vínculo de lojas GPS:** qual loja do GPS é qual loja da RMC (automático
  pelo CNPJ quase sempre; o resto, confirmação).
- **Sem Classificação:** produtos sem categoria, em duas sub-abas (vendidos
  nas lojas; EANs das tabelas Gruppy).

## Por que essa arquitetura

- **Postgres para cadastros e decisões, Spaces para os dados da API.** A
  rotina grava vendas, compras e estoque em Parquet; a Análise lê as compras
  da rede por mês já filtradas pelos EANs do laboratório escolhido, e o
  resultado fica em memória — ordenar, paginar e abrir detalhes não leem
  nada de novo.
- **Nada de consulta ao banco dentro de laço.** O banco fica em Nova York; cada
  ida e volta custa ~140 ms a partir do Brasil. Toda ingestão grava e consulta
  em lote.
- **Leitura de planilha no navegador.** O parse de um .xlsx grande no
  servidor levava dezenas de segundos e centenas de MB, e derrubava o app por
  falta de memória.
- **`EanGenerico` é a única junção EAN → genérico.** Resolver um EAN uma vez
  conserta todo o histórico, dos dois lados, sem reprocessar nada.
- **Explorador de Arquivos nunca exclui de verdade.** "Inativar" move para
  `_Inativos`; "Excluir definitivamente" só existe para envios da Gruppy.
- **Navegação manual na sidebar** (não `pages/`): o login precisa travar toda a
  navegação e o papel decide quais seções aparecem.

## Estrutura do projeto

```
app.py                      # entrada: migrações -> login -> sidebar -> seção ativa
core/
  config.py                 # toda configuração (secrets -> env -> default)
  models.py                 # tabelas (SQLAlchemy)
  db.py                     # engine/pool, sessão, migrações no start, usuários fixos
  analise_api.py            # cálculo da Análise de Oportunidade (compras da API)
  analise.py                # filtros, ordenação, indicadores
  queries.py                # listas dos filtros
  rotinas.py                # controle de rotinas: diária e trava "um por vez"
  sql.py                    # upsert atômico portátil (Postgres/SQLite)
  auth.py, monitoramento.py, security.py, theme.py, ui.py
integrations/
  planilha_navegador.py     # planilha lida no navegador
  fila_compras.py, fila_loja.py  # fila de EAN pelas compras e pelas vendas da API
  gruppy.py                 # tabelas de preço, vigência por UF
  base_genericos.py         # importação da planilha curada
  sistema_interno.py        # sincronização de lojas
  gps_api.py                # cliente da API do GPS (usado pela rotina)
  mapeamento.py, base.py, categorias.py, gps_vinculo.py, pedido_*.py
reconciliation/
  motor.py                  # EAN -> genérico: fuzzy-match, filas, reprocessamento
  normalizador.py
storage/
  filesystem.py             # Explorador de Arquivos (Spaces + fallback local)
views/
  leitor_planilha.py        # componente que lê .xlsx no navegador
  dados.py, login.py, oportunidade_loja.py, oportunidade_produto.py, pedido.py, dashboard.py
alembic/versions/           # migrações do banco
tests/                      # pytest (SQLite + storage local temporário)
data/seed.py                # dados de exemplo (só SQLite)
pedido/                     # rotina da madrugada (API do GPS -> Spaces), cálculo do pedido, compras da rede
limpeza_compras_planilha.py # apaga as compras antigas da planilha do GPS (prévia; --executar aplica)
```

## Testes

```bash
python -m pytest -q
```

Rodam em SQLite em memória/arquivo temporário e com storage local temporário
(`tests/conftest.py`) — nunca tocam no banco nem no bucket reais. Os testes com
concorrência real contra Postgres só rodam com `RMC_TESTE_POSTGRES_DSN` definida.

### Teste visual e de contrato das telas

```bash
python -m tests.visual.rodar              # compara com as referências
python -m tests.visual.rodar --atualizar  # a tela atual vira a referência
```

Fica fora do `pytest` porque sobe o app e abre um navegador (Playwright +
Chromium). Monta um SQLite descartável com `data.seed`, passa ao app um
secrets próprio (nunca o `.streamlit/secrets.toml` real), percorre login,
Por Loja, Por Produto, Detalhes, Pedido, Dashboard e Dados em 1280 e 1920 px e:

- confere se os seletores da estrutura interna do Streamlit usados pelo CSS
  (`core/theme.py`, `SELETORES_STREAMLIT`) ainda existem;
- compara cada captura com `tests/visual/referencia/`; o que mudou sai
  destacado em `tests/visual/resultado/`.

Rodar antes de atualizar o Streamlit (a versão é fixada em `requirements.txt`).
Mudança visual intencional: `--atualizar` e versionar as imagens novas.

## Migrações de banco (Alembic)

O esquema é versionado com Alembic. **Nunca** altere uma tabela à mão: toda
mudança em `core/models.py` precisa de uma migração em `alembic/versions/`, e o
teste `tests/test_migracoes.py::test_migracoes_produzem_o_mesmo_esquema_dos_modelos`
falha se as duas coisas saírem de sincronia.

O app aplica as migrações pendentes **sozinho no start** (`core/db.py::_preparar_esquema`);
se uma falhar, o app não sobe. Um banco anterior ao Alembic é adotado
automaticamente, sem recriar nada.

**Regra enquanto o app publicado estiver com código antigo:** migração só pode
acrescentar (tabela/coluna nova, afrouxar NOT NULL). Apagar ou renomear quebraria
o app no ar, que usa o mesmo banco.

Depois de mexer nos modelos:

```bash
# contra um SQLite descartável, pra não gerar a migração olhando o banco de produção
RMC_IGNORAR_SECRETS=1 DATABASE_URL=sqlite:///caminho/teste.db alembic upgrade head
RMC_IGNORAR_SECRETS=1 DATABASE_URL=sqlite:///caminho/teste.db alembic revision --autogenerate -m "descrição curta"
# revise o arquivo gerado em alembic/versions/
```

A URL do banco não fica no `alembic.ini`: `alembic/env.py` lê de
`core.config.settings.db.url`. **Sem `RMC_IGNORAR_SECRETS=1`, os comandos
`alembic` rodam contra o banco de produção.**

Outros comandos: `alembic current`, `alembic history`, `alembic downgrade -1`,
`alembic upgrade head --sql` (só imprime o SQL).

## Deploy

Publicar = merge/push no `main` do GitHub; o Streamlit Community Cloud republica
sozinho e o app aplica as migrações pendentes ao subir. As credenciais do app
publicado ficam nos *Secrets* do Streamlit Cloud (mesmos nomes do `secrets.toml`).

Para o envio direto do .xlsx do navegador ao Spaces, o bucket precisa de uma
regra de CORS liberando `PUT` (header `content-type`) a partir do endereço do
app — configurável no painel da DigitalOcean. Sem ela o envio funciona igual; o
arquivo original só passa pelo app antes de ir ao Spaces.

O `Dockerfile`/`docker-compose.yml` continuam no repositório como alternativa
para rodar o app no próprio droplet.

## Próximas fases

1. Conferir a cobertura de lojas da API e os casos "a revisar" com a rede
   inteira online.
2. Reconciliação: aceite automático sob confirmação humana e reprocessamento
   incremental da fila (itens 12–14 do plano em `status_e_pendencias.md`).
3. Login individual (os níveis de acesso já existem em `core/acesso.py`).
4. **Dashboard** (KPIs consolidados).
