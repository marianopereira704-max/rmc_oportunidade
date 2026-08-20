"""Hash de senha via bcrypt.

Substitui o SHA-256+sal-fixo do projeto anterior (insuficiente além de um
cenário de 2 usuários fixos de teste). bcrypt já embute um salt aleatório por
hash e um custo computacional ajustável, adequado mesmo que o sistema evolua
para login por loja/consultor no futuro.
"""
from __future__ import annotations

import bcrypt


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("utf-8"))
    except (ValueError, TypeError):
        # hash malformado/vazio — nunca autentica, mas também não derruba a tela
        return False
