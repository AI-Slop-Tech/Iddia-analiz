"""Sistemin kendi ileriye dönük defteri — otomatik forward karne.

NEDEN: Kullanıcı "sistem gerçekte ne tutturuyor?" diye soruyor; elimizdeki bütün
karneler geçmişe dönük ölçümdü (eğitim/test ayrımı, holdout). Bu modül tersini
yapar: sistem her gün KENDİ kuponlarını (hedef 2.00, 2x kâr bölgesi, hedefsiz
1-2 bacak, Sürpriz Radarı işaretlileri) maçlar başlamadan yazar, canlı besleme
ve arşivle sonuçlar, CLV'sini ölçer. Kullanıcının fişlemesine gerek kalmaz.

DÜRÜSTLÜK KURALLARI
  • Kayıt yalnız kickoff'tan ÖNCE yazılır (başlamış maç adaya girmez); geçmiş
    güne asla yazılmaz — hindsight yok. Sunucu kapalıysa gün boş kalır (dürüst
    boşluk), sonradan doldurulmaz.
  • Fiyatı tahmin edilen bacak (gerçek piyasa fiyatı yok) ayrı işaretlenir;
    "roi_gercek" yalnız tüm bacakları gerçek fiyatlı kuponlardan hesaplanır,
    "roi_tahmini" ayrı ve etiketli gösterilir.
  • n küçükken (< 30 sonuçlu kupon) yargı yok; ekran bunu söyler.
  • Aynı gün + mod bir kez yazılır (idempotens); 16:00 slotu yalnız 10:00'da
    kupon çıkmayan (taslak) modları yeniden dener.

Dosya: data/sistem_defteri.json — kupon.py'nin defter biçiminin üst kümesi;
sonuçlandırma kupon.sonuclandir(df, dosya=DOSYA) ile aynı makineden geçer.
"""

from __future__ import annotations

import math
import os
import time

import pandas as pd

from . import kupon, oneri, sistem, veri

DOSYA = os.path.join(veri.VERI_KLASORU, "sistem_defteri.json")
SAATLER = (10, 16)          # TR saati: ilk yazım ≥10:00, taslak yeniden denemesi ≥16:00
EN_FAZLA_KAYIT = 3000
SURPRIZ_ADET = 3

# Üretim varsayılanlarıyla birebir (sisModSec: hedef→%60, kazanç→%65; HEDEF_OTOMATIK_BACAK=4).
MODLAR: dict[str, dict] = {
    "hedef200": {"ad": "Hedef 2.00 — en yüksek tutma şansı",
                 "ayarlar": {"oncelik": "oran", "hedef": 2.0, "esik": 0.60, "maks_bacak": sistem.HEDEF_OTOMATIK_BACAK,
                             "marj": sistem.MARJ_VARSAYILAN, "kapsam": "yaygin", "sans_bacak": 2}},
    "kazanc":   {"ad": "2x kâr bölgesi (1.20–1.60 gerçek fiyat)",
                 "ayarlar": {"oncelik": "kazanc", "hedef": 2.0, "esik": 0.65, "maks_bacak": 3,
                             "marj": sistem.MARJ_VARSAYILAN, "kapsam": "yaygin", "sans_bacak": 2}},
    "sans1":    {"ad": "Hedefsiz en garanti — 1 bacak",
                 "ayarlar": {"oncelik": "sans", "hedef": 2.0, "esik": 0.60, "maks_bacak": 3,
                             "marj": sistem.MARJ_VARSAYILAN, "kapsam": "yaygin", "sans_bacak": 1}},
    "sans2":    {"ad": "Hedefsiz en garanti — 2 bacak",
                 "ayarlar": {"oncelik": "sans", "hedef": 2.0, "esik": 0.60, "maks_bacak": 3,
                             "marj": sistem.MARJ_VARSAYILAN, "kapsam": "yaygin", "sans_bacak": 2}},
    "surpriz":  {"ad": "Sürpriz Radarı — işaretli İY/MS", "adet": SURPRIZ_ADET},
}
_SON_CALISMA: dict = {"zaman": None, "sonuc": None}


