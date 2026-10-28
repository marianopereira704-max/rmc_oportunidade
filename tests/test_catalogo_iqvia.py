"""Carga do catálogo IQVIA no banco (integrations/catalogo.py, 07/10/2026)."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, CatalogoIqvia, CategoriaEan, OrigemCategoria
from integrations import catalogo
from pedido import categorias as cat


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        quando = dt.datetime(2026, 10, 1)
        s.add_all([
            CategoriaEan(ean="111", categoria="PRESCRIÇÃO PROPAGADO", origem=OrigemCategoria.CMED, atualizado_em=quando),
            CategoriaEan(ean="222", categoria="HIGIENE", origem=OrigemCategoria.MANUAL, atualizado_em=quando),
        ])
        s.flush()
        yield s


def _catalogo(**mudar):
    df = pd.DataFrame({"ean": ["111", "222", "333", "444"],
                       "setor_nec": ["RX_GENERICO", "RX_GENERICO", "RX_GENERICO", "NAO_MEDICAMENTO_PEC"],
                       "gmrs": ["G", "G", "G", None]})
    return df.assign(**mudar)


def test_carga_grava_setores_e_so_completa_genericos(session):
    r = catalogo.carregar(session, _catalogo(), "Admin")
    assert r.eans == 4 and r.genericos_novos == 1           # só o 333: o 111 (CMED) e o 222 (manual) já tinham categoria
    assert session.get(CategoriaEan, "333").categoria == cat.GENERICO
    assert session.get(CategoriaEan, "333").origem == OrigemCategoria.IQVIA
    assert session.get(CategoriaEan, "111").categoria == "PRESCRIÇÃO PROPAGADO"     # complementa, não substitui
    assert session.get(CategoriaEan, "222").origem == OrigemCategoria.MANUAL
    assert catalogo.categorias_iqvia(session) == 1
    assert catalogo.setores(session).to_dict() == {"111": "RX_GENERICO", "222": "RX_GENERICO", "333": "RX_GENERICO",
                                                  "444": "NAO_MEDICAMENTO_PEC"}

    # Carregar de novo: atualiza o setor, não duplica nada.
    r2 = catalogo.carregar(session, _catalogo(setor_nec=["RX_TRADE"] * 4), "Admin")
    assert r2.genericos_novos == 0 and session.query(CatalogoIqvia).count() == 4
    assert catalogo.contagem_por_setor(session) == {"RX_TRADE": 4}
    assert catalogo.versao(session)[0] == 4
