"""Kupon defteri: kullanıcının kurduğu kuponların kalıcı kaydı ve sonuçlandırılması.

Her kupon; taramadan, Sürpriz Radarı'ndan ya da elle eklenen seçimlerden (bacak)
oluşur. Sonuçlandırma arşivden otomatik yapılır: maç günü geçtiyse tarih (±1 gün)
+ çözülmüş takım adlarıyla satır bulunur ve pazar kuralı uygulanır. Arşivin
kapsamadığı maçlar (İY verisi olmayan ligler, dünya fikstürü) "belirsiz" kalır
ve arayüzden elle işaretlenebilir.
"""

from __future__ import annotations

import json
import os
import time
from itertools import combinations

import pandas as pd

from . import analiz, veri

KUPON_DOSYASI = os.path.join(veri.VERI_KLASORU, "kuponlar.json")
GECERLI_SISTEMLER = ("kombine",)  # + "k/n" biçimi (ör. "2/4") çalışma anında doğrulanır


def _oku() -> list[dict]:
    try:
        with open(KUPON_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _yaz(kuponlar: list[dict]) -> None:
    os.makedirs(veri.VERI_KLASORU, exist_ok=True)
    gecici = KUPON_DOSYASI + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kuponlar, f, ensure_ascii=False)
    os.replace(gecici, KUPON_DOSYASI)


def _sistem_dogrula(sistem: str, bacak_sayisi: int) -> str:
    s = str(sistem or "kombine").strip()
    if s == "kombine":
        return s
    parcalar = s.split("/")
    if len(parcalar) == 2 and parcalar[0].isdigit() and parcalar[1].isdigit():
        k, n = int(parcalar[0]), int(parcalar[1])
        if n == bacak_sayisi and 2 <= k < n:
            return f"{k}/{n}"
    raise ValueError(f"Geçersiz sistem: {sistem}")


def olustur(secimler: list[dict], sistem: str = "kombine", ad: str = "") -> dict:
    if not isinstance(secimler, list) or not (1 <= len(secimler) <= 12):
        raise ValueError("Kupon 1-12 seçim içermeli.")
    bacaklar = []
    for s in secimler:
        oran = float(s.get("oran", 0))
        if not (1.01 <= oran <= 1000):
            raise ValueError("Her seçimde geçerli bir oran olmalı.")
        bacaklar.append({
            "tarih": str(s.get("tarih", ""))[:10],
            "saat": str(s.get("saat", ""))[:5],
            "lig": str(s.get("lig", ""))[:8],
            "ev": str(s.get("ev", ""))[:60],
            "dep": str(s.get("dep", ""))[:60],
            "pazar": str(s.get("pazar", ""))[:24],
            "oran": oran,
            "kaynak": str(s.get("kaynak", "elle"))[:12],
            "durum": "bekliyor",
            "elle": False,
        })
    kupon = {
        "id": int(time.time() * 1000),
        "ad": str(ad or "")[:60],
        "olusturma": time.strftime("%d.%m.%Y %H:%M"),
        "sistem": _sistem_dogrula(sistem, len(bacaklar)),
        "secimler": bacaklar,
    }
    kuponlar = _oku()
    kuponlar.insert(0, kupon)
    _yaz(kuponlar[:200])  # defter sınırı: en yeni 200 kupon
    return kupon


def sil(kupon_id: int) -> bool:
    kuponlar = _oku()
    yeni = [k for k in kuponlar if k.get("id") != kupon_id]
    if len(yeni) == len(kuponlar):
        return False
    _yaz(yeni)
    return True


def elle_isaretle(kupon_id: int, indeks: int, durum: str) -> bool:
    if durum not in ("tuttu", "yatti", "bekliyor"):
        raise ValueError("Durum tuttu/yatti/bekliyor olmalı.")
    kuponlar = _oku()
    for k in kuponlar:
        if k.get("id") == kupon_id and 0 <= indeks < len(k["secimler"]):
            k["secimler"][indeks]["durum"] = durum
            k["secimler"][indeks]["elle"] = durum != "bekliyor"
            _yaz(kuponlar)
            return True
    return False


