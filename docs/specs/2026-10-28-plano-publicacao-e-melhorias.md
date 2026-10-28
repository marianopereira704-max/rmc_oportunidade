# Plano: publicação, produção separada, escala e melhorias (28/10/2026)

Passo a passo combinado com o Mariano depois da avaliação do sistema de 28/10/2026.
**M** = o Mariano faz (git, painéis da DigitalOcean/GitHub/Streamlit, secrets). **C** = o Claude faz (código,
testes, conferências). Nenhuma etapa faz commit, branch ou push pelo Claude.

Ordem das fases e por quê:

| Fase | O quê | Depende de |
|---|---|---|
| 1 | Ramo `desenvolvimento` no GitHub | — |
| 2 | Conferir: a rotina nova convive com o app antigo? | 1 |
| 3 | Rotina nova rodando toda noite pelo GitHub (sai do Windows) | 2 |
| 4 | Alerta por e-mail (falha e cobertura) | 3 |
| 5 | "A revisar" com a ação a tomar | — (pode correr junto com 1–4) |
| 6 | Organizar o código | 5 (mexe nos mesmos arquivos) |
| 7 | Preparar a escala | 6 |
| 8 | Publicação: produção separada, dados, merge no `main` | 1–7 |
| 9 | Depois de publicar (limpeza, tabelas mortas, credenciais) | 8 |

Decisões já tomadas: **sem tela inicial de resumo** (os cards já respondem; seria mais uma tela);
**os avisos ao abrir a loja ficam como estão** (estoque negativo e sem classificação mudam o pedido — não
podem virar aviso de primeira vez); **alerta por e-mail, não WhatsApp** (WhatsApp exige pagar o envio —
fica como ideia); **credenciais são o último passo**.

---

## Fase 1 — Ramo `desenvolvimento` (M)

O Streamlit Cloud publica só o ramo configurado no app (`main`). Outro ramo fica no GitHub sem chegar
no app antigo.

1. Conferir no Streamlit Cloud → app → Settings que o campo **Branch** é `main`.
2. `git switch -c desenvolvimento`, versionar o trabalho local e `git push -u origin desenvolvimento`.
3. Daqui pra frente: trabalho novo vai para `desenvolvimento`; o `main` só recebe o merge da Fase 8.
4. **Não** trocar ainda o ramo padrão do GitHub (é a Fase 3, depois da conferência).

Atenção: o repositório é público. Antes do push, conferir que nada de dado de terceiro entrou
(planilha FEBRAFAR, catálogo IQVIA, `docs/mapa_api_gps.md`, `.streamlit/secrets.toml`) — o `.gitignore`
já cobre, mas vale um `git status` olhando os arquivos novos.

## Fase 2 — A rotina nova convive com o app antigo? (C)

O GitHub só roda agendamentos (`schedule`) do ramo **padrão**. Para a rotina nova rodar toda noite sem
publicar o app, o ramo padrão vira `desenvolvimento` — e aí o app antigo (05/10, `main`) passa a ler
arquivos que a rotina NOVA gravou no Spaces. Conferir antes:

1. Abrir o `main` numa pasta à parte (`git worktree`, no scratchpad, sem tocar no ramo de trabalho).
2. Gerar com a rotina nova, em disco local, os arquivos de uma loja fictícia (brutos, pronto, rede,
   histórico de estoque, controle de carga) e rodar por cima deles as leituras do app antigo
   (`pedido/pronto.py`, `pedido/calculo.py`, `views/pedido.py` via `data.seed`).
3. Pontos já vistos que precisam de resposta:
   - compras ganharam a coluna `nome` (acréscimo — provável que o antigo ignore);
   - código da loja sem espaços (muda o caminho de uma empresa; o antigo procura o caminho com espaços);
   - `pedido/controle/configuracao.json`: o app antigo grava esse arquivo quando alguém salva as
     Configurações de Pedidos, e a rotina nova lê. Conferir que a rotina nova aceita o formato antigo
     (`dias_por_categoria` por chave e campos novos ausentes → padrão).
