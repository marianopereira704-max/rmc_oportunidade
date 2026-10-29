# Plano de ação — 29/10/2026

Sucede o `2026-10-28-plano-publicacao-e-melhorias.md` (fases 1–7 feitas; ver lá o histórico). Montado com o
Mariano depois da 2ª avaliação do sistema. **M** = o Mariano faz; **C** = o Claude faz. Tudo vai para o ramo
`desenvolvimento`; nada vai para o `main` até a publicação.

**Fora deste plano, por decisão do M:** publicação (Fase 8 — espera a API do GPS estar 100% confiável), troca das
credenciais (última etapa), envio do pedido direto ao distribuidor (precisa de API com eles — futuro distante),
"valeu a pena" no Dashboard (depois).

## Ordem e por quê

| # | Etapa | Por que nesta posição | Depende de |
|---|---|---|---|
| 1 | Backup automático dos bancos | Hoje um disco perdido apaga Gruppy, vínculos, usuários e configurações, sem volta. O risco mais grave e o mais barato. | — |
| 2 | Conferir os dados reais chegando | Tudo o que vem depois (cálculo de madrugada, e-mails) lê o que a rotina grava. Se a base estiver errada, o resto herda o erro. | — |
| 3 | Limpeza rápida | Coisas pequenas que confundem quem lê o projeto (documentação desatualizada, scripts soltos, aviso vencido, teste instável). Uma tarde, e tudo depois fica mais claro. | — |
| 4 | Assistente mais simples (textos + fim das pílulas) | O ganho que o proprietário sente, rápido. Vem antes do cálculo de madrugada porque tira uma escolha (Personalizado × Padrão) que o cálculo teria que repetir. | — |
| 5 | Cálculo de madrugada (opção C) | O maior ganho para quem usa (a loja abre na hora) e a base dos e-mails: o relatório e a notificação usam o resultado pronto. Roda à mão primeiro, depois toda noite. | 2, 4 |
| 6 | Teste e ajustes para celular | Depois do 4 e do 5 para testar as telas já no formato final. | 4, 5 |
| 7 | E-mails para as lojas (relatório diário e aviso de ruptura) | Usa o resultado do 5. | 5 |
| 8 | Dívidas acumuladas (estruturais) | Não muda nada para o usuário; melhor fazer com as funções novas já no lugar. | 5, 7 |
| 9 | E-mail de evolução no início do mês | Precisa de pelo menos 1 mês de histórico do estoque: o primeiro possível é 01/12/2026. | 2, 5, 7 |

---

## 1. Backup automático dos bancos (C, instalação com M) — código pronto em 29/10/2026; falta a instalação (M, `docs/backup.md`)

O quê: cópia diária dos DOIS bancos — `rmc_oportunidades` (o do app publicado, ainda em uso pela equipe) e
`rmc_oportunidades_dev` (onde estão hoje os dados reais: Gruppy, Base Genéricos, classificação, vínculos) — em
`.sql.gz` no Spaces, numa pasta só nossa (`backups/rmc_oportunidades/AAAA-MM-DD/`).

Retenção: os últimos 14 dias + o do dia 1 de cada mês por 12 meses. A limpeza apaga UMA A UMA só chaves dessa
pasta (o bucket é compartilhado; os backups `.sql.gz` de outros sistemas nunca são tocados).

**Decidido (29/10/2026): no próprio droplet**, por agendamento do Linux (cron). `pg_dump` roda ao lado do banco,
sem abrir o firewall para ninguém. O script fica versionado no repositório (`scripts/backup_banco.sh` + instruções)
e o M instala uma vez no droplet. (O GitHub Actions exigiria liberar no firewall os IPs do GitHub, que são milhares
e mudam: na prática, abrir o banco para a internet.)

Entregável: script com modo prévia, teste de restauração (baixar o backup e restaurar num Postgres local ou
SQLite de teste para provar que volta), passo a passo em `docs/backup.md`, e o alerta da rotina (`pedido/alerta.py`)
passa a avisar quando o backup do dia não existir.

