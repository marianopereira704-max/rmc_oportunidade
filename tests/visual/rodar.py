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
   e nenhum título de coluna pode quebrar linha. ASSISTENTE DE PEDIDO (loja
   fictícia, tests/visual/loja_ficticia.py — com personalização): o pop-up
   de alerta ao abrir a loja, o rótulo "Regras desta loja" e a lista.
4. CONTRATO: em cada tela, confere se os seletores da estrutura interna do
   Streamlit de que o tema depende (core/theme.py, SELETORES_STREAMLIT) ainda
   encontram o elemento. Se o Streamlit mudou o HTML numa atualização, é
   aqui que aparece — com o nome da regra e a tela.
5. CAPTURA: fotografa cada tela e compara com `tests/visual/referencia/`.
   Diferença acima da tolerância = falha, com uma imagem destacando o que
   mudou em `tests/visual/resultado/`.
6. CÁLCULO DE MADRUGADA (29/10/2026): publica as fotos dos insumos, roda
   `pedido.precalculo`, esconde os dados brutos da loja fictícia e percorre
   de novo o Assistente e o Dashboard, agora pelo resultado PRONTO — as
   capturas (`pronto_*`) têm de ser iguais às MESMAS referências.

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
        # Por Loja (compras da rede, 05/10/2026): "hoje" fixo — "Último mês"
        # é sempre agosto/2026 (tests/visual/compras_rede.py).
        "RMC_HOJE": "2026-10-06",
        # Pop-up Listas (29/10/2026): a prévia do "Salvar como lista" mostra a hora de agora.
        "RMC_AGORA": "2026-09-27T09:00",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


_BANCO = f"sqlite:///{(TRABALHO / 'visual.db').as_posix()}"
_STORAGE = (TRABALHO / "storage").as_posix()


