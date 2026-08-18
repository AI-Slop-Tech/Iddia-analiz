"""Web arayüzü: Flask tabanlı tek sayfa panel + JSON API.

Başlatma: python tahmin.py web  (varsayılan http://127.0.0.1:8000)

Uç noktalar:
  GET  /api/durum          veri seti özeti (yoksa {"veri_yok": true})
  GET  /api/baglanti       kaynağa erişim/vekil testi (IDDAA_PROXY doğrulama)
  POST /api/guncelle       veriyi indir + belleğe yeniden yükle
  GET  /api/takimlar       takım listesi (arayüz seçicileri için)
  POST /api/oran-analiz    {"oranlar":[1,X,2], "tolerans":0.02} -> oran kalıbı
  POST /api/takim-analiz   {"ev":..,"dep":..,"oranlar":[..]?} -> tam maç analizi
  GET  /api/gecmis-maclar  ?takim=&lig=&limit=  -> oranlarıyla eski maçlar
"""

from __future__ import annotations

import os
import threading
import time

import pandas as pd

from . import __version__ as SURUM
from . import analiz, backtest, kayit, rapor, veri, yorum

_DURUM: dict = {
    "df": None, "elo": None, "fikstur": None, "kitapcilar": [],
    "fikstur_zaman": 0.0, "arsiv_zaman": 0.0,
}
_BAKIM = {"basladi": False}

GUN_ADLARI = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

FIKSTUR_BELLEK_TTL = 15 * 60      # bellekteki fikstür en geç 15 dk'da bir yeniden okunur
ARSIV_YENILEME_ARALIGI = 24 * 3600  # güncel sezon arşivi günde bir tazelenir
BAKIM_PERIYODU = 15 * 60


def _df(zorla: bool = False) -> pd.DataFrame:
    if _DURUM["df"] is None or zorla:
        _DURUM["df"] = veri.veriyi_yukle()
        _DURUM["elo"] = analiz.elo_hesapla(_DURUM["df"])
        _DURUM["arsiv_zaman"] = time.time()
    return _DURUM["df"]


def _bakim_dongusu() -> None:
    """Arka plan bakımı: gün ilerledikçe takvim ve arşiv kendiliğinden tazelenir.

    - Fikstür: bellek kopyası periyodik yeniden okunur (dosya indirme zaten
      6 saatlik TTL'e uyar); geçmiş günler otomatik düşer.
    - Arşiv: günde bir kez güncel sezon dosyaları indirilip veri + Elo
      yeniden yüklenir (eski sezonlar önbellekte olduğundan hızlıdır).
    """
    while True:
        time.sleep(BAKIM_PERIYODU)
        try:
            if _DURUM["df"] is not None and time.time() - _DURUM["arsiv_zaman"] > ARSIV_YENILEME_ARALIGI:
                veri.indir()
                _df(zorla=True)
                _DURUM["fikstur"] = None  # yeni veriyle yeniden okunsun
        except Exception:  # noqa: BLE001 - bakım hatası servisi düşürmesin
            pass
        try:
            if _DURUM["df"] is not None:
                fik, kitapcilar = veri.fikstur_yukle(
                    ligler=sorted(set(_DURUM["df"]["Div"].unique()) | set(veri.EK_LIGLER))
                )
                _DURUM["fikstur"], _DURUM["kitapcilar"] = fik, kitapcilar
                _DURUM["fikstur_zaman"] = time.time()
        except Exception:  # noqa: BLE001
            pass


def _num(x, basamak: int = 2):
    return None if pd.isna(x) else round(float(x), basamak)


def _t(ts: pd.Timestamp) -> str:
    return ts.strftime("%d.%m.%Y")


def _form_json(f: dict) -> dict:
    return {
        "takim": f["takim"],
        "mac": int(f["mac"]),
        "seri": f["seri"],
        "puan": int(f["puan"]),
        "gol_ort": round(float(f["gol_ort"]), 2),
        "yenilen_ort": round(float(f["yenilen_ort"]), 2),
        "son_maclar": [
            {"tarih": _t(m["tarih"]), "ev": m["ev"], "dep": m["dep"], "skor": m["skor"], "sonuc": m["sonuc"]}
            for m in f["son_maclar"]
        ],
    }


def _kalip_json(k: dict | None) -> dict | None:
    if not k:
        return None
    return {
        "n": int(k["n"]),
        "tolerans": float(k["tolerans"]),
        "lig_sayisi": int(k["lig_sayisi"]),
        "ilk_yil": int(k["ilk_tarih"].year),
        "ms1": float(k["ms1"]),
        "ms0": float(k["ms0"]),
        "ms2": float(k["ms2"]),
        "gol_ort": round(float(k["gol_ort"]), 2),
        "ust25": float(k["ust25"]),
        "kg_var": float(k["kg_var"]),
        "skorlar": [{"skor": s, "adet": int(a), "oran": float(o)} for s, a, o in k["skorlar"]],
        "ornekler": [
            {
                "tarih": _t(o["tarih"]),
                "lig": o["lig"],
                "ev": o["ev"],
                "dep": o["dep"],
                "skor": o["skor"],
                "oranlar": [round(x, 2) for x in o["oranlar"]],
                "hucre": (
                    analiz.gercek_hucreler(o["fthg"], o["ftag"], o["hthg"], o["htag"])
                    if "fthg" in o else None
                ),
            }
            for o in k.get("ornekler", [])
        ],
    }


