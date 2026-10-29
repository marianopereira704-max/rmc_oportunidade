# Tirar a pasta `Claude outputs/` do GitHub, inclusive do histórico (29/10/2026)

**Por quê:** o repositório é público, e a pasta `Claude outputs/` — versionada desde o commit `59eb9a9`, presente no
`main` (182 arquivos) e no ramo `atualizacao-set-2026` (10) — tem 3 HTMLs da Análise de Oportunidade com ~420 CNPJs
e nomes de drogarias, e 180 capturas de tela. Decisão do Mariano: opções A + B do plano
(`docs/specs/2026-10-29-plano-de-acao.md`, etapa 3).

**O que muda no app publicado:** o CÓDIGO do `main` continua o mesmo (o histórico novo tem os mesmos arquivos,
só sem a pasta, que o app nunca leu). Mas o `main` recebe um push, e o Streamlit Cloud **reinicia o app uma vez**
com essa mesma versão: 1 a 3 minutos fora do ar, e quem estiver com o app aberto precisa recarregar a página.
Faça num horário em que a equipe não esteja usando, e com a rotina do GitHub parada (aba Actions sem nada
rodando — ela roda de 00:00 até umas 05:30).

Os arquivos continuam na sua pasta do computador: só saem do GitHub.

## Passo 1 — Opção A: tirar a pasta do repositório e salvar o trabalho atual

PowerShell, na pasta do projeto:

```powershell
git status
git rm -r --cached "Claude outputs"
git add -A
git commit -m "Tira a pasta Claude outputs do repositório (dados de lojas) e limpeza de 29/10"
git push origin desenvolvimento
```

`git rm --cached` tira do repositório e deixa os arquivos no disco. A pasta já está no `.gitignore`: não volta.

## Passo 2 — Instalar a ferramenta

```powershell
python -m pip install git-filter-repo
git filter-repo --version
```

## Passo 3 — Opção B: reescrever o histórico numa cópia à parte

Numa pasta FORA do OneDrive (o OneDrive atrapalha com milhares de arquivos pequenos):

```powershell
mkdir C:\temp -ErrorAction SilentlyContinue
cd C:\temp
git clone --mirror https://github.com/marianopereira704-max/rmc_oportunidade.git rmc_limpeza.git
cd rmc_limpeza.git
git filter-repo --path "Claude outputs" --invert-paths
```

Conferir antes de enviar — os três comandos abaixo têm que voltar VAZIOS:

```powershell
git log --all --oneline -- "Claude outputs"
git ls-tree -r main --name-only | Select-String "Claude outputs"
git ls-tree -r desenvolvimento --name-only | Select-String "Claude outputs"
```

## Passo 4 — Conferir que o código do `main` não mudou

Volte para a pasta do projeto e compare o `main` de hoje com o `main` limpo. A única diferença tem que ser a
pasta `Claude outputs` sendo apagada:

```powershell
cd "C:\Users\RMC - 0014\OneDrive\Área de Trabalho\Projetos\rmc_oportunidades"
git fetch C:\temp\rmc_limpeza.git main:refs/limpeza/main
git diff --stat origin/main refs/limpeza/main | Select-String -NotMatch "Claude outputs"
```

O último comando deve mostrar só a linha de resumo ("N files changed, … deletions"). Se aparecer qualquer outro
arquivo, PARE e me mande a saída.

## Passo 5 — Enviar o histórico novo

```powershell
cd C:\temp\rmc_limpeza.git
git remote add origin https://github.com/marianopereira704-max/rmc_oportunidade.git
git push origin --force --all
git push origin --force --tags
```

(O `filter-repo` remove o `origin` de propósito, para ninguém enviar sem querer — por isso o `remote add`.)
Se o GitHub recusar o push no `main` por "protected branch": Settings → Branches → liberar o "force push" só
durante este passo e travar de novo depois.

## Passo 6 — Conferir o app e o GitHub

- O Streamlit Cloud reinicia sozinho; abra o app publicado e entre (deve estar igual a antes).
- No GitHub, a pasta `Claude outputs` não aparece em nenhum ramo.

## Passo 7 — Ajustar a sua pasta do projeto

Sem perder nada do que está na pasta (o `--mixed` só move o ponteiro do ramo; os arquivos ficam como estão):

```powershell
cd "C:\Users\RMC - 0014\OneDrive\Área de Trabalho\Projetos\rmc_oportunidades"
git fetch origin --prune
git reset --mixed origin/desenvolvimento
git status
git update-ref -d refs/limpeza/main
```

O `git status` deve mostrar só o que mudou depois do passo 1 (ou nada). Apague a cópia `C:\temp\rmc_limpeza.git`.

## Cuidados depois

- **Outros computadores:** o ramo `main-DESKTOP-NFSNS62` indica uma cópia do repositório em outra máquina. Lá,
  apague a pasta do projeto e baixe de novo (`git clone`). Se alguém enviar (push) de uma cópia antiga, o histórico
  velho — com a pasta — volta para o GitHub.
- **Cópias que já existem:** forks e cópias que alguém já tenha baixado continuam com os arquivos; o GitHub também
  pode guardar por um tempo os commits antigos acessíveis pelo endereço direto. Para limpar isso de vez: GitHub
  Support → "Remove sensitive data" (opcional).