## 2. Conferir os dados reais chegando (C, sem mudar código)

Só leitura, depois de 2–3 noites da rotina nova:
- relatórios das 8 partes, fase manutenção, lojas com foto de estoque por noite (esperado: perto das ~349 lojas
  com vínculo);
- histórico do estoque crescendo (`historico_estoque/`, um dia por noite);
- alerta por e-mail: não disparou à toa, e disparou se algo faltou;
- job `rede`: compras de agosto e setembro montadas (`analise/compras/`); a Análise abre com elas e a soma bate
  com uma conferência manual de 2–3 lojas;
- cobertura de lojas da API contra a planilha de agosto (336 lojas) e as notas da MILLENIUM nas lojas Reis.

Entregável: um relatório curto com o que bateu e o que não bateu; cada problema vira um item antes da etapa 5.

**1ª conferência (29/10/2026, manhã, com a rotina ainda rodando):**
- Rotina: a execução de 29/10 passou pela carga em segundos (1 empresa pendente) e seguiu para a manutenção
  (a correção de 28/10 funcionou); às ~09h UTC, 48 empresas já com foto de 29/10 e 49 lojas com o 1º dia do
  histórico. Relatório final e alerta: quando a execução terminar.
- Compras da rede: 6 meses montados (abr–set). Agosto: **335 lojas com compra pela API contra 336 da planilha do
  BI**. Arquivos de ~11 MB/mês (a estimativa era 23 MB): 3 meses ≈ 31 MB, o gráfico de 6 meses ≈ 60 MB.
- Análise (Ranbaxy, o único laboratório com tabela no banco dev; jul–set): 12 s, 19.743 notas, 239 lojas,
  R$ 82,6 mil de economia, 0,8% "a revisar" (mediana 103, divergência 50, acima da venda 8). Nenhuma nota da
  MILLENIUM entre os produtos da Ranbaxy — a questão das lojas Reis não se repete com este laboratório.
- Conferência independente (lojas 223 e 203, refeitas das compras brutas): preço por unidade = VlrUnitario ×
  (1 − desconto%) ÷ fração confere em todas as notas; nenhuma compra perdida (a loja 223 tem 389 itens em 384
  linhas porque 5 notas trazem o mesmo genérico de dois fabricantes — junta, como previsto).
- **Problema achado:** nessas notas com o mesmo genérico de fabricantes diferentes, a linha mostra o preço MÉDIO,
  mas a economia conta só o item acima da tabela — ex. Fexofenadina, nota 343497: itens a 6,11, 7,84 e 5,89
  contra 6,73; a tela mostra "pago 6,61, diferença −0,12, 18 un., economia R$ 6,66". 24 notas de 19,7 mil (0,1%),
  e 2 notas marcadas "a revisar" (um item a revisar, outro válido) aparecem com economia. Proposta: uma linha por
  nota × fabricante (EAN), para cada linha ter um preço só — decisão do M.

## 3. Limpeza rápida (C)

- `CLAUDE.md`: frases desatualizadas ("Atualizado em 06/10", "o `main` ainda tem o código de 02/09", o parágrafo
  que diz que o app antigo e o local usam o mesmo banco — são bancos diferentes no mesmo servidor), ramo
  `desenvolvimento`, contagem de testes.
- `status_e_pendencias.md`: juntar com os dois planos e tirar o que já foi feito.
- Scripts soltos na raiz (`diagnostico_producao_leitura.py`, `medir_casamento_lojas.py`) e a pasta `Claude outputs/`:
  conferir com o M o que é de uso e o que sai do repositório.
- Aviso "A aba Dados foi reorganizada" vence em 31/10/2026: apagar o aviso e o código dele.
- Teste visual: a captura do pop-up Listas muda a cada execução por causa do relógio — fixar a hora nessa captura.