4. Resultado: um parágrafo dizendo "pode trocar" ou o que precisa de ajuste (e o ajuste, se for no
   ramo novo).

**Resultado (28/10/2026): pode trocar, sem ajuste.** Caminhos do Spaces só ganharam arquivos novos;
`configuracao.json` tem só `meses_fechados` nos dois; relatório e lista de lojas do GPS só ganharam
campos (o antigo lê com `.get`); marcador de estoque e catálogo idênticos; Secrets do workflow iguais.
Teste prático (scratchpad, disco local): rotina nova monta o pronto → o app antigo REAPROVEITA (mesma
assinatura) e calcula a mesma sugestão; o app antigo monta → a rotina nova lê, monta o modelo de estoque e
grava o histórico. Spaces real (só leitura): carga de 2026-08/09 concluída nas 388 empresas (349 completas,
39 por tentativas) → a rotina nova entra direto na manutenção, com estoque e histórico. Achados: nenhum
relatório desde 25/10, foto de estoque mais recente de 23/10 (215 empresas com foto); a tarefa do Windows de
28/10 não existe no Agendador e não deixou log — o histórico do estoque ainda não começou; desde 09/10 não há
relatório da rotina antiga do GitHub (conferir na aba Actions). A empresa com código de loja com espaços pode
aparecer como pendência nova no vínculo do app antigo (resolver em Dados → Vínculo).

## Fase 3 — Rotina nova toda noite pelo GitHub (M, com C)

1. (M) GitHub → Settings → General → Default branch → `desenvolvimento`.
2. (M) Conferir que os Secrets do GitHub têm tudo que a rotina nova usa (as 8 partes, o job `rede`, a
   foto do estoque).
3. (C) Juntar a coleta do estoque (`--so-estoque`) ao workflow, se ainda não estiver no mesmo agendamento.
4. (M) Disparar manualmente uma vez (`workflow_dispatch`) e acompanhar o log.
5. (M) Na noite seguinte, conferir Dados → Rotinas e o histórico do estoque no Spaces.
6. (M) Desligar a tarefa agendada do Windows (`rmc_rotina/coleta_estoque.ps1`).
7. (M) Já ligar o aviso nativo do GitHub: github.com → Settings (da conta) → Notifications → Actions →
   "Only notify for failed workflows". Sem código: e-mail quando a rotina inteira falha.

**Fase 3 feita em 28/10/2026** (ramo padrão = `desenvolvimento`). A 1ª execução manual ficou presa na carga: a
API passou a listar 538 ids, mas 150 são "empresas" sem cadastro (lojas "derivada de tb_estoque", sem nome nem
CNPJ, nenhuma da rede) — as 388 reais seguem iguais. A carga delas levou 83 s e a execução acabou ali, sem estoque.
Corrigido: a parte que termina a sua fatia da carga com ≥ 10 min de orçamento segue para a manutenção na mesma
execução (`pedido/__main__.py::MINIMO_PARA_MANUTENCAO_SEG`, teste `test_parte_que_termina_a_carga_segue_...`).

## Fase 4 — Alerta por e-mail (C) — feita em 28/10/2026

`pedido/alerta.py` + job `alerta` no workflow + `tests/test_alerta.py`. Decisões tomadas com os padrões propostos:
queda de 5%, e-mail só quando há problema. Usa o dia mais recente com relatório (disparo que vira o dia) e avisa
"nenhum relatório desde …". **Falta (M): criar os Secrets `EMAIL_REMETENTE`, `EMAIL_SENHA_APP` e
`ALERTA_DESTINATARIOS` no GitHub** (sem eles o job falha e o GitHub avisa).

O aviso do GitHub (Fase 3, passo 7) cobre a rotina que QUEBRA. Este cobre a rotina que termina "verde"
mas trouxe menos do que devia.

