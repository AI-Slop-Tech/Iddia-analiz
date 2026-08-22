"""Analiz motoru.

Dört bağımsız sinyal üretir, sonra bunları tek bir öneride birleştirir:

1. Form        — iki takımın son maçları (genel + saha bazlı).
2. H2H         — iki takımın birbirine karşı geçmişi.
3. Poisson     — zaman ağırlıklı hücum/savunma güçlerinden gol beklentisi ve
                 skor olasılık matrisi (profesyonel modellemenin standart temeli).
4. Oran kalıbı — bugünkü oranlara benzer oranla açılmış geçmiş maçlarda
                 gerçekte ne olduğu (kullanıcının istediği "son 10 yılda aynı
                 oranlar" analizi).

Değer analizi: model olasılığı ile bahis oranının içerdiği olasılık
karşılaştırılır; pozitif beklenen değer (EV) taşıyan seçimler işaretlenir.
"""

from __future__ import annotations

import math
import weakref
from functools import lru_cache

import pandas as pd

YARI_OMUR_GUN = 540.0   # ~18 ay önceki maç yarı ağırlık taşır. Izgara testinde 180/365/540
                        # denendi; 540 hem Brier'i hem ROI eğrisini iyileştirdi (istikrarlı
                        # takım gücü > güncellik), 180 belirgin biçimde zarar yazdı
MAKS_GOL = 8            # skor matrisinin boyutu (0-8 gol)
MIN_MAC_UYARI = 8       # takım başına bu sayının altında maç varsa "sınırlı veri" uyarısı
DC_RHO = -0.06          # Dixon-Coles düşük skor düzeltmesi (0-0/1-1 payı artar, 1-0/0-1
                        # azalır). Izgara: 0/-0.06/-0.12 içinde Brier'i en çok -0.06 düzeltti;
                        # beraberlik kalibrasyonu %24.5→%25.9 (gerçek %26.2) — -0.12 aşırıydı


# ---------------------------------------------------------------- yardımcılar

def _agirlik(tarihler: pd.Series, referans: pd.Timestamp) -> pd.Series:
    gun = (referans - tarihler).dt.days.clip(lower=0)
    return 0.5 ** (gun / YARI_OMUR_GUN)


def _agirlikli_ort(degerler: pd.Series, agirliklar: pd.Series, varsayilan: float) -> float:
    toplam_w = float(agirliklar.sum())
    if toplam_w <= 0:
        return varsayilan
    return float((degerler * agirliklar).sum() / toplam_w)


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def dc_tau(i: int, j: int, lam_ev: float, lam_dep: float, rho: float) -> float:
    """Dixon-Coles (1997) düşük skor bağımlılık çarpanı; matris hücresine uygulanır."""
    if rho == 0.0 or i > 1 or j > 1:
        return 1.0
    if i == 0 and j == 0:
        return 1.0 - lam_ev * lam_dep * rho
    if i == 0 and j == 1:
        return 1.0 + lam_ev * rho
    if i == 1 and j == 0:
        return 1.0 + lam_dep * rho
    return 1.0 - rho  # 1-1


def adil_olasilik(oran_ev: float, oran_b: float, oran_dep: float) -> tuple[float, float, float]:
    """Bahis marjı arındırılmış (toplamı 1 olan) olasılık üçlüsü."""
    q = (1 / oran_ev, 1 / oran_b, 1 / oran_dep)
    s = sum(q)
    return q[0] / s, q[1] / s, q[2] / s


# ------------------------------------------------ konsensüs sapması sinyali
#
# Backtest kanıtı (7 sezon, 53.5 bin maç; kontrol gruplarıyla):
#   1X2  ≥+%4, oran 2.00-3.50, model vetosu ile → %47.7 tutma, +%26.5 ROI,
#        kapanışı %79 yenme. Aynı bantta sinyalsiz kontrol: -%2.7.
#   Ü/A  ≥+%3 → %56.0 tutma, +%11.0 ROI, kapanışı %94 yenme.
# Sinyalin mantığı "maçı daha iyi bilmek" DEĞİL: piyasanın kendi konsensüsüyle
# tek bir kitapçının fiyatı arasındaki ölçülebilir tutarsızlığı yakalamaktır.

KONSENSUS_ESIK = 0.04        # 1X2 için en iyi fiyatın adil fiyatı aşma payı
KONSENSUS_ESIK_UA = 0.02     # Üst/Alt (backtest: kapanışı %92 yenme, %50.5 tutma)
KONSENSUS_BANT = (2.00, 3.50)      # 1X2 oran bandı (dışı backtest'te zarar yazdı)
KONSENSUS_BANT_UA = (1.60, 3.00)   # Üst/Alt bandı
MAKS_KOTA_KATI = 1.5         # Max, konsensüsün bu katını aşarsa kota bozuk sayılır
                             # (kaynak veride 22.00/199.00 gibi hatalı kotalar görüldü)


def power_olasilik(oranlar: tuple[float, ...], tur: int = 40) -> tuple[float, ...]:
    """Marj arındırma — "power" yöntemi: Σ q^k = 1 olacak k bulunur.

    Basit normalizasyon marjı tüm sonuçlara eşit dağıtır; oysa kitapçı marjın
    büyük kısmını yüksek oranlara yükler (favori-longshot yanlılığı). Power
    yöntemi bunu düzeltir ve backtest'te isabeti belirgin yükseltti
    (1X2: %42.4 → %45.5, Üst/Alt: %47.9 → %56.2).
    """
    q = [1.0 / o for o in oranlar if o and o > 1]
    if len(q) != len(oranlar):
        return tuple(1.0 / len(oranlar) for _ in oranlar)
    alt, ust = 0.5, 3.0
    for _ in range(tur):
        orta = (alt + ust) / 2
        if sum(x ** orta for x in q) > 1.0:
            alt = orta
        else:
            ust = orta
    k = (alt + ust) / 2
    p = [x ** k for x in q]
    toplam = sum(p)
    return tuple(x / toplam for x in p)


def konsensus_sinyali(konsensus: tuple[float, ...], en_iyi: tuple[float, ...],
                      etiketler: tuple[str, ...], bant: tuple[float, float],
                      esik: float, model_p: dict | None = None) -> dict | None:
    """En iyi fiyat, konsensüsün adil fiyatını yeterince aşıyor mu?

    model_p verilirse model VETO görevi görür: modelin o fiyatta beklenen
    değeri negatifse seçim elenir (backtest: veto seçimlerin üçte birini eler
    ve elenenler kaybedenlerdir — ROI +%20.4 → +%26.5).
    """
    if not konsensus or not en_iyi or len(konsensus) != len(en_iyi):
        return None
    if any(not o or o <= 1 for o in konsensus):
        return None
    adil = power_olasilik(tuple(konsensus))
    adaylar = []
    for i, etiket in enumerate(etiketler):
        fiyat = en_iyi[i]
        if not fiyat or fiyat <= 1:
            continue
        if not (bant[0] <= fiyat < bant[1]):
            continue
        if fiyat > konsensus[i] * MAKS_KOTA_KATI:
            continue  # bozuk kota koruması
        ev = adil[i] * fiyat - 1.0
        if ev < esik:
            continue
        model_ev = None
        if model_p is not None:
            mp = model_p.get(etiket)
            if mp is None:
                continue
            model_ev = mp * fiyat - 1.0
            if model_ev < 0:
                continue  # model vetosu
        adaylar.append({
            "secim": etiket,
            "oran": round(float(fiyat), 2),
            "konsensus": round(float(konsensus[i]), 2),
            "adil_oran": round(1.0 / adil[i], 2) if adil[i] > 0 else None,
            "p": float(adil[i]),
            "ev": float(ev),
            "model_ev": float(model_ev) if model_ev is not None else None,
        })
    if not adaylar:
        return None
    return max(adaylar, key=lambda x: x["ev"])


