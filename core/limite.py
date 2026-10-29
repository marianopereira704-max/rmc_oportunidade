"""No máximo N cálculos pesados ao mesmo tempo no processo do app (28/10/2026, Fase 7 do plano de publicação).

Medido em 28/10/2026 com dado real (docs/specs/2026-10-28-plano-publicacao-e-melhorias.md): montar o Assistente ou
uma página do Dashboard de uma loja grande tem pico de 50–100 MB e leva alguns segundos; o que fica guardado no
cache depois é pequeno (2–7 MB). Dez lojas abrindo frio ao mesmo tempo de manhã somariam 0,6–1 GB só no montar,
contra um teto de ~2,7 GB no Community Cloud, que derruba o app inteiro ("This app has gone over its resource
limits") — para todo mundo, não só para quem abriu. Com a trava, quem chega depois espera com o aviso de
"carregando" e o app não cai. Num só núcleo, rodar dez ao mesmo tempo também não termina antes do que em fila.

Uso, ABAIXO do `st.cache_data` (só o cálculo de verdade espera; quem acha no cache passa direto):

    @st.cache_data(...)
    @limite.pesado
    def _calcular(...): ...

Um cálculo pesado que chama outro (a Ruptura chama o cálculo do Assistente) conta uma vez só: a vaga é da thread
(cada sessão do Streamlit roda na sua), senão duas Rupturas segurando as duas vagas esperariam para sempre pelo
Assistente lá dentro.
"""
from __future__ import annotations

import functools
import threading
from contextlib import contextmanager

from core.config import settings

_vagas = threading.BoundedSemaphore(max(1, settings.streamlit_cloud.calculos_simultaneos))
_local = threading.local()


@contextmanager
def calculo_pesado():
    if getattr(_local, "dentro", 0):
        _local.dentro += 1
        try:
            yield
        finally:
            _local.dentro -= 1
        return
    with _vagas:
        _local.dentro = 1
        try:
            yield
        finally:
            _local.dentro = 0


def pesado(funcao):
    @functools.wraps(funcao)
    def envolta(*args, **kwargs):
        with calculo_pesado():
            return funcao(*args, **kwargs)
    return envolta
