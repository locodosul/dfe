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
REGISTRO_DFE = BASE_DIR / "dfe_emitidos.json"
NAMESPACE_NFSE = "http://www.sped.fazenda.gov.br/nfse"


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
    conteudo = caminho.read_text(encoding="utf-8").strip()
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
    with TemporaryDirectory() as temp:
        cert = preparar_certificado_requests(caminho_certificado, senha_certificado, Path(temp))
        resposta = requests.post(
            f"{URL_SEFIN_RESTRITA}/nfse",
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
        status_http, retorno = transmitir_dps(
            xml_assinado=xml_validar,
            caminho_certificado=Path(certificado.get("caminho", "")),
            senha_certificado=str(certificado.get("senha", "")),
        )
        caminho_retorno = pasta / f"dps_nfse_{numero:015d}_retorno.json"
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
                    "pdf": "",
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
