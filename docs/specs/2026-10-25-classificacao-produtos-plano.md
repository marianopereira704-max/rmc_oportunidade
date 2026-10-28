# Classificação de produtos em cascata — plano de implementação

Design aprovado em 25/10/2026: `2026-10-25-classificacao-produtos-design.md`. Os 5 pontos em aberto do design seguem
os valores recomendados: resto = **"Em classificação"**, rótulos no **singular**, tipos da CMED conferidos na prévia
da carga, cascata em cache por versão, edição com confirmação e histórico.

Regras de execução (do `CLAUDE.md`): nunca commit/branch/push; não publicar; gravar em banco ou bucket reais só com
prévia e depois do seu OK; textos em português; dicas pela regra de `core/dicas.py`; ao fim de cada etapa, rodar a
suíte e listar os arquivos alterados.

## Etapas

**C1 — Regra pura (sem banco, sem tela).** `pedido/classificacao.py`: `categoria_do_ean`, `categoria_do_produto`
(vários EANs), `arvore` (agregação da cascata) e os mapas padrão (IQVIA e CMED). Testes com tabelas pequenas: ordem
manual > IQVIA > CMED > Em classificação; só Genérico e Similar da CMED classificam; FEBRAFAR nunca classifica;
produto com vários EANs; FEBRAFAR só aparece com IQVIA ou CMED; o caso "sem IQVIA" desce direto sob a categoria;
contagens por nível. Nada do app usa isso ainda.

**C2 — Dados.** Migração `0019_classificacao_ean` (só acrescenta) + modelo `ClassificacaoEan` +
`integrations/classificacao.py` (carga por COPY no Postgres / upsert no SQLite, leitura com versão). Teste de
migração (modelo × tabela) e de carga.

**C3 — Carga.** `pedido.carga_categorias` passa a montar também `classificacao_ean` (FEBRAFAR categoria + CMED tipo
bruto por EAN). **Prévia obrigatória** lista os tipos da CMED encontrados e quantos EANs cada um tem. Botão em Dados →
Importar → Categorias carrega no banco. **Parada A:** você roda a prévia com as duas planilhas e confere os tipos
da CMED antes de gravar qualquer coisa (só no banco dev; o Spaces só com o seu OK).

**C4 — Configuração.** `ConfigPedido.linhas_por_cmed`; `linhas_por_setor` e `linhas_por_cmed` saem da personalização
por loja (valores já gravados ficam inertes). Rótulos no singular e "Em classificação" em `pedido/linhas.py`, no
Cadastro e nos gráficos. Códigos gravados nos usuários não mudam.

**C5 — Consumidores.** O Assistente (corte do Comprador) e o Dashboard (Estoque e Ruptura) passam a usar a nova
classificação: o mapa EAN → categoria entra no lugar do `_catalogo` (EAN → setor), com a versão conferida a cada 5
minutos como hoje. Comparação antes e depois na loja 60 (estoque a custo por categoria), para a medição de 25/10 se
repetir com o código de verdade. Substitui `pedido/linhas.py::coluna` e a reserva da Q36 A.

**C6 — Tela.** Seção "Classificação de produtos" nas Configurações de Pedidos: cascata em `<details>` aninhados
(5 → IQVIA → FEBRAFAR → CMED, setinha no fim de cada barra, só o que existe) e botão "Editar correspondência" com
pop-up "curto" (setores da IQVIA e tipos da CMED, uma escolha entre as 5 categorias, confirmação na gravação).
Teste visual: captura da cascata fechada, aberta e do pop-up. **Parada B:** você confere a tela com os dados reais.

**C7 — Fechamento.** Medir o tempo da cascata com o banco em Nova York (cache frio e quente), suíte completa, teste
visual com `--atualizar`, `CLAUDE.md` e `status_e_pendencias.md`, e a lista de arquivos alterados.

## Ordem e dependências

C1 → C2 → C3 (Parada A) → C4 → C5 → C6 (Parada B) → C7. C1 e C2 não mudam nada visível. O app só passa a usar a
classificação nova na C5, e só depois de a carga da C3 estar no banco dev.

## Riscos acompanhados

- Carga: a CMED só traz o tipo no arquivo; sem recarregar, Genérico e Similar da CMED não funcionam. Por isso a Parada A.
- Desempenho da cascata (~550 mil linhas): medido na C7; se passar de 3 s, a agregação sai do app e vai para a carga.
- Mudar o mapa muda o que os Compradores veem: confirmação e histórico na C6.

