"""Diz se a mudança em andamento precisa do teste visual (29/10/2026, pedido do Mariano: o teste leva ~10 min e
rodava a cada verificação, mesmo quando só a rotina ou um documento mudava).

    python -m tests.visual.precisa            # contra o último commit (HEAD), com os arquivos novos
    python -m tests.visual.precisa main       # contra outro ramo ou commit

Precisa quando muda o que aparece na tela: as telas, o tema, as dicas, as contas que dão os números e textos
exibidos e a versão do Streamlit. Na dúvida sobre um arquivo fora da lista que mude a tela, rode o teste.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys

DA_TELA = (
    "views/*", "app.py", "core/theme.py", "core/dicas.py", ".streamlit/config.toml",
    "pedido/calculo.py", "pedido/telas_loja.py", "core/dashboard_*.py", "core/analise_api.py", "core/analise.py",
    "core/revisar.py", "tests/visual/*",
)


def _git(*args: str) -> list[str]:
    saida = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [l.strip() for l in saida.splitlines() if l.strip()]


def arquivos_da_tela(alterados: list[str], requirements_diff: str = "") -> list[str]:
    da_tela = [a for a in alterados if any(fnmatch.fnmatch(a, p) for p in DA_TELA)
               and not a.startswith(("tests/visual/referencia/", "tests/visual/resultado/"))
               and a != "tests/visual/precisa.py"]
    # Só a linha da versão (`streamlit==…`): um comentário que fala do "Streamlit Cloud" não conta.
    versao = [l for l in requirements_diff.splitlines() if l[1:].strip().lower().startswith("streamlit")]
    if "requirements.txt" in alterados and versao:
        da_tela.append("requirements.txt (versão do Streamlit)")
    return da_tela


def main(argv: list[str] | None = None) -> int:
    base = (argv if argv is not None else sys.argv[1:]) or ["HEAD"]
    alterados = sorted(set(_git("diff", "--name-only", base[0]) + _git("ls-files", "--others", "--exclude-standard")))
    req = "\n".join(l for l in _git("diff", base[0], "--", "requirements.txt") if l[:1] in "+-")
    da_tela = arquivos_da_tela(alterados, req)
    if da_tela:
        print("Precisa rodar o teste visual (python -m tests.visual.rodar). Mudou na tela:")
        print("\n".join(f"  {a}" for a in da_tela))
        return 1
    print(f"Não precisa do teste visual ({len(alterados)} arquivo(s) alterado(s), nenhum da tela): só o pytest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
