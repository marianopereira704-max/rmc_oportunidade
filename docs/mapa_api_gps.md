# Mapa da API do GPS Farma (ERP)

Levantado em 25/09/2026, só com consultas de leitura (GET), a partir do Swagger
(`http://143.244.153.213/api/docs`, especificação em `/api/openapi.json`) e de
chamadas reais às lojas de teste. Tudo o que está aqui foi **visto na resposta
da API**; o que ainda não foi confirmado está marcado como **em aberto**.

Complementa a investigação de 16–17/09/2026 (`status_e_pendencias.md` §6).

## 1. Acesso

- Base: `http://143.244.153.213` — **HTTP sem criptografia**: a chave viaja em
  texto claro. Chave em `.streamlit/secrets.toml` (`GPS_API_BASE_URL`,
  `GPS_API_KEY`), nunca no código.
- Autenticação: cabeçalho `X-API-Key`.
- Listagens: `limit` (1–1000) e `offset`. Resposta
  `{"data": [...], "meta": {"total": N, "limit": N, "offset": N}}` — o
  `meta.total` diz de antemão quantas páginas vêm.
- Detalhes (uma empresa, uma loja): `{"data": {...}}` (objeto, não lista).
- A API corta processamento acima de ~10 minutos.

## 2. Hierarquia

```
Empresa (idEmpresa, ex. 698b48e2e0122b20096df0fd)   ← 388 empresas
 └── Loja (CodigoLoja: "1", "2"… ou o próprio CNPJ)  ← vem no detalhe da empresa
      ├── Vendas   (item a item)
      ├── Compras  (item a item, com fornecedor e nota)
      └── Estoque  (uma linha por produto do cadastro)
Produto (CodigoProduto, por empresa) → EAN, grupo, laboratório
```

- Uma loja do RMC (CNPJ) = um par **(idEmpresa, CodigoLoja)** da API.
- `CodigoProduto` é **da empresa** (a mesma farmácia em outra rede terá outro
  código para o mesmo produto). O elo entre redes é o **EAN**.

## 3. Rotas usadas (e para quê)

| Rota | Para quê | Visto |
|---|---|---|
| `GET /api/v1/empresas?limit=1000` | lista das 388 empresas | 1,8 s |
| `GET /api/v1/empresas/{idEmpresa}` | empresa + lojas (com **CNPJ**) | ~1 s |
| `GET /api/v1/empresas/{id}/lojas/{codigoLoja}/vendas?dataInicio&dataFim` | vendas item a item | 3–8 s/página aquecida |
| `GET /api/v1/empresas/{id}/lojas/{codigoLoja}/compras?dataInicio&dataFim` | compras item a item | 4–8 s/página (**erro 500 na Reis**) |
| `GET /api/v1/empresas/{id}/lojas/{codigoLoja}/estoque` | estoque atual por produto | 11 s/página |
| `GET /api/v1/empresas/{id}/estoque` | estoque de todas as lojas | 4 s/página |
| `GET /api/v1/empresas/{id}/produtos` | cadastro de produtos (EAN, grupo, lab.) | 1,5 s/página |

Outras rotas existentes, não necessárias para o Pedido: clientes,
funcionários, fornecedores, detalhe de venda/compra, histórico por produto,
resolução de código de barras.

## 4. Onde está cada informação do Pedido

