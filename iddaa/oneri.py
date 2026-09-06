"""Öneri hesapları: Sistem Önerisi ve Sürpriz Radarı'nın hesap gövdeleri.

Uçlar (web.py) yalnız parametre doğrular ve buradaki fonksiyonları çağırır.
Aynı fonksiyonlar bakım döngüsündeki otomatik defterleme işinden de çağrılır
(sistem_defteri.py): sistem her gün kendi kuponunu kendisi yazar ve sonuçlar.

Davranış, uçların önceki gövdesiyle birebirdir (bkz. test_refactor_esdeger.py):
buraya taşınırken tek satır hesap değişmemiştir; yalnız `_DURUM["elo"]` gibi
istek bağlamı parametreye dönüştü.
"""

from __future__ import annotations

import time

import pandas as pd

from . import analiz, sistem, veri


def _num(x, basamak: int = 2):
    return None if pd.isna(x) else round(float(x), basamak)


# ─────────────────────────────────────────── satır yardımcıları (fikstür satırı)

def fikstur_oranlari(r):
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


def en_iyi_oran(r, secim, maks):
    if secim == "MS1":
        return maks[0] if maks else None
    if secim == "MS0":
        return maks[1] if maks else None
    if secim == "MS2":
        return maks[2] if maks else None
    kolon = "oran_ust25_maks" if secim.startswith("ÜST") else "oran_alt25_maks"
    return _num(r.get(kolon)) or _num(r.get("Max>2.5" if secim.startswith("ÜST") else "Max<2.5"))


def en_iyi_hepsi(r, maks):
    """{"MS1": en iyi oran, ...} — sağlam seçim ölçümü en iyi fiyatla yapıldı."""
    d = {}
    for secim in ("MS1", "MS0", "MS2", "ÜST 2.5", "ALT 2.5"):
        o = en_iyi_oran(r, secim, maks)
        if o:
            d[secim] = o
    return d


def gercek_sonuc(df, r):
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


def gun_satirlari(fik: pd.DataFrame, tarih: str) -> pd.DataFrame:
    """Fikstürden gün satırları (dd.mm.yyyy)."""
    return fik[fik["Tarih"].dt.strftime("%d.%m.%Y") == tarih]


# ─────────────────────────────────────────────────── Sistem Önerisi

SISTEM_VARSAYILAN = {
    "hedef": 2.0, "maks_bacak": 3, "esik": 0.60, "marj": sistem.MARJ_VARSAYILAN,
    "oncelik": "oran", "sans_bacak": 2, "kapsam": "yaygin",
}


def sistem_ayarlari(govde: dict) -> dict:
    """İstek gövdesinden doğrulanmış ayarlar (uç ve defterleme işi ortak).

    Geçersiz sayı ValueError/TypeError fırlatır (uç 400 döner)."""
    a = dict(SISTEM_VARSAYILAN)
    a["hedef"] = max(1.10, min(20.0, float(govde.get("hedef", a["hedef"]))))
    a["maks_bacak"] = max(1, min(6, int(govde.get("maks_bacak", a["maks_bacak"]))))
    a["esik"] = max(0.40, min(0.90, float(govde.get("esik", a["esik"]))))
    a["marj"] = max(sistem.MARJ_ALT, min(sistem.MARJ_UST,
                                        float(govde.get("marj", a["marj"]))))
    oncelik = str(govde.get("oncelik", a["oncelik"]))
    a["oncelik"] = oncelik if oncelik in ("oran", "sans", "kazanc") else "oran"
    a["sans_bacak"] = max(1, min(6, int(govde.get("sans_bacak", a["sans_bacak"]))))
    kapsam = str(govde.get("kapsam", a["kapsam"]))
    a["kapsam"] = kapsam if kapsam in ("temel", "yaygin", "genis") else "yaygin"
    return a


