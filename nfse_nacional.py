"""
Modulo inicial para NFS-e Nacional (DPS) usando nfelib.

Nesta etapa o foco e gerar, assinar e validar a DPS de Porto Alegre em
ambiente de producao restrita. A transmissao REST sera adicionada depois que
o XML base estiver estabilizado.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


@dataclass(frozen=True)
class ArquivosNfse:
    xml_dps: Path
    xml_assinado: Path | None = None


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


def ler_ultimo_numero(caminho: Path = ULTIMO_NUMERO_NFSE) -> int:
    if not caminho.exists():
        return 0
    conteudo = caminho.read_text(encoding="utf-8").strip()
    return int(conteudo) if conteudo else 0


def salvar_ultimo_numero(numero: int, caminho: Path = ULTIMO_NUMERO_NFSE) -> None:
    caminho.write_text(str(numero), encoding="utf-8")


def proximo_numero(numero_manual: int | None) -> int:
    return numero_manual if numero_manual is not None else ler_ultimo_numero() + 1


def enum_por_valor(enum_cls, valor: str):
    for item in enum_cls:
        if item.value == str(valor):
            return item
    raise ValueError(f"Valor {valor!r} invalido para {enum_cls.__name__}")


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
                xNome=dados_emitente.get("nome", ""),
                end=montar_endereco_nacional(dados_emitente),
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


def gerar_arquivos(
    *,
    emitente_id: str | None,
    numero_manual: int | None,
    assinar: bool,
    validar_schema: bool,
) -> ArquivosNfse:
    config = carregar_config()
    emitente_config = localizar_emitente(config, emitente_id)
    numero = proximo_numero(numero_manual)
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
    if assinar:
        certificado = emitente_config.get("certificado") or config.get("certificado") or {}
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

    if assinar and validar_schema and numero_manual is None:
        salvar_ultimo_numero(numero)
        print(f"Ultimo numero NFS-e salvo em: {ULTIMO_NUMERO_NFSE}")

    return ArquivosNfse(xml_dps=caminho_xml, xml_assinado=caminho_assinado)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera DPS da NFS-e Nacional usando nfelib.")
    parser.add_argument("--emitente", help="ID do emitente no config_nfce.json.")
    parser.add_argument("--numero", type=int, help="Numero manual da DPS.")
    parser.add_argument("--assinar", action="store_true", help="Assina a DPS com o certificado do emitente.")
    parser.add_argument("--validar-schema", action="store_true", help="Valida a DPS no schema da nfelib.")
    args = parser.parse_args()

    gerar_arquivos(
        emitente_id=args.emitente,
        numero_manual=args.numero,
        assinar=args.assinar,
        validar_schema=args.validar_schema,
    )


if __name__ == "__main__":
    main()
