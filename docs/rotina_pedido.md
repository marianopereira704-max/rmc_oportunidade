# Rotina de dados do Pedido — passo a passo

A rotina baixa da API do GPS as **vendas, compras e estoque** de cada loja
ativa do RMC e grava no Spaces, em `pedido/`. Roda sozinha às **00:47**
(horário de Brasília), no GitHub Actions, dividida em 8 partes paralelas. O
GitHub não garante o horário do agendamento: em 29/10/2026 a marcada para
00:00 só começou às 06:03 (o minuto quebrado costuma atrasar menos). Cada parte
para sozinha aos 310 min (`PEDIDO_ORCAMENTO_MINUTOS`), com folga até o corte
do GitHub aos 355; parte cortada vira alerta por e-mail.
A **carga inicial** (3 meses fechados + o mês corrente) é o mesmo comando:
na primeira vez falta tudo, e ela baixa tudo; depois, só o dia anterior e o
estoque.

Por que tudo isso e os números medidos: [mapa_api_gps.md](mapa_api_gps.md).

No fim de cada execução, a rotina junta os arquivos de cada loja num
"pronto" (`pedido/pronto/…`), que a tela Pedido lê em ~0,3 s. Se a rotina
não chegar a montar o de alguma loja, a própria tela monta na primeira
abertura (~3 s a mais).

## 1. Cadastrar as chaves no GitHub (uma vez, ~5 minutos)

As chaves **nunca** vão no código (o repositório é público). No GitHub:

1. Abra o repositório `rmc_oportunidade` → **Settings** → **Secrets and
   variables** → **Actions** → **New repository secret**.
2. Crie um secret para cada nome abaixo, copiando o valor do seu
   `.streamlit/secrets.toml` (o mesmo nome, o mesmo valor):

| Nome do secret | De onde copiar |
|---|---|
| `GPS_API_BASE_URL` | `GPS_API_BASE_URL` |
| `GPS_API_KEY` | `GPS_API_KEY` |
| `DO_SPACES_ENDPOINT` | `DO_SPACES_ENDPOINT` |
| `DO_SPACES_REGION` | `DO_SPACES_REGION` |
| `DO_SPACES_BUCKET` | `DO_SPACES_BUCKET` |
| `DO_SPACES_ACCESS_KEY` | `DO_SPACES_ACCESS_KEY` |
| `DO_SPACES_SECRET_KEY` | `DO_SPACES_SECRET_KEY` |
| `SISTEMA_INTERNO_BASE_URL` | `SISTEMA_INTERNO_BASE_URL` |
| `SISTEMA_INTERNO_TOKEN` | `SISTEMA_INTERNO_TOKEN` |
| `EMAIL_REMETENTE` | `EMAIL_REMETENTE` (alerta por e-mail, 28/10/2026) |
| `EMAIL_SENHA_APP` | `EMAIL_SENHA_APP` |
| `ALERTA_DESTINATARIOS` | não está no secrets.toml: os e-mails que recebem o alerta, separados por vírgula |
| `PRECALCULO_CHAVE` | `PRECALCULO_CHAVE` (cálculo de madrugada, 29/10/2026): uma frase longa qualquer, a MESMA no GitHub, no secrets.toml local e — na publicação — nos Secrets do Streamlit Cloud |

E uma **variável** (Settings → Secrets and variables → Actions → aba
**Variables** → New repository variable; não é segredo e não tem custo):
`PRECALCULO_AGENDADO` = `1` liga o cálculo de madrugada na execução da
meia-noite. Sem ela, o job só roda pelo botão (ver 4c).

Quando trocar uma chave (a da API do GPS e a do Spaces estão pendentes de
troca), atualize aqui também.

## 2. Publicar a rotina

A rotina só passa a existir no GitHub depois que o arquivo
`.github/workflows/rotina_pedido.yml` estiver no `main`: faça o commit e o
push como de costume.

## 3. Disparar a carga inicial (sem esperar a meia-noite)

GitHub → aba **Actions** → **Rotina Pedido (API do GPS)** → **Run
workflow** → **Run workflow** (com "O que rodar" em `completa`, o padrão;
`so_precalculo` é o do item 4c).

Cada execução dura no máximo ~5,5 h por parte (limite do GitHub: 6 h). Se a
carga não terminar numa execução, dispare de novo (ou espere a da
meia-noite): ela continua de onde parou — o que já está salvo é pulado.
Duas execuções nunca rodam ao mesmo tempo (a segunda espera).

## 4. Acompanhar

- **No GitHub** (Actions → a execução → cada "Parte N de 4"): o andamento
  por empresa e, no fim, uma linha `FIM — …` com os totais. Não aparece
  nome de loja nem valor (o log é público).
- **No Spaces** (`pedido/controle/execucoes/<data>/`): um relatório por
  parte, com as falhas e o que ficou pendente para a próxima execução.
- **No app**: Dados → **Vínculo de lojas GPS** → "Atualizar vínculos" lê as
  lojas que a rotina encontrou.

É normal aparecer falha em algumas consultas: a API do GPS corta o que passa
de 10 minutos e às vezes sai do ar por alguns minutos. A consulta volta
para uma fila e é tentada de novo no fim da mesma execução; se ainda
falhar, fica para a próxima. As compras da **Drogarias Reis** dão erro 500
em qualquer consulta (defeito do lado do GPS) e vão aparecer como
pendentes até o suporte do GPS corrigir.

## 4a. Compras da rede para a Análise de Oportunidade (05/10/2026)

