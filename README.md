# Exemplo NFC-e com nfelib

Este exemplo e o primeiro passo para o modulo de emissao de NFC-e do SCFacil.

Por enquanto ele:

- monta uma NFC-e com dados fixos;
- trabalha sempre em homologacao (`tpAmb = 2`);
- usa `config_nfce.json` para certificado, CSC e e-mails;
- usa os bindings da `nfelib` para gerar o XML;
- carrega CNPJ e razao social do emitente a partir do certificado ao assinar;
- calcula a chave de acesso;
- assina o XML com certificado A1 `.pfx`;
- gera o QR Code NFC-e V2 online com CSC/token;
- transmite para a SEFAZ/RS em homologacao;
- gera PDF em formato NFC-e/cupom;
- possui opcao de envio por e-mail quando SMTP estiver configurado;
- salva o XML em `saida/nfce_exemplo.xml`.

Ele ainda nao:

- usa banco de dados.

Essas partes devem entrar em etapas separadas, para manter o desenvolvimento controlado.

## Instalar dependencias

```powershell
pip install -r requirements.txt
```

## Configuracao local

As configuracoes ficam em:

```text
C:\Sistema\codexNfe\config_nfce.json
```

Esse arquivo contem:

- caminho, nome e senha do certificado;
- ID e token CSC de homologacao;
- tres destinatarios de e-mail;
- configuracao SMTP para envio futuro.

O arquivo esta no `.gitignore`, pois contem dados sensiveis.

## Rodar o exemplo

```powershell
python gerar_nfce_exemplo.py
```

O script gera o arquivo:

```text
C:\Sistema\codexNfe\saida\nfce_exemplo.xml
```

Esse comando sozinho apenas monta o XML base. Ele nao assina, nao gera QR Code
e nao transmite. Por isso, `python gerar_nfce_exemplo.py --validar-schema`
pode avisar que faltam `infNFeSupl` e `Signature`.

## Assinar XML

Coloque o certificado A1 na pasta do projeto com o nome:

```text
C:\Sistema\codexNfe\certificado.pfx
```

Para assinar, informe a senha em uma variavel de ambiente local:

```powershell
python gerar_nfce_exemplo.py --assinar
```

Por padrao, a senha sera lida de `config_nfce.json`.

O XML assinado sera salvo em:

```text
C:\Sistema\codexNfe\saida\nfce_exemplo_assinado.xml
```

Quando a opcao `--assinar` e usada, o script le o certificado antes de montar
o XML e usa o CNPJ/razao social do certificado no emitente. Isso evita gerar
uma NFC-e com CNPJ diferente do certificado que assina o documento.

## Gerar QR Code NFC-e

Para gerar o grupo `infNFeSupl` com QR Code NFC-e V2 online, o script usa:

- `SCFACIL_CSC_ID`: identificador do CSC/token.
- `SCFACIL_CSC_TOKEN`: codigo CSC/token fornecido pela SEFAZ.
- ou os campos `csc.id` e `csc.token` de `config_nfce.json`.

Exemplo:

```powershell
python gerar_nfce_exemplo.py --gerar-qrcode --assinar --validar-schema
```

Tambem e possivel informar o ID por parametro:

```powershell
python gerar_nfce_exemplo.py --gerar-qrcode --csc-id 1 --assinar
```

O token CSC deve continuar em variavel de ambiente, pois e informacao sensivel.

## Validacao de schema

A validacao completa do schema pode ser chamada assim:

```powershell
python gerar_nfce_exemplo.py --gerar-qrcode --assinar --validar-schema
```

Com assinatura e QR Code, o XML atual valida sem erros de schema pela `nfelib`.

## Transmitir em homologacao

O exemplo transmite somente para NFC-e homologacao da SEFAZ/RS:

```text
https://nfce-homologacao.sefazrs.rs.gov.br/ws/NfeAutorizacao/NFeAutorizacao4.asmx
```

Com as variaveis sensiveis configuradas:

```powershell
python gerar_nfce_exemplo.py --transmitir
```

O comando acima usa `config_nfce.json`.

Quando autorizada, a NFC-e e salva em:

```text
C:\Sistema\codexNfe\saida\<chave>.xml
C:\Sistema\codexNfe\saida\<chave>.pdf
```

O retorno SOAP bruto tambem e salvo:

```text
C:\Sistema\codexNfe\saida\<chave>-retorno.xml
```

## Enviar por e-mail

Configure SMTP em `config_nfce.json`:

```json
"smtp": {
  "host": "smtp.exemplo.com",
  "porta": 587,
  "usuario": "usuario",
  "senha": "senha",
  "usar_tls": true,
  "remetente": "financeiro@empresa.com"
}
```

Depois execute:

```powershell
python gerar_nfce_exemplo.py --enviar-email
```

Esse comando transmite a NFC-e, salva `chave.xml` e `chave.pdf`, e envia os dois
arquivos para os destinatarios configurados.

## Menu HTML

O menu local esta em:

```text
http://127.0.0.1:8765
```

Para iniciar depois de reiniciar o notebook, execute:

```text
C:\Sistema\codexNfe\iniciar_menu_nfce.bat
```

Depois abra:

```text
C:\Sistema\codexNfe\abrir_menu_nfce.bat
```

ou acesse manualmente:

```text
http://127.0.0.1:8765
```

O menu executa o fluxo completo:

1. gerar XML base;
2. assinar e validar;
3. transmitir NFC-e;
4. enviar NFC-e por e-mail.

## Numeracao dos testes

Por enquanto a serie fica fixa em `1`.

O numero da NFC-e e controlado pelo arquivo:

```text
C:\Sistema\codexNfe\ultimo_numero_nfce.txt
```

Ao executar `python gerar_nfce_exemplo.py --transmitir`, o script:

1. le o ultimo numero autorizado;
2. soma 1;
3. transmite a NFC-e;
4. so atualiza o `.txt` se a SEFAZ autorizar com `cStat=100`.

Tambem e possivel forcar um numero manualmente:

```powershell
python gerar_nfce_exemplo.py --transmitir --numero 10
```

Use numero manual com cuidado, porque a SEFAZ rejeita duplicidade de numero/chave
ja autorizada.

## Observacoes importantes

- NFC-e usa modelo `65`.
- Este exemplo usa sempre ambiente de homologacao (`tpAmb = 2`).
- Em homologacao, o destinatario e o primeiro item usam as descricoes exigidas pela SEFAZ.
- A chave de acesso e formada a partir dos dados da nota e do digito verificador.
- O QR Code implementado nesta etapa e V2 online, no formato aceito pelo schema local da `nfelib`.
- Para autorizacao real em homologacao, o CSC/token precisa ser o CSC oficial de homologacao da empresa/UF, nao um valor de teste qualquer.
- O certificado digital e o token CSC devem ficar fora do Git.
- Os dados fiscais do produto, impostos e pagamento precisam ser revisados com contador ou responsavel fiscal antes de uso real.
