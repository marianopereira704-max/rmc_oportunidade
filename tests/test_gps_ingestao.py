"""Ingestão do GPS na regra de 09/2026 (integrations/gps.py,
integrations/gps_processamento.py, integrations/planilha_navegador.py).

O que estes testes travam, na ordem:
1. Leitura: mapeamento automático certo (Quantidade, não QTD), rodapé do BI
   (mês e exportação cortada) e o payload vindo do navegador.
2. Regra de linha: só compra (VlrUnitario e Quantidade > 0) entra; custo é o
   VlrUnitario; repetição soma com média ponderada.
3. Substituição: o envio troca, no mês, só os CNPJs presentes no arquivo.
4. Tudo ou nada: falha no meio não deixa nada pela metade.
5. Filas recalculadas (não somadas) — reenviar não infla.
6. CNPJ órfão: guardado, vinculado, movido, reconhecido nos envios seguintes.
7. Custo em banco independe do número de linhas (nada de consulta por linha).
8. Trava: um processamento por vez.
"""
from __future__ import annotations

import base64
import gzip
from contextlib import contextmanager

import pandas as pd
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from core import rotinas
from core.models import (
    Base,
    BaseGenerico,
    CompraGPSOrfa,
    EanGenerico,
    FilaCnpjOrfao,
    FilaResolucaoEAN,
    FSNode,
    Loja,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusFila,
    UploadGPS,
)
from integrations import gps, gps_processamento
from integrations.planilha_navegador import PlanilhaRecebida, decodificar_payload, ler_rodape
from reconciliation import motor
from storage import filesystem as fs

CNPJ_A = "11.111.111/0001-11"
CNPJ_B = "22.222.222/0001-22"
CNPJ_ORFAO = "99.999.999/0001-99"


def _digitos(cnpj: str) -> str:
    return "".join(c for c in cnpj if c.isdigit())


# ---------------------------------------------------------------------------
# Fixtures: banco em arquivo (várias sessões enxergam o mesmo banco, como em
# produção) e uma fábrica de sessão com commit/rollback de verdade.
# ---------------------------------------------------------------------------

def _novo_banco(caminho):
    engine = create_engine(f"sqlite:///{caminho}", future=True)
    Base.metadata.create_all(engine)
    fabrica_raw = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def fabrica():
        sessao = fabrica_raw()
        try:
            yield sessao
            sessao.commit()
        except Exception:
            sessao.rollback()
            raise
        finally:
            sessao.close()

    with fabrica() as s:
        s.add_all([
            Loja(cnpj=CNPJ_A, razao_social="Farmácia A", uf="MG", cidade="BH"),
            Loja(cnpj=CNPJ_B, razao_social="Farmácia B", uf="MG", cidade="BH"),
        ])
        raiz = fs.garantir_raiz(s)
    return {"engine": engine, "fabrica": fabrica, "raiz_id": raiz.id}


@pytest.fixture()
def banco(tmp_path):
    return _novo_banco(tmp_path / "gps.db")


def _linha(cnpj, ean, qtd=None, vlr=None, **extra) -> dict:
    return {"cnpj": cnpj, "EAN": ean, "Produto": f"PRODUTO {ean}", "Laboratório": "EMS",
            "QTD": 999, "Quantidade": qtd, "VlrUnitario": vlr, **extra}


def _enviar(banco, linhas: list[dict], ano_mes="2026-08", nome="gps.xlsx", esperar_sucesso=True):
    df = pd.DataFrame(linhas)
    mapa = gps.mapear_colunas(df)
    planilha = PlanilhaRecebida(nome=nome, tamanho_bytes=10, df=df, storage_key=None, conteudo=b"original")
    iniciou, motivo = gps_processamento.iniciar(
        planilha, gps.preparar_compras(df, mapa), mapa, ano_mes, banco["raiz_id"], "admin",
        fabrica_sessao=banco["fabrica"], em_segundo_plano=False,
    )
    assert iniciou, motivo
    estado = gps_processamento.estado_atual()
    if esperar_sucesso:
        assert estado.sucesso, estado.erro
    return estado


