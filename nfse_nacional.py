"""
Modulo inicial para NFS-e Nacional (DPS) usando nfelib.

Nesta etapa o foco e gerar, assinar e validar a DPS de Porto Alegre em
ambiente de producao restrita. A transmissao REST sera adicionada depois que
o XML base estiver estabilizado.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree
from nfelib.nfse.bindings.v1_0.dps_v1_00 import Dps
from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import (
    Tccserv,
    TcenderNac,
    Tcendereco,
    TcinfoPessoa,
    TcinfoPrestador,
    TcinfoTributacao,
    TcinfoValores,
    TcinfDps,
    TclocPrest,
    TcregTrib,
    Tcserv,
    TctribMunicipal,
    TctribTotal,
    TcvservPrest,
)
from nfelib.nfse.bindings.v1_0.tipos_simples_v1_00 import (
    TsemitenteDps,
    TsopSimpNac,
    TsregEspTrib,
    TsregimeApuracaoSimpNac,
    TstipoAmbiente,
    TstipoRetIssqn,
    TstribIssqn,
)


BASE_DIR = Path(__file__).parent
CONFIG_PADRAO = BASE_DIR / "config_nfce.json"
SAIDA_DIR = BASE_DIR / "saida"
ULTIMO_NUMERO_NFSE = BASE_DIR / "ultimo_numero_nfse.txt"
FUSO_BRASIL = timezone(timedelta(hours=-3))
VERSAO_NFSE = "1.01"
AMBIENTE_RESTRITO = "2"
URL_SEFIN_RESTRITA = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"
URL_CONSULTA_NFSE_RESTRITA = "https://www.producaorestrita.nfse.gov.br/ConsultaPublica/"
REGISTRO_DFE = BASE_DIR / "dfe_emitidos.json"
NAMESPACE_NFSE = "http://www.sped.fazenda.gov.br/nfse"
TENTATIVAS_TRANSMISSAO = 3
INTERVALO_TENTATIVA_SEGUNDOS = 3


@dataclass(frozen=True)
class ArquivosNfse:
    xml_dps: Path
    xml_assinado: Path | None = None
    xml_nfse: Path | None = None
    retorno: Path | None = None


def somente_digitos(valor: str | None) -> str:
    return "".join(caractere for caractere in str(valor or "") if caractere.isdigit())


def carregar_config(caminho: Path = CONFIG_PADRAO) -> dict:
    if not caminho.exists():
        raise FileNotFoundError(f"Config nao encontrado: {caminho}")
    return json.loads(caminho.read_text(encoding="utf-8-sig"))


def localizar_emitente(config: dict, emitente_id: str | None) -> dict:
    alvo = emitente_id or config.get("emitente_padrao")
    emitentes = config.get("emitentes") or []
    for emitente in emitentes:
        if emitente.get("id") == alvo:
            return emitente
    raise ValueError(f"Emitente nao encontrado no config_nfce.json: {alvo}")


def caminho_numeracao(emitente_id: str | None) -> Path:
    sufixo = "".join(caractere for caractere in str(emitente_id or "padrao") if caractere.isalnum() or caractere in ("-", "_"))
    return BASE_DIR / f"ultimo_numero_nfse_{sufixo}.txt"


def ler_ultimo_numero(caminho: Path = ULTIMO_NUMERO_NFSE) -> int:
    if not caminho.exists():
        return 0
    conteudo = caminho.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    return int(conteudo) if conteudo else 0


def salvar_ultimo_numero(numero: int, caminho: Path = ULTIMO_NUMERO_NFSE) -> None:
    caminho.write_text(str(numero), encoding="utf-8")


def proximo_numero(numero_manual: int | None, caminho: Path) -> int:
    return numero_manual if numero_manual is not None else ler_ultimo_numero(caminho) + 1


def enum_por_valor(enum_cls, valor: str):
    for item in enum_cls:
        if item.value == str(valor):
            return item
    raise ValueError(f"Valor {valor!r} invalido para {enum_cls.__name__}")


def xml_para_bytes_utf8(xml: str) -> bytes:
    raiz = etree.fromstring(xml.encode("utf-8"))
    return etree.tostring(
        raiz,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=False,
    )


def compactar_base64(xml: str) -> str:
    return base64.b64encode(gzip.compress(xml_para_bytes_utf8(xml))).decode("ascii")


def descompactar_base64(valor: str) -> str:
    return gzip.decompress(base64.b64decode(valor)).decode("utf-8")


def carregar_registro_dfe() -> list[dict]:
    if not REGISTRO_DFE.exists():
        return []
    return json.loads(REGISTRO_DFE.read_text(encoding="utf-8-sig") or "[]")


def salvar_registro_dfe(registros: list[dict]) -> None:
    REGISTRO_DFE.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")


def registrar_dfe(registro: dict) -> None:
    registros = carregar_registro_dfe()
    chave = registro.get("chave")
    id_dps = registro.get("id_dps")
    registros = [
        item
        for item in registros
        if not ((chave and item.get("chave") == chave) or (id_dps and item.get("id_dps") == id_dps))
    ]
    registros.append(registro)
    registros.sort(key=lambda item: str(item.get("atualizado_em", "")), reverse=True)
    salvar_registro_dfe(registros)


def dados_nfse_xml(xml: str) -> dict:
    raiz = etree.fromstring(xml.encode("utf-8"))
    ns = {"nfse": NAMESPACE_NFSE}
    inf_nfse = raiz.find(".//nfse:infNFSe", namespaces=ns)
    chave = (inf_nfse.get("Id") if inf_nfse is not None else "").removeprefix("NFS")
    return {
        "chave": chave,
        "numero": raiz.findtext(".//nfse:nNFSe", namespaces=ns) or "",
        "protocolo": raiz.findtext(".//nfse:nDFSe", namespaces=ns) or "",
        "data_emissao": raiz.findtext(".//nfse:DPS/nfse:infDPS/nfse:dhEmi", namespaces=ns) or "",
        "data_recebimento": raiz.findtext(".//nfse:dhProc", namespaces=ns) or "",
        "valor": raiz.findtext(".//nfse:infNFSe/nfse:valores/nfse:vLiq", namespaces=ns)
        or raiz.findtext(".//nfse:DPS/nfse:infDPS/nfse:valores/nfse:vServPrest/nfse:vServ", namespaces=ns)
        or "",
        "cstat": raiz.findtext(".//nfse:cStat", namespaces=ns) or "",
        "motivo": raiz.findtext(".//nfse:xMotivo", namespaces=ns) or "",
    }


def gerar_pdf_nfse(xml: str, caminho_pdf: Path) -> None:
    """Gera DANFS-e em A4 para a NFS-e Nacional autorizada."""
    from fpdf import FPDF
    import qrcode

    raiz = etree.fromstring(xml.encode("utf-8"))
    ns = {"nfse": NAMESPACE_NFSE}

    def texto(xpath: str, padrao: str = "") -> str:
        return raiz.findtext(xpath, namespaces=ns) or padrao

    def dinheiro(valor: str) -> str:
        try:
            return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError:
            return valor

    def doc_formatado(valor: str) -> str:
        numeros = somente_digitos(valor)
        if len(numeros) == 14:
            return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
        if len(numeros) == 11:
            return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"
        return valor

    dados = dados_nfse_xml(xml)
    chave = dados.get("chave", "")
    numero_nfse = dados.get("numero", "")
    numero_dps = texto(".//nfse:DPS/nfse:infDPS/nfse:nDPS")
    serie = texto(".//nfse:DPS/nfse:infDPS/nfse:serie")
    prestador = texto(".//nfse:emit/nfse:xNome") or texto(".//nfse:DPS/nfse:infDPS/nfse:prest/nfse:xNome")
    cnpj_prestador = texto(".//nfse:emit/nfse:CNPJ") or texto(".//nfse:DPS/nfse:infDPS/nfse:prest/nfse:CNPJ")
    tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:xNome")
    cnpj_tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:CNPJ") or texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:CPF")
    email_prestador = texto(".//nfse:emit/nfse:email") or texto(".//nfse:DPS/nfse:infDPS/nfse:prest/nfse:email")
    telefone_prestador = texto(".//nfse:emit/nfse:fone") or texto(".//nfse:DPS/nfse:infDPS/nfse:prest/nfse:fone")
    email_tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:email")
    telefone_tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:fone")
    endereco_prestador = ", ".join(
        item
        for item in (
            texto(".//nfse:emit/nfse:enderNac/nfse:xLgr"),
            texto(".//nfse:emit/nfse:enderNac/nfse:nro"),
            texto(".//nfse:emit/nfse:enderNac/nfse:xBairro"),
        )
        if item
    )
    endereco_tomador = ", ".join(
        item
        for item in (
            texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:end/nfse:xLgr"),
            texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:end/nfse:nro"),
            texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:end/nfse:xBairro"),
        )
        if item
    )
    municipio_prestador = f"{texto('.//nfse:emit/nfse:enderNac/nfse:cMun')} / {texto('.//nfse:emit/nfse:enderNac/nfse:UF')}".strip(" /")
    municipio_tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:end/nfse:endNac/nfse:cMun")
    cep_prestador = texto(".//nfse:emit/nfse:enderNac/nfse:CEP")
    cep_tomador = texto(".//nfse:DPS/nfse:infDPS/nfse:toma/nfse:end/nfse:endNac/nfse:CEP")
    descricao = texto(".//nfse:DPS/nfse:infDPS/nfse:serv/nfse:cServ/nfse:xDescServ")
    codigo_servico = texto(".//nfse:DPS/nfse:infDPS/nfse:serv/nfse:cServ/nfse:cTribNac")
    codigo_municipal = texto(".//nfse:DPS/nfse:infDPS/nfse:serv/nfse:cServ/nfse:cTribMun") or "-"
    codigo_nbs = texto(".//nfse:DPS/nfse:infDPS/nfse:serv/nfse:cServ/nfse:cNBS")
    municipio = texto(".//nfse:xLocPrestacao") or texto(".//nfse:xLocEmi")
    uf_prestacao = texto(".//nfse:emit/nfse:enderNac/nfse:UF")
    local_prestacao = f"{municipio} / {uf_prestacao}".strip(" /")
    valor = dados.get("valor", "")
    protocolo = dados.get("protocolo", "")
    emissao = dados.get("data_emissao", "")
    processamento = dados.get("data_recebimento", "")
    competencia = texto(".//nfse:DPS/nfse:infDPS/nfse:dCompet")
    tributacao_issqn = texto(".//nfse:DPS/nfse:infDPS/nfse:valores/nfse:trib/nfse:tribMun/nfse:tribISSQN")
    retencao_issqn = texto(".//nfse:DPS/nfse:infDPS/nfse:valores/nfse:trib/nfse:tribMun/nfse:tpRetISSQN")
    percentual_tributos = texto(".//nfse:DPS/nfse:infDPS/nfse:valores/nfse:trib/nfse:totTrib/nfse:pTotTribSN")
    xtrib = texto(".//nfse:xTribNac")
    nbs_desc = texto(".//nfse:xNBS")
    url_consulta = f"{URL_CONSULTA_NFSE_RESTRITA}?chave={chave}" if chave else URL_CONSULTA_NFSE_RESTRITA

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_draw_color(35, 35, 35)
    pdf.set_line_width(0.18)

    logo_sc = BASE_DIR / "LogoSC.png"
    logo_porto_alegre = BASE_DIR / "portoalegre.jpg"

    def rect(x: float, y: float, w: float, h: float) -> None:
        pdf.rect(x, y, w, h)

    def texto_bloco(x: float, y0: float, w: float, titulo: str, valor_campo: str = "", tamanho: float = 7, negrito: bool = False, altura_linha: float = 3.0) -> None:
        pdf.set_xy(x, y0)
        pdf.set_font("Helvetica", "B", 5.8)
        pdf.cell(w, 2.6, titulo.upper())
        if valor_campo:
            pdf.set_xy(x, y0 + 2.9)
            pdf.set_font("Helvetica", "B" if negrito else "", tamanho)
            pdf.multi_cell(w, altura_linha, valor_campo[:360])

    def quebra_email(valor: str) -> str:
        return valor.replace("@", "@\n") if len(valor) > 26 else valor

    def secao(y0: float, titulo: str, altura: float) -> None:
        pdf.set_fill_color(232, 232, 232)
        pdf.rect(10, y0, 190, 5.2, style="F")
        rect(10, y0, 190, altura)
        pdf.set_xy(11.5, y0 + 1.15)
        pdf.set_font("Helvetica", "B", 7.4)
        pdf.cell(187, 3, titulo.upper())

    def campo(x: float, y0: float, w: float, titulo: str, valor_campo: str = "", tamanho: float = 7, negrito: bool = False) -> None:
        texto_bloco(x + 1.5, y0 + 1.3, w - 3, titulo, valor_campo, tamanho, negrito)

    y = 8
    rect(10, y, 190, 25)
    if logo_sc.exists():
        pdf.image(str(logo_sc), x=13.2, y=y + 3.2, w=14.5)
    pdf.set_xy(29, y + 3.2)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(112, 5.5, "DANFSe v1.0", align="C")
    pdf.set_xy(29, y + 10.3)
    pdf.set_font("Helvetica", "B", 8.2)
    pdf.cell(112, 4.5, "Documento Auxiliar da NFS-e", align="C")
    if logo_porto_alegre.exists():
        pdf.image(str(logo_porto_alegre), x=143.2, y=y + 3.2, w=14.5)
    pdf.set_xy(162, y + 2.8)
    pdf.set_font("Helvetica", "B", 6.6)
    pdf.multi_cell(36, 3.2, "PREFEITURA DE PORTO\nALEGRE", align="C")
    pdf.set_xy(162, y + 14.7)
    pdf.set_font("Helvetica", "", 5.6)
    pdf.multi_cell(36, 2.8, "SECRETARIA DA FAZENDA", align="C")
    y += 25

    rect(10, y, 190, 33)
    rect(166, y, 34, 33)
    pdf.line(10, y + 12, 166, y + 12)
    pdf.line(10, y + 23, 166, y + 23)
    pdf.line(58, y + 12, 58, y + 33)
    pdf.line(106, y + 12, 106, y + 33)
    texto_bloco(11.5, y + 2, 153, "Chave de Acesso da NFS-e", chave, 7.1, True)
    texto_bloco(11.5, y + 13.3, 45, "Numero da NFS-e", numero_nfse, 7.1, True)
    texto_bloco(59.5, y + 13.3, 45, "Competencia da NFS-e", competencia, 7.1)
    texto_bloco(107.5, y + 13.3, 57, "Data e Hora de Emissao da NFS-e", emissao[:19].replace("T", " "), 7.1)
    texto_bloco(11.5, y + 24.3, 45, "Numero da DPS", numero_dps, 7.1)
    texto_bloco(59.5, y + 24.3, 45, "Serie da DPS", serie, 7.1)
    texto_bloco(107.5, y + 24.3, 57, "Data e Hora de Emissao da DPS", emissao[:19].replace("T", " "), 7.1)
    imagem_qr = qrcode.make(url_consulta)
    with TemporaryDirectory() as temp_dir:
        caminho_qr = Path(temp_dir) / "qr_nfse.png"
        imagem_qr.save(caminho_qr)
        pdf.image(str(caminho_qr), x=170.7, y=y + 2.2, w=24)
    pdf.set_xy(166.5, y + 26)
    pdf.set_font("Helvetica", "", 5)
    pdf.multi_cell(33, 2.2, "A autenticidade pode ser verificada pelo QR Code ou chave de acesso.", align="C")
    y += 33

    secao(y, "Emitente da NFS-e", 36)
    pdf.set_xy(11.5, y + 6.2)
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.cell(187, 3, "PRESTADOR DO SERVICO")
    campo(10, y + 9, 68, "Nome / Nome Empresarial", prestador, 7.2, True)
    campo(78, y + 9, 36, "CNPJ / CPF / NIF", doc_formatado(cnpj_prestador), 7)
    campo(114, y + 9, 36, "Inscricao Municipal", "-", 7)
    campo(150, y + 9, 50, "Telefone", telefone_prestador, 7)
    campo(10, y + 20, 88, "Endereco", endereco_prestador, 7)
    campo(98, y + 20, 35, "Municipio", municipio_prestador or local_prestacao, 7)
    campo(133, y + 20, 25, "CEP", cep_prestador, 7)
    campo(158, y + 20, 42, "E-mail", quebra_email(email_prestador), 6.2)
    campo(10, y + 29, 92, "Simples Nacional na Data de Competencia", "Optante - MicroEmpresa EPP", 6.7, True)
    campo(102, y + 29, 98, "Regime de Apuracao Tributaria pelo SN", "Federais e Municipal pelo SN", 6.7)
    y += 36

    secao(y, "Tomador do Servico", 27)
    campo(10, y + 4, 78, "Nome / Nome Empresarial", tomador, 7.2, True)
    campo(88, y + 4, 40, "CNPJ / CPF / NIF", doc_formatado(cnpj_tomador), 7)
    campo(128, y + 4, 34, "Inscricao Municipal", "-", 7)
    campo(162, y + 4, 38, "Telefone", telefone_tomador, 7)
    campo(10, y + 16, 88, "Endereco", endereco_tomador, 7)
    campo(98, y + 16, 34, "Municipio", municipio_tomador, 7)
    campo(132, y + 16, 25, "CEP", cep_tomador, 7)
    campo(157, y + 16, 43, "E-mail", quebra_email(email_tomador), 6.2)
    y += 27

    rect(10, y, 190, 7)
    pdf.set_xy(10, y + 1.5)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(190, 3, "INTERMEDIARIO DO SERVICO NAO IDENTIFICADO NA NFS-e", align="C")
    y += 7

    secao(y, "Servico Prestado", 27)
    campo(10, y + 6, 58, "Codigo de Tributacao Nacional", f"{codigo_servico} - {xtrib}", 6.7)
    campo(68, y + 6, 46, "Codigo de Tributacao Municipal", codigo_municipal, 7)
    campo(114, y + 6, 42, "Local da Prestacao", local_prestacao, 7)
    campo(156, y + 6, 44, "Pais da Prestacao", "-", 7)
    campo(10, y + 17, 190, "Descricao do Servico", descricao, 6.7)
    y += 27

    secao(y, "Tributacao Municipal", 37)
    campo(10, y + 5, 42, "Tributacao do ISSQN", "Operacao Tributavel" if tributacao_issqn == "1" else tributacao_issqn, 6.6)
    campo(52, y + 5, 58, "Pais Resultado da Prestacao do Servico", "-", 6.6)
    campo(110, y + 5, 48, "Municipio de Incidencia do ISSQN", local_prestacao, 6.6)
    campo(158, y + 5, 42, "Regime Especial de Tributacao", "Nenhum", 6.6)
    campo(10, y + 17, 42, "Tipo de Imunidade", "-", 6.6)
    campo(52, y + 17, 58, "Suspensao da Exigibilidade do ISSQN", "Nao", 6.6)
    campo(110, y + 17, 48, "Retencao do ISSQN", "Nao Retido" if retencao_issqn == "1" else retencao_issqn, 6.6)
    campo(158, y + 17, 42, "ISSQN Apurado", "-", 6.6)
    campo(10, y + 29, 42, "Valor do Servico", f"R$ {dinheiro(valor)}", 6.6)
    campo(52, y + 29, 58, "Aliquota Aplicada", "-", 6.6)
    campo(110, y + 29, 48, "BC ISSQN", "-", 6.6)
    campo(158, y + 29, 42, "Beneficio Municipal", "-", 6.6)
    y += 37

    secao(y, "Tributacao Federal", 22)
    campo(10, y + 5, 42, "IRRF", "-", 6.6)
    campo(52, y + 5, 58, "Contribuicao Previdenciaria-Retida", "-", 6.6)
    campo(110, y + 5, 48, "Contribuicoes Sociais-Retidas", "-", 6.6)
    campo(10, y + 15, 42, "PIS - Debito Apuracao Propria", "-", 6.6)
    campo(52, y + 15, 58, "COFINS-Debito Apuracao Propria", "-", 6.6)
    campo(110, y + 15, 88, "Descricao Contrib. Sociais-Retidas", "PIS/COFINS/CSLL Nao Retidos", 6.6)
    y += 22

    secao(y, "Valor Total da NFS-e", 24)
    campo(10, y + 5, 42, "Valor do Servico", f"R$ {dinheiro(valor)}", 6.8, True)
    campo(52, y + 5, 48, "Desconto Condicionado", "-", 6.6)
    campo(100, y + 5, 48, "Desconto Incondicionado", "-", 6.6)
    campo(148, y + 5, 52, "ISSQN Retido", "-", 6.6)
    campo(10, y + 16, 62, "Total Tributacao Federal", "-", 6.6)
    campo(72, y + 16, 56, "PIS/COFINS Retidos", "-", 6.6)
    campo(148, y + 16, 52, "Valor Liquido da NFS-e", f"R$ {dinheiro(valor)}", 6.8, True)
    y += 24

    secao(y, "Totais Aproximados dos Tributos", 14)
    campo(10, y + 5, 62, "Federais", f"{percentual_tributos}%" if percentual_tributos else "-", 6.6)
    campo(72, y + 5, 62, "Estaduais", "-", 6.6)
    campo(134, y + 5, 62, "Municipais", "-", 6.6)
    y += 14

    secao(y, "Informacoes Complementares", 27)
    complemento = f"NBS: {codigo_nbs} - {nbs_desc}\nConsulta: {url_consulta}"
    campo(10, y + 5, 190, "", complemento, 6.2)
    pdf.set_xy(10, y + 15)
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(190, 8, "AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL", align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 280)
    pdf.set_font("Helvetica", "", 6)
    pdf.cell(190, 4, "Desenvolvido por SCFacil", align="R")
    pdf.output(str(caminho_pdf))


def montar_id_dps(codigo_municipio: str, documento_prestador: str, serie: str, numero: int) -> str:
    documento = somente_digitos(documento_prestador)
    tipo_inscricao = "2" if len(documento) == 14 else "1"
    return (
        f"DPS{somente_digitos(codigo_municipio):0>7}"
        f"{tipo_inscricao}"
        f"{documento:0>14}"
        f"{somente_digitos(serie):0>5}"
        f"{numero:0>15}"
    )


def montar_endereco_nacional(dados: dict) -> Tcendereco:
    return Tcendereco(
        endNac=TcenderNac(
            cMun=somente_digitos(dados.get("municipio_codigo")),
            CEP=somente_digitos(dados.get("cep")),
        ),
        xLgr=dados.get("logradouro", ""),
        nro=str(dados.get("numero", "")),
        xBairro=dados.get("bairro", ""),
    )


def montar_dps(
    *,
    config: dict,
    emitente_config: dict,
    numero: int,
    data_emissao: datetime,
) -> Dps:
    dados_emitente = emitente_config.get("dados") or {}
    nfse_cfg = emitente_config.get("nfse") or config.get("nfse") or {}
    tomador = nfse_cfg.get("tomador") or {}
    servico = nfse_cfg.get("servico") or {}
    tributacao = nfse_cfg.get("tributacao") or {}

    serie = str(nfse_cfg.get("serie", "1"))
    municipio = somente_digitos(nfse_cfg.get("municipio_codigo") or dados_emitente.get("municipio_codigo"))
    valor_servico = f"{float(servico.get('valor', 0)):0.2f}"
    id_dps = montar_id_dps(municipio, dados_emitente.get("cnpj"), serie, numero)

    dps = Dps(
        versao=VERSAO_NFSE,
        infDPS=TcinfDps(
            Id=id_dps,
            tpAmb=enum_por_valor(TstipoAmbiente, nfse_cfg.get("ambiente", AMBIENTE_RESTRITO)),
            dhEmi=data_emissao.isoformat(timespec="seconds"),
            verAplic=nfse_cfg.get("ver_aplic", "SCFacil"),
            serie=serie,
            nDPS=str(numero),
            dCompet=str(nfse_cfg.get("competencia") or data_emissao.date().isoformat()),
            tpEmit=enum_por_valor(TsemitenteDps, nfse_cfg.get("tipo_emitente", "1")),
            cLocEmi=municipio,
            prest=TcinfoPrestador(
                CNPJ=somente_digitos(dados_emitente.get("cnpj")),
                fone=somente_digitos(dados_emitente.get("fone") or nfse_cfg.get("fone")),
                email=dados_emitente.get("email") or nfse_cfg.get("email"),
                regTrib=TcregTrib(
                    opSimpNac=enum_por_valor(TsopSimpNac, tributacao.get("op_simp_nac", "3")),
                    regApTribSN=enum_por_valor(TsregimeApuracaoSimpNac, tributacao.get("reg_ap_trib_sn", "1")),
                    regEspTrib=enum_por_valor(TsregEspTrib, tributacao.get("reg_esp_trib", "0")),
                ),
            ),
            toma=TcinfoPessoa(
                CNPJ=somente_digitos(tomador.get("cnpj")),
                CPF=somente_digitos(tomador.get("cpf")) or None,
                xNome=tomador.get("nome", ""),
                end=montar_endereco_nacional(
                    {
                        "municipio_codigo": tomador.get("municipio_codigo"),
                        "cep": tomador.get("cep"),
                        "logradouro": tomador.get("logradouro"),
                        "numero": tomador.get("numero"),
                        "bairro": tomador.get("bairro"),
                    }
                ),
                fone=somente_digitos(tomador.get("fone")),
                email=tomador.get("email"),
            ),
            serv=Tcserv(
                locPrest=TclocPrest(cLocPrestacao=somente_digitos(servico.get("municipio_prestacao") or municipio)),
                cServ=Tccserv(
                    cTribNac=somente_digitos(servico.get("codigo_tributacao_nacional")),
                    xDescServ=servico.get("descricao", ""),
                    cNBS=somente_digitos(servico.get("codigo_nbs")),
                ),
            ),
            valores=TcinfoValores(
                vServPrest=TcvservPrest(vServ=valor_servico),
                trib=TcinfoTributacao(
                    tribMun=TctribMunicipal(
                        tribISSQN=enum_por_valor(TstribIssqn, tributacao.get("trib_issqn", "1")),
                        tpRetISSQN=enum_por_valor(TstipoRetIssqn, tributacao.get("tipo_retencao_issqn", "1")),
                    ),
                    totTrib=TctribTotal(pTotTribSN=str(tributacao.get("percentual_total_tributos_sn", "18.13"))),
                ),
            ),
        ),
    )
    return dps


def assinar_dps(xml: str, dps: Dps, caminho_certificado: Path, senha_certificado: str) -> str:
    if not caminho_certificado.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {caminho_certificado}")
    return Dps.sign_xml(
        xml,
        pkcs12_data=str(caminho_certificado),
        pkcs12_password=senha_certificado,
        doc_id=dps.infDPS.Id,
    )


def preparar_certificado_requests(caminho_certificado: Path, senha_certificado: str, pasta: Path) -> tuple[str, str]:
    dados_pfx = caminho_certificado.read_bytes()
    chave, certificado, cadeia = pkcs12.load_key_and_certificates(
        dados_pfx,
        senha_certificado.encode("utf-8"),
    )
    if chave is None or certificado is None:
        raise ValueError("Nao foi possivel extrair chave/certificado do PFX.")

    caminho_cert = pasta / "certificado.pem"
    caminho_key = pasta / "chave.pem"
    certificados = [certificado, *(cadeia or [])]
    caminho_cert.write_bytes(
        b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certificados)
    )
    caminho_key.write_bytes(
        chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(caminho_cert), str(caminho_key)


def transmitir_dps(
    *,
    xml_assinado: str,
    caminho_certificado: Path,
    senha_certificado: str,
    timeout: int = 60,
) -> tuple[int, dict]:
    payload = {"dpsXmlGZipB64": compactar_base64(xml_assinado)}
    endpoint = f"{URL_SEFIN_RESTRITA}/nfse"
    erro_final = None
    for tentativa in range(1, TENTATIVAS_TRANSMISSAO + 1):
        try:
            print(f"Tentativa {tentativa}/{TENTATIVAS_TRANSMISSAO} em {endpoint}")
            with TemporaryDirectory() as temp:
                cert = preparar_certificado_requests(caminho_certificado, senha_certificado, Path(temp))
                resposta = requests.post(
                    endpoint,
                    json=payload,
                    cert=cert,
                    timeout=timeout,
                    headers={"Accept": "application/json"},
                )
            try:
                conteudo = resposta.json()
            except ValueError:
                conteudo = {"raw": resposta.text}
            return resposta.status_code, conteudo
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.RequestException, OSError) as erro:
            erro_final = erro
            if tentativa < TENTATIVAS_TRANSMISSAO:
                time.sleep(INTERVALO_TENTATIVA_SEGUNDOS)
                continue
            raise ConnectionError(
                f"Erro de conexao ao transmitir NFSE para {endpoint} "
                f"apos {TENTATIVAS_TRANSMISSAO} tentativas: {erro}"
            ) from erro

    raise ConnectionError(f"Erro de conexao na transmissao da NFSE: {erro_final}")


def gerar_arquivos(
    *,
    emitente_id: str | None,
    numero_manual: int | None,
    assinar: bool,
    validar_schema: bool,
    transmitir: bool,
) -> ArquivosNfse:
    config = carregar_config()
    emitente_config = localizar_emitente(config, emitente_id)
    emitente_codigo = emitente_config.get("id") or emitente_id
    caminho_ultimo_numero = caminho_numeracao(emitente_codigo)
    numero = proximo_numero(numero_manual, caminho_ultimo_numero)
    data_emissao = datetime.now(FUSO_BRASIL)
    dps = montar_dps(
        config=config,
        emitente_config=emitente_config,
        numero=numero,
        data_emissao=data_emissao,
    )

    pasta = SAIDA_DIR / data_emissao.strftime("%Y%m")
    pasta.mkdir(parents=True, exist_ok=True)
    xml = dps.to_xml()
    caminho_xml = pasta / f"dps_nfse_{numero:015d}.xml"
    caminho_xml.write_text(xml, encoding="utf-8")
    print(f"DPS gerada em: {caminho_xml}")

    caminho_assinado = None
    xml_validar = xml
    certificado = emitente_config.get("certificado") or config.get("certificado") or {}
    if assinar:
        xml_validar = assinar_dps(
            xml=xml,
            dps=dps,
            caminho_certificado=Path(certificado.get("caminho", "")),
            senha_certificado=str(certificado.get("senha", "")),
        )
        caminho_assinado = pasta / f"dps_nfse_{numero:015d}_assinada.xml"
        caminho_assinado.write_text(xml_validar, encoding="utf-8")
        print(f"DPS assinada em: {caminho_assinado}")

    if validar_schema:
        Dps.schema_validation(xml_validar)
        print("DPS validada pela nfelib sem erros de schema.")

    caminho_nfse = None
    caminho_retorno = None
    if transmitir:
        if not assinar:
            raise ValueError("Use --assinar junto com --transmitir.")
        print("Transmitindo DPS para a Sefin Nacional em producao restrita...")
        caminho_retorno = pasta / f"dps_nfse_{numero:015d}_retorno.json"
        try:
            status_http, retorno = transmitir_dps(
                xml_assinado=xml_validar,
                caminho_certificado=Path(certificado.get("caminho", "")),
                senha_certificado=str(certificado.get("senha", "")),
            )
        except Exception as erro:
            motivo_erro = "Erro de conexão com a API nacional da NFS-e. Nenhuma autorização foi retornada."
            retorno = {
                "tipo": "erro_conexao",
                "endpoint": f"{URL_SEFIN_RESTRITA}/nfse",
                "tentativas": TENTATIVAS_TRANSMISSAO,
                "erro": str(erro),
                "registrado_em": datetime.now(FUSO_BRASIL).isoformat(),
            }
            caminho_retorno.write_text(json.dumps(retorno, ensure_ascii=False, indent=2), encoding="utf-8")
            registrar_dfe(
                {
                    "tipo": "NFSE",
                    "emitente": emitente_codigo,
                    "emitente_nome": (emitente_config.get("dados") or {}).get("nome", ""),
                    "id_dps": dps.infDPS.Id,
                    "chave": "",
                    "numero": str(numero),
                    "serie": str((emitente_config.get("nfse") or {}).get("serie", "1")),
                    "protocolo": "",
                    "data_emissao": dps.infDPS.dhEmi,
                    "valor": (dps.infDPS.valores.vServPrest.vServ if dps.infDPS.valores and dps.infDPS.valores.vServPrest else ""),
                    "status": "erro_conexao",
                    "cstat": "ERRO_CONEXAO",
                    "erro": motivo_erro,
                    "motivo": motivo_erro,
                    "data_recebimento": "",
                    "xml": "",
                    "pdf": "",
                    "retorno": str(caminho_retorno),
                    "lote": str(caminho_assinado or caminho_xml),
                    "endpoint": f"{URL_SEFIN_RESTRITA}/nfse",
                    "tentativas": TENTATIVAS_TRANSMISSAO,
                    "atualizado_em": datetime.now(FUSO_BRASIL).isoformat(),
                }
            )
            print(f"Transmissao nao concluida: {motivo_erro}")
            raise SystemExit(1) from erro

        caminho_retorno.write_text(json.dumps(retorno, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Retorno salvo em: {caminho_retorno}")

        nfse_xml_b64 = retorno.get("nfseXmlGZipB64") or retorno.get("NfseXmlGZipB64")
        chave_acesso = retorno.get("chaveAcesso") or retorno.get("ChaveAcesso") or ""
        erro = ""
        if status_http not in {200, 201}:
            erros = retorno.get("erros") or retorno.get("Erros") or []
            erro = json.dumps(erros or retorno, ensure_ascii=False)
            print(f"Transmissao recusada. HTTP {status_http}: {erro}")
        if nfse_xml_b64:
            xml_nfse = descompactar_base64(nfse_xml_b64)
            dados_nfse = dados_nfse_xml(xml_nfse)
            chave_acesso = chave_acesso or dados_nfse.get("chave", "")
            nome_nfse = f"{chave_acesso}.xml" if chave_acesso else f"dps_nfse_{numero:015d}_nfse.xml"
            caminho_nfse = pasta / nome_nfse
            caminho_nfse.write_text(xml_nfse, encoding="utf-8")
            print(f"NFS-e autorizada salva em: {caminho_nfse}")
            caminho_pdf = caminho_nfse.with_suffix(".pdf")
            gerar_pdf_nfse(xml_nfse, caminho_pdf)
            print(f"DANFS-e salvo em: {caminho_pdf}")
            registrar_dfe(
                {
                    "tipo": "NFSE",
                    "emitente": emitente_codigo,
                    "emitente_nome": (emitente_config.get("dados") or {}).get("nome", ""),
                    "id_dps": dps.infDPS.Id,
                    "chave": chave_acesso,
                    "numero": dados_nfse.get("numero") or str(numero),
                    "serie": str((emitente_config.get("nfse") or {}).get("serie", "1")),
                    "protocolo": dados_nfse.get("protocolo", ""),
                    "data_emissao": dados_nfse.get("data_emissao", ""),
                    "valor": dados_nfse.get("valor", ""),
                    "status": "autorizado",
                    "cstat": dados_nfse.get("cstat", "100"),
                    "erro": "",
                    "motivo": dados_nfse.get("motivo", ""),
                    "data_recebimento": dados_nfse.get("data_recebimento", ""),
                    "xml": str(caminho_nfse),
                    "pdf": str(caminho_pdf),
                    "retorno": str(caminho_retorno),
                    "lote": str(caminho_assinado or caminho_xml),
                    "atualizado_em": datetime.now(FUSO_BRASIL).isoformat(),
                }
            )
        else:
            registrar_dfe(
                {
                    "tipo": "NFSE",
                    "emitente": emitente_codigo,
                    "emitente_nome": (emitente_config.get("dados") or {}).get("nome", ""),
                    "id_dps": dps.infDPS.Id,
                    "chave": chave_acesso,
                    "numero": str(numero),
                    "serie": str((emitente_config.get("nfse") or {}).get("serie", "1")),
                    "protocolo": "",
                    "data_emissao": dps.infDPS.dhEmi,
                    "valor": (dps.infDPS.valores.vServPrest.vServ if dps.infDPS.valores and dps.infDPS.valores.vServPrest else ""),
                    "status": "erro",
                    "cstat": str(status_http),
                    "erro": erro or json.dumps(retorno, ensure_ascii=False),
                    "motivo": erro or "Retorno sem XML da NFS-e.",
                    "data_recebimento": "",
                    "xml": "",
                    "pdf": "",
                    "retorno": str(caminho_retorno),
                    "lote": str(caminho_assinado or caminho_xml),
                    "atualizado_em": datetime.now(FUSO_BRASIL).isoformat(),
                }
            )
            raise SystemExit(1)

    if assinar and validar_schema and (not transmitir or caminho_nfse):
        numero_atual = ler_ultimo_numero(caminho_ultimo_numero)
        if numero_manual is None or numero > numero_atual:
            salvar_ultimo_numero(numero, caminho_ultimo_numero)
            print(f"Ultimo numero NFS-e salvo em: {caminho_ultimo_numero}")

    return ArquivosNfse(xml_dps=caminho_xml, xml_assinado=caminho_assinado, xml_nfse=caminho_nfse, retorno=caminho_retorno)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera DPS da NFS-e Nacional usando nfelib.")
    parser.add_argument("--emitente", help="ID do emitente no config_nfce.json.")
    parser.add_argument("--numero", type=int, help="Numero manual da DPS.")
    parser.add_argument("--assinar", action="store_true", help="Assina a DPS com o certificado do emitente.")
    parser.add_argument("--validar-schema", action="store_true", help="Valida a DPS no schema da nfelib.")
    parser.add_argument("--transmitir", action="store_true", help="Transmite a DPS para a Sefin Nacional em producao restrita.")
    args = parser.parse_args()
    if args.transmitir:
        args.assinar = True
        args.validar_schema = True

    gerar_arquivos(
        emitente_id=args.emitente,
        numero_manual=args.numero,
        assinar=args.assinar,
        validar_schema=args.validar_schema,
        transmitir=args.transmitir,
    )


if __name__ == "__main__":
    main()
