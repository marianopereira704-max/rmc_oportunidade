#!/usr/bin/env bash
# Backup diário dos bancos do RMC Oportunidades — roda NO DROPLET do Postgres, pelo cron (29/10/2026).
#
# Por que no droplet e não no GitHub Actions: o firewall do banco só libera os IPs do Streamlit Cloud; o GitHub não
# tem IP fixo (são milhares e mudam), e abrir o banco para ele seria abri-lo para a internet. Aqui o pg_dump roda ao
# lado do banco, como o usuário `postgres`, sem senha nem porta aberta.
#
# Por que os DOIS bancos: `rmc_oportunidades` é o do app publicado (a equipe usa todo dia) e
# `rmc_oportunidades_dev` é onde estão hoje os dados reais enviados pelo app novo (Gruppy, Base Genéricos,
# classificação, vínculos, usuários) — perder qualquer um não tem volta.
#
#   backup_banco.sh                        prévia: mostra o que faria, não grava nem apaga nada
#   backup_banco.sh --executar             faz o backup e a limpeza (é o que o cron chama)
#   backup_banco.sh --testar-restauracao   restaura o backup mais recente num banco TEMPORÁRIO, conta as linhas e apaga
#
# Destino: s3://$RMC_BACKUP_BUCKET/backups/rmc_oportunidades/AAAA-MM-DD/<banco>.sql.gz (data de Brasília).
# Retenção: os últimos 14 dias com backup + o do dia 1 de cada mês por 12 meses. O bucket é COMPARTILHADO com
# outros sistemas (que também guardam .sql.gz): a limpeza apaga uma chave por vez, e só as que casam exatamente
# com o padrão acima — nunca por prefixo, nunca fora desta pasta.
#
# Configuração em /etc/rmc_backup.env (ver docs/backup.md): RMC_BACKUP_BUCKET, RMC_BACKUP_DOCKER (se o Postgres
# roda em contêiner) e, se mudar, RMC_BACKUP_BANCOS. Tamanho em 29/10/2026: 36 MB (publicado) e 124 MB (dev).
# Credenciais do Spaces: as do s3cmd (/root/.s3cfg). Trocou a chave do Spaces? Trocar lá também.
set -euo pipefail

[ -f /etc/rmc_backup.env ] && . /etc/rmc_backup.env
BUCKET="${RMC_BACKUP_BUCKET:?defina RMC_BACKUP_BUCKET em /etc/rmc_backup.env}"
BANCOS="${RMC_BACKUP_BANCOS:-rmc_oportunidades rmc_oportunidades_dev}"
PASTA="backups/rmc_oportunidades"
DIAS=14
MESES=12
TEMP_DIR="${RMC_BACKUP_TEMP:-/var/tmp/rmc_backup}"
BANCO_TESTE="rmc_teste_restauracao"   # o único banco que este script cria e apaga
PADRAO="^s3://${BUCKET}/${PASTA}/([0-9]{4}-[0-9]{2}-[0-9]{2})/[a-z0-9_]+\.sql\.gz$"

MODO="previa"
case "${1:-}" in
  "") ;;
  --executar) MODO="executar" ;;
  --testar-restauracao) MODO="testar" ;;
  *) echo "uso: $0 [--executar | --testar-restauracao]" >&2; exit 2 ;;
esac

HOJE="$(TZ=America/Sao_Paulo date +%F)"
log() { echo "$(date -u +%FT%TZ) $*"; }

# Um por vez: se o de ontem ainda estiver rodando (disco lento, banco grande), o de hoje não começa por cima.
exec 9>"${RMC_BACKUP_LOCK:-/var/lock/rmc_backup.lock}"
flock -n 9 || { log "outro backup ainda está rodando; saindo"; exit 1; }

# Postgres num contêiner Docker (o docker-compose do app usa a rede externa `postgres_default`, sinal de que o banco
# roda num projeto Docker "postgres" no droplet) ou instalado direto: RMC_BACKUP_DOCKER = nome do contêiner
# (`docker ps`); vazio = instalado direto. Nos dois casos, como o usuário `postgres` local, sem senha.
como_postgres() {
  if [ -n "${RMC_BACKUP_DOCKER:-}" ]; then
    docker exec -i -u postgres "$RMC_BACKUP_DOCKER" "$@"
  else
    sudo -u postgres "$@"
  fi
}

