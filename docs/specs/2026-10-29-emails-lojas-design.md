# E-mails para as lojas — desenho (29/10/2026)

Etapa 7 de `docs/specs/2026-10-29-plano-de-acao.md`. Desenho aprovado pelo Mariano parte a parte em 29/10/2026.

## Objetivo

O lojista chega na farmácia e já tem no e-mail o resumo do dia, sem abrir o sistema; quando um produto importante
acaba de zerar, ele fica sabendo. Sem custo novo.

## Decisões do Mariano

- Dois conteúdos: **aviso de ruptura** (só os produtos de curva A e B que zeraram desde a foto anterior) e
  **relatório diário** (orçamento sugerido, produtos para comprar, ruptura, perda, os 10 mais críticos, links).
- Saem **logo depois da rotina da madrugada**. Na prática, de manhã: a coleta leva até ~5 h por parte e o GitHub
  atrasa o início (aceito pelo Mariano).
- Quem é cadastrado começa **recebendo o aviso de ruptura**; o relatório diário é opcional. Cada um muda na tela
  **"Minhas notificações"**.
- **Um e-mail por pessoa**, com todas as lojas dela (uma seção por loja) — opção A.
- Com os dois ligados, **um e-mail só**: o aviso no topo, o relatório embaixo — opção A.
- **Curva A e B pela 1ª letra** (valor vendido): AA, AB, AC, BA, BB, BC — a mesma regra do "Ruptura por curva" do
  Dashboard — opção A.
- Envio por um **job novo no GitHub**, depois do pré-cálculo — abordagem 1 (as outras: cron no droplet, que exigiria
  instalar Python lá; e o próprio app, que só roda quando alguém o abre).

## 1. O caminho dos dados

**No app.** Publica no Spaces, ao lado das fotos dos insumos do pré-cálculo
(`pedido/insumos/<banco>/destinatarios.json`), a lista de destinatários: cada usuário de loja (Comprador e
Proprietário) **ativo e com senha criada** — e-mail, nome, nível, lojas (principal e adicionais), linhas de compra
(Comprador) e as duas preferências já resolvidas (vazio = padrão). Quem está "Aguardando" fica de fora: o e-mail
pode estar errado e o primeiro contato é o convite. Republica ao gravar uma preferência, ao mudar o Cadastro (novo,
editar, lojas, excluir, reativar) e na conferência de 10 em 10 min que já existe (`insumos.publicar_em_segundo_plano`).
A equipe RMC (ADM, Consultor) não entra.

**No GitHub, job "E-mails"** (`python -m pedido.emails`), depois do job "Pré-cálculo":
1. Lê a lista de destinatários e, para cada loja deles, os resultados prontos de hoje do Assistente e da Ruptura
   (`pedido/resultados/<banco>/…`, pelo selo e pela assinatura que o app usaria — o mesmo `precalculo.ler`).
2. **Quem zerou:** produto do Assistente com a 1ª letra da curva A ou B (`curva_valor`), em ruptura hoje (a regra
   de ruptura da loja: estoque ≤ N) e que **não estava** em ruptura na foto anterior. A lista anterior fica no Spaces,
   gravada pelo próprio job: `pedido/controle/emails/<banco>/ruptura/<empresa>/<loja>.json` (foto e linhas em
   ruptura de curva A/B). Sem lista anterior (primeiro dia da loja), nada é avisado — só se grava a lista.
3. Loja **sem foto nova** desde o último e-mail, ou sem resultado pronto: fica fora do e-mail. Pessoa sem nenhuma
   loja com novidade não recebe.
4. Monta **um e-mail por pessoa** e envia pelo Gmail do sistema (`EMAIL_REMETENTE`/`EMAIL_SENHA_APP`).
5. Registra o que mandou: `pedido/controle/emails/<banco>/enviados/<data>.json` (id do usuário → hora). Pessoa já
   registrada no dia não recebe de novo; só é registrada se o envio deu certo.
6. Relatório `pedido/controle/emails/<banco>/relatorios/<data>.json` (enviados, pulados por motivo, falhas). No log
   público do GitHub, só contagens.

**O que o e-mail usa:** a **sugestão do sistema** (o pedido como o Assistente monta de manhã), não as quantidades
mudadas à mão na área de trabalho (que ficam no banco). O **Comprador** recebe só as linhas de compra dele, cortadas
como no Assistente (`classificacao.filtrar` com a tabela de classificação dos insumos).

## 2. O conteúdo

**Assunto:** com produto zerado, "3 produtos importantes zeraram · Farmácia X" ("… em 2 lojas" com várias); só o
relatório, "Resumo do dia 30/10 · Farmácia X" ("… · 3 lojas").

