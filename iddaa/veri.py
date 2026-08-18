"""Veri katmanı.

Kaynak: football-data.co.uk — API anahtarı gerektirmeyen, 1993'e kadar giden
ücretsiz CSV arşivi. Her maç için skorlar ve çok sayıda bahis şirketinin
(Bet365, Pinnacle, piyasa ortalaması...) açılış/kapanış oranları bulunur.

Kaynak Türkiye'den erişime kapalıdır (bahis oranı yayınladığı için engelli).
Bu yüzden ağ katmanı iki ortam değişkeniyle yönlendirilebilir:

  IDDAA_PROXY          HTTP(S)/SOCKS5 vekil adresi. Örn:
                       http://kullanici:parola@vekil.example.com:8080
                       socks5h://127.0.0.1:1080  (pip install requests[socks])
  IDDAA_KAYNAK_TABAN   Kaynağın kök adresi. Kendi ters vekiliniz/aynanız varsa
                       buraya yazın: https://iddaa-veri.hesabiniz.workers.dev

İkisi de boşsa istekler doğrudan gider (requests'in standart HTTP_PROXY /
HTTPS_PROXY değişkenleri yine geçerlidir).
"""

from __future__ import annotations

import difflib
import glob
import json
import os
import re
import time
import warnings

import pandas as pd
import requests

VERI_KLASORU = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
VARSAYILAN_TABAN = "https://www.football-data.co.uk"
KAYNAK_YOLU = "/mmz4281/{sezon}/{lig}.csv"
FIKSTUR_YOLU = "/fixtures.csv"
EK_FIKSTUR_YOLU = "/new_league_fixtures.csv"
FIKSTUR_TTL_SANIYE = 6 * 3600  # fikstür dosyası en fazla 6 saatte bir tazelenir

KULLANICI_AJANI = "iddaa-analiz/1.0"
ZAMAN_ASIMI = 30
DENEME_SAYISI = 3  # geçici ağ hatalarında toplam deneme (2s, 4s bekleyerek)

ERISIM_IPUCU = (
    "Kaynağa (football-data.co.uk) ağ seviyesinde ulaşılamıyor. Türkiye'den "
    "erişim engellendiği için bu beklenen bir durumdur.\n"
    "Çözüm — şu ortam değişkenlerinden birini tanımlayın:\n"
    "  IDDAA_PROXY=http://kullanici:parola@vekil:8080   (HTTP/SOCKS5 vekil)\n"
    "  IDDAA_KAYNAK_TABAN=https://<kendi-ters-vekiliniz>  (ör. Cloudflare Worker)\n"
    "Ayrıntı ve ücretsiz Worker tarifi için: README > Erişim sorunu (Türkiye)"
)


class ErisimHatasi(RuntimeError):
    """Kaynağa hiç ulaşılamadı: DNS/bağlantı engeli ya da vekil sorunu.

    Tek bir dosyanın eksik olmasından (404, sezon yayınlanmamış) farklıdır;
    bu hata alındığında kalan dosyaları denemenin anlamı yoktur.
    """

    def __init__(self, mesaj: str, ozet: dict | None = None):
        super().__init__(mesaj)
        self.ozet = ozet or {"indirilen": 0, "onbellek": 0, "hata": []}


def kaynak_taban() -> str:
    """Veri kaynağının kök adresi (IDDAA_KAYNAK_TABAN ile değiştirilebilir)."""
    return (os.environ.get("IDDAA_KAYNAK_TABAN") or VARSAYILAN_TABAN).strip().rstrip("/")


def kaynak_url(sezon: str, lig: str) -> str:
    return kaynak_taban() + KAYNAK_YOLU.format(sezon=sezon, lig=lig)


def fikstur_url() -> str:
    return kaynak_taban() + FIKSTUR_YOLU


def proxy_ayari() -> dict[str, str]:
    """IDDAA_PROXY tanımlıysa hem http hem https için kullanılacak vekil."""
    adres = (os.environ.get("IDDAA_PROXY") or "").strip()
    return {"http": adres, "https": adres} if adres else {}


def _vekil_maskele(adres: str) -> str:
    """Vekil adresindeki kullanıcı adı/parolayı log ve API çıktısından gizler."""
    return re.sub(r"//[^/@]*@", "//***@", adres)


def _oturum() -> requests.Session:
    """User-Agent'ı ayarlanmış paylaşılan HTTP oturumu."""
    oturum = requests.Session()
    oturum.headers["User-Agent"] = KULLANICI_AJANI
    return oturum


def _istek(oturum: requests.Session, url: str, zaman_asimi: int):
    """Tek istek. Vekil, oturum yerine istek düzeyinde verilir.

    requests, ortamdaki HTTP_PROXY/HTTPS_PROXY değerlerini oturum ayarının
    ÜSTÜNE yazar (merge_environment_settings); vekili istek düzeyinde geçmek
    IDDAA_PROXY'nin ortam değişkenlerini gerçekten ezmesini sağlar. Boş dict
    verildiğinde requests her zamanki ortam davranışına döner.
    """
    return oturum.get(url, timeout=zaman_asimi, proxies=proxy_ayari() or None)


def _getir(oturum: requests.Session, url: str, zaman_asimi: int = ZAMAN_ASIMI):
    """Geçici hatalarda üstel bekleyerek tekrar dener; ulaşılamazsa ErisimHatasi."""
    son_hata: Exception | None = None
    for deneme in range(DENEME_SAYISI):
        try:
            yanit = _istek(oturum, url, zaman_asimi)
            if yanit.status_code >= 500:  # sunucu/vekil geçici arızası
                raise requests.HTTPError(f"HTTP {yanit.status_code}")
            return yanit
        except requests.RequestException as hata:
            son_hata = hata
            if deneme < DENEME_SAYISI - 1:
                time.sleep(2 ** (deneme + 1))
    raise ErisimHatasi(f"{ERISIM_IPUCU}\n(son hata: {son_hata})") from son_hata


def baglanti_testi(zaman_asimi: int = 15) -> dict:
    """Kaynağa erişimi tek istekle sınar; vekil ayarını doğrulamak için.

    242 dosyalık indirmeyi başlatmadan önce ayarın çalıştığını görmeyi sağlar.
    """
    vekil = proxy_ayari().get("https", "")
    sonuc = {
        "taban": kaynak_taban(),
        "vekil": _vekil_maskele(vekil) if vekil else None,
        "tamam": False,
        "sure_ms": None,
        "hata": None,
    }
    basla = time.monotonic()
    try:
        yanit = _istek(_oturum(), fikstur_url(), zaman_asimi)
        sonuc["sure_ms"] = round((time.monotonic() - basla) * 1000)
        if yanit.status_code != 200:
            sonuc["hata"] = f"HTTP {yanit.status_code}"
        elif b"Div" not in yanit.content[:200]:
            sonuc["hata"] = (
                "Yanıt CSV değil — vekil/ters vekil araya bir sayfa koyuyor olabilir."
            )
        else:
            sonuc["tamam"] = True
    except requests.RequestException as hata:
        sonuc["sure_ms"] = round((time.monotonic() - basla) * 1000)
        sonuc["hata"] = str(hata)
    if not sonuc["tamam"]:
        if sonuc["vekil"]:
            sonuc["ipucu"] = (
                "IDDAA_PROXY tanımlı ama üzerinden geçilemedi. Vekil adresini, "
                "portu ve varsa kullanıcı/parolayı doğrulayın; SOCKS5 için "
                "`pip install requests[socks]` ve socks5h:// öneki gerekir."
            )
        elif sonuc["taban"] != VARSAYILAN_TABAN:
            sonuc["ipucu"] = (
                f"IDDAA_KAYNAK_TABAN={sonuc['taban']} adresine ulaşılamıyor. "
                "Ters vekilinizin ayakta olduğunu ve /fixtures.csv yolunu "
                "kaynağa ilettiğini doğrulayın."
            )
        else:
            sonuc["ipucu"] = ERISIM_IPUCU
    return sonuc

