# Migrações para depois da publicação

O Alembic só lê `alembic/versions/`. O que está aqui fica pronto, mas parado, até a hora certa.

## `apaga_tabelas_sem_uso.py` (preparada em 29/10/2026)

Apaga `rascunhos_pedido`, `pedido_filtro_salvo`, `registros_compra_gps`, `compras_gps_orfas`, `fila_cnpj_orfao` e
`uploads_gps`. Ativar **só depois** de, nesta ordem:

1. Publicar o app novo (juntar o `desenvolvimento` no `main`) e ver a equipe usando sem problema.
2. Rodar `python limpeza_compras_planilha.py` (prévia) e `python limpeza_compras_planilha.py --executar` contra o banco
   de produção.
3. Ter certeza de que não vai voltar ao `main` antigo: ele ainda lê essas tabelas.

Para ativar:

1. Mover o arquivo para `alembic/versions/` com o nome no padrão (`AAAAMMDD_HHMM_apaga_tabelas_sem_uso.py`) e acertar
   `revision` e `down_revision` (a última migração de `versions/`).
2. Apagar de `core/models.py` as classes `RascunhoPedido`, `FiltroSalvoPedido`, `RegistroCompraGPS`, `CompraGPSOrfa`,
   `FilaCnpjOrfao` e `UploadGPS` (o teste de migrações compara o modelo com o banco), e as referências em
   `data/seed.py` e `limpeza_compras_planilha.py` (o script já terá cumprido o papel e pode sair).
3. `python -m pytest -q -p no:warnings` (o teste de migrações tem de passar) e enviar. A migração roda sozinha quando
   o app sobe.