**Corpo** — HTML simples nas cores do app e a versão em texto puro; uma seção por loja:
1. **Aviso de ruptura** (no topo, só se algo zerou): "Zeraram desde ontem: produtos que mais faturam na loja." Uma
   linha por produto — nome, curva, vendeu em 90 dias, sugestão do pedido. No máximo 20 por loja, maior venda em
   valor primeiro; "+ N outros: veja no Assistente".
2. **Relatório do dia** (se ligado): Orçamento sugerido e Produtos para comprar (os cards do Assistente), Em
   ruptura e Perda (medida ou "se não repor em 30 dias", como no Dashboard); os **10 mais críticos** do Dashboard de
   Ruptura (em ruptura ou ruptura próxima, da maior perda para a menor) com nome, situação e quanto repor; botões
   "Abrir o Assistente de pedido" e "Ver a Ruptura no Dashboard" (`APP_URL`; abrem o login, o "lembrar-me" entra
   direto).
3. **Rodapé:** "Dados do estoque de 30/10 às 03:10" (a foto), "Para mudar o que você recebe: Minhas notificações"
   (link) e o motivo ("Você recebe por estar cadastrado na Farmácia X").

Sem valores por fornecedor e sem dados de outras lojas.

## 3. A tela e o banco

**Banco** (migração `0020`, só acrescenta): `usuarios.email_ruptura` e `usuarios.email_relatorio`, booleanos que
aceitam vazio; vazio = padrão (aviso ligado, relatório desligado). Os usuários que já existem passam a receber o
aviso sem ninguém mexer. O registro dos envios fica no Spaces (a rotina não alcança o banco).

**"Minhas notificações"** — no pé da barra lateral, acima do "Sair", só para usuários de loja. Mostra para qual
e-mail vai (o de login; trocar, só pelo Cadastro) e dois interruptores com uma frase cada:
- Aviso de ruptura: "Quando um produto que mais fatura na loja zera. Só chega nos dias em que isso acontece."
- Relatório diário: "Todo dia de manhã: orçamento, itens a comprar, ruptura e os 10 mais críticos."

Grava na hora ("Preferência salva: vale a partir do e-mail de amanhã") e republica a lista. O link do rodapé do
e-mail (`?pagina=notificacoes`) abre a tela depois do login.

**Cadastro** (ADM e Consultor): o pop-up Editar usuário ganha os mesmos dois interruptores (lojista que pede por
telefone); a lista mostra um ícone discreto de quem recebe o quê.

Fora, de propósito: horário por pessoa e e-mail de teste pela tela.

## 4. Falhas, limites e testes

- Envio que falha não para os outros; vai para o relatório como falha (sem o endereço no log público). A pessoa só
  é marcada no dia se deu certo: um novo disparo no mesmo dia tenta de novo só quem falhou.
- Sem a senha do Gmail ou sem lista de destinatários: não envia nada, grava o relatório com o motivo e termina com
  falha (o GitHub e o alerta avisam).
- **Limite do Gmail** (~500/dia na conta comum, que também manda os e-mails de senha e o alerta): o job para em
  **400 no dia** e registra quantos ficaram de fora.
- O job "Alerta" ganha: relatório do "E-mails" faltando, mais de 10% de falhas, limite atingido.
- **Quando roda:** agendado só com a variável `EMAILS_AGENDADO = 1` (ligada pelo Mariano depois dos primeiros
  e-mails reais); no botão, a opção `so_emails` (manda com os resultados prontos que já existem). Precisa de
  `APP_URL` nos Secrets do GitHub (o endereço publicado do app). Depende do pré-cálculo (`PRECALCULO_AGENDADO = 1`).
- **pytest:** quem zerou (1ª letra, primeiro dia sem aviso, loja sem foto nova), corte do Comprador, um e-mail por
  pessoa com várias lojas, nada duplicado no dia, falha que não para os outros, limite de 400, padrão das
  preferências, a lista de destinatários (Aguardando e equipe RMC fora).
- **Teste visual:** "Minhas notificações" em 1280 e 390 px; os interruptores no Editar usuário.
- **Conferência real:** antes de ligar o agendamento, `so_emails` com a opção do job que manda **tudo para um
  endereço só** (o do Mariano), para ver como chega no celular e no PC.

## 5. Antes e depois da publicação

O job trata cada banco que publicou a lista de destinatários, como o pré-cálculo trata cada banco que publicou os
insumos. **Antes da publicação**, só o app novo (o local, banco dev) publica a lista: os e-mails vão apenas para os
usuários do banco dev — que servem de teste —, e o app da equipe (o `main`, banco de produção) não muda nada. Os
lojistas de verdade começam a receber **depois da publicação**, quando o app publicado passa a ter a tela e a
publicar a lista do banco de produção. `APP_URL` dos links é o do app publicado; antes da publicação, a
conferência usa o envio para um endereço só, e os links levam ao app antigo (sem a tela "Minhas notificações").