def _gun_metni(t) -> str:
    return pd.Timestamp(t).strftime("%d.%m.%Y")


def _bacak_kaydi(b: dict, gun: str) -> dict:
    """Sistem Önerisi bacağını defter bacağına çevirir (kupon.py alanları + ölçüm izi)."""
    gercek = b.get("oran")
    oran = float(gercek) if gercek else float(b.get("site_oran") or b.get("adil") or 1.01)
    return {
        "tarih": gun, "saat": str(b.get("saat") or "")[:5], "lig": str(b.get("lig") or "")[:40],
        "ev": str(b.get("ev_ad") or "")[:60], "dep": str(b.get("dep_ad") or "")[:60],
        "pazar": str(b.get("pazar") or "")[:24],
        "oran": round(max(1.01, oran), 3),
        "oran_kaynak": "piyasa" if gercek else "tahmin",
        "fiyat_tahmini": not bool(gercek),
        "p": float(b.get("p")) if b.get("p") is not None else None,
        "model_p": float(b.get("model_p")) if b.get("model_p") is not None else None,
        "keskin_adil": float(b["keskin_adil"]) if b.get("keskin_adil") else None,
        "guven_kaynak": b.get("guven_kaynak"),
        "kaynak": "sistem", "durum": "bekliyor", "elle": False,
    }


def _kimlik() -> int:
    return int(time.time() * 1000)


def _mevcut(defter: list[dict], gun: str, mod: str) -> dict | None:
    for k in defter:
        if k.get("gun") == gun and k.get("mod") == mod:
            return k
    return None


def _sil(defter: list[dict], gun: str, mod: str) -> None:
    defter[:] = [k for k in defter if not (k.get("gun") == gun and k.get("mod") == mod)]


