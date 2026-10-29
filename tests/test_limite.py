"""Trava de cálculos pesados (core/limite.py) e cópia das bases no Spaces (core/copia_spaces.py), 28/10/2026."""
import threading
import time

import pandas as pd

from core import copia_spaces, limite
from pedido.armazenamento import ArmazenamentoLocal


def test_nunca_mais_que_as_vagas_ao_mesmo_tempo(monkeypatch):
    monkeypatch.setattr(limite, "_vagas", threading.BoundedSemaphore(2))
    agora, maximo, trava = [0], [0], threading.Lock()

    @limite.pesado
    def calculo():
        with trava:
            agora[0] += 1
            maximo[0] = max(maximo[0], agora[0])
        time.sleep(0.05)
        with trava:
            agora[0] -= 1

    threads = [threading.Thread(target=calculo) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert maximo[0] == 2 and agora[0] == 0


def test_calculo_dentro_de_outro_nao_trava(monkeypatch):
    """A Ruptura chama o cálculo do Assistente: com uma vaga só, contar as duas esperaria para sempre."""
    monkeypatch.setattr(limite, "_vagas", threading.BoundedSemaphore(1))

    @limite.pesado
    def assistente():
        return 1

    @limite.pesado
    def ruptura():
        return assistente() + 1

    resultado = []
    t = threading.Thread(target=lambda: resultado.append(ruptura()))
    t.start()
    t.join(2)
    assert resultado == [2]
    assert limite._vagas.acquire(blocking=False)      # a vaga foi devolvida


def test_erro_no_calculo_devolve_a_vaga(monkeypatch):
    monkeypatch.setattr(limite, "_vagas", threading.BoundedSemaphore(1))

    @limite.pesado
    def quebra():
        raise ValueError

    for _ in range(3):
        try:
            quebra()
        except ValueError:
            pass
    assert limite._vagas.acquire(blocking=False)


def test_copia_lida_refeita_na_versao_nova_e_limpa(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    chamadas = []

    def montar(n):
        def f():
            chamadas.append(n)
            return pd.DataFrame({"ean": ["1", "2"], "categoria": [n, None]})
        return f

    a = copia_spaces.ler_ou_montar("base", ("v", 1), montar("v1"), armaz)
    b = copia_spaces.ler_ou_montar("base", ("v", 1), montar("v1"), armaz)    # do arquivo, sem o banco
    assert chamadas == ["v1"] and b.equals(a)
    copia_spaces.ler_ou_montar("base", ("v", 2), montar("v2"), armaz)
    copia_spaces.ler_ou_montar("base", ("v", 3), montar("v3"), armaz)
    pasta = copia_spaces._pasta("base")
    assert len([c for c in armaz.listar(pasta) if c.endswith(".parquet")]) == copia_spaces.MANTER
    assert chamadas == ["v1", "v2", "v3"]
    copia_spaces.ler_ou_montar("base", ("v", 1), montar("v1"), armaz)     # a v1 foi apagada: monta de novo
    assert chamadas[-1] == "v1"


def test_sem_spaces_le_do_banco():
    class Quebrado:
        def existe(self, chave):
            raise ConnectionError("Spaces fora")

    df = copia_spaces.ler_ou_montar("base", 1, lambda: pd.DataFrame({"x": [1]}), Quebrado())
    assert df["x"].tolist() == [1]