def sinyal_tara(oranlar: tuple[float, float, float] | None,
                en_iyi: tuple[float, float, float] | None,
                ust_alt: tuple[float, float] | None = None,
                ust_alt_maks: tuple[float, float] | None = None,
                model_p: dict | None = None) -> dict | None:
    """1X2 ve Üst/Alt 2.5 pazarlarını tarar, en güçlü sinyali döndürür."""
    bulunan = []
    if oranlar and en_iyi:
        s = konsensus_sinyali(tuple(oranlar), tuple(en_iyi), ("MS1", "MS0", "MS2"),
                              KONSENSUS_BANT, KONSENSUS_ESIK, model_p)
        if s:
            s["pazar"] = "1X2"
            bulunan.append(s)
    if ust_alt and ust_alt_maks:
        s = konsensus_sinyali(tuple(ust_alt), tuple(ust_alt_maks), ("ÜST 2.5", "ALT 2.5"),
                              KONSENSUS_BANT_UA, KONSENSUS_ESIK_UA, model_p)
        if s:
            s["pazar"] = "Üst/Alt 2.5"
            bulunan.append(s)
    if not bulunan:
        return None
    return max(bulunan, key=lambda x: x["ev"])


# --- dilim önbelleği -------------------------------------------------------
# Bir günün taraması aynı ligi ve aynı takımı defalarca diliyor; her dilimleme
# 248 bin satırlık tablodan satır kopyalamak demek (profilde sürenin yarısı
# buradaydı). Dilimler, uzun ömürlü arşiv nesnesinin üzerinde önbelleklenir.

_DILIM_DURUM: dict = {"ref": None, "dilimler": {}}


def _dilim_onbellek(df: pd.DataFrame) -> dict:
    """Dilim önbelleği modül düzeyinde tutulur.

    df.attrs içinde saklamak pandas'ın __finalize__ karşılaştırmasını bozuyor
    (attrs sözlükleri == ile kıyaslanıyor, içinde DataFrame olunca patlıyor).
    Bu yüzden zayıf referansla tek bir aktif tabloyu izleriz; tablo değişince
    (veri güncellemesi) önbellek kendiliğinden sıfırlanır.
    """
    mevcut = _DILIM_DURUM["ref"]
    if mevcut is None or mevcut() is not df:
        _DILIM_DURUM["ref"] = weakref.ref(df)
        _DILIM_DURUM["dilimler"] = {}
    return _DILIM_DURUM["dilimler"]


def lig_dilimi(df: pd.DataFrame, lig_kodu: str) -> pd.DataFrame:
    """Ligin maçları — aynı istek içinde tekrar tekrar kesilmez."""
    onb = _dilim_onbellek(df)
    anahtar = ("lig", lig_kodu)
    if anahtar not in onb:
        onb[anahtar] = df[df["Div"] == lig_kodu]
    return onb[anahtar]


def takim_dilimi(df: pd.DataFrame, takim: str) -> pd.DataFrame:
    """Takımın tüm maçları (ev+deplasman) — önbellekli."""
    onb = _dilim_onbellek(df)
    anahtar = ("takim", takim)
    if anahtar not in onb:
        onb[anahtar] = df[(df["HomeTeam"] == takim) | (df["AwayTeam"] == takim)]
    return onb[anahtar]


# -------------------------------------------------------------------- 1) form

def form_analizi(df: pd.DataFrame, takim: str, n: int = 10, saha: str | None = None) -> dict:
    """Takımın son n maçının özeti. saha='ev' sadece iç saha, 'dep' sadece deplasman."""
    if saha == "ev":
        m = df[df["HomeTeam"] == takim]
    elif saha == "dep":
        m = df[df["AwayTeam"] == takim]
    else:
        m = takim_dilimi(df, takim)
    m = m.sort_values("Tarih").tail(n)

    seri, puan, atilan, yenilen, maclar = [], 0, 0, 0, []
    for satir in m.itertuples():
        evde = satir.HomeTeam == takim
        gol_at = satir.FTHG if evde else satir.FTAG
        gol_ye = satir.FTAG if evde else satir.FTHG
        sonuc = "G" if gol_at > gol_ye else ("B" if gol_at == gol_ye else "M")
        seri.append(sonuc)
        puan += {"G": 3, "B": 1, "M": 0}[sonuc]
        atilan += gol_at
        yenilen += gol_ye
        maclar.append(
            {
                "tarih": satir.Tarih,
                "ev": satir.HomeTeam,
                "dep": satir.AwayTeam,
                "skor": f"{satir.FTHG}-{satir.FTAG}",
                "sonuc": sonuc,
            }
        )

    adet = len(seri)
    return {
        "takim": takim,
        "mac": adet,
        "seri": "".join(seri),  # soldan sağa eski -> yeni
        "puan": puan,
        "gol_ort": atilan / adet if adet else 0.0,
        "yenilen_ort": yenilen / adet if adet else 0.0,
        "son_maclar": list(reversed(maclar)),  # rapor için yeni -> eski
    }


# --------------------------------------------------------------------- 2) H2H

def h2h_analizi(df: pd.DataFrame, ev: str, dep: str) -> dict:
    """İki takımın (saha fark etmeksizin) birbirine karşı tüm maçları."""
    m = df[
        ((df["HomeTeam"] == ev) & (df["AwayTeam"] == dep))
        | ((df["HomeTeam"] == dep) & (df["AwayTeam"] == ev))
    ].sort_values("Tarih")

    if m.empty:
        return {"mac": 0}

    ev_g = int((((m["HomeTeam"] == ev) & (m["FTR"] == "H")) | ((m["AwayTeam"] == ev) & (m["FTR"] == "A"))).sum())
    dep_g = int((((m["HomeTeam"] == dep) & (m["FTR"] == "H")) | ((m["AwayTeam"] == dep) & (m["FTR"] == "A"))).sum())
    toplam_gol = m["FTHG"] + m["FTAG"]

    return {
        "mac": len(m),
        "ev_galibiyet": ev_g,
        "beraberlik": int((m["FTR"] == "D").sum()),
        "dep_galibiyet": dep_g,
        "gol_ort": float(toplam_gol.mean()),
        "ust25": float((toplam_gol > 2.5).mean()),
        "kg_var": float(((m["FTHG"] > 0) & (m["FTAG"] > 0)).mean()),
        "son_maclar": [
            {
                "tarih": s.Tarih,
                "ev": s.HomeTeam,
                "dep": s.AwayTeam,
                "skor": f"{s.FTHG}-{s.FTAG}",
            }
            for s in m.tail(6).itertuples()
        ][::-1],
    }


# ----------------------------------------------------------------- 3) Poisson

def _guc_katsayisi(maclar: pd.DataFrame, gol_kolonu: str, referans: pd.Timestamp,
                   lig_ort: float) -> tuple[float, float]:
    """Zaman ağırlıklı gol ortalamasının lig ortalamasına oranı ve etkin maç sayısı."""
    if maclar.empty or lig_ort <= 0:
        return 1.0, 0.0
    w = _agirlik(maclar["Tarih"], referans)
    ort = _agirlikli_ort(maclar[gol_kolonu], w, lig_ort)
    katsayi = ort / lig_ort
    return float(min(max(katsayi, 0.25), 4.0)), float(w.sum())


