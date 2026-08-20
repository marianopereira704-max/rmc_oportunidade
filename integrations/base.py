"""Interface comum para as 3 integrações externas do sistema.

Hoje só a de lojas (sistema interno) tem API real prevista (pronta do lado
da Rede, só faltam as credenciais). Gruppy (ofertas) e GPS (compras) entram
por planilha manual — mas as 3 seguem a MESMA interface: quando uma API real
existir para alguma delas, basta trocar a implementação por trás do adapter,
sem tocar no resto do sistema (telas, queries etc. não sabem se o dado veio
de API ou de planilha).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class StatusIntegracao(str, Enum):
    DISPONIVEL = "disponivel"       # API real configurada e respondendo
    MANUAL = "manual"               # sem API ainda — dado entra por planilha
    INDISPONIVEL = "indisponivel"   # deveria ter API mas não está configurada/respondendo


@dataclass
class ResultadoSincronizacao:
    status: StatusIntegracao
    registros_processados: int
    mensagem: str


class IntegrationAdapter(ABC):
    nome: str

    @abstractmethod
    def status(self) -> StatusIntegracao:
        """Diz se a integração real está disponível agora."""

    @abstractmethod
    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        """Traz os dados mais recentes para o banco local. Implementação real
        chama a API; implementação manual processa a última planilha
        enviada pelo admin."""