def isle(df, fik, elo, simdi=None, zorla: bool = False, mod: str | None = None,
         gun: str | None = None, fikstur_surum: int | None = None) -> dict:
    """Günün sistem kuponlarını yazar. Otomatik çağrı: yalnız BUGÜN ve saat slotları;
    elle çağrı (zorla): bugün ya da gelecek bir gün, her saat.

    Dönen: {"gun", "slot", "yazildi": [...], "atlandi": [...], "kupon_yok": [...]}."""
    simdi = simdi or veri.simdi_tr()
    bugun = _gun_metni(simdi)
    gun = gun or bugun
    try:
        gun_ts = pd.to_datetime(gun, dayfirst=True).normalize()
    except (ValueError, TypeError):
        raise ValueError("Geçersiz tarih.")
    if gun_ts < simdi.normalize():
        raise ValueError("Geçmiş güne kayıt yazılmaz (hindsight yok).")
    saat = int(simdi.hour)
    slot = 16 if saat >= SAATLER[1] else (10 if saat >= SAATLER[0] else None)
    sonuc = {"gun": gun, "slot": slot if not zorla else saat, "yazildi": [], "atlandi": [], "kupon_yok": []}
    if slot is None and not zorla:
        sonuc["atlandi"].append({"mod": "*", "neden": f"saat {SAATLER[0]}:00'dan önce"})
        return sonuc
    istenen = [m for m in MODLAR if (mod in (None, "hepsi") or m == mod)]
    with kupon._kilit(DOSYA):
        defter = kupon._oku(DOSYA)
        yapilacak = []
        for m in istenen:
            var = _mevcut(defter, gun, m)
            if var and not var.get("kupon_yok"):
                sonuc["atlandi"].append({"mod": m, "neden": "zaten var", "id": var.get("id")})
                continue
            if var and var.get("kupon_yok") and slot == 10 and not zorla:
                sonuc["atlandi"].append({"mod": m, "neden": "taslak; 16:00'da yeniden denenir"})
                continue
            yapilacak.append(m)
        if not yapilacak:
            _SON_CALISMA.update({"zaman": time.time(), "sonuc": sonuc})
            return sonuc
        olusturma = simdi.strftime("%d.%m.%Y %H:%M")
        adaylar = None
        for m in yapilacak:
            if m == "surpriz":
                continue
            if adaylar is None:
                adaylar = oneri.gun_adaylari(df, fik, gun, elo, None, butce_sn=60.0, simdi=simdi)
            ayarlar = dict(MODLAR[m]["ayarlar"])
            hesap = oneri.sistem_onerisi_hesapla(df, fik, gun, elo, ayarlar, None, adaylar)
            kup = hesap.get("kupon")
            _sil(defter, gun, m)
            if not kup or not kup.get("bacaklar"):
                kayit = {"id": _kimlik(), "gun": gun, "mod": m, "slot": sonuc["slot"], "kupon_yok": True,
                         "neden": ("1.20–1.60 bandında gerçek fiyatlı aday yok" if m == "kazanc"
                                   else "bu ayarlarla kupon kurulamadı"),
                         "aday_sayisi": int(hesap.get("aday_sayisi") or 0), "mac_sayisi": int(hesap.get("mac_sayisi") or 0),
                         "olusturma": olusturma, "secimler": []}
                defter.insert(0, kayit)
                sonuc["kupon_yok"].append({"mod": m, "neden": kayit["neden"], "aday_sayisi": kayit["aday_sayisi"]})
                continue
            bacaklar = [_bacak_kaydi(b, gun) for b in kup["bacaklar"]]
            kayit = {
                "id": _kimlik(), "ad": f"Sistem · {MODLAR[m]['ad']} · {gun}", "gun": gun, "mod": m,
                "slot": sonuc["slot"], "olusturma": olusturma, "sistem": "kombine", "kaynak": "otomatik",
                "fikstur_surum": fikstur_surum, "ayarlar": ayarlar,
                "iddia": {"p": float(kup.get("p") or 0.0), "oran": float(kup.get("oran") or 0.0),
                          "ev": kup.get("ev"), "fiyatsiz": sum(1 for b in bacaklar if b["fiyat_tahmini"]),
                          "strateji": hesap.get("strateji")},
                "secimler": bacaklar,
            }
            defter.insert(0, kayit)
            sonuc["yazildi"].append({"mod": m, "id": kayit["id"], "bacak": len(bacaklar), "p": kayit["iddia"]["p"],
                                     "oran": kayit["iddia"]["oran"]})
            time.sleep(0.002)   # id (ms) çakışmasın
        if "surpriz" in yapilacak:
            try:
                satirlar = oneri.surpriz_radari_hesapla(df, fik, gun, butce_sn=25.0)
            except Exception:  # noqa: BLE001
                satirlar = []
            secilen = []
            for s in satirlar:
                if not s.get("isaretli"):
                    continue
                try:
                    baslama = pd.to_datetime(f"{gun} {s.get('saat') or '00:00'}", dayfirst=True)
                except (ValueError, TypeError):
                    continue
                if baslama <= simdi:
                    continue
                secilen.append(s)
                if len(secilen) >= MODLAR["surpriz"]["adet"]:
                    break
            _sil(defter, gun, "surpriz")
            if not secilen:
                kayit = {"id": _kimlik(), "gun": gun, "mod": "surpriz", "slot": sonuc["slot"], "kupon_yok": True,
                         "neden": "bugün işaretli (kanıtlı) İY/MS seçimi yok", "aday_sayisi": len(satirlar),
                         "olusturma": olusturma, "secimler": []}
                defter.insert(0, kayit)
                sonuc["kupon_yok"].append({"mod": "surpriz", "neden": kayit["neden"], "aday_sayisi": len(satirlar)})
            else:
                bacaklar = []
                for s in secilen:
                    k = s["isaretli"]
                    kombo = s["kombolar"][k]
                    piyasa = kombo.get("piyasa")
                    bacaklar.append({
                        "tarih": gun, "saat": str(s.get("saat") or "")[:5], "lig": str(s.get("lig") or "")[:40],
                        "ev": str(s["ev"])[:60], "dep": str(s["dep"])[:60], "pazar": f"İY/MS {k}",
                        "oran": round(float(piyasa) if piyasa else float(kombo.get("adil_oran") or 1.01), 3),
                        "oran_kaynak": "piyasa" if piyasa else "tahmin", "fiyat_tahmini": not bool(piyasa),
                        "p": float(kombo["p"]) if kombo.get("p") is not None else None,
                        "kanit": s.get("kanit"), "kaynak": "surpriz", "durum": "bekliyor", "elle": False,
                    })
                # her işaretli seçim TEK başına oynanır (radar tekli öneridir): "1/n sistem" değil,
                # ayrı tekli kuponlar — kâr/zarar bacak başına ölçülsün diye tek kayıtta 'tekli' sistemi
                kayit = {
                    "id": _kimlik(), "ad": f"Sistem · {MODLAR['surpriz']['ad']} · {gun}", "gun": gun, "mod": "surpriz",
                    "slot": sonuc["slot"], "olusturma": olusturma, "sistem": "tekli", "kaynak": "otomatik",
                    "fikstur_surum": fikstur_surum, "ayarlar": {"adet": MODLAR["surpriz"]["adet"]},
                    "iddia": {"p": None, "oran": None, "ev": None,
                              "fiyatsiz": sum(1 for b in bacaklar if b["fiyat_tahmini"]), "strateji": None},
                    "secimler": bacaklar,
                }
                defter.insert(0, kayit)
                sonuc["yazildi"].append({"mod": "surpriz", "id": kayit["id"], "bacak": len(bacaklar)})
        defter.sort(key=lambda k: (k.get("gun", "")[6:10] + k.get("gun", "")[3:5] + k.get("gun", "")[0:2], k.get("id", 0)), reverse=True)
        kupon._yaz(defter[:EN_FAZLA_KAYIT], DOSYA)
    _SON_CALISMA.update({"zaman": time.time(), "sonuc": sonuc})
    return sonuc


