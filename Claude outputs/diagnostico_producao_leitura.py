"""
Diagnóstico SOMENTE LEITURA do banco de produção/dev (Postgres).

Não faz nenhuma escrita: abre a conexão em modo read-only (o Postgres
recusa qualquer INSERT/UPDATE/DELETE nessa sessão) e só executa SELECTs.

Como rodar (no seu computador, na pasta do projeto, com o venv ativado):

    python diagnostico_producao_leitura.py

Ele lê a DATABASE_URL do mesmo lugar que o app usa (.streamlit/secrets.toml
ou variável de ambiente), então não precisa colar senha em lugar nenhum.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

from sqlalchemy import create_engine, text


def _carregar_database_url() -> str:
    caminho_secrets = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if caminho_secrets.exists():
        dados = tomllib.loads(caminho_secrets.read_text(encoding="utf-8"))
        if "DATABASE_URL" in dados:
            return str(dados["DATABASE_URL"])
        for secao in dados.values():
            if isinstance(secao, dict) and "DATABASE_URL" in secao:
                return str(secao["DATABASE_URL"])
    import os

    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    print("Não encontrei DATABASE_URL em .streamlit/secrets.toml nem na variável de ambiente.")
    sys.exit(1)


def main() -> None:
    url = _carregar_database_url()
    engine = create_engine(url)

    with engine.connect() as conn:
        conn.execution_options(postgresql_readonly=True)
        conn.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))

        def linha(sql: str, titulo: str) -> None:
            print(f"\n--- {titulo} ---")
            resultado = conn.execute(text(sql))
            colunas = resultado.keys()
            linhas = resultado.fetchall()
            if not linhas:
                print("(nenhuma linha)")
                return
            for row in linhas:
                print(dict(zip(colunas, row)))

        linha("SELECT COUNT(*) AS total FROM lojas", "lojas (total)")
        linha("SELECT COUNT(*) AS total FROM registros_compra_gps", "registros_compra_gps (total)")
        linha(
            "SELECT ano_mes, COUNT(*) AS linhas, COUNT(DISTINCT ean) AS eans_distintos "
            "FROM registros_compra_gps GROUP BY ano_mes ORDER BY ano_mes",
            "registros_compra_gps por ano_mes",
        )
        linha("SELECT COUNT(*) AS total FROM base_genericos", "base_genericos (total)")
        linha("SELECT COUNT(*) AS total FROM ean_genericos", "ean_genericos (total, resolvidos)")
        linha("SELECT COUNT(*) AS total FROM itens_tabela_gruppy", "itens_tabela_gruppy (total)")
        linha(
            "SELECT status, uf, COUNT(*) AS total FROM tabelas_gruppy_cobertura "
            "GROUP BY status, uf ORDER BY status, uf",
            "tabelas_gruppy_cobertura por status/uf",
        )
        linha(
            "SELECT origem, status, COUNT(*) AS total FROM fila_resolucao_ean "
            "GROUP BY origem, status ORDER BY origem, status",
            "fila_resolucao_ean por origem/status",
        )
        linha(
            """
            SELECT COUNT(DISTINCT l.id) AS lojas_com_compra_resolvida
            FROM lojas l
            JOIN registros_compra_gps c ON c.loja_id = l.id
            JOIN ean_genericos eg ON eg.ean = c.ean
            """,
            "lojas com pelo menos 1 compra GPS já resolvida (EanGenerico) -- ignora período",
        )
        linha(
            """
            SELECT cob.uf, COUNT(DISTINCT eg.base_generico_id) AS genericos_com_preco_ativo
            FROM itens_tabela_gruppy i
            JOIN ean_genericos eg ON eg.ean = i.ean
            JOIN tabelas_gruppy tg ON tg.id = i.tabela_gruppy_id
            JOIN tabelas_gruppy_cobertura cob ON cob.tabela_gruppy_id = tg.id
            WHERE cob.status = 'ATIVA'
            GROUP BY cob.uf
            """,
            "genéricos com preço Gruppy resolvido E cobertura ATIVA, por UF",
        )
        linha(
            # Mesma junção estrutural de _query_base em core/queries.py
            # (compras -> Loja -> menor_preco[uf+base_generico_id] -> BaseGenerico),
            # mas SEM o filtro de período (ano_mes) -- pra responder "existe ALGUMA
            # combinação pronta, ignorando o filtro de Período da tela?"
            """
            WITH compras AS (
                SELECT c.loja_id, eg.base_generico_id
                FROM registros_compra_gps c
                JOIN ean_genericos eg ON eg.ean = c.ean
                GROUP BY c.loja_id, eg.base_generico_id
            ),
            menor_preco AS (
                SELECT cob.uf, eg.base_generico_id
                FROM itens_tabela_gruppy i
                JOIN ean_genericos eg ON eg.ean = i.ean
                JOIN tabelas_gruppy tg ON tg.id = i.tabela_gruppy_id
                JOIN tabelas_gruppy_cobertura cob ON cob.tabela_gruppy_id = tg.id
                WHERE cob.status = 'ATIVA'
                GROUP BY cob.uf, eg.base_generico_id
            )
            SELECT COUNT(*) AS combinacoes_prontas_para_analise
            FROM compras
            JOIN lojas l ON l.id = compras.loja_id
            JOIN menor_preco ON menor_preco.uf = l.uf AND menor_preco.base_generico_id = compras.base_generico_id
            """,
            "combinações (loja, genérico) que DEVERIAM aparecer na análise hoje (ignorando período)",
        )

        linha(
            "SELECT id, laboratorio, nome_arquivo_origem, criado_em FROM tabelas_gruppy ORDER BY criado_em",
            "tabelas_gruppy (cada upload de Gruppy)",
        )
        linha(
            """
            SELECT c.id, c.tabela_gruppy_id, c.uf, c.status, c.inativada_em, c.inativada_por,
                   t.laboratorio, t.nome_arquivo_origem, t.criado_em AS tabela_criada_em
            FROM tabelas_gruppy_cobertura c
            JOIN tabelas_gruppy t ON t.id = c.tabela_gruppy_id
            ORDER BY c.uf, c.id
            """,
            "tabelas_gruppy_cobertura detalhado (pra ver se ATIVA/INATIVA em MG são uploads diferentes)",
        )
        linha(
            "SELECT uf, COUNT(*) AS total_lojas FROM lojas GROUP BY uf ORDER BY total_lojas DESC",
            "lojas por UF",
        )
        linha(
            """
            SELECT f.id, f.nome, f.tipo, f.status, p.nome AS pasta_pai
            FROM fs_nodes f
            LEFT JOIN fs_nodes p ON p.id = f.parent_id
            WHERE f.nome ILIKE '%generic%' OR p.nome ILIKE '%generic%'
            """,
            "arquivos/pastas relacionados a 'genéricos' no explorador de arquivos",
        )

    print("\nFim do diagnóstico. Nenhuma alteração foi feita no banco.")


if __name__ == "__main__":
    main()