**Feito em 29/10/2026:** `CLAUDE.md` corrigido (ramos, bancos, contagens, medições novas); `status_e_pendencias.md`
com um quadro único "Em aberto hoje" no topo e o resto marcado como histórico; aviso da aba Dados apagado;
`RMC_AGORA` fixa a hora do pop-up Listas no teste visual.

**Achado: dados de lojas no GitHub público.** A pasta `Claude outputs/` está versionada desde o commit `59eb9a9`
(também no `main`): 3 HTMLs da Análise de Oportunidade com ~420 CNPJs e nomes de drogarias (e, ao que tudo
indica, preços e economia por loja) e 180 capturas de tela das etapas do redesenho. A pasta já entrou no
`.gitignore` (não volta a subir), mas o que está lá continua público. Opções (decisão do M):
- **A — tirar do repositório daqui pra frente:** `git rm -r --cached "Claude outputs"` e commit. Rápido, sem risco
  para o app; mas os arquivos continuam acessíveis no histórico de commits para quem procurar.
- **B — A + apagar do histórico:** reescrever o histórico sem a pasta (`git filter-repo --path "Claude outputs"
  --invert-paths`) e enviar com push forçado nos dois ramos. Remove de verdade do GitHub (forks e cópias que alguém
  já baixou continuam). O push forçado no `main` faz o Streamlit reiniciar o app com o MESMO código; toda cópia
  local do repositório precisa ser baixada de novo depois.
- C — repositório privado: resolveria na hora, mas o GitHub Actions em repositório privado no plano gratuito tem
  2.000 min/mês, e a rotina gasta muito mais que isso por noite (8 partes de até 5,5 h) — teria custo. Não serve.

Recomendação: A agora e B quando houver uma janela tranquila.

**Também a decidir (M):** `diagnostico_producao_leitura.py` (leitura do banco, de 15/09) e `medir_casamento_lojas.py`
(medição do vínculo por nome, de 17/09, com teste em `tests/test_casamento_lojas.py`) estão na raiz. Os dois já
cumpriram o papel. **Apagados em 29/10/2026** (com o `tests/test_casamento_lojas.py`), por decisão do M.

**Decidido (29/10/2026): A e B** — passo a passo no fim desta etapa, feito pelo M.

## 4. Assistente mais simples (C, textos aprovados pelo M) — feita em 29/10/2026

- **Fim das pílulas "Personalizado desta loja" / "Padrão".** Loja com personalização usa a personalização, sempre;
  sem personalização, o padrão. A lista aparece direto, sem escolha. Um texto discreto no topo diz qual vale
  ("Parâmetros desta loja" ou "Parâmetros padrão"), com dica explicando onde mudar.
- **Textos mais simples** nos pop-ups de entrada (Estoque negativo, Sem classificação), nos cards e nas dicas mais
  vistas (Ruptura, Giro baixo, Venda pontual, A revisar, curva ABC). Método: o C levanta todos os textos numa
  tabela "como está → proposta" em linguagem de dono de farmácia; o M aprova; o C aplica.
- Teste visual atualizado (o fluxo das pílulas sai do roteiro).

**Feito:** textos aprovados pelo M aplicados; pílulas fora. Testados pela regra do M: "Conferir preço" passou (mesma
largura de "Ruptura próxima"); "Mínimo / Ideal" e o "?" da dica nova da Curva não passaram em 1280 px (a tabela
deixava de caber e o nome do produto quebrava) — ficaram "Mín./Ideal" (dica por extenso) e a dica na letra da
curva. O teste visual ganhou a regra de EIXOS E ESPAÇO para todas as tabelas e cards (`_verificar_eixos`).

## 5. Cálculo de madrugada — opção C (C, desenho aprovado pelo M antes de codar)

O quê: a rotina, depois da coleta, monta para cada loja o resultado do Assistente e das 3 páginas do Dashboard e
grava no Spaces; o app só lê (a loja abre em ~1 s em vez de 3–8 s por página, e sem o pico de 50–100 MB).

