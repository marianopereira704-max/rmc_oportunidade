"""Login individual das lojas (integrations/usuarios.py, 07/10/2026): login
por e-mail, links de criar/recuperar senha e os e-mails — sem enviar e-mail
de verdade (o envio é trocado por uma lista)."""
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, Loja, Papel, TokenSenha, Usuario, UsuarioLojaHistorico
from core.security import hash_senha
from integrations import usuarios

AGORA = dt.datetime(2026, 10, 7, 12, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loja = Loja(cnpj="1", razao_social="LOJA", uf="MG", cidade="BH")
        s.add(loja)
        s.flush()
        s.add(Usuario(login="ana@loja.com", senha_hash="", papel=Papel.CONSULTOR, nome_exibicao="Ana Souza",
                      nivel="COMPRADOR", loja_principal_id=loja.id, linhas_compra="genericos,similares",
                      paineis="pedido"))
        s.add(Usuario(cnpj_login="30.208.213/0001-74", senha_hash=hash_senha("adm123"), papel=Papel.ADMIN,
                      nome_exibicao="Admin"))
        s.flush()
        yield s


def _ana(s) -> Usuario:
    return s.query(Usuario).filter_by(login="ana@loja.com").one()


class Caixa:
    def __init__(self):
        self.enviados = []

    def __call__(self, destino, assunto, texto, html):
        self.enviados.append({"destino": destino, "assunto": assunto, "texto": texto, "html": html})


def test_ativacao_cria_a_senha_uma_vez_so_e_entra_por_email(session):
    caixa = Caixa()
    ana = _ana(session)
    assert usuarios.status(ana) == "Aguardando"
    url = usuarios.enviar_link(session, ana, usuarios.ATIVACAO, caixa)
    assert caixa.enviados[0]["destino"] == "ana@loja.com" and url in caixa.enviados[0]["texto"]
    assert "Criar minha senha" in caixa.enviados[0]["html"] and "48 horas" in caixa.enviados[0]["texto"]
    token = url.split("token=")[1]
    # O banco guarda só o hash do token.
    assert session.query(TokenSenha).one().token_hash != token

    with pytest.raises(ValueError, match="8 caracteres"):
        usuarios.definir_senha(session, token, "curta")
    usuarios.definir_senha(session, token, "senha-boa-123")
    assert usuarios.status(_ana(session)) == "Ativo"
    with pytest.raises(ValueError, match="não vale mais"):
        usuarios.definir_senha(session, token, "outra-senha-1")          # link vale uma vez

    assert usuarios.autenticar_email(session, "  ANA@Loja.com ", "senha-boa-123") is not None   # sem diferença de maiúscula
    assert usuarios.autenticar_email(session, "ana@loja.com", "errada-123") is None


def test_link_vence_e_link_novo_invalida_o_anterior(session):
    ana = _ana(session)
    primeiro = usuarios.criar_token(session, ana.id, usuarios.ATIVACAO, agora=AGORA)
    segundo = usuarios.criar_token(session, ana.id, usuarios.ATIVACAO, agora=AGORA)
    assert usuarios.validar_token(session, primeiro, AGORA) is None              # pedir outro invalida o anterior
    assert usuarios.validar_token(session, segundo, AGORA + dt.timedelta(hours=47)) is not None
    assert usuarios.validar_token(session, segundo, AGORA + dt.timedelta(hours=49)) is None   # 48 h
    rec = usuarios.criar_token(session, ana.id, usuarios.RECUPERACAO, agora=AGORA)
    assert usuarios.validar_token(session, rec, AGORA + dt.timedelta(minutes=59)) is not None
    assert usuarios.validar_token(session, rec, AGORA + dt.timedelta(minutes=61)) is None     # 1 h


def test_esqueci_minha_senha(session):
    caixa = Caixa()
    assert usuarios.pedir_recuperacao(session, "naoexiste@x.com", caixa) is False and caixa.enviados == []
    # Ainda sem senha: recebe o de ATIVAÇÃO.
    assert usuarios.pedir_recuperacao(session, "ana@loja.com", caixa) is True
    assert "Criar minha senha" in caixa.enviados[-1]["html"]
    _ana(session).senha_hash = hash_senha("senha-boa-123")
    usuarios.pedir_recuperacao(session, "ana@loja.com", caixa)
    assert "Redefinir" in caixa.enviados[-1]["assunto"] and "1 hora" in caixa.enviados[-1]["texto"]
    # Excluído não recebe nada e não entra.
    ana = _ana(session)
    ana.ativo = False
    assert usuarios.pedir_recuperacao(session, "ana@loja.com", caixa) is False
    assert usuarios.status(ana) == "Excluído"
    assert usuarios.autenticar_email(session, "ana@loja.com", "senha-boa-123") is None


def test_email_unico_e_codigos(session):
    assert usuarios.email_em_uso(session, "ANA@loja.com")
    assert not usuarios.email_em_uso(session, "ana@loja.com", exceto_id=_ana(session).id)
    assert usuarios.codigos("genericos,similares") == ["genericos", "similares"] and usuarios.codigos(None) == []
    assert usuarios.juntar(["similares", "genericos", "genericos"]) == "genericos,similares"
    assert usuarios.parece_email("a@b") and not usuarios.parece_email("30.208.213/0001-74")


# ---------------------------------------------------------------------------
# Cadastro (aba Cadastro, 07/10/2026)
# ---------------------------------------------------------------------------

@pytest.fixture()
def rede(session):
    """Grupo GE10 com 3 lojas (uma fora da rede), GE20 com 1, e uma sem grupo."""
    lojas = {
        "a": Loja(cnpj="10", razao_social="REIS F1", uf="ES", cidade="X", legacy_id=10, grupo_economico="GE10"),
        "b": Loja(cnpj="11", razao_social="REIS F2", uf="ES", cidade="X", legacy_id=11, grupo_economico="GE10"),
        "c": Loja(cnpj="12", razao_social="REIS F3", uf="ES", cidade="X", legacy_id=12, grupo_economico="GE10",
                  na_rede=False),
        "d": Loja(cnpj="20", razao_social="OUTRA", uf="MG", cidade="Y", legacy_id=20, grupo_economico="GE20"),
        "e": Loja(cnpj="30", razao_social="SEM GRUPO", uf="MG", cidade="Y", legacy_id=30),
    }
    session.add_all(lojas.values())
    session.flush()
    return {k: v.id for k, v in lojas.items()}


def _dados(loja_id, **mudar) -> usuarios.DadosUsuario:
    base = dict(nome="Bruno Reis", cargo="Comprador", email="bruno@reis.com", telefone="", nivel="COMPRADOR",
                linhas=["genericos"], paineis=["pedido"], loja_principal_id=loja_id)
    base.update(mudar)
    return usuarios.DadosUsuario(**base)


def test_validacao_do_cadastro(session, rede):
    assert usuarios.validar(session, _dados(rede["a"])) == []
    erros = usuarios.validar(session, _dados(rede["a"], nome=" ", cargo="", email="ANA@loja.com", paineis=[],
                                             linhas=[]))
    assert any("nome" in e for e in erros) and any("cargo" in e for e in erros)
    assert any("já tem cadastro" in e for e in erros)                    # e-mail único, sem diferença de maiúscula
    assert any("painel" in e for e in erros) and any("linha de compra" in e for e in erros)
    # Proprietário não precisa de linha; loja fora da rede não vale.
    assert usuarios.validar(session, _dados(rede["a"], nivel="PROPRIETARIO", linhas=[])) == []
    assert any("loja da rede" in e for e in usuarios.validar(session, _dados(rede["c"])))
    assert any("inválido" in e for e in usuarios.validar(session, _dados(rede["a"], email="bruno@reis")))


def test_comprador_nao_recebe_o_dashboard(session, rede):
    """21/10/2026: o Dashboard é do Proprietário (e da equipe RMC). A tela já só
    oferece os painéis do nível, mas a gravação não confia nela."""
    # Só "dashboard" não basta para o Comprador: sobra nenhum painel válido.
    assert any("painel" in e for e in usuarios.validar(session, _dados(rede["a"], paineis=["dashboard"])))
    assert usuarios.validar(session, _dados(rede["a"], paineis=["dashboard", "pedido"])) == []
    assert usuarios.validar(session, _dados(rede["a"], nivel="PROPRIETARIO", linhas=[], paineis=["dashboard"])) == []
    u = usuarios.criar(session, _dados(rede["a"], paineis=["dashboard", "pedido"]), "Admin")
    assert u.paineis == "pedido"                                         # o painel proibido não é gravado
    with pytest.raises(ValueError, match="painel"):
        usuarios.atribuir_paineis(session, u.id, ["dashboard"], "Admin")
    usuarios.atribuir_paineis(session, u.id, ["dashboard", "oportunidade_produto"], "Admin")
    assert u.paineis == "oportunidade_produto"
    # Promovido a Proprietário, passa a poder receber.
    usuarios.editar(session, u.id, _dados(rede["a"], nivel="PROPRIETARIO", linhas=[], paineis=["dashboard"]), "Admin")
    assert u.paineis == "dashboard"


def test_criar_lista_da_loja_e_atribuir_empresas(session, rede):
    u = usuarios.criar(session, _dados(rede["a"], email=" Bruno@Reis.com "), "Admin")
    assert u.login == "bruno@reis.com" and usuarios.status(u) == "Aguardando" and u.papel == Papel.CONSULTOR
    assert [r["nome"] for r in usuarios.da_loja(session, rede["a"])] == ["Bruno Reis"]
    assert usuarios.da_loja(session, rede["b"]) == []                    # sem atribuir: só a loja do cadastro

    # Só lojas da rede do mesmo grupo (a F3 saiu da rede).
    assert [l["id"] for l in usuarios.lojas_do_grupo(session, rede["a"])] == [rede["a"], rede["b"]]
    assert [l["id"] for l in usuarios.lojas_do_grupo(session, rede["e"])] == [rede["e"]]
    with pytest.raises(ValueError, match="mesmo grupo"):
        usuarios.atribuir_lojas(session, u.id, [rede["d"]], "Admin")
    assert usuarios.atribuir_lojas(session, u.id, [rede["b"]], "Admin") == (1, 0)
    linha = usuarios.da_loja(session, rede["b"])[0]                     # aparece na loja adicional também
    assert linha["lojas"] == ["10 · REIS F1", "11 · REIS F2"]
    assert linha["nivel"] == "COMPRADOR" and usuarios.descrever_nivel("COMPRADOR", ["similares", "genericos"]) \
        == "Comprador · Genérico, Similar"

    assert usuarios.atribuir_lojas(session, u.id, [], "Admin") == (0, 1)
    assert [(h.loja_id, h.registrado_por) for h in session.query(UsuarioLojaHistorico)] == [(rede["b"], "Admin")]


def test_editar_troca_loja_principal_e_paineis(session, rede):
    u = usuarios.criar(session, _dados(rede["a"]), "Admin")
    usuarios.atribuir_lojas(session, u.id, [rede["b"]], "Admin")
    # Proprietário não guarda linha de compra.
    usuarios.editar(session, u.id, _dados(rede["a"], nivel="PROPRIETARIO", linhas=["genericos"]), "Admin")
    assert u.linhas_compra is None and u.nivel == "PROPRIETARIO"
    # A adicional virou a principal: sai das adicionais; a antiga vai pro histórico.
    usuarios.editar(session, u.id, _dados(rede["b"], nivel="PROPRIETARIO"), "Admin")
    assert u.loja_principal_id == rede["b"] and u.lojas == []
    # Loja de outro grupo: as adicionais do grupo antigo saem.
    usuarios.atribuir_lojas(session, u.id, [rede["a"]], "Admin")
    usuarios.editar(session, u.id, _dados(rede["d"], nivel="PROPRIETARIO"), "Admin")
    assert u.lojas == [] and usuarios.da_loja(session, rede["a"]) == []
    motivos = [h.motivo for h in session.query(UsuarioLojaHistorico).order_by(UsuarioLojaHistorico.id)]
    assert motivos.count("Troca de loja principal") == 2 and any("fora do grupo" in m for m in motivos)

    with pytest.raises(ValueError, match="painel"):
        usuarios.atribuir_paineis(session, u.id, [], "Admin")
    usuarios.atribuir_paineis(session, u.id, ["pedido", "oportunidade_produto", "inventado"], "Admin")
    assert u.paineis == "oportunidade_produto,pedido"
    # A equipe RMC não passa pelo cadastro.
    admin = session.query(Usuario).filter_by(papel=Papel.ADMIN).one()
    with pytest.raises(ValueError):
        usuarios.excluir(session, admin.id, "Consultor")


def test_excluir_e_reativar(session, rede):
    u = usuarios.criar(session, _dados(rede["a"]), "Admin")
    token = usuarios.criar_token(session, u.id, usuarios.ATIVACAO)
    usuarios.excluir(session, u.id, "Admin")
    assert usuarios.status(u) == "Excluído" and u.excluido_por == "Admin"
    assert usuarios.validar_token(session, token) is None                # o link aberto para de valer
    assert usuarios.da_loja(session, rede["a"]) == []
    assert [r["status"] for r in usuarios.da_loja(session, rede["a"], com_excluidos=True)] == ["Excluído"]
    usuarios.reativar(session, u.id, "Admin")
    assert usuarios.status(u) == "Aguardando" and u.excluido_em is None
    # Loja principal fora da rede: não reativa.
    usuarios.excluir(session, u.id, "Admin")
    session.get(Loja, rede["a"]).na_rede = False
    with pytest.raises(ValueError, match="não é mais da rede"):
        usuarios.reativar(session, u.id, "Admin")


def test_sincronizacao_tira_o_acesso_de_quem_saiu(session, rede):
    """Q32: loja que sai da rede ou do grupo perde o acesso sozinha, com histórico."""
    dono = usuarios.criar(session, _dados(rede["a"], nivel="PROPRIETARIO", email="dono@reis.com"), "Admin")
    usuarios.atribuir_lojas(session, dono.id, [rede["b"]], "Admin")
    outro = usuarios.criar(session, _dados(rede["d"], email="outro@x.com"), "Admin")
    assert usuarios.ajustar_acessos(session) == {"excluidos": 0, "retiradas": 0}

    session.get(Loja, rede["b"]).grupo_economico = "GE99"               # a T.I. tirou a F2 do grupo
    session.get(Loja, rede["d"]).na_rede = False                         # e a OUTRA saiu da rede
    assert usuarios.ajustar_acessos(session) == {"excluidos": 1, "retiradas": 1}
    assert dono.lojas == [] and dono.ativo and not outro.ativo
    assert outro.excluido_por == usuarios.SINCRONIZACAO
    motivos = {h.motivo for h in session.query(UsuarioLojaHistorico)}
    assert motivos == {"Loja fora do grupo econômico da loja principal", "Loja principal saiu da rede (usuário excluído)"}


def test_preferencias_de_email_com_o_padrao(session):
    """E-mails para as lojas (29/10/2026): vazio = aviso de ruptura ligado, relatório desligado (decisão do Mariano)."""
    ana = _ana(session)
    assert usuarios.preferencias(ana) == (True, False)
    usuarios.gravar_preferencias(session, ana.id, ruptura=False, relatorio=True)
    session.flush()
    assert usuarios.preferencias(session.get(Usuario, ana.id)) == (False, True)
    with pytest.raises(ValueError):
        usuarios.gravar_preferencias(session, 9999, True, True)