## Andamento

- **C1** (26/10/2026): regra pura em `pedido/classificacao.py`, 30 testes.
- **C2** (26/10/2026): migração `0019_classificacao_ean`, modelo `ClassificacaoEan`, `integrations/classificacao.py`.
- **C3** (26/10/2026): carga com prévia. Lista da CMED de 09/10/2026 (gov.br) e FEBRAFAR de Downloads. O leitor da CMED
  passou a procurar o cabeçalho em todas as abas (o arquivo novo tem "Cabeçalho" e "Lista PMC"). Tipos reais da CMED
  (EANs): Genérico 10.200, Similar 9.287, Novo 3.889, Específico 2.109, Biológico 1.775, Fitoterápico 335, Terapia
  Avançada 81, Radiofármaco 3 — só os dois primeiros classificam. **Parada A concluída:** gravado no Spaces só
  `pedido/categorias/classificacao_ean.parquet` (`--so-classificacao`; a base inicial NÃO foi refeita) e carregado no
  banco dev (235.289 EANs). Universo real: 273.065 EANs; leitura 6 s (banco em NY), classificar 0,5 s, cascata 0,2 s.
  Divisão: perfumaria 231.796, genéricos 11.893, propagados 11.095, em classificação 10.715, similares 7.566.
- **C4** (26/10/2026): `ConfigPedido.linhas_por_cmed` (padrão Genérico → genericos, Similar → similares, validado);
  `linhas_por_setor` e `linhas_por_cmed` saíram da personalização por loja (`FORA_DA_PERSONALIZACAO`; valor antigo
  gravado fica inerte; só havia 1 personalização, sem linhas); a seção "Linhas de compra" só aparece no padrão.
  Rótulos no singular e "Em classificação" (`pedido/linhas.py`, Cadastro); códigos gravados não mudaram. Os
  rótulos novos aparecem nas capturas do Dashboard e do Cadastro: o teste visual precisa de `--atualizar` (C6).
- **C5** (26/10/2026): Assistente (corte do Comprador) e Dashboard (Ruptura e Estoque) usam a cascata. Cache global
  `views/pedido._classificacao` (EAN → categoria e origem, por versão das três fontes + mapa do padrão), e cada tela
  pega só os EANs da loja (`classificacao.mapa_da_loja`). Saíram `linhas.coluna`, `linhas.filtrar` e a reserva da
  Q36 A. Loja 60, estoque a custo, antes → depois: Genérico 8,8% → 8,8%; Perfumaria 49,4% → 49,3%; Propagado 22,1%
  → 22,1%; Similar 3,0% → 3,5%; Em classificação 16,7% → 16,3%. 79 de 16.413 SKUs mudaram de linha: 70 de "Em
  classificação" para Similar (pela CMED) e 9 de Perfumaria para "Em classificação" (só a FEBRAFAR os conhecia).
  Tempo da 1ª abertura (cache frio): ler as fontes 6,9 s, classificar 0,8 s; depois, 106 ms por loja.
- **C6** (26/10/2026): seção "Classificação de produtos" no fim da página Padrão das Configurações
  (`views/classificacao_produtos.py`, CSS "CLASSIFICAÇÃO DE PRODUTOS" em `core/theme.py`): cascata em `<details>`
  aninhados (setinha no fim da barra, etiqueta da fonte, EANs e % no nível 1) e pop-up "curto" "Editar
  correspondência" (setores da IQVIA e tipos da CMED → categoria; o 1º clique mostra o que muda, o 2º grava no
  histórico das Configurações). A tabela "Linhas de compra" saiu do formulário. Loja fictícia com IQVIA, FEBRAFAR e
  CMED; capturas `config_pedido_classificacao`, `_aberta` e `config_pedido_correspondencia` (saiu
  `config_pedido_linhas`). Teste visual OK com `--atualizar`. **Parada B:** conferir com os dados reais.
- **C7** (26/10/2026): medido no banco dev: ler as três fontes 7–9 s na 1ª vez de cada versão (a leitura antiga, só
  a IQVIA, já levava 2,5–6 s; agora +2,5 s da FEBRAFAR/CMED), classificar 0,6 s, cascata 0,3 s (abaixo dos 3 s do
  risco: a agregação fica no app), HTML 28 KB (56 barras que abrem), cache 6 MB (Assistente/Dashboard) + 5 KB
  (cascata). `CLAUDE.md` e `status_e_pendencias.md` atualizados. Concluído.