def site_oranlari_ayikla(ham) -> dict:
    """Oranlar sekmesinde kullanıcının iddaa.com'dan yazdığı fiyatlar:
    {"ev|dep|pazar": oran}. Varsa gerçek Türkiye fiyatı olarak kullanılır."""
    site_oranlari = {}
    for anahtar, deger in (ham or {}).items():
        try:
            o = float(deger)
        except (TypeError, ValueError):
            continue
        if 1.01 <= o <= 1000 and isinstance(anahtar, str):
            site_oranlari[anahtar] = o
    return site_oranlari


def gun_adaylari(df, fik, tarih: str, elo, site_oranlari: dict | None = None,
                 butce_sn: float = 40.0, simdi=None) -> dict:
    """Günün BÜTÜN maçlarının fiyatlanabilen BÜTÜN pazarları (137'ye kadar).

    Dönen: {"maclar": [...], "taranan": n, "oransiz": n, "keskin": n}. Başlamış
    maç öneriye girmez; süre bütçesi dolunca kalan maçlar taranmaz."""
    site_oranlari = site_oranlari or {}
    hedef = gun_satirlari(fik, tarih)
    simdi = simdi or veri.simdi_tr()
    maclar, taranan, oransiz = [], 0, 0
    butce = time.time() + float(butce_sn)
    # Pinnacle'ın BÜTÜN pazarları (30'a kadar): gerçek fiyat + marjsız olasılık.
    # Bülten katmanı yalnız 1X2 ve 2.5'i satıra yazıyor; burada geniş harita
    # doğrudan kullanılır ki Alt/Üst 1.5-4.5, takım golleri ve ilk yarı
    # pazarları da GERÇEK fiyatla kupona girebilsin.
    try:
        pin_indeks = veri.pinnacle_indeksi(veri.pinnacle_oranlari())
    except Exception:  # noqa: BLE001
        pin_indeks = {}
    keskin_sayisi = 0
    for idx, r in hedef.iterrows():
        if r["Tarih"] <= simdi:        # başlamış maç öneriye girmez
            continue
        if time.time() > butce:
            break
        taranan += 1
        oranlar, maks, ust_alt = fikstur_oranlari(r)
        if not oranlar:
            oransiz += 1
            continue
        try:
            if bool(r.get("analiz_yok", False) is True):
                a = analiz.kalip_analizi(
                    df, tuple(oranlar),
                    ust_alt=tuple(ust_alt) if ust_alt else None,
                    lig_ipucu=r["Div"])
            else:
                a = analiz.mac_analizi(
                    df, r["HomeTeam"], r["AwayTeam"], oranlar=tuple(oranlar),
                    elo=elo,
                    ust_alt=tuple(ust_alt) if ust_alt else None,
                    lig_ipucu=r["Div"])
        except Exception:  # noqa: BLE001
            continue
        if not a:
            continue
        en_iyi = en_iyi_hepsi(r, maks)
        pin = veri.pinnacle_esle(pin_indeks, r["HomeTeam"], r["AwayTeam"], r["Tarih"]) if pin_indeks else None
        pin_fiyat = (pin or {}).get("pazarlar") or {}
        pin_adil = (pin or {}).get("adil") or {}
        if pin_fiyat:
            keskin_sayisi += 1
        ortak = []
        if a.get("kalip") and a["kalip"].get("n"):
            ortak.append(f"benzer oranlı {a['kalip']['n']:,} geçmiş maç".replace(",", "."))

        # Korner ve kart ayrı motorlardan gelir; arşivde takım verisi yoksa
        # (kalıp modu) kurulamazlar, o maçlarda yalnız gol pazarları olur.
        korner = kart = None
        if not bool(r.get("analiz_yok", False) is True):
            try:
                korner = analiz.korner_beklentisi(df, r["HomeTeam"], r["AwayTeam"], r["Div"])
                kart = analiz.kart_beklentisi(df, r["HomeTeam"], r["AwayTeam"], r["Div"])
            except Exception:  # noqa: BLE001
                korner = kart = None

        # 137 pazarlık geniş havuz. guvenli_secimler() dar çekirdek kümedir ve
        # bülten/tablo ona bağlı olduğu için dokunulmadı; burada tum_pazarlar
        # kullanılıyor. MS ve Ü/A 2.5'te piyasa çapalı model olasılığı
        # (deger_analizi) daha iyi kalibre olduğu için onun değeri geçerli.
        try:
            pazarlar = analiz.tum_pazarlar(a["poisson"], korner, kart)
        except Exception:  # noqa: BLE001
            continue
        for secim, p_piyasa in ((a.get("deger") or {}).get("model_p") or {}).items():
            if secim in pazarlar:
                pazarlar[secim] = float(p_piyasa)

        secenekler = []
        for pazar, p in pazarlar.items():
            gerekce = list(ortak)
            if pazar.startswith("KORNER") or " KORNER " in pazar:
                gerekce.append(f"beklenen korner {korner['toplam']} "
                               f"(lig ortalaması {korner['lig_ort']})")
            elif pazar.startswith("KART"):
                gerekce.append(f"beklenen sarı kart {kart['toplam']} "
                               f"(lig ortalaması {kart['lig_ort']})")
            site_fiyat = site_oranlari.get(f"{r['HomeTeam']}|{r['AwayTeam']}|{pazar}")
            oran_gercek = site_fiyat or en_iyi.get(pazar) or pin_fiyat.get(pazar)
            secenekler.append({
                "pazar": pazar,
                "p": float(p),
                "oran": oran_gercek,                  # iddaa.com (kullanıcı) > bülten > Pinnacle
                "oran_kaynak": "iddaa" if site_fiyat else ("piyasa" if oran_gercek else None),
                "keskin_adil": pin_adil.get(pazar),   # Pinnacle marjsız olasılık
                "gerekce": gerekce,
            })
        if secenekler:
            # DİKKAT: pandas'ta NaN "doğru" sayılır, bu yüzden `r.get("LigAdi") or
            # r["Div"]` boş lig adında NaN döndürür ve jsonify geçersiz JSON yazar
            # (Python NaN'ı okur, tarayıcının JSON.parse'ı reddeder → sayfa boş kalır).
            lig_ad = r.get("LigAdi")
            if lig_ad is None or pd.isna(lig_ad) or not str(lig_ad).strip():
                lig_ad = r["Div"]
            maclar.append({
                "mac_id": int(idx),
                "ev_ad": r["HomeTeam"],
                "dep_ad": r["AwayTeam"],
                "saat": r["Tarih"].strftime("%H:%M"),
                "lig": str(lig_ad),
                "lig_kodu": str(r["Div"]) if pd.notna(r.get("Div")) else None,
                "secenekler": secenekler,
            })
    return {"maclar": maclar, "taranan": taranan, "oransiz": oransiz, "keskin": keskin_sayisi}


