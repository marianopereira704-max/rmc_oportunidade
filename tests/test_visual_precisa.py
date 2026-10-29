"""Quando a mudança precisa do teste visual (tests/visual/precisa.py, 29/10/2026)."""
from tests.visual.precisa import arquivos_da_tela


def test_so_o_que_aparece_na_tela_pede_o_teste_visual():
    assert arquivos_da_tela(["pedido/alerta.py", "docs/rotina_pedido.md", ".github/workflows/rotina_pedido.yml",
                             "integrations/insumos.py", "tests/test_alerta.py"]) == []
    assert arquivos_da_tela(["views/dados/rotinas.py", "core/theme.py", "core/dashboard_ruptura.py"]) == [
        "views/dados/rotinas.py", "core/theme.py", "core/dashboard_ruptura.py"]
    assert arquivos_da_tela(["tests/visual/referencia/login_1280.png"]) == []


def test_requirements_so_com_o_streamlit():
    assert arquivos_da_tela(["requirements.txt"], "+pandas==2.3.0") == []
    assert arquivos_da_tela(["requirements.txt"], "-streamlit==1.62.0\n+streamlit==1.63.0") == [
        "requirements.txt (versão do Streamlit)"]
