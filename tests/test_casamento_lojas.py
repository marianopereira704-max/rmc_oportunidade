"""Testes da lógica de casamento de lojas GPS -> RMC (medir_casamento_lojas.py).

A medição só vale se a classificação for confiável. O que precisa estar
travado aqui:

- Caso ambíguo NUNCA pode ser classificado como alta confiança. Duas farmácias
  de nome parecido na mesma UF disputando a mesma loja é o cenário em que um
  casamento automático atribuiria a compra de uma à outra — o erro mais caro
  possível nesse sistema.
- UF tem que ser comparada com `strip()`: a API devolve `'RN              '`
  em parte dos registros, e sem isso a loja cairia em "UF sem loja RMC" por
  causa de espaço em branco.
- Razão social vazia (metade da base da API) precisa cair no nome fantasia,
  não virar string vazia.
"""
from __future__ import annotations

import medir_casamento_lojas as medicao


# ---------------------------------------------------------------------------
# normalização
# ---------------------------------------------------------------------------

def test_normalizar_tira_acento_pontuacao_e_caixa():
    assert medicao.normalizar_nome("Drogaria São José Ltda.") == "DROGARIA SAO JOSE"


def test_normalizar_remove_sufixo_societario():
    assert medicao.normalizar_nome("MARQUES & REIS LTDA") == "MARQUES REIS"
    assert medicao.normalizar_nome("FARMACIA CENTRAL ME") == "FARMACIA CENTRAL"


def test_normalizar_vazio_e_nulo():
    assert medicao.normalizar_nome(None) == ""
    assert medicao.normalizar_nome("") == ""


def test_limpar_corta_o_preenchimento_a_direita_da_api():
    """A API devolve UF preenchida à direita em parte dos registros."""
    assert medicao.limpar("RN                    ") == "RN"
    assert medicao.limpar(None) == ""


# ---------------------------------------------------------------------------
# escolha do texto identificador
# ---------------------------------------------------------------------------

def test_usa_razao_social_quando_existe():
    loja = {"RazaoSocial": "DROGARIA RODRIGUES DOS REIS LTDA.", "NomeLoja": "REIS 1"}
    assert medicao.texto_identificador(loja) == "DROGARIA RODRIGUES DOS REIS LTDA."


def test_cai_no_nome_da_loja_quando_a_razao_social_vem_vazia():
    """Caso real: a MEGA FARMA tem RazaoSocial = '' nas seis lojas."""
    loja = {"RazaoSocial": "", "NomeLoja": "MEGA FARMA 1 CENTRO GOIANINHA"}
    assert medicao.texto_identificador(loja) == "MEGA FARMA 1 CENTRO GOIANINHA"


def test_sem_nenhum_nome_devolve_vazio():
    assert medicao.texto_identificador({"RazaoSocial": "", "NomeLoja": None}) == ""


# ---------------------------------------------------------------------------
# classificação
# ---------------------------------------------------------------------------

def _cache(lojas: list[dict], id_empresa: str = "emp1") -> dict:
    return {id_empresa: {"idEmpresa": id_empresa, "lojas": lojas}}


def test_nome_identico_vira_alta_confianca():
    cache = _cache([{"CodigoLoja": "1", "NomeLoja": "DROGARIA SAO JOSE", "Estado": "MG", "Cidade": "BH"}])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "DROGARIA SÃO JOSÉ LTDA", "uf": "MG", "cidade": "BH"}]

    resultado = medicao.medir(cache, rmc)[0]

    assert resultado["classificacao"] == "ALTA CONFIANCA"
    assert resultado["melhor_rmc"] == "DROGARIA SÃO JOSÉ LTDA"


