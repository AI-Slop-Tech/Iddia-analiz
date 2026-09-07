"""Kasa ve performans paneli: kupon defteri GERÇEK para birimiyle (₺) izlenir.

Ne yapar: kullanıcının başlangıç kasası, birim bahis ve Kelly böleni saklanır
(data/kasa.json); kupon defterindeki her kuponun tutarı (miktar) ve gerçekleşen
kârıyla bakiye eğrisi, maksimum düşüş (drawdown), getiri, kapanış çizgisi (CLV)
ve pazar/lig/kaynak kırılımı hesaplanır. Fişte, tüm bacakları ÖLÇÜLMÜŞ olasılık
taşıyan kuponlar için kesirli Kelly tutarı önerilir.

Dürüstlük notları:
- Bakiye GERÇEKLEŞEN sonuçlardır; bekleyen kuponların tutarı "açık risk" olarak
  ayrı gösterilir, bakiyeden düşülmez.
- Tutarı kaydedilmemiş (eski) kuponlar birim bahisle sayılır ve işaretlenir
  (miktar_tahmini).
- Pazar/lig/kaynak kırılımında kupon kârı bacaklara EŞİT bölünür: çok bacaklı
  kuponda bu bir yaklaşımdır (isabet oranı kesindir, kâr payı değil).
- Kelly kazandırmaz, batmayı önler: kenar yoksa (p·o ≤ 1) tutar 0'dır. Girdisi
  ölçülmüş olasılıktır; ölçülmemiş bacak varsa hesaplanmaz.
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict

import pandas as pd

from . import rolling, veri

DOSYA = os.path.join(veri.VERI_KLASORU, "kasa.json")
VARSAYILAN = {"baslangic": 1000.0, "birim": 50.0, "kelly_bolen": 4.0}
KELLY_NOT = ("Kelly kazandırmaz, batmayı önler: kesir = (p·o − 1)/(o − 1)/bölen; kenar yoksa 0. "
             "Yalnız ölçülmüş olasılık taşıyan bacaklarda hesaplanır.")
_KILIT = threading.Lock()


def _bugun() -> str:
    return veri.simdi_tr().strftime("%d.%m.%Y")


def oku() -> dict:
    """Kasa ayarları; dosya yoksa varsayılanlar (başlangıç tarihi bugün, kayıtlı değil)."""
    ayar: dict = dict(VARSAYILAN)
    ayar["baslangic_tarihi"] = None
    ayar["guncelleme"] = None
    try:
        with open(DOSYA, encoding="utf-8") as f:
            ham = json.load(f)
        if isinstance(ham, dict):
            for k in ("baslangic", "birim", "kelly_bolen", "baslangic_tarihi", "guncelleme"):
                if k in ham and ham[k] is not None:
                    ayar[k] = ham[k]
        ayar["kayitli"] = True
    except (OSError, json.JSONDecodeError):
        ayar["kayitli"] = False
    if not ayar.get("baslangic_tarihi"):
        ayar["baslangic_tarihi"] = _bugun()
    for k in ("baslangic", "birim", "kelly_bolen"):
        try:
            ayar[k] = float(ayar[k])
        except (TypeError, ValueError):
            ayar[k] = VARSAYILAN[k]
    return ayar


def dogrula(govde: dict) -> dict:
    """İstek gövdesinden geçerli ayar sözlüğü; geçersizse ValueError."""
    mevcut = oku()
    govde = govde or {}
    try:
        baslangic = float(govde.get("baslangic", mevcut["baslangic"]))
        birim = float(govde.get("birim", mevcut["birim"]))
        bolen = float(govde.get("kelly_bolen", mevcut["kelly_bolen"]))
    except (TypeError, ValueError):
        raise ValueError("Başlangıç, birim ve Kelly böleni sayı olmalı.") from None
    if not (1 <= baslangic <= 10_000_000):
        raise ValueError("Başlangıç kasası 1 ile 10 milyon ₺ arasında olmalı.")
    if not (0 < birim <= baslangic):
        raise ValueError("Birim bahis 0'dan büyük ve başlangıç kasasından küçük olmalı.")
    if not (1 <= bolen <= 20):
        raise ValueError("Kelly böleni 1 ile 20 arasında olmalı (4 = çeyrek Kelly).")
    tarih = str(govde.get("baslangic_tarihi") or mevcut["baslangic_tarihi"]).strip()
    try:
        t = pd.to_datetime(tarih, dayfirst=True)
    except (ValueError, TypeError):
        raise ValueError("Başlangıç tarihi gg.aa.yyyy biçiminde olmalı.") from None
    if pd.isna(t) or t > veri.simdi_tr().normalize() + pd.Timedelta(days=1):
        raise ValueError("Başlangıç tarihi gelecekte olamaz.")
    return {"baslangic": round(baslangic, 2), "birim": round(birim, 2), "kelly_bolen": round(bolen, 2),
            "baslangic_tarihi": t.strftime("%d.%m.%Y")}


def yaz(govde: dict) -> dict:
    ayar = dogrula(govde)
    ayar["guncelleme"] = veri.simdi_tr().strftime("%d.%m.%Y %H:%M")
    with _KILIT:
        os.makedirs(os.path.dirname(DOSYA), exist_ok=True)
        gecici = DOSYA + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(ayar, f, ensure_ascii=False, indent=1)
        os.replace(gecici, DOSYA)
    return oku()


def _zaman(k: dict) -> pd.Timestamp:
    """Kuponun sonuç zamanı: en geç bacağın maç saati; yoksa oluşturma anı."""
    en_gec = None
    for b in k.get("secimler") or []:
        try:
            t = pd.to_datetime(f"{b.get('tarih')} {b.get('saat') or '12:00'}", dayfirst=True)
        except (ValueError, TypeError):
            continue
        if pd.isna(t):
            continue
        en_gec = t if en_gec is None or t > en_gec else en_gec
    if en_gec is not None:
        return en_gec
    try:
        t = pd.to_datetime(k.get("olusturma"), dayfirst=True)
        if not pd.isna(t):
            return t
    except (ValueError, TypeError):
        pass
    return pd.Timestamp(veri.simdi_tr())


_AILELER = (("İY/MS", "İY/MS"), ("İY ", "İlk yarı"), ("2Y", "2. yarı"), ("MS", "Maç sonucu"), ("ÇŞ", "Çifte şans"),
            ("ÜST", "Alt/Üst"), ("ALT", "Alt/Üst"), ("KG", "KG"), ("HND", "Handikap"), ("EV ", "Takım golü"),
            ("DEP ", "Takım golü"), ("KORNER", "Korner"), ("KART", "Kart"), ("HER İKİ", "Yarılar"))


def pazar_ailesi(pazar: str) -> str:
    pz = str(pazar or "").strip()
    if " ve " in pz:
        return "Sonuç + Alt/Üst"
    for on_ek, ad in _AILELER:
        if pz.startswith(on_ek):
            return ad
    return pz or "—"


def performans(kuponlar: list[dict], ayarlar: dict, gun: int = 90, simdi=None) -> dict:
    """Saf hesap. kuponlar: kupon.degerlendir(k, birim) çıktıları (kar, yatirim, durum...).

    bakiye = başlangıç + başlangıç tarihinden beri GERÇEKLEŞEN kârlar (pencereden
    bağımsız). Eğri, haftalık, kırılım ve özetin geri kalanı `gun` penceresinde
    (0 = hepsi). Drawdown gerçekleşen bakiyenin zirveden düşüşüdür."""
    simdi = (pd.to_datetime(simdi, dayfirst=True) if isinstance(simdi, str)
             else pd.Timestamp(simdi or veri.simdi_tr()))
    baslangic = float(ayarlar.get("baslangic") or 0.0)
    try:
        t0 = pd.to_datetime(ayarlar.get("baslangic_tarihi"), dayfirst=True).normalize()
    except (ValueError, TypeError):
        t0 = None
    if t0 is not None and pd.isna(t0):
        t0 = None
    kayitlar = []
    for k in kuponlar or []:
        t = _zaman(k)
        if t0 is not None and t < t0:
            continue                          # kasa başlangıcından önceki kuponlar sayılmaz
        kayitlar.append((t, k))
    kayitlar.sort(key=lambda x: x[0])
    sonuclu_hepsi = [(t, k) for t, k in kayitlar
                     if k.get("durum") in ("tuttu", "yatti") and k.get("kar") is not None]
    bakiye = baslangic + sum(float(k["kar"]) for _t, k in sonuclu_hepsi)
    acik = [k for _t, k in kayitlar if k.get("durum") == "bekliyor"]
    acik_risk = sum(float(k.get("yatirim") or 0.0) for k in acik)

    gun = int(gun or 0)
    p0 = (simdi.normalize() - pd.Timedelta(days=gun)) if gun > 0 else None
    if p0 is not None and t0 is not None and p0 < t0:
        p0 = t0
    pencere = [(t, k) for t, k in sonuclu_hepsi if p0 is None or t >= p0]
    once = sum(float(k["kar"]) for t, k in sonuclu_hepsi if p0 is not None and t < p0)
    b = baslangic + once
    ilk = p0 if p0 is not None else (t0 if t0 is not None else (pencere[0][0] if pencere else simdi))
    egri = [{"t": pd.Timestamp(ilk).strftime("%d.%m.%Y"), "bakiye": round(b, 2), "kar_kumule": round(once, 2)}]
    gunluk: dict = defaultdict(float)
    for t, k in pencere:
        gunluk[t.normalize()] += float(k["kar"])
    tepe, max_dd, max_dd_yuzde, kum = b, 0.0, 0.0, once
    for g in sorted(gunluk):
        b += gunluk[g]
        kum += gunluk[g]
        egri.append({"t": g.strftime("%d.%m.%Y"), "bakiye": round(b, 2), "kar_kumule": round(kum, 2)})
        if b > tepe:
            tepe = b
        dd = tepe - b
        if dd > max_dd:
            max_dd = dd
            max_dd_yuzde = dd / tepe if tepe > 0 else 0.0

    kar = sum(float(k["kar"]) for _t, k in pencere)
    yatirim = sum(float(k.get("yatirim") or 0.0) for _t, k in pencere)
    tutan = sum(1 for _t, k in pencere if k["durum"] == "tuttu")
    seri = en_uzun = 0
    for _t, k in pencere:
        seri = seri + 1 if k["durum"] == "yatti" else 0
        en_uzun = max(en_uzun, seri)
    pencere_kuponlar = [k for t, k in kayitlar if p0 is None or t >= p0]
    clv = [float(bk["clv"]) for k in pencere_kuponlar for bk in k.get("secimler") or []
           if isinstance(bk.get("clv"), (int, float))]
    ozet = {
        "bakiye": round(bakiye, 2), "baslangic": baslangic, "kar": round(kar, 2), "yatirim": round(yatirim, 2),
        "roi": (kar / yatirim) if yatirim > 0 else None, "sonuclu": len(pencere), "tutan": tutan,
        "yatan": len(pencere) - tutan, "bekleyen": len(acik), "acik_risk": round(acik_risk, 2),
        "max_dd": round(max_dd, 2), "max_dd_yuzde": round(max_dd_yuzde, 4),
        "clv_ort": (sum(clv) / len(clv)) if clv else None, "clv_n": len(clv),
        "clv_yenen": (sum(1 for c in clv if c > 0) / len(clv)) if clv else None,
        "en_uzun_kayip": en_uzun, "miktar_tahmini": sum(1 for _t, k in pencere if k.get("miktar_tahmini")),
        "toplam_kupon": len(kayitlar), "gun": gun, "pencere_baslangic": egri[0]["t"],
    }

    hafta: dict = defaultdict(lambda: {"kar": 0.0, "yatirim": 0.0, "n": 0, "tutan": 0})
    for t, k in pencere:
        h = t.normalize() - pd.Timedelta(days=int(t.weekday()))     # pazartesi
        r = hafta[h]
        r["kar"] += float(k["kar"])
        r["yatirim"] += float(k.get("yatirim") or 0.0)
        r["n"] += 1
        r["tutan"] += 1 if k["durum"] == "tuttu" else 0
    haftalik = [{"hafta": h.strftime("%d.%m"), "kar": round(v["kar"], 2), "yatirim": round(v["yatirim"], 2),
                 "n": v["n"], "tutan": v["tutan"]} for h, v in sorted(hafta.items())]

    def _kirilim(anahtar: str) -> list[dict]:
        grup: dict = defaultdict(lambda: {"n": 0, "tutan": 0, "yatan": 0, "kar_payi": 0.0, "yatirim_payi": 0.0})
        for _t, k in pencere:
            bacaklar = k.get("secimler") or []
            n = len(bacaklar) or 1
            for bk in bacaklar:
                ad = str(bk.get(anahtar) or "—")
                if anahtar == "pazar":
                    ad = pazar_ailesi(ad)
                g = grup[ad]
                g["n"] += 1
                if bk.get("durum") == "tuttu":
                    g["tutan"] += 1
                elif bk.get("durum") == "yatti":
                    g["yatan"] += 1
                g["kar_payi"] += float(k["kar"]) / n
                g["yatirim_payi"] += float(k.get("yatirim") or 0.0) / n
        cikti = []
        for ad, g in grup.items():
            sonuclu = g["tutan"] + g["yatan"]
            cikti.append({"ad": ad, "n": g["n"], "tutan": g["tutan"], "yatan": g["yatan"],
                          "kar_payi": round(g["kar_payi"], 2), "yatirim_payi": round(g["yatirim_payi"], 2),
                          "isabet": (g["tutan"] / sonuclu) if sonuclu else None,
                          "roi": (g["kar_payi"] / g["yatirim_payi"]) if g["yatirim_payi"] > 0 else None})
        return sorted(cikti, key=lambda x: (-x["n"], x["ad"]))

    kirilim = {"pazar": _kirilim("pazar"), "lig": _kirilim("lig"), "kaynak": _kirilim("kaynak")}
    sis = [bk for k in pencere_kuponlar for bk in k.get("secimler") or [] if isinstance(bk.get("p"), (int, float))]
    sis_sonuclu = [bk for bk in sis if bk.get("durum") in ("tuttu", "yatti")]
    sistem = None
    if sis:
        tutan_s = sum(1 for bk in sis_sonuclu if bk["durum"] == "tuttu")
        sistem = {"n": len(sis), "sonuclu": len(sis_sonuclu), "tutan": tutan_s,
                  "dedi": (sum(float(bk["p"]) for bk in sis_sonuclu) / len(sis_sonuclu)) if sis_sonuclu else None,
                  "gercek": (tutan_s / len(sis_sonuclu)) if sis_sonuclu else None}
    return {"ozet": ozet, "egri": egri, "haftalik": haftalik, "kirilim": kirilim, "sistem": sistem,
            "kelly": {"bolen": float(ayarlar.get("kelly_bolen") or 4.0), "not": KELLY_NOT}}


def kelly_oneri(p: float, oran: float, bakiye: float, bolen: float = 4.0) -> dict:
    """Kesirli Kelly tutarı (rolling.kelly_kesri sarmalı). Kenar yoksa oynama."""
    p, oran, bakiye, bolen = float(p), float(oran), float(bakiye), max(1.0, float(bolen))
    kesir = rolling.kelly_kesri(p, oran, bolen)
    tam = rolling.kelly_kesri(p, oran, 1.0)
    miktar = float(round(max(0.0, bakiye) * kesir))
    return {"kesir": round(kesir, 4), "tam_kelly": round(tam, 4), "miktar": miktar, "bolen": bolen,
            "kenar": round(p * oran - 1.0, 4), "oynama": kesir <= 0.0,
            "not": ("kenar yok (p·o ≤ 1): Kelly \"oynama\" diyor" if kesir <= 0.0
                    else f"1/{bolen:g} Kelly: bakiyenin %{kesir*100:.1f}'i — kazandırmaz, batmayı önler")}