def sonuclandir(df):
    """Bekleyen bacakları canlı/arşivle sonuçlandırır (kupon.py makinesi), defteri döndürür."""
    return kupon.sonuclandir(df, dosya=DOSYA)


def degerlendir(k: dict) -> dict:
    """Kupon değerlendirmesi; 'tekli' sistem = her bacak ayrı bahis."""
    if k.get("kupon_yok"):
        return dict(k)
    if k.get("sistem") == "tekli":
        bacaklar = k["secimler"]
        durumlar = [b["durum"] for b in bacaklar]
        bekleyen = durumlar.count("bekliyor")
        tutan = durumlar.count("tuttu")
        net = None
        if not bekleyen:
            net = sum((b["oran"] - 1.0) if b["durum"] == "tuttu" else -1.0 for b in bacaklar)
        durum = "bekliyor" if bekleyen else ("tuttu" if net is not None and net > 0 else "yatti")
        return {**k, "toplam_oran": None, "maliyet": len(bacaklar), "durum": durum, "net": net,
                "tutan": tutan, "bekleyen": bekleyen}
    return kupon.degerlendir(k)


def _z(tutan: int, p_listesi: list[float]) -> float | None:
    if not p_listesi:
        return None
    sd = math.sqrt(sum(p * (1 - p) for p in p_listesi))
    return round((tutan - sum(p_listesi)) / sd, 2) if sd > 0 else None


