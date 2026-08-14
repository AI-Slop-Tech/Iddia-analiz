"""Web arayüzü: Flask tabanlı tek sayfa panel + JSON API.

Başlatma: python tahmin.py web  (varsayılan http://127.0.0.1:8000)

Uç noktalar:
  GET  /api/durum          veri seti özeti (yoksa {"veri_yok": true})
  POST /api/guncelle       veriyi indir + belleğe yeniden yükle
  GET  /api/takimlar       takım listesi (arayüz seçicileri için)
  POST /api/oran-analiz    {"oranlar":[1,X,2], "tolerans":0.02} -> oran kalıbı
  POST /api/takim-analiz   {"ev":..,"dep":..,"oranlar":[..]?} -> tam maç analizi
  GET  /api/gecmis-maclar  ?takim=&lig=&limit=  -> oranlarıyla eski maçlar
"""

from __future__ import annotations

import os

import pandas as pd

from . import analiz, veri

_DURUM: dict = {"df": None}


def _df(zorla: bool = False) -> pd.DataFrame:
    if _DURUM["df"] is None or zorla:
        _DURUM["df"] = veri.veriyi_yukle()
    return _DURUM["df"]


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
                "ligler": [
                    {"kod": lig, "ad": veri.LIGLER.get(lig, lig), "mac": int(adet)}
                    for lig, adet in df["Div"].value_counts().items()
                ],
            }
        )

    @app.post("/api/guncelle")
    def guncelle():
        govde = request.get_json(silent=True) or {}
        ozet = veri.indir(govde.get("ligler"))
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

        tolerans = min(max(float(govde.get("tolerans", 0.02)), 0.005), 0.10)
        a = analiz.mac_analizi(df, ev, dep, oranlar=oranlar, tolerans=tolerans)
        return jsonify(_mac_json(a))

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