Depois das 4 partes, o job **Compras da rede (Análise)** junta as compras de
todas as lojas num arquivo por mês fechado (`pedido/analise/compras/`). Ele
roda mesmo que alguma parte falhe; o que faltar entra no dia seguinte (o
arquivo do mês é refeito quando muda a lista de lojas ou a data da baixa).

- A rotina guarda **6 meses fechados de compras** (3 para a Análise e 3 para a
  seta de tendência). Na primeira execução depois desta mudança, ela baixa os
  3 meses a mais de cada empresa: a execução demora mais uma vez só.
- O último mês fechado é baixado de novo todo dia até o **dia 6** do mês
  seguinte (notas lançadas com atraso); só então ele aparece na Análise.
- As compras dos **últimos 5 dias** são baixadas de novo toda noite.

## 4b. Alerta por e-mail (28/10/2026)

Por último, o job **Alerta por e-mail** (`python -m pedido.alerta`) lê os
relatórios do dia e manda e-mail para `ALERTA_DESTINATARIOS` **só quando há
problema**: parte sem relatório, parte que caiu com erro, nenhuma loja com
foto de estoque, ou queda de mais de 5% nas lojas com foto contra a noite
anterior (e "nenhum relatório desde …" quando a rotina parou de rodar). O
resumo de cada dia fica em `pedido/controle/alerta/<data>.json`. No log do
GitHub só aparece "nada a avisar" ou "alerta enviado". Se o e-mail não sair,
o job falha e o aviso de falha do próprio GitHub chega.

Desde 28/10/2026 a fase de carga não segura mais a noite: a parte que
termina a sua fatia da carga com pelo menos 10 min de orçamento segue, na
mesma execução, para a manutenção (estoque, histórico, mês corrente). Motivo:
a API passou a listar 150 "empresas" sem cadastro (lojas "derivada de
tb_estoque", sem nome nem CNPJ, nenhuma da rede) e, de 25 a 28/10, nenhuma
loja ganhou foto de estoque.

## 4c. Cálculo de madrugada (29/10/2026)

Job **Pré-cálculo i de 8** (`python -m pedido.precalculo`), depois da coleta e
ao mesmo tempo que as compras da rede: monta, para cada loja, o resultado do
Assistente de pedido e das 3 páginas do Dashboard e grava no Spaces
(`pedido/resultados/<banco>/…`, selado com a `PRECALCULO_CHAVE`). O app lê o
pronto em vez de calcular; se algo mudou depois da madrugada (personalização,
correção de estoque negativo, base nova), aquela loja calcula na hora, como
antes. Desenho completo: `docs/specs/2026-10-29-calculo-madrugada-design.md`.

- A rotina **não alcança o banco**: usa as fotos dos insumos que o app publica
  em `pedido/insumos/<banco>/manifesto.json` (ao gravar Configurações ou uma
  correção, e no máximo a cada 10 min). Sem nenhuma foto publicada, o job não
  tem o que fazer: abra o app uma vez.
- **Rodar à mão:** Run workflow com "O que rodar" = `so_precalculo` — pula a
  coleta e as compras da rede e calcula com as fotos de estoque que já existem.
- **Agendado:** só com a variável `PRECALCULO_AGENDADO = 1` (item 1).
- **Acompanhar:** Dados → Rotinas, linha "Cálculo de madrugada: N de M lojas
  prontas · última execução …"; relatórios em
  `pedido/controle/precalculo/<data>/<banco>/<parte>.json` (com o erro de cada
  loja que falhou). O alerta por e-mail avisa parte sem relatório (inclusive
  por falta da chave) e mais de 10% das lojas com falha. No `so_precalculo` o
  alerta confere só o cálculo (conferir a coleta de novo reenviaria o e-mail da
  noite).
- Na própria máquina: `python -m pedido.precalculo --lojas 60,64` (com a chave
  no secrets.toml; grava no Spaces real, na pasta do banco dev).

## 5. Base de categorias (uma vez)

A categoria de cada produto (medicamento 7 dias, perfumaria 15, Sem
Classificação sem sugestão) vem da FEBRAFAR + CMED. As duas listas são
unidas num arquivo que fica no Spaces (a da FEBRAFAR é de terceiro: nunca
vai para o repositório):

```bash
# prévia (só contagens, não grava nada)
python -m pedido.carga_categorias --febrafar CATEGORIAS_FEBRAFAR.xlsx --cmed <lista CMED>.xlsx
# grava pedido/categorias/base_inicial.parquet no Spaces
python -m pedido.carga_categorias --febrafar CATEGORIAS_FEBRAFAR.xlsx --cmed <lista CMED>.xlsx --executar
```

Depois, no app **publicado**: Dados → **Categorias de produtos** →
**Carregar base inicial** (~5 s). Produtos que nenhuma das listas cobre
aparecem no relatório **Produtos Sem Classificação** da mesma tela: baixe,
preencha a coluna CATEGORIA e envie de volta. O relatório lê os produtos
que a rotina grava em `pedido/catalogo/` junto com o estoque.

## 6. Se a rotina parar de rodar sozinha

Em repositório público, o GitHub **desliga o agendamento depois de 60
dias sem nenhum commit**. Se isso acontecer, aparece um aviso na aba
Actions: é só clicar em **Enable workflow**.

## 7. Rodar na própria máquina (teste)

```bash
# grava numa pasta local em vez do Spaces
python -m pedido --destino local:C:/tmp/pedido --empresas <idEmpresa> --orcamento-minutos 30
```

Sem `--destino`, grava no Spaces configurado no `secrets.toml` (o bucket
real).
