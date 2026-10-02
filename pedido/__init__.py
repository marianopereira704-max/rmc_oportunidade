"""Dados da aba Pedido: coleta diária da API do GPS Farma (vendas, compras e
estoque por loja), guardada no Spaces em Parquet.

Roda FORA do app (GitHub Actions, meia-noite): o Postgres fica atrás de um
firewall que só libera o Streamlit Cloud, e a coleta leva horas — então a
rotina só fala com a API do GPS, com a API do sistema interno (lista de
lojas ativas) e com o Spaces. O app lê o que ela gravou.

Módulos:
- `armazenamento` — Spaces ou pasta local, por chave (Parquet e JSON);
- `plano`         — o que falta baixar (a retomada é "pular o que existe");
- `transformar`   — só as colunas que o Pedido usa (decisão de 26/09/2026);
- `vinculo`       — loja do GPS ↔ loja do RMC (CNPJ ou endereço + nome);
- `coleta`        — a execução, com corte por consulta e fila de tentativas.

Mapa da API e as medições que justificam o desenho: docs/mapa_api_gps.md.
"""