# oynanmış maç istatistikleri: şut, isabetli şut, korner, sarı/kırmızı kart
ISTATISTIK_KOLONLARI = ["HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR"]

# fixtures.csv / sezon dosyalarındaki kitapçı kolon önekleri -> görünen ad
KITAPCI_ADLARI = {
    "B365": "Bet365",
    "PS": "Pinnacle",
    "WH": "William Hill",
    "BW": "Bwin",
    "IW": "Interwetten",
    "VC": "BetVictor",
    "BV": "BetVictor",
    "PP": "Paddy Power",
    "SKB": "SkyBet",
    "BFD": "Betfair",
    "BFE": "Betfair Borsa",
    "1XB": "1xBet",
    "LB": "Ladbrokes",
}

LIGLER = {
    "T1": "Türkiye Süper Lig",
    "E0": "İngiltere Premier League",
    "E1": "İngiltere Championship",
    "E2": "İngiltere League One",
    "E3": "İngiltere League Two",
    "EC": "İngiltere National League",
    "SC0": "İskoçya Premiership",
    "SC1": "İskoçya Championship",
    "SC2": "İskoçya League One",
    "SC3": "İskoçya League Two",
    "SP1": "İspanya La Liga",
    "SP2": "İspanya La Liga 2",
    "D1": "Almanya Bundesliga",
    "D2": "Almanya 2. Bundesliga",
    "I1": "İtalya Serie A",
    "I2": "İtalya Serie B",
    "F1": "Fransa Ligue 1",
    "F2": "Fransa Ligue 2",
    "N1": "Hollanda Eredivisie",
    "P1": "Portekiz Primeira Liga",
    "B1": "Belçika Pro League",
    "G1": "Yunanistan Süper Lig",
}

# football-data.co.uk "ekstra ligler": ülke başına tüm sezonları içeren tek
# dosya (/new/{KOD}.csv) + ayrı fikstür beslemesi (new_league_fixtures.csv).
# Yaz takvimli ligler (İskandinavya, Brezilya, ABD, Japonya...) hafta içi de
# oynadığı için ana bülten boşken bile takvimi doldururlar.
# Not: bu dosyalarda ilk yarı skoru, maç istatistiği ve Alt/Üst oranı yoktur.
EK_LIGLER = {
    "ARG": ("Argentina", "Arjantin Liga Profesional"),
    "AUT": ("Austria", "Avusturya Bundesliga"),
    "BRA": ("Brazil", "Brezilya Serie A"),
    "CHN": ("China", "Çin Süper Ligi"),
    "DNK": ("Denmark", "Danimarka Superliga"),
    "FIN": ("Finland", "Finlandiya Veikkausliiga"),
    "IRL": ("Ireland", "İrlanda Premier Division"),
    "JPN": ("Japan", "Japonya J1 Ligi"),
    "MEX": ("Mexico", "Meksika Liga MX"),
    "NOR": ("Norway", "Norveç Eliteserien"),
    "POL": ("Poland", "Polonya Ekstraklasa"),
    "ROU": ("Romania", "Romanya Liga 1"),
    "RUS": ("Russia", "Rusya Premier Lig"),
    "SWE": ("Sweden", "İsveç Allsvenskan"),
    "SWZ": ("Switzerland", "İsviçre Süper Ligi"),
    "USA": ("USA", "ABD MLS"),
}
LIGLER.update({kod: ad for kod, (_ulke, ad) in EK_LIGLER.items()})
EK_ULKE_KODU = {ulke: kod for kod, (ulke, _ad) in EK_LIGLER.items()}

# Varsayılan: tüm ligler — fikstürdeki her maçın geçmişi bulunsun diye
VARSAYILAN_LIGLER = list(LIGLER)

# Oran kolonları için öncelik sırası: Bet365 -> Pinnacle -> piyasa ortalaması -> diğerleri.
# Eski sezon dosyalarında kolon adları farklı olabildiği için zincir uzun tutuldu.
_EV_ORAN = ["B365H", "PSH", "AvgH", "BbAvH", "WHH", "BWH", "IWH", "LBH", "MaxH"]
_BERABERE_ORAN = ["B365D", "PSD", "AvgD", "BbAvD", "WHD", "BWD", "IWD", "LBD", "MaxD"]
_DEP_ORAN = ["B365A", "PSA", "AvgA", "BbAvA", "WHA", "BWA", "IWA", "LBA", "MaxA"]
_UST25_ORAN = ["B365>2.5", "P>2.5", "Avg>2.5", "BbAv>2.5", "Max>2.5"]
_ALT25_ORAN = ["B365<2.5", "P<2.5", "Avg<2.5", "BbAv<2.5", "Max<2.5"]

# Kullanıcının yazabileceği yaygın isimler -> football-data.co.uk'daki resmi ad.
TAKMA_ADLAR = {
    "basaksehir": "Buyuksehyr",
    "istanbulbasaksehir": "Buyuksehyr",
    "rcbbasaksehir": "Buyuksehyr",
    "adanademirspor": "Ad. Demirspor",
    "caykurrizespor": "Rizespor",
    "gaziantepfk": "Gaziantep",
    "fatihkaragumruk": "Karagumruk",
    "istanbulspor": "Istanbulspor",
    "mkeankaragucu": "Ankaragucu",
    "manutd": "Man United",
    "manchesterunited": "Man United",
    "manchestercity": "Man City",
    "psg": "Paris SG",
    "parissaintgermain": "Paris SG",
    "atleticomadrid": "Ath Madrid",
    "athleticbilbao": "Ath Bilbao",
    "intermilan": "Inter",
    "acmilan": "Milan",
    "nec": "Nijmegen",
    "bayernmunih": "Bayern Munich",
    "borussiadortmund": "Dortmund",
}


def guncel_sezon_baslangic_yili() -> int:
    """İçinde bulunulan Avrupa sezonunun başlangıç yılı (Temmuz'da yeni sezon)."""
    bugun = pd.Timestamp.today()
    return bugun.year if bugun.month >= 7 else bugun.year - 1


def sezon_kodlari(sezon_sayisi: int) -> list[str]:
    """Güncel sezondan geriye doğru sezon kodları: 2026/27 -> '2627'."""
    bas = guncel_sezon_baslangic_yili()
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(bas, bas - sezon_sayisi, -1)]


