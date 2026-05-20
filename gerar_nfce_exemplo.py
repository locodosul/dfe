"""
Exemplo inicial de geracao de XML NF-e/NFC-e usando nfelib.

Objetivo:
- Criar uma NF-e ou NFC-e simples, em homologacao, com dados fixos.
- Gerar o XML pela nfelib, sem montar XML manualmente com strings.
- Servir como base para evoluir depois para assinatura, envio e banco de dados.

Atencao:
- Este XML e de estudo e ainda nao esta pronto para autorizacao na SEFAZ.
- NFC-e real exige certificado, QR Code, CSC, regras fiscais corretas e transmissao.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import getpass
import hashlib
import os
import shutil
import smtplib
from email.message import EmailMessage
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from erpbrasil.assinatura.certificado import ArquivoCertificado, Certificado
from lxml import etree
from nfelib.nfe.bindings.v4_0.leiaute_nfe_v4_00 import (
    CofinsntCst,
    DestIndIedest,
    DetPagIndPag,
    EmitCrt,
    Icms00Cst,
    Icms00ModBc,
    Icmssn102Csosn,
    IdeIdDest,
    IdeIndFinal,
    IdeIndPres,
    IdeTpEmis,
    IdeTpImp,
    IdeTpNf,
    PisntCst,
    ProdIndTot,
    TenderEmi,
    TenderEmiCPais,
    TenderEmiXPais,
    Tendereco,
    TfinNfe,
    Torig,
    TranspModFrete,
)
from nfelib.nfe.bindings.v4_0.nfe_v4_00 import Nfe
from nfelib.nfe.bindings.v4_0.tipos_basico_v4_00 import Tamb, TcodUfIbge, Tmod, Tuf, TufEmi
from nfelib.mdfe.bindings.v3_0.mdfe_v3_00 import Mdfe
from nfelib.cte.bindings.v4_0.cte_v4_00 import Cte
from nfelib.cte.bindings.v4_0.cte_tipos_basico_v4_00 import (
    Icms00Cst as CteIcms00Cst,
    Timp,
)
from requests import Session
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from zeep import Client
from zeep.cache import SqliteCache
from zeep.transports import Transport


VERSAO_NFE = "4.00"
VERSAO_MDFE = "3.00"
VERSAO_CTE = "4.00"
MODELO_NFCE = "65"
MODELO_NFE = "55"
MODELO_MDFE = "58"
MODELO_CTE = "57"
AMBIENTE_HOMOLOGACAO = "2"
FUSO_BRASIL = timezone(timedelta(hours=-3))
CERTIFICADO_PADRAO = Path(__file__).parent / "certificado.pfx"
CONFIG_PADRAO = Path(__file__).parent / "config_nfce.json"
ULTIMO_NUMERO_PADRAO = Path(__file__).parent / "ultimo_numero_nfce.txt"
REGISTRO_DFE_PADRAO = Path(__file__).parent / "dfe_emitidos.json"
URL_CONSULTA_NFCE_RS = "https://www.sefaz.rs.gov.br/NFCE/NFCE-COM.aspx"
URL_AUTORIZACAO_NFCE_HOMOLOGACAO_SVRS = (
    "https://nfce-homologacao.sefazrs.rs.gov.br/ws/NfeAutorizacao/NFeAutorizacao4.asmx"
)
URL_AUTORIZACAO_NFE_HOMOLOGACAO_SVRS = (
    "https://nfe-homologacao.sefazrs.rs.gov.br/ws/NfeAutorizacao/NFeAutorizacao4.asmx"
)
URL_AUTORIZACAO_MDFE_HOMOLOGACAO_SVRS = (
    "https://mdfe-homologacao.svrs.rs.gov.br/ws/MDFeRecepcaoSinc/MDFeRecepcaoSinc.asmx"
)
WSDL_AUTORIZACAO_MDFE_HOMOLOGACAO_SVRS = (
    "https://mdfe-homologacao.svrs.rs.gov.br/ws/MDFeRecepcaoSinc/MDFeRecepcaoSinc.asmx?wsdl"
)
URL_AUTORIZACAO_CTE_HOMOLOGACAO_SVRS = (
    "https://cte-homologacao.svrs.rs.gov.br/ws/CTeRecepcaoSincV4/CTeRecepcaoSincV4.asmx"
)
WSDL_AUTORIZACAO_CTE_HOMOLOGACAO_SVRS = (
    "https://cte-homologacao.svrs.rs.gov.br/ws/CTeRecepcaoSincV4/CTeRecepcaoSincV4.asmx?wsdl"
)
URL_AUTORIZACAO_CTE_HOMOLOGACAO_SP = (
    "https://homologacao.nfe.fazenda.sp.gov.br/CTeWS/WS/CTeRecepcaoSincV4.asmx"
)
WSDL_AUTORIZACAO_CTE_HOMOLOGACAO_SP = (
    "https://homologacao.nfe.fazenda.sp.gov.br/CTeWS/WS/CTeRecepcaoSincV4.asmx?wsdl"
)
EXEMPLO_MDFE_PADRAO = Path(__file__).parent / "exemploMDFE.xml"
EXEMPLO_CTE_PADRAO = Path(r"C:\Sistema\SCTotal\Cte\Xml1\202604\Exemplo-cte.xml")
WSDL_AUTORIZACAO_NFE = (
    Path(__file__).parent
    / "wsdl"
    / "nfeautorizacao4.wsdl"
)
NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"
NAMESPACE_NFE_AUTORIZACAO = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4"
NAMESPACE_MDFE = "http://www.portalfiscal.inf.br/mdfe"
NAMESPACE_MDFE_AUTORIZACAO = "http://www.portalfiscal.inf.br/mdfe/wsdl/MDFeRecepcaoSinc"
NAMESPACE_CTE = "http://www.portalfiscal.inf.br/cte"
NAMESPACE_CTE_AUTORIZACAO = "http://www.portalfiscal.inf.br/cte/wsdl/CTeRecepcaoSincV4"


@dataclass(frozen=True)
class EmitenteExemplo:
    cnpj: str = "27123482000204"
    nome: str = "EMPRESA TESTE NFC-E"
    fantasia: str = "SCFACIL TESTE"
    inscricao_estadual: str = "0240527534"
    crt: str = "1"
    uf_codigo: str = "43"
    uf_sigla: str = "RS"
    municipio_codigo: str = "4304606"
    municipio_nome: str = "Canoas"
    logradouro: str = "Rua Santa Cruz"
    numero: str = "500"
    bairro: str = "Niteroi"
    cep: str = "92120100"


@dataclass(frozen=True)
class ItemExemplo:
    codigo: str = "001"
    descricao: str = "NOTA FISCAL EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"
    ncm: str = "84719012"
    cfop: str = "5102"
    unidade: str = "UN"
    quantidade: Decimal = Decimal("1.00")
    valor_unitario: Decimal = Decimal("100.00")

    @property
    def valor_total(self) -> Decimal:
        return self.quantidade * self.valor_unitario


@dataclass(frozen=True)
class DadosNumeracao:
    serie: int
    numero: int
    codigo_numerico: str


@dataclass(frozen=True)
class TributacaoExemplo:
    regime_tributario: str = "simples_nacional"
    icms_cst: str = "00"
    icms_modalidade_base_calculo: str = "3"
    icms_aliquota: Decimal = Decimal("18.00")

    @property
    def regime_normal(self) -> bool:
        return self.regime_tributario == "normal"


def carregar_config(caminho: Path) -> dict:
    """Carrega configuracoes locais do exemplo."""
    if not caminho.exists():
        return {}

    return json.loads(caminho.read_text(encoding="utf-8-sig"))


def obter_config(config: dict, caminho: str, padrao=None):
    """Busca um valor em um dicionario usando caminho com pontos."""
    atual = config
    for parte in caminho.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return padrao
        atual = atual[parte]
    return atual


def listar_emitentes_config(config: dict) -> list[dict]:
    """Retorna emitentes configurados, aceitando tambem o formato antigo."""
    emitentes = config.get("emitentes")
    if isinstance(emitentes, list) and emitentes:
        return emitentes

    certificado = config.get("certificado", {}) or {}
    return [
        {
            "id": "padrao",
            "nome_exibicao": certificado.get("nome") or "Emitente padrao",
            "certificado": certificado,
            "dados": {},
        }
    ]


def selecionar_emitente_config(config: dict, emitente_id: str | None) -> dict:
    """Seleciona o emitente pelo ID configurado."""
    emitentes = listar_emitentes_config(config)
    padrao = emitente_id or config.get("emitente_padrao") or emitentes[0].get("id")

    for emitente in emitentes:
        if emitente.get("id") == padrao:
            return emitente

    ids = ", ".join(str(emitente.get("id")) for emitente in emitentes)
    raise ValueError(f"Emitente '{padrao}' nao encontrado no config. Disponiveis: {ids}")


def montar_emitente_base(config_emitente: dict | None = None) -> EmitenteExemplo:
    """Monta o emitente base a partir do config, mantendo defaults do exemplo."""
    base = EmitenteExemplo()
    dados = (config_emitente or {}).get("dados", {}) or {}
    campos = {
        campo: str(valor)
        for campo, valor in dados.items()
        if hasattr(base, campo) and valor is not None
    }
    return replace(base, **campos) if campos else base


def montar_tributacao(config_emitente: dict | None = None) -> TributacaoExemplo:
    """Le a tributacao fiscal do emitente no config."""
    tributacao = (config_emitente or {}).get("tributacao", {}) or {}
    icms = tributacao.get("icms", {}) or {}
    return TributacaoExemplo(
        regime_tributario=str(
            tributacao.get("regime_tributario", "simples_nacional")
        ),
        icms_cst=str(icms.get("cst", "00")),
        icms_modalidade_base_calculo=str(icms.get("modalidade_base_calculo", "3")),
        icms_aliquota=Decimal(str(icms.get("aliquota", "18.00"))),
    )


def moeda(valor: Decimal) -> str:
    """Formata valores monetarios no padrao esperado pelo XML."""
    return f"{valor:.2f}"


def quantidade(valor: Decimal) -> str:
    """Formata quantidades com quatro casas, comum nos campos qCom/qTrib."""
    return f"{valor:.4f}"


def calcular_percentual(base: Decimal, aliquota: Decimal) -> Decimal:
    """Calcula valor percentual com duas casas."""
    return (base * aliquota / Decimal("100")).quantize(Decimal("0.01"))


def calcular_dv_chave_acesso(chave_sem_dv: str) -> str:
    """Calcula o digito verificador da chave de acesso usando modulo 11."""
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = 0

    for indice, digito in enumerate(reversed(chave_sem_dv)):
        soma += int(digito) * pesos[indice % len(pesos)]

    resto = soma % 11
    dv = 11 - resto
    return "0" if dv >= 10 else str(dv)


def montar_chave_acesso(
    *,
    codigo_uf: str,
    data_emissao: datetime,
    cnpj_emitente: str,
    modelo: str,
    serie: int,
    numero: int,
    tipo_emissao: str,
    codigo_numerico: str,
) -> str:
    """Monta a chave de acesso de 44 digitos da NF-e/NFC-e."""
    ano_mes = data_emissao.strftime("%y%m")
    chave_sem_dv = (
        f"{codigo_uf}"
        f"{ano_mes}"
        f"{cnpj_emitente}"
        f"{modelo}"
        f"{serie:03d}"
        f"{numero:09d}"
        f"{tipo_emissao}"
        f"{codigo_numerico}"
    )
    return f"{chave_sem_dv}{calcular_dv_chave_acesso(chave_sem_dv)}"


def normalizar_id_csc(id_csc: str) -> str:
    """Normaliza o ID do CSC para o formato aceito pelo schema da NFC-e."""
    apenas_digitos = "".join(caractere for caractere in id_csc if caractere.isdigit())
    if not apenas_digitos:
        raise ValueError("O ID do CSC deve conter ao menos um digito.")

    normalizado = apenas_digitos.lstrip("0") or "0"
    if len(normalizado) > 6:
        raise ValueError("O ID do CSC deve ter no maximo 6 digitos.")

    return normalizado


def montar_qrcode_nfce_v2_online(
    *,
    url_consulta: str,
    chave_acesso: str,
    ambiente: str,
    id_csc: str,
    token_csc: str,
) -> str:
    """Monta o QR Code NFC-e versao 2 para emissao online."""
    id_csc_normalizado = normalizar_id_csc(id_csc)
    parametros_sem_hash = f"{chave_acesso}|2|{ambiente}|{id_csc_normalizado}"
    hash_qrcode = hashlib.sha1(
        f"{parametros_sem_hash}{token_csc}".encode("utf-8")
    ).hexdigest().upper()

    return f"{url_consulta}?p={parametros_sem_hash}|{hash_qrcode}"


def obter_senha_certificado(config: dict, config_emitente: dict | None = None) -> str:
    """Le a senha do certificado sem gravar esse dado no codigo."""
    senha = os.getenv("SCFACIL_CERT_PASSWORD")
    if senha:
        return senha

    senha_emitente = obter_config(config_emitente or {}, "certificado.senha")
    if senha_emitente:
        return str(senha_emitente)

    senha_config = obter_config(config, "certificado.senha")
    if senha_config:
        return str(senha_config)

    return getpass.getpass("Senha do certificado: ")


def ler_ultimo_numero(caminho: Path) -> int:
    """Le o ultimo numero autorizado salvo em arquivo texto."""
    if not caminho.exists():
        return 0

    conteudo = caminho.read_text(encoding="utf-8-sig").strip()
    if not conteudo:
        return 0

    try:
        return int(conteudo)
    except ValueError as erro:
        raise ValueError(f"Numero invalido em {caminho}: {conteudo}") from erro


def salvar_ultimo_numero(caminho: Path, numero: int) -> None:
    """Salva o ultimo numero autorizado."""
    caminho.write_text(str(numero), encoding="utf-8")


def montar_codigo_numerico(numero: int) -> str:
    """Gera um cNF de 8 digitos sem iniciar por zero."""
    return f"{10000000 + (numero % 90000000):08d}"


def obter_numeracao(
    *,
    numero_informado: int | None,
    caminho_ultimo_numero: Path,
) -> DadosNumeracao:
    """Define serie 1 e o proximo numero do documento."""
    serie = 1

    if numero_informado is not None:
        if numero_informado <= 0:
            raise ValueError("O numero informado deve ser maior que zero.")
        numero = numero_informado
    else:
        numero = ler_ultimo_numero(caminho_ultimo_numero) + 1

    return DadosNumeracao(
        serie=serie,
        numero=numero,
        codigo_numerico=montar_codigo_numerico(numero),
    )


def carregar_emitente_do_certificado(
    *,
    caminho_certificado: Path,
    senha_certificado: str,
    emitente_base: EmitenteExemplo,
) -> EmitenteExemplo:
    """Usa CNPJ e razao social do certificado no emitente da NFC-e."""
    if not caminho_certificado.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {caminho_certificado}")

    certificado = Certificado(str(caminho_certificado), senha_certificado)
    cnpj_cpf = "".join(
        caractere for caractere in certificado.cnpj_cpf if caractere.isdigit()
    )
    if len(cnpj_cpf) != 14:
        raise ValueError(
            "O certificado carregado nao possui um CNPJ valido para emissao NFC-e."
        )

    razao_social = certificado.proprietario.split(":", 1)[0].strip()
    if not razao_social:
        razao_social = emitente_base.nome

    return replace(
        emitente_base,
        cnpj=cnpj_cpf,
        nome=razao_social[:60],
        fantasia=razao_social[:60],
    )


def montar_nfce_exemplo(
    *,
    emitente: EmitenteExemplo | None = None,
    tributacao: TributacaoExemplo | None = None,
    numeracao: DadosNumeracao,
    tipo_documento: str = "nfce",
    gerar_qrcode: bool = False,
    id_csc: str | None = None,
    token_csc: str | None = None,
    url_consulta: str = URL_CONSULTA_NFCE_RS,
) -> Nfe:
    emitente = emitente or EmitenteExemplo()
    tributacao = tributacao or TributacaoExemplo()
    item = ItemExemplo()
    tipo_documento = tipo_documento.lower()
    if tipo_documento not in {"nfce", "nfe"}:
        raise ValueError("tipo_documento deve ser 'nfce' ou 'nfe'.")

    eh_nfce = tipo_documento == "nfce"
    modelo = MODELO_NFCE if eh_nfce else MODELO_NFE
    modelo_enum = Tmod.VALUE_65 if eh_nfce else Tmod.VALUE_55
    impressao = IdeTpImp.VALUE_4 if eh_nfce else IdeTpImp.VALUE_1
    valor_bc_icms = item.valor_total if tributacao.regime_normal else Decimal("0.00")
    valor_icms = (
        calcular_percentual(valor_bc_icms, tributacao.icms_aliquota)
        if tributacao.regime_normal
        else Decimal("0.00")
    )
    crt = EmitCrt.VALUE_3 if tributacao.regime_normal else EmitCrt.VALUE_1
    icms_item = (
        Nfe.InfNfe.Det.Imposto.Icms(
            ICMS00=Nfe.InfNfe.Det.Imposto.Icms.Icms00(
                orig=Torig.VALUE_0,
                CST=Icms00Cst.VALUE_00,
                modBC={
                    "0": Icms00ModBc.VALUE_0,
                    "1": Icms00ModBc.VALUE_1,
                    "2": Icms00ModBc.VALUE_2,
                    "3": Icms00ModBc.VALUE_3,
                }.get(tributacao.icms_modalidade_base_calculo, Icms00ModBc.VALUE_3),
                vBC=moeda(valor_bc_icms),
                pICMS=moeda(tributacao.icms_aliquota),
                vICMS=moeda(valor_icms),
            )
        )
        if tributacao.regime_normal
        else Nfe.InfNfe.Det.Imposto.Icms(
            ICMSSN102=Nfe.InfNfe.Det.Imposto.Icms.Icmssn102(
                orig=Torig.VALUE_0,
                CSOSN=Icmssn102Csosn.VALUE_102,
            )
        )
    )

    data_emissao = datetime.now(FUSO_BRASIL).replace(microsecond=0)
    chave = montar_chave_acesso(
        codigo_uf=emitente.uf_codigo,
        data_emissao=data_emissao,
        cnpj_emitente=emitente.cnpj,
        modelo=modelo,
        serie=numeracao.serie,
        numero=numeracao.numero,
        tipo_emissao="1",
        codigo_numerico=numeracao.codigo_numerico,
    )

    inf_nfe = Nfe.InfNfe(
        versao=VERSAO_NFE,
        Id=f"NFe{chave}",
        ide=Nfe.InfNfe.Ide(
            cUF=TcodUfIbge.VALUE_43,
            cNF=numeracao.codigo_numerico,
            natOp="VENDA",
            mod=modelo_enum,
            serie=str(numeracao.serie),
            nNF=str(numeracao.numero),
            dhEmi=data_emissao.isoformat(),
            tpNF=IdeTpNf.VALUE_1,
            idDest=IdeIdDest.VALUE_1,
            cMunFG=emitente.municipio_codigo,
            tpImp=impressao,
            tpEmis=IdeTpEmis.VALUE_1,
            cDV=chave[-1],
            # Este exemplo trabalha sempre em homologacao.
            tpAmb=Tamb.VALUE_2,
            finNFe=TfinNfe.VALUE_1,
            indFinal=IdeIndFinal.VALUE_1,
            indPres=IdeIndPres.VALUE_1,
            procEmi="0",
            verProc="SCFacil exemplo 0.1",
        ),
        emit=Nfe.InfNfe.Emit(
            CNPJ=emitente.cnpj,
            xNome=emitente.nome,
            xFant=emitente.fantasia,
            enderEmit=TenderEmi(
                xLgr=emitente.logradouro,
                nro=emitente.numero,
                xBairro=emitente.bairro,
                cMun=emitente.municipio_codigo,
                xMun=emitente.municipio_nome,
                UF=TufEmi.RS,
                CEP=emitente.cep,
                cPais=TenderEmiCPais.VALUE_1058,
                xPais=TenderEmiXPais.BRASIL,
                fone="5133334444",
            ),
            IE=emitente.inscricao_estadual,
            CRT=crt,
        ),
        dest=Nfe.InfNfe.Dest(
            CPF="00000000191",
            xNome="NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL",
            enderDest=Tendereco(
                xLgr="Rua Teste",
                nro="100",
                xBairro="Centro",
                cMun=emitente.municipio_codigo,
                xMun=emitente.municipio_nome,
                UF=Tuf.RS,
                CEP=emitente.cep,
                cPais="1058",
                xPais="BRASIL",
                fone="5133334444",
            )
            if not eh_nfce
            else None,
            indIEDest=DestIndIedest.VALUE_9,
        ),
        det=[
            Nfe.InfNfe.Det(
                nItem=1,
                prod=Nfe.InfNfe.Det.Prod(
                    cProd=item.codigo,
                    cEAN="SEM GTIN",
                    xProd=item.descricao,
                    NCM=item.ncm,
                    CFOP=item.cfop,
                    uCom=item.unidade,
                    qCom=quantidade(item.quantidade),
                    vUnCom=moeda(item.valor_unitario),
                    vProd=moeda(item.valor_total),
                    cEANTrib="SEM GTIN",
                    uTrib=item.unidade,
                    qTrib=quantidade(item.quantidade),
                    vUnTrib=moeda(item.valor_unitario),
                    indTot=ProdIndTot.VALUE_1,
                ),
                imposto=Nfe.InfNfe.Det.Imposto(
                    ICMS=icms_item,
                    PIS=Nfe.InfNfe.Det.Imposto.Pis(
                        PISNT=Nfe.InfNfe.Det.Imposto.Pis.Pisnt(
                            CST=PisntCst.VALUE_07,
                        )
                    ),
                    COFINS=Nfe.InfNfe.Det.Imposto.Cofins(
                        COFINSNT=Nfe.InfNfe.Det.Imposto.Cofins.Cofinsnt(
                            CST=CofinsntCst.VALUE_07,
                        )
                    ),
                ),
            )
        ],
        total=Nfe.InfNfe.Total(
            ICMSTot=Nfe.InfNfe.Total.Icmstot(
                vBC=moeda(valor_bc_icms),
                vICMS=moeda(valor_icms),
                vICMSDeson="0.00",
                vFCPUFDest="0.00",
                vICMSUFDest="0.00",
                vICMSUFRemet="0.00",
                vFCP="0.00",
                vBCST="0.00",
                vST="0.00",
                vFCPST="0.00",
                vFCPSTRet="0.00",
                vProd=moeda(item.valor_total),
                vFrete="0.00",
                vSeg="0.00",
                vDesc="0.00",
                vII="0.00",
                vIPI="0.00",
                vIPIDevol="0.00",
                vPIS="0.00",
                vCOFINS="0.00",
                vOutro="0.00",
                vNF=moeda(item.valor_total),
            )
        ),
        transp=Nfe.InfNfe.Transp(
            modFrete=TranspModFrete.VALUE_9,
        ),
        pag=Nfe.InfNfe.Pag(
            detPag=[
                Nfe.InfNfe.Pag.DetPag(
                    indPag=DetPagIndPag.VALUE_0,
                    tPag="01",
                    vPag=moeda(item.valor_total),
                )
            ]
        ),
    )

    if not eh_nfce or not gerar_qrcode:
        return Nfe(infNFe=inf_nfe)

    if not id_csc:
        raise ValueError("Informe o ID do CSC em SCFACIL_CSC_ID ou --csc-id.")
    if not token_csc:
        raise ValueError("Informe o token CSC na variavel SCFACIL_CSC_TOKEN.")

    qrcode = montar_qrcode_nfce_v2_online(
        url_consulta=url_consulta,
        chave_acesso=chave,
        ambiente=AMBIENTE_HOMOLOGACAO,
        id_csc=id_csc,
        token_csc=token_csc,
    )

    return Nfe(
        infNFe=inf_nfe,
        infNFeSupl=Nfe.InfNfeSupl(
            qrCode=qrcode,
            urlChave=url_consulta,
        ),
    )


def montar_mdfe_exemplo(
    *,
    emitente: EmitenteExemplo,
    numeracao: DadosNumeracao,
    caminho_modelo: Path = EXEMPLO_MDFE_PADRAO,
) -> str:
    """Monta um MDF-e a partir do XML modelo, atualizando emitente e numeracao."""
    if not caminho_modelo.exists():
        raise FileNotFoundError(f"Modelo MDF-e nao encontrado: {caminho_modelo}")

    parser = etree.XMLParser(remove_blank_text=True)
    raiz = etree.fromstring(caminho_modelo.read_bytes(), parser=parser)
    ns = {"mdfe": NAMESPACE_MDFE, "ds": "http://www.w3.org/2000/09/xmldsig#"}

    mdfe = raiz.find(".//mdfe:MDFe", namespaces=ns)
    if mdfe is None and raiz.tag == f"{{{NAMESPACE_MDFE}}}MDFe":
        mdfe = raiz
    if mdfe is None:
        raise ValueError("O arquivo exemploMDFE.xml nao contem a tag MDFe.")

    mdfe = etree.fromstring(etree.tostring(mdfe), parser=parser)
    for assinatura in mdfe.xpath("./ds:Signature", namespaces=ns):
        assinatura.getparent().remove(assinatura)

    data_emissao = datetime.now(FUSO_BRASIL).replace(microsecond=0)
    chave = montar_chave_acesso(
        codigo_uf=emitente.uf_codigo,
        data_emissao=data_emissao,
        cnpj_emitente=emitente.cnpj,
        modelo=MODELO_MDFE,
        serie=numeracao.serie,
        numero=numeracao.numero,
        tipo_emissao="1",
        codigo_numerico=numeracao.codigo_numerico,
    )

    def set_text(xpath: str, valor: str) -> None:
        elemento = mdfe.find(xpath, namespaces=ns)
        if elemento is not None:
            elemento.text = valor

    inf_mdfe = mdfe.find("mdfe:infMDFe", namespaces=ns)
    if inf_mdfe is None:
        raise ValueError("O modelo MDF-e nao contem infMDFe.")
    inf_mdfe.set("Id", f"MDFe{chave}")
    inf_mdfe.set("versao", VERSAO_MDFE)

    set_text(".//mdfe:ide/mdfe:cUF", emitente.uf_codigo)
    set_text(".//mdfe:ide/mdfe:tpAmb", AMBIENTE_HOMOLOGACAO)
    set_text(".//mdfe:ide/mdfe:mod", MODELO_MDFE)
    set_text(".//mdfe:ide/mdfe:serie", str(numeracao.serie))
    set_text(".//mdfe:ide/mdfe:nMDF", str(numeracao.numero))
    set_text(".//mdfe:ide/mdfe:cMDF", numeracao.codigo_numerico)
    set_text(".//mdfe:ide/mdfe:cDV", chave[-1])
    set_text(".//mdfe:ide/mdfe:dhEmi", data_emissao.isoformat())
    set_text(".//mdfe:ide/mdfe:dhIniViagem", data_emissao.isoformat())
    set_text(".//mdfe:ide/mdfe:UFIni", emitente.uf_sigla)
    set_text(".//mdfe:ide/mdfe:UFFim", emitente.uf_sigla)
    set_text(".//mdfe:ide/mdfe:infMunCarrega/mdfe:cMunCarrega", emitente.municipio_codigo)
    set_text(".//mdfe:ide/mdfe:infMunCarrega/mdfe:xMunCarrega", emitente.municipio_nome.upper())

    set_text(".//mdfe:emit/mdfe:CNPJ", emitente.cnpj)
    set_text(".//mdfe:emit/mdfe:IE", emitente.inscricao_estadual)
    set_text(".//mdfe:emit/mdfe:xNome", emitente.nome)
    set_text(".//mdfe:emit/mdfe:xFant", emitente.fantasia)
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:xLgr", emitente.logradouro.upper())
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:nro", emitente.numero)
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:xBairro", emitente.bairro.upper())
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:cMun", emitente.municipio_codigo)
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:xMun", emitente.municipio_nome.upper())
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:CEP", emitente.cep)
    set_text(".//mdfe:emit/mdfe:enderEmit/mdfe:UF", emitente.uf_sigla)

    set_text(
        ".//mdfe:infMDFeSupl/mdfe:qrCodMDFe",
        f"https://dfe-portal.svrs.rs.gov.br/mdfe/qrCode?chMDFe={chave}&tpAmb={AMBIENTE_HOMOLOGACAO}",
    )

    return etree.tostring(mdfe, encoding="unicode", pretty_print=False)


def montar_cte_exemplo(
    *,
    emitente: EmitenteExemplo,
    tributacao: TributacaoExemplo | None = None,
    numeracao: DadosNumeracao,
    caminho_modelo: Path = EXEMPLO_CTE_PADRAO,
) -> str:
    """Monta um CT-e a partir do XML modelo, atualizando emitente e numeracao."""
    tributacao = tributacao or TributacaoExemplo()
    if not caminho_modelo.exists():
        raise FileNotFoundError(f"Modelo CT-e nao encontrado: {caminho_modelo}")

    parser = etree.XMLParser(remove_blank_text=True)
    raiz = etree.fromstring(caminho_modelo.read_bytes(), parser=parser)
    ns = {"cte": NAMESPACE_CTE, "ds": "http://www.w3.org/2000/09/xmldsig#"}

    cte = raiz.find(".//cte:CTe", namespaces=ns)
    if cte is None and raiz.tag == f"{{{NAMESPACE_CTE}}}CTe":
        cte = raiz
    if cte is None:
        raise ValueError("O arquivo Exemplo-cte.xml nao contem a tag CTe.")

    cte = etree.fromstring(etree.tostring(cte), parser=parser)
    for assinatura in cte.xpath("./ds:Signature", namespaces=ns):
        assinatura.getparent().remove(assinatura)

    data_emissao = datetime.now(FUSO_BRASIL).replace(microsecond=0)
    chave = montar_chave_acesso(
        codigo_uf=emitente.uf_codigo,
        data_emissao=data_emissao,
        cnpj_emitente=emitente.cnpj,
        modelo=MODELO_CTE,
        serie=numeracao.serie,
        numero=numeracao.numero,
        tipo_emissao="1",
        codigo_numerico=numeracao.codigo_numerico,
    )

    def set_text(xpath: str, valor: str) -> None:
        elemento = cte.find(xpath, namespaces=ns)
        if elemento is not None:
            elemento.text = valor

    inf_cte = cte.find("cte:infCte", namespaces=ns)
    if inf_cte is None:
        raise ValueError("O modelo CT-e nao contem infCte.")
    inf_cte.set("Id", f"CTe{chave}")
    inf_cte.set("versao", VERSAO_CTE)

    set_text(".//cte:ide/cte:cUF", emitente.uf_codigo)
    set_text(".//cte:ide/cte:cCT", numeracao.codigo_numerico)
    set_text(".//cte:ide/cte:mod", MODELO_CTE)
    set_text(".//cte:ide/cte:serie", str(numeracao.serie))
    set_text(".//cte:ide/cte:nCT", str(numeracao.numero))
    set_text(".//cte:ide/cte:dhEmi", data_emissao.isoformat())
    set_text(".//cte:ide/cte:cDV", chave[-1])
    set_text(".//cte:ide/cte:tpAmb", AMBIENTE_HOMOLOGACAO)
    set_text(".//cte:ide/cte:cMunEnv", emitente.municipio_codigo)
    set_text(".//cte:ide/cte:xMunEnv", emitente.municipio_nome.upper())
    set_text(".//cte:ide/cte:UFEnv", emitente.uf_sigla)
    set_text(".//cte:ide/cte:cMunIni", emitente.municipio_codigo)
    set_text(".//cte:ide/cte:xMunIni", emitente.municipio_nome.upper())
    set_text(".//cte:ide/cte:UFIni", emitente.uf_sigla)

    set_text(".//cte:emit/cte:CNPJ", emitente.cnpj)
    set_text(".//cte:emit/cte:IE", emitente.inscricao_estadual)
    set_text(".//cte:emit/cte:xNome", emitente.nome)
    set_text(".//cte:emit/cte:xFant", emitente.fantasia)
    set_text(".//cte:emit/cte:enderEmit/cte:xLgr", emitente.logradouro.upper())
    set_text(".//cte:emit/cte:enderEmit/cte:nro", emitente.numero)
    set_text(".//cte:emit/cte:enderEmit/cte:xBairro", emitente.bairro.upper())
    set_text(".//cte:emit/cte:enderEmit/cte:cMun", emitente.municipio_codigo)
    set_text(".//cte:emit/cte:enderEmit/cte:xMun", emitente.municipio_nome.upper())
    set_text(".//cte:emit/cte:enderEmit/cte:CEP", emitente.cep)
    set_text(".//cte:emit/cte:enderEmit/cte:UF", emitente.uf_sigla)
    set_text(".//cte:emit/cte:CRT", "3" if tributacao.regime_normal else emitente.crt)

    if tributacao.regime_normal:
        imp = cte.find(".//cte:imp", namespaces=ns)
        icms = cte.find(".//cte:imp/cte:ICMS", namespaces=ns)
        if imp is not None and icms is not None:
            for filho in list(icms):
                icms.remove(filho)
            valor_servico = Decimal(texto_xml(cte, ".//cte:vPrest/cte:vTPrest", ns, "0.00"))
            valor_icms = calcular_percentual(valor_servico, tributacao.icms_aliquota)
            icms00 = etree.SubElement(icms, etree.QName(NAMESPACE_CTE, "ICMS00"))
            etree.SubElement(icms00, etree.QName(NAMESPACE_CTE, "CST")).text = tributacao.icms_cst
            etree.SubElement(icms00, etree.QName(NAMESPACE_CTE, "vBC")).text = moeda(valor_servico)
            etree.SubElement(icms00, etree.QName(NAMESPACE_CTE, "pICMS")).text = moeda(tributacao.icms_aliquota)
            etree.SubElement(icms00, etree.QName(NAMESPACE_CTE, "vICMS")).text = moeda(valor_icms)

    url_qrcode = (
        "https://homologacao.nfe.fazenda.sp.gov.br/CTeConsulta/qrCode"
        if emitente.uf_sigla == "SP"
        else "https://dfe-portal.svrs.rs.gov.br/cte/qrCode"
    )
    set_text(
        ".//cte:infCTeSupl/cte:qrCodCTe",
        f"{url_qrcode}?chCTe={chave}&tpAmb={AMBIENTE_HOMOLOGACAO}",
    )

    return etree.tostring(cte, encoding="unicode", pretty_print=False)


def assinar_nfce(
    xml: str,
    nfce: Nfe,
    caminho_certificado: Path,
    senha_certificado: str,
) -> str:
    """Assina o XML usando certificado A1 em formato PFX."""
    if not caminho_certificado.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {caminho_certificado}")

    return Nfe.sign_xml(
        xml,
        pkcs12_data=str(caminho_certificado),
        pkcs12_password=senha_certificado,
        doc_id=nfce.infNFe.Id,
    )


def assinar_mdfe(
    xml: str,
    chave: str,
    caminho_certificado: Path,
    senha_certificado: str,
) -> str:
    """Assina o XML MDF-e usando certificado A1 em formato PFX."""
    if not caminho_certificado.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {caminho_certificado}")

    return Mdfe.sign_xml(
        xml,
        pkcs12_data=str(caminho_certificado),
        pkcs12_password=senha_certificado,
        doc_id=f"MDFe{chave}",
    )


def assinar_cte(
    xml: str,
    chave: str,
    caminho_certificado: Path,
    senha_certificado: str,
) -> str:
    """Assina o XML CT-e usando certificado A1 em formato PFX."""
    if not caminho_certificado.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {caminho_certificado}")

    return Cte.sign_xml(
        xml,
        pkcs12_data=str(caminho_certificado),
        pkcs12_password=senha_certificado,
        doc_id=f"CTe{chave}",
    )


def compactar_xml(xml: str) -> str:
    """Remove espacos de formatacao sem alterar dados reais do XML."""
    parser = etree.XMLParser(remove_blank_text=True)
    raiz = etree.fromstring(xml.encode("utf-8"), parser=parser)
    return etree.tostring(raiz, encoding="unicode", pretty_print=False)


def texto_xml(raiz: etree._Element, xpath: str, namespaces: dict, padrao: str = "") -> str:
    """Le texto de um elemento XML com valor padrao."""
    valor = raiz.findtext(xpath, namespaces=namespaces)
    return valor if valor is not None else padrao


def obter_chave_nfce(nfce: Nfe) -> str:
    """Extrai a chave de acesso a partir do Id da infNFe."""
    return nfce.infNFe.Id.removeprefix("NFe")


def obter_chave_xml(xml: str, prefixo: str) -> str:
    """Extrai a chave a partir do atributo Id do XML."""
    raiz = etree.fromstring(xml.encode("utf-8"))
    valor = raiz.xpath("string(.//@Id)")
    return valor.removeprefix(prefixo)


def pasta_ano_mes_chave(chave: str) -> str:
    """Retorna aaaamm a partir da chave de acesso."""
    ano_mes = chave[2:6] if len(chave) >= 6 else datetime.now(FUSO_BRASIL).strftime("%y%m")
    return f"20{ano_mes}"


def caminho_saida_documento(pasta_saida: Path, chave: str) -> Path:
    """Pasta mensal onde os arquivos finais do documento ficam guardados."""
    pasta = pasta_saida / pasta_ano_mes_chave(chave)
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def mover_para_pasta_mensal(caminho: Path, chave: str, pasta_saida: Path) -> Path:
    """Move arquivo do documento para a pasta aaaamm, se necessario."""
    if not caminho.exists():
        return caminho
    destino = caminho_saida_documento(pasta_saida, chave) / caminho.name
    if caminho.resolve() == destino.resolve():
        return caminho
    if destino.exists():
        destino.unlink()
    shutil.move(str(caminho), str(destino))
    return destino


def montar_lote_envio(xml_assinado: str, id_lote: str) -> str:
    """Monta o enviNFe síncrono para autorizacao da NFC-e."""
    nfe_assinada = etree.fromstring(xml_assinado.encode("utf-8"))
    envi_nfe = etree.Element(
        etree.QName(NAMESPACE_NFE, "enviNFe"),
        nsmap={None: NAMESPACE_NFE},
        versao=VERSAO_NFE,
    )
    etree.SubElement(envi_nfe, etree.QName(NAMESPACE_NFE, "idLote")).text = id_lote
    etree.SubElement(envi_nfe, etree.QName(NAMESPACE_NFE, "indSinc")).text = "1"
    envi_nfe.append(nfe_assinada)

    return etree.tostring(envi_nfe, encoding="unicode", pretty_print=False)


def montar_lote_envio_mdfe(xml_assinado: str, id_lote: str) -> str:
    """Monta o enviMDFe para autorizacao sincrona do MDF-e."""
    mdfe_assinado = etree.fromstring(xml_assinado.encode("utf-8"))
    envi_mdfe = etree.Element(
        etree.QName(NAMESPACE_MDFE, "enviMDFe"),
        nsmap={None: NAMESPACE_MDFE},
        versao=VERSAO_MDFE,
    )
    etree.SubElement(envi_mdfe, etree.QName(NAMESPACE_MDFE, "idLote")).text = id_lote
    envi_mdfe.append(mdfe_assinado)

    return etree.tostring(envi_mdfe, encoding="unicode", pretty_print=False)


def transmitir_documento_homologacao(
    *,
    xml_assinado: str,
    chave: str,
    certificado: Certificado,
    pasta_saida: Path,
    tipo_documento: str,
    uf_emitente: str,
) -> str:
    """Transmite NF-e/NFC-e para a SVRS em homologacao e salva o retorno bruto."""
    id_lote = chave[-15:]
    xml_lote = (
        xml_assinado
        if tipo_documento in {"mdfe", "cte"}
        else montar_lote_envio(xml_assinado, id_lote)
    )
    pasta_documento = caminho_saida_documento(pasta_saida, chave)
    caminho_lote = pasta_documento / f"{chave}-lote.xml"
    caminho_lote.write_text(xml_lote, encoding="utf-8")
    url_autorizacao = {
        "nfce": URL_AUTORIZACAO_NFCE_HOMOLOGACAO_SVRS,
        "nfe": URL_AUTORIZACAO_NFE_HOMOLOGACAO_SVRS,
        "mdfe": URL_AUTORIZACAO_MDFE_HOMOLOGACAO_SVRS,
        "cte": URL_AUTORIZACAO_CTE_HOMOLOGACAO_SP
        if uf_emitente == "SP"
        else URL_AUTORIZACAO_CTE_HOMOLOGACAO_SVRS,
    }[tipo_documento]

    with ArquivoCertificado(certificado, "w") as (cert_path, key_path):
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        session = Session()
        session.cert = (cert_path, key_path)
        # A propria erpbrasil.transmissao usa verify=False por padrao para estes
        # webservices. O certificado A1 do cliente continua sendo enviado.
        session.verify = False
        transport = Transport(
            session=session,
            cache=SqliteCache(path=str(pasta_saida / "zeep-cache.db"), timeout=60),
            timeout=30,
        )
        if tipo_documento in {"mdfe", "cte"}:
            wsdl = (
                WSDL_AUTORIZACAO_MDFE_HOMOLOGACAO_SVRS
                if tipo_documento == "mdfe"
                else WSDL_AUTORIZACAO_CTE_HOMOLOGACAO_SP
                if uf_emitente == "SP"
                else WSDL_AUTORIZACAO_CTE_HOMOLOGACAO_SVRS
            )
            binding = (
                "{http://www.portalfiscal.inf.br/mdfe/wsdl/MDFeRecepcaoSinc}MDFeRecepcaoSincSoap12"
                if tipo_documento == "mdfe"
                else "{http://www.portalfiscal.inf.br/cte/wsdl/CTeRecepcaoSincV4}CTeRecepcaoSincV4Soap12"
            )
            metodo = "mdfeRecepcao" if tipo_documento == "mdfe" else "cteRecepcao"
            client = Client(wsdl, transport=transport)
            service = client.create_service(
                binding,
                url_autorizacao,
            )
            xml_lote_compactado = base64.b64encode(
                gzip.compress(xml_lote.encode("utf-8"))
            ).decode("ascii")
            with client.settings(raw_response=True):
                resposta = getattr(service, metodo)(xml_lote_compactado)
        else:
            client = Client(str(WSDL_AUTORIZACAO_NFE), transport=transport)
            service = client.create_service(
                "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4}NFeAutorizacao4Soap12",
                url_autorizacao,
            )
            with client.settings(raw_response=True):
                resposta = service.nfeAutorizacaoLote(
                    etree.fromstring(xml_lote.encode("utf-8"))
                )

    retorno = resposta.text
    caminho_retorno = pasta_documento / f"{chave}-retorno.xml"
    caminho_retorno.write_text(retorno, encoding="utf-8")
    return retorno


def extrair_protocolo_autorizado(xml_retorno: str, tipo_documento: str = "nfce") -> etree._Element | None:
    """Retorna o protocolo autorizado quando a SEFAZ autoriza o documento."""
    raiz = etree.fromstring(xml_retorno.encode("utf-8"))
    if tipo_documento == "mdfe":
        namespaces = {"mdfe": NAMESPACE_MDFE}
        for protocolo in raiz.xpath(".//mdfe:protMDFe", namespaces=namespaces):
            cstat = protocolo.findtext(".//mdfe:cStat", namespaces=namespaces)
            if cstat == "100":
                return protocolo
        return None

    if tipo_documento == "cte":
        namespaces = {"cte": NAMESPACE_CTE}
        for protocolo in raiz.xpath(".//cte:protCTe", namespaces=namespaces):
            cstat = protocolo.findtext(".//cte:cStat", namespaces=namespaces)
            if cstat == "100":
                return protocolo
        return None

    namespaces = {"nfe": NAMESPACE_NFE}
    for protocolo in raiz.xpath(".//nfe:protNFe", namespaces=namespaces):
        cstat = protocolo.findtext(".//nfe:cStat", namespaces=namespaces)
        if cstat == "100":
            return protocolo

    return None


def extrair_status_retorno(xml_retorno: str | None, tipo_documento: str = "nfce") -> dict:
    """Extrai cStat/xMotivo/protocolo de qualquer retorno de autorizacao."""
    if not xml_retorno:
        return {"cstat": "", "motivo": "", "protocolo": "", "data_recebimento": ""}

    raiz = etree.fromstring(xml_retorno.encode("utf-8"))
    if tipo_documento == "mdfe":
        ns = {"dfe": NAMESPACE_MDFE}
        prot_xpath = ".//dfe:protMDFe/dfe:infProt"
    elif tipo_documento == "cte":
        ns = {"dfe": NAMESPACE_CTE}
        prot_xpath = ".//dfe:protCTe/dfe:infProt"
    else:
        ns = {"dfe": NAMESPACE_NFE}
        prot_xpath = ".//dfe:protNFe/dfe:infProt"

    protocolo = raiz.find(prot_xpath, namespaces=ns)
    alvo = protocolo if protocolo is not None else raiz
    return {
        "cstat": alvo.findtext(".//dfe:cStat", namespaces=ns) or "",
        "motivo": alvo.findtext(".//dfe:xMotivo", namespaces=ns) or "",
        "protocolo": alvo.findtext(".//dfe:nProt", namespaces=ns) or "",
        "data_recebimento": alvo.findtext(".//dfe:dhRecbto", namespaces=ns) or "",
    }


def retorno_tem_autorizacao(xml_retorno: str | None, tipo_documento: str = "nfce") -> bool:
    """Indica se o retorno contem protocolo autorizado."""
    if not xml_retorno:
        return False
    return extrair_protocolo_autorizado(xml_retorno, tipo_documento) is not None


def montar_xml_processado(
    xml_assinado: str,
    protocolo: etree._Element,
    tipo_documento: str = "nfce",
) -> str:
    """Monta o XML processado com documento assinado e protocolo de autorizacao."""
    documento_assinado = etree.fromstring(xml_assinado.encode("utf-8"))
    if tipo_documento == "mdfe":
        xml_proc = etree.Element(
            etree.QName(NAMESPACE_MDFE, "mdfeProc"),
            nsmap={None: NAMESPACE_MDFE},
            versao=VERSAO_MDFE,
        )
    elif tipo_documento == "cte":
        xml_proc = etree.Element(
            etree.QName(NAMESPACE_CTE, "cteProc"),
            nsmap={None: NAMESPACE_CTE},
            versao=VERSAO_CTE,
        )
    else:
        xml_proc = etree.Element(
            etree.QName(NAMESPACE_NFE, "nfeProc"),
            nsmap={None: NAMESPACE_NFE},
            versao=VERSAO_NFE,
        )
    xml_proc.append(documento_assinado)
    xml_proc.append(protocolo)

    return etree.tostring(
        xml_proc,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
    ).decode("utf-8")


def dados_documento_xml(xml: str, tipo_documento: str) -> dict:
    """Extrai numero, serie, emissao e valor do XML do documento."""
    raiz = etree.fromstring(xml.encode("utf-8"))
    if tipo_documento == "mdfe":
        ns = {"dfe": NAMESPACE_MDFE}
        return {
            "numero": raiz.findtext(".//dfe:ide/dfe:nMDF", namespaces=ns) or "",
            "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
            "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
            "valor": raiz.findtext(".//dfe:tot/dfe:vCarga", namespaces=ns) or "",
        }
    if tipo_documento == "cte":
        ns = {"dfe": NAMESPACE_CTE}
        return {
            "numero": raiz.findtext(".//dfe:ide/dfe:nCT", namespaces=ns) or "",
            "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
            "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
            "valor": raiz.findtext(".//dfe:vPrest/dfe:vTPrest", namespaces=ns) or "",
        }

    ns = {"dfe": NAMESPACE_NFE}
    return {
        "numero": raiz.findtext(".//dfe:ide/dfe:nNF", namespaces=ns) or "",
        "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
        "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
        "valor": raiz.findtext(".//dfe:total/dfe:ICMSTot/dfe:vNF", namespaces=ns) or "",
    }


def carregar_registro_dfe(caminho: Path = REGISTRO_DFE_PADRAO) -> list[dict]:
    if not caminho.exists():
        return []
    return json.loads(caminho.read_text(encoding="utf-8-sig") or "[]")


def salvar_registro_dfe(registros: list[dict], caminho: Path = REGISTRO_DFE_PADRAO) -> None:
    caminho.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def registrar_dfe(registro: dict, caminho: Path = REGISTRO_DFE_PADRAO) -> None:
    """Insere ou atualiza um documento emitido/transmitido no registro local."""
    registros = carregar_registro_dfe(caminho)
    chave = registro.get("chave")
    registros = [item for item in registros if item.get("chave") != chave]
    registros.append(registro)
    registros.sort(key=lambda item: str(item.get("atualizado_em", "")), reverse=True)
    salvar_registro_dfe(registros, caminho)


def gerar_pdf_nfce(xml: str, caminho_pdf: Path) -> None:
    """Gera DANFE NFC-e em formato de cupom, com dados principais da venda."""
    from fpdf import FPDF
    import qrcode

    raiz = etree.fromstring(xml.encode("utf-8"))
    ns = {"nfe": NAMESPACE_NFE}

    def texto(xpath: str, padrao: str = "") -> str:
        valor = raiz.findtext(xpath, namespaces=ns)
        return valor if valor is not None else padrao

    chave = texto(".//nfe:infProt/nfe:chNFe") or raiz.xpath(
        "string(.//nfe:infNFe/@Id)", namespaces=ns
    ).removeprefix("NFe")
    emitente = texto(".//nfe:emit/nfe:xNome")
    fantasia = texto(".//nfe:emit/nfe:xFant")
    cnpj = texto(".//nfe:emit/nfe:CNPJ")
    ie = texto(".//nfe:emit/nfe:IE")
    endereco = " ".join(
        parte
        for parte in (
            texto(".//nfe:enderEmit/nfe:xLgr"),
            texto(".//nfe:enderEmit/nfe:nro"),
            texto(".//nfe:enderEmit/nfe:xBairro"),
            texto(".//nfe:enderEmit/nfe:xMun"),
            texto(".//nfe:enderEmit/nfe:UF"),
        )
        if parte
    )
    destinatario = texto(".//nfe:dest/nfe:xNome")
    cpf_dest = texto(".//nfe:dest/nfe:CPF")
    serie = texto(".//nfe:ide/nfe:serie")
    numero = texto(".//nfe:ide/nfe:nNF")
    emissao = texto(".//nfe:ide/nfe:dhEmi")
    valor_total = texto(".//nfe:total/nfe:ICMSTot/nfe:vNF", "0.00")
    valor_produtos = texto(".//nfe:total/nfe:ICMSTot/nfe:vProd", "0.00")
    valor_desconto = texto(".//nfe:total/nfe:ICMSTot/nfe:vDesc", "0.00")
    forma_pagamento = texto(".//nfe:pag/nfe:detPag/nfe:tPag")
    valor_pagamento = texto(".//nfe:pag/nfe:detPag/nfe:vPag", "0.00")
    protocolo = texto(".//nfe:infProt/nfe:nProt")
    recebido = texto(".//nfe:infProt/nfe:dhRecbto")
    cstat = texto(".//nfe:infProt/nfe:cStat")
    motivo = texto(".//nfe:infProt/nfe:xMotivo")
    qr_code = texto(".//nfe:infNFeSupl/nfe:qrCode")

    produtos = raiz.findall(".//nfe:det", namespaces=ns)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(0, 0, 0)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    coluna_x = 6
    coluna_w = 72
    y = 4

    def linha(
        texto_linha: str,
        tamanho: int = 7,
        negrito: bool = False,
        centro: bool = False,
        altura_linha: float = 3.7,
        fonte: str = "Helvetica",
    ) -> None:
        nonlocal y
        pdf.set_xy(coluna_x, y)
        pdf.set_font(fonte, "B" if negrito else "", tamanho)
        pdf.multi_cell(
            coluna_w,
            altura_linha,
            texto_linha,
            border=0,
            align="C" if centro else "L",
        )
        y = pdf.get_y()

    def linha_valor(rotulo: str, valor: str, tamanho: int = 7, negrito: bool = False) -> None:
        nonlocal y
        pdf.set_xy(coluna_x, y)
        pdf.set_font("Helvetica", "B" if negrito else "", tamanho)
        pdf.cell(coluna_w * 0.68, 4, rotulo, border=0, align="L")
        pdf.cell(coluna_w * 0.32, 4, valor, border=0, align="R")
        y += 4

    def separador(espaco: float = 1.2) -> None:
        nonlocal y
        y += espaco
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.15)
        pdf.line(coluna_x, y, coluna_x + coluna_w, y)
        y += espaco

    def par(rotulo: str, valor: str) -> None:
        if valor:
            linha(f"{rotulo}: {valor}", 6)

    def dinheiro(valor: str) -> str:
        try:
            return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError:
            return valor

    def forma_pagamento_texto(codigo: str) -> str:
        return {
            "01": "Dinheiro",
            "02": "Cheque",
            "03": "Cartao de credito",
            "04": "Cartao de debito",
            "05": "Credito loja",
            "10": "Vale alimentacao",
            "11": "Vale refeicao",
            "12": "Vale presente",
            "13": "Vale combustivel",
            "15": "Boleto bancario",
            "16": "Deposito bancario",
            "17": "PIX",
            "90": "Sem pagamento",
            "99": "Outros",
        }.get(codigo, codigo)

    linha(emitente, 9, True, True, 4)
    if fantasia and fantasia != emitente:
        linha(fantasia, 7, False, True)
    linha(f"CNPJ: {cnpj}", 7, False, True)
    linha(f"IE: {ie}", 7, False, True)
    linha(endereco, 7, False, True)
    linha("DOCUMENTO AUXILIAR DA NOTA FISCAL DE CONSUMIDOR ELETRONICA", 5, True, True, 3)

    pdf.set_fill_color(205, 205, 205)
    pdf.rect(coluna_x, y, coluna_w, 10, style="F")
    y += 1
    linha("EMITIDA EM AMBIENTE DE HOMOLOGACAO", 7, True, True, 3.6)
    linha("SEM VALOR FISCAL", 8, True, True, 3.6)
    y += 1

    linha("#   Cod  Descricao", 7, True, False, 3.4, "Courier")
    linha("        Qtd    Un   Vl Unit     Vl Total", 7, True, False, 3.4, "Courier")
    par("Emissao", emissao)
    for indice, det in enumerate(produtos, start=1):
        prod = det.find("nfe:prod", namespaces=ns)
        codigo = prod.findtext("nfe:cProd", namespaces=ns)
        descricao = prod.findtext("nfe:xProd", namespaces=ns)
        quantidade_item = prod.findtext("nfe:qCom", namespaces=ns)
        unidade = prod.findtext("nfe:uCom", namespaces=ns)
        valor_unitario = prod.findtext("nfe:vUnCom", namespaces=ns)
        valor_item = prod.findtext("nfe:vProd", namespaces=ns)
        descricao_linhas = descricao[:45]
        linha(f"{indice:03d} {codigo:<4} {descricao_linhas}", 7, False, False, 3.4, "Courier")
        linha(
            f"{'':>8}{quantidade_item:>8} {unidade:<3} x {dinheiro(valor_unitario):>8} {dinheiro(valor_item):>10}",
            7,
            False,
            False,
            3.4,
            "Courier",
        )

    y += 1
    linha_valor("QTD. TOTAL DE ITENS", f"{len(produtos):03d}", 8, True)
    linha_valor("VALOR TOTAL R$", dinheiro(valor_produtos), 8, True)
    if valor_desconto != "0.00":
        linha_valor("Descontos R$", dinheiro(valor_desconto), 7)
    linha_valor("VALOR A PAGAR R$", dinheiro(valor_total), 8, True)
    linha_valor("FORMA DE PAGAMENTO", "Valor Pago", 7)
    linha_valor(forma_pagamento_texto(forma_pagamento), dinheiro(valor_pagamento), 7)

    linha("Consulte pela Chave de Acesso em", 7, True, True, 3.5)
    linha("www.sefaz.rs.gov.br/nfce/consulta", 9, False, True, 4)
    linha(" ".join(chave[i : i + 4] for i in range(0, len(chave), 4)), 7, False, True)
    linha(f"CONSUMIDOR CPF: {cpf_dest}", 7, True, True)
    linha(destinatario, 7, False, True)
    linha(f"NFC-e nº {int(numero):09d} Série {serie} {emissao}", 7, True, True)
    if protocolo:
        linha(f"Protocolo de Autorização: {protocolo}", 7, False, True)
    if recebido:
        linha(f"Data de Autorização {recebido}", 7, False, True)
    if motivo:
        linha("EMITIDA EM AMBIENTE DE HOMOLOGAÇÃO", 7, False, True)
        linha("SEM VALOR FISCAL", 7, False, True)

    if qr_code:
        imagem_qr = qrcode.make(qr_code)
        with NamedTemporaryFile(suffix=".png", delete=False) as arquivo_qr:
            caminho_qr = Path(arquivo_qr.name)
            imagem_qr.save(caminho_qr)
        try:
            y += 4
            pdf.image(str(caminho_qr), x=coluna_x + 21, y=y, w=30)
            y += 33
        finally:
            caminho_qr.unlink(missing_ok=True)

    linha("SCFacil - modulo NFC-e", 6, True, True)
    pdf.output(str(caminho_pdf))


def gerar_pdf_cte_simples(xml: str, caminho_pdf: Path) -> None:
    """Gera um DACTE em A4 quando a biblioteca nao consegue renderizar o XML modelo."""
    from fpdf import FPDF
    import qrcode
    from barcode import Code128
    from barcode.writer import ImageWriter

    raiz = etree.fromstring(xml.encode("utf-8"))
    ns = {"cte": NAMESPACE_CTE}

    def texto(xpath: str, padrao: str = "") -> str:
        valor = raiz.findtext(xpath, namespaces=ns)
        return valor if valor is not None else padrao

    def dinheiro(valor: str) -> str:
        try:
            return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError:
            return valor

    def doc_formatado(valor: str) -> str:
        numeros = "".join(ch for ch in valor if ch.isdigit())
        if len(numeros) == 14:
            return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
        if len(numeros) == 11:
            return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"
        return valor

    chave = texto(".//cte:infProt/cte:chCTe") or raiz.xpath(
        "string(.//cte:infCte/@Id)", namespaces=ns
    ).removeprefix("CTe")
    protocolo = texto(".//cte:infProt/cte:nProt")
    recebido = texto(".//cte:infProt/cte:dhRecbto")
    motivo = texto(".//cte:infProt/cte:xMotivo")
    emitente = texto(".//cte:emit/cte:xNome")
    fantasia = texto(".//cte:emit/cte:xFant")
    cnpj_emitente = texto(".//cte:emit/cte:CNPJ")
    ie_emitente = texto(".//cte:emit/cte:IE")
    remetente = texto(".//cte:rem/cte:xNome")
    cnpj_rem = texto(".//cte:rem/cte:CNPJ") or texto(".//cte:rem/cte:CPF")
    ie_rem = texto(".//cte:rem/cte:IE")
    destinatario = texto(".//cte:dest/cte:xNome")
    cnpj_dest = texto(".//cte:dest/cte:CNPJ") or texto(".//cte:dest/cte:CPF")
    ie_dest = texto(".//cte:dest/cte:IE")
    numero = texto(".//cte:ide/cte:nCT")
    serie = texto(".//cte:ide/cte:serie")
    emissao = texto(".//cte:ide/cte:dhEmi")
    cfop = texto(".//cte:ide/cte:CFOP")
    natureza = texto(".//cte:ide/cte:natOp")
    tipo_cte = texto(".//cte:ide/cte:tpCTe")
    tipo_servico = texto(".//cte:ide/cte:tpServ")
    origem = f"{texto('.//cte:ide/cte:xMunIni')} / {texto('.//cte:ide/cte:UFIni')}"
    destino = f"{texto('.//cte:ide/cte:xMunFim')} / {texto('.//cte:ide/cte:UFFim')}"
    valor = texto(".//cte:vPrest/cte:vTPrest", "0.00")
    valor_receber = texto(".//cte:vPrest/cte:vRec", valor)
    valor_carga = texto(".//cte:infCarga/cte:vCarga", "")
    produto = texto(".//cte:infCarga/cte:proPred", "")
    chave_doc = texto(".//cte:infDoc//cte:chave") or texto(".//cte:infCteComp/cte:chCTe")
    observacao = texto(".//cte:compl/cte:xObs")
    qr_code = texto(".//cte:infCTeSupl/cte:qrCodCTe")

    end_emit = " - ".join(
        item
        for item in (
            texto(".//cte:emit/cte:enderEmit/cte:xLgr"),
            texto(".//cte:emit/cte:enderEmit/cte:nro"),
            texto(".//cte:emit/cte:enderEmit/cte:xBairro"),
            f"{texto('.//cte:emit/cte:enderEmit/cte:xMun')} - {texto('.//cte:emit/cte:enderEmit/cte:UF')}",
            f"CEP {texto('.//cte:emit/cte:enderEmit/cte:CEP')}",
        )
        if item and item.strip(" -")
    )
    end_rem = " - ".join(
        item
        for item in (
            texto(".//cte:rem/cte:enderReme/cte:xLgr"),
            texto(".//cte:rem/cte:enderReme/cte:nro"),
            texto(".//cte:rem/cte:enderReme/cte:xBairro"),
            f"{texto('.//cte:rem/cte:enderReme/cte:xMun')} - {texto('.//cte:rem/cte:enderReme/cte:UF')}",
            f"CEP {texto('.//cte:rem/cte:enderReme/cte:CEP')}",
        )
        if item and item.strip(" -")
    )
    end_dest = " - ".join(
        item
        for item in (
            texto(".//cte:dest/cte:enderDest/cte:xLgr"),
            texto(".//cte:dest/cte:enderDest/cte:nro"),
            texto(".//cte:dest/cte:enderDest/cte:xBairro"),
            f"{texto('.//cte:dest/cte:enderDest/cte:xMun')} - {texto('.//cte:dest/cte:enderDest/cte:UF')}",
            f"CEP {texto('.//cte:dest/cte:enderDest/cte:CEP')}",
        )
        if item and item.strip(" -")
    )

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(6, 6, 6)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    margem = 6
    largura = 198
    y = 8

    def rect(x: float, y0: float, w: float, h: float) -> None:
        pdf.rect(x, y0, w, h)

    def label(x: float, y0: float, w: float, h: float, titulo: str, valor_campo: str = "", tamanho: int = 7, negrito: bool = False, align: str = "L") -> None:
        rect(x, y0, w, h)
        pdf.set_xy(x + 1.2, y0 + 1)
        pdf.set_font("Helvetica", "", 5)
        pdf.cell(w - 2.4, 2.2, titulo.upper(), align="L")
        if valor_campo:
            pdf.set_xy(x + 1.2, y0 + 3.7)
            pdf.set_font("Helvetica", "B" if negrito else "", tamanho)
            pdf.multi_cell(w - 2.4, 3.2, valor_campo[:180], border=0, align=align)

    def titulo_secao(y0: float, texto_secao: str) -> None:
        rect(margem, y0, largura, 5)
        pdf.set_xy(margem, y0 + 1.2)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(largura, 2.5, texto_secao.upper(), align="C")

    def info_linha(x: float, y0: float, titulo: str, valor_campo: str, w: float = 60) -> None:
        pdf.set_xy(x, y0)
        pdf.set_font("Helvetica", "", 5)
        pdf.cell(w, 2.2, titulo.upper())
        pdf.set_xy(x, y0 + 2.4)
        pdf.set_font("Helvetica", "", 7)
        pdf.multi_cell(w, 3, valor_campo[:95])

    # Cabeçalho
    rect(margem, y, largura, 35)
    rect(margem, y, 83, 35)
    rect(margem + 83, y, 80, 35)
    rect(margem + 163, y, 35, 35)
    pdf.set_xy(margem + 3, y + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.multi_cell(77, 3.5, emitente[:70], align="C")
    pdf.set_font("Helvetica", "", 5.5)
    pdf.set_xy(margem + 7, y + 13)
    pdf.multi_cell(68, 3, f"{fantasia}\n{end_emit}\nCNPJ: {doc_formatado(cnpj_emitente)}\nINSCRICAO ESTADUAL: {ie_emitente}")

    pdf.set_xy(margem + 83, y + 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(80, 4, "DACTE", align="C")
    pdf.set_xy(margem + 83, y + 6)
    pdf.set_font("Helvetica", "B", 5)
    pdf.cell(80, 3, "Documento Auxiliar do Conhecimento de Transporte Eletronico", align="C")
    label(margem + 84, y + 12, 12, 8, "Modelo", "57", 8, True, "C")
    label(margem + 96, y + 12, 12, 8, "Serie", serie, 8, True, "C")
    label(margem + 108, y + 12, 26, 8, "Numero", f"{int(numero):010d}" if numero.isdigit() else numero, 8, True, "C")
    label(margem + 134, y + 12, 12, 8, "Folha", "01/01", 8, True, "C")
    label(margem + 146, y + 12, 17, 8, "Emissao", emissao[:10], 5, False, "C")

    with NamedTemporaryFile(suffix=".png", delete=False) as arquivo_barra:
        caminho_barra = Path(arquivo_barra.name)
    try:
        Code128(chave, writer=ImageWriter()).write(
            caminho_barra.open("wb"),
            {
                "module_height": 6.5,
                "module_width": 0.22,
                "font_size": 0,
                "quiet_zone": 0.8,
                "write_text": False,
            },
        )
        pdf.image(str(caminho_barra), x=margem + 87, y=y + 21.2, w=72, h=7.8)
    finally:
        caminho_barra.unlink(missing_ok=True)

    pdf.set_xy(margem + 84, y + 30.2)
    pdf.set_font("Helvetica", "B", 5.5)
    pdf.cell(78, 3, "Chave de acesso", align="C")

    pdf.set_xy(margem + 164, y + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(33, 4, "MODAL", align="C")
    pdf.set_xy(margem + 164, y + 7)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(33, 4, "RODOVIARIO", align="C")
    if qr_code:
        imagem_qr = qrcode.make(qr_code)
        with NamedTemporaryFile(suffix=".png", delete=False) as arquivo_qr:
            caminho_qr = Path(arquivo_qr.name)
            imagem_qr.save(caminho_qr)
        try:
            pdf.image(str(caminho_qr), x=margem + 170, y=y + 13, w=21)
        finally:
            caminho_qr.unlink(missing_ok=True)
    y += 35

    label(margem, y, 45, 9, "Tipo do CT-e", {"0": "NORMAL", "1": "COMPLEMENTAR"}.get(tipo_cte, tipo_cte), 7)
    label(margem + 45, y, 37, 9, "Tipo do servico", {"0": "NORMAL"}.get(tipo_servico, tipo_servico), 7)
    label(margem + 82, y, 116, 9, "Protocolo de autorizacao de uso", f"{protocolo}    {recebido[:19]}", 7, True, "C")
    y += 9

    label(margem, y, largura, 8, "CFOP - Natureza da Operacao", f"{cfop} - {natureza}", 8)
    y += 8
    label(margem, y, largura / 2, 8, "Origem da prestacao", origem, 7)
    label(margem + largura / 2, y, largura / 2, 8, "Destino da prestacao", destino, 7)
    y += 8

    label(margem, y, largura / 2, 26, "Remetente", remetente, 6.5, True)
    info_linha(margem + 2, y + 9, "Endereco", end_rem, 82)
    info_linha(margem + 2, y + 18, "CNPJ/CPF", doc_formatado(cnpj_rem), 34)
    info_linha(margem + 48, y + 18, "Inscricao Estadual", ie_rem, 30)
    label(margem + largura / 2, y, largura / 2, 26, "Destinatario", destinatario, 6.5, True)
    info_linha(margem + largura / 2 + 2, y + 9, "Endereco", end_dest, 82)
    info_linha(margem + largura / 2 + 2, y + 18, "CNPJ/CPF", doc_formatado(cnpj_dest), 34)
    info_linha(margem + largura / 2 + 48, y + 18, "Inscricao Estadual", ie_dest, 30)
    y += 26

    label(margem, y, largura * .72, 10, "Produto predominante", produto or natureza, 7)
    label(margem + largura * .72, y, largura * .28, 10, "Valor total da mercadoria", dinheiro(valor_carga or valor), 7, True, "R")
    y += 10

    titulo_secao(y, "Componentes do valor da prestacao de servico")
    y += 5
    label(margem, y, 49, 18, "Nome", "FRETE", 7)
    label(margem + 49, y, 40, 18, "Valor", dinheiro(valor), 8, True, "R")
    label(margem + 89, y, 54, 18, "Valor total do servico", dinheiro(valor), 8, True, "R")
    label(margem + 143, y, 55, 18, "Valor a receber", dinheiro(valor_receber), 8, True, "R")
    y += 18

    titulo_secao(y, "Informacoes relativas ao imposto")
    y += 5
    label(margem, y, 55, 10, "Situacao tributaria", texto(".//cte:imp/cte:ICMS/*/cte:CST") or "90 - SIMPLES NACIONAL", 7)
    label(margem + 55, y, 40, 10, "Base de calculo", texto(".//cte:imp//cte:vBC"), 7)
    label(margem + 95, y, 35, 10, "Aliq. ICMS", texto(".//cte:imp//cte:pICMS"), 7)
    label(margem + 130, y, 35, 10, "Valor ICMS", texto(".//cte:imp//cte:vICMS"), 7)
    label(margem + 165, y, 33, 10, "% Red. BC Calc.", texto(".//cte:imp//cte:pRedBC"), 7)
    y += 10

    titulo_secao(y, "Documentos originarios")
    y += 5
    label(margem, y, largura, 19, "Chave do DF-e", chave_doc, 6.5)
    y += 19

    titulo_secao(y, "Observacoes")
    y += 5
    rect(margem, y, largura, 22)
    pdf.set_xy(margem + 2, y + 2)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.multi_cell(largura - 4, 3, observacao[:500])
    pdf.set_xy(margem + 15, y + 5)
    pdf.set_text_color(120, 120, 120)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(largura - 30, 8, "SEM VALOR FISCAL - AMBIENTE DE HOMOLOGACAO", align="C")
    pdf.set_text_color(0, 0, 0)
    y += 22

    titulo_secao(y, "Dados especificos do modal rodoviario")
    y += 5
    label(margem, y, 50, 9, "RNTRC da empresa", texto(".//cte:rodo/cte:RNTRC"), 7)
    label(margem + 50, y, 148, 9, "Mensagem", "Este conhecimento de transporte atende a legislacao de transporte rodoviario em vigor.", 6)
    y += 13

    # Recibo inferior
    y = 256
    pdf.line(margem, y, margem + largura, y)
    y += 2
    rect(margem, y, largura, 22)
    pdf.set_xy(margem, y + 1)
    pdf.set_font("Helvetica", "", 5.5)
    pdf.cell(largura, 3, "DECLARO QUE RECEBI OS VOLUMES DESTE CONHECIMENTO EM PERFEITO ESTADO", align="C")
    label(margem, y + 5, 55, 17, "Nome", "", 7)
    label(margem + 55, y + 5, 78, 17, "Assinatura / Carimbo", "", 7)
    label(margem + 133, y + 5, 35, 17, "Chegada/Saida", "____/____/____  ____:____", 6, False, "C")
    label(margem + 168, y + 5, 30, 17, "CT-e", f"N. {numero}\nSerie: {serie}", 7, True, "C")
    pdf.output(str(caminho_pdf))


def gerar_pdf_documento(xml: str, caminho_pdf: Path, tipo_documento: str) -> None:
    """Gera o PDF adequado para cada modelo fiscal."""
    if tipo_documento == "mdfe":
        from brazilfiscalreport.damdfe import Damdfe

        damdfe = Damdfe(xml=xml)
        damdfe.output(str(caminho_pdf))
        return

    if tipo_documento == "cte":
        from brazilfiscalreport.dacte import Dacte

        try:
            dacte = Dacte(xml=xml)
            dacte.output(str(caminho_pdf))
        except TypeError:
            gerar_pdf_cte_simples(xml, caminho_pdf)
        return

    if tipo_documento == "nfe":
        from brazilfiscalreport.danfe import Danfe

        danfe = Danfe(xml=xml)
        danfe.output(str(caminho_pdf))
        return

    gerar_pdf_nfce(xml, caminho_pdf)


def salvar_arquivos_finais(
    *,
    chave: str,
    xml_assinado: str,
    xml_retorno: str | None,
    pasta_saida: Path,
    tipo_documento: str = "nfce",
) -> dict:
    """Salva chave.xml e chave.pdf somente quando houver autorizacao."""
    protocolo = (
        extrair_protocolo_autorizado(xml_retorno, tipo_documento)
        if xml_retorno
        else None
    )

    if protocolo is None:
        print("Documento nao autorizado: PDF e XML final nao foram gerados sem protocolo.")
        return {}

    xml_final = montar_xml_processado(xml_assinado, protocolo, tipo_documento)
    print("Documento autorizado: protocolo encontrado no retorno.")

    pasta_documento = caminho_saida_documento(pasta_saida, chave)
    caminho_xml_final = pasta_documento / f"{chave}.xml"
    caminho_pdf = pasta_documento / f"{chave}.pdf"
    caminho_xml_final.write_text(xml_final, encoding="utf-8")
    print(f"XML final salvo em: {caminho_xml_final}")

    try:
        gerar_pdf_documento(xml_final, caminho_pdf, tipo_documento)
        print(f"PDF salvo em: {caminho_pdf}")
    except Exception as erro:
        print(f"Nao foi possivel gerar o PDF agora: {erro}")

    return {
        "xml": str(caminho_xml_final),
        "pdf": str(caminho_pdf) if caminho_pdf.exists() else "",
    }


def enviar_documento_por_email(
    *,
    config: dict,
    chave: str,
    caminho_xml: Path,
    caminho_pdf: Path,
    tipo_documento: str,
) -> None:
    """Envia XML e PDF autorizados por e-mail usando SMTP do config."""
    destinatarios = obter_config(config, "email.destinatarios", []) or []
    smtp_config = obter_config(config, "email.smtp", {}) or {}
    host = smtp_config.get("host")
    porta = int(smtp_config.get("porta") or 587)
    usuario = smtp_config.get("usuario")
    senha = smtp_config.get("senha")
    remetente = smtp_config.get("remetente") or usuario
    usar_tls = bool(smtp_config.get("usar_tls", True))

    if not destinatarios:
        print("E-mail nao enviado: nenhum destinatario configurado.")
        return
    if not host or not remetente:
        print("E-mail nao enviado: SMTP nao configurado em config_nfce.json.")
        return
    if not caminho_xml.exists() or not caminho_pdf.exists():
        print("E-mail nao enviado: XML ou PDF final nao encontrado.")
        return

    mensagem = EmailMessage()
    nome_documento = {
        "nfce": "NFC-e",
        "nfe": "NF-e",
        "mdfe": "MDF-e",
        "cte": "CT-e",
    }[tipo_documento]
    mensagem["Subject"] = f"{nome_documento} {chave}"
    mensagem["From"] = remetente
    mensagem["To"] = ", ".join(destinatarios)
    mensagem.set_content(
        f"Segue {nome_documento} em anexo.\n\n"
        "Ambiente: homologacao.\n"
        f"Chave: {chave}\n"
    )

    for caminho, subtipo in ((caminho_xml, "xml"), (caminho_pdf, "pdf")):
        mensagem.add_attachment(
            caminho.read_bytes(),
            maintype="application",
            subtype=subtipo,
            filename=caminho.name,
        )

    with smtplib.SMTP(host, porta, timeout=30) as smtp:
        if usar_tls:
            smtp.starttls()
        if usuario and senha:
            smtp.login(usuario, senha)
        smtp.send_message(mensagem)

    print(f"{nome_documento} enviada por e-mail para: {', '.join(destinatarios)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera XML NF-e/NFC-e de exemplo usando nfelib."
    )
    parser.add_argument(
        "--tipo",
        choices=("nfce", "nfe", "mdfe", "cte"),
        default="nfce",
        help="Tipo de documento: nfce modelo 65, nfe modelo 55, mdfe modelo 58 ou cte modelo 57.",
    )
    parser.add_argument(
        "--validar-schema",
        action="store_true",
        help=(
            "Executa a validacao completa do schema. Nesta fase ela ainda deve "
            "avisar sobre QR Code fiscal definitivo e assinatura."
        ),
    )
    parser.add_argument(
        "--assinar",
        action="store_true",
        help="Assina o XML gerado usando o certificado PFX configurado.",
    )
    parser.add_argument(
        "--certificado",
        default=None,
        help="Caminho do certificado PFX. Padrao: certificado.pfx na pasta do projeto.",
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PADRAO),
        help="Arquivo JSON com certificado, CSC e configuracoes de e-mail.",
    )
    parser.add_argument(
        "--emitente",
        default=None,
        help="ID do emitente configurado em config_nfce.json.",
    )
    parser.add_argument(
        "--gerar-qrcode",
        action="store_true",
        help="Gera o infNFeSupl com QR Code NFC-e V2 online usando CSC/token.",
    )
    parser.add_argument(
        "--csc-id",
        default=os.getenv("SCFACIL_CSC_ID"),
        help="ID do CSC. Tambem pode ser informado pela variavel SCFACIL_CSC_ID.",
    )
    parser.add_argument(
        "--url-consulta",
        default=URL_CONSULTA_NFCE_RS,
        help="URL de consulta NFC-e da UF. Padrao configurado para RS.",
    )
    parser.add_argument(
        "--transmitir",
        action="store_true",
        help="Transmite a NFC-e para a SEFAZ/SVRS em homologacao.",
    )
    parser.add_argument(
        "--enviar-email",
        action="store_true",
        help="Envia XML e PDF finais para os destinatarios do config_nfce.json.",
    )
    parser.add_argument(
        "--gerar-pdf",
        action="store_true",
        help="Salva chave.xml e chave.pdf apos gerar/assinar, mesmo sem transmitir.",
    )
    parser.add_argument(
        "--numero",
        type=int,
        default=None,
        help="Numero manual do documento. Se omitido, usa ultimo_numero_nfce.txt + 1.",
    )
    parser.add_argument(
        "--arquivo-ultimo-numero",
        default=str(ULTIMO_NUMERO_PADRAO),
        help="Arquivo texto que guarda o ultimo numero autorizado.",
    )
    args = parser.parse_args()
    config = carregar_config(Path(args.config))

    config_emitente = selecionar_emitente_config(config, args.emitente)
    certificado_config = (
        obter_config(config_emitente, "certificado.caminho")
        or obter_config(config, "certificado.caminho", str(CERTIFICADO_PADRAO))
    )
    caminho_certificado = Path(args.certificado or certificado_config)
    if args.enviar_email:
        args.transmitir = True

    if args.transmitir:
        args.assinar = True
        args.gerar_qrcode = args.tipo == "nfce"
        args.validar_schema = True

    csc_id = args.csc_id or os.getenv("SCFACIL_CSC_ID") or obter_config(config, "csc.id")
    csc_token = os.getenv("SCFACIL_CSC_TOKEN") or obter_config(config, "csc.token")

    senha_certificado = (
        obter_senha_certificado(config, config_emitente) if args.assinar else None
    )
    emitente = montar_emitente_base(config_emitente)
    tributacao = montar_tributacao(config_emitente)

    if args.assinar:
        emitente = carregar_emitente_do_certificado(
            caminho_certificado=caminho_certificado,
            senha_certificado=senha_certificado,
            emitente_base=emitente,
        )
        print(
            f"Emitente selecionado: {config_emitente.get('id')} - "
            f"{emitente.nome} - {emitente.cnpj}"
        )

    caminho_ultimo_numero = Path(args.arquivo_ultimo_numero)
    numeracao = obter_numeracao(
        numero_informado=args.numero,
        caminho_ultimo_numero=caminho_ultimo_numero,
    )
    nome_documento = {
        "nfce": "NFC-e",
        "nfe": "NF-e",
        "mdfe": "MDF-e",
        "cte": "CT-e",
    }[args.tipo]
    print(f"{nome_documento} serie {numeracao.serie}, numero {numeracao.numero}")

    documento = None
    if args.tipo == "mdfe":
        xml = compactar_xml(
            montar_mdfe_exemplo(
                emitente=emitente,
                numeracao=numeracao,
            )
        )
        chave = obter_chave_xml(xml, "MDFe")
    elif args.tipo == "cte":
        xml = compactar_xml(
            montar_cte_exemplo(
                emitente=emitente,
                tributacao=tributacao,
                numeracao=numeracao,
            )
        )
        chave = obter_chave_xml(xml, "CTe")
    else:
        documento = montar_nfce_exemplo(
            emitente=emitente,
            tributacao=tributacao,
            numeracao=numeracao,
            tipo_documento=args.tipo,
            gerar_qrcode=args.gerar_qrcode,
            id_csc=csc_id,
            token_csc=csc_token,
            url_consulta=args.url_consulta,
        )
        xml = compactar_xml(documento.to_xml())
        chave = obter_chave_nfce(documento)
    xml_para_validar = xml

    pasta_saida = Path(__file__).parent / "saida"
    pasta_saida.mkdir(exist_ok=True)

    caminho_xml = pasta_saida / f"{args.tipo}_exemplo.xml"
    caminho_xml.write_text(xml, encoding="utf-8")

    print(f"XML {nome_documento} gerado em: {caminho_xml}")

    xml_assinado = None
    if args.assinar:
        if args.tipo == "mdfe":
            xml_assinado = assinar_mdfe(
                xml,
                chave,
                caminho_certificado,
                senha_certificado,
            )
        elif args.tipo == "cte":
            xml_assinado = assinar_cte(
                xml,
                chave,
                caminho_certificado,
                senha_certificado,
            )
        else:
            xml_assinado = assinar_nfce(
                xml,
                documento,
                caminho_certificado,
                senha_certificado,
            )
        caminho_xml_assinado = pasta_saida / f"{args.tipo}_exemplo_assinado.xml"
        caminho_xml_assinado.write_text(xml_assinado, encoding="utf-8")
        xml_para_validar = xml_assinado
        print(f"XML {nome_documento} assinado em: {caminho_xml_assinado}")

    if not args.validar_schema and not args.gerar_pdf:
        print(
            "Validacao completa nao executada nesta etapa. "
            "Use --validar-schema para conferir o XML contra o schema da nfelib."
        )
        return

    if args.validar_schema:
        erros = (
            Mdfe.schema_validation(xml_para_validar)
            if args.tipo == "mdfe"
            else Cte.schema_validation(xml_para_validar)
            if args.tipo == "cte"
            else Nfe.schema_validation(xml_para_validar)
        )
        if erros:
            print("\nValidacao retornou avisos/erros:")
            for erro in erros:
                print(f"- {erro}")
            print(
                "\nObservacao: enquanto o XML nao for assinado, e normal o schema "
                "exigir o elemento Signature. Para validar NFC-e com QR Code, use tambem "
                "--gerar-qrcode com SCFACIL_CSC_ID e SCFACIL_CSC_TOKEN."
            )
        else:
            print("XML validado pela nfelib sem erros de schema.")

    if args.gerar_pdf and not args.transmitir:
        print("PDF nao gerado: o PDF final so e criado apos transmissao autorizada.")

    if args.transmitir:
        certificado = Certificado(str(caminho_certificado), senha_certificado)
        print(f"Transmitindo {nome_documento} para homologacao...")
        try:
            xml_retorno = transmitir_documento_homologacao(
                xml_assinado=xml_assinado,
                chave=chave,
                certificado=certificado,
                pasta_saida=pasta_saida,
                tipo_documento=args.tipo,
                uf_emitente=emitente.uf_sigla,
            )
        except Exception as erro:
            dados_xml = dados_documento_xml(xml_assinado, args.tipo)
            registrar_dfe(
                {
                    "tipo": args.tipo.upper(),
                    "emitente": config_emitente.get("id"),
                    "emitente_nome": emitente.nome,
                    "chave": chave,
                    "numero": dados_xml.get("numero"),
                    "serie": dados_xml.get("serie"),
                    "protocolo": "",
                    "data_emissao": dados_xml.get("data_emissao"),
                    "valor": dados_xml.get("valor"),
                    "status": "erro",
                    "cstat": "",
                    "erro": str(erro),
                    "motivo": str(erro),
                    "data_recebimento": "",
                    "xml": "",
                    "pdf": "",
                    "retorno": "",
                    "lote": "",
                    "atualizado_em": datetime.now(FUSO_BRASIL).isoformat(),
                }
            )
            print(f"Erro na transmissao registrado: {erro}")
            raise SystemExit(1) from erro

        caminhos_finais = salvar_arquivos_finais(
            chave=chave,
            xml_assinado=xml_assinado,
            xml_retorno=xml_retorno,
            pasta_saida=pasta_saida,
            tipo_documento=args.tipo,
        )
        status_retorno = extrair_status_retorno(xml_retorno, args.tipo)
        autorizado = retorno_tem_autorizacao(xml_retorno, args.tipo)
        dados_xml = dados_documento_xml(xml_assinado, args.tipo)
        caminho_retorno = caminho_saida_documento(pasta_saida, chave) / f"{chave}-retorno.xml"
        caminho_lote = caminho_saida_documento(pasta_saida, chave) / f"{chave}-lote.xml"
        registrar_dfe(
            {
                "tipo": args.tipo.upper(),
                "emitente": config_emitente.get("id"),
                "emitente_nome": emitente.nome,
                "chave": chave,
                "numero": dados_xml.get("numero"),
                "serie": dados_xml.get("serie"),
                "protocolo": status_retorno.get("protocolo"),
                "data_emissao": dados_xml.get("data_emissao"),
                "valor": dados_xml.get("valor"),
                "status": "autorizado" if autorizado else "erro",
                "cstat": status_retorno.get("cstat"),
                "erro": "" if autorizado else status_retorno.get("motivo"),
                "motivo": status_retorno.get("motivo"),
                "data_recebimento": status_retorno.get("data_recebimento"),
                "xml": caminhos_finais.get("xml", ""),
                "pdf": caminhos_finais.get("pdf", ""),
                "retorno": str(caminho_retorno) if caminho_retorno.exists() else "",
                "lote": str(caminho_lote) if caminho_lote.exists() else "",
                "atualizado_em": datetime.now(FUSO_BRASIL).isoformat(),
            }
        )

        if autorizado:
            salvar_ultimo_numero(caminho_ultimo_numero, numeracao.numero)
            print(f"Ultimo numero autorizado salvo em: {caminho_ultimo_numero}")

        if args.enviar_email:
            enviar_documento_por_email(
                config=config,
                chave=chave,
                caminho_xml=pasta_saida / f"{chave}.xml",
                caminho_pdf=pasta_saida / f"{chave}.pdf",
                tipo_documento=args.tipo,
            )


if __name__ == "__main__":
    main()
