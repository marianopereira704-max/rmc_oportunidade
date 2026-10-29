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


def _backups(armaz, dia, bancos=alerta.BANCOS_BACKUP):
    for b in bancos:
        armaz.salvar(f"{alerta.PASTA_BACKUP}{dia}/{b}.sql.gz", b"x")


def _rodar(tmp_path, hoje, com_backup=True):
    if com_backup:
        _backups(ArmazenamentoLocal(tmp_path), hoje)
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


def test_sem_backup_do_dia_manda_email(tmp_path):
    """O backup roda no cron do droplet, fora do GitHub: cron parado só seria notado no dia de precisar."""
    armaz = ArmazenamentoLocal(tmp_path)
    for p in ("parte1de2", "parte2de2"):
        _relatorio(armaz, "2026-10-29", p)
    _backups(armaz, "2026-10-20")
    _backups(armaz, "2026-10-29", bancos=("rmc_oportunidades",))
    codigo, enviados = _rodar(tmp_path, "2026-10-29", com_backup=False)
    assert len(enviados) == 1
    assert "rmc_oportunidades_dev" in enviados[0][1] and "29/10/2026" in enviados[0][1]


def test_backup_de_ontem_basta(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    assert alerta.problemas_backup(armaz, dt.date(2026, 10, 29))          # nenhum backup: problema
    _backups(armaz, "2026-10-28")
    assert alerta.problemas_backup(armaz, dt.date(2026, 10, 29)) == []


# ---------------------------------------------------------------------------
# Cálculo de madrugada (pedido/precalculo.py, 29/10/2026)
# ---------------------------------------------------------------------------

def _precalculo(armaz, dia, partes, banco="rmc", lojas=10, falhas=0):
    armaz.salvar_json(f"pedido/insumos/{banco}/manifesto.json", {})
    for p in partes:
        armaz.salvar_json(f"pedido/controle/precalculo/{dia}/{banco}/{p}.json",
                          {"parte": p, "inicio": f"{dia}T03:10:00-03:00", "lojas_na_parte": lojas,
                           "lojas_prontas": lojas - falhas, "lojas_com_falha": falhas})


def test_precalculo_em_dia_nao_e_problema(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    _precalculo(armaz, "2026-10-29", ("parte1de2", "parte2de2"), falhas=1)       # 2 de 20: 10%, no limite
    assert alerta.problemas_precalculo(armaz, CH, dt.date(2026, 10, 29), 2) == []


def test_precalculo_parte_sem_relatorio_falhas_e_dias_sem_rodar(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    assert "nenhum banco" in alerta.problemas_precalculo(armaz, CH, dt.date(2026, 10, 29), 2)[0]
    _precalculo(armaz, "2026-10-29", ("parte1de2",), falhas=2)
    lista = alerta.problemas_precalculo(armaz, CH, dt.date(2026, 10, 29), 2)
    assert len(lista) == 2 and "parte2de2" in lista[0] and "PRECALCULO_CHAVE" in lista[0] and "2 de 10" in lista[1]
    assert "desde 29/10/2026" in alerta.problemas_precalculo(armaz, CH, dt.date(2026, 11, 2), 2)[0]


def test_precalculo_so_entra_no_alerta_com_a_opcao(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    for p in ("parte1de2", "parte2de2"):
        _relatorio(armaz, "2026-10-29", p)
    _precalculo(armaz, "2026-10-29", ("parte1de2",))
    assert _rodar(tmp_path, "2026-10-29") == (0, [])                       # job do pré-cálculo não rodou
    enviados = []
    alerta.main(["--destino", f"local:{tmp_path}", "--partes", "2", "--hoje", "2026-10-29", "--precalculo"],
                enviar_email=lambda d, a, t: enviados.append(t))
    assert len(enviados) == 1 and "parte2de2" in enviados[0]


def test_so_precalculo_nao_confere_a_coleta_nem_regrava_o_resumo(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    _relatorio(armaz, "2026-10-29", "parte1de2", historicos_gravados=0)    # coleta com problema: já avisada
    _precalculo(armaz, "2026-10-29", ("parte1de2", "parte2de2"))
    enviados = []
    codigo = alerta.main(["--destino", f"local:{tmp_path}", "--partes", "2", "--hoje", "2026-10-29",
                          "--so-precalculo"], enviar_email=lambda d, a, t: enviados.append(t))
    assert (codigo, enviados) == (0, [])
    assert not armaz.existe("pedido/controle/alerta/2026-10-29.json")
