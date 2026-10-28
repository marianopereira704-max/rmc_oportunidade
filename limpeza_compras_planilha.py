"""Limpeza das compras da PLANILHA do GPS — depois de publicar a Análise
pela API (decisão de 05/10/2026: a Por Loja e a Por Produto leem as compras
da API, montadas pela rotina; as compras que vieram do BI ficaram sem uso).

ATENÇÃO À ORDEM: rode só DEPOIS de publicar a versão nova (push no main). O
app publicado antigo ainda lê estas tabelas — rodar antes deixa a Análise
dele vazia até a publicação.

O que APAGA (tudo numa transação — ou vai tudo, ou nada):
- todas as compras da planilha (`registros_compra_gps`), de qualquer mês;
- as compras de CNPJ órfão (`compras_gps_orfas`) e a fila de CNPJ órfão
  (`fila_cnpj_orfao`) — a fila saiu do app; loja sem vínculo vai para
  Dados → Pendências → Vínculo de lojas GPS;
- os registros de envio da planilha (`uploads_gps`);
- o "último mapeamento de colunas" do GPS (o da Gruppy fica).

O que MANTÉM:
- os arquivos originais das planilhas no Explorador (Q10 de 05/10/2026:
  ficam como histórico, sem mexer no bucket compartilhado);
- a fila de EAN inteira — o valor dos itens de origem GPS passa a vir das
  compras da API (integrations/fila_compras.py, uma vez por dia);
- Base Genéricos, EANs resolvidos, tabelas Gruppy, lojas, vínculos.

As TABELAS continuam no banco, vazias: migração nunca apaga tabela (o teste
de migrações compara modelo × tabela).

Uso (na raiz do projeto):
    python limpeza_compras_planilha.py            # só mostra o que faria
    python limpeza_compras_planilha.py --executar # apaga de verdade
"""
from __future__ import annotations

import sys

from sqlalchemy import delete, func, select

from core.db import get_session
from core.models import (
    BaseGenerico,
    CompraGPSOrfa,
    EanGenerico,
    FilaCnpjOrfao,
    FilaResolucaoEAN,
    ItemTabelaGruppy,
    OrigemFila,
    RegistroCompraGPS,
    UltimoMapeamentoColuna,
    UploadGPS,
)


def _contar(session, modelo, *condicoes) -> int:
    return session.execute(select(func.count()).select_from(modelo).where(*condicoes)).scalar()


def main(executar: bool) -> None:
    with get_session() as s:
        meses = sorted(set(s.execute(select(RegistroCompraGPS.ano_mes).distinct()).scalars()))
        previa = {
            "compras da planilha (registros_compra_gps)": _contar(s, RegistroCompraGPS),
            "compras de CNPJ órfão (compras_gps_orfas)": _contar(s, CompraGPSOrfa),
            "fila de CNPJ órfão (fila_cnpj_orfao)": _contar(s, FilaCnpjOrfao),
            "envios da planilha (uploads_gps)": _contar(s, UploadGPS),
            "mapeamento de colunas do GPS": _contar(s, UltimoMapeamentoColuna,
                                                    UltimoMapeamentoColuna.fornecedor == OrigemFila.GPS),
        }
        mantidos = {
            "EANs resolvidos": _contar(s, EanGenerico),
            "Base Genéricos": _contar(s, BaseGenerico),
            "itens Gruppy": _contar(s, ItemTabelaGruppy),
            "fila de EAN": _contar(s, FilaResolucaoEAN),
        }
        print("Meses de compra da planilha no banco:", meses or "nenhum")
        print("Vai apagar:")
        for nome, qtd in previa.items():
            print(f"  {nome}: {qtd}")
        print("Mantém:", mantidos)
        if not executar:
            print("\nNada foi alterado. Rode com --executar para aplicar (depois de publicar a versão nova).")
            return

        s.execute(delete(RegistroCompraGPS))
        s.execute(delete(CompraGPSOrfa))
        s.execute(delete(FilaCnpjOrfao))
        s.execute(delete(UploadGPS))
        s.execute(delete(UltimoMapeamentoColuna).where(UltimoMapeamentoColuna.fornecedor == OrigemFila.GPS))

        for nome, modelo in (("EANs resolvidos", EanGenerico), ("Base Genéricos", BaseGenerico),
                             ("itens Gruppy", ItemTabelaGruppy), ("fila de EAN", FilaResolucaoEAN)):
            assert _contar(s, modelo) == mantidos[nome], f"{nome} mudou — nada foi gravado"
        print("\nFeito. As compras da planilha saíram; os arquivos originais continuam no Explorador.")


if __name__ == "__main__":
    main(executar="--executar" in sys.argv)