def _secrets_de_teste(chave_precalculo: str | None = None) -> Path:
    """Secrets próprio do app de teste, passado em `--secrets.files`.

    RMC_IGNORAR_SECRETS NÃO basta sob `streamlit run`: ao subir, o Streamlit
    lê .streamlit/secrets.toml e COPIA as chaves de primeiro nível para
    os.environ, sobrescrevendo as que este script definiu (medido em
    24/09/2026: a primeira versão deste teste abriu o banco de dev do
    secrets.toml em vez do SQLite). Trocando a lista de arquivos de
    secrets, o secrets.toml real nem é lido — sem Spaces, sem Postgres."""
    arquivo = TRABALHO / "secrets_teste.toml"
    texto = f'DATABASE_URL = "{_BANCO}"\nLOCAL_STORAGE_DIR = "{_STORAGE}"\n'
    if chave_precalculo:   # 2ª volta (cálculo de madrugada): o app lê o pronto
        texto += f'PRECALCULO_CHAVE = "{chave_precalculo}"\n'
    arquivo.write_text(texto, encoding="utf-8")
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
    # Loja com dados do Pedido: sem ela o pop-up de filtros nem abre.
    subprocess.run([sys.executable, "-m", "tests.visual.loja_ficticia"], cwd=RAIZ, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    # Compras da rede (Por Loja): o seed não cria o arquivo da rede.
    subprocess.run([sys.executable, "-m", "tests.visual.compras_rede"], cwd=RAIZ, env=env, check=True,
                   stdout=subprocess.DEVNULL)


def _subir_app(env: dict, porta: int, chave_precalculo: str | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(porta),
         "--server.headless", "true", "--browser.gatherUsageStats", "false",
         f"--secrets.files={_secrets_de_teste(chave_precalculo)}"],
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


# Cálculo de madrugada (pedido/precalculo.py, 29/10/2026): a 2ª volta do Assistente e do Dashboard lê o resultado
# PRONTO e tem que sair igual às referências do cálculo na hora.
_CHAVE_PRECALCULO = "chave-do-teste-visual"
_PUBLICAR_INSUMOS = "from core.db import get_session\nfrom integrations import insumos\nwith get_session() as s:\n" \
                    "    insumos.publicar(s)\n"


def _preparar_pronto(env: dict) -> None:
    """Publica as fotos dos insumos (com o banco como a 1ª volta deixou: o "Considerar como zero" do pop-up gravou
    correções), roda o pré-cálculo e ESCONDE os dados brutos da loja fictícia — fica só o marcador da foto de
    estoque, que entra na assinatura. Se o app tentasse calcular na hora, não teria com o que, e a captura mudaria:
    é a prova de que a 2ª volta leu o pronto, sem gancho nenhum no código do app."""
    from tests.visual import loja_ficticia as lf

    env = {**env, "PRECALCULO_CHAVE": _CHAVE_PRECALCULO}
    subprocess.run([sys.executable, "-c", _PUBLICAR_INSUMOS], cwd=RAIZ, env=env, check=True)
    r = subprocess.run([sys.executable, "-m", "pedido.precalculo"], cwd=RAIZ, env=env, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode != 0 or "telas calculadas 4," not in r.stdout:
        raise RuntimeError(f"Pré-cálculo da loja fictícia não fez as 4 telas: {r.stdout.strip()} {r.stderr[-800:]}")
    storage = Path(_STORAGE)
    escondidos = TRABALHO / "brutos_escondidos"
    for pasta in (storage / "pedido" / "bruto" / lf.EMPRESA, storage / "pedido" / "historico_estoque" / lf.EMPRESA,
                  storage / "pedido" / "pronto" / lf.EMPRESA):
        for arq in [a for a in pasta.rglob("*") if a.is_file() and a.name != "_estoque.json"]:
            destino = escondidos / arq.relative_to(storage)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(arq), destino)
    if not any(escondidos.rglob("*.parquet")):
        raise RuntimeError("Nenhum dado bruto da loja fictícia para esconder: a 2ª volta não provaria nada.")


def _telas_pronto(page, url):
    """2ª volta: entra e percorre o Assistente (escolhendo a loja fictícia) e o Dashboard. Os pop-ups ao abrir a loja
    já foram respondidos na 1ª volta (mesmo banco)."""
    _abrir_login(page, url)
    _entrar(page, url)
    yield from _telas_assistente(page)
    yield from _telas_dashboard(page)


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
    # antigo e o novo juntos (dois campos "E-mail ou CNPJ"); espera sobrar um só.
    page.wait_for_function(
        """() => document.querySelectorAll('input[aria-label="E-mail ou CNPJ"]').length === 1""",
        timeout=120_000,
    )
    # Logo depois de abrir, a leitura do cookie "lembrar-me" roda o app de
    # novo e o login é redesenhado — preencher antes disso falhava às vezes.
    _estabilizar(page)


def _entrar(page, url: str) -> None:
    _abrir_login(page, url)
    page.get_by_label("E-mail ou CNPJ").fill("30208213000174")
    page.get_by_label("E-mail ou CNPJ").press("Tab")
    # O Tab formata o CNPJ e roda o app de novo; senha digitada durante esse
    # rerun era apagada (visto em 24/09/2026: "Informe CNPJ e senha").
    _estabilizar(page)
    page.get_by_label("Senha").fill("adm123")
    page.get_by_role("button", name="Entrar").click()
    page.get_by_role("button", name="Por Loja").wait_for(timeout=120_000)


def _escolher_laboratorio(page) -> None:
    faixa = page.locator('[class*="st-key-faixa-laboratorio"]')
    faixa.wait_for(timeout=60_000)
    # Logo depois do clique na barra a tela anterior ainda está na página: a
    # escolha caía nela e se perdia (Por Produto, 05/10/2026).
    _estabilizar(page)
    if faixa.locator("input").input_value() == "":
        faixa.locator("input").click()
        page.get_by_role("option").first.click()
    page.locator(".linha.corpo").first.wait_for(timeout=120_000)  # tabela (views/tabela.py)


_DIALOGO = 'div[data-testid="stDialog"] section[role="dialog"]'


def _popup(page, abrir, nome: str, fechar: str = "Escape"):
    """Abre um pop-up, entrega a captura e fecha (ESC ou pelo botão `fechar`
    — formulário longo não fecha por ESC, Q14 de 07/10/2026)."""
    abrir.click()
    dialogo = page.locator(_DIALOGO)
    dialogo.wait_for(timeout=60_000)
    _estabilizar(page)
    yield nome, []
    if fechar == "Escape":
        page.keyboard.press("Escape")
    else:
        dialogo.get_by_role("button", name=fechar, exact=True).click()
    dialogo.wait_for(state="detached", timeout=30_000)
    _estabilizar(page)


def _telas(page, url):
    """Gera (nome, mascaras) com a página já na tela correspondente."""
    _abrir_login(page, url)
    yield "login", []
    _entrar(page, url)

    page.get_by_role("button", name="Por Loja").click()
    _escolher_laboratorio(page)
    yield "por_loja", []
    _abrir_detalhe(page, "Compras desta loja", page.locator("button.acao"))
    yield "por_loja_detalhe", []
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Por Produto").click()
    _escolher_laboratorio(page)
    yield "por_produto", []
    _abrir_detalhe(page, "Notas fiscais do período", page.locator("button.acao"))
    yield "por_produto_detalhe", []
    page.keyboard.press("Escape")

    # Pela chave do botão (st-key-nav_<seção>), não pelo nome: o nome
    # acessível inclui o ícone e "Pedido" também casava com "Configurações
    # de Pedidos" (28/09/2026).
    # Espera a tela PEDIDA (st-key-tela-<nome>), não "qualquer tela": logo
    # depois do clique a anterior (Por Produto) ainda está na página, e a
    # captura saía dela às vezes (05/10/2026).
    for secao, tela in (("pedido", "pedido"), ("config_pedido", "config-pedido")):
        page.locator(f'[class*="st-key-nav_{secao}"] button').click()
        page.locator(f'[class*="st-key-tela-{tela}"]').first.wait_for(timeout=60_000)
        _estabilizar(page)
        yield secao, []
    # Dica (22/10/2026, core/dicas.py): o balão do help= das Configurações, com o mesmo visual do balão dos
    # cards e da tabela. Só aparece com o mouse em cima do "?".
    page.locator('[data-testid="stTooltipIcon"]').first.scroll_into_view_if_needed()
    page.locator('[data-testid="stTooltipIcon"]').first.hover()
    page.locator('[data-testid="stTooltipContent"]').first.wait_for(timeout=30_000)
    _estabilizar(page)
    yield "config_pedido_dica", []
    page.mouse.move(1, 1)
    page.locator('[data-testid="stTooltipContent"]').first.wait_for(state="detached", timeout=30_000)
    page.evaluate("window.scrollTo(0, 0)")
    # Configurações → Classificação de produtos (25/10/2026, no lugar da tabela "Linhas de compra"): a seção
    # fica no fim da página; a foto alinha o título dela ao topo. Depois, a 1ª categoria aberta (a setinha no
    # fim da barra) e o pop-up "Editar correspondência".
    page.get_by_text("Classificação de produtos", exact=True).first.evaluate("e => e.scrollIntoView({block: 'start'})")
    page.locator(".rmc-cascata").first.wait_for(timeout=60_000)
    _estabilizar(page)
    yield "config_pedido_classificacao", []
    page.locator(".rmc-cascata > details > summary").first.click()
    page.locator(".rmc-cascata > details[open] .filhos").first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "config_pedido_classificacao_aberta", []
    yield from _popup(page, page.locator('[class*="st-key-classificacao_produtos_editar"] button'),
                      "config_pedido_correspondencia")

    yield from _telas_assistente(page)

    yield from _telas_dashboard(page)

    # Aba Dados reorganizada em 29/09/2026: Pendências / Importar / Rotinas / Explorador (o aviso "foi
    # reorganizada" saiu em 29/10/2026).
    page.locator('[class*="st-key-nav_dados"] button').click()
    page.get_by_role("tab", name="Pendências").wait_for(timeout=120_000)
    page.get_by_text("Fila de Resolução de EAN").first.wait_for(timeout=60_000)
    yield "dados_fila_ean", []
    # A fila de CNPJ órfão saiu em 05/10/2026 (compras pela API).
    for texto, nome in (("Vínculo de lojas GPS", "dados_vinculo"), ("Sem Classificação", "dados_sem_classificacao")):
        page.get_by_test_id("stButtonGroup").get_by_text(texto).click()
        page.locator('[data-testid="stMarkdownContainer"]').filter(has_text="O que é:").first.wait_for(timeout=60_000)
        yield nome, []
    page.get_by_role("tab", name="Importar").click()
    # O envio da planilha do GPS (com mês/ano mascarados) saiu em 05/10/2026.
    page.get_by_text("Base Genéricos (planilha curada)").first.wait_for(timeout=60_000)
    yield "dados_importar", []
    page.get_by_role("tab", name="Rotinas").click()
    page.get_by_text("Rotina do GPS").first.wait_for(timeout=60_000)
    yield "dados_rotinas", []
    page.get_by_role("tab", name="Explorador de Arquivos").click()
    yield "dados_explorador", []

    # Cadastro (07/10/2026): a loja da barra continua a fictícia (escolhida
    # no Assistente), com dois usuários (tests/visual/loja_ficticia.py).
    page.locator('[class*="st-key-nav_cadastro"] button').click()
    page.locator('[class*="st-key-cadastro-linha-"]').first.wait_for(timeout=60_000)
    _estabilizar(page)
    yield "cadastro", []
    page.locator('[class*="st-key-cadastro_novo"] button').click()
    page.get_by_role("dialog").get_by_text("Identificação").first.wait_for(timeout=60_000)
    _estabilizar(page)
    yield "cadastro_novo", []
    page.get_by_role("dialog").get_by_role("button", name="Cancelar", exact=True).click()
    page.locator(_DIALOGO).wait_for(state="detached", timeout=30_000)
    linha = page.locator('[class*="st-key-cadastro-linha-"]').filter(has_text="Carlos Souza")
    yield from _popup(page, linha.locator('[class*="st-key-cadastro_editar_"] button:visible').first, "cadastro_editar",
                      fechar="Cancelar")
    yield from _popup(page, linha.locator('[class*="st-key-cadastro_paineis_"] button:visible').first, "cadastro_paineis")
    yield from _popup(page, linha.locator('[class*="st-key-cadastro_empresas_"] button:visible').first, "cadastro_empresas")
    yield from _popup(page, linha.locator('[class*="st-key-cadastro_email_"] button:visible').first, "cadastro_email")
    yield from _popup(page, linha.locator('[class*="st-key-cadastro_excluir_"] button:visible').first, "cadastro_excluir")
    # Celular (Q8): uma vez só, na volta de 1280 px — o pop-up ocupa a tela
    # menos 32 px.
    largura_original = page.viewport_size["width"]
    if largura_original == LARGURAS[0]:
        page.set_viewport_size({"width": 390, "height": 844})
        _estabilizar(page)
        yield from _popup(page, page.locator('[class*="st-key-cadastro_novo"] button'), "celular_cadastro_novo",
                          fechar="Cancelar")
        yield from _popup(page, linha.locator('[class*="st-key-cadastro_excluir_"] button:visible').first,
                          "celular_cadastro_excluir")
        page.set_viewport_size({"width": largura_original, "height": ALTURA})
        _estabilizar(page)

    # Usuário de loja (etapa 4, 07/10/2026): Ana, Compradora de Genéricos e
    # Similares só com o painel do Assistente — a barra mostra só ele, a loja
    # vem fixa e a lista sai cortada pelas linhas dela.
    page.locator('[class*="st-key-nav_sair"] button').click()
    _abrir_login(page, url)
    page.get_by_label("E-mail ou CNPJ").fill("ana.compras@ficticia.com.br")
    page.get_by_label("Senha").fill("senha-teste-1")
    page.get_by_role("button", name="Entrar").click()
    page.locator('[class*="st-key-tela-pedido"]').first.wait_for(timeout=120_000)
    dialogo = page.locator('div[data-testid="stDialog"] section[role="dialog"]')
    parametros = page.locator('[class*="st-key-pedido-parametros"]')
    dialogo.or_(parametros).first.wait_for(timeout=120_000)
    if dialogo.count():
        page.get_by_role("button", name="Considerar como zero").click()
        page.get_by_role("button", name="Entendi").click()
        dialogo.wait_for(state="detached", timeout=60_000)
    parametros.wait_for(timeout=60_000)
    page.locator(".linha.corpo").first.wait_for(timeout=120_000)
    _estabilizar(page)
    yield "pedido_comprador", []


def _telas_assistente(page):
    """Assistente de pedido com a loja fictícia; também a 2ª volta, pelo pronto (`_telas_pronto`)."""
    # Assistente de pedido com a loja fictícia (01/10/2026): ao abrir a loja,
    # os pop-ups de alerta (estoque negativo → sem classificação) e a lista, com o rótulo "Regras desta loja"
    # (as pílulas Personalizado × Padrão saíram em 29/10/2026).
    # A loja é escolhida na barra lateral desde 02/10/2026.
    page.locator('[class*="st-key-nav_pedido"] button').click()
    campo = page.locator('[class*="st-key-loja_barra"] input')
    campo.wait_for(timeout=60_000)
    campo.click()
    page.keyboard.type("FICTÍCIA")
    page.get_by_role("option").first.click()
    # Os pop-ups aparecem uma vez por foto do GPS por usuário: na 2ª largura
    # (mesmo banco) já foram respondidos e a tela vai direto à lista.
    dialogo = page.locator('div[data-testid="stDialog"] section[role="dialog"]')
    parametros = page.locator('[class*="st-key-pedido-parametros"]')
    dialogo.or_(parametros).first.wait_for(timeout=120_000)
    if dialogo.count():
        yield "pedido_alerta", []
        page.get_by_role("button", name="Considerar como zero").click()
        # 2º alerta (só informa) — também na régua dos pop-ups.
        dialogo.get_by_role("button", name="Entendi").wait_for(timeout=60_000)
        _estabilizar(page)
        yield "pedido_alerta_sem", []
        page.get_by_role("button", name="Entendi").click()
        dialogo.wait_for(state="detached", timeout=60_000)
    parametros.wait_for(timeout=60_000)
    page.locator(".linha.corpo").first.wait_for(timeout=120_000)
    # Sem os pop-ups (já respondidos — 2ª largura, 2ª volta pelo pronto), o mouse fica onde a loja foi escolhida, em
    # cima de uma célula: a dica dela entrava na foto (29/10/2026).
    page.mouse.move(1, 1)
    _estabilizar(page)
    yield "pedido_loja", []
    # Uma categoria escolhida: a etiqueta do multiselect com a cara dos chips.
    # A 1ª opção da lista é "Select all" do Streamlit — pega a 2ª.
    # Com nova tentativa: clicado enquanto a tela ainda recalculava (logo
    # depois de abrir a loja), o menu fechava sem escolher (visto em 1920 px).
    etiqueta = page.locator('[data-testid="stMultiSelectTagsContainer"] span[role="group"]')
    for _ in range(3):
        _estabilizar(page)
        page.locator('[class*="st-key-pedido_cat_"] input').click()
        page.get_by_role("option").nth(1).wait_for(timeout=30_000)
        page.get_by_role("option").nth(1).click()
        # Espera a tela assentar ANTES do ESC (08/10/2026): o ESC no meio do
        # recálculo da escolha fazia ela se perder — a falha intermitente
        # deste passo, duas vezes seguidas no mesmo dia.
        _estabilizar(page)
        try:
            etiqueta.first.wait_for(timeout=15_000)
            page.keyboard.press("Escape")
            break
        except Exception:  # noqa: BLE001 — tenta de novo
            continue
    etiqueta.first.wait_for(timeout=15_000)
    _estabilizar(page)
    yield "pedido_filtrado", []
    # Pop-ups do Assistente (07/10/2026): "Saiba mais", Listas e Exportar.
    yield from _popup(page, page.locator('[class*="st-key-pedido_saiba_mais"] button'), "pedido_saiba_mais")
    yield from _popup(page, page.get_by_role("button", name="Listas"), "pedido_listas")
    yield from _popup(page, page.get_by_role("button", name="Exportar"), "pedido_exportar")


def _telas_dashboard(page):
    """As 3 páginas do Dashboard, com a loja da barra já escolhida; também a 2ª volta, pelo pronto."""
    # Dashboard (21/10/2026). Vem DEPOIS do Assistente de propósito: precisa de uma loja escolhida na
    # barra lateral, e escolhê-la antes mudaria a barra de todas as capturas anteriores. O menu começa
    # recolhido: abre o pai (`.st-key-nav_dashboard` é a classe EXATA do pai; o [class*=] casaria
    # também as filhas). Três páginas desde 22/10/2026: Estoque (nova, 22/10/2026),
    # Ruptura (a tela de 21/10/2026, sem mudança) e Compras (em construção).
    page.locator(".st-key-nav_dashboard button").click()
    page.locator(".st-key-nav_dashboard_estoque button").wait_for(timeout=60_000)
    page.locator(".st-key-nav_dashboard_estoque button").click()
    page.locator('[class*="st-key-tela-dashboard-estoque"]').first.wait_for(timeout=60_000)
    _estabilizar(page)
    page.locator(".linha.corpo.clicavel").first.wait_for(timeout=120_000)
    page.mouse.move(1, 1)
    yield "dashboard_estoque", []
    # Dica do card "Valor em excesso" (22/10/2026): o balão único do app, com dois parágrafos.
    page.locator(".rmc-kpi").nth(2).hover()
    _estabilizar(page)
    yield "dashboard_estoque_dica", []
    page.mouse.move(1, 1)
    # Filtro da página: clicar numa linha da análise por categoria (a linha fica, o resto escurece e os
    # cards, o gráfico e os críticos refazem); depois a tabela de críticos alinhada ao topo e o detalhe.
    page.locator(".linha.corpo.clicavel").first.click()
    page.locator(".linha.corpo.selecionada").first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_estoque_filtrado", []
    page.locator(".linha.corpo.selecionada").first.click()   # clicar de novo no único selecionado tira o filtro
    page.locator(".linha.corpo.selecionada").first.wait_for(state="detached", timeout=30_000)
    _estabilizar(page)
    # Análise por categoria (26/10/2026): a setinha no fim da 1ª categoria abre o detalhamento (IQVIA → FEBRAFAR →
    # CMED) sem rodar o script; fecha de novo para as capturas seguintes.
    page.locator("button.abrir").first.click()
    page.locator(".linha.corpo.sub:not(.oculta)").first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_estoque_detalhamento", []
    page.locator("button.abrir").first.click()
    page.locator(".linha.corpo.sub:not(.oculta)").first.wait_for(state="detached", timeout=30_000)
    _estabilizar(page)
    page.get_by_text("Produtos críticos", exact=True).first.evaluate("e => e.scrollIntoView({block: 'start'})")
    _estabilizar(page)
    yield "dashboard_estoque_criticos", []
    yield from _popup(page, page.locator("button.acao").first, "dashboard_estoque_detalhe")
    page.locator(".st-key-nav_dashboard_ruptura button").click()
    page.locator('[class*="st-key-tela-dashboard-ruptura"]').first.wait_for(timeout=60_000)
    page.locator(".barra").first.wait_for(timeout=120_000)
    page.locator(".linha.corpo.clicavel").first.wait_for(timeout=120_000)
    _estabilizar(page)
    page.mouse.move(1, 1)   # sem o mouse em cima de uma barra: a dica dela entraria na foto
    yield "dashboard_ruptura", []
    # Página nova (27/10/2026): sem chips de filtro — a barra escolhida fica marcada (aria-pressed) e o resto
    # escurece. Uma barra (clique), depois duas (Ctrl+clique), depois tira (clique na única escolhida).
    page.locator('.barra[data-chave="A"]').first.click()
    page.locator('.barra[data-chave="A"][aria-pressed="true"]').first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_ruptura_filtrado", []
    page.locator('.barra[data-chave="B"]').first.click(modifiers=["Control"])
    page.locator('.barra[data-chave="B"][aria-pressed="true"]').first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_ruptura_filtrado_duas", []
    page.locator('.barra[data-chave="B"]').first.click()          # sobra só a B
    page.locator('.barra[data-chave="A"][aria-pressed="true"]').first.wait_for(state="detached", timeout=30_000)
    page.locator('.barra[data-chave="B"]').first.click()          # clique na única escolhida: tira o filtro
    page.locator('.barra[aria-pressed="true"]').first.wait_for(state="detached", timeout=30_000)
    _estabilizar(page)
    # Ruptura por categoria: a setinha abre IQVIA › FEBRAFAR › CMED; fecha de novo para as fotos seguintes.
    page.locator("button.abrir").first.click()
    page.locator(".linha.corpo.sub:not(.oculta)").first.wait_for(timeout=30_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_ruptura_detalhamento", []
    page.locator("button.abrir").first.click()
    page.locator(".linha.corpo.sub:not(.oculta)").first.wait_for(state="detached", timeout=30_000)
    _estabilizar(page)
    # A tabela começa na dobra: a foto alinha o título dela ao topo (o
    # `scroll_into_view_if_needed` não rola quando o título já aparece embaixo).
    page.get_by_text("Produtos críticos", exact=True).first.evaluate("e => e.scrollIntoView({block: 'start'})")
    _estabilizar(page)
    yield "dashboard_ruptura_tabela", []
    yield from _popup(page, page.locator("button.acao").first, "dashboard_ruptura_detalhe")
    page.locator(".st-key-nav_dashboard_compras button").click()
    page.locator('[class*="st-key-tela-dashboard-compras"]').first.wait_for(timeout=60_000)
    # Página nova (27/10/2026): pílulas de período, cards, compras por categoria, distribuição, dotação × consumido
    # (gráfico do plotly) e os produtos não otimizados.
    page.locator(".barra").first.wait_for(timeout=120_000)
    page.locator(".linha.corpo.clicavel").first.wait_for(timeout=120_000)
    page.locator(".js-plotly-plot").first.wait_for(timeout=60_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_compras", []
    page.get_by_text("Produtos com compra não otimizada", exact=True).first.evaluate("e => e.scrollIntoView({block: 'start'})")
    _estabilizar(page)
    yield "dashboard_compras_produtos", []
    yield from _popup(page, page.locator("button.acao").first, "dashboard_compras_detalhe")
    page.evaluate("window.scrollTo(0, 0)")
    page.locator('[class*="st-key-dash-compras-periodo"]').get_by_text("Últimos 3 meses").click()
    page.get_by_text("Julho a setembro/2026", exact=False).first.wait_for(timeout=60_000)
    _estabilizar(page)
    page.mouse.move(1, 1)
    yield "dashboard_compras_tres_meses", []


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


# Eixos e espaço (29/10/2026, pedido do Mariano ao trocar os textos do Assistente: "não quebra a continuidade do
# cabeçalho, fica dentro dos espaços documentados, não perde o eixo x e y"). Em toda tabela e todo card:
# - ESPAÇO: a página não rola na horizontal; a tabela não passa de LARGURA_MAXIMA_PX; nenhuma célula vaza
#   (conteúdo mais largo que a própria coluna invade o vão);
# - EIXO X: o título de cada coluna alinha com o conteúdo das linhas, pelo lado da coluna (esquerda, centro ou
#   direita — `.cel.centro`/`.cel.direita`);
# - EIXO Y: em cada linha, o conteúdo de toda célula tem o mesmo centro vertical (`align-items: center`);
# - etiqueta (`selo-*`) numa linha só; título de card em até 2 linhas e sem vazar.
EIXO_TOLERANCIA_PX = 3
_JS_EIXOS = r"""(larguraMax) => {
  const out = {rolagem: document.documentElement.scrollWidth - innerWidth, tabelas: [], cards: []};
  const caixa = (el) => {
    let r = null;
    const it = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const juntar = (b) => { if (b.width <= 0 || b.height <= 0) return;
      r = r ? {l: Math.min(r.l, b.left), r: Math.max(r.r, b.right), t: Math.min(r.t, b.top), b: Math.max(r.b, b.bottom)}
            : {l: b.left, r: b.right, t: b.top, b: b.bottom}; };
    for (let n = it.nextNode(); n; n = it.nextNode()) {
      if (!n.textContent.trim() || n.parentElement.closest('.seta')) continue;
      const rg = document.createRange(); rg.selectNodeContents(n); juntar(rg.getBoundingClientRect());
    }
    el.querySelectorAll('svg:not(.seta), [class*="selo-"], button.acao, input').forEach(e => juntar(e.getBoundingClientRect()));
    return r;
  };
  for (const quadro of document.querySelectorAll('.rmc-tabela .quadro')) {
    const linhas = [...quadro.children].filter(l => l.classList.contains('linha') && l.offsetParent !== null);
    if (linhas.length < 2) continue;
    const cab = linhas[0], corpo = linhas.slice(1);
    const titulos = [...cab.children].map(c => c.innerText.trim());
    const t = {nome: titulos.filter(Boolean).slice(0, 3).join(' | '), largura: quadro.getBoundingClientRect().width,
               larguraMax, vazam: [], eixoX: [], eixoY: [], selos: []};
    [...cab.children].forEach((celCab, i) => {
      const titulo = celCab.querySelector('.titulo') || celCab;
      const ct = caixa(titulo);
      if (!ct) return;
      const lado = celCab.classList.contains('direita') ? 'direita' : celCab.classList.contains('centro') ? 'centro' : 'esquerda';
      let pior = 0;
      for (const l of corpo) {
        const cel = l.children[i]; if (!cel) continue;
        const cc = caixa(cel); if (!cc) continue;
        const d = lado === 'direita' ? Math.abs(cc.r - ct.r) : lado === 'centro' ? Math.abs((cc.l + cc.r) / 2 - (ct.l + ct.r) / 2)
                : Math.abs(cc.l - ct.l);
        pior = Math.max(pior, d);
      }
      if (pior > %(tol)s) t.eixoX.push(`${titulos[i]} (${lado}, ${Math.round(pior)} px)`);
    });
    for (const l of linhas) {
      const centros = [];
      [...l.children].forEach((cel, i) => {
        if (cel.scrollWidth > cel.clientWidth + 1) t.vazam.push(`${titulos[i] || 'coluna ' + (i + 1)}: ${cel.innerText.trim().slice(0, 30)}`);
        if (l === cab) return;
        const cc = caixa(cel); if (cc) centros.push((cc.t + cc.b) / 2);
      });
      if (centros.length > 1 && Math.max(...centros) - Math.min(...centros) > %(tol)s)
        t.eixoY.push(`${l.innerText.trim().split('\n')[0].slice(0, 30)} (${Math.round(Math.max(...centros) - Math.min(...centros))} px)`);
      l.querySelectorAll('[class*="selo-"]').forEach(s => {
        const alt = parseFloat(getComputedStyle(s).lineHeight) || 16;
        if (s.getBoundingClientRect().height > alt * 1.6) t.selos.push(s.innerText.trim());
      });
    }
    out.tabelas.push(t);
  }
  for (const k of document.querySelectorAll('.rmc-kpis .rmc-kpi')) {
    const label = k.querySelector('.label'); if (!label || label.offsetParent === null) continue;
    const alt = parseFloat(getComputedStyle(label).lineHeight) || 16;
    const linhasLabel = Math.round(label.getBoundingClientRect().height / alt);
    // Só os textos do card: o balão da dica (pseudo-elemento do próprio card, aberto de propósito na captura
    // `dashboard_estoque_dica`) contava como o card vazando (29/10/2026).
    const vaza = [...k.querySelectorAll('.label, .valor, .sub')].some(e => e.scrollWidth > e.clientWidth + 1);
    if (linhasLabel > 2 || vaza) out.cards.push(`${label.innerText.trim()} (${linhasLabel} linha(s)${vaza ? ', vaza' : ''})`);
  }
  return out;
}""" % {"tol": EIXO_TOLERANCIA_PX}


def _verificar_eixos(page, tela: str, largura: int, falhas: list[str]) -> None:
    from core.config import settings

    m = page.evaluate(_JS_EIXOS, settings.tema.largura_maxima_px)
    onde = f"{tela} @ {largura}px"
    if m["rolagem"] > 1:
        falhas.append(f"{onde}: a página rola na horizontal ({m['rolagem']} px a mais que a tela)")
    for t in m["tabelas"]:
        nome = f"tabela [{t['nome']} …]"
        if t["largura"] > t["larguraMax"] + 2:
            falhas.append(f"{onde}, {nome}: {t['largura']:.0f} px, passa da largura máxima de {t['larguraMax']} px")
        for chave, texto in (("vazam", "conteúdo vaza da coluna"), ("eixoX", "título fora do eixo do conteúdo"),
                             ("eixoY", "linha fora do eixo vertical"), ("selos", "etiqueta em 2 linhas")):
            if t[chave]:
                itens = sorted(set(t[chave]))
                falhas.append(f"{onde}, {nome}: {texto}: {', '.join(itens[:5])}" + (f" (+{len(itens) - 5})" if len(itens) > 5 else ""))
    if m["cards"]:
        falhas.append(f"{onde}: card com título em mais de 2 linhas ou vazando: {', '.join(sorted(set(m['cards'])))}")


# Regra dos pop-ups (grill de 07/10/2026, views/popups.py), medida em toda
# captura com pop-up aberto: largura do tipo (ou a área do conteúdo − 32 px,
# a menor), centro na área do conteúdo (à direita da barra lateral aberta),
# centro na vertical e o rodapé de botões visível sem rolar.
POPUP_TOLERANCIA_PX = 8
_JS_POPUP = """() => {
  const d = document.querySelector('div[data-testid="stDialog"] section[role="dialog"]');
  if (!d) return null;
  const marca = d.querySelector('[class*="st-key-popup-aviso"], [class*="st-key-popup-curto"], '
    + '[class*="st-key-popup-longo"], [class*="st-key-popup-detalhe"]');
  const tipo = marca ? marca.className.match(/st-key-popup-(aviso|curto|longo|detalhe)/)[1] : null;
  const sb = document.querySelector('section[data-testid="stSidebar"][aria-expanded="true"]');
  const barra = sb && innerWidth >= 640 ? sb.getBoundingClientRect().width : 0;
  const r = d.getBoundingClientRect();
  const rodape = d.querySelector('[class*="st-key-popup-rodape"]');
  const rr = rodape ? rodape.getBoundingClientRect() : null;
  return {tipo, barra, vw: innerWidth, vh: innerHeight, x: r.left, w: r.width, top: r.top, bottom: r.bottom,
          rodapeFundo: rr ? rr.bottom : null};
}"""


def _verificar_popup(page, tela: str, largura: int, falhas: list[str]) -> None:
    from core.theme import POPUP_LARGURAS

    m = page.evaluate(_JS_POPUP)
    if m is None:
        return
    onde = f"{tela} @ {largura}px, pop-up"
    if m["tipo"] is None:
        falhas.append(f"{onde}: feito sem a fábrica views/popups.py (sem tipo)")
        return
    t = POPUP_TOLERANCIA_PX
    area = m["vw"] - m["barra"]
    esperado = min(POPUP_LARGURAS[m["tipo"]], area - 32)
    if abs(m["w"] - esperado) > 2:
        falhas.append(f"{onde} {m['tipo']}: largura {m['w']:.0f} px, esperado {esperado:.0f}")
    centro_area = m["barra"] + area / 2
    if abs(m["x"] + m["w"] / 2 - centro_area) > t:
        falhas.append(f"{onde}: centro em {m['x'] + m['w'] / 2:.0f} px, área do conteúdo em {centro_area:.0f}")
    if abs((m["top"] + m["bottom"]) / 2 - m["vh"] / 2) > t:
        falhas.append(f"{onde}: fora do centro vertical (topo {m['top']:.0f}, base {m['bottom']:.0f})")
    if m["rodapeFundo"] is not None and m["rodapeFundo"] > m["vh"] + 1:
        falhas.append(f"{onde}: rodapé de botões fora da tela ({m['rodapeFundo']:.0f} > {m['vh']})")


def _registrar(page, tela, largura, mascaras, atualizar, falhas_contrato, conferidos, falhas_visuais,
               falhas_layout, prefixo: str = "") -> None:
    _verificar_tabelas(page, tela, largura, falhas_layout)
    _verificar_eixos(page, tela, largura, falhas_layout)
    _verificar_popup(page, tela, largura, falhas_layout)
    from core.theme import SELETORES_STREAMLIT

    for nome, (seletor, tela_contrato) in SELETORES_STREAMLIT.items():
        if tela_contrato == tela:
            conferidos.add(nome)
            if page.locator(seletor).count() == 0:
                falhas_contrato.append(f"{nome} ({seletor}) não encontrado na tela {tela} @ {largura}px")
    arquivo = f"{tela}_{largura}.png"
    atual = RESULTADO / f"{prefixo}{arquivo}"
    # "Atualização dos dados" no pé da barra (02/10/2026): os meses do
    # data.seed vêm do dia de hoje — sem a máscara, toda imagem "mudaria"
    # na virada do mês.
    # Notificações (st.toast, 02/10/2026) somem sozinhas em 5 s: a captura
    # pegava às vezes com, às vezes sem — saem da página antes da foto
    # (05/10/2026). Máscara não resolve: ela só existe quando o toast existe.
    page.evaluate("""() => document.querySelectorAll('[data-testid="stToast"]').forEach(t => t.remove())""")
    mascaras = list(mascaras) + [page.locator(".rmc-atualizacao")]
    page.screenshot(path=str(atual), mask=mascaras, mask_color="#9AA3AF")
    if atualizar:
        shutil.copy(atual, REFERENCIA / arquivo)
        return
    if not (REFERENCIA / arquivo).exists():
        falhas_visuais.append(f"{arquivo}: sem referência (rode com --atualizar)")
        return
    ok, mudou = _comparar(atual, REFERENCIA / arquivo, RESULTADO / f"DIFERENCA_{prefixo}{arquivo}")
    if not ok:
        falhas_visuais.append(f"{prefixo}{arquivo}: {mudou:.2%} dos pixels mudaram "
                              f"(ver DIFERENCA_{prefixo}{arquivo})")


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
    def volta(roteiro, url: str, atualizar: bool, prefixo: str = "") -> None:
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            for largura in LARGURAS:
                page = navegador.new_page(viewport={"width": largura, "height": ALTURA})
                tela = "inicio"
                try:
                    for tela, mascaras in roteiro(page, url):
                        _estabilizar(page)
                        _registrar(page, tela, largura, mascaras, atualizar,
                                   falhas_contrato, conferidos, falhas_visuais, falhas_layout, prefixo)
                except Exception as erro:
                    # Foto de onde travou: sem ela, só sobra um timeout genérico.
                    page.screenshot(path=str(RESULTADO / f"ERRO_{prefixo}depois_de_{tela}_{largura}.png"))
                    raise RuntimeError(f"Roteiro {prefixo}parou depois da tela '{tela}' @ {largura}px: "
                                       f"{erro}") from erro
                page.close()
            navegador.close()

    try:
        volta(_telas, url, args.atualizar)
    finally:
        app.terminate()
        app.wait(timeout=30)  # solta o visual.db antes da próxima execução

    # 2ª volta (29/10/2026): o Assistente e o Dashboard pelo resultado PRONTO do cálculo de madrugada, comparados
    # com as MESMAS referências — nunca as atualiza: o pronto tem que dar a tela de sempre.
    print("Cálculo de madrugada: publicando os insumos, pré-calculando e subindo o app de novo...")
    _preparar_pronto(env)
    porta = _porta_livre()
    app = _subir_app(env, porta, _CHAVE_PRECALCULO)
    try:
        volta(_telas_pronto, f"http://localhost:{porta}", False, prefixo="pronto_")
    finally:
        app.terminate()
        app.wait(timeout=30)

    sem_tela = sorted(set(SELETORES_STREAMLIT) - conferidos)
    print("\n== CONTRATO com a estrutura do Streamlit ==")
    print("OK" if not falhas_contrato else "\n".join(f"FALHOU: {f}" for f in falhas_contrato))
    if sem_tela:
        print(f"(não conferidos neste roteiro — sem tela garantida: {', '.join(sem_tela)})")
    print("\n== TABELAS E POP-UPS (vãos iguais, títulos numa linha; pop-ups no tamanho e no centro) ==")
    print("OK" if not falhas_layout else "\n".join(f"FALHOU: {f}" for f in falhas_layout))
    print("\n== CAPTURAS ==")
    if args.atualizar:
        print(f"Referências atualizadas em {REFERENCIA}")
    else:
        print("OK — nenhuma tela mudou." if not falhas_visuais else "\n".join(f"MUDOU: {f}" for f in falhas_visuais))
    return 1 if (falhas_contrato or falhas_visuais or falhas_layout) else 0


if __name__ == "__main__":
    raise SystemExit(main())