def _compras(banco, ano_mes=None) -> dict[tuple[str, str, str], tuple[float, float]]:
    """(cnpj da loja, ean, ano_mes) -> (quantidade, custo)."""
    with banco["fabrica"]() as s:
        stmt = select(Loja.cnpj, RegistroCompraGPS.ean, RegistroCompraGPS.ano_mes,
                      RegistroCompraGPS.quantidade, RegistroCompraGPS.custo_unitario).join(Loja)
        if ano_mes:
            stmt = stmt.where(RegistroCompraGPS.ano_mes == ano_mes)
        return {(c, e, m): (float(q), float(v)) for c, e, m, q, v in s.execute(stmt)}


# ---------------------------------------------------------------------------
# 1. Leitura
# ---------------------------------------------------------------------------

def test_mapeamento_automatico_pega_quantidade_comprada_e_nao_qtd_vendida():
    """Na exportação real, `QTD` (vendida) vem ANTES de `Quantidade`
    (comprada). Pela ordem das colunas, a vendida ganhava."""
    df = pd.DataFrame([_linha(CNPJ_A, "789", 2, 10.0)])
    mapa = gps.detectar_colunas_automatico(df)
    assert mapa["quantidade"] == "Quantidade"
    assert mapa["custo_unitario"] == "VlrUnitario"
    assert mapa["laboratorio"] == "Laboratório"


def test_rodape_do_bi_traz_mes_e_aviso_de_corte():
    df = pd.DataFrame([_linha(CNPJ_A, "789", 2, 10.0)] + [
        {"cnpj": None, "EAN": None, "Produto": None, "Laboratório": None, "QTD": None, "Quantidade": None,
         "VlrUnitario": None, "Id": "Filtros aplicados:\nIncluídos (1) 2026 (Ano) + 2026/Ago. (AnoMesFiltro)"},
        {"cnpj": None, "EAN": None, "Produto": None, "Laboratório": None, "QTD": None, "Quantidade": None,
         "VlrUnitario": None, "Id": "Exported data exceeded the allowed volume. Some data may have been omitted."},
    ])
    rodape = ler_rodape(df)
    assert rodape.ano_mes == "2026-08"
    assert rodape.exportacao_cortada is True


def test_arquivo_sem_rodape_nao_inventa_mes_nem_corte():
    rodape = ler_rodape(pd.DataFrame([_linha(CNPJ_A, "789", 2, 10.0)]))
    assert rodape.ano_mes is None
    assert rodape.exportacao_cortada is False


def test_payload_do_navegador_vira_o_mesmo_dataframe():
    df = pd.DataFrame([_linha(CNPJ_A, 7896004796581, 2, 10.5), _linha(CNPJ_B, None, None, None)])
    csv = df.to_csv(index=False).encode()
    recebida = decodificar_payload({
        "nome": "ago.xlsx", "tamanho": 123, "csv_gz_b64": base64.b64encode(gzip.compress(csv)).decode(),
        "original_b64": base64.b64encode(b"bytes-originais").decode(), "enviado_storage": False,
        "storage_key": "nao-usada", "segundos_leitura": 1.5,
    })
    assert recebida.nome == "ago.xlsx"
    assert recebida.conteudo == b"bytes-originais"
    assert recebida.storage_key is None  # não foi enviado direto ao storage
    assert list(recebida.df.columns) == list(df.columns)
    preparadas = gps.preparar_compras(recebida.df, gps.mapear_colunas(recebida.df))
    assert preparadas.compras["ean"].tolist() == ["7896004796581"]  # sem sufixo ".0"


# ---------------------------------------------------------------------------
# 2. Regra de linha
# ---------------------------------------------------------------------------

def test_so_linha_de_compra_entra_e_custo_e_o_vlrunitario():
    df = pd.DataFrame([
        _linha(CNPJ_A, "1", 2, 89.8),          # compra
        _linha(CNPJ_A, "2", None, None),       # só venda
        _linha(CNPJ_A, "3", 0, 10.0),          # quantidade zero
        _linha(CNPJ_A, "4", 5, -1.0),          # valor negativo
        _linha(None, "5", 1, 1.0),             # sem CNPJ (rodapé/total)
    ])
    p = gps.preparar_compras(df, gps.mapear_colunas(df))
    assert p.compras[["ean", "quantidade", "custo_unitario"]].values.tolist() == [["1", 2.0, 89.8]]
    assert (p.linhas_sem_compra, p.linhas_invalidas, p.linhas_sem_chave) == (1, 2, 1)
    assert p.cnpjs_no_arquivo == {_digitos(CNPJ_A)}


