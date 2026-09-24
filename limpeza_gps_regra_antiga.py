"""Limpeza ÚNICA antes de recarregar GPS e Gruppy do zero.

Decidido em 23–24/09/2026: as compras do GPS foram carregadas em regras
anteriores (Fat × %CMV ÷ QTD, com imposto; depois sem os campos de recuo de
preço) e as tabelas Gruppy também serão reenviadas — tudo é apagado pra
recomeçar limpo.

O que APAGA (tudo numa transação — ou vai tudo, ou nada):
- todas as compras do GPS, de QUALQUER mês (`registros_compra_gps`) e as
  compras de CNPJ órfão (`compras_gps_orfas`);
- os registros de upload do GPS (`uploads_gps`);
- toda a base Gruppy: itens, coberturas por UF e tabelas;
- a fila de CNPJ órfão inteira;
- os itens PENDENTES da fila de EAN (de GPS e de Gruppy — eles só existem por
  causa desses dados);
- o "último mapeamento de colunas" do GPS e da Gruppy.

O que MANTÉM: Base Genéricos, todos os EANs já resolvidos (decisões humanas),
itens da fila de EAN já resolvidos ou ignorados, lojas. Os arquivos originais
(.xlsx do GPS e da Gruppy) NÃO são apagados do Spaces: vão para `_Inativos` no
Explorador de Arquivos.

Uso (na raiz do projeto):
    python limpeza_gps_regra_antiga.py            # só mostra o que faria
    python limpeza_gps_regra_antiga.py --executar # apaga de verdade
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
    FSNode,
    ItemTabelaGruppy,
    RegistroCompraGPS,
    StatusFila,
    StatusNode,
    TabelaGruppy,
    TabelaGruppyCobertura,
    UltimoMapeamentoColuna,
    UploadGPS,
)
from storage import filesystem as fs


def _contar(session, stmt) -> int:
    return session.execute(select(func.count()).select_from(stmt.subquery())).scalar()


def main(executar: bool) -> None:
    with get_session() as s:
        meses = sorted(set(s.execute(select(RegistroCompraGPS.ano_mes).distinct()).scalars()))
        laboratorios = sorted(set(s.execute(select(TabelaGruppy.laboratorio).distinct()).scalars()))

        previa = {
            "compras GPS": _contar(s, select(RegistroCompraGPS.id)),
            "compras GPS de CNPJ órfão": _contar(s, select(CompraGPSOrfa.id)),
            "uploads GPS": _contar(s, select(UploadGPS.id)),
            "tabelas Gruppy": _contar(s, select(TabelaGruppy.id)),
            "coberturas Gruppy (UF)": _contar(s, select(TabelaGruppyCobertura.id)),
            "itens Gruppy": _contar(s, select(ItemTabelaGruppy.id)),
            "fila CNPJ órfão": _contar(s, select(FilaCnpjOrfao.id)),
            "fila EAN (pendentes)": _contar(
                s, select(FilaResolucaoEAN.id).where(FilaResolucaoEAN.status == StatusFila.PENDENTE)
            ),
            "mapeamentos de coluna (GPS e Gruppy)": _contar(s, select(UltimoMapeamentoColuna.id)),
        }
        mantidos = {
            "EANs resolvidos": s.execute(select(func.count()).select_from(EanGenerico)).scalar(),
            "Base Genéricos": s.execute(select(func.count()).select_from(BaseGenerico)).scalar(),
        }
        print("Meses de compra GPS no banco:", meses or "nenhum")
        print("Laboratórios Gruppy no banco:", laboratorios or "nenhum")
        print("Vai apagar:")
        for nome, qtd in previa.items():
            print(f"  {nome}: {qtd}")
        print("Mantém:", mantidos)
        if not executar:
            print("\nNada foi alterado. Rode com --executar para aplicar.")
            return

        # Arquivos originais (GPS e Gruppy) — vão pra _Inativos, não são apagados.
        nodes = set(s.execute(select(UploadGPS.fs_node_id)).scalars())
        nodes |= {n for n in s.execute(select(TabelaGruppy.upload_fs_node_id)).scalars() if n is not None}

        # Filhos antes dos pais (chaves estrangeiras).
        s.execute(delete(RegistroCompraGPS))
        s.execute(delete(CompraGPSOrfa))
        s.execute(delete(UploadGPS))
        s.execute(delete(ItemTabelaGruppy))
        s.execute(delete(TabelaGruppyCobertura))
        s.execute(delete(TabelaGruppy))
        s.execute(delete(FilaCnpjOrfao))
        s.execute(delete(FilaResolucaoEAN).where(FilaResolucaoEAN.status == StatusFila.PENDENTE))
        s.execute(delete(UltimoMapeamentoColuna))

        inativados = 0
        for node_id in nodes:
            node = s.get(FSNode, node_id)
            if node is not None and node.status == StatusNode.ATIVO:
                fs.inativar(s, node_id)
                inativados += 1

        assert s.execute(select(func.count()).select_from(EanGenerico)).scalar() == mantidos["EANs resolvidos"]
        assert s.execute(select(func.count()).select_from(BaseGenerico)).scalar() == mantidos["Base Genéricos"]
        print(f"\nFeito. {inativados} arquivo(s) antigo(s) movido(s) para _Inativos.")


if __name__ == "__main__":
    main(executar="--executar" in sys.argv)