def poisson_tahmini(df: pd.DataFrame, ev: str, dep: str, lig_ipucu: str | None = None) -> dict:
    """Zaman ağırlıklı hücum/savunma güçleriyle gol beklentisi ve olasılıklar."""
    referans = df["Tarih"].max() + pd.Timedelta(days=1)

    # Modeli ev sahibinin ligindeki maçlarla kur (lig ortalamaları o lige ait olmalı).
    # Takımın geçmişi yoksa (yeni çıkan) fikstürden gelen lig ipucu kullanılır.
    ev_maclari = takim_dilimi(df, ev)
    if not ev_maclari.empty:
        lig_kodu = ev_maclari.sort_values("Tarih")["Div"].iloc[-1]
    elif lig_ipucu and (df["Div"] == lig_ipucu).any():
        lig_kodu = lig_ipucu
    else:
        lig_kodu = df["Div"].iloc[-1]
    L = lig_dilimi(df, lig_kodu)

    w_lig = _agirlik(L["Tarih"], referans)
    lig_ev_ort = _agirlikli_ort(L["FTHG"], w_lig, 1.5)
    lig_dep_ort = _agirlikli_ort(L["FTAG"], w_lig, 1.2)

    evde = L[L["HomeTeam"] == ev]
    depte = L[L["AwayTeam"] == dep]

    ev_atak, n1 = _guc_katsayisi(evde, "FTHG", referans, lig_ev_ort)      # evinde attığı
    ev_defans, _ = _guc_katsayisi(evde, "FTAG", referans, lig_dep_ort)    # evinde yediği
    dep_atak, n2 = _guc_katsayisi(depte, "FTAG", referans, lig_dep_ort)   # deplasmanda attığı
    dep_defans, _ = _guc_katsayisi(depte, "FTHG", referans, lig_ev_ort)   # deplasmanda yediği

    lam_ev = min(max(lig_ev_ort * ev_atak * dep_defans, 0.2), 5.5)
    lam_dep = min(max(lig_dep_ort * dep_atak * ev_defans, 0.2), 5.5)

    uyarilar = []
    if len(evde) < MIN_MAC_UYARI:
        uyarilar.append(f"{ev} için iç saha verisi sınırlı ({len(evde)} maç)")
    if len(depte) < MIN_MAC_UYARI:
        uyarilar.append(f"{dep} için deplasman verisi sınırlı ({len(depte)} maç)")

    # Skor olasılık matrisi (0..MAKS_GOL), kesme kaybına karşı normalize edilir.
    p_ev = [_poisson_pmf(i, lam_ev) for i in range(MAKS_GOL + 1)]
    p_dep = [_poisson_pmf(j, lam_dep) for j in range(MAKS_GOL + 1)]
    matris = [[p_ev[i] * p_dep[j] * dc_tau(i, j, lam_ev, lam_dep, DC_RHO)
               for j in range(MAKS_GOL + 1)] for i in range(MAKS_GOL + 1)]
    toplam = sum(sum(satir) for satir in matris)

    ms1 = sum(matris[i][j] for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1) if i > j) / toplam
    ms0 = sum(matris[i][i] for i in range(MAKS_GOL + 1)) / toplam
    ms2 = 1.0 - ms1 - ms0
    ust25 = sum(matris[i][j] for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1) if i + j > 2.5) / toplam
    kg_var = sum(matris[i][j] for i in range(1, MAKS_GOL + 1) for j in range(1, MAKS_GOL + 1)) / toplam

    skorlar = sorted(
        ((f"{i}-{j}", matris[i][j] / toplam) for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1)),
        key=lambda x: x[1],
        reverse=True,
    )[:6]

    def _esik_ust(esik: float) -> float:
        return sum(
            matris[i][j] for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1) if i + j > esik
        ) / toplam

    # İlk yarı modeli: ligdeki gollerin ilk yarıya düşen payı veriden ölçülür
    # (tipik ~%45), gol beklentisi bu payla ilk/ikinci yarıya bölüştürülür.
    ht = L.dropna(subset=["HTHG", "HTAG"])
    toplam_ft = float((ht["FTHG"] + ht["FTAG"]).sum())
    iy_pay = float((ht["HTHG"] + ht["HTAG"]).sum()) / toplam_ft if toplam_ft > 0 else 0.45
    iy_pay = min(max(iy_pay, 0.35), 0.55)

    def _yari_blok(lam_e: float, lam_d: float) -> dict:
        p_e = [_poisson_pmf(i, lam_e) for i in range(MAKS_GOL + 1)]
        p_d = [_poisson_pmf(j, lam_d) for j in range(MAKS_GOL + 1)]
        m = [[p_e[i] * p_d[j] for j in range(MAKS_GOL + 1)] for i in range(MAKS_GOL + 1)]
        t = sum(sum(satir) for satir in m)
        skor = max(
            ((f"{i}-{j}", m[i][j] / t) for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1)),
            key=lambda x: x[1],
        )
        lam_t = lam_e + lam_d
        return {
            "ms1": sum(m[i][j] for i in range(MAKS_GOL + 1) for j in range(MAKS_GOL + 1) if i > j) / t,
            "ms0": sum(m[i][i] for i in range(MAKS_GOL + 1)) / t,
            "ust05": 1.0 - math.exp(-lam_t),
            "ust15": 1.0 - math.exp(-lam_t) * (1.0 + lam_t),
            "skor": skor,
        }

    iy = _yari_blok(lam_ev * iy_pay, lam_dep * iy_pay)
    y2 = _yari_blok(lam_ev * (1 - iy_pay), lam_dep * (1 - iy_pay))
    iy["ms2"] = 1.0 - iy["ms1"] - iy["ms0"]

    return {
        "lig": lig_kodu,
        "lambda_ev": lam_ev,
        "lambda_dep": lam_dep,
        "ms1": ms1,
        "ms0": ms0,
        "ms2": ms2,
        "ust15": _esik_ust(1.5),
        "ust25": ust25,
        "ust35": _esik_ust(3.5),
        "alt25": 1.0 - ust25,
        "kg_var": kg_var,
        "skorlar": skorlar,
        "iy_pay": iy_pay,
        "iy": iy,
        "y2_skor": y2["skor"],
        "uyarilar": uyarilar,
    }


# ------------------------------------------------------------- 4) oran kalıbı

# Lig kodu → ülke grubu: kalıp analizinde "aynı ülkenin ligleri" filtresi için
# (A/B/C ligi fark etmez, ülke karakteri ortaktır: tempo, ev avantajı, marj alışkanlığı)
ULKE_GRUBU = {
    "E0": "İngiltere", "E1": "İngiltere", "E2": "İngiltere", "E3": "İngiltere", "EC": "İngiltere",
    "SC0": "İskoçya", "SC1": "İskoçya", "SC2": "İskoçya", "SC3": "İskoçya",
    "D1": "Almanya", "D2": "Almanya", "I1": "İtalya", "I2": "İtalya",
    "SP1": "İspanya", "SP2": "İspanya", "F1": "Fransa", "F2": "Fransa",
    "N1": "Hollanda", "B1": "Belçika", "P1": "Portekiz", "T1": "Türkiye", "G1": "Yunanistan",
    "ARG": "Arjantin", "AUT": "Avusturya", "BRA": "Brezilya", "CHN": "Çin",
    "DNK": "Danimarka", "FIN": "Finlandiya", "IRL": "İrlanda", "JPN": "Japonya",
    "MEX": "Meksika", "NOR": "Norveç", "POL": "Polonya", "ROU": "Romanya",
    "RUS": "Rusya", "SWE": "İsveç", "SWZ": "İsviçre", "USA": "ABD",
}


def _ulke_kodlari(lig_ipucu: str | None) -> list[str]:
    ulke = ULKE_GRUBU.get(str(lig_ipucu or ""))
    return [k for k, v in ULKE_GRUBU.items() if v == ulke] if ulke else []


def _kalip_verisi(df: pd.DataFrame) -> dict:
    """Oran kalıbı için sayısal arşiv görünümü — tablo başına BİR KEZ kurulur.

    Eskiden oran_kalibi her çağrıda 248 bin satırlık tabloyu .copy() ediyor ve
    tüm arşivin marj arındırılmış olasılıklarını yeniden hesaplıyordu; tarama
    süresinin üçte ikisi buradaydı (maç başına 136 ms). Artık diziler bir kez
    hazırlanır, her maç yalnız numpy karşılaştırması yapar.
    """
    onb = _dilim_onbellek(df)
    if "kalip_verisi" in onb:
        return onb["kalip_verisi"]
    import numpy as np

    o1 = pd.to_numeric(df["oran_ev"], errors="coerce").to_numpy(float)
    o0 = pd.to_numeric(df["oran_berabere"], errors="coerce").to_numpy(float)
    o2 = pd.to_numeric(df["oran_dep"], errors="coerce").to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q1, q0, q2 = 1.0 / o1, 1.0 / o0, 1.0 / o2
        toplam = q1 + q0 + q2
        p1, p0, p2 = q1 / toplam, q0 / toplam, q2 / toplam
    gecerli = np.isfinite(p1) & np.isfinite(p0) & np.isfinite(p2)
    ftr = df["FTR"].astype(str).to_numpy()
    fthg = pd.to_numeric(df["FTHG"], errors="coerce").to_numpy(float)
    ftag = pd.to_numeric(df["FTAG"], errors="coerce").to_numpy(float)
    hthg = pd.to_numeric(df["HTHG"], errors="coerce").to_numpy(float)
    htag = pd.to_numeric(df["HTAG"], errors="coerce").to_numpy(float)
    div = df["Div"].astype(str).to_numpy()
    veri = {
        "np": np, "p1": p1, "p0": p0, "p2": p2, "gecerli": gecerli,
        "ftr_h": ftr == "H", "ftr_d": ftr == "D", "ftr_a": ftr == "A",
        "fthg": fthg, "ftag": ftag, "hthg": hthg, "htag": htag,
        "gol": fthg + ftag, "div": div,
        "iy_var": np.isfinite(hthg) & np.isfinite(htag),
        "tarih": df["Tarih"].to_numpy(),
    }
    onb["kalip_verisi"] = veri
    return veri


