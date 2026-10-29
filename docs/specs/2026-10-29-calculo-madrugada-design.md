# Cálculo de madrugada — design (29/10/2026)

Etapa 5 do `docs/specs/2026-10-29-plano-de-acao.md`. Desenho aprovado com o Mariano parte a parte em 29/10/2026
(abordagem 1, opção A).

## Objetivo

Quem abrir qualquer loja, a qualquer hora, vê o Assistente de pedido e as 3 páginas do Dashboard (Estoque,
Ruptura, Compras) em ~1 s. Hoje: 3–8 s por página na 1ª abertura e pico de 50–100 MB de memória por loja
(medição de 28/10/2026). Sem custo novo; resultado IDÊNTICO ao cálculo na hora; o app nunca depende da rotina.

## Decisões

- **Abordagem 1:** a rotina do GitHub calcula com "fotos" dos insumos que o app publica no Spaces. Descartadas:
  a rotina ler o banco (abriria o firewall para a internet) e o app calcular em segundo plano (Streamlit Cloud
  com pouca CPU e que hiberna).
- **Opção A:** mudou algo depois da madrugada (categoria, genérico, Configurações, personalização, correção de
  estoque negativo) → a loja afetada calcula na hora, como hoje, até a madrugada seguinte.
- Todas as lojas com vínculo (~349), não só as com usuário. Refazer fora de hora: botão do GitHub.
- O que é de cada usuário (marcações, quantidades, listas, corte do Comprador) continua aplicado na hora.

## Parte 1 — fotos dos insumos

Pasta por banco: `pedido/insumos/<banco>/` (`<banco>` = nome do banco da `DATABASE_URL`, como em
`core/copia_spaces.py`): o app local (banco dev) e o publicado nunca se misturam.

| Insumo | Como vai |
|---|---|
| Base de categorias, fontes da classificação | Cópia de `core/copia_spaces.py` (já existe) |
| Genéricos | Mesma cópia, tabela nova |
| Configuração padrão, personalização por loja, correções de estoque negativo por loja, vínculo loja RMC ↔ GPS, versões das bases | `manifesto.json` |

Quando publica: nos pontos que hoje limpam o cache (importar em Dados, salvar Configurações, corrigir estoque
negativo) e, como garantia, no 1º acesso do dia (em segundo plano, como a conferência de vínculos de
`views/loja_barra.py`), completando o que faltar. A rotina calcula para CADA banco com fotos publicadas (hoje só o
dev; depois da publicação, o de produção entra sozinho).

## Parte 2 — assinatura

Cada resultado pronto leva a assinatura (hash) de: marcador da foto de estoque da loja (data e `gerado_em`);
versões das bases (categorias, genéricos, catálogo IQVIA, classificação); configuração efetiva da loja (padrão +
personalização); correções de estoque negativo da loja (só Assistente e Ruptura — o Compras não as usa);
`VERSAO` do código do cálculo. O app monta a mesma assinatura com os dados que já tem: bateu → lê o pronto; não
bateu ou não existe → calcula na hora.

Selo: HMAC-SHA256 com `PRECALCULO_CHAVE` (Secret novo no GitHub e no Streamlit). O resultado é guardado em pickle
(para sair idêntico); o app confere o selo ANTES de abrir — selo inválido ou chave ausente → ignora e calcula na
hora.

## Parte 3 — rotina

- Job `precalculo` no `.github/workflows/rotina_pedido.yml`, depois da coleta (precisa do pronto, do modelo de
  estoque e do histórico da noite), em 8 partes, em paralelo com o `rede`; o `alerta` espera os dois.
- `python -m pedido.precalculo --parte i --partes 8` (e `--lojas 60,64` para testar). Cada parte lê as fotos de
  cada banco e as bases uma vez, pega sua fatia das lojas com vínculo e foto de estoque, calcula as 4 telas com
  as MESMAS funções do app e grava em `pedido/resultados/<banco>/<empresa>/<loja>/<tela>.bin` + `<tela>.json`
  (assinatura, gerado_em, segundos). Assinatura igual à gravada → pula a loja.
- Tempo: ~12,5 s por loja grande nas 4 telas → ~70 min no total, ~9 min por parte, por banco.
- Botão "Run workflow" ganha a opção "só o pré-cálculo".
- Relatório por parte em `controle/precalculo/<data>/`; o `pedido/alerta.py` avisa quando falta o relatório de
  uma parte ou quando mais de 10% das lojas falham.
- Um resultado por loja e tela, substituído; loja que sai da rede tem os arquivos apagados um a um, só em
  `pedido/resultados/`. Espaço estimado: 1–1,5 GB.

## Parte 4 — telas e código

Nada muda na tela, só o tempo de abrir. Peças novas:

| Arquivo | Faz | Usado por |
|---|---|---|
| `pedido/telas_loja.py` | As 4 contas como funções puras (sem Streamlit nem banco) | App e rotina |
| `pedido/precalculo.py` | Rotina (ler fotos, calcular, gravar) e leitura do pronto | Rotina e app |
| `pedido/selo.py` | Fazer e conferir o selo | Os dois |
| `integrations/insumos.py` | Publicar as fotos do banco no Spaces | App |

As contas saem de `views/pedido/dados_loja.py` (`_calcular`) e `views/dashboard_comum.py` (`_estoque_preparado`,
`_ruptura_preparada`, `_compras_preparadas`) só mudando de lugar, conferidas como na Fase 6. As funções com cache
das telas ficam onde estão (cache em memória + `@limite.pesado`) e passam a tentar primeiro o pronto. Dados →
Rotinas ganha "Pré-cálculo: N de M lojas prontas · última execução".

## Parte 5 — erros e testes

| Situação | O que acontece |
|---|---|
| Rotina não rodou, falhou ou não chegou na loja | Sem pronto: calcula na hora |
| Mudou algo depois da madrugada | Assinatura não bate: só a loja afetada calcula na hora |
| Selo não confere / chave ausente | Ignora o arquivo, calcula na hora, registra no log |
| Foto dos insumos velha | Assinatura não bate; a garantia do 1º acesso do dia publica para a noite seguinte |
| Uma loja falha na rotina | As outras seguem; falha no relatório; alerta acima de 10% |

Testes (pytest): pronto idêntico ao cálculo na hora (loja fictícia, 4 telas); a assinatura muda quando deve (foto,
versões, configuração, personalização, correção — só Assistente e Ruptura —, `VERSAO`); selo recusa arquivo
alterado ou com outra chave; rotina (divisão em partes, pular loja em dia, falha isolada, relatório, limpeza só na
pasta); publicação das fotos em cada ponto de mudança e na conferência diária.

Conferência real antes de agendar: rodar à mão pelo GitHub; script só de leitura compara pronto × cálculo na hora
em TODAS as lojas, tela a tela (tem que dar 100% idêntico); medir a abertura de 3 lojas (grande, média,
pequena). Teste visual: zero diferença, com uma volta extra pelo caminho do pronto.

## Fora do escopo

Botão no app para refazer o pré-cálculo; Análise de Oportunidade (já abre em ~12 s para 3 meses e tem cache por
laboratório); os e-mails da etapa 7 (vão usar estes resultados, mas são outra etapa).
