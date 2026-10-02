"""Vínculo de lojas GPS do lado do app (integrations/gps_vinculo.py)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, Loja, SituacaoVinculoGps, VinculoLojaGps
from integrations import gps_vinculo
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([
            Loja(cnpj="04076088000186", razao_social="HUDSON VENZEL PEGO", nome_fantasia="FARMACIA CENTRAL",
                 uf="MG", cidade="SANTANA DO MANHUACU"),
            Loja(cnpj="10296546000195", razao_social="MEGA FARMA PRODUTOS FARMACEUTICOS LTDA", nome_fantasia="MEGA FARMA",
                 uf="RN", cidade="GOIANINHA", endereco_numero="37", bairro="CENTRO"),
            Loja(cnpj="11111111000111", razao_social="X", nome_fantasia="DROGARIA BELA VISTA",
                 uf="MG", cidade="Coronel Fabriciano", endereco_numero="265"),
        ])
        s.flush()
        yield s


LOJAS_GPS = [
    {"id_empresa": "H", "codigo_loja": "04076088000186", "nome_loja": "FARMACIA CENTRAL", "cnpj": "04076088000186", "numero": None, "uf": "MG"},
    {"id_empresa": "M", "codigo_loja": "1", "nome_loja": "MEGA FARMA 1 CENTRO GOIANINHA", "cnpj": None, "numero": "37", "uf": "RN"},
    {"id_empresa": "O", "codigo_loja": "1", "nome_loja": "OTIMAFARMA CORONEL FABRICIANO", "cnpj": None, "numero": "265 LJ 0", "uf": "MG"},
    {"id_empresa": "Z", "codigo_loja": "9", "nome_loja": "FARMACIAS ECONOMICA", "cnpj": None, "numero": "1417", "uf": "RS"},
]


def _vinculo(s, emp):
    return s.query(VinculoLojaGps).filter_by(id_empresa_gps=emp).one()


def test_recalcular_grava_as_sugestoes_com_a_loja_certa(session):
    r = gps_vinculo.recalcular(session, LOJAS_GPS)
    assert (r.automaticos, r.a_confirmar, r.sem_candidato) == (2, 1, 1)
    hudson = session.query(Loja).filter_by(cnpj="04076088000186").one()
    assert _vinculo(session, "H").loja_id == hudson.id
    assert gps_vinculo.loja_gps_da_loja(session, hudson.id) == ("H", "04076088000186")


def test_a_confirmar_nao_vale_para_a_tela_pedido_ate_alguem_confirmar(session):
    gps_vinculo.recalcular(session, LOJAS_GPS)
    otima = _vinculo(session, "O")
    bela_vista = session.query(Loja).filter_by(cnpj="11111111000111").one()
    assert otima.situacao == SituacaoVinculoGps.CONFIRMAR
    assert gps_vinculo.loja_gps_da_loja(session, bela_vista.id) is None
    gps_vinculo.confirmar(session, otima.id, bela_vista.id, "admin")
    assert gps_vinculo.loja_gps_da_loja(session, bela_vista.id) == ("O", "1")


def test_recalculo_nunca_desfaz_decisao_humana(session):
    gps_vinculo.recalcular(session, LOJAS_GPS)
    gps_vinculo.marcar_nao_cliente(session, _vinculo(session, "O").id, "admin")
    r = gps_vinculo.recalcular(session, LOJAS_GPS)
    assert r.decisoes_preservadas == 1
    assert _vinculo(session, "O").situacao == SituacaoVinculoGps.NAO_CLIENTE


def test_le_as_lojas_que_a_rotina_gravou(tmp_path):
    armaz, ch = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    armaz.salvar_json(ch.lojas_gps("E1"), [{"CodigoLoja": "1", "NomeLoja": "L1", "CNPJ": None, "Numero": "37",
                                            "Estado": "RN            ", "Cidade": "GOIANINHA"}])
    lojas = gps_vinculo.lojas_gps_salvas(armaz, ch)
    assert lojas == [{"id_empresa": "E1", "codigo_loja": "1", "nome_loja": "L1", "cnpj": None, "numero": "37",
                      "uf": "RN", "cidade": "GOIANINHA"}]
