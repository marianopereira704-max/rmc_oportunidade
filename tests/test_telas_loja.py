"""As contas das 4 telas da loja (pedido/telas_loja.py, 29/10/2026): a rotina da madrugada roda sem Streamlit e sem
banco, então o módulo não pode depender de nenhum dos dois — nem por um import indireto de `views/` ou
`integrations/` (que puxam o banco)."""
import ast
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "pedido" / "telas_loja.py"
PROIBIDOS = ("streamlit", "core.db", "views", "integrations", "sqlalchemy")


def _imports(arvore) -> set[str]:
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes |= {a.name for a in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module)
            nomes |= {f"{no.module}.{a.name}" for a in no.names}
    return nomes


def test_telas_loja_nao_depende_de_streamlit_nem_do_banco():
    usados = _imports(ast.parse(ARQUIVO.read_text(encoding="utf-8")))
    ruins = sorted(n for n in usados if any(n == p or n.startswith(p + ".") for p in PROIBIDOS))
    assert not ruins, f"pedido/telas_loja.py importa {ruins}: a rotina da madrugada não tem Streamlit nem banco"


def test_as_quatro_contas_existem():
    from pedido import telas_loja

    for nome in ("assistente", "estoque", "ruptura", "compras"):
        assert callable(getattr(telas_loja, nome))
