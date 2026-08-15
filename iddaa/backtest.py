"""Geriye dönük test (backtest): sistem geçmişte oynasaydı ne olurdu?

Dürüstlük kuralları:
- Bakış sızıntısı (look-ahead bias) yok: her maç, yalnızca kendisinden ÖNCE
  oynanmış maçların verisiyle değerlendirilir. Zaman ağırlıklı takım güçleri
  artımlı (üstel sönümlü) tutulur; oran kalıbı yalnızca önceki maçlarda aranır.
- Canlı modelle aynı karışım: %35 piyasa + (örnekleme göre <=%25) oran kalıbı
  + kalan Poisson. Aynı yarı ömür, aynı katsayı/lambda kırpmaları.
- Bahis kuralı: adayların (MS1/MS0/MS2 + varsa ÜST/ALT 2.5) en yüksek beklenen
  değerlisi eşiği aşarsa açılış oranından düz 1 birim oynanır.
- Veri yetersizse (takım başına 8 maçtan az saha verisi) maç atlanır.

Sonuç raporu eşik-ROI tablosu içerir; böylece "eşiği yükseltince ne oluyor"
sorusu tek koşuda cevaplanır.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .analiz import W_PIYASA, YARI_OMUR_GUN

_FAKTORIYEL = [1, 1, 2, 6, 24, 120, 720, 5040, 40320]
_MAKS_GOL = 8
MIN_SAHA_MAC = 8  # canlı sistemdeki "sınırlı veri" uyarı sınırıyla aynı


class _Durum:
    """Üstel sönümlü (yarı ömür 1 yıl) ağırlıklı gol toplamları."""

    __slots__ = ("w", "gf", "ga", "n", "son")

    def __init__(self):
        self.w = 0.0
        self.gf = 0.0
        self.ga = 0.0
        self.n = 0
        self.son = None

    def _sondur(self, tarih):
        if self.son is not None:
            k = 0.5 ** ((tarih - self.son).days / YARI_OMUR_GUN)
            self.w *= k
            self.gf *= k
            self.ga *= k
        self.son = tarih

    def ekle(self, tarih, gf: int, ga: int):
        self._sondur(tarih)
        self.w += 1.0
        self.gf += gf
        self.ga += ga
        self.n += 1

    def ort(self, tarih) -> tuple[float, float, float]:
        """(ağırlık, gol/maç, yenilen/maç) — sorgu tarihine sönümlenmiş."""
        self._sondur(tarih)
        if self.w <= 0:
            return 0.0, 0.0, 0.0
        return self.w, self.gf / self.w, self.ga / self.w


def _kirp(x: float, alt: float = 0.25, ust: float = 4.0) -> float:
    return min(max(x, alt), ust)


def _poisson_olasiliklar(lam_ev: float, lam_dep: float) -> tuple[float, float, float, float]:
    """(ms1, ms0, ms2, ust25) — 0..8 gollük skor matrisinden."""
    pe = [math.exp(-lam_ev) * lam_ev**k / _FAKTORIYEL[k] for k in range(_MAKS_GOL + 1)]
    pd_ = [math.exp(-lam_dep) * lam_dep**k / _FAKTORIYEL[k] for k in range(_MAKS_GOL + 1)]
    ms1 = ms0 = ms2 = ust = toplam = 0.0
    for i in range(_MAKS_GOL + 1):
        for j in range(_MAKS_GOL + 1):
            p = pe[i] * pd_[j]
            toplam += p
            if i > j:
                ms1 += p
            elif i == j:
                ms0 += p
            else:
                ms2 += p
            if i + j > 2.5:
                ust += p
    return ms1 / toplam, ms0 / toplam, ms2 / toplam, ust / toplam


def backtest_calistir(df: pd.DataFrame, sezon_sayisi: int = 3, lig: str | None = None,
                      esik: float = 0.04, tolerans: float = 0.02,
                      maks_oran: float | None = 3.60) -> dict:
    """Kronolojik simülasyon; seçilen eşik için ayrıntı + eşik-ROI tablosu döndürür.

    maks_oran: sürpriz oran tuzağı (favorite-longshot bias) filtresi — bahis
    adayları bu oranın üzerindeyse değerlendirme dışı kalır. Piyasada yüksek
    oranların sistematik olarak "pahalı" fiyatlandığı iyi belgelenmiştir;
    model kaynaklı sahte değer sinyallerinin çoğu bu bölgede toplanır.
    None verilirse filtre kapalıdır.
    """
    D = df.sort_values("Tarih").reset_index(drop=True)

    sezonlar = sorted(D["Sezon"].unique())
    test_sezonlar = set(sezonlar[-sezon_sayisi:])

    # Oran kalıbı için marj arındırılmış olasılık dizileri (numpy)
    with np.errstate(divide="ignore", invalid="ignore"):
        q1 = 1.0 / D["oran_ev"].to_numpy(dtype=float)
        q0 = 1.0 / D["oran_berabere"].to_numpy(dtype=float)
        q2 = 1.0 / D["oran_dep"].to_numpy(dtype=float)
        s = q1 + q0 + q2
        p1a, p0a, p2a = q1 / s, q0 / s, q2 / s
    gecerli = np.isfinite(p1a) & np.isfinite(p0a) & np.isfinite(p2a)
    res_h = (D["FTR"] == "H").to_numpy()
    res_d = (D["FTR"] == "D").to_numpy()
    res_a = (D["FTR"] == "A").to_numpy()
    ust_g = ((D["FTHG"] + D["FTAG"]) > 2.5).to_numpy()

    lig_durum: dict[str, _Durum] = {}
    evde: dict[str, _Durum] = {}
    depte: dict[str, _Durum] = {}

    def _al(havuz: dict, anahtar: str) -> _Durum:
        if anahtar not in havuz:
            havuz[anahtar] = _Durum()
        return havuz[anahtar]

    secimler = []      # modelin her test maçındaki en iyi adayı
    taban_satirlar = []  # kıyas stratejileri için (hep MS1, hep favori)

    kayitlar = list(D.itertuples())
    for i, r in enumerate(kayitlar):
        test_mi = (
            r.Sezon in test_sezonlar
            and (lig is None or r.Div == lig)
            and bool(gecerli[i])
        )
        if test_mi:
            LG = _al(lig_durum, r.Div)
            ev_d = _al(evde, r.HomeTeam)
            dep_d = _al(depte, r.AwayTeam)
            w_lig, lig_ev_ort, lig_dep_ort = LG.ort(r.Tarih)
            if w_lig > 0 and lig_ev_ort > 0 and lig_dep_ort > 0 \
                    and ev_d.n >= MIN_SAHA_MAC and dep_d.n >= MIN_SAHA_MAC:
                _, ev_gf, ev_ga = ev_d.ort(r.Tarih)
                _, dep_gf, dep_ga = dep_d.ort(r.Tarih)
                lam_ev = min(max(lig_ev_ort * _kirp(ev_gf / lig_ev_ort) * _kirp(dep_ga / lig_ev_ort), 0.2), 5.5)
                lam_dep = min(max(lig_dep_ort * _kirp(dep_gf / lig_dep_ort) * _kirp(ev_ga / lig_dep_ort), 0.2), 5.5)
                po1, po0, po2, po_ust = _poisson_olasiliklar(lam_ev, lam_dep)

                # oran kalıbı: yalnızca önceki maçlar; örnek azsa tolerans genişler
                dmax = np.maximum(
                    np.abs(p1a[:i] - p1a[i]),
                    np.maximum(np.abs(p0a[:i] - p0a[i]), np.abs(p2a[:i] - p2a[i])),
                )
                gecerli_dilim = gecerli[:i]
                n_k, k1 = 0, None
                for kat in (1.0, 1.5, 2.0, 2.5):
                    m = (dmax <= tolerans * kat) & gecerli_dilim
                    n_k = int(m.sum())
                    if n_k >= 40:
                        break
                w_kalip = 0.0
                k1 = k0 = k2 = k_ust = 0.0
                if n_k >= 30:
                    k1 = float(res_h[:i][m].mean())
                    k0 = float(res_d[:i][m].mean())
                    k2 = float(res_a[:i][m].mean())
                    k_ust = float(ust_g[:i][m].mean())
                    w_kalip = min(n_k / 200.0, 1.0) * 0.25
                w_poisson = 1.0 - W_PIYASA - w_kalip

                # canlı modelle aynı karışım
                m1 = w_poisson * po1 + w_kalip * k1 + W_PIYASA * p1a[i]
                m0 = w_poisson * po0 + w_kalip * k0 + W_PIYASA * p0a[i]
                m2 = w_poisson * po2 + w_kalip * k2 + W_PIYASA * p2a[i]
                norm = m1 + m0 + m2
                m1, m0, m2 = m1 / norm, m0 / norm, m2 / norm

                def _maks(deger_, yedek):
                    return float(deger_) if not pd.isna(deger_) else yedek

                adaylar = [
                    ("MS1", float(r.oran_ev), m1, bool(res_h[i]), _maks(r.oran_ev_maks, float(r.oran_ev))),
                    ("MS0", float(r.oran_berabere), m0, bool(res_d[i]), _maks(r.oran_berabere_maks, float(r.oran_berabere))),
                    ("MS2", float(r.oran_dep), m2, bool(res_a[i]), _maks(r.oran_dep_maks, float(r.oran_dep))),
                ]
                o_u, o_a = getattr(r, "oran_ust25", float("nan")), getattr(r, "oran_alt25", float("nan"))
                if not (pd.isna(o_u) or pd.isna(o_a)) and min(o_u, o_a) > 1.0:
                    adil_ust = (1 / o_u) / (1 / o_u + 1 / o_a)
                    ku = k_ust if w_kalip > 0 else po_ust
                    mu = w_poisson * po_ust + w_kalip * ku + W_PIYASA * adil_ust
                    adaylar.append(("ÜST 2.5", float(o_u), mu, bool(ust_g[i]), _maks(r.oran_ust25_maks, float(o_u))))
                    adaylar.append(("ALT 2.5", float(o_a), 1 - mu, not bool(ust_g[i]), _maks(r.oran_alt25_maks, float(o_a))))

                if maks_oran is not None:
                    adaylar = [a for a in adaylar if a[1] <= maks_oran]
                if adaylar:
                    secim, oran, p_model, tuttu, oran_m = max(adaylar, key=lambda x: x[2] * x[1] - 1.0)
                    secimler.append(
                        {
                            "tarih": r.Tarih,
                            "sezon": r.Sezon,
                            "lig": r.Div,
                            "ev": r.HomeTeam,
                            "dep": r.AwayTeam,
                            "secim": secim,
                            "oran": oran,
                            "oran_maks": max(oran_m, oran),
                            "ev_degeri": p_model * oran - 1.0,
                            "tuttu": tuttu,
                            "kalip_n": n_k,
                        }
                    )

            # kıyas stratejileri model verisinden bağımsızdır
            fav = min(
                (("MS1", float(r.oran_ev), bool(res_h[i])),
                 ("MS0", float(r.oran_berabere), bool(res_d[i])),
                 ("MS2", float(r.oran_dep), bool(res_a[i]))),
                key=lambda x: x[1],
            )
            taban_satirlar.append(
                {
                    "ms1_kar": (float(r.oran_ev) - 1.0) if res_h[i] else -1.0,
                    "fav_kar": (fav[1] - 1.0) if fav[2] else -1.0,
                }
            )

        # durum güncellemesi HER maçta (test olsun olmasın) — kronolojik bilgi birikimi
        LG = _al(lig_durum, r.Div)
        LG.ekle(r.Tarih, int(r.FTHG), int(r.FTAG))
        _al(evde, r.HomeTeam).ekle(r.Tarih, int(r.FTHG), int(r.FTAG))
        _al(depte, r.AwayTeam).ekle(r.Tarih, int(r.FTAG), int(r.FTHG))

    S = pd.DataFrame(secimler)
    if S.empty:
        return {"hata": "Test döneminde değerlendirilebilir maç bulunamadı."}

    def _rapor(alt_kume: pd.DataFrame) -> dict:
        n = len(alt_kume)
        if n == 0:
            return {"bahis": 0, "isabet": 0.0, "kar": 0.0, "roi": 0.0,
                    "kar_maks": 0.0, "roi_maks": 0.0, "ort_oran": 0.0}
        kar = float((alt_kume["tuttu"] * alt_kume["oran"] - 1.0).sum())
        kar_maks = float((alt_kume["tuttu"] * alt_kume["oran_maks"] - 1.0).sum())
        return {
            "bahis": int(n),
            "isabet": float(alt_kume["tuttu"].mean()),
            "kar": kar,
            "roi": kar / n,
            "kar_maks": kar_maks,
            "roi_maks": kar_maks / n,
            "ort_oran": float(alt_kume["oran"].mean()),
        }

    oynanan = S[S["ev_degeri"] >= esik].sort_values("tarih").reset_index(drop=True)

    # eşik-ROI tablosu: aynı koşudan farklı eşiklerin sonucu
    esik_tablosu = []
    for e in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10):
        esik_tablosu.append({"esik": e, **_rapor(S[S["ev_degeri"] >= e])})

    # bakiye eğrisi + maksimum düşüş
    egri, maks_dusus = [], 0.0
    if not oynanan.empty:
        kum = (oynanan["tuttu"] * oynanan["oran"] - 1.0).cumsum()
        kum_maks = (oynanan["tuttu"] * oynanan["oran_maks"] - 1.0).cumsum()
        zirve, md = -1e9, 0.0
        for v in kum:
            zirve = max(zirve, v)
            md = max(md, zirve - v)
        maks_dusus = float(md)
        adim = max(1, len(oynanan) // 240)
        indeksler = list(range(0, len(oynanan), adim)) + [len(oynanan) - 1]
        for j in indeksler:
            egri.append(
                {
                    "t": oynanan["tarih"].iloc[j].strftime("%d.%m.%Y"),
                    "kum": float(kum.iloc[j]),
                    "kum_maks": float(kum_maks.iloc[j]),
                }
            )

    def _kirilim(kolon: str, en_cok: int | None = None) -> list[dict]:
        gruplar = []
        for ad, g in oynanan.groupby(kolon):
            gruplar.append({"ad": str(ad), **_rapor(g)})
        gruplar.sort(key=lambda x: -x["bahis"])
        return gruplar[:en_cok] if en_cok else gruplar

    T = pd.DataFrame(taban_satirlar)
    return {
        "parametreler": {
            "sezonlar": sorted(test_sezonlar),
            "lig": lig,
            "esik": esik,
            "tolerans": tolerans,
            "test_mac": int(len(S)),
        },
        "ozet": {**_rapor(oynanan), "maks_dusus": maks_dusus},
        "esik_tablosu": esik_tablosu,
        "secim_kirilim": _kirilim("secim"),
        "lig_kirilim": _kirilim("lig", en_cok=10),
        "sezon_kirilim": _kirilim("sezon"),
        "egri": egri,
        "taban": {
            "mac": int(len(T)),
            "ms1_roi": float(T["ms1_kar"].mean()) if len(T) else 0.0,
            "favori_roi": float(T["fav_kar"].mean()) if len(T) else 0.0,
        },
    }