def oran_kalibi(df: pd.DataFrame, oranlar: tuple[float, float, float],
                tolerans: float = 0.02, min_mac: int = 40,
                ornek_sayisi: int = 0, lig_ipucu: str | None = None) -> dict | None:
    """Benzer oranla açılmış geçmiş maçlarda gerçekleşen sonuç dağılımı.

    Karşılaştırma, bahis marjı arındırılmış olasılık uzayında yapılır; böylece
    farklı bahisçilerin/yılların marj farkları kalıbı bozmaz. Yeterli örnek
    bulunamazsa tolerans kademeli olarak genişletilir.
    """
    V = _kalip_verisi(df)
    np = V["np"]
    if not V["gecerli"].any():
        return None

    h_ev, h_b, h_dep = adil_olasilik(*oranlar)
    fark = np.maximum(np.abs(V["p1"] - h_ev),
                      np.maximum(np.abs(V["p0"] - h_b), np.abs(V["p2"] - h_dep)))
    fark = np.where(V["gecerli"], fark, np.inf)

    kullanilan_tol = tolerans
    maske = fark <= tolerans
    for carpan in (1.5, 2.0, 2.5):
        if int(maske.sum()) >= min_mac:
            break
        kullanilan_tol = tolerans * carpan
        maske = fark <= kullanilan_tol
    n = int(maske.sum())
    if n < 10:
        return None

    gol = V["gol"][maske]
    fthg_s, ftag_s = V["fthg"][maske], V["ftag"][maske]
    skor_etiket = np.char.add(np.char.add(fthg_s.astype(int).astype(str), "-"),
                              ftag_s.astype(int).astype(str))
    etiketler, adetler = np.unique(skor_etiket, return_counts=True)
    sirali_skor = sorted(zip(etiketler, adetler), key=lambda x: -x[1])

    # İY/MS kombinasyonlarının bu oran kalıbındaki gerçekleşme sayıları
    # (sürpriz radarı: "1/2", "2/1" gibi çapraz sonuçlar tarihte kaç kez geldi?)
    iy_maske = maske & V["iy_var"]
    iyms_n = int(iy_maske.sum())
    iyms: dict[str, int] = {}
    if iyms_n:
        iy_fark = V["hthg"][iy_maske] - V["htag"][iy_maske]
        iy_harf = np.where(iy_fark > 0, "1", np.where(iy_fark == 0, "0", "2"))
        ms_harf = np.where(V["ftr_h"][iy_maske], "1",
                           np.where(V["ftr_d"][iy_maske], "0", "2"))
        kombo = np.char.add(np.char.add(iy_harf, "/"), ms_harf)
        k_et, k_ad = np.unique(kombo, return_counts=True)
        iyms = {str(a): int(b) for a, b in zip(k_et, k_ad)}

    # Aynı ülkenin liglerindeki kalıp (kullanıcı isteği: "Çin maçına Çin
    # liglerinden benzerler") — küçük örneklem yanıltmasın diye genel kalıpla
    # birlikte sunulur ve ancak n>=15'te doldurulur.
    ulke_kodlari = _ulke_kodlari(lig_ipucu)
    ulke_blok = None
    if ulke_kodlari:
        # önce genel kalıpla aynı tolerans; örnek yetmezse ülkeye özel genişleme
        ulke_m = np.isin(V["div"], list(ulke_kodlari))
        u_maske = ulke_m & maske
        if int(u_maske.sum()) < 15:
            u_maske = ulke_m & (fark <= tolerans * 2.5)
        u_n = int(u_maske.sum())
        if u_n >= 15:
            ug = V["gol"][u_maske]
            ulke_blok = {
                "ad": ULKE_GRUBU.get(str(lig_ipucu)),
                "n": u_n,
                "ms1": float(V["ftr_h"][u_maske].mean()),
                "ms0": float(V["ftr_d"][u_maske].mean()),
                "ms2": float(V["ftr_a"][u_maske].mean()),
                "gol_ort": float(ug.mean()),
                "ust25": float((ug > 2.5).mean()),
                "kg_var": float(((V["fthg"][u_maske] > 0) & (V["ftag"][u_maske] > 0)).mean()),
            }

    ornekler = []
    if ornek_sayisi > 0:
        # Örnek önceliği: (1) aynı ülke + İY'li, (2) aynı ülke, (3) İY'li, (4) kalan
        # — her katman kendi içinde en yeniden eskiye.
        sec = df.iloc[np.flatnonzero(maske)]
        sirali = sec.sort_values("Tarih", ascending=False).copy()
        ayni_ulke_s = (sirali["Div"].isin(ulke_kodlari)
                       if ulke_kodlari else pd.Series(False, index=sirali.index))
        iy_var_s = sirali[["HTHG", "HTAG"]].notna().all(axis=1)
        sirali["oncelik"] = (~ayni_ulke_s).astype(int) * 2 + (~iy_var_s).astype(int)
        secilen = sirali.sort_values("oncelik", kind="stable").head(ornek_sayisi)
        for s in secilen.itertuples():
            iy_var = not (pd.isna(s.HTHG) or pd.isna(s.HTAG))
            ornekler.append(
                {
                    "tarih": s.Tarih,
                    "lig": s.Div,
                    "ayni_ulke": bool(s.oncelik < 2),
                    "ev": s.HomeTeam,
                    "dep": s.AwayTeam,
                    "skor": f"{int(s.FTHG)}-{int(s.FTAG)}",
                    "fthg": int(s.FTHG),
                    "ftag": int(s.FTAG),
                    "hthg": int(s.HTHG) if iy_var else None,
                    "htag": int(s.HTAG) if iy_var else None,
                    "oranlar": [float(s.oran_ev), float(s.oran_berabere), float(s.oran_dep)],
                }
            )

    return {
        "ornekler": ornekler,
        "ulke": ulke_blok,
        "iyms_n": iyms_n,
        "iyms": iyms,
        "n": n,
        "tolerans": kullanilan_tol,
        "lig_sayisi": int(len(np.unique(V["div"][maske]))),
        "ilk_tarih": pd.Timestamp(V["tarih"][maske].min()),
        "ms1": float(V["ftr_h"][maske].mean()),
        "ms0": float(V["ftr_d"][maske].mean()),
        "ms2": float(V["ftr_a"][maske].mean()),
        "gol_ort": float(gol.mean()),
        "ust25": float((gol > 2.5).mean()),
        "kg_var": float(((fthg_s > 0) & (ftag_s > 0)).mean()),
        "skorlar": [(str(skor), int(adet), int(adet) / n) for skor, adet in sirali_skor[:6]],
    }