def test_dois_nomes_quase_iguais_viram_ambiguo_e_nao_alta_confianca():
    """O caso perigoso: sem a checagem de margem, uma das duas seria escolhida
    com score alto e a compra iria para a farmácia errada."""
    cache = _cache([{"CodigoLoja": "1", "NomeLoja": "FARMACIA CENTRAL 2", "Estado": "MG", "Cidade": "BH"}])
    rmc = [
        {"id": 1, "cnpj": "1", "razao_social": "FARMACIA CENTRAL 1", "uf": "MG", "cidade": "BH"},
        {"id": 2, "cnpj": "2", "razao_social": "FARMACIA CENTRAL 3", "uf": "MG", "cidade": "BH"},
    ]

    resultado = medicao.medir(cache, rmc)[0]

    assert resultado["classificacao"] == "AMBIGUO", (
        f"esperava AMBIGUO, veio {resultado['classificacao']} "
        f"(melhor={resultado['melhor_rmc']} {resultado['score']}, "
        f"segundo={resultado['segundo_rmc']} {resultado['score_segundo']})"
    )


def test_nome_totalmente_diferente_fica_sem_correspondencia():
    cache = _cache([{"CodigoLoja": "1", "NomeLoja": "MEGA FARMA 1 GOIANINHA", "Estado": "RN", "Cidade": "GOIANINHA"}])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "PANIFICADORA TRIGO DE OURO", "uf": "RN", "cidade": "NATAL"}]

    assert medicao.medir(cache, rmc)[0]["classificacao"] == "SEM CORRESPONDENCIA"


def test_uf_com_preenchimento_a_direita_ainda_encontra_as_candidatas():
    """Sem o strip() da UF, esta loja cairia em 'UF SEM LOJA RMC' por espaço."""
    cache = _cache([{"CodigoLoja": "1", "NomeLoja": "DROGARIA SAO JOSE", "Estado": "MG            ", "Cidade": "BH"}])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "DROGARIA SÃO JOSÉ LTDA", "uf": "MG", "cidade": "BH"}]

    resultado = medicao.medir(cache, rmc)[0]

    assert resultado["classificacao"] == "ALTA CONFIANCA"
    assert resultado["uf_gps"] == "MG"


def test_uf_sem_nenhuma_loja_do_rmc_e_classificada_separadamente():
    """Não é "não casou": é "nem havia com quem casar". Misturar os dois
    esconderia que a UF inteira está fora da base do RMC."""
    cache = _cache([{"CodigoLoja": "1", "NomeLoja": "FARMACIA X", "Estado": "AC", "Cidade": "RIO BRANCO"}])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "FARMACIA X", "uf": "MG", "cidade": "BH"}]

    assert medicao.medir(cache, rmc)[0]["classificacao"] == "UF SEM LOJA RMC"


def test_loja_sem_nome_na_api_e_classificada_separadamente():
    cache = _cache([{"CodigoLoja": "1", "RazaoSocial": "", "NomeLoja": "", "Estado": "MG", "Cidade": "BH"}])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "DROGARIA SAO JOSE", "uf": "MG", "cidade": "BH"}]

    assert medicao.medir(cache, rmc)[0]["classificacao"] == "SEM NOME NA API"


def test_empresa_que_falhou_na_api_aparece_no_relatorio():
    """Falha de uma empresa não pode sumir do relatório — senão o total não
    fecha e ninguém percebe que faltou medir um pedaço."""
    cache = {"emp1": {"_erro": "GpsApiIndisponivel: timeout"}}

    resultado = medicao.medir(cache, [])

    assert len(resultado) == 1
    assert resultado[0]["classificacao"] == "ERRO NA API"


def test_todas_as_lojas_da_empresa_entram_na_medicao():
    cache = _cache([
        {"CodigoLoja": str(i), "NomeLoja": f"MEGA FARMA {i}", "Estado": "RN", "Cidade": "GOIANINHA"}
        for i in range(1, 7)
    ])
    rmc = [{"id": 1, "cnpj": "1", "razao_social": "MEGA FARMA 1", "uf": "RN", "cidade": "GOIANINHA"}]

    assert len(medicao.medir(cache, rmc)) == 6