| Informação do Pedido | Onde está na API | Observação |
|---|---|---|
| Loja (vínculo com a nossa) | detalhe da empresa → `lojas[].CNPJ`, `RazaoSocial`, `NomeLoja` | ver §6 |
| EAN | estoque: `ProdutoRelacionado_CodigoBarras`; compras: idem; produtos: `CodigoBarras` | **vendas NÃO trazem EAN** — só `CodigoProduto` |
| Produto (nome) | `ProdutoRelacionado_NomeProduto` / `NomeProduto` | nome do estoque vem com espaço à esquerda às vezes |
| Categoria | `NomeGrupo` / `ProdutoRelacionado_NomeGrupo` | **texto livre de cada rede** (ver §5); `NomeCategoria` é quase sempre "NAO INFORMADO" |
| Laboratório | `NomeLaboratorio` / `ProdutoRelacionado_NomeLaboratorio` | |
| Venda (demanda) | vendas: `Quantidade`, `Fracao`, `DataVenda`, `TipoVenda` | `Quantidade` em fração quando `Fracao > 1` (ver §7) |
| Estoque atual | estoque: `QtdEstoque` | também `QtdEstoqueMinimo` (do ERP), `PermiteCompra`, `Produtivo` |
| Última compra (lab., data, preço) | compras: `DataEmissaoNF`, `VlrUnitario`, `VlrDesconto`, `VlrTotalLiquido`, `Quantidade`, `Fracao`, `ProdutoRelacionado_NomeLaboratorio`, fornecedor | filtrar `TipoCompra == "0"` **do nosso lado** (ver §8) |
| Nota fiscal da compra | compras: `NroNF`, `CodigoCompra`, `NomeRazaoSocialFornecedor` (100% preenchidos na Hudson), `FornecedorRelacionado_Cnpj` (76%) | |
| Custo de referência | estoque: `VlrPrecoCompra`; vendas: `VlrCMV` | alternativa quando não houver compra no período |

## 5. Categorias (grupos) — cada rede tem os seus

Reis (24.148 produtos): `PERFUMARIA COMISSAO 2%`, `ETICOS`,
`GENERICO COMISSAO 10%`, `GENERICOS ONEROSOS`, `GENERICO COMISSAO 5% `,
`SIMILAR COMISSAO 10%`, `SIMILAR ONEROSO`, `DERMO COSMETICOS`, `FRALDAS`…

Hudson (48.904 produtos): `PERFUMARIA`, `SIMILAR`, `GENERICOS`, `ETICO`,
`LIBERADOS`, `SIMILARES`, `GENNERICO CONTROLADO`, `ETICO CONTROLADO`,
`GENERICO ANTIBIOTICO`, `PERFUMRIA `…

Mistura tipo com comissão, tem erros de digitação e espaços sobrando. Para
virar "categoria" é preciso uma **tradução** (grupo da rede → categoria nossa).

### 5a. Classificação padronizada: a API não tem; a CMED/ANVISA tem

Conferido em 25/09/2026: nem a listagem nem o detalhe de produto
(`/produtos/{codigo}`) trazem um tipo padronizado — só `NomeGrupo`,
`NomeCategoria` (quase sempre "NAO INFORMADO", "TODOS" ou vazio) e
`PrincipioAtivo`. O detalhe traz `codigosBarras` (lista: um produto pode ter
mais de um EAN).

Fonte oficial por EAN: **lista de preços CMED** (ANVISA, mensal,
`gov.br/anvisa/.../cmed/precos`, planilha de 09/09/2026: 26.242
apresentações, colunas `EAN 1/2/3` e `TIPO DE PRODUTO (STATUS DO PRODUTO)`:
Genérico, Similar, Novo, Específico, Biológico, Fitoterápico…).

Cruzamento com os EANs que a Hudson comprou (3 meses + setembro):

| Grupo da rede | na CMED | CMED diz |
|---|---|---|
| Genérico (573) | 94% | 92% Genérico |
| Similar (446) | 58% | 35% Similar, 9% Específico, 6% Novo |
| Ético (949) | 85% | 41% Novo, **32% Similar** |
| Perfumaria (1.666) | 1% | — (não é medicamento) |

### 5b. Categorias FEBRAFAR (planilha do cliente)

`CATEGORIAS_FEBRAFAR.xlsx` (Downloads, 15/09/2026): 221.564 EANs, colunas
`CD_EAN`, `DS_PRODUTO`, `DS_MARCA`, `LAB_FEBRAFAR`, `CATEGORIA` (coluna E,
12 valores): BELEZA PELE CABELOS, HIGIENE, CUIDADOS, NUTRIÇÃO E
SUPLEMENTOS ALIMENTARES, MIP l OTC, INFANTIL, ALIMENTOS E BEBIDAS,
EM CLASSIFICAÇÃO, CONVENIENCIA, PRESCRIÇÃO PROPAGADO, PRESCRIÇÃO GENERICO,
PRESCRIÇÃO TRADE.