def indir(ligler: list[str] | None = None, sezon_sayisi: int = 11, yenile: bool = False) -> dict:
    """Seçilen liglerin sezon CSV'lerini indirir ve data/ altında önbelleğe alır.

    Güncel sezon dosyası her çağrıda tazelenir; eski sezonlar değişmediği için
    yalnızca eksikse indirilir (yenile=True hepsini yeniden indirir).
    """
    ligler = ligler or VARSAYILAN_LIGLER
    os.makedirs(VERI_KLASORU, exist_ok=True)
    kodlar = sezon_kodlari(sezon_sayisi)
    guncel_kod = kodlar[0]

    ozet = {"indirilen": 0, "onbellek": 0, "hata": []}
    oturum = _oturum()

    for lig in ligler:
        if lig in EK_LIGLER:
            continue  # ekstra ligler aşağıda tek dosya olarak indirilir
        for sezon in kodlar:
            hedef = os.path.join(VERI_KLASORU, f"{sezon}_{lig}.csv")
            if os.path.exists(hedef) and not yenile and sezon != guncel_kod:
                ozet["onbellek"] += 1
                continue
            url = kaynak_url(sezon, lig)
            try:
                # Ağ seviyesinde erişim yoksa 242 dosyayı tek tek denemenin
                # anlamı yok; ErisimHatasi yakalanmadan yukarı fırlatılır.
                yanit = _getir(oturum, url)
                if yanit.status_code != 200 or b"Div" not in yanit.content[:200]:
                    raise ValueError(f"HTTP {yanit.status_code}")
                satirlar = yanit.content.splitlines()
                # Henüz yayınlanmamış sezonlarda sunucu başka ligin dosyasını
                # döndürebiliyor; ilk veri satırının lig kodunu doğrula.
                if len(satirlar) < 2 or not satirlar[1].startswith(lig.encode() + b","):
                    raise ValueError("sezon henüz yayınlanmamış")
                with open(hedef, "wb") as f:
                    f.write(yanit.content)
                ozet["indirilen"] += 1
                print(f"  ✓ {lig} {sezon[:2]}/{sezon[2:]} sezonu indirildi")
            except ErisimHatasi as h:
                h.ozet = ozet  # o ana kadar inen dosyalar diskte kalır
                raise
            except Exception as h:  # noqa: BLE001 - tek dosya hatası akışı durdurmasın
                ozet["hata"].append(f"{lig} {sezon}: {h}")
                print(f"  ✗ {lig} {sezon[:2]}/{sezon[2:]} indirilemedi ({h})")

    # ekstra ligler: tüm sezonlar tek dosyada; sonuçlar işlendikçe değiştiği
    # için her güncellemede tazelenir
    for lig in ligler:
        if lig not in EK_LIGLER:
            continue
        hedef = os.path.join(VERI_KLASORU, f"EK_{lig}.csv")
        try:
            yanit = _getir(oturum, kaynak_taban() + f"/new/{lig}.csv")
            if yanit.status_code != 200 or b"Country" not in yanit.content[:200]:
                raise ValueError(f"HTTP {yanit.status_code}")
            with open(hedef, "wb") as f:
                f.write(yanit.content)
            ozet["indirilen"] += 1
            print(f"  ✓ {LIGLER[lig]} arşivi indirildi")
        except ErisimHatasi as h:
            h.ozet = ozet
            raise
        except Exception as h:  # noqa: BLE001
            ozet["hata"].append(f"{lig}: {h}")
            print(f"  ✗ {LIGLER[lig]} indirilemedi ({h})")
    return ozet


def _ilk_dolu_kolon(df: pd.DataFrame, kolonlar: list[str]) -> pd.Series:
    """Verilen kolon zincirinden satır bazında ilk dolu değeri döndürür."""
    mevcut = [k for k in kolonlar if k in df.columns]
    if not mevcut:
        return pd.Series(float("nan"), index=df.index)
    blok = df[mevcut].apply(pd.to_numeric, errors="coerce")
    return blok.bfill(axis=1).iloc[:, 0]


def _tek_dosya_oku(yol: str) -> pd.DataFrame | None:
    for kodlama in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(yol, encoding=kodlama, on_bad_lines="skip")
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


_EK_EV_ORAN = ["PSH", "PH", "B365H", "AvgH", "PSCH", "B365CH", "AvgCH", "MaxH", "MaxCH"]
_EK_BERABERE_ORAN = ["PSD", "PD", "B365D", "AvgD", "PSCD", "B365CD", "AvgCD", "MaxD", "MaxCD"]
_EK_DEP_ORAN = ["PSA", "PA", "B365A", "AvgA", "PSCA", "B365CA", "AvgCA", "MaxA", "MaxCA"]


def _ek_sezon(s) -> str:
    s = str(s)
    if "/" in s:  # "2025/2026" -> "2526"
        a, b = s.split("/", 1)
        return a[-2:] + b[-2:]
    return s  # takvim yılı ligleri: "2026"


def _satir_maksimum(p: pd.DataFrame, kolonlar: list[str]) -> pd.Series:
    mevcut = [k for k in kolonlar if k in p.columns]
    if not mevcut:
        return pd.Series(float("nan"), index=p.index)
    return p[mevcut].apply(pd.to_numeric, errors="coerce").max(axis=1)


def _ek_arsiv_oku(yol: str, kod: str) -> pd.DataFrame | None:
    """Ekstra lig arşivini (Country/League/Season/HG/AG/Res...) ana şemaya çevirir."""
    p = _tek_dosya_oku(yol)
    if p is None or "Res" not in p.columns:
        return None
    p = p.dropna(subset=["Date", "Home", "Away", "Res"]).copy()
    c = pd.DataFrame(index=p.index)
    c["Div"] = kod
    c["Sezon"] = p["Season"].map(_ek_sezon) if "Season" in p.columns else "?"
    c["Tarih"] = pd.to_datetime(p["Date"], dayfirst=True, format="mixed", errors="coerce")
    c["HomeTeam"] = p["Home"]
    c["AwayTeam"] = p["Away"]
    c["FTHG"] = pd.to_numeric(p["HG"], errors="coerce")
    c["FTAG"] = pd.to_numeric(p["AG"], errors="coerce")
    c["FTR"] = p["Res"]
    for k in ("HTHG", "HTAG", *ISTATISTIK_KOLONLARI):
        c[k] = float("nan")  # ekstra dosyalarda İY skoru ve istatistik yok
    c["oran_ev"] = _ilk_dolu_kolon(p, _EK_EV_ORAN)
    c["oran_berabere"] = _ilk_dolu_kolon(p, _EK_BERABERE_ORAN)
    c["oran_dep"] = _ilk_dolu_kolon(p, _EK_DEP_ORAN)
    c["oran_ust25"] = float("nan")
    c["oran_alt25"] = float("nan")
    c["oran_ev_maks"] = _satir_maksimum(p, ["MaxH", "MaxCH", "PSH", "PSCH", "B365H", "B365CH", "BFEH", "BFECH"])
    c["oran_berabere_maks"] = _satir_maksimum(p, ["MaxD", "MaxCD", "PSD", "PSCD", "B365D", "B365CD", "BFED", "BFECD"])
    c["oran_dep_maks"] = _satir_maksimum(p, ["MaxA", "MaxCA", "PSA", "PSCA", "B365A", "B365CA", "BFEA", "BFECA"])
    c["oran_ust25_maks"] = float("nan")
    c["oran_alt25_maks"] = float("nan")
    c["oran_ev_kapanis"] = _ilk_dolu_kolon(p, ["PSCH", "B365CH", "AvgCH", "MaxCH"])
    c["oran_berabere_kapanis"] = _ilk_dolu_kolon(p, ["PSCD", "B365CD", "AvgCD", "MaxCD"])
    c["oran_dep_kapanis"] = _ilk_dolu_kolon(p, ["PSCA", "B365CA", "AvgCA", "MaxCA"])
    c["oran_ust25_kapanis"] = float("nan")
    c["oran_alt25_kapanis"] = float("nan")
    c = c.dropna(subset=["Tarih", "FTHG", "FTAG", "FTR"])
    c["FTHG"] = c["FTHG"].astype(int)
    c["FTAG"] = c["FTAG"].astype(int)
    return c