def _pazar_sonucu(pazar: str, r) -> str:
    """Arşiv satırına göre pazar kuralı: tuttu / yatti / belirsiz."""
    p = str(pazar).upper().replace("İ", "I")
    fthg, ftag = int(r["FTHG"]), int(r["FTAG"])
    toplam = fthg + ftag
    ftr = "1" if fthg > ftag else ("0" if fthg == ftag else "2")

    if p in ("MS1", "MS0", "MS2"):
        return "tuttu" if p[-1] == ftr else "yatti"
    if p.startswith(("ÇS", "CS", "ÇŞ")):  # çifte şans: "ÇŞ 1X" / "ÇŞ 12" / "ÇŞ X2"
        kapsam = {"1X": ("1", "0"), "12": ("1", "2"), "X2": ("0", "2")}.get(p.split()[-1])
        if kapsam:
            return "tuttu" if ftr in kapsam else "yatti"
        return "belirsiz"
    if p.startswith("IY "):  # "İY 0.5 ÜST" / "İY 1.5 ALT" (İ→I dönüşümü sonrası)
        parcalar = p.split()
        if len(parcalar) == 3:
            try:
                cizgi = float(parcalar[1])
            except ValueError:
                return "belirsiz"
            if pd.isna(r.get("HTHG")) or pd.isna(r.get("HTAG")):
                return "belirsiz"
            iy_toplam = int(r["HTHG"]) + int(r["HTAG"])
            ust_geldi = iy_toplam > cizgi
            return "tuttu" if ust_geldi == parcalar[2].startswith(("U", "Ü")) else "yatti"
        # "İY 0.5 ÜST" kalıbına uymayan İY adları (ör. "İY 0" = ilk yarı sonucu)
        # aşağıdaki ortak çözücüye düşsün; burada "belirsiz" demek onları yutuyordu.
    if p.startswith("KORNER"):  # "KORNER ÜST 9.5" / "KORNER ALT 9.5"
        parcalar = p.split()
        if len(parcalar) == 3:
            try:
                cizgi = float(parcalar[2])
            except ValueError:
                return "belirsiz"
            if pd.isna(r.get("HC")) or pd.isna(r.get("AC")):
                return "belirsiz"
            korner = int(r["HC"]) + int(r["AC"])
            ust_geldi = korner > cizgi
            return "tuttu" if ust_geldi == parcalar[1].startswith(("U", "Ü")) else "yatti"
        return "belirsiz"
    if p.startswith(("UST", "ÜST")) and "2.5" in p:
        return "tuttu" if toplam > 2.5 else "yatti"
    if p.startswith("ALT") and "2.5" in p:
        return "tuttu" if toplam < 2.5 else "yatti"
    if p.startswith("KG"):
        var = fthg > 0 and ftag > 0
        return "tuttu" if var == ("VAR" in p) else "yatti"
    if "/" in p:  # İY/MS kombinasyonu: "IY/MS 1/2" ya da düz "1/2"
        kombo = p.split()[-1]
        parcalar = kombo.split("/")
        if len(parcalar) == 2 and all(x in "102" for x in parcalar):
            if pd.isna(r.get("HTHG")) or pd.isna(r.get("HTAG")):
                return "belirsiz"
            hthg, htag = int(r["HTHG"]), int(r["HTAG"])
            iy = "1" if hthg > htag else ("0" if hthg == htag else "2")
            return "tuttu" if (iy == parcalar[0] and ftr == parcalar[1]) else "yatti"
    # Yukarıdaki kurallar eski (dar) pazar kümesini aynen karşılamaya devam eder.
    # Buraya düşen ad, Sistem Önerisi'nin geniş havuzundan gelmiş olabilir
    # (takım gol sayısı, handikap, MS+Alt/Üst, yarı sonucu, kart, takım korneri…).
    # Öyleyse ortak çözücüye sorulur; o da bilmiyorsa gerçekten belirsizdir.
    gercek = analiz.pazar_gerceklesti(str(pazar), r)
    if gercek is not None:
        return "tuttu" if gercek else "yatti"
    return "belirsiz"


def sonuclandir(df: pd.DataFrame | None) -> list[dict]:
    """Açık bacakları arşivden sonuçlandırır, dosyaya işler, defteri döndürür."""
    kuponlar = _oku()
    if df is None or not kuponlar:
        return kuponlar

    simdi = veri.simdi_tr()
    cozucu = veri.takim_cozucu(df, hizli=True)
    degisti = False

    def _coz(ad: str) -> str:
        try:
            return cozucu(str(ad))
        except ValueError:
            return str(ad)

    for k in kuponlar:
        for b in k["secimler"]:
            if b["durum"] != "bekliyor" or b.get("elle"):
                continue
            try:
                t = pd.to_datetime(b["tarih"], dayfirst=True)
            except (ValueError, TypeError):
                continue
            if t >= simdi.normalize():  # maç günü geçmeden arama yapılmaz
                continue
            ev, dep = _coz(b["ev"]), _coz(b["dep"])
            aday = df[(df["Tarih"] >= t - pd.Timedelta(days=1))
                      & (df["Tarih"] <= t + pd.Timedelta(days=1))
                      & (df["HomeTeam"] == ev) & (df["AwayTeam"] == dep)]
            if aday.empty:
                continue
            r = aday.iloc[-1]
            sonuc = _pazar_sonucu(b["pazar"], r)
            b["skor"] = f"{int(r['FTHG'])}-{int(r['FTAG'])}"
            if not pd.isna(r.get("HTHG")):
                b["iy_skor"] = f"{int(r['HTHG'])}-{int(r['HTAG'])}"
            if sonuc != "belirsiz":
                b["durum"] = sonuc
            degisti = True

    if degisti:
        _yaz(kuponlar)
    return kuponlar


def degerlendir(kupon: dict) -> dict:
    """Kuponun toplam oranı, durumu ve (sonuçlanmışsa) net getirisi."""
    bacaklar = kupon["secimler"]
    oranlar = [b["oran"] for b in bacaklar]
    durumlar = [b["durum"] for b in bacaklar]
    bekleyen = durumlar.count("bekliyor")
    tutan = durumlar.count("tuttu")
    n = len(bacaklar)

    sistem = kupon.get("sistem", "kombine")
    if sistem == "kombine":
        toplam_oran = 1.0
        for o in oranlar:
            toplam_oran *= o
        maliyet = 1
        if "yatti" in durumlar:
            durum, net = "yatti", -1.0
        elif bekleyen:
            durum, net = "bekliyor", None
        else:
            durum, net = "tuttu", toplam_oran - 1.0
    else:
        kk = int(sistem.split("/")[0])
        kolonlar = list(combinations(range(n), kk))
        maliyet = len(kolonlar)
        toplam_oran = None  # sistemde tek "toplam oran" yoktur
        if bekleyen:
            durum, net = "bekliyor", None
        else:
            kazanc = 0.0
            for c in kolonlar:
                if all(durumlar[i] == "tuttu" for i in c):
                    carpim = 1.0
                    for i in c:
                        carpim *= oranlar[i]
                    kazanc += carpim
            net = kazanc - maliyet
            durum = "tuttu" if net > 0 else "yatti"

    return {**kupon, "toplam_oran": toplam_oran, "maliyet": maliyet,
            "durum": durum, "net": net, "tutan": tutan, "bekleyen": bekleyen}