Cobertura por EAN: cadastro Reis 75%, cadastro Hudson 53%, **EANs comprados
pela Hudson (3m+set) 87%** (CMED: 40%). Grupo "genérico" da rede →
PRESCRIÇÃO GENERICO em 80% dos comprados. Nos comprados que estão nas duas
listas, PRESCRIÇÃO GENERICO = Genérico na CMED em 487 de 487; a FEBRAFAR é
comercial (prescrição × MIP × beleza…) e a CMED é regulatória (genérico ×
similar × novo): 37 genéricos da CMED são "MIP l OTC" na FEBRAFAR.

### 5c. A fonte por trás da API: o Space `gps-farma-space`

A API lê arquivos da "camada bronze" num Space da DigitalOcean
(`gps-farma-space`, região **sfo3**, confirmado em 25/09/2026). A chave do
nosso bucket (`consultoria-interna`, nyc3) recebe AccessDenied — é preciso
credencial só de leitura liberada pela TI do GPS.

## 6. Lojas e CNPJ

Em 16/09/2026 o CNPJ da loja vinha vazio; **em 25/09/2026 ele vem preenchido**
no detalhe da empresa. Nas lojas de teste, todos bateram com o nosso cadastro:

| Nosso `legacyId` | Nosso cadastro | GPS (empresa / loja) | CNPJ |
|---|---|---|---|
| 60 | Farmácia Central (Hudson Venzel Pêgo) | HUDSON VENZEL PEGO / `04076088000186` | 04.076.088/0001-86 |
| 788 | Drogarias Reis F1 (inativa no nosso cadastro) | DROGARIAS REIS F1 LTDA / `1` | 29.976.378/0001-07 |
| 789 | Drogarias Reis F2 | idem / `2` | 31.062.732/0001-30 |
| 790 | Drogarias Reis F3 | idem / `3` | 04.635.947/0001-20 |
| 787 | Drogaria Nova Nacional | idem / `4` ("DROGARIAS REIS F4 LTDA") | 23.630.459/0001-74 |

Os números 60, 787–790 são o `legacyId` do sistema interno (hoje não
guardado no nosso cadastro). Cobertura de CNPJ em todas as empresas: §10.

## 7. `Fracao` (venda e compra fracionada)

Conferido na Hudson em 25/09/2026:

| Onde | Unidade | Exemplo |
|---|---|---|
| Venda (`Quantidade`, `VlrUnitario`) | **fração** | SONRISAL CX 30: `Quantidade 2`, `Fracao 30`, R$ 3,50 cada = 2 envelopes |
| Estoque (`QtdEstoque`, `VlrPrecoVenda`) | **fração** | TORSILAX (Fracao 25): estoque 42, venda R$ 5,00 = 42 cartelas |
| Compra (`Quantidade`, `VlrUnitario`) | **embalagem da compra** (`Fracao` unidades cada) | SELENE CARTELA (Fracao 3): 3 × R$ 61,04 = 3 embalagens de 3 cartelas = R$ 20,35 por cartela |

**O `Fracao` da compra é da embalagem DAQUELA compra, não do produto**
(conferido em 27/09/2026 nas 7.639 compras da Hudson): 413 produtos (9,8%;
13,2% do valor) vêm com `Fracao` > 1 — mais comuns 6, 12, 3, 4, 24 — e em 64
deles o valor muda de uma compra para outra (SELENE: 1, 3 e 6; creme NIVEA
lata: 1 e 6 = fardo). Portanto:

- unidades compradas = `Quantidade × Fracao`;
- preço por unidade = `VlrUnitario × (1 − VlrDesconto/100) ÷ Fracao`;
- estoque e venda já estão na menor unidade.
`VlrPrecoCompra` do estoque em fracionados vem com valores sem sentido
(SONRISAL R$ 0,06; POLYDRAT R$ 0,015) — não usar como preço nesses itens.