O que o desenho precisa resolver (a rotina não alcança o banco):
- as bases que o cálculo usa — base de categorias e classificação (já têm cópia no Spaces desde 28/10),
  genéricos (falta a cópia), configuração padrão e personalizações por loja, correções de estoque negativo:
  o app publica uma "foto" de cada uma no Spaces quando muda (como já faz com a janela de meses);
- quando o app usa o resultado pronto e quando calcula na hora (versão das bases ou configuração mudou depois
  da madrugada, correção feita durante o dia → calcula na hora, como hoje);
- onde roda: job novo depois das partes (como o `rede`), dividido em partes para caber no tempo.

Execução: (1) desenho e aprovação; (2) implementar com testes; (3) **rodar à mão** pelo botão do GitHub e conferir
loja a loja (resultado pronto idêntico ao calculado na hora, como na cópia das bases); (4) ligar no agendamento.

## 6. Teste e ajustes para celular (C)

- Teste visual em 390 px (celular comum) das telas que a loja usa: login, Assistente, Por Produto, Dashboard
  (Ruptura, Estoque, Compras), Criar senha. Hoje só os pop-ups Novo/Excluir são fotografados em 390 px.
- Medir o que quebra (tabela larga, rodapé fixo, barra lateral, filtros) e corrigir; o que não couber numa
  tabela no celular vira cartão por produto (decisão de desenho com o M, com capturas antes/depois).

## 7. E-mails para as lojas (C, com decisões do M)

Para Comprador e Proprietário, cada um escolhe: **relatório diário**, **aviso de ruptura**, **os dois** ou nenhum
(tela "Minhas notificações", acessível pelo próprio usuário; o padrão de quem é cadastrado decide o M).

- **Relatório diário:** resumo da loja — orçamento sugerido, itens a comprar, produtos em ruptura, perda estimada,
  os 10 mais críticos, link para o Assistente.
- **Aviso de ruptura:** a lista de produtos que ZERARAM desde a foto anterior (não todos os que estão em falta: a
  loja 60 tem ~900 em ruptura, o e-mail viraria ruído).

Limitação importante: o estoque chega UMA vez por dia (foto da madrugada). "Assim que zerar" na prática é "logo
depois que a rotina da madrugada encontra" — não dá para avisar no meio do dia.

**Decidido (29/10/2026):**
- relatório logo depois da rotina da madrugada, para o usuário chegar na farmácia e já comprar;
- o aviso de ruptura leva só os produtos de curva A e B que zeraram;
- quem é cadastrado começa recebendo o aviso de ruptura (o relatório diário é opcional).

Ainda a conferir: o Gmail do sistema tem limite diário (~500 e-mails/dia na conta comum); com centenas de lojas e
dois e-mails por usuário, conferir a conta antes.

## 8. Dívidas acumuladas — estruturais (C)

- `reconciliation/motor.py` (947 linhas): dividir em fuzzy-match, filas e reprocessamento, se a divisão for natural.
- Código morto: procurar funções e módulos sem uso (varredura + conferência à mão) e apagar.
- Nomes que ficaram da Fase 5 (`pedido_rascunho.py` guarda correções e exportações; o rascunho não existe mais).
- Tabelas sem uso (`rascunhos_pedido`, `pedido_filtro_salvo`, compras da planilha, fila de CNPJ órfão): migração
  que apaga, preparada agora e aplicada só na publicação, DEPOIS do `limpeza_compras_planilha.py` (o app antigo
  ainda lê o banco de produção).
- `core/theme.py` (1.500 linhas de CSS): fica — está em camadas e o contrato do teste visual depende dele.

## 9. E-mail de evolução no início do mês (C)

No dia 1, para quem escolheu receber: o mês que fechou contra o anterior — ruptura e perda, economia possível na
Análise, itens "a revisar" resolvidos. Primeiro envio possível: 01/12/2026 (novembro inteiro de histórico). Usa a
mesma tela de preferências da etapa 7.
