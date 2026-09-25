"""Engine/Session do SQLAlchemy, além do bootstrap do banco (migração do
esquema + seed dos 2 usuários fixos + estrutura mínima de pastas)."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from core.config import BASE_DIR, settings
from core.models import Base, Papel, Usuario
from core.security import hash_senha

logger = logging.getLogger(__name__)

if settings.db.is_sqlite:
    _connect_args = {"check_same_thread": False}
    # SQLite é um arquivo local, sem servidor do outro lado da rede — não há
    # conexão pra ficar "morta" nem custo de abrir uma nova, então as opções
    # de pool abaixo (pensadas pra Postgres atrás de rede) nem se aplicam:
    # QueuePool aceitaria, mas SingletonThreadPool (usado por sqlite:///:memory:,
    # como nos testes que criam engine própria fora deste módulo) rejeita
    # pool_size/max_overflow/pool_recycle com erro na hora de criar o engine.
    _opcoes_pool: dict = {}
else:
    # Sem isso, um problema de rede entre o app e o Postgres (ex: firewall
    # descartando pacote em silêncio, em vez de recusar a conexão na hora)
    # deixa o app travado por minutos esperando o TCP dar timeout sozinho —
    # com connect_timeout curto, falha rápido com um erro claro em vez de
    # travar a tela inteira do Streamlit.
    _connect_args = {"connect_timeout": 10}
    _opcoes_pool = {
        # Testa a conexão (SELECT 1 leve) antes de entregá-la pro chamador, e
        # descarta em silêncio se estiver morta, abrindo outra no lugar. Sem
        # isso, com app e banco em servidores diferentes, uma conexão ociosa
        # derrubada pelo firewall ou por um restart do Postgres só é
        # descoberta quando o app tenta USAR ela — e aparece pro usuário como
        # "server closed the connection unexpectedly" numa tela qualquer, sem
        # relação com o que ele estava fazendo.
        "pool_pre_ping": True,
        # Recicla toda conexão com mais de 30 min de vida, mesmo que pareça
        # saudável — evita depender só do pre_ping pra pegar um firewall/
        # load balancer que fecha conexão ociosa por tempo (comum em managed
        # Postgres), e mantém a idade das conexões abertas previsível.
        "pool_recycle": 1800,
        # Conexões mantidas abertas em espera + até quantas extras o pool
        # pode abrir sob pico — um valor explícito, não o default genérico do
        # SQLAlchemy, dimensionado pra um Streamlit de poucos usuários
        # simultâneos (a equipe RMC), não pra uma API de alto tráfego.
        "pool_size": 5,
        "max_overflow": 10,
        # Gravações em lista (`session.execute(stmt, [linhas])`) que NÃO são
        # INSERT simples — INSERT ... ON CONFLICT e UPDATE com bindparam —
        # iam linha por linha no modo padrão do psycopg2: uma ida e volta até
        # o Postgres em NY por linha. Medido em 24/09/2026: 100 linhas com ON
        # CONFLICT levavam 14,5 s; neste modo, 2.000 linhas levam 3,3 s
        # (UPDATE: 0,7 s). Atingia a importação da Base Genéricos (~14 min num
        # banco vazio), a sincronização diária de lojas, a fila de EAN e o
        # vínculo de CNPJ órfão. INSERT simples continua no "insertmanyvalues"
        # de sempre; o que muda é só o resto, agora em páginas de 500.
        "executemany_mode": "values_plus_batch",
        "executemany_batch_page_size": 500,
    }

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

engine = create_engine(settings.db.url, connect_args=_connect_args, future=True, **_opcoes_pool)
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

# Revisão que representa o esquema exatamente como `Base.metadata.create_all`
# o criava antes do Alembic existir neste projeto. É o ponto de adoção: um
# banco que já estava em produção naquele formato é CARIMBADO nesta revisão
# (nada é recriado) e só então recebe as migrações seguintes.
_REVISAO_ESQUEMA_PRE_ALEMBIC = "0001_esquema_inicial"


def _config_alembic(conexao=None, url: str | None = None):
    """Configuração do Alembic apontando pro alembic.ini da raiz do projeto.

    `configure_logger=False`: sem isso o `fileConfig` do alembic.ini
    reconfiguraria o logging do processo inteiro — aceitável no CLI, ruim
    dentro do Streamlit, que perderia a própria configuração de log.

    Quando recebe `conexao`, o env.py reaproveita a conexão do engine deste
    módulo em vez de abrir outra: as migrações rodam com os mesmos
    parâmetros de conexão (timeout, SSL, pool) que o app usa no resto do
    tempo, e não com um segundo conjunto criado só pra elas.
    """
    from alembic.config import Config as _ConfigAlembic

    cfg = _ConfigAlembic(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["url_banco"] = url or settings.db.url
    if conexao is not None:
        cfg.attributes["connection"] = conexao
    return cfg


def _preparar_esquema(engine_alvo=None) -> None:
    """Deixa o esquema do banco na última revisão — o substituto de
    `Base.metadata.create_all`, que criava tabela faltante mas NUNCA
    alterava tabela existente (uma coluna nova simplesmente não chegava ao
    banco, e o app só descobria isso quebrando na primeira consulta que a
    usasse).

    Três cenários, todos resolvidos aqui sem intervenção manual:

    - Banco vazio (ambiente novo, teste manual, máquina de desenvolvimento):
      não há nada pra adotar, o `upgrade` cria tudo do zero.
    - Banco que já existia ANTES do Alembic (o caso de produção hoje: criado
      por `create_all`, sem tabela `alembic_version`): tentar `upgrade`
      direto falharia, porque a primeira migração mandaria criar tabelas que
      já existem. Então ele é carimbado na revisão de adoção — que descreve
      exatamente o esquema que ele já tem, sem executar nenhum DDL — e só
      depois recebe as migrações posteriores.
    - Banco já sob Alembic: aplica só o que estiver pendente (`upgrade` é
      no-op quando já está em `head`).

    Falha aqui NUNCA é silenciada: se a migração não passa, o app não sobe.
    Subir com o esquema errado é exatamente o problema que este código
    existe pra impedir.

    `engine_alvo` existe só pra os testes poderem exercitar esta função
    contra um banco descartável (ver tests/test_migracoes.py) sem precisar
    recarregar este módulo; em produção é sempre o engine deste módulo.
    """
    from alembic import command

    alvo = engine_alvo if engine_alvo is not None else engine
    inspetor = inspect(alvo)
    tabelas_no_banco = set(inspetor.get_table_names())
    tabelas_do_modelo = set(Base.metadata.tables)

    with alvo.begin() as conexao:
        cfg = _config_alembic(conexao, url=str(alvo.url))
        precisa_adotar = (
            "alembic_version" not in tabelas_no_banco
            and bool(tabelas_no_banco & tabelas_do_modelo)
        )
        if precisa_adotar:
            command.stamp(cfg, _REVISAO_ESQUEMA_PRE_ALEMBIC)
        command.upgrade(cfg, "head")

    _criar_tabelas_faltantes(alvo)


def _criar_tabelas_faltantes(alvo) -> None:
    """Cria as tabelas do modelo que ainda não existem no banco — só as
    que faltam, sem alterar nem apagar nenhuma que já existe.

    Por que existe: a adoção de um banco anterior ao Alembic CARIMBA a
    revisão inicial, que presume todas as tabelas daquela revisão. Um banco
    criado por uma versão ainda mais antiga do código não tinha algumas delas
    — e o carimbo as dava por existentes, então nenhuma migração as criava.
    Aconteceu em 24/09/2026 no banco do app publicado: faltava
    `verificacoes_ips_streamlit_cloud`, e o login do admin quebrava com
    UndefinedTable. Tabela ausente é seguro criar a partir do modelo (não há
    dado a preservar); coluna ausente em tabela existente continua sendo
    trabalho de migração, não daqui."""
    existentes = set(inspect(alvo).get_table_names())
    faltando = [tabela for nome, tabela in Base.metadata.tables.items() if nome not in existentes]
    if faltando:
        logger.warning("Criando tabelas que faltavam no banco: %s", ", ".join(t.name for t in faltando))
        Base.metadata.create_all(bind=alvo, tables=faltando)


def init_db() -> None:
    """Deixa o esquema na última revisão (ver `_preparar_esquema`), garante
    os 2 usuários fixos definidos (mesmo CNPJ de login, senha diferente por
    papel) e a estrutura mínima de pastas do Explorador de Arquivos.

    Importante: o Streamlit reexecuta app.py inteiro a cada interação do
    usuário (cada clique, cada rerun), e `app.py` chama `init_db()` no topo do
    script. Sem essa trava, `_bootstrap_pastas()` rodaria em toda e qualquer
    interação e recriaria pastas do sistema que o admin acabou de inativar,
    porque a checagem de 'já existe' só olha itens ativos. A trava garante que
    a inicialização roda só uma vez por processo."""
    global _ja_inicializado
    if _ja_inicializado:
        return

    _preparar_esquema()

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