## 7a. Preço da compra: sem ST

Nas 7.639 compras da Hudson (3 meses + setembro):

| Relação | Linhas |
|---|---|
| `VlrTotalLiquido` > `Quantidade × VlrUnitario` (inclui **ST**; CFOP 1403) | 3.553 (46%) |
| `VlrTotalLiquido` = `Quantidade × VlrUnitario` | 2.039 |
| `VlrTotalLiquido` = `Quantidade × VlrUnitario × (1 − VlrDesconto/100)` | 1.966 |
| outros | 81 |

- `VlrDesconto` é **percentual**, não valor em reais.
- Preço comparável (sem ST, igual à regra da planilha GPS e à Gruppy):
  **`VlrUnitario × (1 − VlrDesconto/100)`**. `VlrTotalLiquido ÷ Quantidade`
  (decisão de 16/09) inclui ST e superestima o preço.
- Exemplo do cliente: `Quantidade 2`, `VlrUnitario 89,80`, `VlrDesconto 0`,
  `VlrTotalLiquido 189,90` → preço = R$ 89,80 (o total tem R$ 10,30 de ST).
- Bonificação (`VlrUnitario` < R$ 0,10): 1 linha em 7.639.

## 8. Defeitos encontrados na API (levar ao suporte do GPS)

1. **Compras da Reis dão erro 500** em qualquer consulta (rota da empresa e da
   loja, com e sem filtro, até sem data). Na Hudson funciona.
2. **Filtro `tipoCompra=0` devolve 0 linhas** mesmo quando todas as linhas têm
   `TipoCompra "0"` — filtrar do nosso lado.
3. HTTP sem TLS (chave em texto claro).

## 9. Desempenho medido (o que define o desenho)

- **Aquecimento por EMPRESA:** a primeira consulta a uma empresa custa de 30 s
  a mais de 10 min (a de 3 meses da Reis estourou 10 min; a de 1 dia, 235 s).
  Depois, qualquer loja e qualquer período da mesma empresa respondem em
  segundos (loja 3 da Reis, nunca consultada: 2,7 s).
- Volume por loja (3 meses + 25 dias de setembro):
  vendas 26–30 mil linhas; compras ~7,6 mil; estoque = cadastro inteiro
  (24–49 mil linhas, com os de estoque zero).
- Páginas de 1.000 linhas aquecidas: vendas 3–8 s, compras 4–8 s,
  estoque 11 s (loja) / 4 s (empresa), produtos 1,5 s.
- Vendas da loja em 3m+: ~29 páginas ≈ **4 min** (aquecida).
- Um dia de vendas de uma loja: 158–178 linhas (1 página).

## 10. Cobertura de CNPJ nas lojas do GPS

Varredura do detalhe das 388 empresas em 25/09/2026 (253 s, nenhuma falha):

| | Lojas |
|---|---|
| Lojas no GPS | 409 |
| Com CNPJ preenchido | 379 (93%) |
| CNPJ encontrado no cadastro do sistema interno | 377 |
| … e a loja está **ativa** no nosso cadastro | 341 |
| Sem CNPJ (casar pela razão social, com confirmação) | 30 |

Ou seja: o vínculo loja RMC ↔ loja GPS sai **automático pelo CNPJ** para
quase todas. Nas 30 sem CNPJ a razão social vem **vazia** (e o bairro só
aparece dentro do nome da loja); o que casa é o **número do endereço**
(rota `/empresas/{id}/lojas`, campo `Numero`) + UF + nome — regras em
`pedido/vinculo.py`. Resultado com as 805 lojas ativas (27/09/2026):
**365 automáticos, 8 para confirmar, 36 sem loja parecida** (lojas que não
são clientes ativos ou estão inativas no nosso cadastro). O vínculo continua
precisando ser gravado (e conferido quando o GPS mudar o CNPJ de uma loja).
