# RMC Oportunidades

Sistema de comparativo de preço de compra: mostra, loja a loja e produto a
produto, quanto cada farmácia da Rede deixou de economizar comprando genéricos
de outro fornecedor em vez da RMC.

O cálculo cruza três fontes:

- **GPS** — o que cada loja comprou e quanto pagou (planilha exportada do BI);
- **Gruppy** — as tabelas de preço da RMC, por laboratório e por UF;
- **Base Genéricos** — qual genérico cada EAN é (EANs diferentes do mesmo
  genérico somam juntos).

Economia perdida, por (loja, genérico), contra **um laboratório Gruppy por vez**
(escolhido no filtro principal): `(menor preço pago − preço do laboratório) × quantidade da compra vencedora`,
só quando positiva. Detalhes da regra em `core/analise.py`.
Sempre calculada na hora (nunca gravada). O preço pago usa o `VlrUnitario` e,
quando ele destoa mais de ±50% do laboratório, recua para o custo CMV
(`Fat × %CMV ÷ QTD`) ou o `R$ Custo médio`; bonificações (< R$ 0,10) ficam fora.

Telas prontas: login, **Análise de Oportunidade** (por Loja e por Produto) e
**Dados** (só admin: Explorador de Arquivos, importação de planilhas e filas
de pendência). `Pedido` e `Dashboard` são telas de "próxima fase" — ainda sem
modelo de dados nem interface.

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
- **`GPS_API_BASE_URL`, `GPS_API_KEY`** — API do GPS. O cliente existe
  (`integrations/gps_api.py`), mas a ingestão de compras ainda é pela planilha.
- Opcionais: limiares da reconciliação (`RECON_LIMIAR_AUTO`, padrão 92;
  `RECON_LIMIAR_MEDIA`, padrão 75), sinônimos de coluna (`COLUNAS_GPS`,
  `COLUNAS_GRUPPY`, `COLUNAS_BASE_GENERICOS`) — ver `core/config.py`.

## Como os dados entram (aba Dados → Importar Planilhas)

Ordem recomendada: **lojas → Base Genéricos → Gruppy → GPS → filas**. Nenhum
dado se perde se a ordem mudar; ela só evita trabalho manual nas filas.

1. **Lojas** — automático na primeira abertura da aba Dados de cada dia. Botão
   "Forçar sincronização agora" para rodar na hora.
2. **Base Genéricos** — planilha curada (EAN + nome canônico). A descrição já é
   o nome do genérico; EAN já resolvido nunca é sobrescrito. Depois de subir,
   use **Reprocessar fila contra a base atual** na fila de EAN.
3. **Gruppy** — uma tabela por laboratório: escolha o laboratório, as UFs que a
   tabela cobre e se o custo vem pronto ou como preço bruto + % de desconto.
   Tabela nova do mesmo laboratório substitui só as UFs repetidas.
4. **GPS** — escolha mês e ano, selecione a planilha exportada do BI, confira o
   popup e confirme. O processamento roda em segundo plano (pode fechar a aba).

Regras do GPS:

- Entra como compra a linha com `VlrUnitario > 0` e `Quantidade > 0`. Custo
  unitário = `VlrUnitario` (sem ST, comparável à Gruppy). Linhas só de venda,
  com valor/quantidade ≤ 0 ou sem CNPJ/EAN são ignoradas e contadas.
- Cada envio **substitui, no mês escolhido, as compras dos CNPJs presentes no
  arquivo** — os demais CNPJs e os outros meses não são tocados. Para passar do
  limite de exportação do BI (150 mil linhas), divida por atributo de **loja**
  (UF, cidade, grupo), nunca por produto.
- Tudo ou nada: se falhar em qualquer ponto, nada muda.
- O popup avisa se o rodapé do arquivo indicar outro mês (exige confirmação) e
  se a exportação foi cortada pelo BI.

A planilha é **lida no navegador** de quem envia (componente em
`views/leitor_planilha.py`): o servidor recebe só os dados compactados, e o
.xlsx original vai para o Spaces. Isso vale também para Gruppy e Base Genéricos.

## Análise de Oportunidade

- **Filtro Laboratório obrigatório:** a análise é sempre contra uma tabela
  Gruppy por vez, e a coluna de preço leva o nome do laboratório.
- **Por Produto:** Produto | Laboratório (da compra) | Preço pago | *laboratório* |
  Diferença un. | Qtd. | Preço médio (3m) | Economia.
- **Por Loja:** Loja | Time de atendimento | UF | Cidade | Genéricos | Economia;
  o Detalhes mostra os produtos da loja com as colunas do Por Produto.
