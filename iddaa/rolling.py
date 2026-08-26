"""Rolling defteri: kasayı her adımda tamamen yeni bir seçime taşıma planı.

Kullanıcının isteği: "400 TL ile başla, her gün ~2.00'lik bir seçim, tutarsa
tamamı bir sonrakine — 15 günde ne olur?" Tablo sekmesi gibi satır satır
takip edilir; oranları kullanıcı yazar, bakiye zincirini burası hesaplar,
sonuçlar kupon defterindeki aynı kuralla arşivden otomatik işlenir.

DÜRÜSTLÜK: rolling kâr beklentisi YARATMAZ — varyansı katlar. Adım başına
tutma olasılığı p ve oran o ise seriyi bitirme olasılığı p^N, beklenen değer
yaklaşık B·(p·o)^N'dir; kitapçı marjı yüzünden p·o < 1 olduğundan beklenen
değer başlangıcın ALTINDADIR. Arayüz bu sayıları saklamaz, gösterir.
"""

from __future__ import annotations

import json
import os
import time

import pandas as pd

from . import veri
from .kupon import _pazar_sonucu

ROLLING_DOSYASI = os.path.join(veri.VERI_KLASORU, "rolling.json")
MAKS_PLAN = 20
MAKS_ADIM = 60


def _oku() -> list[dict]:
    try:
        with open(ROLLING_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _yaz(planlar: list[dict]) -> None:
    os.makedirs(veri.VERI_KLASORU, exist_ok=True)
    gecici = ROLLING_DOSYASI + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(planlar, f, ensure_ascii=False)
    os.replace(gecici, ROLLING_DOSYASI)


def olustur(ad: str, baslangic: float, hedef_oran: float = 2.0,
            hedef_gun: int = 15) -> dict:
    baslangic = float(baslangic)
    if not (1 <= baslangic <= 10_000_000):
        raise ValueError("Başlangıç kasası 1 ile 10 milyon TL arasında olmalı.")
    plan = {
        "id": int(time.time() * 1000),
        "ad": str(ad or "Rolling")[:40],
        "baslangic": baslangic,
        "hedef_oran": max(1.01, min(100.0, float(hedef_oran or 2.0))),
        "hedef_gun": max(1, min(MAKS_ADIM, int(hedef_gun or 15))),
        "olusturma": time.strftime("%d.%m.%Y %H:%M"),
        "adimlar": [],
    }
    planlar = _oku()
    planlar.insert(0, plan)
    _yaz(planlar[:MAKS_PLAN])
    return plan


def sil(plan_id: int) -> bool:
    planlar = _oku()
    yeni = [p for p in planlar if p.get("id") != plan_id]
    if len(yeni) == len(planlar):
        return False
    _yaz(yeni)
    return True


def adim_ekle(plan_id: int, secim: dict) -> dict:
    oran = float(secim.get("oran", 0))
    if not (1.01 <= oran <= 1000):
        raise ValueError("Geçerli bir oran girin (1.01 - 1000).")
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if len(p["adimlar"]) >= MAKS_ADIM:
            raise ValueError(f"Plan en fazla {MAKS_ADIM} adım içerebilir.")
        if any(a["durum"] == "yatti" for a in p["adimlar"]):
            raise ValueError("Bu seri bitti (bir adım yattı) — yeni plan açın.")
        p["adimlar"].append({
            "tarih": str(secim.get("tarih", ""))[:10],
            "saat": str(secim.get("saat", ""))[:5],
            "lig": str(secim.get("lig", ""))[:8],
            "ev": str(secim.get("ev", ""))[:60],
            "dep": str(secim.get("dep", ""))[:60],
            "pazar": str(secim.get("pazar", ""))[:24],
            "oran": oran,
            "durum": "bekliyor",
            "elle": False,
        })
        _yaz(planlar)
        return p
    raise ValueError("Plan bulunamadı.")


def adim_duzenle(plan_id: int, indeks: int, alanlar: dict) -> bool:
    """Yalnız 'bekliyor' adımda oran/maç bilgisi değiştirilebilir."""
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if not (0 <= indeks < len(p["adimlar"])):
            return False
        a = p["adimlar"][indeks]
        if a["durum"] != "bekliyor" and "oran" in alanlar:
            raise ValueError("Sonuçlanmış adımın oranı değiştirilemez.")
        if "oran" in alanlar:
            o = float(alanlar["oran"])
            if not (1.01 <= o <= 1000):
                raise ValueError("Geçerli bir oran girin.")
            a["oran"] = o
        for k in ("tarih", "ev", "dep", "pazar", "lig", "saat"):
            if k in alanlar:
                a[k] = str(alanlar[k])[: 60 if k in ("ev", "dep") else 24]
        _yaz(planlar)
        return True
    return False


def elle_isaretle(plan_id: int, indeks: int, durum: str) -> bool:
    if durum not in ("tuttu", "yatti", "bekliyor"):
        raise ValueError("Durum tuttu/yatti/bekliyor olmalı.")
    planlar = _oku()
    for p in planlar:
        if p.get("id") == plan_id and 0 <= indeks < len(p["adimlar"]):
            p["adimlar"][indeks]["durum"] = durum
            p["adimlar"][indeks]["elle"] = durum != "bekliyor"
            _yaz(planlar)
            return True
    return False


def adim_sil(plan_id: int, indeks: int) -> bool:
    """Zincir bozulmasın diye yalnız SON adım silinebilir."""
    planlar = _oku()
    for p in planlar:
        if p.get("id") == plan_id and p["adimlar"] and indeks == len(p["adimlar"]) - 1:
            p["adimlar"].pop()
            _yaz(planlar)
            return True
    return False


def sonuclandir(df: pd.DataFrame | None) -> list[dict]:
    """Bekleyen adımları arşivden sonuçlandırır (kupon defteriyle aynı kural)."""
    planlar = _oku()
    if df is None or not planlar:
        return planlar
    simdi = veri.simdi_tr()
    cozucu = veri.takim_cozucu_onbellekli(df, hizli=True)
    degisti = False

    def _coz(ad: str) -> str:
        try:
            return cozucu(str(ad))
        except ValueError:
            return str(ad)

    for p in planlar:
        for a in p["adimlar"]:
            if a["durum"] != "bekliyor" or a.get("elle") or not a.get("ev"):
                continue
            try:
                t = pd.to_datetime(a["tarih"], dayfirst=True)
            except (ValueError, TypeError):
                continue
            if t >= simdi.normalize():
                continue
            ev, dep = _coz(a["ev"]), _coz(a["dep"])
            aday = df[(df["Tarih"] >= t - pd.Timedelta(days=1))
                      & (df["Tarih"] <= t + pd.Timedelta(days=1))
                      & (df["HomeTeam"] == ev) & (df["AwayTeam"] == dep)]
            if aday.empty:
                continue
            r = aday.iloc[-1]
            sonuc = _pazar_sonucu(a["pazar"], r)
            a["skor"] = f"{int(r['FTHG'])}-{int(r['FTAG'])}"
            if sonuc != "belirsiz":
                a["durum"] = sonuc
            degisti = True
    if degisti:
        _yaz(planlar)
    return planlar


def hesapla(plan: dict) -> dict:
    """Bakiye zinciri: her adımın yatırımı bir önceki bakiyenin TAMAMIDIR."""
    bakiye = float(plan["baslangic"])
    adimlar = []
    plan_durum = "aktif"
    for a in plan["adimlar"]:
        yatirim = round(bakiye, 2)
        if a["durum"] == "tuttu":
            bakiye = bakiye * float(a["oran"])
            sonra = round(bakiye, 2)
        elif a["durum"] == "yatti":
            bakiye = 0.0
            sonra = 0.0
            plan_durum = "yatti"
        else:
            sonra = None  # bekliyor: zincir burada duruyor
        adimlar.append({**a, "yatirim": yatirim, "bakiye": sonra})
        if plan_durum == "yatti":
            # seri bitti; kalan adımlar (varsa) gösterilir ama yatırım 0
            bakiye = 0.0
    tamam = sum(1 for a in plan["adimlar"] if a["durum"] == "tuttu")
    if plan_durum != "yatti" and tamam >= plan.get("hedef_gun", 15):
        plan_durum = "tamam"
    return {
        **plan,
        "adimlar": adimlar,
        "durum": plan_durum,
        "bakiye": round(bakiye, 2),
        "kat": round(bakiye / float(plan["baslangic"]), 2) if plan["baslangic"] else 0,
        "tamamlanan": tamam,
    }