@lru_cache(maxsize=4096)
def _oranlardan_lambdalar(oranlar: tuple[float, float, float], N: int = 8) -> tuple[float, float]:
    """1X2 oranlarından Poisson gol beklentilerini (ev, dep) geri çözer.

    Kitapçılar İY/MS gibi türev pazarları 1X2'yi üreten aynı gol modelinden
    fiyatlar; burada o modelin tersine mühendisliği yapılır: marjı
    arındırılmış 1X2 olasılıklarını en iyi üreten (λ_ev, λ_dep) çifti aranır.
    """
    p1, p0, _p2 = adil_olasilik(*oranlar)
    hedef_fark = p1 - (1 - p1 - p0)

    en_iyi, en_kucuk = (1.35, 1.15), float("inf")
    for adim in range(15, 45):          # toplam gol beklentisi 1.5 .. 4.4
        toplam = adim / 10
        lo, hi = -min(2.4, toplam - 0.2), min(2.4, toplam - 0.2)
        le = ld = toplam / 2
        for _ in range(16):             # fark için ikiye bölme
            fark = (lo + hi) / 2
            le, ld = (toplam + fark) / 2, (toplam - fark) / 2
            pe = [_poisson_pmf(i, le) for i in range(N)]
            pd_ = [_poisson_pmf(i, ld) for i in range(N)]
            q1 = sum(pe[i] * pd_[j] for i in range(1, N) for j in range(i))
            q0 = sum(pe[i] * pd_[i] for i in range(N))
            if (q1 - (1 - q1 - q0)) < hedef_fark:
                lo = fark
            else:
                hi = fark
        hata = abs(q0 - p0)
        if hata < en_kucuk:
            en_kucuk, en_iyi = hata, (le, ld)
    return en_iyi


def iyms_adil_oranlar(oranlar: tuple[float, float, float], iy_pay: float = 0.45,
                      odak: tuple[str, ...] = ("1/0", "1/2", "2/1", "2/0")) -> dict[str, float]:
    """Bir maçın 1X2 oranlarından türetilmiş adil İY/MS oranları.

    Tarihsel İY/MS piyasa fiyatı hiçbir ücretsiz kaynakta yayınlanmadığı için
    fiyat, kitapçıların kendi yöntemiyle (1X2 → gol modeli → çift yarı) yeniden
    üretilir; çıktı marjsız adil orandır — bülten bunun altını öder.
    """
    le, ld = _oranlardan_lambdalar(tuple(round(float(x), 2) for x in oranlar))
    olasiliklar = iyms_olasiliklar({"lambda_ev": le, "lambda_dep": ld, "iy_pay": iy_pay})
    return {k: round(1.0 / olasiliklar[k], 1) for k in odak if olasiliklar.get(k, 0) > 1e-6}


def _birebir_korner(sec: pd.DataFrame) -> dict | None:
    """Birebir oranlı örneklemin korner özeti (veri olan alt küme, n≥30)."""
    if "HC" not in sec.columns or "AC" not in sec.columns:
        return None
    k = sec.dropna(subset=["HC", "AC"])
    if len(k) < 30:
        return None
    toplam = k["HC"] + k["AC"]
    return {"n": int(len(k)), "ort": round(float(toplam.mean()), 1),
            "ust95": round(float((toplam > 9.5).mean()), 3)}


def korner_beklentisi(df: pd.DataFrame, ev: str, dep: str,
                      lig_ipucu: str | None = None) -> dict | None:
    """Beklenen korner sayıları: takımların zaman ağırlıklı korner üretim/yeme
    oranlarından ev-dep kırılımı ve toplam; Üst/Alt olasılıkları Poisson yaklaşımı.

    Not: korner dağılımı Poisson'dan biraz daha oynaktır (aşırı saçılım);
    olasılıklar yaklaşıktır ve bandın ortası en güvenilir bölgedir.
    """
    if "HC" not in df.columns or "AC" not in df.columns:
        return None
    D = df.dropna(subset=["HC", "AC"])
    if len(D) < 200:
        return None
    ref = D["Tarih"].max()
    lig_df = D[D["Div"] == lig_ipucu] if lig_ipucu and (D["Div"] == lig_ipucu).any() else D
    lig_ev, lig_dep = float(lig_df["HC"].mean()), float(lig_df["AC"].mean())

    def taraf(takim: str, evde: bool) -> tuple[float | None, float | None, int]:
        sec = D[D["HomeTeam" if evde else "AwayTeam"] == takim]
        if len(sec) < 4:
            return None, None, int(len(sec))
        w = _agirlik(sec["Tarih"], ref)
        atak = _agirlikli_ort(sec["HC" if evde else "AC"], w, lig_ev if evde else lig_dep)
        yeme = _agirlikli_ort(sec["AC" if evde else "HC"], w, lig_dep if evde else lig_ev)
        return atak, yeme, int(len(sec))

    ev_atak, ev_yeme, n_ev = taraf(ev, True)
    dep_atak, dep_yeme, n_dep = taraf(dep, False)
    if ev_atak is None and dep_atak is None:
        return None
    if ev_atak is None:
        ev_atak, ev_yeme = lig_ev, lig_dep
    if dep_atak is None:
        dep_atak, dep_yeme = lig_dep, lig_ev

    b_ev = max(1.5, min(11.0, (ev_atak + dep_yeme) / 2))
    b_dep = max(1.0, min(10.0, (dep_atak + ev_yeme) / 2))
    toplam = b_ev + b_dep

    def ust_olasilik(cizgi: float) -> float:
        return 1.0 - sum(_poisson_pmf(i, toplam) for i in range(int(math.floor(cizgi)) + 1))

    return {
        "toplam": round(toplam, 1),
        "ev": round(b_ev, 1),
        "dep": round(b_dep, 1),
        "n_ev": n_ev,
        "n_dep": n_dep,
        "lig_ort": round(lig_ev + lig_dep, 1),
        "ustler": {str(c): round(ust_olasilik(c), 3) for c in (8.5, 9.5, 10.5, 11.5)},
    }