def test_mesma_loja_e_ean_repetidos_somam_com_media_ponderada():
    df = pd.DataFrame([_linha(CNPJ_A, "1", 2, 10.0), _linha(CNPJ_A, "1", 8, 20.0)])
    p = gps.preparar_compras(df, gps.mapear_colunas(df))
    assert len(p.compras) == 1
    assert p.compras.loc[0, "quantidade"] == 10
    assert p.compras.loc[0, "custo_unitario"] == pytest.approx((2 * 10 + 8 * 20) / 10)
    assert p.linhas_somadas == 1


def test_mapeamento_incompleto_e_recusado():
    df = pd.DataFrame([_linha(CNPJ_A, "1", 2, 10.0)])
    with pytest.raises(ValueError, match="custo_unitario"):
        gps.preparar_compras(df, {"cnpj": "cnpj", "ean": "EAN", "descricao": "Produto", "quantidade": "Quantidade"})


# ---------------------------------------------------------------------------
# 3. Substituição por (mês, CNPJ)
# ---------------------------------------------------------------------------

def test_reenvio_substitui_so_os_cnpjs_do_arquivo_no_mes(banco):
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0), _linha(CNPJ_A, "2", 1, 10.0), _linha(CNPJ_B, "1", 1, 10.0)])
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0)], ano_mes="2026-07")  # outro mês

    # Novo envio de agosto só com a loja A, e sem o EAN 2: a loja A passa a
    # ter só o EAN 1 (com o valor novo); a loja B, ausente do arquivo, fica
    # intacta; julho não é tocado.
    _enviar(banco, [_linha(CNPJ_A, "1", 3, 12.0)], nome="gps_dia20.xlsx")

    assert _compras(banco, "2026-08") == {
        (CNPJ_A, "1", "2026-08"): (3.0, 12.0),
        (CNPJ_B, "1", "2026-08"): (1.0, 10.0),
    }
    assert _compras(banco, "2026-07") == {(CNPJ_A, "1", "2026-07"): (1.0, 10.0)}


def test_cnpj_presente_so_com_linhas_de_venda_fica_sem_compra_no_mes(banco):
    """O arquivo é a verdade dos CNPJs que traz: se a loja veio, mas sem
    nenhuma compra, ela não tem compra naquele mês."""
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0)])
    # No envio novo, a loja A veio só com linha de venda; a B, com compra.
    _enviar(banco, [_linha(CNPJ_A, "1", None, None), _linha(CNPJ_B, "1", 2, 11.0)])
    assert _compras(banco, "2026-08") == {(CNPJ_B, "1", "2026-08"): (2.0, 11.0)}


def test_divisao_por_uf_em_varios_arquivos_nao_apaga_o_anterior(banco):
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0)], nome="mg.xlsx")
    _enviar(banco, [_linha(CNPJ_B, "1", 2, 11.0)], nome="es.xlsx")
    assert set(_compras(banco, "2026-08")) == {(CNPJ_A, "1", "2026-08"), (CNPJ_B, "1", "2026-08")}


# ---------------------------------------------------------------------------
# 4. Tudo ou nada
# ---------------------------------------------------------------------------

def test_falha_no_meio_nao_muda_nada_e_libera_a_trava(banco, monkeypatch):
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0)])
    antes = _compras(banco)
    with banco["fabrica"]() as s:
        uploads_antes = s.execute(select(func.count()).select_from(UploadGPS)).scalar_one()
        nodes_antes = s.execute(select(func.count()).select_from(FSNode)).scalar_one()

    def explode(_session):
        raise RuntimeError("queda no meio do recálculo")

    # Falha DEPOIS do DELETE e do INSERT: prova que eles voltam atrás.
    monkeypatch.setattr(motor, "recalcular_valores_fila_ean", explode)
    estado = _enviar(banco, [_linha(CNPJ_A, "9", 5, 50.0)], esperar_sucesso=False)

    assert estado.concluido and not estado.sucesso
    assert "queda no meio" in estado.erro
    assert _compras(banco) == antes
    with banco["fabrica"]() as s:
        assert s.execute(select(func.count()).select_from(UploadGPS)).scalar_one() == uploads_antes
        assert s.execute(select(func.count()).select_from(FSNode)).scalar_one() == nodes_antes
        controle = rotinas.obter(s, rotinas.ROTINA_UPLOAD_GPS)
        assert controle.ultima_tentativa_em is None  # trava liberada
        assert "queda no meio" in controle.ultimo_erro


