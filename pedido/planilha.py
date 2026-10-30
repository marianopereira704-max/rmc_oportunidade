"""A planilha Excel das exportações do Assistente de pedido — sem Streamlit, para a tela (views/pedido/dialogos.py,
views/pedido/__init__.py) e para a rotina dos e-mails para as lojas (pedido/emails.py, 29/10/2026), que manda o
mesmo arquivo do "Enviar pedido" do celular."""
from __future__ import annotations

import io

import pandas as pd

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def excel(abas: dict[str, pd.DataFrame]) -> bytes:
    """Uma planilha com uma aba por item, colunas na largura do conteúdo
    (até 50 caracteres) e o cabeçalho travado — abre pronta pra ler."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        for nome, tabela in abas.items():
            tabela.to_excel(escritor, index=False, sheet_name=nome)
            folha = escritor.sheets[nome]
            folha.freeze_panes = "A2"
            for i, coluna in enumerate(tabela.columns, start=1):
                largura = max([len(str(coluna))] + [len(str(v)) for v in tabela[coluna].head(500)])
                folha.column_dimensions[folha.cell(row=1, column=i).column_letter].width = min(50, largura + 2)
    return buffer.getvalue()
