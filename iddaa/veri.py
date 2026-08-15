"""Veri katmanı.

Kaynak: football-data.co.uk — API anahtarı gerektirmeyen, 1993'e kadar giden
ücretsiz CSV arşivi. Her maç için skorlar ve çok sayıda bahis şirketinin
(Bet365, Pinnacle, piyasa ortalaması...) açılış/kapanış oranları bulunur.
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
KAYNAK_URL = "https://www.football-data.co.uk/mmz4281/{sezon}/{lig}.csv"
FIKSTUR_URL = "https://www.football-data.co.uk/fixtures.csv"
FIKSTUR_TTL_SANIYE = 6 * 3600  # fikstür dosyası en fazla 6 saatte bir tazelenir

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

# Varsayılan: tüm ligler — fikstürdeki her maçın 10 yıllık geçmişi bulunsun diye
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
    oturum = requests.Session()
    oturum.headers["User-Agent"] = "iddaa-analiz/1.0"

    for lig in ligler:
        for sezon in kodlar:
            hedef = os.path.join(VERI_KLASORU, f"{sezon}_{lig}.csv")
            if os.path.exists(hedef) and not yenile and sezon != guncel_kod:
                ozet["onbellek"] += 1
                continue
            url = KAYNAK_URL.format(sezon=sezon, lig=lig)
            try:
                yanit = oturum.get(url, timeout=30)
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
            except Exception as h:  # noqa: BLE001 - tek dosya hatası akışı durdurmasın
                ozet["hata"].append(f"{lig} {sezon}: {h}")
                print(f"  ✗ {lig} {sezon[:2]}/{sezon[2:]} indirilemedi ({h})")
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


def veriyi_yukle(ligler: list[str] | None = None) -> pd.DataFrame:
    """Önbellekteki tüm CSV'leri tek bir normalize DataFrame'de birleştirir."""
    dosyalar = sorted(glob.glob(os.path.join(VERI_KLASORU, "*_*.csv")))
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
        # ilk yarı skorları (çok eski dosyalarda olmayabilir -> NaN kalır)
        for k in ("HTHG", "HTAG"):
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
        "HTHG", "HTAG",
        "oran_ev", "oran_berabere", "oran_dep", "oran_ust25", "oran_alt25",
        "oran_ev_maks", "oran_berabere_maks", "oran_dep_maks",
        "oran_ust25_maks", "oran_alt25_maks",
    ]
    return df[kolonlar].sort_values("Tarih").reset_index(drop=True)


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
        yanit = requests.get(FIKSTUR_URL, timeout=30, headers={"User-Agent": "iddaa-analiz/1.0"})
        if yanit.status_code != 200 or b"Div" not in yanit.content[:200]:
            if os.path.exists(hedef):  # eski kopya varsa onunla devam et
                return hedef
            raise RuntimeError(f"Fikstür indirilemedi (HTTP {yanit.status_code})")
        with open(hedef, "wb") as f:
            f.write(yanit.content)
    return hedef


def fikstur_yukle(ligler: list[str] | None = None,
                  yenile: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Fikstürü normalize eder; (DataFrame, mevcut kitapçı önekleri) döndürür.

    Birleşik kolonlar: oran_ev/berabere/dep (analiz için piyasa ortalaması,
    yoksa Bet365), oran_max_* (piyasadaki en yüksek oran), oran_ust25/alt25.
    Kitapçı bazlı ham kolonlar ({önek}H/D/A) ayrıca korunur.
    """
    yol = fikstur_indir(yenile=yenile)
    f = _tek_dosya_oku(yol)
    if f is None or "Div" not in f.columns:
        raise RuntimeError("Fikstür dosyası okunamadı.")
    f = f.dropna(subset=["Div", "Date", "HomeTeam", "AwayTeam"]).copy()
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

    # analiz oranı: piyasa ortalaması en sağlıklısı; yoksa Bet365, o da yoksa Max
    f["oran_ev"] = _ilk_dolu_kolon(f, ["AvgH", "B365H", "MaxH"])
    f["oran_berabere"] = _ilk_dolu_kolon(f, ["AvgD", "B365D", "MaxD"])
    f["oran_dep"] = _ilk_dolu_kolon(f, ["AvgA", "B365A", "MaxA"])
    # en iyi (en yüksek) piyasa oranı: Max kolonu, yoksa kitapçıların satır maksimumu
    for uc, kolon in (("ev", "H"), ("berabere", "D"), ("dep", "A")):
        kaynaklar = [f"{p}{kolon}" for p in mevcut_kitapcilar]
        satir_maks = f[kaynaklar].max(axis=1) if kaynaklar else pd.Series(float("nan"), index=f.index)
        f[f"oran_max_{uc}"] = pd.to_numeric(f.get(f"Max{kolon}"), errors="coerce").fillna(satir_maks)
    f["oran_ust25"] = _ilk_dolu_kolon(f, ["Avg>2.5", "B365>2.5", "Max>2.5"])
    f["oran_alt25"] = _ilk_dolu_kolon(f, ["Avg<2.5", "B365<2.5", "Max<2.5"])

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
