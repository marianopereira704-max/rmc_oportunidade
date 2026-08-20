# RMC Oportunidades

Sistema de comparativo de preço de compra: mostra, loja a loja e produto a
produto, quanto cada loja da Rede está deixando de economizar por não comprar
da RMC — e prepara o pedido pronto para ser exportado.

Este é o começo do sistema, focado no módulo combinado como prioridade:
**Análise de Oportunidade** (visão por Loja e por Produto), com login, sidebar
de navegação, gerenciador de arquivos ("Explorador de Arquivos" sobre o
DigitalOcean Spaces) e a importação manual das planilhas de Gruppy e GPS
enquanto essas duas integrações não têm API disponível.

`Pedido` (montar pedido + exportar PDF/Excel) e `Dashboard` estão com telas
de "próxima fase" — o modelo de dados de pedido já está pronto no banco, só
falta a interface.

## Como rodar localmente

```bash
python3 -m venv .venv && source .venv/bin/activate   # opcional, mas recomendado
pip install -r requirements.txt

# popula dados de exemplo (lojas, produtos, tabela de preços, histórico de
# compras) para testar todas as telas sem depender das integrações reais
python -m data.seed

streamlit run app.py
```

Acesse http://localhost:8501 e entre com:

| Papel | CNPJ (login) | Senha |
|---|---|---|
| Admin | `30.208.213/0001-74` | `adm123` |
| Consultor | `30.208.213/0001-74` | `consultor` |

A única diferença entre os dois: o admin tem a aba extra **Dados** (gerenciador
de arquivos + upload de planilhas). O consultor só vê Análise de Oportunidade,
Pedido e Dashboard.

Sem nenhuma credencial configurada, o sistema roda 100% em modo dev: banco
SQLite local (`data/app.db`) e arquivos gravados em `data/local_storage/`.
Nada disso depende de internet nem de credencial nenhuma — dá pra abrir e
testar direto.

## Configurando as credenciais reais

Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e
preencha:

- **`DATABASE_URL`** — string de conexão Postgres de produção (ex:
  `postgresql+psycopg2://usuario:senha@host:5432/rmc_oportunidades`). Sem
  isso, continua em SQLite.
- **`DO_SPACES_*`** — endpoint, região, bucket, access key e secret key do
  DigitalOcean Spaces. Sem isso, os arquivos do Explorador de Arquivos ficam
  salvos localmente em `data/local_storage/` (só para dev).
- **`SISTEMA_INTERNO_BASE_URL`** e **`SISTEMA_INTERNO_TOKEN`** — quando a API
  da base de lojas estiver disponível. O formato de resposta esperado está
  documentado em `integrations/sistema_interno.py` — ajuste esse adapter
  quando a doc real da API chegar (hoje é um formato "melhor palpite", já que
  ainda não tenho a documentação).

Nenhuma outra parte do sistema precisa mudar quando as credenciais reais
entrarem — é só preencher o `secrets.toml` (ou as variáveis de ambiente
equivalentes, mesmo nome, se preferir rodar via Docker/servidor em vez de
Streamlit Cloud).

## Por que essa arquitetura

- **Postgres para os dados estruturados, DigitalOcean Spaces só para
  arquivos.** Com 100k+ linhas de histórico de compras, listar/filtrar/somar
  precisa de um banco de verdade — o Spaces é ótimo para guardar arquivos,
  péssimo para consultas. Todas as telas paginam a consulta dentro do banco
  (`core/queries.py`), nunca carregam a tabela inteira em memória.
- **Sem API ainda ≠ sistema travado.** Gruppy (ofertas) e GPS (compras) ainda
  não têm API disponível, então entram por planilha Excel (aba Dados, só
  admin). As 3 integrações seguem a mesma interface (`integrations/base.py`)
  — quando uma API real ficar disponível, troca-se só a implementação do
  adapter correspondente, sem mexer no resto do sistema.
- **Explorador de Arquivos nunca exclui de verdade.** "Inativar" move o
  item para dentro da pasta `_Inativos` e marca o status — "Reativar" devolve
  pro lugar original. Ver `storage/filesystem.py`.
- **Streamlit multipágina "manual"** (sidebar com botões, não
  `st.navigation`/pastas `pages/`) porque a tela de login precisa travar
  100% da navegação antes de qualquer seção renderizar, e o papel do usuário
  (admin/consultor) precisa decidir quais seções aparecem.

## Estrutura do projeto

```
app.py                     # entrada: login -> sidebar -> seção ativa
core/
  config.py                 # todas as credenciais/config (env/secrets/dev-default)
  models.py                  # tabelas (SQLAlchemy)
  db.py                       # engine, sessão, bootstrap (usuários fixos + pastas padrão)
  queries.py                   # consultas paginadas da Análise de Oportunidade
  auth.py                        # login, papéis, cookie "lembrar-me"
  theme.py                        # identidade visual (skill mariano-identidade-visual)
  ui.py                            # paginação e formatação reaproveitadas nas telas
integrations/
  base.py                    # interface comum das 3 integrações
  sistema_interno.py          # base de lojas — única com API real hoje (stub configurável)
  gruppy.py                    # ofertas — manual (planilha) até ter API
  gps.py                         # compras — manual (planilha) até ter API
storage/
  filesystem.py               # Explorador de Arquivos (DigitalOcean Spaces + fallback local)
views/
  login.py, oportunidade_loja.py, oportunidade_produto.py, dados.py, pedido.py, dashboard.py
data/
  seed.py                   # gerador de dados de exemplo (só dev/teste)
```

## Deploy sugerido

Como vocês já têm conta na DigitalOcean (usada no Spaces), o caminho mais
simples é manter tudo no mesmo provedor: um **Droplet** (VPS) rodando este
app via Docker/systemd + um **banco Postgres gerenciado da DigitalOcean**,
com HTTPS na frente (Nginx ou Caddy). Isso evita depender de hospedagens
gratuitas de terceiros para um sistema com dado comercial sensível
(preço de compra por loja).

## O que ainda falta (próximas fases, já combinado)

1. **Pedido**: montar pedido a partir da Análise de Oportunidade + exportar
   em PDF/Excel (popup de escolha de formato). Modelo de dados já pronto
   (`Pedido`/`PedidoItem` em `core/models.py`).
2. **Dashboard**: KPIs consolidados (economia total em aberto, ranking de
   lojas/produtos, evolução mês a mês).
3. Trocar os 3 adapters de `integrations/` pelas implementações reais assim
   que cada API estiver disponível (hoje só a de lojas tem endpoint previsto,
   e mesmo essa depende da doc real da API pra confirmar o formato de
   resposta usado em `sistema_interno.py`).