- Clique no título de qualquer coluna para ordenar; de novo, inverte.
- Preço "ajustado" = veio do custo CMV ou do Custo médio porque o VlrUnitario
  destoava; "a revisar" = nenhum preço coerente (provável erro de cadastro),
  economia não contabilizada. Regras completas no `CLAUDE.md`.

## Filas de pendência (aba Dados)

- **Fila de CNPJ Órfão** (só GPS): CNPJ de compra que não bate com nenhuma loja.
  As compras ficam guardadas; **Vincular à loja** passa elas para a loja na hora
  e o vínculo vale para os próximos envios.
- **Fila de Resolução de EAN** (uma fila só, para Gruppy e GPS): EAN ainda sem
  genérico. Item de origem GPS vale o dinheiro de compra em jogo; item de
  origem Gruppy entra com valor 0 (é catálogo de preço) e ganha valor se o EAN
  aparecer em compras. Resolver um EAN vale para os dois lados. O fuzzy-match
  resolve sozinho com score ≥ 92, sugere entre 75 e 92, e deixa manual abaixo de 75.

Os valores das duas filas são **recalculados** a partir dos dados vigentes a
cada envio — reenviar o mesmo arquivo não infla a prioridade.

## Por que essa arquitetura

- **Postgres para dados, Spaces só para arquivos.** O banco traz, numa única
  consulta, só as compras do laboratório e do período escolhidos; o cálculo
  da oportunidade fica guardado em memória por (laboratório, período, versão
  dos dados), e ordenar pelo cabeçalho, paginar e abrir detalhes não voltam
  ao banco.
- **Nada de consulta ao banco dentro de laço.** O banco fica em Nova York; cada
  ida e volta custa ~140 ms a partir do Brasil. Toda ingestão grava e consulta
  em lote.
- **Leitura de planilha no navegador, processamento em segundo plano.** O parse
  de um .xlsx de 150 mil linhas no servidor levava 25 s e centenas de MB, e
  derrubava o app por falta de memória.
- **`EanGenerico` é a única junção EAN → genérico.** Resolver um EAN uma vez
  conserta todo o histórico, dos dois lados, sem reprocessar nada.
- **Explorador de Arquivos nunca exclui de verdade.** "Inativar" move para
  `_Inativos`; "Excluir definitivamente" só existe para envios da Gruppy e do GPS.
- **Navegação manual na sidebar** (não `pages/`): o login precisa travar toda a
  navegação e o papel decide quais seções aparecem.

## Estrutura do projeto

```
app.py                      # entrada: migrações -> login -> sidebar -> seção ativa
core/
  config.py                 # toda configuração (secrets -> env -> default)
  models.py                 # tabelas (SQLAlchemy)
  db.py                     # engine/pool, sessão, migrações no start, usuários fixos
  queries.py                # cálculo da Análise de Oportunidade (SQL paginado)
  rotinas.py                # controle de rotinas: diária e trava "um por vez"
  sql.py                    # upsert atômico portátil (Postgres/SQLite)
  auth.py, monitoramento.py, security.py, theme.py, ui.py
integrations/
  gps.py                    # regra de compra, substituição por (mês, CNPJ), órfãos, filas
  gps_processamento.py      # processamento do GPS em segundo plano (trava, tudo ou nada)
  planilha_navegador.py     # planilha lida no navegador + rodapé do BI
  gruppy.py                 # tabelas de preço, vigência por UF
  base_genericos.py         # importação da planilha curada
  sistema_interno.py        # sincronização de lojas
  gps_api.py                # cliente da API do GPS (ainda não usado na ingestão)
  mapeamento.py, base.py, gps_cache_orfaos.py (legado)
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
limpeza_gps_regra_antiga.py # limpeza única dos meses da regra antiga do GPS
```

## Testes

```bash
python -m pytest -q
```

Rodam em SQLite em memória/arquivo temporário e com storage local temporário
(`tests/conftest.py`) — nunca tocam no banco nem no bucket reais. Os testes com
concorrência real contra Postgres só rodam com `RMC_TESTE_POSTGRES_DSN` definida.

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

1. Velocidade das telas de análise (cache e tabela resumo pré-agregada).
2. Reconciliação: aceite automático sob confirmação humana e reprocessamento
   incremental da fila (itens 12–14 do plano em `status_e_pendencias.md`).
3. Ingestão de compras pela API do GPS (depende de casar lojas da API, que não
   trazem CNPJ, com as lojas da RMC).
4. **Pedido** (montar pedido a partir da análise e exportar PDF/Excel) e
   **Dashboard** (KPIs consolidados).
