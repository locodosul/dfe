"""
Servidor local simples para operar o fluxo NF-e/NFC-e pelo navegador.

Uso:
    python app_menu_nfce.py

Depois acesse:
    http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lxml import etree


BASE_DIR = Path(__file__).parent
SCRIPT_NFCE = BASE_DIR / "gerar_nfce_exemplo.py"
SAIDA_DIR = BASE_DIR / "saida"
PORTA_ATUAL = BASE_DIR / "menu_porta_atual.txt"
CONFIG_PADRAO = BASE_DIR / "config_nfce.json"
REGISTRO_DFE = BASE_DIR / "dfe_emitidos.json"
HOST = "127.0.0.1"
PORT = int(os.getenv("SCFACIL_MENU_PORT", "8765"))
NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"
NAMESPACE_CTE = "http://www.portalfiscal.inf.br/cte"
NAMESPACE_MDFE = "http://www.portalfiscal.inf.br/mdfe"


def carregar_config() -> dict:
    if not CONFIG_PADRAO.exists():
        return {}
    return json.loads(CONFIG_PADRAO.read_text(encoding="utf-8"))


def carregar_registro_dfe() -> list[dict]:
    if not REGISTRO_DFE.exists():
        return []
    return json.loads(REGISTRO_DFE.read_text(encoding="utf-8-sig") or "[]")


def salvar_registro_dfe(registros: list[dict]) -> None:
    REGISTRO_DFE.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pasta_ano_mes_chave(chave: str) -> str:
    ano_mes = chave[2:6] if len(chave) >= 6 else ""
    return f"20{ano_mes}" if len(ano_mes) == 4 else "sem_data"


def caminho_relativo_saida(caminho: str | None) -> str:
    if not caminho:
        return ""
    try:
        return str(Path(caminho).resolve().relative_to(SAIDA_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return Path(caminho).name


def url_saida(caminho: str | None) -> str:
    rel = caminho_relativo_saida(caminho)
    return f"/saida/{rel}" if rel else ""


def organizar_saida_existente() -> None:
    """Move XML/PDF/lote/retorno por chave para saida/aaaamm."""
    if not SAIDA_DIR.exists():
        return

    for arquivo in list(SAIDA_DIR.iterdir()):
        if not arquivo.is_file():
            continue
        nome = arquivo.name
        chave = nome.split("-", 1)[0].split(".", 1)[0]
        if len(chave) != 44 or not chave.isdigit():
            continue
        destino_dir = SAIDA_DIR / pasta_ano_mes_chave(chave)
        destino_dir.mkdir(exist_ok=True)
        destino = destino_dir / nome
        if destino.exists():
            destino.unlink()
        shutil.move(str(arquivo), str(destino))


def extrair_dados_xml_emitido(xml: str, tipo: str) -> dict:
    """Extrai dados principais de XML final/processado ja emitido."""
    raiz = etree.fromstring(xml.encode("utf-8"))
    if tipo == "MDFE":
        ns = {"dfe": NAMESPACE_MDFE}
        return {
            "numero": raiz.findtext(".//dfe:ide/dfe:nMDF", namespaces=ns) or "",
            "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
            "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
            "valor": raiz.findtext(".//dfe:tot/dfe:vCarga", namespaces=ns) or "",
            "protocolo": raiz.findtext(".//dfe:protMDFe/dfe:infProt/dfe:nProt", namespaces=ns) or "",
            "cstat": raiz.findtext(".//dfe:protMDFe/dfe:infProt/dfe:cStat", namespaces=ns) or "",
            "motivo": raiz.findtext(".//dfe:protMDFe/dfe:infProt/dfe:xMotivo", namespaces=ns) or "",
            "data_recebimento": raiz.findtext(".//dfe:protMDFe/dfe:infProt/dfe:dhRecbto", namespaces=ns) or "",
        }
    if tipo == "CTE":
        ns = {"dfe": NAMESPACE_CTE}
        return {
            "numero": raiz.findtext(".//dfe:ide/dfe:nCT", namespaces=ns) or "",
            "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
            "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
            "valor": raiz.findtext(".//dfe:vPrest/dfe:vTPrest", namespaces=ns) or "",
            "protocolo": raiz.findtext(".//dfe:protCTe/dfe:infProt/dfe:nProt", namespaces=ns) or "",
            "cstat": raiz.findtext(".//dfe:protCTe/dfe:infProt/dfe:cStat", namespaces=ns) or "",
            "motivo": raiz.findtext(".//dfe:protCTe/dfe:infProt/dfe:xMotivo", namespaces=ns) or "",
            "data_recebimento": raiz.findtext(".//dfe:protCTe/dfe:infProt/dfe:dhRecbto", namespaces=ns) or "",
        }

    ns = {"dfe": NAMESPACE_NFE}
    return {
        "numero": raiz.findtext(".//dfe:ide/dfe:nNF", namespaces=ns) or "",
        "serie": raiz.findtext(".//dfe:ide/dfe:serie", namespaces=ns) or "",
        "data_emissao": raiz.findtext(".//dfe:ide/dfe:dhEmi", namespaces=ns) or "",
        "valor": raiz.findtext(".//dfe:total/dfe:ICMSTot/dfe:vNF", namespaces=ns) or "",
        "protocolo": raiz.findtext(".//dfe:protNFe/dfe:infProt/dfe:nProt", namespaces=ns) or "",
        "cstat": raiz.findtext(".//dfe:protNFe/dfe:infProt/dfe:cStat", namespaces=ns) or "",
        "motivo": raiz.findtext(".//dfe:protNFe/dfe:infProt/dfe:xMotivo", namespaces=ns) or "",
        "data_recebimento": raiz.findtext(".//dfe:protNFe/dfe:infProt/dfe:dhRecbto", namespaces=ns) or "",
    }


def reconstruir_registro_de_saida() -> None:
    """Cria registros basicos para arquivos antigos encontrados em saida/aaaamm."""
    registros = carregar_registro_dfe()
    por_chave = {item.get("chave"): item for item in registros if item.get("chave")}

    for arquivo in SAIDA_DIR.rglob("*.xml"):
        nome = arquivo.name
        if "-lote" in nome or "-retorno" in nome:
            continue
        chave = arquivo.stem
        if len(chave) != 44 or not chave.isdigit() or chave in por_chave:
            continue
        tipo_modelo = chave[20:22]
        tipo = {"55": "NFE", "65": "NFCE", "57": "CTE", "58": "MDFE"}.get(tipo_modelo, "")
        pdf = arquivo.with_suffix(".pdf")
        dados_xml = {}
        try:
            dados_xml = extrair_dados_xml_emitido(arquivo.read_text(encoding="utf-8-sig"), tipo)
        except Exception:
            dados_xml = {}
        por_chave[chave] = {
            "tipo": tipo,
            "emitente": "",
            "emitente_nome": "",
            "chave": chave,
            "numero": dados_xml.get("numero") or str(int(chave[25:34])),
            "serie": dados_xml.get("serie") or str(int(chave[22:25])),
            "protocolo": dados_xml.get("protocolo", ""),
            "data_emissao": dados_xml.get("data_emissao", ""),
            "valor": dados_xml.get("valor", ""),
            "status": "autorizado" if dados_xml.get("cstat") == "100" else ("emitido" if pdf.exists() else "importado"),
            "cstat": dados_xml.get("cstat", ""),
            "erro": "",
            "motivo": dados_xml.get("motivo") or "Importado da pasta saida",
            "data_recebimento": dados_xml.get("data_recebimento", ""),
            "xml": str(arquivo),
            "pdf": str(pdf) if pdf.exists() else "",
            "retorno": str(arquivo.with_name(f"{chave}-retorno.xml")) if arquivo.with_name(f"{chave}-retorno.xml").exists() else "",
            "lote": str(arquivo.with_name(f"{chave}-lote.xml")) if arquivo.with_name(f"{chave}-lote.xml").exists() else "",
            "atualizado_em": arquivo.stat().st_mtime_ns,
        }

    salvar_registro_dfe(list(por_chave.values()))


def listar_emitentes() -> list[dict]:
    config = carregar_config()
    emitentes = config.get("emitentes") or []
    return [
        {
            "id": emitente.get("id"),
            "nome": emitente.get("nome_exibicao")
            or emitente.get("nome")
            or emitente.get("id"),
        }
        for emitente in emitentes
        if emitente.get("id")
    ]


def executar_fluxo(
    acao: str,
    numero: str | None = None,
    tipo: str = "nfce",
    emitente: str | None = None,
) -> dict:
    tipo = (tipo or "nfce").lower()
    if tipo not in {"nfce", "nfe", "mdfe", "cte"}:
        return {"ok": False, "erro": f"Tipo de documento desconhecido: {tipo}"}

    base = ["--tipo", tipo]
    validar = base + ["--assinar", "--validar-schema"]
    if tipo == "nfce":
        validar.append("--gerar-qrcode")

    comandos = {
        "gerar": base,
        "assinar_validar": validar,
        "transmitir": base + ["--transmitir"],
        "email": base + ["--enviar-email"],
    }
    if acao not in comandos:
        return {"ok": False, "erro": f"Acao desconhecida: {acao}"}

    comando = [sys.executable, str(SCRIPT_NFCE), *comandos[acao]]
    if numero:
        comando.extend(["--numero", numero])
    if emitente:
        comando.extend(["--emitente", emitente])

    processo = subprocess.run(
        comando,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        timeout=180,
    )

    saida = processo.stdout
    erro = processo.stderr
    chave = extrair_chave_saida(saida)

    return {
        "ok": processo.returncode == 0,
        "codigo": processo.returncode,
        "comando": " ".join(comando),
        "saida": saida,
        "erro": erro,
        "chave": chave,
        "links": montar_links(chave),
    }


def extrair_chave_saida(saida: str) -> str | None:
    marcador = "XML final salvo em:"
    for linha in saida.splitlines():
        if marcador in linha:
            caminho = linha.split(marcador, 1)[1].strip()
            return Path(caminho).stem
    return None


def montar_links(chave: str | None) -> dict:
    if not chave:
        return {}

    links = {}
    pasta = SAIDA_DIR / pasta_ano_mes_chave(chave)
    for sufixo, nome in (
        (".xml", "xml"),
        (".pdf", "pdf"),
        ("-retorno.xml", "retorno"),
        ("-lote.xml", "lote"),
    ):
        caminho = pasta / f"{chave}{sufixo}"
        if not caminho.exists():
            caminho = SAIDA_DIR / f"{chave}{sufixo}"
        if caminho.exists():
            links[nome] = url_saida(str(caminho))
    return links


def listar_ultimos_arquivos() -> list[dict]:
    if not SAIDA_DIR.exists():
        return []

    arquivos = sorted(
        [arquivo for arquivo in SAIDA_DIR.iterdir() if arquivo.is_file()],
        key=lambda arquivo: arquivo.stat().st_mtime,
        reverse=True,
    )[:12]
    return [
        {
            "nome": arquivo.name,
            "url": url_saida(str(arquivo)),
            "tamanho": arquivo.stat().st_size,
        }
        for arquivo in arquivos
    ]


def listar_documentos_grid() -> list[dict]:
    registros = carregar_registro_dfe()
    normalizados = []
    for item in registros:
        normalizados.append(
            {
                **item,
                "pdf_url": url_saida(item.get("pdf")),
                "xml_url": url_saida(item.get("xml")),
                "retorno_url": url_saida(item.get("retorno")),
            }
        )
    return normalizados


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        caminho = urlparse(self.path).path
        if caminho == "/":
            self.enviar_arquivo(BASE_DIR / "menu_nfce.html", "text/html; charset=utf-8")
            return
        if caminho == "/imprimir":
            self.enviar_html_impressao()
            return
        if caminho == "/api/status":
            ultimo = BASE_DIR / "ultimo_numero_nfce.txt"
            self.enviar_json(
                {
                    "ultimo_numero": ultimo.read_text(encoding="utf-8").strip()
                    if ultimo.exists()
                    else "0",
                    "emitentes": listar_emitentes(),
                    "emitente_padrao": carregar_config().get("emitente_padrao"),
                    "arquivos": listar_ultimos_arquivos(),
                    "documentos": listar_documentos_grid(),
                }
            )
            return
        if caminho == "/api/documentos":
            self.enviar_json({"documentos": listar_documentos_grid()})
            return
        if caminho.startswith("/saida/"):
            rel = caminho.removeprefix("/saida/").replace("/", os.sep)
            arquivo = (SAIDA_DIR / rel).resolve()
            if SAIDA_DIR.resolve() not in arquivo.parents and arquivo != SAIDA_DIR.resolve():
                self.send_error(403)
                return
            if not arquivo.exists():
                self.send_error(404)
                return
            content_type = "application/pdf" if arquivo.suffix.lower() == ".pdf" else "application/xml"
            self.enviar_arquivo(arquivo, content_type)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        caminho = urlparse(self.path).path
        if caminho != "/api/executar":
            self.send_error(404)
            return

        tamanho = int(self.headers.get("Content-Length", "0"))
        dados = json.loads(self.rfile.read(tamanho).decode("utf-8") or "{}")
        resultado = executar_fluxo(
            dados.get("acao", ""),
            dados.get("numero") or None,
            dados.get("tipo") or "nfce",
            dados.get("emitente") or None,
        )
        self.enviar_json(resultado)

    def enviar_json(self, dados: dict) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def enviar_arquivo(self, arquivo: Path, content_type: str) -> None:
        corpo = arquivo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def enviar_html_impressao(self) -> None:
        consulta = parse_qs(urlparse(self.path).query)
        rel = (consulta.get("arquivo") or [""])[0].replace("/", os.sep)
        arquivo = (SAIDA_DIR / rel).resolve()
        nome = arquivo.name
        if SAIDA_DIR.resolve() not in arquivo.parents and arquivo != SAIDA_DIR.resolve():
            self.send_error(403)
            return
        if arquivo.suffix.lower() != ".pdf" or not arquivo.exists():
            self.send_error(404)
            return

        corpo = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Imprimir {nome}</title>
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; background: #f6f8fb; }}
    iframe {{ width: 100vw; height: 100vh; border: 0; display: block; }}
    @media print {{
      html, body, iframe {{ width: 100%; height: 100%; background: #fff; }}
    }}
  </style>
</head>
<body>
  <iframe src="/saida/{rel.replace(os.sep, "/")}" title="Visualizacao do PDF"></iframe>
  <script>
    window.addEventListener("load", () => {{
      setTimeout(() => window.print(), 900);
    }});
  </script>
</body>
</html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    organizar_saida_existente()
    reconstruir_registro_de_saida()
    servidor = None
    porta = PORT
    for tentativa in range(PORT, PORT + 10):
        try:
            servidor = ThreadingHTTPServer((HOST, tentativa), Handler)
            porta = tentativa
            break
        except OSError:
            continue

    if servidor is None:
        raise RuntimeError(f"Nao foi possivel iniciar o menu entre as portas {PORT} e {PORT + 9}.")

    PORTA_ATUAL.write_text(str(porta), encoding="utf-8")
    print(f"Menu NF-e/NFC-e rodando em http://{HOST}:{porta}")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
