@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Iddaa Analiz - Baslatici

echo ==========================================================
echo   IDDAA ANALIZ - Otomatik Baslatici
echo ==========================================================
echo.
echo Klasor: %CD%
echo.

rem --- Python'u bul: once "py" baslaticisi, sonra "python" ---
set "PY="
py --version >nul 2>&1 && set "PY=py"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo [HATA] Python bulunamadi.
    echo.
    echo Cozum:
    echo   1^) https://www.python.org/downloads/ adresinden Python indirin
    echo   2^) Kurulum ekraninda EN ALTTAKI "Add python.exe to PATH"
    echo      kutusunu MUTLAKA isaretleyin
    echo   3^) Kurduktan sonra bu dosyayi tekrar cift tiklayin
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('%PY% --version 2^>^&1') do set "SURUM=%%v"
echo [OK] Python bulundu: !SURUM!  ^(komut: %PY%^)
echo.

rem --- Gerekli paketler ---
echo [1/4] Gerekli paketler kuruluyor...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [HATA] Paketler kurulamadi. Internet baglantinizi kontrol edin.
    pause
    exit /b 1
)
echo       tamam.
echo.

rem --- Veri kaynagina erisim (Turkiye'den engelli olabilir) ---
echo [2/4] Veri kaynagina erisim test ediliyor...
%PY% tahmin.py baglanti
if errorlevel 1 (
    echo.
    echo [UYARI] Veri kaynagina erisilemedi.
    echo         football-data.co.uk Turkiye'den engellidir.
    echo         VPN acip bu dosyayi tekrar calistirin.
    echo.
    pause
    exit /b 1
)
echo.

rem --- Arsiv indirilmis mi? ---
echo [3/4] Mac arsivi kontrol ediliyor...
if not exist "data\*.csv" (
    echo       Arsiv yok - indiriliyor. Ilk seferde 2-3 dakika surer, bekleyin...
    echo.
    %PY% tahmin.py guncelle
    if errorlevel 1 (
        echo [HATA] Veri indirilemedi. Baglantinizi kontrol edip tekrar deneyin.
        pause
        exit /b 1
    )
) else (
    echo       arsiv mevcut - atlaniyor.
)
echo.

rem --- Panel ---
echo [4/4] Panel baslatiliyor...
echo.
echo ==========================================================
echo   Tarayicida acin:  http://127.0.0.1:8000
echo   Kapatmak icin bu pencerede Ctrl+C yapin.
echo ==========================================================
echo.

start "" http://127.0.0.1:8000
%PY% tahmin.py web

echo.
echo Panel kapandi.
pause