def karne(defter: list[dict]) -> dict:
    """Mod başına ileriye dönük karne. Yalnız sonuçlanmış kuponlar sayılır."""
    cikti: dict = {}
    for m in MODLAR:
        kayitlar = [k for k in defter if k.get("mod") == m]
        gercekler = [degerlendir(k) for k in kayitlar if not k.get("kupon_yok")]
        taslaklar = [k for k in kayitlar if k.get("kupon_yok")]
        sonuclu = [k for k in gercekler if k["durum"] in ("tuttu", "yatti")]
        tutan = sum(1 for k in sonuclu if k["durum"] == "tuttu")
        if m == "surpriz":
            # tekli: bacak bazında say
            bacaklar = [b for k in gercekler for b in k["secimler"]]
            sonuclu_b = [b for b in bacaklar if b["durum"] in ("tuttu", "yatti")]
            tutan_b = sum(1 for b in sonuclu_b if b["durum"] == "tuttu")
            p_l = [b["p"] for b in sonuclu_b if b.get("p") is not None]
            gercek_f = [b for b in sonuclu_b if not b.get("fiyat_tahmini")]
            cikti[m] = {
                "ad": MODLAR[m]["ad"], "n": len(bacaklar), "sonuclu": len(sonuclu_b), "tutan": tutan_b,
                "bekleyen": len(bacaklar) - len(sonuclu_b),
                "dedi": round(sum(p_l) / len(p_l), 4) if p_l else None,
                "gercek": round(tutan_b / len(sonuclu_b), 4) if sonuclu_b else None,
                "z": _z(sum(1 for b in sonuclu_b if b.get("p") is not None and b["durum"] == "tuttu"), p_l),
                "roi_gercek": (round(sum((b["oran"] - 1) if b["durum"] == "tuttu" else -1 for b in gercek_f) / len(gercek_f), 4)
                               if gercek_f else None),
                "roi_gercek_n": len(gercek_f),
                "roi_tahmini": (round(sum((b["oran"] - 1) if b["durum"] == "tuttu" else -1 for b in sonuclu_b) / len(sonuclu_b), 4)
                                if sonuclu_b else None),
                "kupon_yok_gun": len({k["gun"] for k in taslaklar}),
                "gun_sayisi": len({k["gun"] for k in kayitlar}),
                "ilk_gun": min((k["gun"] for k in kayitlar), key=lambda g: g[6:10] + g[3:5] + g[0:2]) if kayitlar else None,
            }
            clv = [b["clv"] for b in bacaklar if isinstance(b.get("clv"), (int, float))]
        else:
            p_l = [float(k["iddia"]["p"]) for k in sonuclu if (k.get("iddia") or {}).get("p") is not None]
            gercek_f = [k for k in sonuclu if all(not b.get("fiyat_tahmini") for b in k["secimler"])]
            cikti[m] = {
                "ad": MODLAR[m]["ad"], "n": len(gercekler), "sonuclu": len(sonuclu), "tutan": tutan,
                "bekleyen": len(gercekler) - len(sonuclu),
                "dedi": round(sum(p_l) / len(p_l), 4) if p_l else None,
                "gercek": round(tutan / len(sonuclu), 4) if sonuclu else None,
                "z": _z(tutan, p_l) if len(p_l) == len(sonuclu) else None,
                "roi_gercek": (round(sum((k["toplam_oran"] - 1) if k["durum"] == "tuttu" else -1 for k in gercek_f) / len(gercek_f), 4)
                               if gercek_f else None),
                "roi_gercek_n": len(gercek_f),
                "roi_tahmini": (round(sum((k["toplam_oran"] - 1) if k["durum"] == "tuttu" else -1 for k in sonuclu) / len(sonuclu), 4)
                                if sonuclu else None),
                "kupon_yok_gun": len({k["gun"] for k in taslaklar}),
                "gun_sayisi": len({k["gun"] for k in kayitlar}),
                "ilk_gun": min((k["gun"] for k in kayitlar), key=lambda g: g[6:10] + g[3:5] + g[0:2]) if kayitlar else None,
                "ort_oran": round(sum(k["toplam_oran"] for k in gercekler) / len(gercekler), 2) if gercekler else None,
            }
            clv = [b["clv"] for k in gercekler for b in k["secimler"] if isinstance(b.get("clv"), (int, float))]
        cikti[m]["clv_n"] = len(clv)
        cikti[m]["clv_ort"] = round(sum(clv) / len(clv), 4) if clv else None
        cikti[m]["clv_yenen"] = round(sum(1 for c in clv if c > 0) / len(clv), 3) if clv else None
    return cikti


def son_calisma() -> dict:
    return {"zaman": (time.strftime("%d.%m.%Y %H:%M", time.localtime(_SON_CALISMA["zaman"]))
                      if _SON_CALISMA["zaman"] else None),
            "sonuc": _SON_CALISMA["sonuc"]}
