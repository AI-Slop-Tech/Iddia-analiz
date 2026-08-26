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
from . import analiz, backtest, kayit, kupon, rapor, rolling, veri, yorum

_DURUM: dict = {
    "df": None, "elo": None, "fikstur": None, "kitapcilar": [],
    "fikstur_zaman": 0.0, "arsiv_zaman": 0.0,
}
_BAKIM = {"basladi": False}
# Bülten yeniden kurulumu tek seferde: kurulum artık dört kaynağı çekiyor,
# eşzamanlı istekler aynı işi tekrarlamasın.
_FIKSTUR_KILIT = threading.Lock()

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
    # Açık dünya arşivi henüz yoksa ilk turda toplanır: yeni kurulumda kullanıcı
    # "Veriyi Güncelle"ye basmadan da analiz kapsamı açılsın. Artımlıdır, bir
    # sonraki turlarda saniyeler sürer.
    try:
        if not veri.acik_arsiv_ozeti()["var"]:
            veri.acik_arsiv_topla()
            _DURUM["df"] = None      # yeni arşivle yeniden yüklensin
            _DURUM["fikstur"] = None
    except Exception:  # noqa: BLE001
        pass

    while True:
        time.sleep(BAKIM_PERIYODU)
        try:
            # açık arşiv her turda tazelenir: dünkü sonuçlar birkaç saniyede iner
            veri.acik_arsiv_topla(zaman_butcesi=60.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            if _DURUM["df"] is not None and time.time() - _DURUM["arsiv_zaman"] > ARSIV_YENILEME_ARALIGI:
                veri.indir()
                # İY hasadı: ek ülke liglerinin ilk yarı skorları yan kaynaklardan
                # toplanıp kalıcı depoya eklenir; ardından yeniden yüklenen arşive işlenir.
                try:
                    veri.iy_hasadi()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    veri.kupa_hasadi()
                except Exception:  # noqa: BLE001
                    pass
                _df(zorla=True)
                _DURUM["fikstur"] = None  # yeni veriyle yeniden okunsun
        except Exception:  # noqa: BLE001 - bakım hatası servisi düşürmesin
            pass
        try:
            if _DURUM["df"] is not None:
                # dosya önbelleğini tazele (6 saatlik TTL'e uyar); bellek kopyasını
                # DOĞRUDAN yazma — dış/AF kapsama katmanlarını atlayarak eziyordu.
                # Sadece süreyi eskit: bir sonraki bülten çağrısı tam zincirle kurar.
                veri.fikstur_indir()
                _DURUM["fikstur_zaman"] = 0.0
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
        "ulke": k.get("ulke"),
        "ornekler": [
            {
                "tarih": _t(o["tarih"]),
                "lig": o["lig"],
                "ayni_ulke": bool(o.get("ayni_ulke")),
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
        "lig_adi": veri.lig_adi(p["lig"]),
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

        # Açılış ısıtması: arşiv arka planda yüklenir ki ilk ziyaretçi ve API
        # istekleri dakikalarca beklemesin (sağlık kontrolü artık /api/ping'te).
        def _isit():
            try:
                _df()
            except Exception:  # noqa: BLE001 — veri henüz yoksa panel zaten yönlendirir
                pass

        threading.Thread(target=_isit, daemon=True, name="iddaa-isitma").start()

    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
        static_url_path="",
    )

    @app.get("/")
    def ana_sayfa():
        return app.send_static_file("index.html")

    @app.get("/api/ping")
    def ping():
        """Hafif sağlık ucu: veri yüklemesine DOKUNMAZ.

        /api/durum ilk çağrıda tüm arşivi yükler (1-3 dk sürebilir); sağlık
        kontrolü ona bağlanınca açılışta konteyner 'unhealthy' sayılıp proxy
        trafiği kesiyordu. Ping her zaman anında döner.
        """
        return jsonify({"tamam": True, "surum": SURUM,
                        "veri_hazir": _DURUM["df"] is not None})

    @app.after_request
    def _onbellek_kapat(yanit):
        # iPhone/Safari eski arayüzü önbellekten açmasın: HTML ve API her zaman taze gelsin
        if yanit.mimetype in ("text/html", "application/json"):
            yanit.headers["Cache-Control"] = "no-store, must-revalidate"
        return yanit

    @app.get("/api/teshis")
    def teshis():
        """Tek bakışta kurulum sağlığı — kullanıcı ekran görüntüsüyle destek isteyebilsin.

        Anahtar değerleri asla dönmez; yalnız var/yok ve son hata metinleri.
        """
        kontroller = []

        def ekle(ad, tamam, detay):
            kontroller.append({"ad": ad, "tamam": bool(tamam), "detay": str(detay)})

        ekle("Yazılım sürümü", True,
             f"v{SURUM} — sayfa başlığında da bu yazmalı; farklıysa tarayıcınız eski sayfayı "
             "önbellekten açıyor demektir (gizli sekmede deneyin)")

        try:
            df = _df()
            ekle("Maç arşivi", True,
                 f"{len(df):,} maç · {df['Div'].nunique()} lig · yükleme: "
                 f"{time.strftime('%d.%m %H:%M', time.localtime(_DURUM['arsiv_zaman']))}")
            sl_n = int((df["Div"] == "ŞL").sum())
            ekle("Avrupa kupası arşivi (ŞL)", sl_n > 0,
                 f"{sl_n} Şampiyonlar Ligi maçı (football-data.org, son ~3 sezon)"
                 if sl_n else "henüz toplanmadı — 🔄 Veriyi Güncelle toplar (fd.org anahtarı gerekir)")
        except FileNotFoundError:
            df = None
            ekle("Maç arşivi", False, "veri indirilmemiş — üstteki 🔄 Veriyi Güncelle'ye basın")

        try:
            fik, _k = _fikstur()
            gelecek = fik[fik["Tarih"] >= veri.simdi_tr().normalize()]
            bayrak = (gelecek["analiz_yok"].fillna(False).astype(bool)
                      if "analiz_yok" in gelecek.columns
                      else pd.Series(False, index=gelecek.index))
            analizli = int((~bayrak).sum())
            ekle("Bülten (fikstür)", len(gelecek) > 0,
                 f"takvimde {len(gelecek)} maç ({analizli} analizli) · kaynak yayını: "
                 f"{veri.fikstur_kaynak_yayini() or 'bilinmiyor'}")
        except Exception as hata:  # noqa: BLE001
            ekle("Bülten (fikstür)", False, f"alınamadı: {str(hata)[:160]}")

        try:
            t = veri.baglanti_testi()
            ekle("Veri kaynağı erişimi (football-data.co.uk)", bool(t.get("tamam")),
                 (f"{t.get('sure_ms')} ms · vekil: {t.get('vekil') or 'yok'}" if t.get("tamam")
                  else f"hata: {t.get('hata')} · vekil: {t.get('vekil') or 'yok'} — Türkiye'deki "
                       "sunucularda IDDAA_PROXY / IDDAA_KAYNAK_TABAN gerekir"))
        except Exception as hata:  # noqa: BLE001
            ekle("Veri kaynağı erişimi (football-data.co.uk)", False, str(hata)[:160])

        yollar = dict(veri.AG_YOLLARI)
        if yollar:
            vekilli = sorted(h for h, y in yollar.items() if y == "vekil")
            duz = [h for h, y in yollar.items() if y == "duz"]
            anahtarli = [h for h, y in yollar.items() if y == "anahtar"]
            ekle("Ağ yolu (doğrudan / vekil)", True,
                 ((f"vekilden geçen {len(vekilli)}: {', '.join(vekilli[:4])}"
                   if vekilli else "engellenen adres yok, hepsi doğrudan")
                  + f" · doğrudan: {len(duz)}"
                  + (f" · anahtarlı servis (vekil KULLANILMAZ): {len(anahtarli)}"
                     if anahtarli else "")
                  + (" · IDDAA_PROXY tanımlı değil" if not veri.proxy_ayari()
                     else " · engellenen adres kendiliğinden vekile düşer")))

        fd_var = bool(veri.gizli_anahtar("FOOTBALL_DATA_ORG_KEY", "football_data_org_key"))
        d = veri.DIS_SON_DURUM
        if not fd_var:
            ekle("football-data.org (ŞL + büyük ligler)", False,
                 "anahtar kayıtlı değil — Bu Hafta sekmesindeki 🔑 kutudan ekleyin")
        else:
            ekle("football-data.org (ŞL + büyük ligler)", not d.get("hata"),
                 f"hata: {d['hata']}" if d.get("hata")
                 else f"son çekim {d.get('zaman') or '—'} · {d.get('mac') if d.get('mac') is not None else '?'} maç geldi")

        d_pin = veri.PINNACLE_SON_DURUM
        if d_pin.get("kapali"):
            ekle("Pinnacle açık oranları", False,
                 "IDDAA_PINNACLE=0 ile kapatılmış — bültenin büyük kısmı oransız kalır")
        else:
            ekle("Pinnacle açık oranları", bool(d_pin.get("oranli")),
                 (f"son bakış {d_pin.get('zaman') or '—'} · kaynakta {d_pin.get('mac') or 0} maç, "
                  f"{d_pin.get('oranli') or 0} tanesinde 1X2"
                  + (f" · son hata: {d_pin['hata']}" if d_pin.get("hata") else ""))
                 if d_pin.get("zaman")
                 else "henüz denenmedi — bülten kurulunca dolar (anahtar gerekmez)")

        oa_var = bool(veri.gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key"))
        if not oa_var:
            ekle("odds-api.io (Bet365 İY/MS oranları)", False,
                 "anahtar kayıtlı değil — 🎭 Sürpriz sekmesindeki 🔑 kutudan ekleyin")
        else:
            try:
                ligler = veri._oddsapi_ligler()
                ekle("odds-api.io (Bet365 İY/MS oranları)", True,
                     f"anahtar çalışıyor · {len(ligler)} lig erişilebilir"
                     + (f" · son hata: {veri.IYMS_SON_DURUM['hata']}" if veri.IYMS_SON_DURUM.get("hata") else ""))
            except Exception as hata:  # noqa: BLE001
                ekle("odds-api.io (Bet365 İY/MS oranları)", False, f"erişim hatası: {str(hata)[:160]}")

        af_var = bool(veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key"))
        if not af_var:
            ekle("API-Football (14+ kitapçı, ŞL/UEL oranları)", False,
                 "anahtar kayıtlı değil — 🎭 sekmesindeki kutudan ekleyin (ücretsiz: dashboard.api-football.com)")
        else:
            d_af = veri.AF_SON_DURUM
            pencere = (" · Free plan penceresi: bugün ±1 gün (tüm haftayı 14 kitapçıyla taramak için Pro $19)"
                       if d_af.get("pencere_free") else "")
            ekle("API-Football (14+ kitapçı, ŞL/UEL oranları)", not d_af.get("hata"),
                 (f"hata: {d_af['hata']}" if d_af.get("hata")
                  else f"bağlı · bugün {d_af.get('bugun_istek', 0)}/90 istek kullanıldı{pencere}"))

        try:
            a = veri.acik_arsiv_ozeti()
            ekle("Açık dünya arşivi (analiz kapsamı)", bool(a.get("mac")),
                 (f"{a['mac']:,} maç · {a['lig']} turnuva · {a['gun']}/{a['hedef_gun']} gün "
                  f"toplandı · analiz eşiğini geçen lig: {len(veri.ACIK_LIG_TAKIMLARI)}")
                 if a.get("mac") else
                 "henüz toplanmadı — 🔄 Veriyi Güncelle bir yıllık dünya sonuçlarını indirir "
                 "(anahtar gerekmez, ~1.5 dk)")
        except Exception as hata:  # noqa: BLE001
            ekle("Açık dünya arşivi (analiz kapsamı)", False, str(hata)[:160])

        d_acik = veri.ACIK_SON_DURUM
        if d_acik.get("kapali"):
            ekle("Açık dünya fikstürü (kupa + eleme maçları)", False,
                 "IDDAA_ACIK_FIKSTUR=0 ile kapatılmış — kupa/eleme maçları bültene girmez")
        else:
            ekle("Açık dünya fikstürü (kupa + eleme maçları)",
                 bool(d_acik.get("mac")),
                 (f"son bakış {d_acik.get('zaman') or '—'} · {d_acik.get('gun') or 0}/8 gün hazır "
                  f"({d_acik.get('yeni') or 0} gün yeni çekildi) · "
                  f"{d_acik.get('mac') if d_acik.get('mac') is not None else '?'} maç görüldü"
                  + (f" · son hata: {d_acik['hata']}" if d_acik.get("hata") else ""))
                 if d_acik.get("zaman")
                 else "henüz denenmedi — bülten kurulunca dolar (anahtar gerekmez)")

        ekle("Gemini yorumu", bool(veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")),
             "bağlı" if veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")
             else "anahtar yok (isteğe bağlı özellik)")

        return jsonify({"surum": SURUM, "kontroller": kontroller})

    @app.get("/api/af-test")
    def af_test():
        """API-Football anahtarını kaynağın kendi /status yanıtıyla sınar."""
        try:
            return jsonify(veri.af_tanilama())
        except Exception as hata:  # noqa: BLE001
            return jsonify({"sonuc": "Test çalıştırılamadı.", "hata": str(hata)[:300]}), 500

    @app.get("/api/durum")
    def durum():
        try:
            df = _df()
        except FileNotFoundError:
            return jsonify({"veri_yok": True, "surum": SURUM})
        return jsonify(
            {
                "veri_yok": False,
                "surum": SURUM,
                "toplam_mac": int(len(df)),
                "ilk_tarih": _t(df["Tarih"].min()),
                "son_tarih": _t(df["Tarih"].max()),
                "oran_kapsami": round(float(df["oran_ev"].notna().mean()), 3),
                "surum": SURUM,
                "gemini": bool(veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")),
                "dis_kapsam": bool(veri.gizli_anahtar("FOOTBALL_DATA_ORG_KEY", "football_data_org_key")),
                "piyasa_iyms": bool(veri.gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key"))
                               or bool(veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key")),
                "apifootball": bool(veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key")),
                "kapsam": _DURUM.get("kapsam"),
                "af_durum": dict(veri.AF_SON_DURUM),
                "acik_durum": dict(veri.ACIK_SON_DURUM),
                "pinnacle_durum": dict(veri.PINNACLE_SON_DURUM),
                "oddsapi": bool(veri.gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key")),
                "veri_zamani": time.strftime("%d.%m %H:%M", time.localtime(_DURUM["arsiv_zaman"])),
                "ligler": [
                    {"kod": lig, "ad": veri.lig_adi(lig), "mac": int(adet)}
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
            veri.iy_hasadi()  # ek ülke İY skorları yan kaynaklardan depoya
        except Exception:  # noqa: BLE001
            pass
        try:
            veri.kupa_hasadi()  # ŞL sonuç arşivi (football-data.org)
        except Exception:  # noqa: BLE001
            pass
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
        takma = veri.takma_ad_haritasi()
        return jsonify(
            [
                {
                    "ad": s.Takim,
                    "lig": s.lig,
                    "mac": int(s.mac),
                    "aktif": bool(s.son_mac >= aktif_sinir),
                    # arayüzdeki aramalı seçici bunları da tarar: kullanıcı
                    # "başakşehir" yazınca arşivdeki "Buyuksehyr" bulunsun
                    "ara": takma.get(s.Takim, []),
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

    def _son_lig_haritasi(df: pd.DataFrame) -> dict:
        """Takım → arşivdeki EN SON lig kodu. Bulanık eşleşme koruması için:
        Hollanda maçındaki "NEC", Meksika'nın Necaxa'sına bağlanmasın."""
        return {
            s["Takim"]: s["Div"]
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
            )
        }

    def _takim_esitleyici():
        """İki ismin aynı takım olup olmadığını difflib'siz söyleyen hızlı ölçüt.

        Kapsama katmanları bunu TEKİLLEŞTİRME için kullanır; yanlış "aynı"
        demek maçı bültenden düşürür. Eskiden tek ortak parça yetiyordu ve
        "Real Betis – Real Madrid", listedeki "Real Madrid – Real Sociedad"
        ile aynı sayılıp eleniyordu ("real" ikisinde de var). Artık tek ortak
        parça ancak ayırt ediciyse (uzun, iki ad da kısa) kabul edilir.
        """
        bellek: dict = {}
        genel = {"real", "atletico", "athletic", "sporting", "olympique", "olympiakos",
                 "dynamo", "dinamo", "union", "racing", "rapid", "standard", "national",
                 "nacional", "internacional", "america", "united", "city", "town",
                 "county", "rovers", "albion", "wanderers", "sport", "santa", "san"}

        def parcala(ad: str) -> frozenset:
            if ad not in bellek:
                bellek[ad] = frozenset(veri._oddsapi_takim_parcalari(ad))
            return bellek[ad]

        def ayni(a: str, b: str) -> bool:
            A, B = parcala(a), parcala(b)
            if not A or not B:
                return False
            ortak = A & B
            if not ortak:
                return False
            if len(ortak) >= 2:
                return True
            tek = next(iter(ortak))
            return len(tek) >= 5 and len(A) <= 2 and len(B) <= 2 and tek not in genel

        return ayni

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
        cozucu = veri.takim_cozucu(df)  # harita bir kez kurulur (isim başına değil)

        def _esle(ad: str):
            if ad not in hafiza:
                try:
                    hafiza[ad] = cozucu(ad)
                except ValueError:
                    hafiza[ad] = None
            return hafiza[ad]

        # bulanık eşleşme koruması: takımın arşivdeki güncel ligi, maçın
        # ligiyle uyuşmalı (ör. Hollanda maçındaki "NEC", Meksika'nın
        # Necaxa'sına bağlanmasın). Lig kodu arşivde yoksa (ŞL, CLI gibi
        # ligler-arası turnuvalar) kontrol atlanır.
        son_lig = _son_lig_haritasi(df)

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

    # İki günlük pencere (bugün+yarın) tipik olarak 400-550 maç getiriyor;
    # 400 tavanı yarını kırpıyordu. Tavan yalnız listeleme maliyetini
    # sınırlar, tarama zaten gün gün çalışır.
    AF_KAPSAM_SINIRI = 800   # dünya fikstürü tavanı (bugün + yarın)

    def _af_kapsami_ekle(df: pd.DataFrame, fik: pd.DataFrame) -> pd.DataFrame:
        """API-Football günlük dünya fikstürünü bültene 3. katman olarak katar.

        CSV kaynağı kupa/eleme maçlarını bilmez, football-data.org ücretsizi
        eleme-playoff vermez — Fenerbahçe'nin Avrupa maçları bu yüzden
        görünmüyordu. AF'nin gün penceresi (Free: bugün ±1) buradan girer.
        Kapsam: varsayılan olarak günün TÜM dünya fikstürü listelenir. Eskiden
        yalnız beyaz listedeki turnuvalar (ŞL/UEL/KL/T1/TK) veya iki takımı da
        arşive oturan maçlar eklenirdi; kullanıcı "bütün maçları dökmüyor"
        dediği için bu sel önleme kaldırıldı. Takımı arşivde çözülemeyen maç
        da listelenir, "analiz_yok" ile işaretlenir — satırda nedeni yazar.
        Üst sınır 120'den AF_KAPSAM_SINIRI'na çıkarıldı.
        """
        if not veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key"):
            return fik
        simdi = veri.simdi_tr()
        gunler_utc = sorted({(simdi - pd.Timedelta(hours=3)).strftime("%Y-%m-%d"),
                             (simdi + pd.Timedelta(hours=21)).strftime("%Y-%m-%d")})
        kayitlar = []
        for g in gunler_utc:
            try:
                kayitlar.extend(veri._af_gun_fiksturu(g))
            except Exception as h:  # noqa: BLE001 — pencere/kota hatası bülteni düşürmesin
                # Hata kapsam kutusunda GÖRÜNSÜN: kullanıcı "anahtar kayıtlı
                # ama AF 0 maç" durumunda sebebi (askı, kota, 429...) ekrandan
                # okuyabilsin — daha önce sessizce yutuluyordu.
                veri.AF_SON_DURUM["hata"] = str(h)[:160]
                try:
                    # kota/ağ yoksa bayat önbellek hiç yoktan iyidir
                    kayitlar.extend(veri._af_gun_fiksturu(g, sadece_onbellek=True))
                except Exception:  # noqa: BLE001
                    continue
        if not kayitlar:
            return fik

        hafiza: dict = {}
        cozucu = veri.takim_cozucu(df)  # harita bir kez kurulur (isim başına değil)

        def _esle(ad: str):
            if ad not in hafiza:
                try:
                    hafiza[ad] = cozucu(ad)
                except ValueError:
                    hafiza[ad] = None
            return hafiza[ad]

        son_lig = _son_lig_haritasi(df)

        _ayni_takim = _takim_esitleyici()

        gun_haritasi: dict = {}
        for r in fik.itertuples():
            gun_haritasi.setdefault(r.Tarih.date(), []).append((str(r.HomeTeam), str(r.AwayTeam)))

        # dünya fikstüründeki yüzlerce yabancı isim için difflib'siz hızlı çözücü
        hafiza.clear()
        cozucu = veri.takim_cozucu_onbellekli(df, hizli=True)   # harita istek boyu paylaşılır

        satirlar = []
        for m in kayitlar:
            if len(satirlar) >= AF_KAPSAM_SINIRI:
                break
            if m.get("durum") in ("PST", "CANC", "ABD", "AWD", "WO"):
                continue
            try:
                t = pd.Timestamp(str(m.get("ts", "")))
                if t.tzinfo is not None:
                    t = t.tz_convert("UTC").tz_localize(None)
                tarih = t + pd.Timedelta(hours=3)  # UTC → TR
            except Exception:  # noqa: BLE001
                continue
            if tarih < simdi.normalize():
                continue
            kod = veri.AF_LIG_ESLEME.get(m.get("lig_id"))
            ev, dep = _esle(str(m.get("ev") or "")), _esle(str(m.get("dep") or ""))
            if kod is None:
                # Beyaz liste dışı maçlar da listelenir. Takımlar arşive
                # oturuyorsa lig kodu onlardan türetilir ve maç tam analiz
                # alır; oturmuyorsa "AF" koduyla yalnız listelenir.
                kod = son_lig.get(ev, "AF") if ev else "AF"
                if kod in veri.LIGLER and (son_lig.get(ev) != son_lig.get(dep)):
                    kod = "AF"  # ligler-arası (kupa benzeri): kod iddiasında bulunma
            analiz_var = bool(ev and dep)
            # aynı gün + aynı ikili bültende zaten varsa (CSV/fd.org) ekleme
            ayni_gun = gun_haritasi.get(tarih.date(), [])
            e_ad, d_ad = ev or str(m.get("ev")), dep or str(m.get("dep"))
            if any(_ayni_takim(e_ad, me) and _ayni_takim(d_ad, md) for me, md in ayni_gun):
                continue
            gun_haritasi.setdefault(tarih.date(), []).append((e_ad, d_ad))
            satirlar.append(
                {
                    "Tarih": tarih,
                    "Div": kod,
                    "HomeTeam": e_ad,
                    "AwayTeam": d_ad,
                    "LigAdi": veri.AF_LIG_ADLARI.get(kod)
                              or f"{m.get('ulke') or ''} {m.get('lig_ad') or ''}".strip(),
                    "analiz_yok": not analiz_var,
                }
            )
        if not satirlar:
            return fik
        birlesik = pd.concat([fik, pd.DataFrame(satirlar)], ignore_index=True)
        return birlesik.sort_values("Tarih").reset_index(drop=True)

    # Açık kapsam penceresi ve süzgeçleri.
    #
    # LİSTELEME geniştir, ANALİZ dardır — ikisi ayrı kapıdan geçer.
    #
    # Listeleme: kaynak cumartesileri 800+ maç görüyor, büyük kısmı bölgesel
    # amatör lig; hepsini dökmek bülteni okunmaz yapar. Ayıklama beslemenin
    # KENDİ önem sırasına dayanır (29.08.2026 ölçümü: 0=Premier League,
    # 1=LaLiga, 2=Bundesliga, 3=Serie A, 4=Ligue 1 ... 9=Süper Lig, ardından
    # alfabetik ülke bloğu). Bir maç şu üç koşuldan biriyle listelenir:
    #   1) turnuva Türkiye'nin ya da bir konfederasyonun (UEFA/CONMEBOL...) —
    #      Türkiye Kupası alfabetik blokta "T"de kalıp tavana takılmasın diye,
    #   2) takımlardan en az biri istatistik arşivinde var,
    #   3) turnuva günün ilk ACIK_ASAMA_SINIRI turnuvası arasında.
    # Analiz: aşağıdaki "eşleşme güveni" bölümü karar verir; geçemeyen maç
    # yine listelenir, yalnız "analiz yok" etiketiyle.
    ACIK_GUN_SAYISI = 8       # bugün + 7 gün
    # Kaynak yavaşladığında bülten kurulumu 8 × 20 sn kilitlenmesin: bütçe
    # dolunca kalan günler önbellekten gelir, eksik gün sonraki kurulumda
    # tamamlanır.
    ACIK_ZAMAN_BUTCESI = 25.0
    ACIK_ASAMA_SINIRI = 45    # "ciddi" turnuva sayılan besleme sırası
    # Gün tavanı ikiye ayrıldı: ANALİZ ALAN maç asla tavana takılmaz (bültenin
    # değerli kısmı odur), tavan yalnız "yalnız listeleme" satırlarını sınırlar.
    # Tek tavan varken cumartesileri gerçek ligler kesiliyordu.
    ACIK_GUN_SINIRI = 500        # gün başına sert üst sınır (emniyet freni)
    ACIK_LISTELEME_SINIRI = 140  # analiz almayan satırlar için tavan
    # ertelenen/iptal maçlar bültene girmez
    _ACIK_ATIL_DURUM = ("postp", "canc", "aband", "abd", "awarded", "susp", "delayed")

    def _acik_kapsami_ekle(df: pd.DataFrame, fik: pd.DataFrame) -> pd.DataFrame:
        """Anahtarsız dünya fikstürünü bültene 4. katman olarak katar.

        Diğer üç katmanın hiçbiri kupa/eleme maçlarını haftanın tamamı için
        veremiyor (ayrıntı: veri.py'deki "Açık dünya fikstürü" başlığı). Bu
        katman anahtar, kota ve vekil gerektirmeden takvimi doldurur; oran
        getirmez. Takımlar arşivde çözülüyorsa maç tam analiz alır, yoksa
        "yalnız listeleme" olarak görünür.
        """
        durum = veri.ACIK_SON_DURUM
        durum.update({"zaman": veri.simdi_tr().strftime("%H:%M"), "gun": 0,
                      "mac": 0, "hata": None,
                      "kapali": veri.acik_fikstur_kapali()})
        if durum["kapali"]:
            return fik

        simdi = veri.simdi_tr()
        gunler = [(simdi.normalize() + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
                  for i in range(ACIK_GUN_SAYISI)]
        kayitlar, cekim = veri.acik_fiksturu_getir(gunler, zaman_butcesi=ACIK_ZAMAN_BUTCESI)
        durum.update(cekim)
        if not kayitlar:
            return fik

        # önem sırası: önce Türkiye/konfederasyon, sonra beslemenin kendi sırası
        kayitlar.sort(key=lambda m: (0 if veri.acik_oncelikli(m.get("ulke")) else 1,
                                     m.get("sira", 9999), str(m.get("ts"))))

        son_lig = _son_lig_haritasi(df)
        _ayni_takim = _takim_esitleyici()
        cozucu = veri.takim_cozucu_onbellekli(df, hizli=True)   # harita istek boyu paylaşılır
        hafiza: dict = {}

        def _esle(ad: str):
            if ad not in hafiza:
                try:
                    hafiza[ad] = cozucu(ad)
                except ValueError:
                    hafiza[ad] = None
            return hafiza[ad]

        gun_haritasi: dict = {}
        for r in fik.itertuples():
            gun_haritasi.setdefault(r.Tarih.date(), []).append((str(r.HomeTeam), str(r.AwayTeam)))

        gun_adedi: dict = {}
        gun_listeleme: dict = {}
        satirlar = []
        for m in kayitlar:
            if str(m.get("durum", "")).lower().startswith(_ACIK_ATIL_DURUM):
                continue
            try:
                tarih = pd.to_datetime(str(m.get("ts")), format="%Y%m%d%H%M%S")
            except (ValueError, TypeError):
                continue
            if tarih < simdi.normalize():
                continue
            gun = tarih.date()
            if gun_adedi.get(gun, 0) >= ACIK_GUN_SINIRI:
                continue

            onemli = veri.acik_oncelikli(m.get("ulke")) or m.get("sira", 9999) < ACIK_ASAMA_SINIRI
            ev, dep = _esle(str(m.get("ev") or "")), _esle(str(m.get("dep") or ""))
            if not (onemli or ev or dep):
                continue   # ne ciddi turnuva ne de arşivde tanıdığımız takım

            # --- eşleşme: önce AÇIK ARŞİV (tam eşleşme) ----------------------
            # Açık arşiv bültenle AYNI beslemeden geliyor: takım adları birebir
            # aynı. Bu yüzden burada bulanık eşleştirme yok — ad tam tutar ya
            # da tutmaz. Yanlış eşleşme riski sıfır, kapsam ise football-data'nın
            # 38 liginden 149 ligine çıkıyor (Ekvador, Peru, Sırbistan, Mısır,
            # Kore, Özbekistan... hepsi analiz alıyor).
            arsiv_kod = veri.acik_arsiv_kodu(m.get("ulke"), m.get("lig"))
            kod_lig = veri.acik_lig_kodu(m.get("ulke"), m.get("lig"))
            kupa = veri.acik_kupa_mi(m.get("lig"), m.get("lig_ad"))
            if arsiv_kod and arsiv_kod in veri.ACIK_LIG_TAKIMLARI:
                takimlar = veri.ACIK_LIG_TAKIMLARI[arsiv_kod]
                a_ev = veri.acik_arsiv_takimi(m.get("ulke"), str(m.get("ev") or ""))
                a_dep = veri.acik_arsiv_takimi(m.get("ulke"), str(m.get("dep") or ""))
                ev = a_ev if a_ev in takimlar else None
                dep = a_dep if a_dep in takimlar else None
            elif veri.acik_ulke_mu(m.get("ulke")):
                # Açık arşivde olmayan ülke turnuvası: eski bulanık yol, ama iki
                # kapıyla. İsim eşleştirme yabancı adlarda yanılıp bültende
                # "Nautico – Ath Bilbao", "Tigers FC – Juventus" gibi var olmayan
                # maçlar üretiyordu.
                # 1) Turnuva, arşivimizde takip ettiğimiz bir lige ya da o
                #    ülkenin kupasına ait olmalı.
                # 2) Takımın arşivdeki ligi de o ülkenin olmalı.
                ulke_kodlari = veri.acik_ulke_kodlari(m.get("ulke"))
                guvenli = bool(ulke_kodlari) and (kupa or kod_lig in (ulke_kodlari or ()))
                if not guvenli:
                    ev = dep = None
                else:
                    if ev and son_lig.get(ev) not in ulke_kodlari:
                        ev = None
                    if dep and son_lig.get(dep) not in ulke_kodlari:
                        dep = None
            # kalan durum: uluslararası turnuva (UEFA/CONMEBOL) — takım her
            # ülkeden gelebilir, bulanık eşleşme olduğu gibi kalır

            kod = kod_lig or arsiv_kod
            if kod is None and kupa:
                kod = "KUPA"   # kupada iki takım farklı liglerden gelir: lig
                               # kodu iddiasında bulunma, rozet "KUPA" olsun
            if kod is None and ev and dep and son_lig.get(ev) == son_lig.get(dep):
                kod = son_lig.get(ev) or "DÜNYA"
            kod = kod or "DÜNYA"
            analiz_var = bool(ev and dep)
            if analiz_var and kod in veri.LIGLER:
                # aynı denetimin lig düzeyi: takımın güncel ligi maçın ligiyle
                # uyuşmuyorsa yalnız listele
                if son_lig.get(ev) != kod or son_lig.get(dep) != kod:
                    analiz_var = False

            if not analiz_var and gun_listeleme.get(gun, 0) >= ACIK_LISTELEME_SINIRI:
                continue   # tavan yalnız analiz almayan satırlara uygulanır

            e_ad, d_ad = ev or str(m.get("ev")), dep or str(m.get("dep"))
            ayni_gun = gun_haritasi.get(gun, [])
            if any(_ayni_takim(e_ad, me) and _ayni_takim(d_ad, md) for me, md in ayni_gun):
                continue  # bültende zaten var (CSV/fd.org/AF katmanı)
            gun_haritasi.setdefault(gun, []).append((e_ad, d_ad))
            gun_adedi[gun] = gun_adedi.get(gun, 0) + 1
            if not analiz_var:
                gun_listeleme[gun] = gun_listeleme.get(gun, 0) + 1
            satirlar.append(
                {
                    "Tarih": tarih,
                    "Div": kod,
                    "HomeTeam": e_ad,
                    "AwayTeam": d_ad,
                    "LigAdi": veri.AF_LIG_ADLARI.get(kod) or m.get("lig_ad")
                              or veri.lig_adi(kod),
                    "analiz_yok": not analiz_var,
                }
            )
        if not satirlar:
            return fik
        birlesik = pd.concat([fik, pd.DataFrame(satirlar)], ignore_index=True)
        return birlesik.sort_values("Tarih").reset_index(drop=True)

    def _pinnacle_fuzyonu(fik: pd.DataFrame) -> int:
        """Oransız satırlara Pinnacle'ın açık fiyatlarını işler.

        Anahtar gerektirmez, tek istekle bütün takvimi fiyatlandırır. Yalnız
        ORANSIZ satırlara dokunur: fixtures.csv'de oran varsa o kalır, çünkü
        orada 7 kitapçı var ve konsensüs motoru fiyat farkından beslenir.
        Pinnacle tek kitapçı olduğu için konsensüs = en iyi fiyat; sapma
        sinyali üretmez (üretemez), ama model-fiyat kıyası ve oran kalıbı
        katmanı bu maçlarda da çalışır.
        """
        if "OranKaynak" not in fik.columns:
            fik["OranKaynak"] = None
        try:
            kayitlar = veri.pinnacle_oranlari()
        except Exception as h:  # noqa: BLE001
            veri.PINNACLE_SON_DURUM["hata"] = str(h)[:160]
            return 0
        if not kayitlar:
            return 0
        indeks = veri.pinnacle_indeksi(kayitlar)
        oransiz = fik["oran_ev"].isna() if "oran_ev" in fik.columns else None
        if oransiz is None or not oransiz.any():
            return 0
        dolan = 0
        for idx in fik.index[oransiz]:
            r = fik.loc[idx]
            k = veri.pinnacle_esle(indeks, r["HomeTeam"], r["AwayTeam"], r["Tarih"])
            if not k:
                continue
            fik.loc[idx, ["oran_ev", "oran_berabere", "oran_dep"]] = k["ms"]
            fik.loc[idx, ["oran_max_ev", "oran_max_berabere", "oran_max_dep"]] = k["ms"]
            if k.get("ust_alt"):
                fik.loc[idx, ["oran_ust25", "oran_alt25"]] = k["ust_alt"]
                fik.loc[idx, ["oran_ust25_maks", "oran_alt25_maks"]] = k["ust_alt"]
            fik.loc[idx, "OranKaynak"] = "pinnacle"
            dolan += 1
        return dolan

    def _piyasa_fuzyonu_uygula(fik: pd.DataFrame) -> int:
        """Önbellekteki piyasa 1X2'lerini oransız fikstür satırlarına işler.

        Yalnız önbellek okur (ağ yok, hızlı); ağı arka plan ısıtıcısı yapar.
        Doldurulan satırlar OranKaynak="canli" ile işaretlenir — bülten, tablo
        ve detay oranları doğal kolonlardan görür.
        """
        if "OranKaynak" not in fik.columns:
            fik["OranKaynak"] = None
        simdi = veri.simdi_tr()
        pencere = (fik["Tarih"] >= simdi.normalize()) & \
                  (fik["Tarih"] < simdi.normalize() + pd.Timedelta(days=2))
        oransiz = pencere & fik["oran_ev"].isna() if "oran_ev" in fik.columns else pencere
        dolan = 0
        for idx in fik.index[oransiz]:
            r = fik.loc[idx]
            try:
                pk = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                      lig_adi=r.get("LigAdi"),
                                      sadece_onbellek=True)
            except Exception:  # noqa: BLE001
                continue
            if not (pk and pk.get("ms")):
                continue
            fik.loc[idx, ["oran_ev", "oran_berabere", "oran_dep"]] = pk["ms"]
            if pk.get("ms_maks"):
                fik.loc[idx, ["oran_max_ev", "oran_max_berabere", "oran_max_dep"]] = pk["ms_maks"]
            if pk.get("ust_alt25"):
                fik.loc[idx, ["oran_ust25", "oran_alt25"]] = pk["ust_alt25"]
            fik.loc[idx, "OranKaynak"] = "canli"
            dolan += 1
        return dolan

    # Arka plan oran ısıtması: süre VE istek tavanı birlikte. Ücretsiz oran
    # planları günde 500 (odds-api) / 100 (API-Football) istek veriyor; bülten
    # 15 dakikada bir yeniden kurulduğu için tavansız bir ısıtma günlük kotayı
    # öğleden önce bitirebilir.
    PIYASA_ISITMA_SANIYE = 150
    PIYASA_ISITMA_TAVANI = 90

    def _piyasa_isit_baslat(fik: pd.DataFrame) -> None:
        """Bugün+yarının oransız maçları için piyasa oranlarını ARKA PLANDA çeker.

        Kullanıcı hiçbir düğmeye basmadan önbellek dolar; bitince fikstür süresi
        eskitilir ve bir sonraki bülten çağrısı oranları satırlara işler.
        """
        if _DURUM.get("piyasa_isitma"):
            return
        if not (veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key")
                or veri.gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key")):
            return
        simdi = veri.simdi_tr()
        pencere = (fik["Tarih"] >= simdi.normalize()) & \
                  (fik["Tarih"] < simdi.normalize() + pd.Timedelta(days=2))
        if "LigAdi" not in fik.columns:
            fik["LigAdi"] = None
        oransiz = fik.loc[pencere & fik["oran_ev"].isna(),
                          ["HomeTeam", "AwayTeam", "Div", "Tarih", "LigAdi"]]
        if oransiz.empty:
            return
        hedefler = [tuple(x) for x in oransiz.itertuples(index=False)]
        # kota önce en kıymetli maçlara: Avrupa kupaları + Süper Lig başa.
        # Açık dünya fikstürü bültene yüzlerce maç ekledi; ücretsiz oran
        # planları (odds-api 500/gün, AF 100/gün) hepsine yetmez, o yüzden
        # sıralama ve ISTEK_TAVANI birlikte çalışır.
        oncelik = ("ŞL", "EL", "KL", "T1", "TK")
        hedefler.sort(key=lambda h: (h[2] not in oncelik, h[3]))
        _DURUM["piyasa_isitma"] = True

        def _isit():
            try:
                son = time.time() + PIYASA_ISITMA_SANIYE
                for i, (ev, dep, lig, tarih, lig_adi) in enumerate(hedefler):
                    if time.time() > son or i >= PIYASA_ISITMA_TAVANI:
                        break
                    try:
                        # ağ; önbelleğe yazar
                        veri.iyms_piyasa(ev, dep, lig, tarih, lig_adi=lig_adi)
                    except Exception:  # noqa: BLE001
                        continue
                _DURUM["fikstur_zaman"] = 0.0  # sıradaki bülten oranları işlesin
            finally:
                _DURUM["piyasa_isitma"] = False

        threading.Thread(target=_isit, daemon=True, name="iddaa-piyasa-isitma").start()

    def _dun_arsivden_ekle(df, fik):
        """Dünkü OYNANMIŞ maçları arşivden takvime ekler.

        Kaynak fikstür dosyası oynanan maçları listeden düşürüyor; oysa
        kullanıcı radarın/taramanın dünkü seçimlerini gerçek sonuçla
        karşılaştırmak istiyor. Arşiv satırları fikstür şemasına çevrilip
        eklenir (maç öncesi oranlarıyla), sonuçları _gercek_sonuc doldurur.
        """
        simdi = veri.simdi_tr()
        bas = simdi.normalize() - pd.Timedelta(days=1)
        dun = df[(df["Tarih"] >= bas) & (df["Tarih"] < simdi.normalize())]
        if dun.empty:
            return fik
        varolan = set(zip(fik["HomeTeam"].astype(str), fik["AwayTeam"].astype(str)))
        dun = dun[~pd.Series(list(zip(dun["HomeTeam"].astype(str), dun["AwayTeam"].astype(str))),
                             index=dun.index).isin(varolan)]
        if dun.empty:
            return fik
        y = pd.DataFrame({
            "Div": dun["Div"].astype(str), "Tarih": dun["Tarih"],
            "HomeTeam": dun["HomeTeam"].astype(str), "AwayTeam": dun["AwayTeam"].astype(str),
            "oran_ev": dun["oran_ev"], "oran_berabere": dun["oran_berabere"],
            "oran_dep": dun["oran_dep"],
            "oran_max_ev": dun["oran_ev_maks"].fillna(dun["oran_ev"]),
            "oran_max_berabere": dun["oran_berabere_maks"].fillna(dun["oran_berabere"]),
            "oran_max_dep": dun["oran_dep_maks"].fillna(dun["oran_dep"]),
            "oran_ust25": dun["oran_ust25"], "oran_alt25": dun["oran_alt25"],
            "oran_ust25_maks": dun["oran_ust25_maks"].fillna(dun["oran_ust25"]),
            "oran_alt25_maks": dun["oran_alt25_maks"].fillna(dun["oran_alt25"]),
            "analiz_yok": False, "OranKaynak": "arsiv", "oynandi": True,
        })
        return pd.concat([fik, y], ignore_index=True).sort_values("Tarih").reset_index(drop=True)

    def _fikstur(yenile: bool = False, bellek_ttl: bool = False):
        """bellek_ttl=True: listeleme çağrıları için TTL dolduysa yeniden oku.
        Detay/tarama çağrıları mevcut kopyayı kullanır ki satır id'leri kaymasın."""
        df = _df()
        bayat = bellek_ttl and time.time() - _DURUM["fikstur_zaman"] > FIKSTUR_BELLEK_TTL
        if not (_DURUM["fikstur"] is None or yenile or bayat):
            return _DURUM["fikstur"], _DURUM["kitapcilar"]
        # Sunucu 8 iş parçacığıyla çalışıyor: kilit olmadan aynı anda gelen
        # sekiz istek bülteni sekiz kez kuruyor, her biri dört kaynağı ayrı
        # ayrı çekiyordu. Kilidi bekleyen istek, kurulum bitmişse hazır
        # kopyayı alır.
        with _FIKSTUR_KILIT:
            bayat = bellek_ttl and time.time() - _DURUM["fikstur_zaman"] > FIKSTUR_BELLEK_TTL
            if _DURUM["fikstur"] is None or yenile or bayat:
                fik, kitapcilar = veri.fikstur_yukle(
                    ligler=sorted(set(df["Div"].unique()) | set(veri.EK_LIGLER)), yenile=yenile
                )
                # Katman katman kaç maç eklendiği ve hata varsa nedeni kaydedilir:
                # eskiden bu hatalar sessizce yutuluyordu, "neden az maç var"
                # sorusu ancak sunucu günlüğüne bakarak yanıtlanabiliyordu.
                kapsam = {"csv": int(len(fik))}
                n = len(fik)
                try:
                    fik = _dis_kapsami_ekle(df, fik, yenile)
                    kapsam["fdorg"] = int(len(fik) - n)
                except Exception as h:  # noqa: BLE001
                    kapsam["fdorg"] = 0
                    kapsam["fdorg_hata"] = str(h)[:200]
                n = len(fik)
                try:
                    fik = _dun_arsivden_ekle(df, fik)   # dünkü oynanmışlar denetim için
                    kapsam["dun"] = int(len(fik) - n)
                except Exception as h:  # noqa: BLE001
                    kapsam["dun"] = 0
                    kapsam["dun_hata"] = str(h)[:200]
                n = len(fik)
                try:
                    fik = _af_kapsami_ekle(df, fik)
                    kapsam["af"] = int(len(fik) - n)
                except Exception as h:  # noqa: BLE001 — kapsama katmanı bülteni düşürmesin
                    kapsam["af"] = 0
                    kapsam["af_hata"] = str(h)[:200]
                n = len(fik)
                try:
                    fik = _acik_kapsami_ekle(df, fik)
                    kapsam["acik"] = int(len(fik) - n)
                except Exception as h:  # noqa: BLE001
                    kapsam["acik"] = 0
                    kapsam["acik_hata"] = str(h)[:200]
                kapsam["acik_kapali"] = bool(veri.acik_fikstur_kapali())
                try:
                    kapsam["acik_arsiv"] = veri.acik_arsiv_ozeti()
                    kapsam["acik_arsiv"]["analiz_lig"] = len(veri.ACIK_LIG_TAKIMLARI)
                except Exception:  # noqa: BLE001
                    pass
                kapsam["toplam"] = int(len(fik))
                kapsam["af_anahtar"] = bool(veri.gizli_anahtar("APIFOOTBALL_KEY", "apifootball_key"))
                # "Oran neden yok" sorusu ekrandan okunabilsin: oran bültenin
                # kendisinden (fixtures.csv) gelir; AF/fd.org katmanları maçı
                # listeler ama fiyat getirmez. Bu ikisini ayırmak şart.
                try:
                    yol = os.path.join(veri.VERI_KLASORU, "fixtures.csv")
                    if os.path.exists(yol):
                        yas = (time.time() - os.path.getmtime(yol)) / 3600.0
                        kapsam["fikstur_saat"] = round(yas, 1)
                        kapsam["fikstur_kb"] = int(os.path.getsize(yol) / 1024)
                    else:
                        kapsam["fikstur_yok"] = True
                except Exception:  # noqa: BLE001 — teşhis asla bülteni düşürmesin
                    pass
                _DURUM["kapsam"] = kapsam
                try:
                    kapsam["pinnacle"] = _pinnacle_fuzyonu(fik)   # anahtarsız oran
                except Exception as h:  # noqa: BLE001
                    kapsam["pinnacle"] = 0
                    kapsam["pinnacle_hata"] = str(h)[:200]
                try:
                    _piyasa_fuzyonu_uygula(fik)      # önbellekte ne varsa satırlara işle
                    _piyasa_isit_baslat(fik)         # eksikleri arka planda çek
                except Exception:  # noqa: BLE001
                    pass
                # "oranlı" sayımı EN SON: bütün oran katmanları işlendikten sonra
                try:
                    oran_kol = ["oran_ev", "oran_berabere", "oran_dep"]
                    if all(k in fik.columns for k in oran_kol):
                        kapsam["oranli"] = int(fik[oran_kol].notna().all(axis=1).sum())
                    _DURUM["kapsam"] = kapsam
                except Exception:  # noqa: BLE001
                    pass
                _DURUM["fikstur"], _DURUM["kitapcilar"] = fik, kitapcilar
                _DURUM["fikstur_zaman"] = time.time()
        return _DURUM["fikstur"], _DURUM["kitapcilar"]


    def _gercek_sonuc(df, r):
        """Oynanmış maçın arşivdeki gerçek sonucu — dünkü seçimler denetlensin."""
        try:
            if r["Tarih"] >= veri.simdi_tr():
                return None
            cozucu = veri.takim_cozucu_onbellekli(df, hizli=True)
            try:
                ev, dep = cozucu(str(r["HomeTeam"])), cozucu(str(r["AwayTeam"]))
            except ValueError:
                return None
            t = pd.Timestamp(r["Tarih"]).normalize()
            aday = df[(df["Tarih"] >= t - pd.Timedelta(days=1))
                      & (df["Tarih"] <= t + pd.Timedelta(days=1))
                      & (df["HomeTeam"] == ev) & (df["AwayTeam"] == dep)]
            if aday.empty:
                return None
            s = aday.iloc[-1]
            cikti = {"skor": f"{int(s['FTHG'])}-{int(s['FTAG'])}",
                     "ms": "1" if s["FTR"] == "H" else ("0" if s["FTR"] == "D" else "2")}
            if pd.notna(s.get("HTHG")) and pd.notna(s.get("HTAG")):
                iy_h, iy_d = int(s["HTHG"]), int(s["HTAG"])
                iy = "1" if iy_h > iy_d else ("0" if iy_h == iy_d else "2")
                cikti["iy_skor"] = f"{iy_h}-{iy_d}"
                cikti["iyms"] = f"{iy}/{cikti['ms']}"
            return cikti
        except Exception:  # noqa: BLE001 — denetim katmanı yanıtı düşürmesin
            return None

    def _en_iyi_hepsi(r, maks):
        """{"MS1": en iyi oran, ...} — sağlam seçim ölçümü en iyi fiyatla yapıldı."""
        d = {}
        for secim in ("MS1", "MS0", "MS2", "ÜST 2.5", "ALT 2.5"):
            o = _en_iyi_oran(r, secim, maks)
            if o:
                d[secim] = o
        return d

    def _sinyal(r, oranlar, maks, ust_alt, model_p=None):
        """Konsensüs sapması sinyali — backtest kanıtlı ana motor.

        Liste oranı zaten piyasa ortalamasıdır (fikstür Avg'yi önceler), en iyi
        fiyat oran_max_* kolonundan gelir. Model, yalnız VETO görevi görür.
        """
        try:
            ua_maks = (_num(r.get("oran_ust25_maks")), _num(r.get("oran_alt25_maks")))
            if any(x is None for x in ua_maks):
                ua_maks = None
            return analiz.sinyal_tara(
                tuple(oranlar) if oranlar else None,
                tuple(maks) if maks else None,
                tuple(ust_alt) if ust_alt else None,
                ua_maks,
                model_p,
            )
        except Exception:  # noqa: BLE001 — sinyal katmanı taramayı düşürmesin
            return None

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
                       or veri.lig_adi(r["Div"]),
            "ev": r["HomeTeam"],
            "dep": r["AwayTeam"],
            "oranlar": oranlar,
            "maks": maks,
            "ust_alt": ust_alt,
            "kitapcilar": kitapci,
            "analiz_yok": bool(r.get("analiz_yok", False) is True),
            "oran_kaynak": (r.get("OranKaynak") if isinstance(r.get("OranKaynak"), str)
                            else None),
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
                "piyasa_isitma": bool(_DURUM.get("piyasa_isitma")),
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
        kolon = "oran_ust25_maks" if secim.startswith("ÜST") else "oran_alt25_maks"
        return _num(r.get(kolon)) or _num(r.get("Max>2.5" if secim.startswith("ÜST") else "Max<2.5"))

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
        piyasa_butce = time.time() + 25.0
        for idx, r in hedef.iterrows():
            # Takımları arşivde çözülemeyen maç ELENMEZ: oranı varsa oran
            # kalıbı ile analiz edilir. ŞL elemesi gibi maçlarda taraflar
            # (Sabah FA, Hapoel Beer Sheva...) arşivde yok ama "aynı oranla
            # açılmış geçmiş maçlarda ne oldu" sorusu yine yanıtlanabiliyor.
            kalip_modu = bool(r.get("analiz_yok", False) is True)
            oranlar, maks, ust_alt = _fikstur_oranlari(r)
            if not oranlar and time.time() < piyasa_butce:
                # API füzyonu: bülten oranı yoksa canlı piyasa 1X2'si kullanılır
                # (yalnız önbellekten — ağ gecikmesi taramayı yavaşlatmasın;
                # önbelleği Sürpriz Radarı taraması doldurur)
                pk = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                      lig_adi=r.get("LigAdi"),
                                      sadece_onbellek=True)
                if pk and pk.get("ms"):
                    oranlar = pk["ms"]
                    maks = maks or pk.get("ms_maks")
                    ust_alt = ust_alt or pk.get("ust_alt25")
            if not oranlar:
                continue
            if kalip_modu:
                a = analiz.kalip_analizi(
                    df, tuple(oranlar),
                    ust_alt=tuple(ust_alt) if ust_alt else None,
                    lig_ipucu=r["Div"],
                )
                if not a:      # benzer oranlı geçmiş maç da yoksa gerçekten yapacak bir şey yok
                    continue
            else:
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
            sinyal = _sinyal(r, oranlar, maks, ust_alt, a["deger"]["model_p"])
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
                    "basladi": bool(r["Tarih"] <= simdi),
                    # güvenli kupon kurucusu maç başına SEÇENEKLER ister: eşik
                    # 0.55'e inince 1.80'e kadar adil oranlı taraflar da girer
                    # ve hedef toplam orana ulaşmak mümkün olur
                    "guvenli": analiz.guvenli_secimler(a, sinir=0.55)[:4],
                    "yalniz_kalip": kalip_modu,
                    "sinyal": sinyal,
                    "saglam": analiz.saglam_secim(a.get("deger"), _en_iyi_hepsi(r, maks)),
                    "sonuc": _gercek_sonuc(df, r),
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
        # sinyalli maçlar en üstte (backtest kanıtlı motor), sonra değer sırası
        sonuclar.sort(key=lambda x: (x.get("sinyal") is not None,
                                     (x.get("sinyal") or {}).get("ev", 0.0),
                                     x["ev_max"]), reverse=True)
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
            if not oranlar:
                pk = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                      lig_adi=r.get("LigAdi"),
                                      sadece_onbellek=True)
                if pk and pk.get("ms"):
                    oranlar = pk["ms"]  # API füzyonu: canlı piyasa 1X2'si (önbellekten)
            poisson = analiz.poisson_tahmini(df, r["HomeTeam"], r["AwayTeam"], lig_ipucu=r["Div"])
            kalip = (analiz.oran_kalibi(df, tuple(oranlar), lig_ipucu=r["Div"])
                     if oranlar else None)
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

    @app.post("/api/surpriz-radar")
    def surpriz_radar():
        """Günün TÜM maçlarını İY/MS çapraz kombinasyonlarına göre tarar.

        Kademeli: takımlar arşivde çözülüyorsa model+kalıp harmanı; takım
        çözülemiyor ama 1X2 oranı biliniyorsa yalnız kalıp frekansı; o da
        yoksa Bet365'in gerçek İY/MS fiyatı tek başına gösterilir. Maç hiçbir
        koşulda listeden düşmez — veri eksikse nedeni satırda yazar.
        """
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

        # Bülten İY/MS pazarının 9 seçeneği hesaplanır; ⭐ adayları, ilk
        # yarıda bir taraf öndeyken sonucun değiştiği 4 çapraz kombinasyondur
        # (0/1 ve 0/2 bilgi olarak gösterilir ama yarışa girmez).
        FOKUS = ("1/1", "1/0", "1/2", "0/1", "0/0", "0/2", "2/1", "2/0", "2/2")
        SURPRIZ = ("1/0", "1/2", "2/1", "2/0")
        satirlar = []
        # İlk taramada onlarca piyasa isteği yavaş ağda yanıtı geciktirmesin;
        # bütçe dolunca kalan maçlar piyasasız döner, sonraki tarama önbellekten tamamlar.
        piyasa_butce_bitis = time.time() + 25.0
        for idx, r in hedef.iterrows():
            analizsiz = bool(r.get("analiz_yok", False) is True)
            oranlar, _maks, _ua = _fikstur_oranlari(r)
            # Piyasa yanıtı önce alınır (İY/MS + canlı 1X2 aynı pakette gelir):
            # bülten CSV'sinde oran yayınlanmamışsa canlı 1X2 ile füzyon yapılır,
            # böylece maç kalıp eşleşmesi ve tam analiz alabilir.
            piyasa = None
            if time.time() < piyasa_butce_bitis:
                piyasa = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                          lig_adi=r.get("LigAdi"))
            satir_kaynagi = r.get("OranKaynak")
            oran_kaynak = (satir_kaynagi if isinstance(satir_kaynagi, str) and satir_kaynagi
                           else ("bulten" if oranlar else None))
            if not oranlar and piyasa and piyasa.get("ms"):
                oranlar, oran_kaynak = piyasa["ms"], "canli"
            # Birebir oran eşleşmesi: geçmiş maçın üç açılış oranı da hedefe
            # ±eşik kadar yakın olmalı (±0.05'ten başlar, örnek yetersizse genişler).
            birebir = (analiz.birebir_oran_maclari(df, tuple(oranlar), lig_ipucu=r["Div"])
                       if oranlar else None)
            # Çapraz sürpriz süzgeci birebir eşleşmeyi DEĞİL, marj-arındırılmış
            # olasılık bandını kullanır: birebir örneklem çoğu maçta 30-300
            # arasında kalıyor, %2'lik bir olayı ölçmeye yetmiyor. Kalıp bandı
            # aynı maçlarda binlerce örnek veriyor — kabarma ölçümü de bu
            # tahminciyle doğrulandı.
            kalip_bant = (analiz.oran_kalibi(df, tuple(oranlar), lig_ipucu=r["Div"])
                          if oranlar else None)
            model, poisson = None, None   # poisson önceki maçtan sarkmasın
            if not analizsiz:
                poisson = analiz.poisson_tahmini(df, r["HomeTeam"], r["AwayTeam"], lig_ipucu=r["Div"])
                model = analiz.iyms_olasiliklar(poisson)

            kombolar = {}
            for k in FOKUS:
                kalip_adet = kalip_n = None
                if birebir and birebir["n"] > 0:
                    kalip_n = int(birebir["n"])
                    kalip_adet = int(birebir["iyms"].get(k, 0))
                p = None
                if model is not None:
                    p = float(model.get(k, 0.0))
                    if kalip_n:
                        w = min(kalip_n / 300.0, 1.0) * 0.5  # nadir olaylar: kalıba ancak büyük örneklemle güven
                        p = (1 - w) * p + w * (kalip_adet / kalip_n)
                elif kalip_n and kalip_n >= 40:
                    p = kalip_adet / kalip_n  # takım analizi yok: yalnız kalıp frekansı
                kombolar[k] = {
                    "p": float(p) if p is not None else None,
                    "adil_oran": round(1.0 / p, 1) if p else None,
                    "kalip_adet": kalip_adet,
                    "kalip_n": kalip_n,
                }

            piyasa_iyms = bool(piyasa and piyasa.get("kombolar"))
            if piyasa_iyms:
                for k, kombo in kombolar.items():
                    oran = piyasa["kombolar"].get(k)
                    if oran:
                        kombo["piyasa"] = oran
                        kombo["kitapci"] = piyasa["kombo_kitapci"].get(k)
                        if kombo["p"]:
                            kombo["ev"] = round(kombo["p"] * oran - 1.0, 3)

            olasilikli = any(v["p"] is not None for v in kombolar.values())
            mod = ("model" if model is not None
                   else ("kalip" if olasilikli else ("piyasa" if piyasa_iyms else "liste")))
            # DOKUZ kombonun tamamı yarışır. Eskiden yalnız çapraz dörtlü
            # (1/0, 2/0, 2/1, 1/2) taranıyordu; o dördün toplam gerçekleşmesi
            # %15 ve 1/0-2/0 yapısal olarak diğer ikisinden hep olası olduğu
            # için radar sürekli aynı ikisini gösteriyor, isabet %5.65'te
            # kalıyordu. Artık seçim kanıta dayanır: aynı oran profilinden
            # açılmış geçmiş maçların gerçek frekansı.
            one_cikan = isaretli = ikinci = None
            kanit = None
            olasi = [k for k in FOKUS if kombolar[k]["p"] is not None]
            if olasi:
                sirali = sorted(olasi, key=lambda k: kombolar[k]["p"] or 0.0, reverse=True)
                one_cikan = sirali[0]
                ikinci = sirali[1] if len(sirali) > 1 else None
                ust = kombolar[one_cikan]
                n_k, adet = ust.get("kalip_n"), ust.get("kalip_adet")
                if n_k and n_k >= analiz.IYMS_MIN_ORNEK and adet is not None:
                    frekans = adet / n_k
                    kanit = {"n": int(n_k), "adet": int(adet), "frekans": float(frekans)}
                    # Eşik kombo başına: 1/1 için 0.35, 2/2 için 0.30 (ölçümle
                    # seçildi). 0.40 üstü "güçlü" kademe. 1/0 ve 2/0 hiç
                    # işaretlenmez — frekansları eşiğe yapısal olarak ulaşmıyor.
                    kademe = analiz.iyms_isaret(one_cikan, frekans, int(n_k))
                    if kademe and one_cikan not in analiz.IYMS_AVLANAMAZ:
                        isaretli = one_cikan
                        kanit["kademe"] = kademe
            neden = None
            if mod == "kalip":
                neden = "takımlar arşivde çözülemedi — yalnız oran kalıbı konuşuyor"
            elif mod == "piyasa":
                neden = "arşiv analizi yok — yalnız Bet365 İY/MS fiyatı"
            elif mod == "liste":
                neden = ("takımlar arşivde çözülemedi ve oran/piyasa verisi yok"
                         if analizsiz else "oran ve piyasa verisi henüz yayınlanmadı")

            satirlar.append(
                {
                    "id": int(idx),
                    "tarih": tarih,
                    "saat": r["Tarih"].strftime("%H:%M"),
                    "lig": r["Div"],
                    "ev": r["HomeTeam"],
                    "dep": r["AwayTeam"],
                    "oranli": bool(oranlar),
                    "oran_kaynak": oran_kaynak,
                    "mod": mod,
                    "neden": neden,
                    "kombolar": kombolar,
                    "one_cikan": one_cikan,
                    "isaretli": isaretli,
                    "ikinci": ikinci,
                    "kanit": kanit,
                    "capraz": analiz.capraz_surpriz(
                        kalip_bant,
                        {k: (kombolar[k] or {}).get("piyasa") for k in analiz.CAPRAZ_TABAN}
                        if piyasa_iyms else None),
                    # 1Y/2Y karşılıklı gol kombinasyonu (kullanıcının kitapçısındaki
                    # "1. Yarı / 2. Yarı Karşılıklı Gol" pazarı için karar desteği)
                    "yari_kg": analiz.yari_kg_kombo(kalip_bant, poisson),
                    "sonuc": _gercek_sonuc(df, r),
                    "piyasa": (
                        {"kitapci": piyasa["kitapci"], "guncel": piyasa["guncel"]}
                        if piyasa_iyms else None
                    ),
                    "kalip": (
                        {"esik": birebir["esik"], "n": birebir["n"],
                         "hedef": birebir["hedef"], "ms": birebir["ms"],
                         "iyms_adil": birebir["hedef_iyms_adil"],
                         "ulke": birebir.get("ulke")}
                        if birebir else None
                    ),
                    "ornekler": birebir["ornekler"] if birebir else [],
                    "surpriz": float(kombolar[one_cikan]["p"]) if one_cikan else 0.0,
                }
            )
        MOD_SIRA = {"model": 0, "kalip": 1, "piyasa": 2, "liste": 3}
        satirlar.sort(key=lambda x: (x.get("isaretli") is None,
                                     MOD_SIRA.get(x["mod"], 9), -x["surpriz"], x["saat"]))
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
            # KISMİ ANALİZ: rakibin ligi arşivde olmasa da elimizdekini göster —
            # çözülen tarafın son maçları (geçmişi), oran kalıbı, kitapçı panosu,
            # piyasa İY/MS fiyatları. (Beşiktaş–Kauno Žalgiris tipi kupa maçları.)
            oranlar, maks, ust_alt = _fikstur_oranlari(r)
            piyasa_k = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                        lig_adi=r.get("LigAdi"),
                                        sadece_onbellek=True)
            if not oranlar and piyasa_k and piyasa_k.get("ms"):
                oranlar = piyasa_k["ms"]
                maks = maks or piyasa_k.get("ms_maks")
                ust_alt = ust_alt or piyasa_k.get("ust_alt25")

            cozucu = veri.takim_cozucu_onbellekli(df, hizli=True)
            taraflar = []
            for ad in (r["HomeTeam"], r["AwayTeam"]):
                try:
                    arsiv_ad = cozucu(str(ad))
                except ValueError:
                    continue
                sec = df[(df["HomeTeam"] == arsiv_ad) | (df["AwayTeam"] == arsiv_ad)] \
                    .sort_values("Tarih", ascending=False).head(10)
                maclar = []
                for s in sec.itertuples():
                    evde = s.HomeTeam == arsiv_ad
                    sonuc = "G" if (s.FTR == ("H" if evde else "A")) else ("B" if s.FTR == "D" else "M")
                    maclar.append({
                        "tarih": s.Tarih.strftime("%d.%m.%Y"), "lig": s.Div,
                        "ev": s.HomeTeam, "dep": s.AwayTeam,
                        "skor": f"{int(s.FTHG)}-{int(s.FTAG)}", "sonuc": sonuc,
                    })
                taraflar.append({"ad": str(ad), "arsiv_adi": arsiv_ad, "maclar": maclar})

            kalip = (analiz.oran_kalibi(df, tuple(oranlar), ornek_sayisi=12)
                     if oranlar else None)
            korner_piyasa = None
            if piyasa_k and piyasa_k.get("korner"):
                korner_piyasa = {"kitapci": piyasa_k.get("korner_kitapci"),
                                 "barem": sorted(piyasa_k["korner"],
                                                 key=lambda x: x["cizgi"])[:7]}
            ozet = _mac_ozeti(idx, r, kitapcilar)
            if oranlar and not ozet["oranlar"]:
                ozet["oranlar"] = oranlar
            if piyasa_k and piyasa_k.get("ms"):
                ozet["kitapcilar"][f"{piyasa_k.get('ms_kitapci') or 'Piyasa'} (canlı)"] = piyasa_k["ms"]
            iyms_fiyatlar = (
                {k: {"oran": v, "kitapci": piyasa_k["kombo_kitapci"].get(k)}
                 for k, v in piyasa_k["kombolar"].items()}
                if piyasa_k and piyasa_k.get("kombolar") else None
            )
            return jsonify({
                "kismi": True,
                "ev": r["HomeTeam"], "dep": r["AwayTeam"],
                "neden": "Rakip takımın ligi 38 liglik arşivde yok — tam model kurulamıyor; "
                         "eldeki her şey aşağıda: çözülen tarafın geçmişi, oran kalıbı ve piyasa fiyatları.",
                "fikstur": {"tarih": r["Tarih"].strftime("%d.%m.%Y"), "saat": ozet["saat"],
                            "kitapcilar": ozet["kitapcilar"], "maks": maks,
                            "ust_alt": ust_alt, "oran_kaynak": r.get("OranKaynak") or ("canli" if piyasa_k and piyasa_k.get("ms") else None)},
                "kalip": _kalip_json(kalip),
                "taraflar": taraflar,
                "iyms": iyms_fiyatlar,
                "korner": {"model": None, "kalip": None, "piyasa": korner_piyasa} if korner_piyasa else None,
            })

        oranlar, maks, ust_alt = _fikstur_oranlari(r)
        # API füzyonu: aynı pakette İY/MS + korner + canlı 1X2 gelir; bülten
        # oranı yayınlanmamışsa canlı 1X2 ile tam analiz yapılır.
        piyasa_k = veri.iyms_piyasa(r["HomeTeam"], r["AwayTeam"], r["Div"], r["Tarih"],
                                    lig_adi=r.get("LigAdi"),
                                    sadece_onbellek=True)
        satir_kaynagi = r.get("OranKaynak")
        oran_kaynak = (satir_kaynagi if isinstance(satir_kaynagi, str) and satir_kaynagi
                       else ("bulten" if oranlar else None))
        if not oranlar and piyasa_k and piyasa_k.get("ms"):
            oranlar, oran_kaynak = piyasa_k["ms"], "canli"
            maks = maks or piyasa_k.get("ms_maks")
            ust_alt = ust_alt or piyasa_k.get("ust_alt25")
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

        # 🚩 Korner: model beklentisi + birebir oranlı geçmişin korner özeti +
        # piyasa baremi (1xbet/Bet365) ve modele göre beklenen değer.
        korner_model = analiz.korner_beklentisi(df, r["HomeTeam"], r["AwayTeam"], lig_ipucu=r["Div"])
        birebir = (analiz.birebir_oran_maclari(df, tuple(oranlar), ornek_sayisi=0, lig_ipucu=r["Div"])
                   if oranlar else None)
        korner_piyasa = None
        if piyasa_k and piyasa_k.get("korner"):
            import math as _m
            barem = []
            for satir in piyasa_k["korner"]:
                kayit = dict(satir)
                if korner_model:
                    toplam = korner_model["toplam"]
                    p_alt = sum(analiz._poisson_pmf(i, toplam)
                                for i in range(int(_m.floor(satir["cizgi"])) + 1))
                    p_ust = 1.0 - p_alt
                    kayit["p_ust"] = round(p_ust, 3)
                    kayit["ev_ust"] = round(p_ust * satir["ust"] - 1.0, 3)
                    kayit["ev_alt"] = round(p_alt * satir["alt"] - 1.0, 3)
                barem.append(kayit)
            # modelin beklediği toplamın etrafındaki en anlamlı 7 çizgi
            if korner_model:
                barem.sort(key=lambda x: abs(x["cizgi"] - korner_model["toplam"]))
            korner_piyasa = {"kitapci": piyasa_k.get("korner_kitapci"),
                             "barem": sorted(barem[:7], key=lambda x: x["cizgi"])}
        if korner_model or korner_piyasa or (birebir and birebir.get("korner")):
            j["korner"] = {
                "model": korner_model,
                "kalip": birebir.get("korner") if birebir else None,
                "piyasa": korner_piyasa,
            }
        if j["deger"]:
            model_p = a["deger"]["model_p"]
            for satir in j["deger"]["satirlar"]:
                en_iyi = _en_iyi_oran(r, satir["secim"], maks)
                if en_iyi:
                    satir["oran_max"] = en_iyi
                    satir["ev_max"] = model_p[satir["secim"]] * en_iyi - 1.0
        ozet = _mac_ozeti(idx, r, kitapcilar)
        # Canlı piyasa 1X2'si panoya eklenir — bülten açılışıyla yan yana
        # görünce oran hareketi (açılış → şimdi) okunur hale gelir.
        if piyasa_k and piyasa_k.get("ms"):
            ozet["kitapcilar"][f"{piyasa_k.get('ms_kitapci') or 'Piyasa'} (canlı)"] = piyasa_k["ms"]
        j["fikstur"] = {
            "tarih": r["Tarih"].strftime("%d.%m.%Y"),
            "saat": ozet["saat"],
            "kitapcilar": ozet["kitapcilar"],
            "maks": ozet["maks"],
            "ust_alt": ozet["ust_alt"],
            "oran_kaynak": oran_kaynak,
        }
        j["guvenli"] = analiz.guvenli_secimler(a)[:6]
        j["sinyal"] = _sinyal(r, oranlar, maks, ust_alt,
                              (a.get("deger") or {}).get("model_p"))
        j["saglam"] = analiz.saglam_secim(a.get("deger"), _en_iyi_hepsi(r, maks))
        return jsonify(j)

    @app.post("/api/ayarlar")
    def ayarlar():
        """API anahtarlarını panelden kaydeder (kalıcı diske; env gerektirmez).

        Değerler asla geri okunmaz/gösterilmez; boş değer kaydı siler.
        """
        govde = request.get_json(silent=True) or {}
        degisti = False
        for ad in ("football_data_org_key", "gemini_api_key", "odds_api_io_key", "apifootball_key"):
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
                "piyasa_iyms": bool(veri.gizli_anahtar("ODDS_API_IO_KEY", "odds_api_io_key")),
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
        ek_bolum = ""
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
                ozet = _mac_ozeti(int(govde["id"]), r, _kitapcilar)
                if ozet["kitapcilar"]:
                    satirlar = [
                        f"  {ad}: {o[0]:.2f} / {o[1]:.2f} / {o[2]:.2f}"
                        for ad, o in ozet["kitapcilar"].items()
                    ]
                    if ozet["maks"]:
                        satirlar.append(
                            f"  En iyi (piyasa maks.): {ozet['maks'][0]:.2f} / {ozet['maks'][1]:.2f} / {ozet['maks'][2]:.2f}"
                        )
                    ek_bolum = "\n\n=== KİTAPÇI ORANLARI (1 / X / 2) ===\n" + "\n".join(satirlar)
            else:
                ev = veri.takim_bul(df, str(govde.get("ev", "")))
                dep = veri.takim_bul(df, str(govde.get("dep", "")))
                oranlar = _oranlari_dogrula(govde.get("oranlar")) if govde.get("oranlar") else None
                a = analiz.mac_analizi(df, ev, dep, oranlar=oranlar, elo=_DURUM["elo"])
        except (KeyError, ValueError) as hata:
            return jsonify({"hata": f"Maç kurulamadı: {hata}"}), 404

        metin = rapor.rapor_olustur(a, lig_adi=veri.lig_adi(a["poisson"]["lig"])) + ek_bolum
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

        def _kapanis_orani(secim: str | None, r) -> float | None:
            kol = {"MS1": "oran_ev_kapanis", "MS0": "oran_berabere_kapanis",
                   "MS2": "oran_dep_kapanis", "ÜST 2.5": "oran_ust25_kapanis",
                   "ALT 2.5": "oran_alt25_kapanis"}.get(secim or "")
            if not kol:
                return None
            deger = getattr(r, kol, None)
            return float(deger) if deger is not None and not pd.isna(deger) else None

        satirlar = []
        karne = {"toplam": 0, "ms_dogru": 0, "ms_n": 0, "ua_dogru": 0, "ua_n": 0,
                 "kg_dogru": 0, "kg_n": 0, "secim_tutan": 0, "secim_n": 0,
                 "degerli_kar": 0.0, "degerli_n": 0,
                 "clv_toplam": 0.0, "clv_n": 0, "clv_yenen": 0}
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
                    # CLV: kayıt anında alınan oran vs kapanış oranı. Alınan oran
                    # kapanıştan yüksekse piyasa bize doğru kapanmış demektir —
                    # uzun vadeli kazanmanın asıl göstergesi budur.
                    kapanis = _kapanis_orani(t.get("secim"), r)
                    if kapanis and kapanis > 1 and t.get("oran"):
                        clv = float(t["oran"]) / kapanis - 1.0
                        tahmin["kapanis_oran"] = round(kapanis, 2)
                        tahmin["clv"] = round(clv, 4)
                        karne["clv_n"] += 1
                        karne["clv_toplam"] += clv
                        karne["clv_yenen"] += int(clv > 0)

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
                    "lig_adi": veri.lig_adi(r.Div),
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

        karne["clv_ort"] = round(karne["clv_toplam"] / karne["clv_n"], 4) if karne["clv_n"] else None

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

    # ------------------------------------------------------------- kupon defteri

    @app.get("/api/kuponlar")
    def kuponlar_listele():
        try:
            df = _df()
        except FileNotFoundError:
            df = None
        kuponlar = kupon.sonuclandir(df)
        return jsonify({"kuponlar": [kupon.degerlendir(k) for k in kuponlar]})

    @app.post("/api/kuponlar")
    def kupon_olustur():
        govde = request.get_json(silent=True) or {}
        try:
            yeni = kupon.olustur(govde.get("secimler") or [],
                                 sistem=govde.get("sistem", "kombine"),
                                 ad=govde.get("ad", ""))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": True, "id": yeni["id"]})

    @app.post("/api/kupon-sil")
    def kupon_sil():
        govde = request.get_json(silent=True) or {}
        return jsonify({"tamam": kupon.sil(int(govde.get("id", 0)))})

    @app.post("/api/kupon-bacak")
    def kupon_bacak():
        govde = request.get_json(silent=True) or {}
        try:
            tamam = kupon.elle_isaretle(int(govde.get("id", 0)),
                                        int(govde.get("indeks", -1)),
                                        str(govde.get("durum", "")))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": tamam})

    # ---------------------------------------------------------- 📈 Rolling
    @app.get("/api/rolling")
    def rolling_listele():
        try:
            df = _df()
        except FileNotFoundError:
            df = None
        planlar = rolling.sonuclandir(df)
        return jsonify({"planlar": [rolling.hesapla(p) for p in planlar]})

    @app.post("/api/rolling")
    def rolling_olustur():
        govde = request.get_json(silent=True) or {}
        try:
            plan = rolling.olustur(str(govde.get("ad", "")),
                                   float(govde.get("baslangic", 0)),
                                   float(govde.get("hedef_oran", 2.0) or 2.0),
                                   int(govde.get("hedef_gun", 15) or 15))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": True, "plan": rolling.hesapla(plan)})

    @app.post("/api/rolling-sil")
    def rolling_sil():
        govde = request.get_json(silent=True) or {}
        return jsonify({"tamam": rolling.sil(int(govde.get("id", 0)))})

    @app.post("/api/rolling-adim")
    def rolling_adim():
        govde = request.get_json(silent=True) or {}
        try:
            plan = rolling.adim_ekle(int(govde.get("id", 0)), govde.get("secim") or {})
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": True, "plan": rolling.hesapla(plan)})

    @app.post("/api/rolling-adim-sil")
    def rolling_adim_sil():
        govde = request.get_json(silent=True) or {}
        try:
            tamam = rolling.adim_sil(int(govde.get("id", 0)),
                                     int(govde.get("indeks", -1)))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": tamam})

    @app.post("/api/rolling-duzenle")
    def rolling_duzenle():
        govde = request.get_json(silent=True) or {}
        try:
            tamam = rolling.adim_duzenle(int(govde.get("id", 0)),
                                         int(govde.get("indeks", -1)),
                                         govde.get("alanlar") or {})
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": tamam})

    @app.post("/api/rolling-isaretle")
    def rolling_isaretle():
        govde = request.get_json(silent=True) or {}
        try:
            tamam = rolling.elle_isaretle(int(govde.get("id", 0)),
                                          int(govde.get("indeks", -1)),
                                          str(govde.get("durum", "")),
                                          int(govde.get("bacak", 0)))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": tamam})

    @app.post("/api/rolling-bacak")
    def rolling_bacak():
        """Bekleyen adıma bacak ekler — kombineyi tek tek yazma akışı."""
        govde = request.get_json(silent=True) or {}
        try:
            plan = rolling.bacak_ekle(int(govde.get("id", 0)),
                                      int(govde.get("adim", -1)),
                                      govde.get("secim") or {})
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": True, "plan": rolling.hesapla(plan)})

    @app.post("/api/rolling-bacak-sil")
    def rolling_bacak_sil():
        govde = request.get_json(silent=True) or {}
        try:
            tamam = rolling.bacak_sil(int(govde.get("id", 0)),
                                      int(govde.get("adim", -1)),
                                      int(govde.get("bacak", -1)))
        except (ValueError, TypeError) as hata:
            return jsonify({"hata": str(hata)}), 400
        return jsonify({"tamam": tamam})

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