1. Job novo `alerta` no workflow, depois das partes e do `rede`, com `if: always()` (roda mesmo se uma
   parte falhar).
2. Lê os relatórios das 8 partes no Spaces e calcula:
   - partes com `erro_geral` ou que não gravaram relatório;
   - lojas vinculadas × lojas com foto de estoque de hoje (cobertura);
   - `empresas_sem_lista_de_lojas`;
   - na fase de carga: empresas concluídas na noite.
3. Compara com a noite anterior (`pedido/controle/alerta/ultimo.json`) e manda e-mail quando:
   parte falhou; cobertura caiu mais que X% (proposta: 5%) ou ficou abaixo de Y lojas; nenhuma foto nova.
4. Envio pelo Gmail do sistema (`EMAIL_REMETENTE`, `EMAIL_SENHA_APP`) + `ALERTA_DESTINATARIOS`, tudo em
   Secrets do GitHub. O e-mail traz os números; o log público só "alerta enviado / nada a avisar".
5. Testes com relatórios fictícios (falha, queda, tudo normal) e envio de mentira.
6. A decidir: mandar também um resumo diário "tudo certo" (proposta: não — só quando há problema).

## Fase 5 — "A revisar" com a ação a tomar (C) — feita em 28/10/2026

Achado ao implementar: o Assistente e a Análise já terminavam com "Confira as notas no sistema de vendas", igual
para todos os motivos; o Dashboard só dava o porquê. Feito: `core/revisar.py` (uma ação por motivo, parágrafo
"O que fazer: …") usado nas três telas; o porquê continua em cada tela; `tests/test_revisar.py`.

Hoje o porquê está em três lugares: `core/dashboard_estoque._MOTIVO_CUSTO` (Dashboard),
`views/pedido.py` (~l. 422, Assistente) e `views/analise_comum.py` (~l. 523, Análise).

1. Um módulo só (`core/revisar.py`): motivo → (por que, o que fazer). As três telas passam a usar ele.
2. A dica ganha o parágrafo "O que fazer", dentro da regra de `core/dicas.py` (até 4 parágrafos de 220).
3. Textos propostos (aprovar antes de implementar):

   | Motivo | O que fazer |
   |---|---|
   | Compra acima do preço de venda | Confira no sistema de vendas o custo e a fração da embalagem. Uma caixa com 10 lançada como 1 unidade causa isso. |
   | Cadastro sem compra e fora da faixa | Corrija o custo de cadastro no sistema de vendas. O pedido usa esse custo enquanto não houver compra. |
   | Compra muito diferente das outras | Confira a última nota: quantidade, fração ou preço podem ter sido lançados errado. |
   | Sem custo nenhum | Cadastre o custo do produto no sistema de vendas. |
   | Análise: cadastro × nota divergem | Confira a nota com o fornecedor. A economia desta compra fica fora da conta até a correção. |

4. `tests/test_dicas.py` passa; teste de cada motivo → texto; teste visual com `--atualizar` só nas
   capturas de dica (o resto tem que ficar igual).

## Fase 6 — Organizar o código (C)

Só mudar de lugar — nenhum comportamento muda. Um arquivo por vez; ao fim de cada um, pytest inteiro e
teste visual com as MESMAS imagens (sem `--atualizar`).

1. `views/dados.py` (1.496 linhas) → pasta `views/dados/`: `__init__.py` (entrada, seletor das seções,
   mesmo `render` de hoje), `pendencias.py`, `importar.py`, `rotinas.py`, `explorador.py`, `comum.py`
   (`_explicacao` e afins).
2. `views/pedido.py` (1.130 linhas) → `views/pedido/`: estado da loja (`_EstadoLoja`), avisos ao abrir,
   filtros, tabela, rodapé e pop-ups (Listas, Exportar, Estoque negativo).
3. `reconciliation/motor.py` (947): avaliar depois dos dois — separar fuzzy-match, filas e
   reprocessamento só se a divisão ficar natural.