def birebir_oran_maclari(df: pd.DataFrame, oranlar: tuple[float, float, float],
                         esikler: tuple[float, ...] = (0.05, 0.10, 0.15, 0.25),
                         min_mac: int = 25, ornek_sayisi: int = 10,
                         lig_ipucu: str | None = None) -> dict | None:
    """Birebir aynı oranla açılmış geçmiş maçlar (ham oran uzayında eşleşme).

    oran_kalibi'nden farkı: marj-arındırılmış olasılık bandı yerine, geçmiş
    maçın üç açılış oranının da hedefe en fazla `esik` kadar uzak olması
    aranır (ör. 2.05/3.40/3.50 → ±0.05 içinde). En dar eşikten başlanır;
    min_mac İY verili örnek bulununca durulur. Örnekler oran yakınlığına
    göre sıralı döner ki "oranlar gerçekten aynı mı" gözle doğrulanabilsin.
    """
    h, b, a = oranlar
    D = df.dropna(subset=["oran_ev", "oran_berabere", "oran_dep",
                          "HTHG", "HTAG", "FTHG", "FTAG"])
    if D.empty:
        return None
    fark = pd.concat(
        [(D["oran_ev"] - h).abs(), (D["oran_berabere"] - b).abs(), (D["oran_dep"] - a).abs()],
        axis=1,
    ).max(axis=1)

    sec, kullanilan = D.iloc[0:0], esikler[-1]
    for esik in esikler:
        sec, kullanilan = D[fark <= esik], esik
        if len(sec) >= min_mac:
            break
    if len(sec) < 5:
        return None

    iy_harf = (sec["HTHG"] - sec["HTAG"]).map(lambda f: "1" if f > 0 else ("0" if f == 0 else "2"))
    ms_harf = sec["FTR"].map({"H": "1", "D": "0", "A": "2"})
    kombo = iy_harf + "/" + ms_harf
    iyms = {k: int(v) for k, v in kombo.value_counts().items()}

    # Aynı ülke katmanı: en geniş eşikte ülke-içi eşleşmeler (İY yayınlayan
    # ülkelerde çalışır; küçük örneklemde gösterilmez)
    ulke_kodlari = _ulke_kodlari(lig_ipucu)
    ulke_blok = None
    if ulke_kodlari:
        u = D[D["Div"].isin(ulke_kodlari) & (fark <= esikler[-1])]
        if len(u) >= 12:
            u_iy = (u["HTHG"] - u["HTAG"]).map(lambda f: "1" if f > 0 else ("0" if f == 0 else "2"))
            u_ms = u["FTR"].map({"H": "1", "D": "0", "A": "2"})
            ulke_blok = {
                "ad": ULKE_GRUBU.get(str(lig_ipucu)),
                "n": int(len(u)),
                "esik": esikler[-1],
                "iyms": {k: int(v) for k, v in (u_iy + "/" + u_ms).value_counts().items()},
            }

    ornekler = []
    if ornek_sayisi > 0:
        yakinlik = fark.loc[sec.index]
        if ulke_kodlari:
            oncelik = (~sec["Div"].isin(ulke_kodlari)).astype(int)
            en_yakin = sec.assign(_o=oncelik, _f=yakinlik).sort_values(["_o", "_f"]).index[:ornek_sayisi]
        else:
            en_yakin = yakinlik.sort_values().index[:ornek_sayisi]
        for idx in en_yakin:
            s = sec.loc[idx]
            o3 = (round(float(s["oran_ev"]), 2),
                  round(float(s["oran_berabere"]), 2),
                  round(float(s["oran_dep"]), 2))
            ornekler.append(
                {
                    "tarih": s["Tarih"].strftime("%d.%m.%Y"),
                    "lig": s["Div"],
                    "ayni_ulke": bool(ulke_kodlari and s["Div"] in ulke_kodlari),
                    "ev": s["HomeTeam"],
                    "dep": s["AwayTeam"],
                    "oranlar": list(o3),
                    # o günün 1X2'sinden türetilmiş İY/MS adil oranları —
                    # "geçmişte bu maça İY/MS kaç verilirdi" sorusunun cevabı
                    "iyms_adil": iyms_adil_oranlar(o3),
                    "iy": f"{int(s['HTHG'])}-{int(s['HTAG'])}",
                    "ms": f"{int(s['FTHG'])}-{int(s['FTAG'])}",
                    "kombo": kombo.loc[idx],
                }
            )

    return {
        "n": int(len(sec)),
        "esik": kullanilan,
        "hedef": [round(h, 2), round(b, 2), round(a, 2)],
        "hedef_iyms_adil": iyms_adil_oranlar((round(h, 2), round(b, 2), round(a, 2))),
        "iyms": iyms,
        "ulke": ulke_blok,
        # Aynı oranlı geçmişin MS dağılımı — çapraz kombinasyon kanıtının
        # yanında maç sonucu eğilimi de okunabilsin diye.
        "ms": {
            "1": float((sec["FTR"] == "H").mean()),
            "0": float((sec["FTR"] == "D").mean()),
            "2": float((sec["FTR"] == "A").mean()),
        },
        "korner": _birebir_korner(sec),
        "ornekler": ornekler,
    }


def iyms_olasiliklar(poisson: dict) -> dict[str, float]:
    """İY/MS kombinasyon olasılıkları (bağımsız iki yarı Poisson modeli).

    İlk yarı ve ikinci yarı skorları ayrı Poisson süreçleri olarak numaralanır
    (yarı payı ligin gerçek İY verisinden gelir); 9 kombinasyonun olasılığı
    çıkarılır: "1/1", "1/0", "1/2", "0/1", ... Çapraz sonuçlar ("1/2", "2/1")
    doğaları gereği düşük olasılıklıdır — yüksek oran ödemelerinin nedeni budur.
    """
    pay = poisson.get("iy_pay", 0.45)
    l1e, l1d = poisson["lambda_ev"] * pay, poisson["lambda_dep"] * pay
    l2e, l2d = poisson["lambda_ev"] * (1 - pay), poisson["lambda_dep"] * (1 - pay)
    N = 7
    pe1 = [_poisson_pmf(i, l1e) for i in range(N)]
    pd1 = [_poisson_pmf(i, l1d) for i in range(N)]
    pe2 = [_poisson_pmf(i, l2e) for i in range(N)]
    pd2 = [_poisson_pmf(i, l2d) for i in range(N)]

    sonuc: dict[str, float] = {}
    toplam = 0.0
    for h1 in range(N):
        for a1 in range(N):
            p_iy = pe1[h1] * pd1[a1]
            iy = "1" if h1 > a1 else ("0" if h1 == a1 else "2")
            for h2 in range(N):
                for a2 in range(N):
                    p = p_iy * pe2[h2] * pd2[a2]
                    th, ta = h1 + h2, a1 + a2
                    ms = "1" if th > ta else ("0" if th == ta else "2")
                    anahtar = iy + "/" + ms
                    sonuc[anahtar] = sonuc.get(anahtar, 0.0) + p
                    toplam += p
    return {k: v / toplam for k, v in sonuc.items()}


def tahmin_hucreleri(poisson: dict, kalip: dict | None = None) -> dict:
    """Tahmin tablosu satırı: her pazar için yön + olasılık.

    MS Üst 2.5 ve KG için kalıp mevcutsa Poisson ile 50/50 harmanlanır
    (kalıp, benzer oranlı maçların gerçekleşmiş frekansıdır); diğer hücreler
    saf Poisson modelinden gelir.
    """
    def ikili(p: float, ust_ad: str = "Ü", alt_ad: str = "A") -> dict:
        return {"sec": ust_ad if p >= 0.5 else alt_ad, "p": p if p >= 0.5 else 1 - p}

    def uclu(p1: float, p0: float, p2: float) -> dict:
        en = max((("1", p1), ("X", p0), ("2", p2)), key=lambda x: x[1])
        return {"sec": en[0], "p": en[1]}

    p_ust25 = poisson["ust25"]
    p_kg = poisson["kg_var"]
    p1, p0, p2 = poisson["ms1"], poisson["ms0"], poisson["ms2"]
    if kalip:
        p_ust25 = 0.5 * p_ust25 + 0.5 * kalip["ust25"]
        p_kg = 0.5 * p_kg + 0.5 * kalip["kg_var"]
        # MS sonucu da kalıpla yarı yarıya harmanlanır: saf Poisson "1 %76" gibi
        # aşırı özgüvenli hücreler üretebiliyor; kalıp (benzer oranla açılan
        # maçların gerçek dağılımı) bunu gerçekçi banda çeker
        p1 = 0.5 * p1 + 0.5 * kalip["ms1"]
        p0 = 0.5 * p0 + 0.5 * kalip["ms0"]
        p2 = 0.5 * p2 + 0.5 * kalip["ms2"]
        t = p1 + p0 + p2
        p1, p0, p2 = p1 / t, p0 / t, p2 / t
    p_kg = min(0.98, p_kg + KG_DUZELTME)  # pazar karnesinde ölçülen sapma düzeltmesi

    iy = poisson["iy"]
    return {
        "iy05": ikili(iy["ust05"]),
        "iy15": ikili(iy["ust15"]),
        "ms15": ikili(poisson["ust15"]),
        "ms25": ikili(p_ust25),
        "ms35": ikili(poisson["ust35"]),
        "kg": ikili(p_kg, "Var", "Yok"),
        "iy_skor": iy["skor"][0],
        "y2_skor": poisson["y2_skor"][0],
        "ms_skor": poisson["skorlar"][0][0],
        "iy_sonuc": uclu(iy["ms1"], iy["ms0"], iy["ms2"]),
        "ms_sonuc": uclu(p1, p0, p2),
    }


