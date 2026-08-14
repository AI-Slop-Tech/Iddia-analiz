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

import pandas as pd

YARI_OMUR_GUN = 365.0   # 1 yıl önceki maç, bugünkünün yarısı kadar ağırlık taşır
MAKS_GOL = 8            # skor matrisinin boyutu (0-8 gol)
MIN_MAC_UYARI = 8       # takım başına bu sayının altında maç varsa "sınırlı veri" uyarısı


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


def adil_olasilik(oran_ev: float, oran_b: float, oran_dep: float) -> tuple[float, float, float]:
    """Bahis marjı arındırılmış (toplamı 1 olan) olasılık üçlüsü."""
    q = (1 / oran_ev, 1 / oran_b, 1 / oran_dep)
    s = sum(q)
    return q[0] / s, q[1] / s, q[2] / s


# -------------------------------------------------------------------- 1) form

def form_analizi(df: pd.DataFrame, takim: str, n: int = 10, saha: str | None = None) -> dict:
    """Takımın son n maçının özeti. saha='ev' sadece iç saha, 'dep' sadece deplasman."""
    if saha == "ev":
        m = df[df["HomeTeam"] == takim]
    elif saha == "dep":
        m = df[df["AwayTeam"] == takim]
    else:
        m = df[(df["HomeTeam"] == takim) | (df["AwayTeam"] == takim)]
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


def poisson_tahmini(df: pd.DataFrame, ev: str, dep: str) -> dict:
    """Zaman ağırlıklı hücum/savunma güçleriyle gol beklentisi ve olasılıklar."""
    referans = df["Tarih"].max() + pd.Timedelta(days=1)

    # Modeli ev sahibinin ligindeki maçlarla kur (lig ortalamaları o lige ait olmalı).
    ev_maclari = df[(df["HomeTeam"] == ev) | (df["AwayTeam"] == ev)]
    lig_kodu = ev_maclari.sort_values("Tarih")["Div"].iloc[-1] if not ev_maclari.empty else df["Div"].iloc[-1]
    L = df[df["Div"] == lig_kodu]

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
    matris = [[p_ev[i] * p_dep[j] for j in range(MAKS_GOL + 1)] for i in range(MAKS_GOL + 1)]
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

    return {
        "lig": lig_kodu,
        "lambda_ev": lam_ev,
        "lambda_dep": lam_dep,
        "ms1": ms1,
        "ms0": ms0,
        "ms2": ms2,
        "ust25": ust25,
        "alt25": 1.0 - ust25,
        "kg_var": kg_var,
        "skorlar": skorlar,
        "uyarilar": uyarilar,
    }


# ------------------------------------------------------------- 4) oran kalıbı

def oran_kalibi(df: pd.DataFrame, oranlar: tuple[float, float, float],
                tolerans: float = 0.02, min_mac: int = 40,
                ornek_sayisi: int = 0) -> dict | None:
    """Benzer oranla açılmış geçmiş maçlarda gerçekleşen sonuç dağılımı.

    Karşılaştırma, bahis marjı arındırılmış olasılık uzayında yapılır; böylece
    farklı bahisçilerin/yılların marj farkları kalıbı bozmaz. Yeterli örnek
    bulunamazsa tolerans kademeli olarak genişletilir.
    """
    D = df.dropna(subset=["oran_ev", "oran_berabere", "oran_dep"]).copy()
    if D.empty:
        return None

    h_ev, h_b, h_dep = adil_olasilik(*oranlar)
    q_ev, q_b, q_dep = 1 / D["oran_ev"], 1 / D["oran_berabere"], 1 / D["oran_dep"]
    s = q_ev + q_b + q_dep
    fark = pd.concat(
        [(q_ev / s - h_ev).abs(), (q_b / s - h_b).abs(), (q_dep / s - h_dep).abs()], axis=1
    ).max(axis=1)

    kullanilan_tol, sec = tolerans, D[fark <= tolerans]
    for carpan in (1.5, 2.0, 2.5):
        if len(sec) >= min_mac:
            break
        kullanilan_tol = tolerans * carpan
        sec = D[fark <= kullanilan_tol]
    if len(sec) < 10:
        return None

    n = len(sec)
    toplam_gol = sec["FTHG"] + sec["FTAG"]
    skor_sayimi = (sec["FTHG"].astype(str) + "-" + sec["FTAG"].astype(str)).value_counts()

    ornekler = []
    if ornek_sayisi > 0:
        for s in sec.sort_values("Tarih", ascending=False).head(ornek_sayisi).itertuples():
            ornekler.append(
                {
                    "tarih": s.Tarih,
                    "lig": s.Div,
                    "ev": s.HomeTeam,
                    "dep": s.AwayTeam,
                    "skor": f"{int(s.FTHG)}-{int(s.FTAG)}",
                    "oranlar": [float(s.oran_ev), float(s.oran_berabere), float(s.oran_dep)],
                }
            )

    return {
        "ornekler": ornekler,
        "n": n,
        "tolerans": kullanilan_tol,
        "lig_sayisi": int(sec["Div"].nunique()),
        "ilk_tarih": sec["Tarih"].min(),
        "ms1": float((sec["FTR"] == "H").mean()),
        "ms0": float((sec["FTR"] == "D").mean()),
        "ms2": float((sec["FTR"] == "A").mean()),
        "gol_ort": float(toplam_gol.mean()),
        "ust25": float((toplam_gol > 2.5).mean()),
        "kg_var": float(((sec["FTHG"] > 0) & (sec["FTAG"] > 0)).mean()),
        "skorlar": [(skor, int(adet), adet / n) for skor, adet in skor_sayimi.head(6).items()],
    }


