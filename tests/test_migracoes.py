"""Testes do controle de versão do esquema (Alembic).

Estes testes existem por causa de um bug de classe, não de um bug pontual:
antes do Alembic, o esquema era criado por `Base.metadata.create_all`, que
cria tabela que falta mas NUNCA altera tabela que já existe. Uma coluna
adicionada em core/models.py simplesmente não chegava ao banco de produção,
e o sistema só descobria isso quebrando na primeira consulta que a usasse —
em produção, na frente do usuário.

O teste de deriva (`test_migracoes_produzem_o_mesmo_esquema_dos_modelos`) é
o que impede a volta desse bug: se alguém mexer em core/models.py sem criar
a migração correspondente, ele falha na hora, no CI/pytest, e não meses
depois no servidor.
"""
from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from core import db as core_db
from core.models import Base


def _engine_descartavel(tmp_path, nome="teste.db"):
    """Banco SQLite em ARQUIVO (não `:memory:`): o Alembic abre e fecha
    conexões próprias durante as migrações, e cada conexão nova num
    `:memory:` enxergaria um banco vazio e diferente."""
    return create_engine(f"sqlite:///{tmp_path / nome}", future=True)


def test_migracoes_criam_todas_as_tabelas_do_modelo(tmp_path):
    """Banco vazio + `upgrade head` tem que produzir o esquema inteiro —
    é o caminho de um ambiente novo (máquina de desenvolvimento, servidor
    novo, restauração de backup zerado)."""
    engine = _engine_descartavel(tmp_path)

    core_db._preparar_esquema(engine_alvo=engine)

    tabelas = set(inspect(engine).get_table_names())
    faltando = set(Base.metadata.tables) - tabelas
    assert not faltando, f"migrações não criaram: {sorted(faltando)}"
    assert "alembic_version" in tabelas


def test_migracoes_produzem_o_mesmo_esquema_dos_modelos(tmp_path):
    """Trava de deriva entre core/models.py e alembic/versions/.

    Aplica todas as migrações num banco limpo e compara o resultado com
    `Base.metadata`. Qualquer diferença — coluna nova, coluna removida,
    índice, constraint — falha aqui. Ou seja: mexeu no modelo sem gerar a
    migração (`alembic revision --autogenerate -m "..."`), o teste acusa.
    """
    engine = _engine_descartavel(tmp_path)
    core_db._preparar_esquema(engine_alvo=engine)

    with engine.connect() as conexao:
        contexto = MigrationContext.configure(conexao)
        diferencas = compare_metadata(contexto, Base.metadata)

    assert not diferencas, (
        "O esquema gerado pelas migrações não bate com core/models.py.\n"
        "Gere a migração que falta com:\n"
        '    alembic revision --autogenerate -m "descrição da mudança"\n\n'
        f"Diferenças detectadas: {diferencas}"
    )


def _criar_esquema_legado(engine) -> None:
    """Reproduz um banco pré-Alembic FIEL: o esquema exatamente como era na
    revisão de adoção, sem a tabela `alembic_version`.

    Por que não `Base.metadata.create_all(engine)`, que seria o jeito óbvio:
    `create_all` usa os modelos de HOJE, então o banco "legado" nasceria já
    com as tabelas criadas por migrações POSTERIORES à adoção — e a primeira
    dessas migrações falharia mandando criar uma tabela que o próprio teste
    acabou de criar. Isso não é um problema do código de produção (um banco
    legado de verdade tem só o que existia na época), é a simulação que
    estaria errada. Aplicar a migração de adoção e remover o carimbo dá o
    mesmo banco que produção tinha antes do Alembic, e continua fiel a cada
    migração nova que entrar no projeto."""
    with engine.begin() as conexao:
        cfg = core_db._config_alembic(conexao, url=str(engine.url))
        command.upgrade(cfg, core_db._REVISAO_ESQUEMA_PRE_ALEMBIC)
    with engine.begin() as conexao:
        conexao.exec_driver_sql("DROP TABLE alembic_version")


def test_banco_anterior_ao_alembic_e_adotado_sem_recriar_nada(tmp_path):
    """O caso REAL de produção: um banco que já existe, criado antes do
    Alembic entrar no projeto, sem tabela `alembic_version`.

    `upgrade head` direto falharia nele (mandaria criar tabelas que já
    existem). O esperado é que ele seja carimbado na revisão de adoção, sem
    executar DDL nenhum, que os DADOS que já estavam lá continuem intactos, e
    que as migrações POSTERIORES à adoção sejam aplicadas normalmente em
    seguida — é isso que este teste prova.
    """
    engine = _engine_descartavel(tmp_path, "legado.db")

    _criar_esquema_legado(engine)
    with engine.begin() as conexao:
        conexao.exec_driver_sql(
            "INSERT INTO base_genericos (nome_canonico, ativo, criado_em) "
            "VALUES ('DIPIRONA 500MG 10CPR', 1, '2026-01-01 00:00:00')"
        )
    assert "alembic_version" not in set(inspect(engine).get_table_names())

    core_db._preparar_esquema(engine_alvo=engine)

    with engine.connect() as conexao:
        revisao = conexao.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        sobreviveu = conexao.exec_driver_sql(
            "SELECT nome_canonico FROM base_genericos"
        ).scalar_one()
    tabelas = set(inspect(engine).get_table_names())

    assert revisao is not None
    assert sobreviveu == "DIPIRONA 500MG 10CPR", "a adoção não pode tocar nos dados existentes"
    faltando = set(Base.metadata.tables) - tabelas
    assert not faltando, (
        "depois da adoção, as migrações seguintes precisam ser aplicadas normalmente — "
        f"faltou criar: {sorted(faltando)}"
    )


def test_preparar_esquema_e_idempotente(tmp_path):
    """Rodar duas vezes seguidas não pode fazer nada na segunda — é o que
    acontece a cada restart do container em produção."""
    engine = _engine_descartavel(tmp_path)

    core_db._preparar_esquema(engine_alvo=engine)
    with engine.connect() as conexao:
        primeira = conexao.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    core_db._preparar_esquema(engine_alvo=engine)
    with engine.connect() as conexao:
        segunda = conexao.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        tabelas = set(inspect(engine).get_table_names())

    assert primeira == segunda
    assert set(Base.metadata.tables) <= tabelas


def test_existe_exatamente_uma_cabeca_de_migracao():
    """Duas cabeças (branch) fariam `upgrade head` falhar em produção com
    'Multiple head revisions are present'. Acontece quando duas migrações
    são criadas em paralelo apontando pro mesmo pai — barato de detectar
    aqui, caro de descobrir no deploy."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(core_db._config_alembic())
    cabecas = script.get_heads()
    assert len(cabecas) == 1, f"esperava 1 cabeça de migração, achei {cabecas}"


def test_revisao_de_adocao_existe_no_historico():
    """`_preparar_esquema` carimba um banco pré-Alembic nesta revisão. Se
    ela for renomeada ou removida sem atualizar a constante, a adoção de
    produção quebra — e só na hora do deploy."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(core_db._config_alembic())
    revisoes = {r.revision for r in script.walk_revisions()}
    assert core_db._REVISAO_ESQUEMA_PRE_ALEMBIC in revisoes
