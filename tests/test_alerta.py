"""Alerta por e-mail da rotina do Pedido (pedido/alerta.py, 28/10/2026)."""
import datetime as dt

from pedido import alerta
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves

CH = Chaves("pedido")


def _relatorio(armaz, dia, parte, hora="030000", **campos):
    base = {"parte": parte, "lojas": 50, "historicos_gravados": 50, "unidades_ok": 100, "unidades_com_falha": 0,
            "empresas_sem_lista_de_lojas": 0, "parou_por_tempo": False, "fase": "manutencao", "erro_geral": None}
    armaz.salvar_json(CH.execucao(dt.date.fromisoformat(dia), parte, hora), {**base, **campos})


def _rodar(tmp_path, hoje):
    enviados = []
    codigo = alerta.main(["--destino", f"local:{tmp_path}", "--partes", "2", "--hoje", hoje],
                         enviar_email=lambda dest, assunto, texto: enviados.append((assunto, texto)))
    return codigo, enviados


def test_noite_normal_nao_manda_email_e_guarda_o_resumo(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    for p in ("parte1de2", "parte2de2"):
        _relatorio(armaz, "2026-10-29", p)
    assert _rodar(tmp_path, "2026-10-29") == (0, [])
    assert armaz.ler_json("pedido/controle/alerta/2026-10-29.json")["lojas_com_foto"] == 100


def test_noite_presa_na_carga_sem_foto_de_estoque_manda_email(tmp_path):
    """25 a 28/10/2026: partes 'verdes', presas na carga, nenhuma loja com foto."""
    armaz = ArmazenamentoLocal(tmp_path)
    for p in ("parte1de2", "parte2de2"):
        _relatorio(armaz, "2026-10-28", p, fase="carga", lojas=0, historicos_gravados=0, unidades_ok=0)
    codigo, enviados = _rodar(tmp_path, "2026-10-28")
    assert codigo == 0 and len(enviados) == 1
    assert "Nenhuma loja ganhou foto de estoque" in enviados[0][1] and "carga" in enviados[0][1]


def test_parte_sem_relatorio_erro_e_queda_de_lojas(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    armaz.salvar_json("pedido/controle/alerta/2026-10-28.json", {"data": "2026-10-28", "lojas_com_foto": 400})
    _relatorio(armaz, "2026-10-29", "parte1de2", historicos_gravados=300, erro_geral="RuntimeError: 502")
    lista = alerta.problemas(alerta.resumo(armaz, CH, dt.date(2026, 10, 29), 2),
                             alerta.anterior(armaz, CH, dt.date(2026, 10, 29)))
    assert len(lista) == 3
    assert "parte2de2" in lista[0] and "parte1de2" in lista[1] and "de 400" in lista[2] and "para 300" in lista[2]


def test_queda_pequena_nao_e_problema_e_vale_o_ultimo_relatorio_da_parte(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    armaz.salvar_json("pedido/controle/alerta/2026-10-28.json", {"data": "2026-10-28", "lojas_com_foto": 100})
    # Carga primeiro, manutenção depois (pedido/__main__.py): o da manutenção é o que vale.
    _relatorio(armaz, "2026-10-29", "parte1de2", hora="003000", fase="carga", historicos_gravados=0)
    _relatorio(armaz, "2026-10-29", "parte1de2", hora="040000", historicos_gravados=48)
    _relatorio(armaz, "2026-10-29", "parte2de2", historicos_gravados=49)
    assert _rodar(tmp_path, "2026-10-29") == (0, [])


def test_disparo_que_vira_o_dia_usa_os_relatorios_da_vespera(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    for p in ("parte1de2", "parte2de2"):
        _relatorio(armaz, "2026-10-28", p)
    assert _rodar(tmp_path, "2026-10-29") == (0, [])


def test_rotina_que_nao_roda_ha_dias_manda_email(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    _relatorio(armaz, "2026-10-23", "parte1de2")
    codigo, enviados = _rodar(tmp_path, "2026-10-28")
    assert len(enviados) == 1 and "desde 23/10/2026" in enviados[0][1]


def test_falha_no_envio_falha_o_job(tmp_path):
    def quebra(*_):
        raise OSError("smtp fora")
    armaz = ArmazenamentoLocal(tmp_path)
    _relatorio(armaz, "2026-10-28", "parte1de2", historicos_gravados=0)
    assert alerta.main(["--destino", f"local:{tmp_path}", "--partes", "1", "--hoje", "2026-10-28"],
                       enviar_email=quebra) == 1
