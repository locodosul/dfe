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
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).parent
SCRIPT_NFCE = BASE_DIR / "gerar_nfce_exemplo.py"
SAIDA_DIR = BASE_DIR / "saida"
PORTA_ATUAL = BASE_DIR / "menu_porta_atual.txt"
CONFIG_PADRAO = BASE_DIR / "config_nfce.json"
HOST = "127.0.0.1"
PORT = int(os.getenv("SCFACIL_MENU_PORT", "8765"))


def carregar_config() -> dict:
    if not CONFIG_PADRAO.exists():
        return {}
    return json.loads(CONFIG_PADRAO.read_text(encoding="utf-8"))


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
    for sufixo, nome in (
        (".xml", "xml"),
        (".pdf", "pdf"),
        ("-retorno.xml", "retorno"),
        ("-lote.xml", "lote"),
    ):
        caminho = SAIDA_DIR / f"{chave}{sufixo}"
        if caminho.exists():
            links[nome] = f"/saida/{caminho.name}"
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
            "url": f"/saida/{arquivo.name}",
            "tamanho": arquivo.stat().st_size,
        }
        for arquivo in arquivos
    ]


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
                }
            )
            return
        if caminho.startswith("/saida/"):
            nome = Path(caminho).name
            arquivo = SAIDA_DIR / nome
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
        nome = Path((consulta.get("arquivo") or [""])[0]).name
        arquivo = SAIDA_DIR / nome
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
  <iframe src="/saida/{nome}" title="Visualizacao do PDF"></iframe>
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