4. `core/theme.py` (1.500) fica como está: já é organizado em camadas e o contrato depende dele.
5. Atualizar o mapa do código no `CLAUDE.md`.

## Fase 7 — Preparar a escala (C, decisões com M)

1. **Medir antes de mudar.** Script (Playwright, como o teste visual) que abre 10 e depois 20 sessões ao
   mesmo tempo no app local apontado para dados de tamanho real (cópia do Spaces/banco dev, só leitura) e
   registra tempo de cada tela e memória do processo. Rodar de novo no fim da fase.
2. **Limitar os caches** por loja e por laboratório (`max_entries`/`ttl` em `carregar_ruptura`, cálculo da
   loja, resultado da Análise): o app guarda as lojas usadas recentemente, não todas.
3. **Ruptura calculada de madrugada**: a rotina monta, como já faz com o "pronto" do Pedido, e o app só
   lê. Tira os ~30 s da 1ª abertura da loja.
4. **Análise lendo só parte dos arquivos** (pyarrow + S3FileSystem: rodapé do Parquet e só os blocos dos
   EANs do laboratório). Medir antes/depois com os arquivos reais do job `rede`.
5. **Decidir a hospedagem do app** com os números do passo 1:
   - se aguentar 20 sessões com folga → fica no Streamlit Community Cloud;
   - se não → app num droplet de 2–4 GB na DigitalOcean (~US$ 24–48/mês), na mesma região do banco
     (latência some, firewall deixa de depender dos IPs do Streamlit). A decisão entra na Fase 8.

## Fase 8 — Publicação com produção separada (M, com C)

Hoje os dados reais (Gruppy, Base Genéricos, classificação, vínculos) estão no banco **dev**; o banco
`rmc_oportunidades` é o do app antigo.

1. (C) Inventário do banco do app antigo: o que existe lá que não existe no dev (ex.: algo enviado pelo
   app publicado depois de 24/09) — para não perder nada.
2. (M) Criar o Postgres de produção: DigitalOcean Managed PostgreSQL (~US$ 15/mês, backup diário), mesma
   região do app. Ele já nasce com usuário e senha novos.
3. (M, roteiro do C) `pg_dump` do dev → `pg_restore` no novo; migrações rodam sozinhas no primeiro start.
4. (M) Firewall do banco novo: IPs do Streamlit Cloud (ou do droplet, se a Fase 7 decidir mudar) e do
   GitHub Actions, se a rotina usar banco.
5. (C) Spaces: o app LOCAL continua lendo o que a rotina grava (`pedido/…`), mas o que ele grava
   (uploads, Explorador, `pedido/controle/configuracao.json`, base de categorias) vai para um prefixo
   `dev/`. Hoje salvar as Configurações no app local muda a configuração que a rotina de produção usa.
6. (M) Secrets do Streamlit Cloud → banco novo. O banco dev antigo fica só para desenvolvimento.
7. (M) Merge `desenvolvimento` → `main`; o Streamlit publica. Ramo padrão do GitHub de volta para `main`
   (ou manter `desenvolvimento` como padrão de trabalho — decidir).
8. (M) No app publicado: Dados → Importar → Categorias → "Carregar fontes"; conferir login, Análise,
   Assistente, Dashboard e Cadastro com um usuário de cada nível.

## Fase 9 — Depois de publicar

1. (M) `python limpeza_compras_planilha.py` (prévia) e depois `--executar`.
2. (C) Com o app antigo fora do ar, migração pode voltar a apagar: migração que remove as tabelas sem uso
   (`rascunhos_pedido`, `pedido_filtro_salvo`, compras da planilha, fila de CNPJ órfão) e os modelos.
3. (M) Conferir a cobertura de lojas da API e reanalisar as notas da MILLENIUM com a rede online.
4. (M) **Último passo: trocar as credenciais** que ainda não mudaram (Spaces, senha do Postgres dev,
   chave da API da T.I., senha de app do Gmail, Secrets do GitHub e do Streamlit).
