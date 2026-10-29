# Cálculo de madrugada — plano de implementação (29/10/2026)

Implementa `docs/specs/2026-10-29-calculo-madrugada-design.md` (aprovado pelo Mariano em 29/10/2026). **C** = o
Claude faz; **M** = o Mariano faz. Cada tarefa termina com pytest inteiro passando; as marcadas com 👁 também com
o teste visual sem nenhuma diferença. Tudo no ramo `desenvolvimento`; o app publicado (`main`) não é tocado.

## Ordem

| # | Tarefa | Quem | Entrega |
|---|---|---|---|
| 1 | Mover as 4 contas para `pedido/telas_loja.py` 👁 | C | Contas sem Streamlit, resultado idêntico |
| 2 | Assinatura e `VERSAO` | C | `pedido/precalculo.assinatura` |
| 3 | Selo | C | `pedido/selo.py` + `PRECALCULO_CHAVE` |
| 4 | Fotos dos insumos | C | Cópia dos genéricos + `integrations/insumos.py` |
| 5 | Pontos de publicação | C | Publica ao mudar e na conferência diária |
| 6 | Rotina `pedido/precalculo.py` | C | CLI, partes, pular em dia, relatório, limpeza |
| 7 | Leitura do pronto no app 👁 | C | Telas tentam o pronto primeiro |
| 8 | Workflow, alerta e Dados → Rotinas | C | Job, opção "só o pré-cálculo", avisos |
| 9 | Teste visual pelo caminho do pronto 👁 | C | Volta extra no roteiro |
| 10 | Secret, 1ª execução à mão, conferência real, agendar | M + C | 100% idêntico em todas as lojas |

**Pontos de parada para o M conferir:** depois da 1 (a mudança de lugar das contas), da 7 (o caminho completo
funcionando na loja fictícia) e na 10 (antes de ligar no agendamento).

**Andamento (29/10/2026): tarefas 1 a 7 feitas.** Conferido na loja fictícia (ambiente isolado): as 4 telas
iguais à foto de antes depois da mudança de lugar; publicar as fotos é idempotente (2ª vez não regrava); a rotina
calcula as 4 telas e, na 2ª execução, pula tudo; com as contas PROIBIDAS de rodar, o app lê os 4 prontos e eles
saem idênticos à foto de antes; chave errada e personalização mudada depois da madrugada → o app calcula na hora.
Pytest: 632 (os casos de assinatura, selo e rotina em `tests/test_precalculo.py`). Teste visual: nenhuma tela mudou.
Diferença do plano: a conferência de ponta a ponta com a loja fictícia ficou num script (o `loja_ficticia` só monta
no ambiente do teste visual, com banco próprio); vira parte do teste visual na tarefa 9.

**Tarefas 8 e 9 feitas (29/10/2026).** Workflow: job `precalculo` (8 partes, depois da coleta, junto com o `rede`),
"O que rodar" no botão (`completa` / `so_precalculo`), agendado só com `PRECALCULO_AGENDADO = 1`; alerta com
`--precalculo` (quando o job rodou) e `--so-precalculo` (não reconfere a coleta nem regrava o resumo da noite);
relatório com `lojas_prontas` e `inicio`; linha em Dados → Rotinas. Teste visual: 2ª volta do Assistente e do
Dashboard com os dados brutos da loja fictícia ESCONDIDOS depois do pré-cálculo — 42 capturas iguais às referências.
De quebra, a referência `pedido_loja_1280` tinha um balão de dica aberto (o mouse ficava em cima de uma célula
depois do "Entendi"); refeita sem ele. Pytest: 640.

## 1. Mover as 4 contas para `pedido/telas_loja.py` 👁

- **Antes de mexer:** gravar, com o código de hoje, o resultado das 4 telas da loja fictícia
  (`tests/visual/loja_ficticia.py`) num SQLite de teste — é a "foto de antes" da conferência.
- Criar `pedido/telas_loja.py` com `assistente(...)`, `estoque(...)`, `ruptura(...)`, `compras(...)`: recebem tudo
  explícito (armazenamento, chaves, empresa, loja, meses, configuração, correções, base de categorias, genéricos,
  tabela de classificação) e devolvem o mesmo objeto que as telas guardam hoje.
- Os corpos saem de `views/pedido/dados_loja.py::_calcular` e `views/dashboard_comum.py::_estoque_preparado`,
  `_ruptura_preparada`, `_compras_preparadas`, sem mudar uma conta; as funções das telas ficam com o
  `st.cache_data` e o `@limite.pesado` e passam a chamar as novas.
- **Teste:** `tests/test_telas_loja.py` — as 4 funções, com a loja fictícia, dão exatamente a "foto de antes".
  Conferir também que `pedido/telas_loja.py` não importa `streamlit` nem `core.db`.

## 2. Assinatura e `VERSAO`

- `pedido/precalculo.py`: `VERSAO = 1` e `assinatura(tela, foto, versoes, config, correcoes)` (hash estável:
  JSON ordenado da configuração efetiva, correções só para Assistente e Ruptura).
- **Teste:** muda com foto, cada versão de base, configuração, personalização e `VERSAO`; correção muda Assistente
  e Ruptura mas não Estoque nem Compras; mesma entrada → mesma assinatura em duas execuções.

## 3. Selo

- `pedido/selo.py`: `selar(bytes, chave)` e `abrir(bytes, chave) -> bytes | None` (HMAC-SHA256 na frente do
  conteúdo; comparação em tempo constante). `PRECALCULO_CHAVE` em `core/config.py` (Secret; sem ele, selar recusa e
  abrir devolve None).
