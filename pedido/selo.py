"""Selo dos resultados prontos do cálculo de madrugada (29/10/2026, `docs/specs/2026-10-29-calculo-madrugada-design.md`).

O resultado de cada tela é guardado em pickle para sair IDÊNTICO ao calculado na hora (quadros do pandas, dicionários
e as regras como dataclass). Pickle não pode ser aberto sem garantia de origem — quem consegue gravar no arquivo
consegue rodar código no app —, e a chave do Spaces é a mesma de outros sistemas. O selo (HMAC-SHA256 com
`PRECALCULO_CHAVE`, um Secret que só a rotina e o app conhecem) vai na frente do conteúdo; o app confere ANTES de
abrir e, se não bater, ignora o arquivo e calcula na hora.
"""
from __future__ import annotations

import hashlib
import hmac

_TAMANHO = hashlib.sha256().digest_size   # 32 bytes


def selar(conteudo: bytes, chave: str | None) -> bytes:
    if not chave:
        raise ValueError("Sem PRECALCULO_CHAVE: o resultado pronto não pode ser selado.")
    return hmac.new(chave.encode("utf-8"), conteudo, hashlib.sha256).digest() + conteudo


def abrir(selado: bytes, chave: str | None) -> bytes | None:
    """O conteúdo, se o selo confere; senão None (arquivo alterado, outra chave ou sem chave)."""
    if not chave or len(selado) < _TAMANHO:
        return None
    selo, conteudo = selado[:_TAMANHO], selado[_TAMANHO:]
    esperado = hmac.new(chave.encode("utf-8"), conteudo, hashlib.sha256).digest()
    return conteudo if hmac.compare_digest(selo, esperado) else None
