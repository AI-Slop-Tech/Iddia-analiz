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

    kolonlar = [
        "Div", "Sezon", "Tarih", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "HTHG", "HTAG", *ISTATISTIK_KOLONLARI,
        "oran_ev", "oran_berabere", "oran_dep", "oran_ust25", "oran_alt25",
        "oran_ev_maks", "oran_berabere_maks", "oran_dep_maks",
        "oran_ust25_maks", "oran_alt25_maks",
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