- **Teste:** volta igual; conteúdo alterado, selo alterado, outra chave ou chave ausente → None.

## 4. Fotos dos insumos

- `core/copia_spaces.py` ganha a tabela de genéricos (mesmo padrão das outras duas).
- `integrations/insumos.py`: `publicar(session, armaz)` grava `pedido/insumos/<banco>/manifesto.json` com
  configuração padrão, personalizações por loja, correções por loja, vínculos loja RMC ↔ GPS, as versões das
  bases e as chaves das cópias das 3 tabelas (gravando as cópias que faltarem).
- `pedido/precalculo.py`: `ler_insumos(armaz, banco)` devolve tudo pronto para as contas (lado da rotina, sem banco).
- **Teste:** publicar num SQLite de teste e ler de volta dá as mesmas tabelas e a mesma configuração efetiva de cada
  loja que o app usa (`cfg.aplicar(padrão, personalização)`).

## 5. Pontos de publicação

- Republicar em: `views/pedido/dados_loja.invalidar` (chamado pela aba Dados e pelas Configurações) e ao gravar
  uma correção de estoque negativo (`views/pedido/dialogos.py`).
- Conferência diária: no 1º acesso de cada dia, em segundo plano e no máximo 1× por dia por processo (mesmo
  esquema de `loja_barra.conferir_vinculos_em_segundo_plano`), compara as versões publicadas com as do banco e
  publica se diferirem.
- **Teste:** cada ponto chama a publicação; a conferência publica quando a versão difere e não publica quando é
  igual; falha na publicação nunca quebra a tela (só registra no log).

## 6. Rotina `pedido/precalculo.py`

- `python -m pedido.precalculo --parte i --partes 8 [--lojas 60,64] [--destino local:<pasta>]`.
- Para cada banco em `pedido/insumos/`: lê os insumos uma vez; divide as lojas com vínculo e foto de estoque
  entre as partes (`coleta.dividir`); por loja, as 4 contas de `pedido/telas_loja.py`; grava
  `pedido/resultados/<banco>/<empresa>/<loja>/<tela>.bin` (pickle selado) e `<tela>.json` (assinatura, gerado_em,
  segundos); pula a tela com assinatura igual à gravada; falha numa loja vai para o relatório e a próxima segue.
- Relatório `pedido/controle/precalculo/<data>/<parte>.json` (calculadas, em dia, falhas, segundos).
- Limpeza: apaga, uma chave por vez, os resultados de lojas que não estão mais no manifesto (só em
  `pedido/resultados/<banco>/`).
- **Teste:** com `ArmazenamentoLocal` e a loja fictícia: grava as 4 telas; 2ª execução pula tudo; foto nova
  recalcula; divisão entre partes cobre todas as lojas sem repetir; falha numa loja não para as outras; limpeza
  não apaga nada fora da pasta.

## 7. Leitura do pronto no app 👁

- `pedido/precalculo.ler(armaz, banco, empresa, loja, tela, assinatura) -> objeto | None` (confere o `.json`,
  depois o selo, depois abre).
- `_calcular` e as 3 `_*_preparada` montam a assinatura com o que já recebem e tentam o pronto antes de calcular.
- **Teste de ponta a ponta:** publicar insumos → rodar o pré-cálculo → as 4 telas lidas do pronto são idênticas
  ao cálculo na hora; mudar a personalização → o app calcula na hora; selo inválido → calcula na hora.

## 8. Workflow, alerta e Dados → Rotinas

- Job `precalculo` (8 partes) depois da `coleta`, em paralelo com o `rede`; `alerta` com `needs` nos dois.
- `workflow_dispatch` com a opção "só o pré-cálculo" (pula coleta e rede). No agendamento, o job só roda com a
  variável do repositório `PRECALCULO_AGENDADO = 1` (GitHub → Settings → Variables, sem custo): o M liga depois
  da conferência da tarefa 10.
- `pedido/alerta.py`: relatório faltando ou mais de 10% de falhas → e-mail.
- Dados → Rotinas: "Pré-cálculo: N de M lojas prontas · última execução dd/mm às hh:mm".
- **Teste:** alerta nos casos novos; contagem da linha de Dados → Rotinas.

## 9. Teste visual pelo caminho do pronto 👁

- O roteiro publica os insumos e roda o pré-cálculo da loja fictícia antes de abrir o Assistente e o Dashboard
  numa segunda volta; as capturas têm que ser IGUAIS às do caminho de cálculo na hora.

## 10. Secret, 1ª execução, conferência real e agendamento (M + C)

1. (M) Criar o Secret `PRECALCULO_CHAVE` (uma frase longa qualquer) no GitHub e no `.streamlit/secrets.toml`
   local (e no Streamlit Cloud só na publicação).
2. (M) Enviar para o `desenvolvimento` e rodar à mão com "só o pré-cálculo".
3. (C) Script só de leitura: em TODAS as lojas, pronto × cálculo na hora, tela a tela — 100% idêntico; e o tempo de
   abrir 3 lojas (grande, média, pequena) pelos dois caminhos.
4. (M) Ligar `PRECALCULO_AGENDADO = 1`.
5. (C) `CLAUDE.md`, `docs/rotina_pedido.md` (Secret e variável novos) e o plano de ação atualizados.

## Como executar

Escolha do M:
- **A (recomendado): em sequência nesta conversa**, com as 3 paradas para conferir (depois da 1, da 7 e na 10).
- B: tarefa por tarefa, cada uma só depois da sua confirmação.