# ------------------------------------------------------------ değer + öneri

def deger_analizi(oranlar: tuple[float, float, float], poisson: dict,
                  kalip: dict | None) -> dict:
    """Model olasılığı ile oranın içerdiği olasılığı karşılaştırır (value bet).

    Model olasılığı: Poisson + oran kalıbı karışımı. Kalıp örneklemi büyüdükçe
    ağırlığı artar (200+ maçta %50'ye ulaşır); kalıp yoksa saf Poisson kullanılır.
    """
    o = dict(zip(("MS1", "MS0", "MS2"), oranlar))
    p_poisson = {"MS1": poisson["ms1"], "MS0": poisson["ms0"], "MS2": poisson["ms2"]}

    w_kalip = 0.0
    if kalip and kalip["n"] >= 30:
        w_kalip = min(kalip["n"] / 200.0, 1.0) * 0.5
    p_kalip = {"MS1": kalip["ms1"], "MS0": kalip["ms0"], "MS2": kalip["ms2"]} if kalip else p_poisson

    model = {k: (1 - w_kalip) * p_poisson[k] + w_kalip * p_kalip[k] for k in o}
    norm = sum(model.values())
    model = {k: v / norm for k, v in model.items()}

    q = {k: 1 / v for k, v in o.items()}
    marj = sum(q.values()) - 1.0
    adil = {k: v / sum(q.values()) for k, v in q.items()}

    satirlar = []
    for k in ("MS1", "MS0", "MS2"):
        ev_degeri = model[k] * o[k] - 1.0
        tam_kelly = max(0.0, (model[k] * o[k] - 1.0) / (o[k] - 1.0)) if o[k] > 1 else 0.0
        satirlar.append(
            {
                "secim": k,
                "oran": o[k],
                "piyasa": adil[k],
                "model": model[k],
                "ev": ev_degeri,
                "kelly": min(tam_kelly / 4.0, 0.05),  # çeyrek Kelly, %5 kasa tavanı
            }
        )

    return {"satirlar": satirlar, "marj": marj, "w_kalip": w_kalip}


def oneri_uret(deger: dict, poisson: dict, kalip: dict | None,
               form_ev: dict, form_dep: dict) -> dict:
    """Sinyalleri tek bir karara bağlar: seçim, güven yıldızı, gerekçe."""
    en_iyi = max(deger["satirlar"], key=lambda s: s["ev"])
    secim = en_iyi["secim"]

    poisson_zirve = max(["ms1", "ms0", "ms2"], key=lambda k: poisson[k]).upper()

    yildiz = 1
    if poisson_zirve == secim:
        yildiz += 1
    if kalip:
        kalip_zirve = max(["ms1", "ms0", "ms2"], key=lambda k: kalip[k]).upper()
        if kalip_zirve == secim:
            yildiz += 1
    puan_farki = form_ev["puan"] - form_dep["puan"]
    form_yonu = "MS1" if puan_farki >= 3 else ("MS2" if puan_farki <= -3 else "MS0")
    if form_yonu == secim:
        yildiz += 1
    if en_iyi["ev"] >= 0.05:
        yildiz += 1
    if poisson["uyarilar"]:
        yildiz -= 1
    yildiz = max(1, min(5, yildiz))

    if en_iyi["ev"] >= 0.04:
        karar = "degerli"
    elif en_iyi["ev"] >= 0.01:
        karar = "sinirda"
    else:
        karar = "pas"

    return {
        "secim": secim,
        "oran": en_iyi["oran"],
        "ev": en_iyi["ev"],
        "kelly": en_iyi["kelly"],
        "yildiz": yildiz,
        "karar": karar,
    }


def mac_analizi(df: pd.DataFrame, ev: str, dep: str,
                oranlar: tuple[float, float, float] | None = None,
                tolerans: float = 0.02) -> dict:
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
        "poisson": poisson_tahmini(df, ev, dep),
        "kalip": None,
        "deger": None,
        "oneri": None,
    }
    if oranlar:
        sonuc["kalip"] = oran_kalibi(df, oranlar, tolerans=tolerans)
        sonuc["deger"] = deger_analizi(oranlar, sonuc["poisson"], sonuc["kalip"])
        sonuc["oneri"] = oneri_uret(
            sonuc["deger"], sonuc["poisson"], sonuc["kalip"], sonuc["form_ev"], sonuc["form_dep"]
        )
    return sonuc