fazer_backups() {
  mkdir -p "$TEMP_DIR"
  for banco in $BANCOS; do
    destino="s3://${BUCKET}/${PASTA}/${HOJE}/${banco}.sql.gz"
    tamanho="$(como_postgres psql -Atc "select pg_size_pretty(pg_database_size('${banco}'))")"
    if [ "$MODO" = "previa" ]; then
      log "PRÉVIA: ${banco} (${tamanho} no Postgres) iria para ${destino}"
      continue
    fi
    arquivo="${TEMP_DIR}/${banco}.sql.gz"
    log "backup de ${banco} (${tamanho})"
    # --no-owner/--no-privileges: restaura sob qualquer usuário (inclusive para copiar o dev para a produção).
    # nice/ionice: o droplet tem 1 vCPU e 1 GB; o banco continua respondendo enquanto o dump roda.
    como_postgres nice -n 10 ionice -c3 pg_dump --no-owner --no-privileges "$banco" | gzip -6 > "$arquivo"
    gzip -t "$arquivo"                                   # arquivo truncado não sobe
    [ -s "$arquivo" ] || { log "ERRO: backup de ${banco} vazio"; exit 1; }
    s3cmd put --no-progress --quiet "$arquivo" "$destino"
    log "enviado ${destino} ($(du -h "$arquivo" | cut -f1))"
    rm -f "$arquivo"
  done
}

limpar_antigos() {
  # Chaves que casam com o padrão, com a data de cada uma.
  mapfile -t chaves < <(s3cmd ls -r "s3://${BUCKET}/${PASTA}/" | awk '{print $4}' | grep -E "$PADRAO" || true)
  [ "${#chaves[@]}" -eq 0 ] && { log "nenhum backup antigo para conferir"; return; }
  mapfile -t datas < <(printf '%s\n' "${chaves[@]}" | sed -E "s#${PADRAO}#\1#" | sort -ru)
  # Ficam: os 14 dias mais recentes com backup + os 12 "dia 1" mais recentes.
  mapfile -t manter < <({ printf '%s\n' "${datas[@]}" | head -n "$DIAS"
                          printf '%s\n' "${datas[@]}" | grep -E -- '-01$' | head -n "$MESES"; } | sort -u)
  for chave in "${chaves[@]}"; do
    data="$(echo "$chave" | sed -E "s#${PADRAO}#\1#")"
    if ! printf '%s\n' "${manter[@]}" | grep -qx "$data"; then
      if [ "$MODO" = "executar" ]; then
        s3cmd del --quiet "$chave"
        log "apagado ${chave}"
      else
        log "PRÉVIA: apagaria ${chave}"
      fi
    fi
  done
  log "guardados: ${#manter[@]} dia(s) de backup"
}

testar_restauracao() {
  mkdir -p "$TEMP_DIR"
  for banco in $BANCOS; do
    ultima="$(s3cmd ls -r "s3://${BUCKET}/${PASTA}/" | awk '{print $4}' | grep -E "$PADRAO" \
              | grep "/${banco}\.sql\.gz$" | sort | tail -n 1 || true)"
    [ -n "$ultima" ] || { log "sem backup de ${banco} para testar"; continue; }
    arquivo="${TEMP_DIR}/teste_${banco}.sql.gz"
    s3cmd get --no-progress --quiet --force "$ultima" "$arquivo"
    log "restaurando ${ultima} em ${BANCO_TESTE}"
    como_postgres dropdb --if-exists "$BANCO_TESTE"
    como_postgres createdb "$BANCO_TESTE"
    gunzip -c "$arquivo" | como_postgres psql -q -v ON_ERROR_STOP=1 -d "$BANCO_TESTE" > /dev/null
    tabelas="$(como_postgres psql -Atd "$BANCO_TESTE" -c "select count(*) from information_schema.tables where table_schema='public'")"
    log "${banco}: ${tabelas} tabelas restauradas; linhas das principais (backup | banco atual):"
    for t in lojas usuarios tabelas_gruppy itens_tabela_gruppy base_genericos ean_genericos categorias_ean vinculos_loja_gps; do
      no_backup="$(como_postgres psql -Atd "$BANCO_TESTE" -c "select count(*) from ${t}" 2>/dev/null || echo '-')"
      no_banco="$(como_postgres psql -Atd "$banco" -c "select count(*) from ${t}" 2>/dev/null || echo '-')"
      log "  ${t}: ${no_backup} | ${no_banco}"
    done
    como_postgres dropdb "$BANCO_TESTE"
    rm -f "$arquivo"
  done
}

if [ "$MODO" = "testar" ]; then
  testar_restauracao
else
  fazer_backups
  limpar_antigos
fi
log "fim (${MODO})"
