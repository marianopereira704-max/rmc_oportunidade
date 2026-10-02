"""Loja do GPS (idEmpresa, CodigoLoja) ↔ loja do RMC (CNPJ).

Medido em 25–27/09/2026 (docs/mapa_api_gps.md §6 e §10):
- 379 das 409 lojas do GPS trazem CNPJ → casamento direto.
- As 30 sem CNPJ não têm razão social nem bairro na API (vêm vazios); o
  bairro só aparece DENTRO do nome ("MEGA FARMA 1 CENTRO GOIANINHA") e a
  cidade às vezes está errada (a loja "3 CENTRO SÃO JOSÉ" vem como
  Parnamirim). O que bate de verdade é o NÚMERO DO ENDEREÇO: nas 6 da MEGA
  FARMA, 37/55/15/330/54/170-A = exatamente o nosso cadastro.
- Mas o número sozinho engana: OTIMAFARMA caiu em "DROGARIA BELA VISTA"
  (mesmo número, outra loja) e duas "POUPE MAIS" de Tucuruí caíram na
  mesma loja nossa. Por isso número + nome concordando = automático; se
  discordam, ou dois apontam pro mesmo lugar, vai pra confirmação humana.

Casar errado = lançar venda de uma farmácia na conta de outra. Na dúvida,
nunca automático.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

AUTOMATICO = "automatico"
CONFIRMAR = "confirmar"
SEM_CANDIDATO = "sem_candidato"

POR_CNPJ = "cnpj"
POR_ENDERECO = "endereco"
POR_NOME = "nome"

# Nome que concorda com o número do endereço (0–100). 90, não menos: com 60,
# "DROGARIA JOSE SIQUEIRA" virava automático em "DROGARIA JOSE DA LUZ" (nota
# 83, só "JOSE" em comum — revisão de 27/09/2026). Os 19 casos certos tinham
# nota 100.
_NOME_CONFIRMA_NUMERO = 90
# Só pelo nome (sem número batendo): no máximo uma sugestão pra confirmar.
_NOME_SOZINHO = 85
# Mesmo número em mais de uma loja da UF: o nome decide sozinho se for
# forte e bem à frente do segundo.
_NOME_DESEMPATA = 85
_FOLGA_DESEMPATE = 15

_PALAVRAS_GENERICAS = re.compile(
    r"\b(LTDA|ME|EPP|EIRELI|S ?A|DROGARIAS?|FARMACIAS?|DROGA|FARMA|MATRIZ|FILIAL|LOJA|DE|DO|DA|DOS|DAS|E)\b"
)


def so_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def normalizar_nome(valor) -> str:
    t = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = _PALAVRAS_GENERICAS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalizar_uf(valor) -> str:
    return str(valor or "").strip().upper()


def numero_endereco(valor) -> str | None:
    """'170-A' → '170'; '09' → '9'; '265 LJ 0' → '265'; 'S/N' → None."""
    m = re.match(r"\s*0*(\d+)", str(valor or ""))
    return m.group(1) if m and m.group(1) else None


@dataclass
class Sugestao:
    id_empresa: str
    codigo_loja: str
    cnpj_rmc: str | None
    metodo: str | None
    situacao: str
    pontuacao: float
    motivo: str


def _nota_nome(nome_gps: str, loja: dict, nome_gps_bruto: str = "") -> float:
    """Semelhança entre o nome da loja no GPS e o nome + bairro da nossa.

    O nome da CIDADE sai dos dois lados antes de comparar: ela aparece em
    muitos nomes do GPS ("OTIMAFARMA CORONEL FABRICIANO") e, deixada, dava
    nota alta pra qualquer loja da mesma cidade — foi assim que OTIMAFARMA
    "concordou" com DROGARIA BELA VISTA no teste de 27/09/2026. A cidade já
    pesa pelo número do endereço + UF."""
    cidade = set(normalizar_nome(loja.get("cidade")).split())
    nosso = normalizar_nome(" ".join(str(loja.get(k) or "") for k in ("nome_fantasia", "razao_social", "bairro")))

    def sem_cidade(t: str) -> str:
        return " ".join(p for p in t.split() if p not in cidade)

    a, b = sem_cidade(nome_gps), sem_cidade(nosso)
    nota = float(fuzz.token_set_ratio(a, b)) if a and b else 0.0
    # Nome grudado × separado ("DROGAFARMA" × "DROGA FARMA", "POUPEMAIS" ×
    # "POUPE MAIS"): a comparação por palavras dava 25–32 (teste de
    # 27/09/2026). Compara também o nome fantasia inteiro, sem espaços e sem
    # tirar palavras genéricas.
    compacto_gps = _compacto(nome_gps_bruto)
    compacto_nosso = _compacto(loja.get("nome_fantasia"))
    if compacto_gps and compacto_nosso:
        nota = max(nota, float(fuzz.ratio(compacto_gps, compacto_nosso)),
                   float(fuzz.partial_ratio(compacto_nosso, compacto_gps)) if len(compacto_nosso) >= 8 else 0.0)
    return nota


def _compacto(valor) -> str:
    t = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", t)


def sugerir(lojas_gps: list[dict], lojas_rmc: list[dict]) -> list[Sugestao]:
    """`lojas_gps`: id_empresa, codigo_loja, nome_loja, cnpj, numero, uf.
    `lojas_rmc`: cnpj, razao_social, nome_fantasia, numero, bairro, cidade, uf.
    Uma sugestão por loja do GPS, na mesma ordem."""
    por_cnpj = {so_digitos(l["cnpj"]): l for l in lojas_rmc if so_digitos(l.get("cnpj"))}
    por_uf: dict[str, list[dict]] = {}
    for l in lojas_rmc:
        por_uf.setdefault(normalizar_uf(l.get("uf")), []).append(l)

    saida: list[Sugestao] = []
    for g in lojas_gps:
        emp, cod = str(g["id_empresa"]), str(g["codigo_loja"])
        cnpj = so_digitos(g.get("cnpj"))
        if cnpj and cnpj in por_cnpj:
            saida.append(Sugestao(emp, cod, cnpj, POR_CNPJ, AUTOMATICO, 100.0, "CNPJ igual"))
            continue
        uf = normalizar_uf(g.get("uf"))
        nome_gps = normalizar_nome(g.get("nome_loja"))
        candidatas = por_uf.get(uf, [])
        numero = numero_endereco(g.get("numero"))
        mesmo_numero = [l for l in candidatas if numero and numero_endereco(l.get("numero")) == numero]
        if mesmo_numero:
            notas = sorted(((_nota_nome(nome_gps, l, g.get("nome_loja")), l) for l in mesmo_numero),
                           key=lambda x: -x[0])
            nota, melhor = notas[0]
            segunda = notas[1][0] if len(notas) > 1 else 0.0
            unica = len(mesmo_numero) == 1
            # Mesmo número em duas lojas da UF (Ferrari, Sara Farma: a outra
            # era de outra cidade): o nome desempata, se desempatar com folga.
            desempata = not unica and nota >= _NOME_DESEMPATA and nota - segunda >= _FOLGA_DESEMPATE
            if (unica and nota >= _NOME_CONFIRMA_NUMERO) or desempata:
                saida.append(Sugestao(emp, cod, so_digitos(melhor["cnpj"]), POR_ENDERECO, AUTOMATICO, float(nota),
                                      f"número {numero} + nome concordam"))
            else:
                motivo = (f"{len(mesmo_numero)} lojas com o número {numero} na UF" if not unica
                          else f"número {numero} bate, mas o nome não (nota {nota:.0f})")
                saida.append(Sugestao(emp, cod, so_digitos(melhor["cnpj"]), POR_ENDERECO, CONFIRMAR, float(nota), motivo))
            continue
        if candidatas and nome_gps:
            nota, melhor = max(((_nota_nome(nome_gps, l, g.get("nome_loja")), l) for l in candidatas),
                               key=lambda x: x[0])
            if nota >= _NOME_SOZINHO:
                saida.append(Sugestao(emp, cod, so_digitos(melhor["cnpj"]), POR_NOME, CONFIRMAR, float(nota),
                                      "só o nome é parecido"))
                continue
        saida.append(Sugestao(emp, cod, None, None, SEM_CANDIDATO, 0.0, "nenhuma loja parecida na UF"))

    # Duas lojas do GPS na mesma loja nossa: nenhuma fica automática (só o
    # CNPJ é prova suficiente pra conviver com isso).
    contagem: dict[str, int] = {}
    for s in saida:
        if s.cnpj_rmc:
            contagem[s.cnpj_rmc] = contagem.get(s.cnpj_rmc, 0) + 1
    for s in saida:
        if s.cnpj_rmc and contagem[s.cnpj_rmc] > 1 and s.metodo != POR_CNPJ:
            s.situacao = CONFIRMAR
            s.motivo = "outra loja do GPS aponta para a mesma loja do RMC"
    return saida
