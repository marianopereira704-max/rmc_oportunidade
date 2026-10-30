"""Lista de destinatários dos e-mails para as lojas (integrations/destinatarios.py, 29/10/2026)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, Loja, Papel, SituacaoVinculoGps, Usuario, UsuarioLoja, VinculoLojaGps
from integrations import destinatarios
from pedido.armazenamento import ArmazenamentoLocal


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        a = Loja(cnpj="10", razao_social="REIS F1", uf="ES", cidade="X", legacy_id=10)
        b = Loja(cnpj="11", razao_social="REIS F2", uf="ES", cidade="X", legacy_id=11)
        fora = Loja(cnpj="12", razao_social="REIS F3", uf="ES", cidade="X", legacy_id=12, na_rede=False)
        sem_vinculo = Loja(cnpj="13", razao_social="SEM GPS", uf="ES", cidade="X", legacy_id=13)
        s.add_all([a, b, fora, sem_vinculo])
        s.flush()
        for loja, emp in ((a, "E1"), (b, "E2"), (fora, "E3")):
            s.add(VinculoLojaGps(id_empresa_gps=emp, codigo_loja_gps="1", loja_id=loja.id,
                                 situacao=SituacaoVinculoGps.AUTOMATICO))

        def usuario(login, nivel, senha="x", **k):
            u = Usuario(login=login, senha_hash=senha, papel=Papel.CONSULTOR, nome_exibicao=login.split("@")[0],
                        nivel=nivel, loja_principal_id=a.id, **k)
            s.add(u)
            return u
        dono = usuario("dono@reis.com", "PROPRIETARIO")
        usuario("compra@reis.com", "COMPRADOR", linhas_compra="genericos,similares")
        usuario("novo@reis.com", "PROPRIETARIO", senha="")                      # Aguardando
        usuario("saiu@reis.com", "PROPRIETARIO", ativo=False)                    # excluído
        usuario("nada@reis.com", "PROPRIETARIO", email_ruptura=False)            # tudo desligado
        usuario("consultor@rmc.com", "CONSULTOR")                                # equipe RMC
        s.add(Usuario(cnpj_login="30.208.213/0001-74", senha_hash="x", papel=Papel.ADMIN, nome_exibicao="Admin"))
        s.flush()
        for loja in (b, fora, sem_vinculo):
            s.add(UsuarioLoja(usuario_id=dono.id, loja_id=loja.id))
        s.flush()
        yield s


def test_so_usuarios_de_loja_ativos_com_senha_e_algo_ligado(session):
    lista = destinatarios.montar(session)
    assert [d["email"] for d in lista] == ["dono@reis.com", "compra@reis.com"]
    dono, compra = lista
    assert (dono["ruptura"], dono["relatorio"], dono["linhas"]) == (True, False, None)    # o padrão
    assert compra["linhas"] == ["genericos", "similares"]


def test_lojas_principal_e_adicionais_so_da_rede_com_vinculo(session):
    dono = destinatarios.montar(session)[0]
    assert [(l["legacy_id"], l["empresa_gps"]) for l in dono["lojas"]] == [(10, "E1"), (11, "E2")]


def test_publicar_so_quando_muda(session, tmp_path, monkeypatch):
    monkeypatch.setattr("core.copia_spaces.nome_do_banco", lambda: "teste")
    armaz = ArmazenamentoLocal(tmp_path)
    assert destinatarios.publicar(session, armaz) is True
    assert destinatarios.publicar(session, armaz) is False
    session.query(Usuario).filter_by(login="dono@reis.com").one().email_relatorio = True
    session.flush()
    assert destinatarios.publicar(session, armaz) is True
    assert armaz.ler_json("pedido/insumos/teste/destinatarios.json")["destinatarios"][0]["relatorio"] is True


def test_minhas_notificacoes_so_para_usuario_de_loja():
    from core import acesso

    assert acesso.pode_ver(acesso.COMPRADOR, acesso.NOTIFICACOES, ["pedido"])
    assert acesso.pode_ver(acesso.PROPRIETARIO, acesso.NOTIFICACOES, [])        # não depende dos painéis
    assert not acesso.pode_ver(acesso.ADM, acesso.NOTIFICACOES)
    assert not acesso.pode_ver(acesso.CONSULTOR, acesso.NOTIFICACOES)
