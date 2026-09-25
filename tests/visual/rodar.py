"""Teste VISUAL e de CONTRATO das telas — roda à mão, antes de publicar ou de
atualizar o Streamlit. Fica fora do `pytest` normal porque precisa do app no
ar e de um navegador (Playwright + Chromium).

    python -m tests.visual.rodar              # compara com as referências
    python -m tests.visual.rodar --atualizar  # a tela atual vira referência

O que faz:
1. Monta um banco SQLite descartável com `data.seed` (semente fixa: mesmos
   dados em toda execução) e sobe o app contra ele — nunca toca em produção.
2. Percorre as telas (login, Por Loja, Por Produto, Detalhes, Dados, Pedido,
   Dashboard) em 1280 e 1920 px.
3. TABELAS: em toda tabela, os vãos entre colunas têm de ser iguais (±8 px)
   e nenhum título de coluna pode quebrar linha.
4. CONTRATO: em cada tela, confere se os seletores da estrutura interna do
   Streamlit de que o tema depende (core/theme.py, SELETORES_STREAMLIT) ainda
   encontram o elemento. Se o Streamlit mudou o HTML numa atualização, é
   aqui que aparece — com o nome da regra e a tela.
5. CAPTURA: fotografa cada tela e compara com `tests/visual/referencia/`.
   Diferença acima da tolerância = falha, com uma imagem destacando o que
   mudou em `tests/visual/resultado/`.

Mudança visual DE PROPÓSITO: rode com `--atualizar` e versione as imagens
novas de `referencia/` junto com a mudança.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import tempfile
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PASTA = Path(__file__).resolve().parent
REFERENCIA = PASTA / "referencia"
RESULTADO = PASTA / "resultado"
# Banco e arquivos do app de teste FORA do OneDrive: com o storage local
# dentro da pasta sincronizada, o OneDrive travava a pasta e a limpeza da
# execução seguinte dava "Acesso negado" (visto em 25/09/2026).
TRABALHO = Path(tempfile.gettempdir()) / "rmc_teste_visual"

LARGURAS = (1280, 1920)
ALTURA = 900
# Pixel "diferente" = algum canal muda mais que isto (0–255); tela "mudou"
# quando mais que TOLERANCIA dos pixels mudam. Medido em 25/09/2026: telas
# sem mudança variam no máximo 0,004% (antialiasing); tirar o rótulo da busca
# e trocar "1 mês" por "Último mês" mudou só 0,07–0,10% da tela — com a
# tolerância antiga (0,3%) essa mudança passava sem ser vista.
LIMIAR_CANAL = 24
TOLERANCIA = 0.0001


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chaves_do_secrets_real() -> set[str]:
    arquivo = RAIZ / ".streamlit" / "secrets.toml"
    if not arquivo.exists():
        return set()
    import tomllib
    return set(tomllib.loads(arquivo.read_text(encoding="utf-8")))


def _ambiente() -> dict:
    """Ambiente do seed e do app de teste — sem NENHUMA chave do secrets.toml
    real. Visto em 25/09/2026: importar core.* aqui fazia o Streamlit copiar
    o secrets.toml (Spaces, API interna) pro os.environ deste processo, e o
    app de teste herdava — a tela Dados mostrava "DigitalOcean Spaces" e
    "API disponível". `main()` também liga RMC_IGNORAR_SECRETS antes de
    importar core.*, pra cópia nem acontecer."""
    env = {k: v for k, v in os.environ.items() if k not in _chaves_do_secrets_real()}
    env.update({
        "RMC_IGNORAR_SECRETS": "1",  # para o data.seed (python puro) não ler o secrets.toml
        "DATABASE_URL": _BANCO,
        "LOCAL_STORAGE_DIR": _STORAGE,
        "SEED_QTD_LOJAS": "24",
        "SEED_QTD_GENERICOS": "60",
        "SEED_QTD_MESES": "3",
        "SEED_SEMENTE_ALEATORIA": "42",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


_BANCO = f"sqlite:///{(TRABALHO / 'visual.db').as_posix()}"
_STORAGE = (TRABALHO / "storage").as_posix()


def _secrets_de_teste() -> Path:
    """Secrets próprio do app de teste, passado em `--secrets.files`.

    RMC_IGNORAR_SECRETS NÃO basta sob `streamlit run`: ao subir, o Streamlit
    lê .streamlit/secrets.toml e COPIA as chaves de primeiro nível para
    os.environ, sobrescrevendo as que este script definiu (medido em
    24/09/2026: a primeira versão deste teste abriu o banco de dev do
    secrets.toml em vez do SQLite). Trocando a lista de arquivos de
    secrets, o secrets.toml real nem é lido — sem Spaces, sem Postgres."""
    arquivo = TRABALHO / "secrets_teste.toml"
    arquivo.write_text(
        f'DATABASE_URL = "{_BANCO}"\nLOCAL_STORAGE_DIR = "{_STORAGE}"\n', encoding="utf-8"
    )
    return arquivo


def _limpar(pasta: Path) -> None:
    """Esvazia a pasta sem apagá-la: no Windows (e com o OneDrive
    sincronizando) remover a própria pasta logo após o app fechar dá
    "Acesso negado" — o conteúdo sai sem problema."""
    pasta.mkdir(parents=True, exist_ok=True)
    for item in pasta.iterdir():
        shutil.rmtree(item) if item.is_dir() else item.unlink()


def _preparar_banco(env: dict) -> None:
    _limpar(TRABALHO)
    subprocess.run([sys.executable, "-m", "data.seed"], cwd=RAIZ, env=env, check=True,
                   stdout=subprocess.DEVNULL)


def _subir_app(env: dict, porta: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(porta),
         "--server.headless", "true", "--browser.gatherUsageStats", "false",
         f"--secrets.files={_secrets_de_teste()}"],
        cwd=RAIZ, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    fim = time.time() + 60
    while time.time() < fim:
        try:
            urllib.request.urlopen(f"http://localhost:{porta}/_stcore/health", timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("O app não subiu em 60 s.")


# CSS injetado só na captura: sem animação/transição e sem o canvas de
# partículas do login (anima sem parar) — senão a foto nunca sai igual.
_CSS_ESTAVEL = """
*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }
[data-testid="stIFrame"] { visibility: hidden !important; }
"""


_RODANDO = """() => !!document.querySelector('[data-testid="stStatusWidget"]')"""
_ESMAECIDOS = """() => document.querySelectorAll('[data-stale="true"]').length"""


def _estabilizar(page) -> None:
    """Espera o app terminar de rodar antes da foto. Sinal principal: sumir o
    "Running/Stop" do topo. Depois, até 5 s para sumirem os elementos
    "stale" (o que sobra, esmaecido, da tela anterior) — sem isso a foto
    saía no meio da troca de tela. Às vezes o Streamlit deixa alguns stale
    para trás com o app já parado (visto em 24/09/2026, intermitente); aí
    segue em frente em vez de travar o roteiro."""
    page.add_style_tag(content=_CSS_ESTAVEL)
    for _ in range(2):  # duas vezes: um rerun pode emendar no outro
        time.sleep(0.8)
        page.wait_for_function(f"() => !({_RODANDO})()", timeout=90_000)
    fim = time.time() + 5
    while page.evaluate(_ESMAECIDOS) and time.time() < fim:
        time.sleep(0.25)


def _abrir_detalhe(page, texto_do_popup: str, botao) -> None:
    """Abre o primeiro "Detalhes" e confirma que o popup ficou aberto. Falha
    intermitente vista em 24/09/2026: um rerun que ainda estava na fila
    fechava o popup logo depois de aberto; tenta de novo."""
    for _ in range(3):
        _estabilizar(page)
        botao.first.click()
        page.get_by_role("dialog").get_by_text(texto_do_popup).first.wait_for(timeout=60_000)
        _estabilizar(page)
        if page.get_by_role("dialog").count():
            return
    raise RuntimeError("o popup de Detalhes não ficou aberto")


def _abrir_login(page, url: str) -> None:
    page.goto(url)
    # Durante o rerun inicial o Streamlit mostra por um instante o login
    # antigo e o novo juntos (dois campos "CNPJ"); espera sobrar um só.
    page.wait_for_function(
        """() => document.querySelectorAll('input[aria-label="CNPJ"]').length === 1""",
        timeout=120_000,
    )
    # Logo depois de abrir, a leitura do cookie "lembrar-me" roda o app de
    # novo e o login é redesenhado — preencher antes disso falhava às vezes.
    _estabilizar(page)


def _entrar(page, url: str) -> None:
    _abrir_login(page, url)
    page.get_by_label("CNPJ").fill("30208213000174")
    page.get_by_label("CNPJ").press("Tab")
    # O Tab formata o CNPJ e roda o app de novo; senha digitada durante esse
    # rerun era apagada (visto em 24/09/2026: "Informe CNPJ e senha").
    _estabilizar(page)
    page.get_by_label("Senha").fill("adm123")
    page.get_by_role("button", name="Entrar").click()
    page.get_by_role("button", name="Por Loja").wait_for(timeout=120_000)


def _escolher_laboratorio(page) -> None:
    faixa = page.locator('[class*="st-key-faixa-laboratorio"]')
    faixa.wait_for(timeout=60_000)
    if faixa.locator("input").input_value() == "":
        faixa.locator("input").click()
        page.get_by_role("option").first.click()
    page.locator(".linha.corpo").first.wait_for(timeout=120_000)  # tabela (views/tabela.py)


def _telas(page, url):
    """Gera (nome, mascaras) com a página já na tela correspondente."""
    _abrir_login(page, url)
    yield "login", []
    _entrar(page, url)

    page.get_by_role("button", name="Por Loja").click()
    _escolher_laboratorio(page)
    yield "por_loja", []
    _abrir_detalhe(page, "Produtos comprados", page.locator("button.acao"))
    yield "por_loja_detalhe", []
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Por Produto").click()
    _escolher_laboratorio(page)
    yield "por_produto", []
    _abrir_detalhe(page, "Genérico", page.locator("button.acao"))
    yield "por_produto_detalhe", []
    page.keyboard.press("Escape")

    for secao in ("Pedido", "Dashboard"):
        page.get_by_role("button", name=secao).click()
        page.locator('[class*="st-key-tela-"]').first.wait_for(timeout=60_000)
        yield secao.lower(), []

    page.get_by_role("button", name="Dados").click()
    page.get_by_role("tab", name="Explorador de Arquivos").wait_for(timeout=120_000)
    yield "dados_explorador", []
    page.get_by_role("tab", name="Importar Planilhas").click()
    page.get_by_text("Compras das Lojas (GPS)").first.wait_for(timeout=60_000)
    # Mês/ano padrão = data de hoje: mascarados, senão a foto muda todo mês.
    yield "dados_importar", [page.get_by_test_id("stSelectbox").filter(has_text="Mês de referência"),
                             page.get_by_test_id("stNumberInput")]
    page.get_by_role("tab", name="Fila de Resolução de EAN").click()
    yield "dados_fila_ean", []
    page.get_by_role("tab", name="Fila de CNPJ Órfão").click()
    yield "dados_fila_cnpj", []


def _comparar(atual: Path, referencia: Path, diferenca: Path) -> tuple[bool, float]:
    from PIL import Image, ImageChops

    a = Image.open(atual).convert("RGB")
    r = Image.open(referencia).convert("RGB")
    if a.size != r.size:
        return False, 1.0
    diff = ImageChops.difference(a, r)
    mascara = diff.point(lambda v: 255 if v > LIMIAR_CANAL else 0).convert("L")
    mudou = mascara.histogram()[255] / (a.size[0] * a.size[1])
    if mudou > TOLERANCIA:
        destaque = a.copy()
        destaque.paste((230, 0, 110), mask=mascara)
        destaque.save(diferenca)
    return mudou <= TOLERANCIA, mudou


# Regra das tabelas (views/tabela.py), medida em toda tela que tiver uma:
# vãos iguais entre as colunas e nenhum título quebrando linha. Pedido de
# 25/09/2026, depois de um vão enorme entre "Responsável" e "Produtos".
# 8 px: nome longo que quebra linha deixa a borda direita "serrilhada" alguns
# px antes do fim da coluna (medido: +5 a +6 px na Por Loja real). O buraco
# que motivou a regra tinha mais de 100 px.
VAO_DIFERENCA_MAX_PX = 8
# O vão medido é entre o que APARECE: a borda do conteúdo mais à direita de
# uma coluna (texto, ícone, selo — em qualquer linha, título incluído) até a
# borda do conteúdo mais à esquerda da seguinte. Medir a caixa das colunas
# não serve: na versão com pesos fixos as caixas tinham vão constante de
# 16 px e mesmo assim sobrava um buraco visível entre "Responsável" e
# "Produtos" (a primeira versão desta checagem passou nela — 25/09/2026).
_JS_TABELAS = r"""quadros => quadros.map(quadro => {
  const linhas = [...quadro.children].filter(l => l.classList.contains('linha'));
  const ncol = linhas.length ? linhas[0].children.length : 0;
  const extensao = Array.from({length: ncol}, () => [Infinity, -Infinity]);
  const somar = (i, r) => { if (r.width > 0) { extensao[i][0] = Math.min(extensao[i][0], r.left); extensao[i][1] = Math.max(extensao[i][1], r.right); } };
  for (const linha of linhas) {
    [...linha.children].forEach((cel, i) => {
      const it = document.createTreeWalker(cel, NodeFilter.SHOW_TEXT);
      for (let n = it.nextNode(); n; n = it.nextNode()) {
        if (!n.textContent.trim() || n.parentElement.closest('.seta')) continue;
        const rg = document.createRange(); rg.selectNodeContents(n); somar(i, rg.getBoundingClientRect());
      }
      cel.querySelectorAll('svg:not(.seta), [class*="selo-"], button.acao').forEach(e => somar(i, e.getBoundingClientRect()));
    });
  }
  const cols = extensao.filter(e => e[1] > -Infinity);
  const vaos = cols.slice(1).map((e, i) => Math.round(e[0] - cols[i][1]));
  const cab = linhas[0];
  const quebrados = [...cab.querySelectorAll('.titulo')].filter(t => {
    const alt = parseFloat(getComputedStyle(t).lineHeight) || 19;
    return t.getBoundingClientRect().height > alt * 1.5;
  }).map(t => t.innerText.trim());
  const titulos = [...cab.querySelectorAll('.titulo')].map(t => t.innerText.trim());
  return {titulos, vaos, quebrados};
})"""


def _verificar_tabelas(page, tela: str, largura: int, falhas: list[str]) -> None:
    for tabela in page.locator(".rmc-tabela .quadro").evaluate_all(_JS_TABELAS):
        if not tabela["titulos"]:
            continue  # tabela vazia ("Nenhuma oportunidade encontrada")
        nome = " | ".join(tabela["titulos"][:3]) + " …"
        vaos = tabela["vaos"]
        if vaos and max(vaos) - min(vaos) > VAO_DIFERENCA_MAX_PX:
            falhas.append(f"{tela} @ {largura}px, tabela [{nome}]: vãos desiguais entre colunas {vaos}")
        if tabela["quebrados"]:
            falhas.append(f"{tela} @ {largura}px, tabela [{nome}]: título quebrado {tabela['quebrados']}")


def _registrar(page, tela, largura, mascaras, atualizar, falhas_contrato, conferidos, falhas_visuais,
               falhas_layout) -> None:
    _verificar_tabelas(page, tela, largura, falhas_layout)
    from core.theme import SELETORES_STREAMLIT

    for nome, (seletor, tela_contrato) in SELETORES_STREAMLIT.items():
        if tela_contrato == tela:
            conferidos.add(nome)
            if page.locator(seletor).count() == 0:
                falhas_contrato.append(f"{nome} ({seletor}) não encontrado na tela {tela} @ {largura}px")
    arquivo = f"{tela}_{largura}.png"
    atual = RESULTADO / arquivo
    page.screenshot(path=str(atual), mask=mascaras, mask_color="#9AA3AF")
    if atualizar:
        shutil.copy(atual, REFERENCIA / arquivo)
        return
    if not (REFERENCIA / arquivo).exists():
        falhas_visuais.append(f"{arquivo}: sem referência (rode com --atualizar)")
        return
    ok, mudou = _comparar(atual, REFERENCIA / arquivo, RESULTADO / f"DIFERENCA_{arquivo}")
    if not ok:
        falhas_visuais.append(f"{arquivo}: {mudou:.2%} dos pixels mudaram (ver DIFERENCA_{arquivo})")


def main() -> int:
    os.environ["RMC_IGNORAR_SECRETS"] = "1"  # antes de qualquer import de core.* (ver _ambiente)
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(RAIZ))
    from core.theme import SELETORES_STREAMLIT

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--atualizar", action="store_true", help="grava as telas atuais como referência")
    args = parser.parse_args()

    env = _ambiente()
    print("Montando banco de teste (seed)...")
    _preparar_banco(env)
    porta = _porta_livre()
    print(f"Subindo o app na porta {porta}...")
    app = _subir_app(env, porta)
    url = f"http://localhost:{porta}"

    REFERENCIA.mkdir(exist_ok=True)
    _limpar(RESULTADO)
    falhas_contrato: list[str] = []
    conferidos: set[str] = set()
    falhas_visuais: list[str] = []
    falhas_layout: list[str] = []
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            for largura in LARGURAS:
                page = navegador.new_page(viewport={"width": largura, "height": ALTURA})
                tela = "inicio"
                try:
                    for tela, mascaras in _telas(page, url):
                        _estabilizar(page)
                        _registrar(page, tela, largura, mascaras, args.atualizar,
                                   falhas_contrato, conferidos, falhas_visuais, falhas_layout)
                except Exception as erro:
                    # Foto de onde travou: sem ela, só sobra um timeout genérico.
                    page.screenshot(path=str(RESULTADO / f"ERRO_depois_de_{tela}_{largura}.png"))
                    raise RuntimeError(f"Roteiro parou depois da tela '{tela}' @ {largura}px: {erro}") from erro
                page.close()
            navegador.close()
    finally:
        app.terminate()
        app.wait(timeout=30)  # solta o visual.db antes da próxima execução

    sem_tela = sorted(set(SELETORES_STREAMLIT) - conferidos)
    print("\n== CONTRATO com a estrutura do Streamlit ==")
    print("OK" if not falhas_contrato else "\n".join(f"FALHOU: {f}" for f in falhas_contrato))
    if sem_tela:
        print(f"(não conferidos neste roteiro — sem tela garantida: {', '.join(sem_tela)})")
    print("\n== TABELAS (vãos iguais entre colunas, títulos numa linha) ==")
    print("OK" if not falhas_layout else "\n".join(f"FALHOU: {f}" for f in falhas_layout))
    print("\n== CAPTURAS ==")
    if args.atualizar:
        print(f"Referências atualizadas em {REFERENCIA}")
    else:
        print("OK — nenhuma tela mudou." if not falhas_visuais else "\n".join(f"MUDOU: {f}" for f in falhas_visuais))
    return 1 if (falhas_contrato or falhas_visuais or falhas_layout) else 0


if __name__ == "__main__":
    raise SystemExit(main())
