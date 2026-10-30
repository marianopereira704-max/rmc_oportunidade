"""Motor de reconciliação EAN -> Base Genéricos — dividido em 29/10/2026 (etapa 8 do plano de ação; o arquivo
tinha 947 linhas). Fica só repassando os nomes, para nenhum import antigo quebrar:

- `reconciliation/fuzzy.py` — comparação de nomes: cache de EANs resolvidos, candidatos, os 3 níveis, `resolver_ean`;
- `reconciliation/fila.py` — a Fila de Resolução de EAN (gravar em lote, confirmar, cadastrar, ignorar, listar);
- `reconciliation/importacao_base.py` — importação da planilha Base Genéricos;
- `reconciliation/reprocessamento.py` — limpeza de EAN sujo e reprocessamento da fila.

Código novo importa direto do arquivo certo. Para trocar uma função num teste, troque no módulo onde ela mora
(ex.: `fuzzy.buscar_candidatos`): trocar aqui não muda quem a chama.
"""
from __future__ import annotations

from core.config import settings  # noqa: F401 — os testes ajustam os limiares por aqui
from reconciliation.fila import (  # noqa: F401
    _gravar_bloco_fila,
    confirmar_resolucao_manual,
    ignorar_fila,
    listar_fila_priorizada,
    registrar_novo_generico,
    soma_ocorrencias_pendentes,
    upsert_fila_resolucao,
    upsert_fila_resolucao_em_lote,
)
from reconciliation.fuzzy import (  # noqa: F401
    Decisao,
    buscar_candidatos,
    carregar_cache_eans_resolvidos,
    classificar,
    completar_cache_eans_resolvidos,
    resolver_ean,
)
from reconciliation.importacao_base import ResultadoImportacaoBase, importar_base_genericos  # noqa: F401
from reconciliation.reprocessamento import (  # noqa: F401
    ResultadoLimpezaEan,
    ResultadoReprocessamentoFila,
    limpar_eans_sujos,
    reprocessar_fila_resolucao,
)
