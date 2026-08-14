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
import warnings

import pandas as pd
import requests

VERI_KLASORU = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
KAYNAK_URL = "https://www.football-data.co.uk/mmz4281/{sezon}/{lig}.csv"

LIGLER = {
    "T1": "Türkiye Süper Lig",
    "E0": "İngiltere Premier League",
    "E1": "İngiltere Championship",
    "SP1": "İspanya La Liga",
    "D1": "Almanya Bundesliga",
    "I1": "İtalya Serie A",
    "F1": "Fransa Ligue 1",
    "N1": "Hollanda Eredivisie",
    "P1": "Portekiz Primeira Liga",
    "B1": "Belçika Pro League",
    "G1": "Yunanistan Süper Lig",
    "SC0": "İskoçya Premiership",
}

VARSAYILAN_LIGLER = ["T1", "E0", "SP1", "D1", "I1", "F1"]

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

        df["oran_ev"] = _ilk_dolu_kolon(df, _EV_ORAN)
        df["oran_berabere"] = _ilk_dolu_kolon(df, _BERABERE_ORAN)
        df["oran_dep"] = _ilk_dolu_kolon(df, _DEP_ORAN)
        df["oran_ust25"] = _ilk_dolu_kolon(df, _UST25_ORAN)
        df["oran_alt25"] = _ilk_dolu_kolon(df, _ALT25_ORAN)

    kolonlar = [
        "Div", "Sezon", "Tarih", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "oran_ev", "oran_berabere", "oran_dep", "oran_ust25", "oran_alt25",
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