# ---------------------------------------------------------------------------
# 5. Filas recalculadas, não somadas
# ---------------------------------------------------------------------------

def test_reenviar_o_mesmo_mes_nao_infla_a_fila_de_ean(banco):
    linhas = [_linha(CNPJ_A, "7891", 2, 10.0), _linha(CNPJ_B, "7891", 3, 10.0)]
    for _ in range(3):
        _enviar(banco, linhas)
    with banco["fabrica"]() as s:
        item = s.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "7891")).scalar_one()
    assert float(item.valor_total_acumulado) == pytest.approx(50.0)  # (2 + 3) × 10, uma vez só
    assert item.qtd_ocorrencias == 2


def test_ean_ja_resolvido_nao_vai_pra_fila(banco):
    with banco["fabrica"]() as s:
        generico = BaseGenerico(nome_canonico="DIPIRONA 500MG")
        s.add(generico)
        s.flush()
        s.add(EanGenerico(ean="7891", base_generico_id=generico.id, origem_resolucao=OrigemResolucao.IMPORTADA,
                          descricao_origem_snapshot="x", resolvido_por="admin"))
    _enviar(banco, [_linha(CNPJ_A, "7891", 2, 10.0)])
    with banco["fabrica"]() as s:
        assert s.execute(select(func.count()).select_from(FilaResolucaoEAN)).scalar_one() == 0


# ---------------------------------------------------------------------------
# 6. CNPJ órfão
# ---------------------------------------------------------------------------

def test_cnpj_orfao_fica_guardado_e_a_fila_nao_infla_no_reenvio(banco):
    linhas = [_linha(CNPJ_ORFAO, "1", 2, 10.0, razao="Farmácia Nova"), _linha(CNPJ_ORFAO, "2", 1, 5.0)]
    for _ in range(2):
        _enviar(banco, linhas)
    with banco["fabrica"]() as s:
        assert s.execute(select(func.count()).select_from(CompraGPSOrfa)).scalar_one() == 2
        fila = s.execute(select(FilaCnpjOrfao)).scalar_one()
    assert fila.cnpj == _digitos(CNPJ_ORFAO)
    assert float(fila.valor_total_acumulado) == pytest.approx(25.0)
    assert fila.qtd_ocorrencias == 2
    assert fila.status == StatusFila.PENDENTE


def test_vincular_orfao_move_as_compras_e_vale_para_os_proximos_envios(banco):
    _enviar(banco, [_linha(CNPJ_ORFAO, "1", 2, 10.0)])
    with banco["fabrica"]() as s:
        fila = s.execute(select(FilaCnpjOrfao)).scalar_one()
        loja_a = s.execute(select(Loja).where(Loja.cnpj == CNPJ_A)).scalar_one()
        movidas = gps.resolver_cnpj_orfao(s, fila.id, loja_a.id, "admin")
    assert movidas == 1
    assert _compras(banco) == {(CNPJ_A, "1", "2026-08"): (2.0, 10.0)}

    # Próximo envio: o CNPJ antes órfão já cai direto na loja vinculada, e
    # substitui o que ela tinha naquele mês.
    _enviar(banco, [_linha(CNPJ_ORFAO, "1", 7, 11.0)])
    assert _compras(banco) == {(CNPJ_A, "1", "2026-08"): (7.0, 11.0)}
    with banco["fabrica"]() as s:
        assert s.execute(select(func.count()).select_from(CompraGPSOrfa)).scalar_one() == 0


def test_resolucao_em_lote_quando_a_loja_passa_a_existir(banco):
    _enviar(banco, [_linha(CNPJ_ORFAO, "1", 2, 10.0)])
    with banco["fabrica"]() as s:
        s.add(Loja(cnpj=CNPJ_ORFAO, razao_social="Nova", uf="MG", cidade="BH"))
    with banco["fabrica"]() as s:
        resultado = gps.resolver_cnpjs_orfaos_identicos_em_lote(s, "admin")
    assert resultado["resolvidos"] == 1 and resultado["linhas_inseridas"] == 1
    assert _compras(banco) == {(CNPJ_ORFAO, "1", "2026-08"): (2.0, 10.0)}