def veriyi_yukle(ligler: list[str] | None = None) -> pd.DataFrame:
    """Önbellekteki tüm CSV'leri tek bir normalize DataFrame'de birleştirir."""
    dosyalar = sorted(glob.glob(os.path.join(VERI_KLASORU, "*_*.csv")))
    dosyalar = [d for d in dosyalar if not os.path.basename(d).startswith("EK_")]
    if ligler:
        dosyalar = [d for d in dosyalar if os.path.basename(d).split("_", 1)[1][:-4] in ligler]
    if not dosyalar:
        raise FileNotFoundError(
            "Önbellekte veri yok. Önce `python tahmin.py guncelle` çalıştırın."
        )

    parcalar = []
    for yol in dosyalar:
        sezon, lig_kodu = os.path.basename(yol)[:-4].split("_", 1)
        p = _tek_dosya_oku(yol)
        if p is None or "Div" not in p.columns:
            continue
        p = p[p["Div"] == lig_kodu].dropna(subset=["Date", "HomeTeam", "AwayTeam"]).copy()
        if p.empty:
            continue
        p["Sezon"] = sezon
        parcalar.append(p)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        df = pd.concat(parcalar, ignore_index=True)
        df["Tarih"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed", errors="coerce")
        for k in ("FTHG", "FTAG"):
            df[k] = pd.to_numeric(df[k], errors="coerce")
        df = df.dropna(subset=["Tarih", "FTHG", "FTAG", "FTR"])
        df["FTHG"] = df["FTHG"].astype(int)
        df["FTAG"] = df["FTAG"].astype(int)
        # ilk yarı skorları ve maç istatistikleri (şut, isabetli şut, korner,
        # kart) — eski dosyalarda olmayabilir -> NaN kalır
        for k in ("HTHG", "HTAG", *ISTATISTIK_KOLONLARI):
            df[k] = pd.to_numeric(df[k], errors="coerce") if k in df.columns else float("nan")

        df["oran_ev"] = _ilk_dolu_kolon(df, _EV_ORAN)
        df["oran_berabere"] = _ilk_dolu_kolon(df, _BERABERE_ORAN)
        df["oran_dep"] = _ilk_dolu_kolon(df, _DEP_ORAN)
        df["oran_ust25"] = _ilk_dolu_kolon(df, _UST25_ORAN)
        df["oran_alt25"] = _ilk_dolu_kolon(df, _ALT25_ORAN)

        # o maç için kayıtlı kitapçıların EN YÜKSEK oranı (backtest'te "en iyi
        # orandan oynasaydık" senaryosu için; BbMx = eski dosyalardaki piyasa maks.)
        def _satir_maks(kolonlar):
            mevcut = [k for k in kolonlar if k in df.columns]
            if not mevcut:
                return df["oran_ev"] * float("nan")
            return df[mevcut].apply(pd.to_numeric, errors="coerce").max(axis=1)

        df["oran_ev_maks"] = _satir_maks(_EV_ORAN + ["BbMxH"])
        df["oran_berabere_maks"] = _satir_maks(_BERABERE_ORAN + ["BbMxD"])
        df["oran_dep_maks"] = _satir_maks(_DEP_ORAN + ["BbMxA"])
        df["oran_ust25_maks"] = _satir_maks(_UST25_ORAN + ["BbMx>2.5"])
        df["oran_alt25_maks"] = _satir_maks(_ALT25_ORAN + ["BbMx<2.5"])

        # KAPANIŞ oranları (CLV takibi için): keskin Pinnacle kapanışı önce,
        # sonra Bet365 ve piyasa ortalaması kapanışları (~2019 sonrası dosyalarda var)
        df["oran_ev_kapanis"] = _ilk_dolu_kolon(df, ["PSCH", "B365CH", "AvgCH", "MaxCH"])
        df["oran_berabere_kapanis"] = _ilk_dolu_kolon(df, ["PSCD", "B365CD", "AvgCD", "MaxCD"])
        df["oran_dep_kapanis"] = _ilk_dolu_kolon(df, ["PSCA", "B365CA", "AvgCA", "MaxCA"])
        df["oran_ust25_kapanis"] = _ilk_dolu_kolon(df, ["PC>2.5", "B365C>2.5", "AvgC>2.5", "MaxC>2.5"])
        df["oran_alt25_kapanis"] = _ilk_dolu_kolon(df, ["PC<2.5", "B365C<2.5", "AvgC<2.5", "MaxC<2.5"])

    kolonlar = [
        "Div", "Sezon", "Tarih", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "HTHG", "HTAG", *ISTATISTIK_KOLONLARI,
        "oran_ev", "oran_berabere", "oran_dep", "oran_ust25", "oran_alt25",
        "oran_ev_maks", "oran_berabere_maks", "oran_dep_maks",
        "oran_ust25_maks", "oran_alt25_maks",
        "oran_ev_kapanis", "oran_berabere_kapanis", "oran_dep_kapanis",
        "oran_ust25_kapanis", "oran_alt25_kapanis",
    ]
    parcalar = [df[kolonlar]]

    # ekstra lig arşivleri (EK_*.csv) aynı şemaya çevrilip eklenir
    for yol in sorted(glob.glob(os.path.join(VERI_KLASORU, "EK_*.csv"))):
        kod = os.path.basename(yol)[3:-4]
        if ligler and kod not in ligler:
            continue
        ek = _ek_arsiv_oku(yol, kod)
        if ek is not None and not ek.empty:
            parcalar.append(ek[kolonlar])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        birlesik = pd.concat(parcalar, ignore_index=True)
    return birlesik.sort_values("Tarih").reset_index(drop=True)


def _normalize(isim: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.sub(r"[^a-z0-9]", "", isim.translate(tr).lower())


def takim_bul(df: pd.DataFrame, isim: str) -> str:
    """Kullanıcının yazdığı ismi veri setindeki resmi takım adına eşler."""
    adaylar = pd.unique(pd.concat([df["HomeTeam"], df["AwayTeam"]]))
    norm_map = {_normalize(a): a for a in adaylar}
    hedef = _normalize(isim)

    if hedef in norm_map:
        return norm_map[hedef]
    if hedef in TAKMA_ADLAR and _normalize(TAKMA_ADLAR[hedef]) in norm_map:
        return norm_map[_normalize(TAKMA_ADLAR[hedef])]

    icinde_gecen = [v for k, v in norm_map.items() if hedef in k or k in hedef]
    if len(icinde_gecen) == 1:
        return icinde_gecen[0]

    yakin = difflib.get_close_matches(hedef, norm_map.keys(), n=5, cutoff=0.75)
    if len(yakin) >= 1 and not icinde_gecen:
        return norm_map[yakin[0]]
    if icinde_gecen:
        # Birden fazla kısmi eşleşme: en çok maçı olanı seç (büyük kulüp önceliği).
        sayilar = {
            t: int(((df["HomeTeam"] == t) | (df["AwayTeam"] == t)).sum()) for t in icinde_gecen
        }
        return max(sayilar, key=sayilar.get)

    oneriler = [norm_map[y] for y in difflib.get_close_matches(hedef, norm_map.keys(), n=5, cutoff=0.5)]
    ek = f" Şunlardan birini mi kastettiniz: {', '.join(oneriler)}?" if oneriler else ""
    raise ValueError(
        f"'{isim}' takımı veri setinde bulunamadı.{ek} "
        f"Tüm isimler için: python tahmin.py takimlar"
    )


def simdi_tr() -> pd.Timestamp:
    """Türkiye saatiyle şu an (saat dilimi bilgisiz Timestamp)."""
    try:
        from zoneinfo import ZoneInfo

        return pd.Timestamp.now(tz=ZoneInfo("Europe/Istanbul")).tz_localize(None)
    except Exception:  # tz veritabanı yoksa sistem saatiyle yetin
        return pd.Timestamp.now()


def fikstur_indir(yenile: bool = False) -> str:
    """Önümüzdeki günlerin maçlarını (çok kitapçılı oranlarla) indirir, 6 saat önbellekler."""
    os.makedirs(VERI_KLASORU, exist_ok=True)
    hedef = os.path.join(VERI_KLASORU, "fixtures.csv")
    taze = (
        os.path.exists(hedef)
        and not yenile
        and time.time() - os.path.getmtime(hedef) < FIKSTUR_TTL_SANIYE
    )
    if not taze:
        try:
            yanit = _getir(_oturum(), fikstur_url())
        except ErisimHatasi:
            if os.path.exists(hedef):  # eski kopya varsa onunla devam et
                return hedef
            raise
        if yanit.status_code != 200 or b"Div" not in yanit.content[:200]:
            if os.path.exists(hedef):
                return hedef
            raise RuntimeError(f"Fikstür indirilemedi (HTTP {yanit.status_code})")
        with open(hedef, "wb") as f:
            f.write(yanit.content)
        _kaynak_zamani_kaydet("fixtures", yanit.headers.get("Last-Modified"))
    return hedef


def _fikstur_ek_indir(yenile: bool = False) -> str | None:
    """Ekstra liglerin fikstürünü indirir; ulaşılamazsa sessizce None döner
    (ana bülten tek başına da yeterlidir)."""
    hedef = os.path.join(VERI_KLASORU, "fixtures_ek.csv")
    taze = (
        os.path.exists(hedef)
        and not yenile
        and time.time() - os.path.getmtime(hedef) < FIKSTUR_TTL_SANIYE
    )
    if not taze:
        try:
            yanit = _getir(_oturum(), kaynak_taban() + EK_FIKSTUR_YOLU)
            if yanit.status_code != 200 or b"Country" not in yanit.content[:200]:
                raise ValueError(f"HTTP {yanit.status_code}")
            with open(hedef, "wb") as f:
                f.write(yanit.content)
            _kaynak_zamani_kaydet("fixtures_ek", yanit.headers.get("Last-Modified"))
        except Exception:  # noqa: BLE001
            return hedef if os.path.exists(hedef) else None
    return hedef


def _kaynak_zamani_kaydet(ad: str, last_modified: str | None) -> None:
    """Kaynağın kendi 'son yayın' damgasını saklar (arayüzde gösterilir)."""
    if not last_modified:
        return
    try:
        with open(os.path.join(VERI_KLASORU, f"{ad}.meta"), "w", encoding="utf-8") as f:
            f.write(last_modified)
    except OSError:
        pass


def fikstur_kaynak_yayini() -> str | None:
    """Bülten dosyalarının kaynaktaki en güncel yayın zamanı, TR saatiyle."""
    from email.utils import parsedate_to_datetime

    en_yeni = None
    for ad in ("fixtures", "fixtures_ek"):
        yol = os.path.join(VERI_KLASORU, f"{ad}.meta")
        try:
            with open(yol, encoding="utf-8") as f:
                t = parsedate_to_datetime(f.read().strip())
            if en_yeni is None or t > en_yeni:
                en_yeni = t
        except (OSError, ValueError, TypeError):
            continue
    if en_yeni is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        en_yeni = en_yeni.astimezone(ZoneInfo("Europe/Istanbul"))
    except Exception:  # noqa: BLE001
        pass
    gunler = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    return f"{gunler[en_yeni.weekday()]} {en_yeni:%d.%m %H:%M}"


def fikstur_yukle(ligler: list[str] | None = None,
                  yenile: bool = False,
                  gecmisi_at: bool = True) -> tuple[pd.DataFrame, list[str]]:
    """Fikstürü normalize eder; (DataFrame, mevcut kitapçı önekleri) döndürür.

    Birleşik kolonlar: oran_ev/berabere/dep (analiz için piyasa ortalaması,
    yoksa Bet365), oran_max_* (piyasadaki en yüksek oran), oran_ust25/alt25.
    Kitapçı bazlı ham kolonlar ({önek}H/D/A) ayrıca korunur.
    gecmisi_at: bugünden önceki günler takvimden düşürülür (gün ilerledikçe
    takvim kendiliğinden kayar; bugünün oynanmış maçları listede kalır).
    """
    yol = fikstur_indir(yenile=yenile)
    f = _tek_dosya_oku(yol)
    if f is None or "Div" not in f.columns:
        raise RuntimeError("Fikstür dosyası okunamadı.")
    f = f.dropna(subset=["Div", "Date", "HomeTeam", "AwayTeam"]).copy()

    # ekstra liglerin fikstürü aynı çerçeveye eklenir (Country -> Div kodu)
    ek_yol = _fikstur_ek_indir(yenile=yenile)
    if ek_yol:
        e = _tek_dosya_oku(ek_yol)
        if e is not None and "Country" in e.columns:
            e = e.dropna(subset=["Country", "Date", "Home", "Away"]).copy()
            e["Div"] = e["Country"].map(EK_ULKE_KODU)
            e = e.dropna(subset=["Div"]).rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
            if not e.empty:
                f = pd.concat([f, e], ignore_index=True)

    if ligler:
        f = f[f["Div"].isin(ligler)]

    ham = pd.to_datetime(
        f["Date"].astype(str) + " " + f.get("Time", pd.Series("", index=f.index)).fillna("12:00").astype(str),
        dayfirst=True, errors="coerce", format="mixed",
    )
    try:  # kaynak saatleri İngiltere saatidir; Türkiye saatine çevir
        f["Tarih"] = (
            ham.dt.tz_localize("Europe/London", nonexistent="shift_forward", ambiguous=True)
            .dt.tz_convert("Europe/Istanbul")
            .dt.tz_localize(None)
        )
    except Exception:  # tz veritabanı yoksa kaba +2 saat
        f["Tarih"] = ham + pd.Timedelta(hours=2)
    f = f.dropna(subset=["Tarih"])

    mevcut_kitapcilar = [
        p for p in KITAPCI_ADLARI
        if {f"{p}H", f"{p}D", f"{p}A"} <= set(f.columns)
    ]
    for p in mevcut_kitapcilar:
        for k in ("H", "D", "A"):
            f[f"{p}{k}"] = pd.to_numeric(f[f"{p}{k}"], errors="coerce")

    # analiz oranı: piyasa ortalaması en sağlıklısı; yoksa Bet365/Pinnacle/Max
    f["oran_ev"] = _ilk_dolu_kolon(f, ["AvgH", "B365H", "PSH", "MaxH"])
    f["oran_berabere"] = _ilk_dolu_kolon(f, ["AvgD", "B365D", "PSD", "MaxD"])
    f["oran_dep"] = _ilk_dolu_kolon(f, ["AvgA", "B365A", "PSA", "MaxA"])
    # en iyi (en yüksek) piyasa oranı: Max kolonu, yoksa kitapçıların satır maksimumu
    for uc, kolon in (("ev", "H"), ("berabere", "D"), ("dep", "A")):
        kaynaklar = [f"{p}{kolon}" for p in mevcut_kitapcilar]
        satir_maks = f[kaynaklar].max(axis=1) if kaynaklar else pd.Series(float("nan"), index=f.index)
        f[f"oran_max_{uc}"] = pd.to_numeric(f.get(f"Max{kolon}"), errors="coerce").fillna(satir_maks)
    f["oran_ust25"] = _ilk_dolu_kolon(f, ["Avg>2.5", "B365>2.5", "Max>2.5"])
    f["oran_alt25"] = _ilk_dolu_kolon(f, ["Avg<2.5", "B365<2.5", "Max<2.5"])

    if gecmisi_at:
        f = f[f["Tarih"] >= simdi_tr().normalize()]

    f = f.sort_values("Tarih").reset_index(drop=True)
    return f, mevcut_kitapcilar


DIS_KAPSAM_URL = "https://api.football-data.org/v4/matches"
# football-data.org yarışma kodu -> (bizim kod, görünen ad). None ad = LIGLER'den.
_DIS_KOD = {
    "CL": ("ŞL", "UEFA Şampiyonlar Ligi"),
    "PL": ("E0", None), "ELC": ("E1", None), "BL1": ("D1", None),
    "SA": ("I1", None), "PD": ("SP1", None), "FL1": ("F1", None),
    "DED": ("N1", None), "PPL": ("P1", None), "BSA": ("BRA", None),
    "EC": ("EURO", "Avrupa Şampiyonası"), "WC": ("DK", "Dünya Kupası"),
}


AYAR_DOSYASI = os.path.join(VERI_KLASORU, "ayarlar.json")


def ayar_oku(ad: str) -> str:
    """Panelden kaydedilen ayarı okur (data/ayarlar.json — kalıcı diskte)."""
    try:
        with open(AYAR_DOSYASI, encoding="utf-8") as f:
            return str(json.load(f).get(ad) or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def ayar_yaz(ad: str, deger: str) -> None:
    os.makedirs(VERI_KLASORU, exist_ok=True)
    try:
        with open(AYAR_DOSYASI, encoding="utf-8") as f:
            mevcut = json.load(f)
    except (OSError, json.JSONDecodeError):
        mevcut = {}
    deger = (deger or "").strip()
    if deger:
        mevcut[ad] = deger
    else:
        mevcut.pop(ad, None)
    gecici = AYAR_DOSYASI + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(mevcut, f)
    os.replace(gecici, AYAR_DOSYASI)


def gizli_anahtar(env_adi: str, ayar_adi: str) -> str:
    """Önce ortam değişkeni, yoksa panelden kaydedilen ayar."""
    return (os.environ.get(env_adi) or "").strip() or ayar_oku(ayar_adi)


# geniş kapsama katmanının son deneme durumu (arayüzde öz-teşhis için)
DIS_SON_DURUM: dict = {"zaman": None, "mac": None, "hata": None, "anahtar_var": False}


def dis_fikstur(gun_sayisi: int = 8, yenile: bool = False) -> pd.DataFrame | None:
    """Geniş kapsama fikstürü: football-data.org (ücretsiz anahtar, opsiyonel).

    FOOTBALL_DATA_ORG_KEY tanımlıysa Şampiyonlar Ligi (elemeler dahil) ve
    büyük liglerin maçlarını, oranlar yayınlanmadan günler önce takvime
    düşürür. Oran içermez; anahtar yoksa None döner (özellik uykuda).
    """
    anahtar = gizli_anahtar("FOOTBALL_DATA_ORG_KEY", "football_data_org_key")
    DIS_SON_DURUM.update(
        {"zaman": simdi_tr().strftime("%H:%M"), "anahtar_var": bool(anahtar), "hata": None, "mac": None}
    )
    onbellek = os.path.join(VERI_KLASORU, "fixtures_dis.json")
    govde = None
    taze = (
        os.path.exists(onbellek)
        and not yenile
        and time.time() - os.path.getmtime(onbellek) < FIKSTUR_TTL_SANIYE
    )
    if taze:
        try:
            with open(onbellek, encoding="utf-8") as f:
                govde = json.load(f)
        except (OSError, json.JSONDecodeError):
            govde = None
    if govde is None:
        if not anahtar:
            DIS_SON_DURUM["hata"] = "anahtar tanımlı değil — aşağıdaki kutudan yapıştırın"
            return None
        bas = simdi_tr().date()
        son = (simdi_tr() + pd.Timedelta(days=gun_sayisi)).date()
        try:
            yanit = requests.get(
                DIS_KAPSAM_URL,
                params={"dateFrom": str(bas), "dateTo": str(son)},
                headers={"X-Auth-Token": anahtar, "User-Agent": KULLANICI_AJANI},
                timeout=30,
                proxies=proxy_ayari() or None,
            )
            if yanit.status_code != 200:
                ek = " — anahtar hatalı/eksik olabilir" if yanit.status_code in (400, 403) else ""
                raise ValueError(f"HTTP {yanit.status_code}{ek}")
            govde = yanit.json()
            os.makedirs(VERI_KLASORU, exist_ok=True)
            with open(onbellek, "w", encoding="utf-8") as f:
                json.dump(govde, f)
        except Exception as hata:  # noqa: BLE001 - kapsama katmanı ana akışı asla düşürmez
            DIS_SON_DURUM["hata"] = str(hata)
            try:
                with open(onbellek, encoding="utf-8") as f:
                    govde = json.load(f)
                DIS_SON_DURUM["hata"] = f"canlı çekilemedi ({hata}); önbellek kullanılıyor"
            except (OSError, json.JSONDecodeError):
                return None

    satirlar = []
    for m in govde.get("matches") or []:
        yarisma = m.get("competition") or {}
        kod, ad = _DIS_KOD.get(yarisma.get("code"), (yarisma.get("code") or "?", yarisma.get("name")))
        utc = pd.to_datetime(m.get("utcDate"), errors="coerce", utc=True)
        if pd.isna(utc):
            continue
        try:
            tarih = utc.tz_convert("Europe/Istanbul").tz_localize(None)
        except Exception:  # noqa: BLE001
            tarih = utc.tz_localize(None) + pd.Timedelta(hours=3)
        ev = (m.get("homeTeam") or {}).get("shortName") or (m.get("homeTeam") or {}).get("name")
        dep = (m.get("awayTeam") or {}).get("shortName") or (m.get("awayTeam") or {}).get("name")
        if not ev or not dep:
            continue
        satirlar.append({"Div": kod, "LigAdi": ad, "Tarih": tarih, "HomeTeam": ev, "AwayTeam": dep})
    d = pd.DataFrame(satirlar)
    DIS_SON_DURUM["mac"] = int(len(d))
    if d.empty:
        return d
    for k in ("oran_ev", "oran_berabere", "oran_dep", "oran_ust25", "oran_alt25",
              "oran_max_ev", "oran_max_berabere", "oran_max_dep"):
        d[k] = float("nan")
    return d


def takim_listesi(df: pd.DataFrame, lig: str | None = None) -> pd.DataFrame:
    """Takım adlarını maç sayısı ve son görülme tarihiyle listeler."""
    if lig:
        df = df[df["Div"] == lig]
    ev = df[["HomeTeam", "Tarih", "Div"]].rename(columns={"HomeTeam": "Takim"})
    dep = df[["AwayTeam", "Tarih", "Div"]].rename(columns={"AwayTeam": "Takim"})
    hepsi = pd.concat([ev, dep])
    ozet = (
        hepsi.groupby("Takim")
        .agg(mac=("Tarih", "size"), son_mac=("Tarih", "max"), lig=("Div", "last"))
        .sort_values("Takim")
        .reset_index()
    )
    return ozet


# ---------------------------------------------------------- odds-api.io: gerçek İY/MS piyasa oranları

ODDSAPI_TABAN = "https://api.odds-api.io/v3"
# Ücretsiz pakette hesap başına 2 kitapçı seçilebiliyor; bu hesapta kilitli ikili.
# İY/MS pazarını fiilen Bet365, korner baremlerini 1xbet taşıyor — her pazar için
# ikisinden en iyi fiyat alınır.
ODDSAPI_KITAPCILAR = "Bet365,1xbet"
_ODDSAPI_ORAN_TTL = 6 * 3600     # maç başı oran önbelleği
_ODDSAPI_MAC_TTL = 12 * 3600     # lig fikstürü önbelleği
_ODDSAPI_LIG_TTL = 24 * 3600     # lig listesi önbelleği

# öz-teşhis (arayüz "piyasa oranı neden yok" diyebilsin)
IYMS_SON_DURUM: dict = {"zaman": None, "hata": None, "anahtar_var": False}

# Div kodu → odds-api.io lig adı ön eki (kaynağın gerçek adlandırmasıyla;
# karşılaştırma noktalama/boşluk bağımsız yapılır, ör. "LaLiga" ↔ "La Liga")
_ODDSAPI_LIG_IPUCU = {
    "E0": "England - Premier League", "E1": "England - Championship",
    "E2": "England - League One", "E3": "England - League Two",
    "EC": "England - National League",
    "SC0": "Scotland - Premiership", "SC1": "Scotland - Championship",
    "SC2": "Scotland - League One", "SC3": "Scotland - League Two",
    "D1": "Germany - Bundesliga", "D2": "Germany - 2. Bundesliga",
    "I1": "Italy - Serie A", "I2": "Italy - Serie B",
    "SP1": "Spain - LaLiga", "SP2": "Spain - LaLiga 2",
    "F1": "France - Ligue 1", "F2": "France - Ligue 2",
    "N1": "Netherlands - Eredivisie", "B1": "Belgium - First Division A",
    "P1": "Portugal - Liga Portugal", "T1": "Turkiye - Super Lig",
    "G1": "Greece - Super League",
    "ARG": "Argentina - Primera LPF", "AUT": "Austria - Bundesliga",
    "BRA": "Brazil - Brasileiro Serie A", "CHN": "China - Chinese Super League",
    "DNK": "Denmark - Superligaen", "FIN": "Finland - Veikkausliiga",
    "IRL": "Ireland - Premier Division", "JPN": "Japan - J-League",
    "MEX": "Mexico - Liga MX", "NOR": "Norway - Eliteserien",
    "POL": "Poland - Ekstraklasa", "ROU": "Romania - Superliga",
    "RUS": "Russia - Premier League", "SWE": "Sweden - Allsvenskan",
    "SWZ": "Switzerland - Super League", "USA": "USA - MLS",
    "ŞL": "International Clubs - UEFA Champions League",
    "EL": "International Clubs - UEFA Europa League",
    "CLI": "International Clubs - CONMEBOL Libertadores",
    "CSA": "International Clubs - CONMEBOL Sudamericana",
}
_ODDSAPI_DISLA = ("women", "u17", "u19", "u20", "u21", "u23", "reserve", "youth", "amateur")

_IYMS_ETIKET = {
    "Home / Home": "1/1", "Home / Draw": "1/0", "Home / Away": "1/2",
    "Draw / Home": "0/1", "Draw / Draw": "0/0", "Draw / Away": "0/2",
    "Away / Home": "2/1", "Away / Draw": "2/0", "Away / Away": "2/2",
}


def _oddsapi_anahtar() -> str:
    return gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key")


def _oddsapi_getir(yol: str, parametreler: dict):
    parametreler = dict(parametreler, apiKey=_oddsapi_anahtar())
    yanit = requests.get(
        ODDSAPI_TABAN + yol, params=parametreler,
        headers={"User-Agent": KULLANICI_AJANI}, timeout=12,  # tarama bütçesini tek istek yemesin
    )
    yanit.raise_for_status()
    govde = yanit.json()
    if isinstance(govde, dict) and govde.get("error"):
        raise RuntimeError(str(govde["error"]))
    return govde


def _oddsapi_onbellek(ad: str) -> dict:
    try:
        with open(os.path.join(VERI_KLASORU, ad), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _oddsapi_onbellek_yaz(ad: str, veri_sozlugu: dict) -> None:
    os.makedirs(VERI_KLASORU, exist_ok=True)
    yol = os.path.join(VERI_KLASORU, ad)
    with open(yol + ".tmp", "w", encoding="utf-8") as f:
        json.dump(veri_sozlugu, f, ensure_ascii=False)
    os.replace(yol + ".tmp", yol)


def _oddsapi_ligler() -> list:
    onbellek = _oddsapi_onbellek("oddsapi_ligler.json")
    if onbellek.get("veri") and time.time() - onbellek.get("zaman", 0) < _ODDSAPI_LIG_TTL:
        return onbellek["veri"]
    ligler = _oddsapi_getir("/leagues", {"sport": "football"})
    _oddsapi_onbellek_yaz("oddsapi_ligler.json", {"zaman": time.time(), "veri": ligler})
    return ligler


def _oddsapi_maclar(slug: str) -> list:
    onbellek = _oddsapi_onbellek("oddsapi_maclar.json")
    kayit = onbellek.get(slug)
    if kayit and time.time() - kayit.get("zaman", 0) < _ODDSAPI_MAC_TTL:
        return kayit["veri"]
    maclar = _oddsapi_getir("/events", {"sport": "football", "league": slug})
    onbellek[slug] = {"zaman": time.time(), "veri": maclar}
    # eski ligleri buda ki dosya şişmesin
    for eski in [k for k, v in onbellek.items()
                 if time.time() - v.get("zaman", 0) > 3 * _ODDSAPI_MAC_TTL]:
        onbellek.pop(eski, None)
    _oddsapi_onbellek_yaz("oddsapi_maclar.json", onbellek)
    return maclar


_TAKIM_GENEL_EK = {
    "cf", "fc", "cd", "ca", "ac", "afc", "cfc", "sc", "club", "clube", "cp",
    "de", "the", "fk", "if", "bk", "sk", "ff", "aif", "calcio", "deportivo",
}


_TAKIM_HARF_CEVRIM = str.maketrans(
    {"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
     "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "à": "a", "â": "a",
     "ã": "a", "ê": "e", "ô": "o", "û": "u", "ñ": "n", "ß": "ss"}
)


def _oddsapi_takim_parcalari(ad: str) -> set[str]:
    duz = str(ad).lower().translate(_TAKIM_HARF_CEVRIM)
    duz = re.sub(r"[^a-z0-9 ]", " ", duz)
    parcalar = {p for p in duz.split() if len(p) > 1 and p not in _TAKIM_GENEL_EK}
    return parcalar or ({duz.strip()} if duz.strip() else set())


def _oddsapi_takim_puani(bizim: str, onlarin: str) -> float:
    A, B = _oddsapi_takim_parcalari(bizim), _oddsapi_takim_parcalari(onlarin)
    if not A or not B:
        return 0.0
    kesisim = len(A & B)
    if kesisim:
        return kesisim / min(len(A), len(B))
    return difflib.SequenceMatcher(None, " ".join(sorted(A)), " ".join(sorted(B))).ratio()


def _oddsapi_oranlari_isle(govde: dict) -> dict | None:
    """İki kitapçının yanıtından İY/MS (kombo başına en iyi fiyat) + korner baremi."""
    kombolar: dict[str, float] = {}
    kombo_kitapci: dict[str, str] = {}
    korner = korner_kitapci = None
    guncel = ""
    for kitapci, pazarlar in (govde.get("bookmakers") or {}).items():
        if "(no latency)" in kitapci:
            continue
        for pazar in pazarlar:
            ad = pazar.get("name")
            if ad == "Half Time / Full Time":
                guncel = guncel or str(pazar.get("updatedAt", ""))[:16].replace("T", " ")
                for satir in pazar.get("odds", []):
                    k = _IYMS_ETIKET.get(satir.get("label"))
                    try:
                        oran = float(satir.get("odds"))
                    except (TypeError, ValueError):
                        continue
                    if k and oran > 1 and oran > kombolar.get(k, 0.0):
                        kombolar[k] = oran
                        kombo_kitapci[k] = kitapci
            elif ad == "Corners Totals":
                basamaklar = []
                for satir in pazar.get("odds", []):
                    try:
                        basamaklar.append({"cizgi": float(satir.get("hdp")),
                                           "ust": float(satir.get("over")),
                                           "alt": float(satir.get("under"))})
                    except (TypeError, ValueError):
                        continue
                if basamaklar and (korner is None or len(basamaklar) > len(korner)):
                    korner, korner_kitapci = basamaklar, kitapci
    if not kombolar and not korner:
        return None
    kitapci_ana = None
    if kombo_kitapci:
        adlar = list(kombo_kitapci.values())
        kitapci_ana = max(set(adlar), key=adlar.count)
    return {"kombolar": kombolar, "kombo_kitapci": kombo_kitapci, "kitapci": kitapci_ana,
            "guncel": guncel, "korner": korner, "korner_kitapci": korner_kitapci}


def _oddsapi_iyms_cek(mac_id: int) -> dict | None:
    onbellek = _oddsapi_onbellek("oddsapi_iyms.json")
    kayit = onbellek.get(str(mac_id))
    taze = kayit and time.time() - kayit.get("zaman", 0) < _ODDSAPI_ORAN_TTL
    eski_bicim = kayit and kayit.get("veri") and "kombo_kitapci" not in kayit["veri"]
    if taze and not eski_bicim:
        return kayit["veri"] or None
    govde = _oddsapi_getir("/odds", {"eventId": mac_id, "bookmakers": ODDSAPI_KITAPCILAR})
    sonuc = _oddsapi_oranlari_isle(govde)
    onbellek[str(mac_id)] = {"zaman": time.time(), "veri": sonuc}
    for eski in [k for k, v in onbellek.items()
                 if time.time() - v.get("zaman", 0) > 4 * _ODDSAPI_ORAN_TTL]:
        onbellek.pop(eski, None)
    _oddsapi_onbellek_yaz("oddsapi_iyms.json", onbellek)
    return sonuc


def iyms_piyasa(ev: str, dep: str, lig_kodu: str, tarih: pd.Timestamp) -> dict | None:
    """Bir bülten maçı için Bet365'in gerçek İY/MS oranları (odds-api.io).

    Anahtar yoksa None döner (özellik uykuda). Lig, ada göre eşlenir; maç,
    takım adı benzerliği + tarih yakınlığıyla bulunur. Tüm ağ sonuçları
    diskte önbelleklenir — günlük ücretsiz istek bütçesi (500) rahat yeter.
    """
    IYMS_SON_DURUM.update(
        {"zaman": simdi_tr().strftime("%H:%M"), "anahtar_var": bool(_oddsapi_anahtar()), "hata": None}
    )
    if not IYMS_SON_DURUM["anahtar_var"]:
        return None
    ipucu = _ODDSAPI_LIG_IPUCU.get(str(lig_kodu))
    if not ipucu:
        return None
    try:
        def _duz(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", str(s).lower())

        ipucu_duz = _duz(ipucu)
        adaylar = sorted(
            (
                l for l in _oddsapi_ligler()
                if _duz(l.get("name", "")).startswith(ipucu_duz)
                and not any(d in str(l.get("name", "")).lower() for d in _ODDSAPI_DISLA)
            ),
            key=lambda l: len(str(l.get("name", ""))),  # en kısa (en birebir) ad önce
        )
        hedef_utc = pd.Timestamp(tarih) - pd.Timedelta(hours=3)  # TR → UTC
        en_mac, en_puan = None, 0.0
        for lig in adaylar[:5]:
            for m in _oddsapi_maclar(lig["slug"]):
                if m.get("status") == "settled":
                    continue
                try:
                    mac_utc = pd.Timestamp(str(m.get("date", "")).replace("Z", ""))
                except ValueError:
                    continue
                if abs((mac_utc - hedef_utc).total_seconds()) > 36 * 3600:
                    continue
                puan = min(_oddsapi_takim_puani(ev, m.get("home", "")),
                           _oddsapi_takim_puani(dep, m.get("away", "")))
                if puan > en_puan:
                    en_mac, en_puan = m, puan
        if en_mac is None or en_puan < 0.5:
            return None
        return _oddsapi_iyms_cek(int(en_mac["id"]))
    except Exception as hata:  # noqa: BLE001 — ağ/format hataları özelliği durdurmasın
        IYMS_SON_DURUM["hata"] = str(hata)[:200]
        return None
