"""Normalização de texto para o motor de reconciliação de EAN.

O objetivo é aproximar descrições que se referem ao mesmo genérico mas vêm
escritas de formas diferentes entre Gruppy e GPS (acentuação, caixa,
abreviações de forma farmacêutica) ANTES de rodar o fuzzy-match — isso reduz
tanto falso-negativo (duas descrições do mesmo produto não batendo por causa
de "COMPR" vs "COMPRIMIDO") quanto o volume de itens que caem na fila manual.

Note que normalização aqui NUNCA decide o genérico sozinha — ela só prepara o
texto pro `reconciliation.motor` comparar. A decisão de aceite automático vs.
fila é sempre do motor, com base no score de similaridade.

Também moram aqui `normalizar_ean` e `normalizar_percentual`, que resolvem
problemas diferentes (não são de texto/fuzzy-match): `normalizar_ean` trata
planilhas exportadas do Excel que podem trazer a coluna de EAN inteira como
float quando alguma célula está vazia, corrompendo o código com um sufixo
".0" se convertido ingenuamente. `normalizar_percentual` trata planilhas que
trazem um percentual em escalas diferentes (fração 0-1 ou percentual 0-100)
— usada tanto pelo % de CMV do GPS quanto pelo % de desconto da Gruppy, com
o MESMO critério de detecção de escala nos dois lugares (ver docstring da
função).
"""
from __future__ import annotations

import math
import re
import unicodedata

from core.config import settings

# As abreviações em si (COMPR->COMPRIMIDO etc.) não ficam mais fixas aqui —
# vêm de `settings.reconciliacao.abreviacoes` (core/config.py), que já nasce
# com esse mesmo conjunto como default mas pode ser ajustado via
# secrets/env (RECON_ABREVIACOES) sem editar código, conforme aparecerem
# casos reais que o fuzzy-match sozinho não resolve bem.

_ESPACOS_MULTIPLOS = re.compile(r"\s+")
_CARACTERES_NAO_ALFANUM = re.compile(r"[^A-Z0-9 ]")


def _remover_acentos(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def normalizar_texto(texto: str) -> str:
    """Remove acento, maiúsculo, colapsa espaço e expande abreviações token a
    token via `settings.reconciliacao.abreviacoes`. Determinístico — mesmo
    texto de entrada sempre produz a mesma saída (importante pro atalho de
    EAN já resolvido, que depende de comparar o mesmo texto normalizado)."""
    if not texto:
        return ""

    sem_acento = _remover_acentos(str(texto)).upper()
    somente_alfanum = _CARACTERES_NAO_ALFANUM.sub(" ", sem_acento)
    colapsado = _ESPACOS_MULTIPLOS.sub(" ", somente_alfanum).strip()

    abreviacoes = settings.reconciliacao.abreviacoes
    tokens = [abreviacoes.get(tok, tok) for tok in colapsado.split(" ")]
    return " ".join(tokens)


_EAN_STRING_COM_SUFIXO_ZERO = re.compile(r"^\d+\.0+$")


def normalizar_ean(valor) -> str:
    """EAN pode chegar como int, string, ou float — pandas lê a coluna
    inteira como float quando alguma célula está vazia em outra linha, e aí
    um EAN limpo como 7896422507295 vira o valor float 7896422507295.0.
    Convertido ingenuamente com `str()`, isso vira o texto
    "7896422507295.0" (sufixo que corrompe o código). Sempre devolve só
    dígitos, nunca esse sufixo; nulo/NaN vira string vazia."""
    if valor is None:
        return ""
    if isinstance(valor, float):
        if math.isnan(valor):
            return ""
        valor = int(round(valor))

    texto = str(valor).strip()
    if texto.lower() == "nan":
        return ""
    # Defesa extra pro caso do valor já chegar como STRING nesse formato
    # (célula formatada como texto, não como float puro) — descarta a parte
    # fracionária antes de filtrar dígitos, senão o "0" depois do ponto
    # gruda no final e corrompe o código (vira "78964225072950").
    if _EAN_STRING_COM_SUFIXO_ZERO.match(texto):
        texto = texto.split(".")[0]

    return "".join(ch for ch in texto if ch.isdigit())


def normalizar_percentual(valor) -> float:
    """Aceita fração (0.65) ou percentual (65 ou "65%") e sempre devolve
    fração — usada tanto pro % de CMV do GPS (fórmula de custo real,
    Fat_liquido × pct_CMV) quanto pro % de desconto da Gruppy (custo =
    preco_bruto × (1 − desconto)); nos dois casos, deixar a escala errada
    vazar faz o resultado sair ~100x errado.

    Critério de detecção de escala: valor <= 1 já é fração, usado direto;
    valor > 1 é percentual, dividido por 100. Caso-limite em exatamente 1:
    tratado como fração 1.0 (100%), não como 1% — é a mesma convenção já
    validada pro GPS, replicada aqui sem alterar o critério."""
    if isinstance(valor, str):
        valor = valor.strip().replace("%", "").replace(",", ".")
    numero = float(valor)
    return numero / 100 if numero > 1 else numero