def test_excluir_envio_remove_compras_dele_e_recalcula_filas(banco):
    _enviar(banco, [_linha(CNPJ_A, "7891", 2, 10.0), _linha(CNPJ_ORFAO, "1", 1, 1.0)])
    with banco["fabrica"]() as s:
        node_id = s.execute(select(UploadGPS.fs_node_id)).scalar_one()
        gps.excluir_upload_gps_definitivamente(s, node_id)
    assert _compras(banco) == {}
    with banco["fabrica"]() as s:
        assert s.execute(select(func.count()).select_from(CompraGPSOrfa)).scalar_one() == 0
        assert float(s.execute(select(FilaCnpjOrfao.valor_total_acumulado)).scalar_one()) == 0
        valores_fila = s.execute(select(FilaResolucaoEAN.valor_total_acumulado)).scalars().all()
        assert valores_fila and all(float(v) == 0 for v in valores_fila)


# ---------------------------------------------------------------------------
# 7. Consultas não escalam com o número de linhas
# ---------------------------------------------------------------------------

def _contar_instrucoes(banco, linhas) -> int:
    contador = {"n": 0}

    def _conta(*_args, **_kwargs):
        contador["n"] += 1

    event.listen(banco["engine"], "before_cursor_execute", _conta)
    try:
        _enviar(banco, linhas)
    finally:
        event.remove(banco["engine"], "before_cursor_execute", _conta)
    return contador["n"]


def test_numero_de_instrucoes_nao_cresce_com_as_linhas(tmp_path):
    """O problema original: 2 idas ao banco por linha de CNPJ órfão e 1 por
    EAN pendente — com 138 ms de rede até o Postgres, minutos de espera. Aqui
    40 e 400 linhas (com órfãos e EANs novos) precisam custar o mesmo número
    de instruções, salvo os blocos do INSERT."""
    def planilha(n):
        return [_linha(CNPJ_A if i % 3 else CNPJ_ORFAO, str(1000 + i % 20), 1, 10.0 + i) for i in range(n)]

    # Bancos separados e novos: os dois envios encontram o mesmo ponto de
    # partida (mesmas filas vazias), só o tamanho do arquivo muda.
    poucas = _contar_instrucoes(_novo_banco(tmp_path / "poucas.db"), planilha(40))
    muitas = _contar_instrucoes(_novo_banco(tmp_path / "muitas.db"), planilha(400))
    assert muitas == poucas


# ---------------------------------------------------------------------------
# 8. Trava
# ---------------------------------------------------------------------------

def test_segundo_processamento_simultaneo_e_recusado(banco):
    with banco["fabrica"]() as s:
        assert rotinas.reivindicar_trava(s, rotinas.ROTINA_UPLOAD_GPS)
    df = pd.DataFrame([_linha(CNPJ_A, "1", 1, 10.0)])
    mapa = gps.mapear_colunas(df)
    iniciou, motivo = gps_processamento.iniciar(
        PlanilhaRecebida("x.xlsx", 1, df, None, b"x"), gps.preparar_compras(df, mapa), mapa, "2026-08",
        banco["raiz_id"], "admin", fabrica_sessao=banco["fabrica"], em_segundo_plano=False,
    )
    assert not iniciou
    assert "em andamento" in motivo

    with banco["fabrica"]() as s:
        rotinas.liberar_trava(s, rotinas.ROTINA_UPLOAD_GPS)
    _enviar(banco, [_linha(CNPJ_A, "1", 1, 10.0)])  # liberada, volta a aceitar


def test_campos_de_recuo_intercalados_nao_quebram_o_lote(tmp_path):
    """Regressão de 24/09/2026: linhas com e sem Fat/%CMV/QTD/Custo médio
    intercaladas faziam o bulk insert do ORM gravar uma linha por instrução
    (~80 min pra ~36 mil compras contra o Postgres em NY). O número de
    instruções tem que ser o mesmo pra 40 e pra 400 linhas."""
    def planilha(n):
        linhas = []
        for i in range(n):
            linha = _linha(CNPJ_A, str(1000 + i), 1, 10.0)
            if i % 2:
                linha.update({"Fat. líquido": 100.0, "% CMV": 0.5, "R$ Custo médio": 9.5})
            else:
                linha.update({"Fat. líquido": None, "% CMV": None, "R$ Custo médio": None})
            linhas.append(linha)
        return linhas

    poucas = _contar_instrucoes(_novo_banco(tmp_path / "poucas.db"), planilha(40))
    muitas = _contar_instrucoes(_novo_banco(tmp_path / "muitas.db"), planilha(400))
    assert muitas == poucas