def guvenli_secimler(a: dict, sinir: float = 0.70) -> list[dict]:
    """Fiyatlanabilir TÜM çekirdek pazarları tek listede toplar, olasılığa göre sıralar.

    "En garantiye yakın" arayışı içindir: sıralama anahtarı modelin olasılığıdır,
    beklenen değer değil. Gerçek oranı bilinen pazarlarda (MS, Üst/Alt) o oran ve
    EV de eklenir; bilinmeyenlerde yalnız adil oran (1/p) verilir — kullanıcı
    bültendeki gerçek fiyatla kıyaslar. Garanti diye bir şey yoktur; bu liste
    yalnız "en yüksek olasılıklı taraf"ı gösterir, düşük oranlı favorilerin
    çoğu zaman negatif değerli olduğu da unutulmamalıdır.
    """
    p = a["poisson"]
    kalip = a.get("kalip")
    d = a.get("deger")
    adaylar: list[dict] = []

    def ekle(pazar: str, olasilik: float, oran: float | None = None) -> None:
        if olasilik >= sinir:
            kayit = {"pazar": pazar, "p": float(olasilik),
                     "adil": round(1.0 / max(olasilik, 1e-6), 2),
                     "oran": round(float(oran), 2) if oran else None}
            if oran:
                kayit["ev"] = float(olasilik) * float(oran) - 1.0
            adaylar.append(kayit)

    if d:  # piyasa çapalı model olasılıkları — en iyi kalibre kaynağımız
        for s in d["satirlar"]:
            ekle(s["secim"], s["model"], s["oran"])
        mp = d["model_p"]
        p1, p0, p2 = mp["MS1"], mp["MS0"], mp["MS2"]
    else:
        p1, p0, p2 = p["ms1"], p["ms0"], p["ms2"]

    # çifte şans: iki sonucu birden kapsar — "garanti" arayışının doğal adresi
    ekle("ÇŞ 1X", p1 + p0)
    ekle("ÇŞ 12", p1 + p2)
    ekle("ÇŞ X2", p0 + p2)

    kg = p["kg_var"]
    ust = p["ust25"]
    if kalip:  # tahmin tablosuyla aynı ruh: kalıpla 50/50 harman
        kg = 0.5 * kg + 0.5 * kalip["kg_var"]
        ust = 0.5 * ust + 0.5 * kalip["ust25"]
    kg = min(0.98, kg + KG_DUZELTME)  # pazar karnesinde ölçülen sapma düzeltmesi
    ekle("KG VAR", kg)
    ekle("KG YOK", 1 - kg)
    if not d:
        ekle("ÜST 2.5", ust)
        ekle("ALT 2.5", 1 - ust)

    iy = p.get("iy") or {}
    if "ust05" in iy:
        ekle("İY 0.5 ÜST", iy["ust05"])
        ekle("İY 0.5 ALT", 1 - iy["ust05"])
    if "ust15" in iy:
        ekle("İY 1.5 ÜST", iy["ust15"])
        ekle("İY 1.5 ALT", 1 - iy["ust15"])

    adaylar.sort(key=lambda x: -x["p"])
    return adaylar


def gercek_hucreler(fthg: int, ftag: int, hthg: int | None, htag: int | None) -> dict:
    """Oynanmış bir maçın tahmin tablosu kolonlarındaki gerçekleşmiş değerleri."""
    def sonuc(a: int, b: int) -> str:
        return "1" if a > b else ("X" if a == b else "2")

    toplam = fthg + ftag
    hucre = {
        "ms15": "Ü" if toplam > 1.5 else "A",
        "ms25": "Ü" if toplam > 2.5 else "A",
        "ms35": "Ü" if toplam > 3.5 else "A",
        "kg": "Var" if fthg > 0 and ftag > 0 else "Yok",
        "ms_skor": f"{fthg}-{ftag}",
        "ms_sonuc": sonuc(fthg, ftag),
        "iy05": None, "iy15": None, "iy_skor": None, "y2_skor": None, "iy_sonuc": None,
    }
    if hthg is not None and htag is not None:
        iy_toplam = hthg + htag
        hucre.update(
            {
                "iy05": "Ü" if iy_toplam > 0.5 else "A",
                "iy15": "Ü" if iy_toplam > 1.5 else "A",
                "iy_skor": f"{hthg}-{htag}",
                "y2_skor": f"{fthg - hthg}-{ftag - htag}",
                "iy_sonuc": sonuc(hthg, htag),
            }
        )
    return hucre


# ----------------------------------------------------------------- 5) Elo

def elo_hesapla(df: pd.DataFrame, k: float = 20.0, ev_avantaji: float = 60.0) -> dict[str, float]:
    """Tüm arşivi kronolojik gezerek takım Elo reytinglerini üretir (başlangıç 1500)."""
    r: dict[str, float] = {}
    for s in df.sort_values("Tarih").itertuples():
        ra, rb = r.get(s.HomeTeam, 1500.0), r.get(s.AwayTeam, 1500.0)
        beklenen = 1.0 / (1.0 + 10 ** (-((ra + ev_avantaji) - rb) / 400.0))
        skor = 1.0 if s.FTR == "H" else (0.5 if s.FTR == "D" else 0.0)
        r[s.HomeTeam] = ra + k * (skor - beklenen)
        r[s.AwayTeam] = rb - k * (skor - beklenen)
    return r


# ------------------------------------------------------------ değer + öneri

W_PIYASA = 0.50      # model karışımında piyasa (marj arındırılmış oran) payı
MAKS_AYRISMA = 0.05  # model, piyasadan sonuç başına en çok bu kadar ayrışabilir
                     # (backtest kanıtı: serbest ayrışma her eşikte zarar yazdı)


def ayrismayi_sinirla(model: dict, adil: dict) -> dict:
    """Model olasılıklarını piyasa etrafında ±MAKS_AYRISMA banda kırpar.

    Piyasa (özellikle kapanış) uzun vadede en iyi tekil tahmincidir; +%15'lik
    "değer" iddiaları neredeyse her zaman model hatasıdır. Kırpma sahte değer
    işaretlerini keser, gerçek küçük kenarları korur."""
    kirpik = {k: adil[k] + max(-MAKS_AYRISMA, min(MAKS_AYRISMA, model[k] - adil[k]))
              for k in model}
    toplam = sum(kirpik.values())
    return {k: v / toplam for k, v in kirpik.items()}
KG_DUZETME_NOTU = """Pazar karnesi (3 sezon, 18.933 maç) her pazarda ölçüldü:
MS2/ÇŞ1X sapma +0.7 puan (Brier 0.190, en iyi bildiğimiz pazar), MS0 -0.3,
MS1 -0.4, Üst2.5 -1.0, KG -2.0 puan (Brier 0.247, en zayıfı). KG'deki
sistematik eksiklik ölçülen değeriyle düzeltilir; bahis kararı üreten
piyasa çapalı yol (deger_analizi) BİLEREK dokunulmadan bırakıldı — ROI
bantları o haliyle doğrulandı, 1 puanlık kayma için riske atılmaz."""
KG_DUZELTME = 0.020  # karnede ölçülen KG VAR eksik-tahmini (52.1% model → 54.1% gerçek)

ORAN_TAVANI = 3.60  # sürpriz oran filtresi: bu oranın üstü öneriye giremez
                    # (yüksek oranların sistematik pahalı fiyatlandığı — favorite-longshot
                    # bias — hem literatürde hem kendi backtest'imizde doğrulandı)


