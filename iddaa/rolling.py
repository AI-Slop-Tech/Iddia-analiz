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


MAKS_BACAK = 8


def _oku() -> list[dict]:
    try:
        with open(ROLLING_DOSYASI, encoding="utf-8") as f:
            planlar = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    # Göç: eski düz adımlar (tek maç alanları adımın kendisinde) tek bacaklı
    # kombine biçimine çevrilir — kod tek şekil üzerinde çalışır, ilk yazmada
    # dosya da bu biçime oturur. İdempotent.
    for plan in planlar:
        for a in plan.get("adimlar", []):
            if "bacaklar" not in a:
                bacak = {k: a.pop(k) for k in ("tarih", "saat", "lig", "ev", "dep",
                                               "pazar", "oran", "durum", "elle", "skor")
                         if k in a}
                a["bacaklar"] = [bacak]
    return planlar


def _adim_durumu(a: dict) -> str:
    """Kombine kuralı: bir bacak yattıysa adım yatar; hepsi tuttuysa tutar."""
    durumlar = [b.get("durum", "bekliyor") for b in a.get("bacaklar", [])]
    if "yatti" in durumlar:
        return "yatti"
    if durumlar and all(d == "tuttu" for d in durumlar):
        return "tuttu"
    return "bekliyor"


def _adim_orani(a: dict) -> float:
    carpim = 1.0
    for b in a.get("bacaklar", []):
        carpim *= float(b.get("oran", 1.0))
    return carpim


def _bacak_dogrula(secim: dict) -> dict:
    oran = float(secim.get("oran", 0))
    if not (1.01 <= oran <= 1000):
        raise ValueError("Geçerli bir oran girin (1.01 - 1000).")
    return {
        "tarih": str(secim.get("tarih", ""))[:10],
        "saat": str(secim.get("saat", ""))[:5],
        "lig": str(secim.get("lig", ""))[:8],
        "ev": str(secim.get("ev", ""))[:60],
        "dep": str(secim.get("dep", ""))[:60],
        "pazar": str(secim.get("pazar", ""))[:24],
        "oran": oran,
        "durum": "bekliyor",
        "elle": False,
    }


def _yaz(planlar: list[dict]) -> None:
    os.makedirs(veri.VERI_KLASORU, exist_ok=True)
    gecici = ROLLING_DOSYASI + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(planlar, f, ensure_ascii=False)
    os.replace(gecici, ROLLING_DOSYASI)


def kelly_kesri(p: float, oran: float, bolen: float = 4.0) -> float:
    """Kesirli Kelly: bakiyenin hangi payı yatırılmalı.

    Tam Kelly f = (p·o − 1)/(o − 1) uzun vadede bakiyeyi en hızlı büyütür ama
    varyansı yaşanmazdır; profesyoneller çeyrek Kelly (bolen=4) kullanır.
    Kenar yoksa (p·o ≤ 1) sonuç 0 — yani OYNAMA. Ölçülmüş olasılıklar dürüst
    olduğu için bu formülün girdisi hazır; formül kazandırmaz, BATMAYI önler:
    tüm bakiyeyi basan zincirde tek yatış her şeyi götürür, kesirli Kelly'de
    bakiye hiç sıfırlanmaz.
    """
    p, oran = float(p), float(oran)
    if oran <= 1.0 or not (0.0 < p < 1.0):
        return 0.0
    f = (p * oran - 1.0) / (oran - 1.0)
    return max(0.0, min(1.0, f / max(1.0, bolen)))


