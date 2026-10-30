# E-mails para as lojas — plano de implementação (29/10/2026)

Implementa `docs/specs/2026-10-29-emails-lojas-design.md` (aprovado pelo Mariano em 29/10/2026). **C** = o Claude faz;
**M** = o Mariano faz. Cada tarefa termina com o pytest inteiro passando; as marcadas com 👁 também com o teste
visual (só quando muda o que aparece na tela — `python -m tests.visual.precisa`). Tudo no ramo `desenvolvimento`; o
`main` não é tocado.

## Ordem

| # | Tarefa | Quem | Entrega |
|---|---|---|---|
| 1 | Preferências no banco | C | Migração 0020, modelo, padrão resolvido |
| 2 | Lista de destinatários | C | `integrations/destinatarios.py` + pontos de publicação |
| 3 | Tela "Minhas notificações" e Cadastro 👁 | C | Tela, link do e-mail, interruptores no Editar |
| 4 | Quem zerou e o conteúdo do e-mail | C | Funções puras + e-mail de exemplo da loja fictícia |
| 5 | Rotina de envio | C | `pedido/emails.py`: envio, registro, limite, relatório |
| 6 | Workflow e alerta | C | Job "E-mails", `so_emails`, `EMAILS_AGENDADO`, alerta |
| 7 | Conferência real e agendamento | M + C | E-mail real num endereço só; ligar |

**Pontos de parada para o M conferir:** depois da 3 (as fotos da tela no PC e no celular), depois da 4 (o e-mail de
exemplo aberto no navegador, antes de qualquer envio) e na 7 (o primeiro e-mail real, antes de ligar o agendamento).

## 1. Preferências no banco

- Migração `0020_preferencias_email`: `usuarios.email_ruptura` e `usuarios.email_relatorio` (Boolean, aceitam
  vazio). Só acrescenta (regra do projeto). Modelo em `core/models.py`.
- `integrations/usuarios.py`: `preferencias(usuario) -> (ruptura, relatorio)` com o padrão (vazio → aviso ligado,
  relatório desligado) e `gravar_preferencias(session, usuario_id, ruptura, relatorio, por)`.
- **Teste:** padrão com vazio; gravar e ler; o teste de migrações (modelo × tabela) continua passando.

## 2. Lista de destinatários

- `integrations/destinatarios.py`: `montar(session)` — usuários de loja ativos com senha criada (Comprador e
  Proprietário), e-mail, nome, nível, lojas (principal + adicionais, só as da rede, com empresa e loja do GPS e a
  razão social), linhas de compra, preferências resolvidas; `publicar(session, armaz)` grava
  `pedido/insumos/<banco>/destinatarios.json` só quando muda (como `integrations/insumos.publicar`).
- Publicar ao gravar preferências, nas ações do Cadastro (criar, editar, lojas, excluir, reativar) e junto com a
  conferência de 10 em 10 min (`insumos.publicar_em_segundo_plano`).
- **Teste:** Aguardando, excluído e equipe RMC ficam fora; Comprador leva as linhas; loja fora da rede não entra;
  publicar duas vezes sem mudança não regrava.

## 3. Tela "Minhas notificações" e Cadastro 👁

- `views/notificacoes.py`: e-mail de envio, dois interruptores com a frase de cada um, grava na hora com a
  notificação "Preferência salva: vale a partir do e-mail de amanhã".
- Barra lateral: "Minhas notificações" no pé, acima do "Sair", só para usuários de loja (`core/acesso.py`).
- `?pagina=notificacoes` abre a tela depois do login (`app.py`).
- Cadastro: os dois interruptores no pop-up Editar usuário; ícone discreto na lista.
- **Teste:** acesso (só usuário de loja vê a tela); a página do link; o Editar grava as preferências. Teste visual:
  a tela em 1280 e 390 px e o Editar com os interruptores (fotos novas de referência, aprovadas pelo M).

## 4. Quem zerou e o conteúdo do e-mail

- `pedido/emails_conteudo.py` (puro, sem banco nem Streamlit):
  - `zeraram(calc_hoje, anterior, ruptura_unidades) -> DataFrame` — 1ª letra da curva A ou B, em ruptura hoje e
    não na lista anterior; sem lista anterior, vazio;
  - `secao_loja(...)` — o aviso (até 20, maior venda em valor primeiro, "+ N outros") e o relatório (4 números,
    os 10 mais críticos do resultado da Ruptura);
  - `email(pessoa, secoes, app_url) -> (assunto, texto, html)` — assunto conforme o caso, uma seção por loja,
    rodapé com a foto, o link de "Minhas notificações" e o motivo.
- Script de exemplo: monta o e-mail da loja fictícia (`tests/visual/loja_ficticia.py`) e grava o HTML numa pasta
  local para o M abrir no navegador — **parada**.
- **Teste:** 1ª letra (AC entra, CA não), primeiro dia sem aviso, limite de 20 com "+ N", corte do Comprador pelas
  linhas, assunto para cada caso, nada de outra loja no e-mail de uma pessoa, HTML sem dado do banco cru (escapado).

## 5. Rotina de envio — `pedido/emails.py`

- `python -m pedido.emails [--so-para <e-mail>] [--destino local:<pasta>]`. Para cada banco com lista de
  destinatários: lê os insumos e os resultados prontos de cada loja (`precalculo.ler`, com a assinatura que o app
  usaria), monta a seção de cada loja com novidade, junta por pessoa, envia (Gmail), registra o envio do dia, grava a
  lista de ruptura de cada loja para o dia seguinte e o relatório. `--so-para`: manda TUDO para um endereço só (a
  conferência), sem registrar como enviado para as pessoas nem gravar a lista de ruptura.
- Limite de 400 no dia (contando os já registrados); falha de um não para os outros; sem senha do Gmail ou sem lista
  → nada enviado, relatório com o motivo, saída com erro. Log público só com contagens.
- **Teste:** com `ArmazenamentoLocal` e o envio trocado por uma lista: um e-mail por pessoa com várias lojas; nada
  duplicado no dia; segunda execução manda só quem falhou; loja sem foto nova fica fora; limite; `--so-para` não
  registra nem grava lista.

## 6. Workflow e alerta

- Job "E-mails" depois do "Pré-cálculo" (`needs: precalculo`); agendado só com `EMAILS_AGENDADO = 1`; opção
  `so_emails` no Run workflow (pula coleta, rede e pré-cálculo) e o campo "Mandar só para" (opcional) que vira o
  `--so-para`. Secrets: os do Gmail, os do Spaces, `PRECALCULO_CHAVE` e `APP_URL`.
- `pedido/alerta.py`: relatório do "E-mails" faltando (quando o job rodou), mais de 10% de falhas, limite atingido.
- `docs/rotina_pedido.md`, `CLAUDE.md`.
- **Teste:** alerta nos casos novos; o YAML lê e os `if` dos jobs são os esperados.

## 7. Conferência real e agendamento (M + C)

1. (M) Secret `APP_URL` no GitHub (o endereço publicado do app); enviar ao `desenvolvimento`.
2. (M) Run workflow com `so_emails` e "Mandar só para" = o seu e-mail.
3. (M + C) Conferir o e-mail no celular e no PC; ajustes, se houver.
4. (M) Ligar `EMAILS_AGENDADO = 1`. Lembrete: antes da publicação, só os usuários do banco dev recebem.

## Como executar

Escolha do M:
- **A (recomendado): em sequência nesta conversa**, com as 3 paradas para conferir (depois da 3, da 4 e na 7).
- B: tarefa por tarefa, cada uma só depois da sua confirmação.
