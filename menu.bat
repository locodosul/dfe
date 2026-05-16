@echo off
setlocal

cd /d C:\Sistema\codexNfe

set PYTHON=C:\Users\scmar\AppData\Local\Programs\Python\Python312\python.exe
set PORTA=8765

echo Iniciando menu DF-e...
echo.

netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul
if errorlevel 1 (
    start "Servidor DF-e" /min "%PYTHON%" app_menu_nfce.py
    ping 127.0.0.1 -n 3 >nul
) else (
    echo Servidor ja esta rodando na porta 8765.
)

if exist menu_porta_atual.txt set /p PORTA=<menu_porta_atual.txt

echo Abrindo http://127.0.0.1:%PORTA%
start "" "http://127.0.0.1:%PORTA%"

echo.
echo Pode fechar esta janela.
ping 127.0.0.1 -n 4 >nul
