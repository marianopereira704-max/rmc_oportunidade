# Rotina de dados do Pedido — passo a passo

A rotina baixa da API do GPS as **vendas, compras e estoque** de cada loja
ativa do RMC e grava no Spaces, em `pedido/`. Roda sozinha à **meia-noite**
(horário de Brasília), no GitHub Actions, dividida em 4 partes paralelas.
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

Quando trocar uma chave (a da API do GPS e a do Spaces estão pendentes de
troca), atualize aqui também.

## 2. Publicar a rotina

A rotina só passa a existir no GitHub depois que o arquivo
`.github/workflows/rotina_pedido.yml` estiver no `main`: faça o commit e o
push como de costume.

## 3. Disparar a carga inicial (sem esperar a meia-noite)

GitHub → aba **Actions** → **Rotina Pedido (API do GPS)** → **Run
workflow** → **Run workflow**.

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