def _mac_json(a: dict) -> dict:
    p = a["poisson"]
    sonuc = {
        "ev": a["ev"],
        "dep": a["dep"],
        "lig": p["lig"],
        "lig_adi": veri.LIGLER.get(p["lig"], p["lig"]),
        "oranlar": list(a["oranlar"]) if a["oranlar"] else None,
        "form_ev": _form_json(a["form_ev"]),
        "form_dep": _form_json(a["form_dep"]),
        "form_ev_saha": _form_json(a["form_ev_saha"]),
        "form_dep_saha": _form_json(a["form_dep_saha"]),
        "poisson": {
            "lambda_ev": round(float(p["lambda_ev"]), 2),
            "lambda_dep": round(float(p["lambda_dep"]), 2),
            "ms1": float(p["ms1"]),
            "ms0": float(p["ms0"]),
            "ms2": float(p["ms2"]),
            "ust25": float(p["ust25"]),
            "alt25": float(p["alt25"]),
            "kg_var": float(p["kg_var"]),
            "skorlar": [{"skor": s, "p": float(x)} for s, x in p["skorlar"]],
            "uyarilar": p["uyarilar"],
        },
        "kalip": _kalip_json(a["kalip"]),
        "elo": (
            {"ev": round(a["elo"]["ev"]), "dep": round(a["elo"]["dep"]), "fark": round(a["elo"]["fark"])}
            if a.get("elo") else None
        ),
        "deger": None,
        "oneri": None,
    }

    h = a["h2h"]
    if h["mac"]:
        sonuc["h2h"] = {
            "mac": int(h["mac"]),
            "ev_galibiyet": int(h["ev_galibiyet"]),
            "beraberlik": int(h["beraberlik"]),
            "dep_galibiyet": int(h["dep_galibiyet"]),
            "gol_ort": round(float(h["gol_ort"]), 2),
            "ust25": float(h["ust25"]),
            "kg_var": float(h["kg_var"]),
            "son_maclar": [
                {"tarih": _t(m["tarih"]), "ev": m["ev"], "dep": m["dep"], "skor": m["skor"]}
                for m in h["son_maclar"]
            ],
        }
    else:
        sonuc["h2h"] = {"mac": 0}

    if a["deger"]:
        d = a["deger"]
        sonuc["deger"] = {
            "marj": float(d["marj"]),
            "w_kalip": float(d["w_kalip"]),
            "w_piyasa": float(d.get("w_piyasa", 0.0)),
            "satirlar": [
                {
                    "secim": s["secim"],
                    "oran": float(s["oran"]),
                    "piyasa": float(s["piyasa"]),
                    "model": float(s["model"]),
                    "ev": float(s["ev"]),
                    "kelly": float(s["kelly"]),
                }
                for s in d["satirlar"]
            ],
        }
    if a["oneri"]:
        o = a["oneri"]
        sonuc["oneri"] = {
            "secim": o["secim"],
            "oran": float(o["oran"]),
            "ev": float(o["ev"]),
            "kelly": float(o["kelly"]),
            "yildiz": int(o["yildiz"]),
            "karar": o["karar"],
        }
    sonuc["yorum"] = yorum.olustur(a)
    return sonuc


def _oranlari_dogrula(ham) -> tuple[float, float, float] | None:
    if not isinstance(ham, (list, tuple)) or len(ham) != 3:
        return None
    try:
        oranlar = tuple(float(x) for x in ham)
    except (TypeError, ValueError):
        return None
    if min(oranlar) <= 1.0 or max(oranlar) > 1000:
        return None
    return oranlar


