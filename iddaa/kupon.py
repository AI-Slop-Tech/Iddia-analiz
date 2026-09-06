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
import threading
import time
from itertools import combinations

import pandas as pd

from . import analiz, veri

KUPON_DOSYASI = os.path.join(veri.VERI_KLASORU, "kuponlar.json")
GECERLI_SISTEMLER = ("kombine",)  # + "k/n" biçimi (ör. "2/4") çalışma anında doğrulanır

# Defter dosyası parametreli: kullanıcının defteri (kuponlar.json) ile sistemin
# kendi otomatik defteri (sistem_defteri.json) aynı okuma/yazma/sonuçlandırma
# makinesini kullanır. Dosya başına kilit: bakım iş parçacığı ile istekler aynı
# dosyayı aynı anda yazmasın.
_KILITLER: dict[str, threading.Lock] = {}
_KILIT_KILIDI = threading.Lock()


def _kilit(dosya: str) -> threading.Lock:
    with _KILIT_KILIDI:
        return _KILITLER.setdefault(dosya, threading.Lock())


def _oku(dosya: str = KUPON_DOSYASI) -> list[dict]:
    try:
        with open(dosya, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _yaz(kuponlar: list[dict], dosya: str = KUPON_DOSYASI) -> None:
    os.makedirs(veri.VERI_KLASORU, exist_ok=True)
    gecici = dosya + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kuponlar, f, ensure_ascii=False)
    os.replace(gecici, dosya)


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
            # Sistem Önerisi'nden gelen seçimlerde modelin olasılığı: kullanıcının
            # kendi sonuçlarını beklentiyle kıyaslayan "sistem karnesi" bunu kullanır.
            "p": (float(s["p"]) if s.get("p") is not None and 0 < float(s["p"]) < 1 else None),
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


def sonuclandir(df: pd.DataFrame | None, dosya: str = KUPON_DOSYASI) -> list[dict]:
    """Açık bacakları arşivden/canlıdan sonuçlandırır, dosyaya işler, defteri döndürür."""
    with _kilit(dosya):
        kuponlar = _oku(dosya)
        if not kuponlar:
            return kuponlar
        if _sonuclandir_govde(df, kuponlar):
            _yaz(kuponlar, dosya)
        return kuponlar


def _sonuclandir_govde(df: pd.DataFrame | None, kuponlar: list[dict]) -> bool:
    """Defterdeki bekleyen bacakları yerinde sonuçlandırır; değişiklik olduysa True."""
    simdi = veri.simdi_tr()
    cozucu = veri.takim_cozucu(df, hizli=True) if df is not None else None
    degisti = False

    def _coz(ad: str) -> str:
        if cozucu is None:
            return str(ad)
        try:
            return cozucu(str(ad))
        except ValueError:
            return str(ad)

    # Canlı besleme: maç bittiği an (FT) skor/İY ile sonuçlandır — arşivin
    # işlenmesini beklemeden. Korner/kart gibi skordan çıkmayan pazarlar
    # "belirsiz" kalır, arşiv gelince işlenir. Uzatmalı biten maç (AET/AP)
    # otomatik işlenmez: besleme 120 dk skorunu verir, bahis 90 dk'ya bakar.
    canli_indeks: dict = {}

    def _canli(ev: str, dep: str, t: pd.Timestamp, saat_var: bool):
        gun = t.strftime("%Y-%m-%d")
        if gun not in canli_indeks:
            try:
                canli_indeks[gun] = veri.canli_indeksi(veri.canli_skorlar(gun))
            except Exception:  # noqa: BLE001
                canli_indeks[gun] = {}
        if not canli_indeks[gun]:
            return None
        return veri.canli_esle(canli_indeks[gun], ev, dep, t, cozucu=cozucu,
                               pencere_saat=3.0 if saat_var else 24.0)

    for k in kuponlar:
        for b in k["secimler"]:
            # CLV: aldığın fiyat vs Pinnacle kapanışı — durumdan bağımsız, bir kez
            if "clv" not in b and b.get("oran"):
                try:
                    # saat şart: kapanış eşlemesi başlama saatine 3 saat pencere uygular
                    t_clv = pd.to_datetime(f"{b['tarih']} {b.get('saat') or '12:00'}", dayfirst=True)
                    kap = veri.kapanis_fiyati(b["ev"], b["dep"], t_clv, b["pazar"])
                except Exception:  # noqa: BLE001
                    kap = None
                if kap:
                    b["kapanis"] = round(float(kap), 3)
                    b["clv"] = round(float(b["oran"]) / float(kap) - 1.0, 4)
                    degisti = True
            if b["durum"] != "bekliyor" or b.get("elle"):
                continue
            try:
                t = pd.to_datetime(b["tarih"], dayfirst=True)
            except (ValueError, TypeError):
                continue
            # Bugün/dün oynanan maç: önce canlı beslemeye bak (bittiyse hemen işle)
            if simdi.normalize() - pd.Timedelta(days=1) <= t <= simdi.normalize():
                saat = str(b.get("saat") or "")
                try:
                    t_canli = pd.to_datetime(f"{b['tarih']} {saat or '12:00'}", dayfirst=True)
                except (ValueError, TypeError):
                    t_canli = t + pd.Timedelta(hours=12)
                cs = _canli(b["ev"], b["dep"], t_canli, bool(saat))
                if cs and cs["durum"] == "bitti" and not cs.get("uzatma") \
                        and cs.get("ev_gol") is not None and cs.get("dep_gol") is not None:
                    satir = {"FTHG": cs["ev_gol"], "FTAG": cs["dep_gol"],
                             "HTHG": cs.get("iy_ev"), "HTAG": cs.get("iy_dep")}
                    sonuc = _pazar_sonucu(b["pazar"], satir)
                    if sonuc != "belirsiz":
                        b["durum"] = sonuc
                        b["skor"] = f"{cs['ev_gol']}-{cs['dep_gol']}"
                        if cs.get("iy_ev") is not None and cs.get("iy_dep") is not None:
                            b["iy_skor"] = f"{cs['iy_ev']}-{cs['iy_dep']}"
                        b["sonuc_kaynak"] = "canli"
                        degisti = True
                        continue
            if df is None or t >= simdi.normalize():  # maç günü geçmeden arşiv aranmaz
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

    return degisti


def acik_bacaklar(gunler: set[str] | None = None, dosya: str = KUPON_DOSYASI) -> list[dict]:
    """Bekleyen bacaklar (kupon_id, indeks ile): canlı şans/kesinleşme sorgusu için."""
    cikti = []
    for k in _oku(dosya):
        for i, b in enumerate(k.get("secimler") or []):
            if b.get("durum") != "bekliyor":
                continue
            if gunler and b.get("tarih") not in gunler:
                continue
            cikti.append({"kupon_id": k.get("id"), "indeks": i, **b})
    return cikti


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
