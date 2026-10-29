# Backup dos bancos — passo a passo (29/10/2026)

O script `scripts/backup_banco.sh` roda **no droplet do Postgres**, todo dia às 02:00 de Brasília, e guarda os dois
bancos no Spaces:

```
<bucket>/backups/rmc_oportunidades/AAAA-MM-DD/rmc_oportunidades.sql.gz       (app publicado, 36 MB no banco)
<bucket>/backups/rmc_oportunidades/AAAA-MM-DD/rmc_oportunidades_dev.sql.gz   (dados reais do app novo, 124 MB)
```

Ficam os **14 dias mais recentes** e o **dia 1 de cada mês por 12 meses** (até 26 dias guardados, cerca de
1 GB no Spaces — dentro do espaço que o plano já inclui). A limpeza só apaga, uma por uma, chaves desta pasta com
exatamente esse formato: os backups `.sql.gz` de outros sistemas no mesmo bucket nunca são tocados.

O alerta da rotina (`pedido/alerta.py`) manda e-mail quando falta o backup de hoje ou de ontem de algum dos dois
bancos — até a instalação abaixo ser feita, esse e-mail chega todo dia.

## Instalação (uma vez, ~15 minutos)

Tudo como `root` no droplet (`ssh root@<IP do droplet>`).

**1. Descobrir como o Postgres roda.**

```bash
docker ps --format '{{.Names}}  {{.Image}}'
```

Se aparecer um contêiner com imagem `postgres:16` (o docker-compose do app usa a rede `postgres_default`, o que
indica isso), anote o NOME dele — vai no passo 4. Se o comando não existir ou não listar Postgres, o banco está
instalado direto e o passo 4 fica sem `RMC_BACKUP_DOCKER`.

**2. Instalar o s3cmd e ligar no Spaces.**

```bash
apt update && apt install -y s3cmd
s3cmd --configure
```

Respostas (os valores estão no seu `.streamlit/secrets.toml`):
- Access Key: `DO_SPACES_ACCESS_KEY`
- Secret Key: `DO_SPACES_SECRET_KEY`
- Default Region: `nyc3`
- S3 Endpoint: `nyc3.digitaloceanspaces.com`
- DNS-style bucket: `%(bucket)s.nyc3.digitaloceanspaces.com`
- Encryption password e GPG: deixar em branco
- Use HTTPS: `Yes`
- Test access: `Y` (tem que dar "Success")
- Save settings: `y`

```bash
chmod 600 /root/.s3cfg
```

**3. Copiar o script** (do Windows, na pasta do projeto, no PowerShell):

```powershell
scp scripts/backup_banco.sh root@<IP do droplet>:/usr/local/bin/rmc_backup_banco.sh
```

No droplet:

```bash
chmod 700 /usr/local/bin/rmc_backup_banco.sh
```

**4. Configuração.**

```bash
cat > /etc/rmc_backup.env <<'EOF'
RMC_BACKUP_BUCKET=<o valor de DO_SPACES_BUCKET>
RMC_BACKUP_DOCKER=<nome do contêiner do passo 1, ou apague esta linha>
EOF
chmod 600 /etc/rmc_backup.env
```

**5. Prévia** (não grava nem apaga nada; mostra o tamanho de cada banco e o destino):

```bash
rmc_backup_banco.sh
```

**6. Primeiro backup de verdade:**

```bash
rmc_backup_banco.sh --executar
```

**7. Provar que o backup volta.** Restaura o backup mais recente de cada banco num banco TEMPORÁRIO
(`rmc_teste_restauracao`, o único que o script cria e apaga), compara as linhas das tabelas principais com o banco
atual e apaga o temporário. Precisa de espaço livre no disco do tamanho do maior banco (confira com `df -h`):

```bash
rmc_backup_banco.sh --testar-restauracao
```

As duas colunas de cada tabela ("backup | banco atual") têm que bater, ou quase (o que mudou desde o backup).

**8. Agendar.** Confira o fuso do droplet com `timedatectl` (droplets vêm em UTC). Em UTC, 05:00 = 02:00 de
Brasília:

```bash
crontab -e
```

Acrescentar a linha:

```
0 5 * * * /usr/local/bin/rmc_backup_banco.sh --executar >> /var/log/rmc_backup.log 2>&1
```

**9. Manter o log pequeno:**

```bash
cat > /etc/logrotate.d/rmc_backup <<'EOF'
/var/log/rmc_backup.log {
    monthly
    rotate 6
    compress
    missingok
}
EOF
```

## Atualizar o script

Mudou o `scripts/backup_banco.sh` no repositório: repetir o passo 3. O fim de linha precisa ser LF (o
`.gitattributes` garante isso no Windows); com CRLF o bash do droplet falha com `$'\r': command not found`.

## Restaurar de verdade (desastre)

1. Baixar o backup do dia escolhido:
   `s3cmd get s3://<bucket>/backups/rmc_oportunidades/AAAA-MM-DD/<banco>.sql.gz`
2. Criar um banco vazio com outro nome (nunca restaurar por cima do banco em uso), restaurar e conferir:
   `createdb <banco>_restaurado` e `gunzip -c <banco>.sql.gz | psql -d <banco>_restaurado`
   (com Docker: `docker exec -u postgres <contêiner> createdb ...` e `gunzip -c ... | docker exec -i -u postgres <contêiner> psql -d ...`).
3. O backup é feito sem dono e sem permissões (`--no-owner --no-privileges`): dar ao usuário do app a posse das
   tabelas (`REASSIGN OWNED` / `GRANT`) antes de apontar o app para ele.
4. Trocar o nome do banco na `DATABASE_URL` (Secrets do Streamlit Cloud ou `.streamlit/secrets.toml`).

## Quando trocar as credenciais (última etapa do plano)

A chave do Spaces também está em `/root/.s3cfg` no droplet: rodar `s3cmd --configure` de novo com a chave nova.
