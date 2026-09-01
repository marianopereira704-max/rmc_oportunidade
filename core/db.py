"""Engine/Session do SQLAlchemy, além do bootstrap do banco (criação de
tabelas + seed dos 2 usuários fixos + estrutura mínima de pastas)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings
from core.models import Base, Papel, Usuario
from core.security import hash_senha

_connect_args = {"check_same_thread": False} if settings.db.is_sqlite else {}

if settings.db.is_sqlite:
    # SQLite não cria diretórios sozinho — só o arquivo, e só se a pasta pai já
    # existir. `DatabaseConfig.url` pode ter sido sobrescrito via secrets.toml
    # pra apontar pra fora da árvore do OneDrive (evitar lock de sincronização
    # — ver comentário em .streamlit/secrets.toml), e essa pasta pode ainda não
    # existir na máquina. Sem isso, create_engine/create_all falha com
    # "unable to open database file".
    _caminho_db = make_url(settings.db.url).database
    if _caminho_db:
        Path(_caminho_db).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.db.url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _configurar_sqlite(dbapi_conn, _):
    """WAL deixa leituras concorrentes não bloquearem escritas (e vice-versa)
    — só escritor-vs-escritor ainda disputa lock. busy_timeout faz a conexão
    esperar (em vez de falhar na hora) quando bate nessa disputa, dando
    margem pra uma transação de importação terminar ou uma sincronização de
    nuvem (OneDrive) liberar o arquivo. Só se aplica a SQLite — Postgres
    nem entra nesse listener fora do ambiente de dev."""
    if settings.db.is_sqlite:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.db.sqlite_busy_timeout_ms}")
        cursor.close()


@contextmanager
def get_session():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_ja_inicializado = False


def init_db() -> None:
    """Cria as tabelas (se não existirem), garante os 2 usuários fixos
    definidos (mesmo CNPJ de login, senha diferente por papel) e a estrutura
    mínima de pastas do Explorador de Arquivos.

    Importante: o Streamlit reexecuta app.py inteiro a cada interação do
    usuário (cada clique, cada rerun), e `app.py` chama `init_db()` no topo do
    script. Sem essa trava, `_bootstrap_pastas()` rodaria em toda e qualquer
    interação e recriaria pastas do sistema que o admin acabou de inativar,
    porque a checagem de 'já existe' só olha itens ativos. A trava garante que
    a inicialização roda só uma vez por processo."""
    global _ja_inicializado
    if _ja_inicializado:
        return

    Base.metadata.create_all(engine)

    cnpj_login = "30.208.213/0001-74"
    usuarios_fixos = [
        (Papel.ADMIN, "adm123", "Administrador RMC"),
        (Papel.CONSULTOR, "consultor", "Consultor"),
    ]

    with get_session() as session:
        for papel, senha, nome in usuarios_fixos:
            existente = session.execute(
                select(Usuario).where(Usuario.cnpj_login == cnpj_login, Usuario.papel == papel)
            ).scalar_one_or_none()
            if existente is None:
                session.add(
                    Usuario(
                        cnpj_login=cnpj_login,
                        senha_hash=hash_senha(senha),
                        papel=papel,
                        nome_exibicao=nome,
                    )
                )

    _bootstrap_pastas()
    _ja_inicializado = True


def _bootstrap_pastas() -> None:
    """Garante as pastas padrão (Ofertas/Gruppy, Compras/GPS) na primeira vez
    que o sistema roda. Importante: a checagem de 'já existe' é GLOBAL (por
    nome + criado_por='sistema'), não "é filha ativa do pai X" — porque
    inativar move o nó pra _Inativos (muda o parent_id). Se checássemos só os
    filhos ativos do pai esperado, toda pasta padrão que o admin inativasse
    voltaria a ser criada do zero (duplicada) no próximo restart, em vez de
    continuar inativa como o admin decidiu."""
    from sqlalchemy import select as _select

    from core.models import FSNode
    from storage import filesystem as fs  # import local pra evitar ciclo de import

    with get_session() as session:
        raiz = fs.garantir_raiz(session)
        fs.garantir_pasta_inativos(session)

        def _pasta_sistema_existe(nome: str) -> bool:
            return session.execute(
                _select(FSNode.id).where(FSNode.nome == nome, FSNode.criado_por == "sistema")
            ).first() is not None

        for nome_pai, nome_filha in [("Ofertas", "Gruppy"), ("Compras", "GPS")]:
            if not _pasta_sistema_existe(nome_pai):
                fs.criar_pasta(session, raiz.id, nome_pai, "sistema")
            pai = next(
                (f for f in fs.listar_conteudo(session, raiz.id, incluir_inativos=True) if f.nome == nome_pai), None
            )
            if pai is not None and not _pasta_sistema_existe(nome_filha):
                fs.criar_pasta(session, pai.id, nome_filha, "sistema")