def kupon_sec(havuz: list[dict], ayarlar: dict) -> dict | None:
    """Mod dispatch: hedef oran / şans / kazanç."""
    if ayarlar["oncelik"] == "sans":
        return sistem.en_yuksek_sans(havuz, bacak_sayisi=ayarlar["sans_bacak"],
                                     esik=ayarlar["esik"], marj=ayarlar["marj"])
    if ayarlar["oncelik"] == "kazanc":
        return sistem.kazanc_kuponu(havuz, hedef=ayarlar["hedef"], esik=ayarlar["esik"],
                                    maks_bacak=ayarlar["maks_bacak"])
    return sistem.kupon_kur(havuz, hedef=ayarlar["hedef"], maks_bacak=ayarlar["maks_bacak"],
                            esik=ayarlar["esik"], marj=ayarlar["marj"])


def sistem_onerisi_hesapla(df, fik, tarih: str, elo, ayarlar: dict,
                           site_oranlari: dict | None = None,
                           adaylar: dict | None = None) -> dict:
    """/api/sistem-onerisi yanıtı. `adaylar` verilirse (gün bir kez taranmış)
    yeniden taranmaz — defterleme işi dört modu tek taramadan kurar."""
    if adaylar is None:
        adaylar = gun_adaylari(df, fik, tarih, elo, site_oranlari)
    maclar = adaylar["maclar"]
    havuz, elenen, kapsam_disi = sistem.havuz_kur(maclar, kapsam=ayarlar["kapsam"])
    kupon = kupon_sec(havuz, ayarlar)
    derin_mac = sum(1 for m in maclar
                    if sistem.lig_derinligi(m.get("lig"), m.get("lig_kodu")) == "derin")
    hedef_oran, esik, oncelik = ayarlar["hedef"], ayarlar["esik"], ayarlar["oncelik"]
    return {
        "tarih": tarih,
        "mac_sayisi": adaylar["taranan"],
        "aday_sayisi": len(havuz),
        "elenen": elenen,
        "kapsam": ayarlar["kapsam"],
        "kapsam_disi": kapsam_disi,
        "derin_mac": derin_mac,
        "sig_mac": len(maclar) - derin_mac,
        "keskin_mac": adaylar["keskin"],
        "min_oran": hedef_oran,
        "kupon": kupon,
        "marj": ayarlar["marj"],
        "oncelik": oncelik,
        # "tek başına 2.00+ değer" listesi ölçümde zararlı çıktı (bkz. sistem.py);
        # alan uyumluluk için duruyor, içerik boş.
        "tekliler": [],
        "karne": sistem.karne_tablosu() + sistem.fiyatlanamaz_satirlari(),
        "karne_not": sistem.KARNE_NOT,
        "strateji": (sistem.kazanc_karne(hedef_oran, esik) if oncelik == "kazanc"
                     else (sistem.sans_karne(ayarlar["sans_bacak"]) if oncelik == "sans"
                           else sistem.strateji_karne(hedef_oran, esik))),
        # "tutmuyor" şikâyetinin panzehiri: bu şansla ne beklenmeli
        "beklenti": sistem.beklenti(kupon["p"], 10) if kupon else None,
        "notlar": sistem.notlar(adaylar["oransiz"], kapsam=ayarlar["kapsam"],
                                 sig_mac=len(maclar) - derin_mac),
    }