def olustur(ad: str, baslangic: float, hedef_oran: float = 2.0,
            hedef_gun: int = 15, kesir: float = 1.0) -> dict:
    baslangic = float(baslangic)
    if not (1 <= baslangic <= 10_000_000):
        raise ValueError("Başlangıç kasası 1 ile 10 milyon TL arasında olmalı.")
    plan = {
        "id": int(time.time() * 1000),
        "ad": str(ad or "Rolling")[:40],
        "baslangic": baslangic,
        "hedef_oran": max(1.01, min(100.0, float(hedef_oran or 2.0))),
        "kesir": max(0.01, min(1.0, float(kesir or 1.0))),   # bakiyenin yatırılan payı; 1.0 = tümü
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
    """Yeni adım açar. secim tek bacak alanları YA DA {"bacaklar": [...]}."""
    ham = secim.get("bacaklar") if isinstance(secim.get("bacaklar"), list) else [secim]
    if not (1 <= len(ham) <= MAKS_BACAK):
        raise ValueError(f"Bir adım 1-{MAKS_BACAK} bacak içerebilir.")
    bacaklar = [_bacak_dogrula(b) for b in ham]
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if len(p["adimlar"]) >= MAKS_ADIM:
            raise ValueError(f"Plan en fazla {MAKS_ADIM} adım içerebilir.")
        if any(_adim_durumu(a) == "yatti" for a in p["adimlar"]):
            raise ValueError("Bu seri bitti (bir adım yattı) — yeni plan açın.")
        p["adimlar"].append({"bacaklar": bacaklar})
        _yaz(planlar)
        return p
    raise ValueError("Plan bulunamadı.")


def bacak_ekle(plan_id: int, adim_indeks: int, secim: dict) -> dict:
    """Var olan BEKLEYEN adıma bacak ekler — kombineyi tek tek yazma akışı."""
    bacak = _bacak_dogrula(secim)
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if not (0 <= adim_indeks < len(p["adimlar"])):
            raise ValueError("Adım bulunamadı.")
        a = p["adimlar"][adim_indeks]
        if _adim_durumu(a) != "bekliyor":
            raise ValueError("Sonuçlanmış adıma bacak eklenemez.")
        if len(a["bacaklar"]) >= MAKS_BACAK:
            raise ValueError(f"Bir adım en fazla {MAKS_BACAK} bacak içerebilir.")
        a["bacaklar"].append(bacak)
        _yaz(planlar)
        return p
    raise ValueError("Plan bulunamadı.")


def bacak_sil(plan_id: int, adim_indeks: int, bacak_indeks: int) -> bool:
    """Bekleyen bacak silinir; adımın son bacağı silinirse adım da silinir
    (yalnız son adımda — zincir bozulmasın)."""
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if not (0 <= adim_indeks < len(p["adimlar"])):
            return False
        a = p["adimlar"][adim_indeks]
        if not (0 <= bacak_indeks < len(a["bacaklar"])):
            return False
        if a["bacaklar"][bacak_indeks].get("durum") != "bekliyor":
            raise ValueError("Sonuçlanmış bacak silinemez — önce ↺ ile geri alın.")
        if len(a["bacaklar"]) == 1:
            if adim_indeks != len(p["adimlar"]) - 1:
                raise ValueError("Ortadaki adım silinemez (zincir bozulur).")
            p["adimlar"].pop()
        else:
            a["bacaklar"].pop(bacak_indeks)
        _yaz(planlar)
        return True
    return False


def adim_duzenle(plan_id: int, indeks: int, alanlar: dict) -> bool:
    """Yalnız 'bekliyor' bacakta oran/maç bilgisi değiştirilebilir.
    alanlar["bacak"] hedef bacağı seçer (varsayılan 0)."""
    planlar = _oku()
    for p in planlar:
        if p.get("id") != plan_id:
            continue
        if not (0 <= indeks < len(p["adimlar"])):
            return False
        a = p["adimlar"][indeks]
        bi = int(alanlar.get("bacak", 0))
        if not (0 <= bi < len(a["bacaklar"])):
            return False
        b = a["bacaklar"][bi]
        if b["durum"] != "bekliyor" and "oran" in alanlar:
            raise ValueError("Sonuçlanmış bacağın oranı değiştirilemez.")
        if "oran" in alanlar:
            o = float(alanlar["oran"])
            if not (1.01 <= o <= 1000):
                raise ValueError("Geçerli bir oran girin.")
            b["oran"] = o
        for k in ("tarih", "ev", "dep", "pazar", "lig", "saat"):
            if k in alanlar:
                b[k] = str(alanlar[k])[: 60 if k in ("ev", "dep") else 24]
        _yaz(planlar)
        return True
    return False


def elle_isaretle(plan_id: int, indeks: int, durum: str, bacak: int = 0) -> bool:
    """Tek BACAĞI işaretler; adımın durumu bacaklarından türetilir."""
    if durum not in ("tuttu", "yatti", "bekliyor"):
        raise ValueError("Durum tuttu/yatti/bekliyor olmalı.")
    planlar = _oku()
    for p in planlar:
        if p.get("id") == plan_id and 0 <= indeks < len(p["adimlar"]):
            a = p["adimlar"][indeks]
            if not (0 <= bacak < len(a["bacaklar"])):
                return False
            a["bacaklar"][bacak]["durum"] = durum
            a["bacaklar"][bacak]["elle"] = durum != "bekliyor"
            _yaz(planlar)
            return True
    return False


def adim_sil(plan_id: int, indeks: int) -> bool:
    """Zincir bozulmasın diye yalnız SON ve tamamen BEKLEYEN adım silinebilir."""
    planlar = _oku()
    for p in planlar:
        if p.get("id") == plan_id and p["adimlar"] and indeks == len(p["adimlar"]) - 1:
            if _adim_durumu(p["adimlar"][indeks]) != "bekliyor" or any(
                    b["durum"] != "bekliyor" for b in p["adimlar"][indeks]["bacaklar"]):
                raise ValueError("Sonuçlanmış bacağı olan adım silinemez — önce ↺ ile geri alın.")
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
            for b in a["bacaklar"]:
                if b["durum"] != "bekliyor" or b.get("elle") or not b.get("ev"):
                    continue
                try:
                    t = pd.to_datetime(b["tarih"], dayfirst=True)
                except (ValueError, TypeError):
                    continue
                if t >= simdi.normalize():
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
                if sonuc != "belirsiz":
                    b["durum"] = sonuc
                degisti = True
    if degisti:
        _yaz(planlar)
    return planlar


def hesapla(plan: dict) -> dict:
    """Bakiye zinciri. kesir=1.0 → her adımda bakiyenin TAMAMI (eski davranış);
    kesir<1.0 → yalnız o pay yatırılır, yatınca bakiye sıfırlanmaz, azalır."""
    bakiye = float(plan["baslangic"])
    kesir = max(0.01, min(1.0, float(plan.get("kesir", 1.0) or 1.0)))
    adimlar = []
    plan_durum = "aktif"
    for a in plan["adimlar"]:
        durum = _adim_durumu(a)
        oran = _adim_orani(a)
        yatirim = round(bakiye * kesir, 2)
        if durum == "tuttu":
            bakiye = bakiye + yatirim * (oran - 1.0)
            sonra = round(bakiye, 2)
        elif durum == "yatti":
            bakiye = round(bakiye - yatirim, 2)
            sonra = bakiye
            if kesir >= 0.999 or bakiye <= 0.0:
                plan_durum = "yatti"
        else:
            sonra = None  # bekliyor: zincir burada duruyor
        adimlar.append({"bacaklar": a["bacaklar"], "durum": durum,
                        "oran": round(oran, 2), "yatirim": yatirim, "bakiye": sonra})
        if plan_durum == "yatti":
            # seri bitti; kalan adımlar (varsa) gösterilir ama yatırım 0
            bakiye = 0.0
    tamam = sum(1 for a in plan["adimlar"] if _adim_durumu(a) == "tuttu")
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