def uygulama_olustur():
    from flask import Flask, jsonify, request

    if not _BAKIM["basladi"]:
        _BAKIM["basladi"] = True
        threading.Thread(target=_bakim_dongusu, daemon=True, name="iddaa-bakim").start()

    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
        static_url_path="",
    )

    @app.get("/")
    def ana_sayfa():
        return app.send_static_file("index.html")

    @app.get("/api/durum")
    def durum():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"veri_yok": True})
        return jsonify(
            {
                "veri_yok": False,
                "toplam_mac": int(len(df)),
                "ilk_tarih": _t(df["Tarih"].min()),
                "son_tarih": _t(df["Tarih"].max()),
                "oran_kapsami": round(float(df["oran_ev"].notna().mean()), 3),
                "surum": SURUM,
                "gemini": bool(veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")),
                "dis_kapsam": bool(veri.gizli_anahtar("FOOTBALL_DATA_ORG_KEY", "football_data_org_key")),
                "veri_zamani": time.strftime("%d.%m %H:%M", time.localtime(_DURUM["arsiv_zaman"])),
                "ligler": [
                    {"kod": lig, "ad": veri.LIGLER.get(lig, lig), "mac": int(adet)}
                    for lig, adet in df["Div"].value_counts().items()
                ],
            }
        )

    @app.get("/api/baglanti")
    def baglanti():
        """Veri kaynağına erişim/vekil testi — indirmeye başlamadan önce."""
        return jsonify(veri.baglanti_testi())

    @app.post("/api/guncelle")
    def guncelle():
        govde = request.get_json(silent=True) or {}
        try:
            ozet = veri.indir(govde.get("ligler"))
        except veri.ErisimHatasi as hata:
            return jsonify({"hata": str(hata), "indirilen": hata.ozet.get("indirilen", 0)}), 502
        try:
            _df(zorla=True)
        except FileNotFoundError:
            return jsonify({"hata": "Veri indirilemedi, internet bağlantısını kontrol edin."}), 502
        return jsonify({"indirilen": ozet["indirilen"], "onbellek": ozet["onbellek"], "hata_sayisi": len(ozet["hata"])})

    @app.get("/api/takimlar")
    def takimlar():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        aktif_sinir = df["Tarih"].max() - pd.Timedelta(days=365)
        liste = veri.takim_listesi(df)
        return jsonify(
            [
                {
                    "ad": s.Takim,
                    "lig": s.lig,
                    "mac": int(s.mac),
                    "aktif": bool(s.son_mac >= aktif_sinir),
                }
                for s in liste.itertuples()
            ]
        )

    @app.post("/api/oran-analiz")
    def oran_analiz():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        govde = request.get_json(silent=True) or {}
        oranlar = _oranlari_dogrula(govde.get("oranlar"))
        if not oranlar:
            return jsonify({"hata": "Üç oran da 1.00'den büyük sayı olmalı (sıra: MS1, MS0, MS2)."}), 400
        tolerans = min(max(float(govde.get("tolerans", 0.02)), 0.005), 0.10)

        adil = analiz.adil_olasilik(*oranlar)
        marj = sum(1 / o for o in oranlar) - 1.0
        kalip = analiz.oran_kalibi(df, oranlar, tolerans=tolerans, ornek_sayisi=12)
        return jsonify(
            {
                "oranlar": list(oranlar),
                "adil": {"ms1": adil[0], "ms0": adil[1], "ms2": adil[2]},
                "marj": float(marj),
                "kalip": _kalip_json(kalip),
            }
        )

    @app.get("/api/oran-bul")
    def oran_bul():
        """Takım isimleriyle maçı önümüzdeki günlerin bülteninde bulup
        uluslararası oranları döndürür (Takım Analizi'nin otomatik doldurması)."""
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        try:
            ev = veri.takim_bul(df, str(request.args.get("ev", "")))
            dep = veri.takim_bul(df, str(request.args.get("dep", "")))
        except ValueError as hata:
            return jsonify({"hata": str(hata)}), 400
        try:
            fik, kitapcilar = _fikstur()
        except Exception as hata:  # noqa: BLE001
            return jsonify({"hata": f"Fikstür alınamadı: {hata}"}), 502

        duz = fik[(fik["HomeTeam"] == ev) & (fik["AwayTeam"] == dep)]
        ters = fik[(fik["HomeTeam"] == dep) & (fik["AwayTeam"] == ev)]
        secilen, ters_mi = (duz, False) if not duz.empty else (ters, True)
        if secilen.empty:
            return jsonify(
                {
                    "bulundu": False,
                    "mesaj": f"{ev} – {dep} önümüzdeki günlerin bülteninde bulunamadı; "
                             "oranı elle girebilirsiniz (analiz oransız da çalışır).",
                }
            )
        r = secilen.iloc[0]
        oranlar, maks, ust_alt = _fikstur_oranlari(r)
        ozet = _mac_ozeti(secilen.index[0], r, kitapcilar)
        return jsonify(
            {
                "bulundu": True,
                "ters": ters_mi,
                "ev": r["HomeTeam"],
                "dep": r["AwayTeam"],
                "tarih": r["Tarih"].strftime("%d.%m.%Y"),
                "saat": ozet["saat"],
                "lig_adi": ozet["lig_adi"],
                "oranlar": oranlar,
                "maks": maks,
                "ust_alt": ust_alt,
                "kitapcilar": ozet["kitapcilar"],
                "oran_bekleniyor": oranlar is None,
            }
        )

    @app.post("/api/takim-analiz")
    def takim_analiz():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        govde = request.get_json(silent=True) or {}
        try:
            ev = veri.takim_bul(df, str(govde.get("ev", "")))
            dep = veri.takim_bul(df, str(govde.get("dep", "")))
        except ValueError as hata:
            return jsonify({"hata": str(hata)}), 400
        if ev == dep:
            return jsonify({"hata": "Aynı takımı iki kez seçtiniz."}), 400

        oranlar = None
        if govde.get("oranlar"):
            oranlar = _oranlari_dogrula(govde["oranlar"])
            if not oranlar:
                return jsonify({"hata": "Oranlar geçersiz: üçü de 1.00'den büyük olmalı."}), 400

        ust_alt = None
        ua = govde.get("ust_alt")
        if isinstance(ua, (list, tuple)) and len(ua) == 2:
            try:
                ua = (float(ua[0]), float(ua[1]))
                if min(ua) > 1.0:
                    ust_alt = ua
            except (TypeError, ValueError):
                ust_alt = None

        tolerans = min(max(float(govde.get("tolerans", 0.02)), 0.005), 0.10)
        a = analiz.mac_analizi(
            df, ev, dep, oranlar=oranlar, tolerans=tolerans, elo=_DURUM["elo"], ust_alt=ust_alt
        )
        return jsonify(_mac_json(a))

    def _dis_kapsami_ekle(df: pd.DataFrame, fik: pd.DataFrame, yenile: bool) -> pd.DataFrame:
        """football-data.org kapsama maçlarını bültene katar (oran yok).

        Takım adları arşive bulanık eşlenir; ikisi de eşleşirse maç tam analiz
        alır, eşleşmezse yalnız listelenir (analiz_yok). Oranlı bültenle çakışan
        maçlar (aynı gün + aynı eşleşmiş ikili) elenir — oranlı satır kazanır.
        """
        dis = veri.dis_fikstur(yenile=yenile)
        if dis is None or dis.empty:
            return fik
        dis = dis[dis["Tarih"] >= veri.simdi_tr().normalize()].copy()
        if dis.empty:
            return fik

        hafiza: dict = {}

        def _esle(ad: str):
            if ad not in hafiza:
                try:
                    hafiza[ad] = veri.takim_bul(df, ad)
                except ValueError:
                    hafiza[ad] = None
            return hafiza[ad]

        # bulanık eşleşme koruması: takımın arşivdeki güncel ligi, maçın
        # ligiyle uyuşmalı (ör. Hollanda maçındaki "NEC", Meksika'nın
        # Necaxa'sına bağlanmasın). Lig kodu arşivde yoksa (ŞL, CLI gibi
        # ligler-arası turnuvalar) kontrol atlanır.
        son_lig = {}
        for _, s in (
            pd.concat(
                [
                    df[["HomeTeam", "Div", "Tarih"]].rename(columns={"HomeTeam": "Takim"}),
                    df[["AwayTeam", "Div", "Tarih"]].rename(columns={"AwayTeam": "Takim"}),
                ]
            )
            .sort_values("Tarih")
            .drop_duplicates("Takim", keep="last")
            .iterrows()
        ):
            son_lig[s["Takim"]] = s["Div"]

        mevcut = {
            (r.Tarih.date(), r.HomeTeam, r.AwayTeam) for r in fik.itertuples()
        }
        satirlar = []
        for r in dis.itertuples():
            ev, dep = _esle(r.HomeTeam), _esle(r.AwayTeam)
            analiz_var = ev is not None and dep is not None
            if analiz_var and r.Div in veri.LIGLER:
                if son_lig.get(ev) != r.Div or son_lig.get(dep) != r.Div:
                    analiz_var = False  # şüpheli eşleşme: yalnız listele
            anahtar = (r.Tarih.date(), ev or r.HomeTeam, dep or r.AwayTeam)
            if anahtar in mevcut:
                continue  # oranlı bültende zaten var
            mevcut.add(anahtar)
            s = r._asdict()
            s.pop("Index", None)
            if analiz_var:
                s["HomeTeam"], s["AwayTeam"] = ev, dep
            s["analiz_yok"] = not analiz_var
            satirlar.append(s)
        if not satirlar:
            return fik
        birlesik = pd.concat([fik, pd.DataFrame(satirlar)], ignore_index=True)
        return birlesik.sort_values("Tarih").reset_index(drop=True)

    def _fikstur(yenile: bool = False, bellek_ttl: bool = False):
        """bellek_ttl=True: listeleme çağrıları için TTL dolduysa yeniden oku.
        Detay/tarama çağrıları mevcut kopyayı kullanır ki satır id'leri kaymasın."""
        df = _df()
        bayat = bellek_ttl and time.time() - _DURUM["fikstur_zaman"] > FIKSTUR_BELLEK_TTL
        if _DURUM["fikstur"] is None or yenile or bayat:
            fik, kitapcilar = veri.fikstur_yukle(
                ligler=sorted(set(df["Div"].unique()) | set(veri.EK_LIGLER)), yenile=yenile
            )
            fik = _dis_kapsami_ekle(df, fik, yenile)
            _DURUM["fikstur"], _DURUM["kitapcilar"] = fik, kitapcilar
            _DURUM["fikstur_zaman"] = time.time()
        return _DURUM["fikstur"], _DURUM["kitapcilar"]

    def _fikstur_oranlari(r):
        """Satırdan (analiz, en iyi, üst/alt) oran üçlülerini çıkarır."""
        oranlar = [_num(r["oran_ev"]), _num(r["oran_berabere"]), _num(r["oran_dep"])]
        if any(x is None for x in oranlar):
            oranlar = None
        maks = [_num(r["oran_max_ev"]), _num(r["oran_max_berabere"]), _num(r["oran_max_dep"])]
        if any(x is None for x in maks):
            maks = None
        ust_alt = [_num(r["oran_ust25"]), _num(r["oran_alt25"])]
        if any(x is None for x in ust_alt):
            ust_alt = None
        return oranlar, maks, ust_alt

    def _mac_ozeti(idx, r, kitapcilar):
        oranlar, maks, ust_alt = _fikstur_oranlari(r)
        kitapci = {}
        for p in kitapcilar:
            uclu = [_num(r.get(f"{p}H")), _num(r.get(f"{p}D")), _num(r.get(f"{p}A"))]
            if all(x is not None for x in uclu):
                kitapci[veri.KITAPCI_ADLARI[p]] = uclu
        ozel_ad = r.get("LigAdi")
        return {
            "id": int(idx),
            "saat": r["Tarih"].strftime("%H:%M"),
            "lig": r["Div"],
            "lig_adi": (ozel_ad if isinstance(ozel_ad, str) and ozel_ad else None)
                       or veri.LIGLER.get(r["Div"], r["Div"]),
            "ev": r["HomeTeam"],
            "dep": r["AwayTeam"],
            "oranlar": oranlar,
            "maks": maks,
            "ust_alt": ust_alt,
            "kitapcilar": kitapci,
            "analiz_yok": bool(r.get("analiz_yok", False) is True),
        }

    @app.get("/api/bulten")
    def bulten():
        try:
            _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        try:
            fik, kitapcilar = _fikstur(yenile=request.args.get("yenile") == "1", bellek_ttl=True)
        except Exception as hata:  # noqa: BLE001
            return jsonify({"hata": f"Fikstür alınamadı: {hata}"}), 502
        simdi = veri.simdi_tr()
        gunler = []
        for gun, grup in fik.groupby(fik["Tarih"].dt.date):
            maclar = []
            for i, r in grup.iterrows():
                m = _mac_ozeti(i, r, kitapcilar)
                m["basladi"] = bool(r["Tarih"] <= simdi)
                maclar.append(m)
            gunler.append(
                {
                    "tarih": gun.strftime("%d.%m.%Y"),
                    "gun_adi": GUN_ADLARI[gun.weekday()],
                    "maclar": maclar,
                }
            )
        return jsonify(
            {
                "gunler": gunler,
                "bugun": simdi.strftime("%d.%m.%Y"),
                "simdi": simdi.strftime("%H:%M"),
                "guncelleme": time.strftime("%H:%M", time.localtime(_DURUM["fikstur_zaman"])),
                "kaynak_yayini": veri.fikstur_kaynak_yayini(),
                "dis": dict(veri.DIS_SON_DURUM),
            }
        )

    def _en_iyi_oran(r, secim, maks):
        if secim == "MS1":
            return maks[0] if maks else None
        if secim == "MS0":
            return maks[1] if maks else None
        if secim == "MS2":
            return maks[2] if maks else None
        kolon = "Max>2.5" if secim.startswith("ÜST") else "Max<2.5"
        return _num(r.get(kolon))

    @app.post("/api/bulten-tara")
    def bulten_tara():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        govde = request.get_json(silent=True) or {}
        tarih = str(govde.get("tarih", ""))
        try:
            fik, _kitapcilar = _fikstur()
        except Exception as hata:  # noqa: BLE001
            return jsonify({"hata": f"Fikstür alınamadı: {hata}"}), 502
        hedef = fik[fik["Tarih"].dt.strftime("%d.%m.%Y") == tarih]
        simdi = veri.simdi_tr()

        sonuclar, gunluk_kayitlar = [], []
        for idx, r in hedef.iterrows():
            oranlar, maks, ust_alt = _fikstur_oranlari(r)
            if not oranlar:
                continue
            a = analiz.mac_analizi(
                df, r["HomeTeam"], r["AwayTeam"],
                oranlar=tuple(oranlar), elo=_DURUM["elo"],
                ust_alt=tuple(ust_alt) if ust_alt else None,
                lig_ipucu=r["Div"],
            )
            o = a["oneri"]
            en_iyi = _en_iyi_oran(r, o["secim"], maks)
            model_p = a["deger"]["model_p"].get(o["secim"], 0.0)
            ev_max = model_p * en_iyi - 1.0 if en_iyi else o["ev"]
            sonuclar.append(
                {
                    "id": int(idx),
                    "saat": r["Tarih"].strftime("%H:%M"),
                    "lig": r["Div"],
                    "ev": r["HomeTeam"],
                    "dep": r["AwayTeam"],
                    "secim": o["secim"],
                    "oran": float(o["oran"]),
                    "en_iyi_oran": en_iyi,
                    "ev_degeri": float(o["ev"]),
                    "ev_max": float(ev_max),
                    "yildiz": int(o["yildiz"]),
                    "karar": o["karar"],
                    "kalip_n": int(a["kalip"]["n"]) if a["kalip"] else 0,
                }
            )
            if r["Tarih"] > simdi:  # karne dürüstlüğü: yalnız başlamamış maç kaydedilir
                gunluk_kayitlar.append(
                    {
                        "tarih": r["Tarih"].strftime("%d.%m.%Y"),
                        "saat": r["Tarih"].strftime("%H:%M"),
                        "lig": r["Div"],
                        "ev": r["HomeTeam"],
                        "dep": r["AwayTeam"],
                        "secim": o["secim"],
                        "oran": float(o["oran"]),
                        "en_iyi_oran": en_iyi,
                        "ev_degeri": float(o["ev"]),
                        "yildiz": int(o["yildiz"]),
                        "karar": o["karar"],
                        "kayit_zamani": simdi.strftime("%d.%m.%Y %H:%M"),
                    }
                )
        kayit.kaydet(gunluk_kayitlar)
        sonuclar.sort(key=lambda x: x["ev_max"], reverse=True)
        return jsonify(sonuclar)

    def _hucreler_json(h: dict) -> dict:
        cikti = {}
        for anahtar, deger in h.items():
            if isinstance(deger, dict):
                cikti[anahtar] = {"sec": deger["sec"], "p": float(deger["p"])}
            else:
                cikti[anahtar] = deger
        return cikti

    @app.post("/api/tahmin-tablosu")
    def tahmin_tablosu():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        govde = request.get_json(silent=True) or {}
        tarih = str(govde.get("tarih", ""))
        try:
            fik, _kitapcilar = _fikstur()
        except Exception as hata:  # noqa: BLE001
            return jsonify({"hata": f"Fikstür alınamadı: {hata}"}), 502
        hedef = fik[fik["Tarih"].dt.strftime("%d.%m.%Y") == tarih]

        satirlar, gunluk_kayitlar = [], []
        simdi = veri.simdi_tr()
        for idx, r in hedef.iterrows():
            if bool(r.get("analiz_yok", False) is True):
                continue
            oranlar, _maks, _ust_alt = _fikstur_oranlari(r)
            poisson = analiz.poisson_tahmini(df, r["HomeTeam"], r["AwayTeam"], lig_ipucu=r["Div"])
            kalip = analiz.oran_kalibi(df, tuple(oranlar)) if oranlar else None
            hucreler = analiz.tahmin_hucreleri(poisson, kalip)
            h = _hucreler_json(hucreler)
            satirlar.append(
                {
                    "id": int(idx),
                    "saat": r["Tarih"].strftime("%H:%M"),
                    "lig": r["Div"],
                    "ev": r["HomeTeam"],
                    "dep": r["AwayTeam"],
                    "oranlar": oranlar,
                    "kalip_n": int(kalip["n"]) if kalip else 0,
                    "uyari": bool(poisson["uyarilar"]),
                    "hucreler": h,
                }
            )
            if r["Tarih"] > simdi:
                gunluk_kayitlar.append(
                    {
                        "tarih": r["Tarih"].strftime("%d.%m.%Y"),
                        "saat": r["Tarih"].strftime("%H:%M"),
                        "lig": r["Div"],
                        "ev": r["HomeTeam"],
                        "dep": r["AwayTeam"],
                        "ms_sonuc_sec": h["ms_sonuc"]["sec"],
                        "iy_sonuc_sec": h["iy_sonuc"]["sec"],
                        "ms25_sec": h["ms25"]["sec"],
                        "kg_sec": h["kg"]["sec"],
                        "ms_skor_tahmin": h["ms_skor"],
                        "kayit_zamani": simdi.strftime("%d.%m.%Y %H:%M"),
                    }
                )
        kayit.kaydet(gunluk_kayitlar)
        return jsonify(satirlar)

    @app.get("/api/mac-detay")
    def mac_detay():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        try:
            fik, kitapcilar = _fikstur()
        except Exception as hata:  # noqa: BLE001
            return jsonify({"hata": f"Fikstür alınamadı: {hata}"}), 502
        try:
            idx = int(request.args.get("id", -1))
            r = fik.loc[idx]
        except (ValueError, KeyError):
            return jsonify({"hata": "Maç bulunamadı; bülteni yenileyin."}), 404
        if bool(r.get("analiz_yok", False) is True):
            return jsonify({"hata": "Bu maçın takımları istatistik arşivimizin kapsamı dışında; listeleyebiliyor ama analiz edemiyoruz."}), 400

        oranlar, maks, ust_alt = _fikstur_oranlari(r)
        a = analiz.mac_analizi(
            df, r["HomeTeam"], r["AwayTeam"],
            oranlar=tuple(oranlar) if oranlar else None,
            elo=_DURUM["elo"],
            ust_alt=tuple(ust_alt) if ust_alt else None,
            lig_ipucu=r["Div"],
            ornek_sayisi=12,
        )
        j = _mac_json(a)
        j["tahmin"] = _hucreler_json(analiz.tahmin_hucreleri(a["poisson"], a["kalip"]))
        if j["deger"]:
            model_p = a["deger"]["model_p"]
            for satir in j["deger"]["satirlar"]:
                en_iyi = _en_iyi_oran(r, satir["secim"], maks)
                if en_iyi:
                    satir["oran_max"] = en_iyi
                    satir["ev_max"] = model_p[satir["secim"]] * en_iyi - 1.0
        ozet = _mac_ozeti(idx, r, kitapcilar)
        j["fikstur"] = {
            "tarih": r["Tarih"].strftime("%d.%m.%Y"),
            "saat": ozet["saat"],
            "kitapcilar": ozet["kitapcilar"],
            "maks": ozet["maks"],
            "ust_alt": ozet["ust_alt"],
        }
        return jsonify(j)

    @app.post("/api/ayarlar")
    def ayarlar():
        """API anahtarlarını panelden kaydeder (kalıcı diske; env gerektirmez).

        Değerler asla geri okunmaz/gösterilmez; boş değer kaydı siler.
        """
        govde = request.get_json(silent=True) or {}
        degisti = False
        for ad in ("football_data_org_key", "gemini_api_key"):
            if ad in govde:
                veri.ayar_yaz(ad, str(govde.get(ad) or ""))
                degisti = True
        if not degisti:
            return jsonify({"hata": "Kaydedilecek ayar gönderilmedi."}), 400
        # yeni anahtar hemen denensin: kapsama önbelleğini ve bellekteki fikstürü düşür
        try:
            os.remove(os.path.join(veri.VERI_KLASORU, "fixtures_dis.json"))
        except OSError:
            pass
        _DURUM["fikstur"] = None
        return jsonify(
            {
                "tamam": True,
                "gemini": bool(veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")),
                "dis_kapsam": bool(veri.gizli_anahtar("FOOTBALL_DATA_ORG_KEY", "football_data_org_key")),
            }
        )

    @app.post("/api/gemini-yorum")
    def gemini_yorumu():
        """Maçın tam istatistik raporunu Gemini'ye gönderip analist yorumu döndürür.

        Gövde: {"id": fikstür-id}  veya  {"ev","dep","oranlar"?}.
        Anahtar sunucu ortamından okunur (GEMINI_API_KEY) — istemciden asla gelmez.
        """
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        if not veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key"):
            return jsonify({"hata": "Gemini anahtarı tanımlı değil (Bu Hafta sekmesindeki anahtar kutusundan veya GEMINI_API_KEY ile ekleyin)."}), 400
        govde = request.get_json(silent=True) or {}
        try:
            if govde.get("id") is not None:
                fik, _kitapcilar = _fikstur()
                r = fik.loc[int(govde["id"])]
                oranlar, _maks, ust_alt = _fikstur_oranlari(r)
                a = analiz.mac_analizi(
                    df, r["HomeTeam"], r["AwayTeam"],
                    oranlar=tuple(oranlar) if oranlar else None,
                    elo=_DURUM["elo"],
                    ust_alt=tuple(ust_alt) if ust_alt else None,
                    lig_ipucu=r["Div"],
                )
            else:
                ev = veri.takim_bul(df, str(govde.get("ev", "")))
                dep = veri.takim_bul(df, str(govde.get("dep", "")))
                oranlar = _oranlari_dogrula(govde.get("oranlar")) if govde.get("oranlar") else None
                a = analiz.mac_analizi(df, ev, dep, oranlar=oranlar, elo=_DURUM["elo"])
        except (KeyError, ValueError) as hata:
            return jsonify({"hata": f"Maç kurulamadı: {hata}"}), 404

        metin = rapor.rapor_olustur(a, lig_adi=veri.LIGLER.get(a["poisson"]["lig"], ""))
        try:
            from . import gemini_yorum
            return jsonify({"yorum": gemini_yorum.yorum_al(metin)})
        except Exception as hata:  # noqa: BLE001 - kota/anahtar hataları kullanıcıya düz metin döner
            return jsonify({"hata": str(hata)}), 502

    @app.get("/api/sonuclar")
    def sonuclar():
        """Son günlerin oynanmış maçları (skor + istatistik) ve tahmin karnesi."""
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        gun = min(max(int(request.args.get("gun", 7)), 1), 60)
        lig = request.args.get("lig", "").strip()
        simdi = veri.simdi_tr()
        baslangic = simdi.normalize() - pd.Timedelta(days=gun - 1)

        m = df[df["Tarih"] >= baslangic]
        if lig:
            m = m[m["Div"] == lig]
        m = m.sort_values("Tarih", ascending=False).head(400)
        gunluk = kayit.yukle()

        def _secim_tuttu(secim: str | None, r) -> bool | None:
            if not secim:
                return None
            toplam = int(r.FTHG) + int(r.FTAG)
            return {
                "MS1": r.FTR == "H", "MS0": r.FTR == "D", "MS2": r.FTR == "A",
                "ÜST 2.5": toplam > 2.5, "ALT 2.5": toplam < 2.5,
            }.get(secim)

        satirlar = []
        karne = {"toplam": 0, "ms_dogru": 0, "ms_n": 0, "ua_dogru": 0, "ua_n": 0,
                 "kg_dogru": 0, "kg_n": 0, "secim_tutan": 0, "secim_n": 0,
                 "degerli_kar": 0.0, "degerli_n": 0}
        for r in m.itertuples():
            anahtar = f"{r.Tarih.strftime('%d.%m.%Y')}|{r.HomeTeam}|{r.AwayTeam}"
            t = gunluk.get(anahtar)
            toplam_gol = int(r.FTHG) + int(r.FTAG)
            gercek_ms = "1" if r.FTR == "H" else ("X" if r.FTR == "D" else "2")

            tahmin = None
            if t:
                karne["toplam"] += 1
                tahmin = dict(t)
                if t.get("ms_sonuc_sec"):
                    karne["ms_n"] += 1
                    tahmin["ms_dogru"] = t["ms_sonuc_sec"] == gercek_ms
                    karne["ms_dogru"] += int(tahmin["ms_dogru"])
                if t.get("ms25_sec"):
                    karne["ua_n"] += 1
                    tahmin["ua_dogru"] = (t["ms25_sec"] == "Ü") == (toplam_gol > 2.5)
                    karne["ua_dogru"] += int(tahmin["ua_dogru"])
                if t.get("kg_sec"):
                    karne["kg_n"] += 1
                    tahmin["kg_dogru"] = (t["kg_sec"] == "Var") == (r.FTHG > 0 and r.FTAG > 0)
                    karne["kg_dogru"] += int(tahmin["kg_dogru"])
                tuttu = _secim_tuttu(t.get("secim"), r)
                if tuttu is not None:
                    karne["secim_n"] += 1
                    karne["secim_tutan"] += int(tuttu)
                    tahmin["tuttu"] = tuttu
                    if t.get("karar") == "degerli":
                        karne["degerli_n"] += 1
                        karne["degerli_kar"] += (float(t["oran"]) - 1.0) if tuttu else -1.0

            istatistik = {}
            for ad, (hk, ak) in (
                ("şut", ("HS", "AS")), ("isabetli", ("HST", "AST")),
                ("korner", ("HC", "AC")), ("sarı", ("HY", "AY")), ("kırmızı", ("HR", "AR")),
            ):
                hv, av = getattr(r, hk), getattr(r, ak)
                if not (pd.isna(hv) or pd.isna(av)):
                    istatistik[ad] = [int(hv), int(av)]

            satirlar.append(
                {
                    "tarih": r.Tarih.strftime("%d.%m.%Y"),
                    "lig": r.Div,
                    "lig_adi": veri.LIGLER.get(r.Div, r.Div),
                    "ev": r.HomeTeam,
                    "dep": r.AwayTeam,
                    "skor": f"{int(r.FTHG)}-{int(r.FTAG)}",
                    "iy_skor": None if pd.isna(r.HTHG) else f"{int(r.HTHG)}-{int(r.HTAG)}",
                    "ms": gercek_ms,
                    "ust25": toplam_gol > 2.5,
                    "kg": bool(r.FTHG > 0 and r.FTAG > 0),
                    "istatistik": istatistik or None,
                    "tahmin": tahmin,
                }
            )

        # sonucu henüz arşive düşmemiş (bekleyen) kayıtlı tahminler
        eslesen = {f"{s['tarih']}|{s['ev']}|{s['dep']}" for s in satirlar}
        bekleyen = sum(1 for a in gunluk if a not in eslesen)

        return jsonify(
            {
                "satirlar": satirlar,
                "karne": karne,
                "bekleyen": bekleyen,
                "veri_son": _t(df["Tarih"].max()),
            }
        )

    @app.post("/api/backtest")
    def backtest_calistir():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503
        govde = request.get_json(silent=True) or {}
        sezon = min(max(int(govde.get("sezon", 3)), 1), 6)
        lig = govde.get("lig") or None
        if lig and lig not in set(df["Div"].unique()):
            return jsonify({"hata": f"'{lig}' için veri yok."}), 400
        esik = min(max(float(govde.get("esik", 0.04)), 0.0), 0.15)
        sonuc = backtest.backtest_calistir(df, sezon_sayisi=sezon, lig=lig, esik=esik)
        if "hata" in sonuc:
            return jsonify(sonuc), 400
        return jsonify(sonuc)

    @app.get("/api/gecmis-maclar")
    def gecmis_maclar():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"hata": "Önce veriyi güncelleyin."}), 503

        m = df
        takim = request.args.get("takim", "").strip()
        if takim:
            try:
                ad = veri.takim_bul(df, takim)
            except ValueError as hata:
                return jsonify({"hata": str(hata)}), 400
            m = m[(m["HomeTeam"] == ad) | (m["AwayTeam"] == ad)]
        lig = request.args.get("lig", "").strip()
        if lig:
            m = m[m["Div"] == lig]
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)

        satirlar = []
        for s in m.sort_values("Tarih", ascending=False).head(limit).itertuples():
            oranlar = None
            if not (pd.isna(s.oran_ev) or pd.isna(s.oran_berabere) or pd.isna(s.oran_dep)):
                oranlar = [round(float(s.oran_ev), 2), round(float(s.oran_berabere), 2), round(float(s.oran_dep), 2)]
            satirlar.append(
                {
                    "tarih": _t(s.Tarih),
                    "sezon": s.Sezon,
                    "lig": s.Div,
                    "ev": s.HomeTeam,
                    "dep": s.AwayTeam,
                    "skor": f"{int(s.FTHG)}-{int(s.FTAG)}",
                    "sonuc": s.FTR,
                    "oranlar": oranlar,
                }
            )
        return jsonify(satirlar)

    return app


def calistir(host: str = "127.0.0.1", port: int = 8000) -> None:
    uygulama_olustur().run(host=host, port=port, debug=False)