# ─────────────────────────────────────────────────── Sürpriz Radarı

FOKUS = ("1/1", "1/0", "1/2", "0/1", "0/0", "0/2", "2/1", "2/0", "2/2")
SURPRIZ = ("1/0", "1/2", "2/1", "2/0")
MOD_SIRA = {"model": 0, "kalip": 1, "piyasa": 2, "liste": 3}


def surpriz_radari_hesapla(df, fik, tarih: str, butce_sn: float = 25.0) -> list[dict]:
    """Günün TÜM maçlarını İY/MS çapraz kombinasyonlarına göre tarar.

    Kademeli: takımlar arşivde çözülüyorsa model+kalıp harmanı; takım
    çözülemiyor ama 1X2 oranı biliniyorsa yalnız kalıp frekansı; o da
    yoksa Bet365'in gerçek İY/MS fiyatı tek başına gösterilir. Maç hiçbir
    koşulda listeden düşmez — veri eksikse nedeni satırda yazar.
    """
    hedef = gun_satirlari(fik, tarih)
    satirlar = []
    # İlk taramada onlarca piyasa isteği yavaş ağda yanıtı geciktirmesin;
    # bütçe dolunca kalan maçlar piyasasız döner, sonraki tarama önbellekten tamamlar.
    piyasa_butce_bitis = time.time() + float(butce_sn)
    for idx, r in hedef.iterrows():
        analizsiz = bool(r.get("analiz_yok", False) is True)
        oranlar, _maks, _ua = fikstur_oranlari(r)
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
                "sonuc": gercek_sonuc(df, r),
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
    satirlar.sort(key=lambda x: (x.get("isaretli") is None,
                                 MOD_SIRA.get(x["mod"], 9), -x["surpriz"], x["saat"]))
    return satirlar
