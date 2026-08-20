"""Sugestão de mapeamento campo -> coluna para o popup de confirmação que
antecede o processamento de uma planilha da GPS ou da Gruppy (nome de coluna
às vezes é ambíguo demais pra heurística de sinônimo resolver sozinha — ex:
"Preço" pode ser preço bruto ou líquido na Gruppy).

Prioridade da sugestão: (1) o último mapeamento confirmado pelo admin pra
aquele fornecedor, SE a coluna sugerida ainda existir na planilha atual (uma
planilha nova pode não ter a mesma coluna que a anterior tinha); (2) senão, a
detecção automática por sinônimo que já existe em integrations/gps.py e
integrations/gruppy.py.

Este módulo não sabe nada de xlsx/pandas — só orquestra a sugestão e a
atualização do "último mapeamento confirmado" (`UltimoMapeamentoColuna`),
usando o dict campo -> coluna que cada integração já sabe detectar/precisar.

Também serializa/desserializa o mapeamento EXATO gravado por upload
(`UploadGPS.mapa_colunas_json` / `TabelaGruppy.mapa_colunas_json`) — um
registro por upload, diferente do "último confirmado" acima (que é global
por fornecedor e só serve de sugestão pra próxima planilha).
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import OrigemFila, UltimoMapeamentoColuna


def serializar_mapa(mapa: dict[str, str]) -> str:
    return json.dumps(mapa, ensure_ascii=False, sort_keys=True)


def desserializar_mapa(texto: str | None) -> dict[str, str] | None:
    """None tanto pra texto vazio/nulo (upload de antes deste campo existir)
    quanto pra JSON inválido — nunca levanta, sempre deixa o chamador decidir
    o fallback (ver integrations/gps.py::_reprocessar_cnpjs)."""
    if not texto:
        return None
    try:
        carregado = json.loads(texto)
    except (TypeError, ValueError):
        return None
    return carregado if isinstance(carregado, dict) else None


def sugerir_mapeamento(
    session: Session,
    fornecedor: OrigemFila,
    campos_obrigatorios: set[str],
    colunas_planilha: list[str],
    mapa_automatico: dict[str, str],
) -> dict[str, str]:
    ultimos = session.execute(
        select(UltimoMapeamentoColuna).where(UltimoMapeamentoColuna.fornecedor == fornecedor)
    ).scalars().all()
    ultimo_por_campo = {u.campo: u.nome_coluna for u in ultimos}

    sugestao: dict[str, str] = {}
    for campo in campos_obrigatorios:
        coluna_confirmada_antes = ultimo_por_campo.get(campo)
        if coluna_confirmada_antes is not None and coluna_confirmada_antes in colunas_planilha:
            sugestao[campo] = coluna_confirmada_antes
        elif campo in mapa_automatico:
            sugestao[campo] = mapa_automatico[campo]
    return sugestao


def confirmar_mapeamento(
    session: Session, fornecedor: OrigemFila, mapa: dict[str, str], confirmado_por: str,
) -> None:
    """Grava o mapeamento escolhido como o "último confirmado" daquele
    fornecedor, campo por campo — vira a sugestão de prioridade máxima na
    próxima planilha do mesmo fornecedor."""
    existentes = session.execute(
        select(UltimoMapeamentoColuna).where(UltimoMapeamentoColuna.fornecedor == fornecedor)
    ).scalars().all()
    existente_por_campo = {u.campo: u for u in existentes}

    for campo, coluna in mapa.items():
        registro = existente_por_campo.get(campo)
        if registro is None:
            session.add(UltimoMapeamentoColuna(
                fornecedor=fornecedor, campo=campo, nome_coluna=coluna, atualizado_por=confirmado_por,
            ))
        else:
            registro.nome_coluna = coluna
            registro.atualizado_por = confirmado_por
    session.flush()