def deger_analizi(oranlar: tuple[float, float, float], poisson: dict,
                  kalip: dict | None,
                  ust_alt: tuple[float, float] | None = None) -> dict:
    """Model olasılığı ile oranın içerdiği olasılığı karşılaştırır (value bet).

    Model karışımı üç bileşenlidir:
      piyasa %35 (bahis piyasası uzun vadede en iyi tekil tahmincidir; modele
      karıştırmak aşırı özgüveni ve sahte 'değer' sinyallerini azaltır)
      + oran kalıbı %0-25 (örneklem büyüdükçe artar, 200+ maçta tavana ulaşır)
      + Poisson kalan pay.
    ust_alt verilirse Üst/Alt 2.5 pazarı için de değer satırları eklenir.
    """
    o = dict(zip(("MS1", "MS0", "MS2"), oranlar))
    p_poisson = {"MS1": poisson["ms1"], "MS0": poisson["ms0"], "MS2": poisson["ms2"]}
    a1, a0, a2 = adil_olasilik(*oranlar)
    p_adil = {"MS1": a1, "MS0": a0, "MS2": a2}

    w_kalip = 0.0
    if kalip and kalip["n"] >= 30:
        w_kalip = min(kalip["n"] / 200.0, 1.0) * 0.25
    w_poisson = 1.0 - W_PIYASA - w_kalip
    p_kalip = {"MS1": kalip["ms1"], "MS0": kalip["ms0"], "MS2": kalip["ms2"]} if kalip else p_poisson

    model = {
        k: w_poisson * p_poisson[k] + w_kalip * p_kalip[k] + W_PIYASA * p_adil[k] for k in o
    }
    norm = sum(model.values())
    model = {k: v / norm for k, v in model.items()}
    model = ayrismayi_sinirla(model, p_adil)

    marj = sum(1 / v for v in o.values()) - 1.0

    def _satir(secim: str, oran: float, piyasa_p: float, model_p: float) -> dict:
        ev_degeri = model_p * oran - 1.0
        tam_kelly = max(0.0, ev_degeri / (oran - 1.0)) if oran > 1 else 0.0
        return {
            "secim": secim,
            "oran": oran,
            "piyasa": piyasa_p,
            "model": model_p,
            "ev": ev_degeri,
            "kelly": min(tam_kelly / 4.0, 0.05),  # çeyrek Kelly, %5 kasa tavanı
        }

    satirlar = [_satir(k, o[k], p_adil[k], model[k]) for k in ("MS1", "MS0", "MS2")]
    model_p = dict(model)

    if ust_alt and min(ust_alt) > 1.0:
        o_ust, o_alt = ust_alt
        q_u, q_a = 1 / o_ust, 1 / o_alt
        adil_ust = q_u / (q_u + q_a)
        kalip_ust = kalip["ust25"] if kalip else poisson["ust25"]
        m_ust = w_poisson * poisson["ust25"] + w_kalip * kalip_ust + W_PIYASA * adil_ust
        m_ust = adil_ust + max(-MAKS_AYRISMA, min(MAKS_AYRISMA, m_ust - adil_ust))
        satirlar.append(_satir("ÜST 2.5", o_ust, adil_ust, m_ust))
        satirlar.append(_satir("ALT 2.5", o_alt, 1 - adil_ust, 1 - m_ust))
        model_p["ÜST 2.5"] = m_ust
        model_p["ALT 2.5"] = 1 - m_ust

    return {
        "satirlar": satirlar,
        "marj": marj,
        "w_kalip": w_kalip,
        "w_piyasa": W_PIYASA,
        "model_p": model_p,
    }


def oneri_uret(deger: dict, poisson: dict, kalip: dict | None,
               form_ev: dict, form_dep: dict, elo_farki: float | None = None) -> dict:
    """Sinyalleri tek bir karara bağlar: seçim, güven yıldızı, karar."""
    adaylar = [s for s in deger["satirlar"] if s["oran"] <= ORAN_TAVANI]
    surpriz_filtresi = not adaylar
    en_iyi = max(adaylar or deger["satirlar"], key=lambda s: s["ev"])
    secim = en_iyi["secim"]

    # Azınlık-ihtimal değer bahsi açıklaması: seçimin model olasılığı %50'nin
    # altındaysa (ör. kalıp tablosu Üst derken Alt önerilmesi) bu bir çelişki
    # değil fiyat oyunudur — ama kartta AÇIKÇA söylenmezse çelişki gibi okunur.
    # (Böyle seçimleri elemek denendi: backtest kârı düşürdü — bkz. backtest.py.)
    azinlik_notu = None
    if en_iyi["ev"] >= 0.04 and en_iyi["model"] < 0.5:
        ters = {"ÜST 2.5": "Alt", "ALT 2.5": "Üst",
                "MS1": "başka bir sonuç", "MS0": "başka bir sonuç", "MS2": "başka bir sonuç"}
        azinlik_notu = (f"Dikkat — azınlık ihtimale değer bahsi: model bu seçime %{en_iyi['model'] * 100:.0f} "
                        f"veriyor (piyasa %{en_iyi['piyasa'] * 100:.0f} fiyatlıyor); muhtemel sonuç yine "
                        f"{ters.get(secim, 'diğer taraf')}. Kazanç tek maçtan değil, fiyat hatasının "
                        f"seride birikmesinden beklenir — bu bahsin yatması normaldir.")

    yildiz = 1
    if secim in ("MS1", "MS0", "MS2"):
        if max(["ms1", "ms0", "ms2"], key=lambda k: poisson[k]).upper() == secim:
            yildiz += 1
        if kalip and max(["ms1", "ms0", "ms2"], key=lambda k: kalip[k]).upper() == secim:
            yildiz += 1
        puan_farki = form_ev["puan"] - form_dep["puan"]
        form_yonu = "MS1" if puan_farki >= 3 else ("MS2" if puan_farki <= -3 else "MS0")
        if form_yonu == secim:
            yildiz += 1
        if elo_farki is not None:
            elo_yonu = "MS1" if elo_farki >= 50 else ("MS2" if elo_farki <= -50 else "MS0")
            if elo_yonu == secim:
                yildiz += 1
    else:  # ÜST 2.5 / ALT 2.5
        ust_secildi = secim.startswith("ÜST")
        if (poisson["ust25"] > 0.53) == ust_secildi and abs(poisson["ust25"] - 0.5) > 0.03:
            yildiz += 1
        if kalip and (kalip["ust25"] > 0.53) == ust_secildi and abs(kalip["ust25"] - 0.5) > 0.03:
            yildiz += 1
    if en_iyi["ev"] >= 0.08:
        yildiz += 1
    if poisson["uyarilar"]:
        yildiz -= 1
    yildiz = max(1, min(5, yildiz))

    # Eşikler backtest kanıtına göre: kalibreli modelde +%8 üzeri işaretler
    # 3 sezonda +%4.6/+%8.1 ROI yazdı; altı bant tarihte zarardaydı.
    if en_iyi["ev"] >= 0.08:
        karar = "degerli"
    elif en_iyi["ev"] >= 0.04:
        karar = "sinirda"
    else:
        karar = "pas"
    if surpriz_filtresi:
        karar = "pas"  # tüm adaylar oran tavanının üstünde: longshot tuzağı, oynanmaz

    return {
        "secim": secim,
        "oran": en_iyi["oran"],
        "ev": en_iyi["ev"],
        "kelly": en_iyi["kelly"],
        "yildiz": yildiz,
        "karar": karar,
        "azinlik_notu": azinlik_notu,
    }


def mac_analizi(df: pd.DataFrame, ev: str, dep: str,
                oranlar: tuple[float, float, float] | None = None,
                tolerans: float = 0.02,
                elo: dict[str, float] | None = None,
                ust_alt: tuple[float, float] | None = None,
                lig_ipucu: str | None = None,
                ornek_sayisi: int = 0) -> dict:
    """Tüm analiz adımlarını çalıştırıp tek sözlükte toplar (rapor girdisi)."""
    sonuc = {
        "ev": ev,
        "dep": dep,
        "oranlar": oranlar,
        "form_ev": form_analizi(df, ev, n=10),
        "form_dep": form_analizi(df, dep, n=10),
        "form_ev_saha": form_analizi(df, ev, n=5, saha="ev"),
        "form_dep_saha": form_analizi(df, dep, n=5, saha="dep"),
        "h2h": h2h_analizi(df, ev, dep),
        "poisson": poisson_tahmini(df, ev, dep, lig_ipucu=lig_ipucu),
        "elo": None,
        "kalip": None,
        "deger": None,
        "oneri": None,
    }
    elo_farki = None
    if elo and ev in elo and dep in elo:
        elo_farki = elo[ev] - elo[dep]
        sonuc["elo"] = {"ev": elo[ev], "dep": elo[dep], "fark": elo_farki}
    if oranlar:
        sonuc["kalip"] = oran_kalibi(df, oranlar, tolerans=tolerans,
                                     ornek_sayisi=ornek_sayisi, lig_ipucu=lig_ipucu)
        sonuc["deger"] = deger_analizi(oranlar, sonuc["poisson"], sonuc["kalip"], ust_alt=ust_alt)
        sonuc["oneri"] = oneri_uret(
            sonuc["deger"], sonuc["poisson"], sonuc["kalip"],
            sonuc["form_ev"], sonuc["form_dep"], elo_farki=elo_farki,
        )
    return sonuc
