"""Analist yorumu: sayısal analizi profesyonel Türkçe maç yorumuna çevirir.

Kural tabanlıdır — yapay zekâ anahtarı gerektirmez, her kurulumda çalışır.
Form, saha performansı, H2H, Elo, gol beklentisi, oran kalıbı ve değer
sinyalleri; hangileri mevcutsa onlarla, doğal bir analist diliyle birleştirilir.
(GEMINI_API_KEY tanımlıysa CLI'daki --gemini yorumu ayrıca kullanılabilir.)
"""

from __future__ import annotations

from .analiz import ORAN_TAVANI

SECIM_AD = {
    "MS1": "ev sahibi galibiyeti (MS 1)",
    "MS0": "beraberlik (MS 0)",
    "MS2": "deplasman galibiyeti (MS 2)",
    "ÜST 2.5": "Üst 2.5 gol",
    "ALT 2.5": "Alt 2.5 gol",
}


def _y(p: float) -> str:
    return f"%{p * 100:.0f}"


def _guc_dengesi(a: dict) -> str:
    ev, dep = a["ev"], a["dep"]
    cumleler = []

    elo = a.get("elo")
    if elo:
        fark = elo["fark"]
        onde = ev if fark > 0 else dep
        if abs(fark) >= 120:
            cumleler.append(
                f"Güç dengesi belirgin biçimde {onde} lehine: iki takım arasındaki "
                f"Elo farkı {abs(fark):.0f} puan — bu, lig ortalamasının epey üzerinde bir makas."
            )
        elif abs(fark) >= 50:
            cumleler.append(
                f"Elo reytingi {onde} tarafına {abs(fark):.0f} puanlık anlamlı ama "
                f"kapatılabilir bir üstünlük veriyor."
            )
        else:
            cumleler.append("Elo reytingleri neredeyse başa baş; kâğıt üstünde dengeli bir eşleşme.")

    fe, fd = a["form_ev"], a["form_dep"]
    if fe["mac"] >= 5 and fd["mac"] >= 5:
        pf = fe["puan"] - fd["puan"]
        if pf >= 6:
            cumleler.append(
                f"Yakın dönem form da aynı yönü gösteriyor: {ev} son {fe['mac']} maçta "
                f"{fe['puan']} puan toplarken {dep} {fd['puan']} puanda kaldı."
            )
        elif pf <= -6:
            cumleler.append(
                f"Formda ibre konuktan yana: {dep} son {fd['mac']} maçta {fd['puan']} puan "
                f"topladı; {ev} aynı dönemde {fe['puan']} puanda."
            )
        else:
            cumleler.append(
                f"Form tablosu dengeli: son {fe['mac']} maçta {ev} {fe['puan']}, "
                f"{dep} {fd['puan']} puan üretti."
            )
        cumleler.append(
            f"Gol profillerinde {ev} maç başına {fe['gol_ort']:.1f} atıp {fe['yenilen_ort']:.1f} yerken, "
            f"{dep} {fd['gol_ort']:.1f} atıp {fd['yenilen_ort']:.1f} yiyor."
        )

    fes, fds = a["form_ev_saha"], a["form_dep_saha"]
    if fes["mac"] >= 3 and fds["mac"] >= 3:
        cumleler.append(
            f"Saha kırılımı önemli: {ev} iç sahada son {fes['mac']} maçında {fes['puan']} puan "
            f"({fes['seri']}), {dep} ise deplasmanda {fds['puan']} puan ({fds['seri']}) çıkardı."
        )
    return " ".join(cumleler)


def _h2h(a: dict) -> str:
    h = a["h2h"]
    if h["mac"] < 3:
        return ""
    ev, dep = a["ev"], a["dep"]
    cumleler = []
    if h["ev_galibiyet"] >= h["dep_galibiyet"] + 3:
        cumleler.append(
            f"İkili geçmiş {ev} cephesinde: son {h['mac']} karşılaşmada {h['ev_galibiyet']} "
            f"galibiyete karşılık {dep} yalnızca {h['dep_galibiyet']} kez kazandı."
        )
    elif h["dep_galibiyet"] >= h["ev_galibiyet"] + 3:
        cumleler.append(
            f"Eşleşmenin tarihi {dep}'i işaret ediyor: son {h['mac']} maçta {h['dep_galibiyet']} "
            f"galibiyet aldı, {ev} {h['ev_galibiyet']}'te kaldı."
        )
    else:
        cumleler.append(
            f"Aralarındaki {h['mac']} maçta belirgin bir taraf yok "
            f"({h['ev_galibiyet']}-{h['beraberlik']}-{h['dep_galibiyet']})"
            + (f"; beraberlik oranı dikkat çekici: {_y(h['beraberlik'] / h['mac'])}." if h["beraberlik"] / h["mac"] >= 0.4 else ".")
        )
    if h["gol_ort"] <= 2.1:
        cumleler.append(
            f"Bu eşleşme geleneksel olarak kapalı geçer: maç başına {h['gol_ort']:.1f} gol, "
            f"Üst 2.5 yalnızca {_y(h['ust25'])}."
        )
    elif h["gol_ort"] >= 2.9:
        cumleler.append(
            f"İkili tarihçe gol vaat ediyor: maç başına {h['gol_ort']:.1f} gol, "
            f"karşılıklı gol {_y(h['kg_var'])} oranında geldi."
        )
    return " ".join(cumleler)


def _gol_beklentisi(a: dict) -> str:
    p = a["poisson"]
    ev, dep = a["ev"], a["dep"]
    toplam = p["lambda_ev"] + p["lambda_dep"]
    if toplam <= 2.2:
        tempo = "düşük tempolu, temkinli bir maç profili"
    elif toplam < 2.9:
        tempo = "orta tempolu bir maç profili"
    else:
        tempo = "açık ve gollü bir maç profili"
    cumleler = [
        f"Model gol beklentisini {ev} {p['lambda_ev']:.2f} – {p['lambda_dep']:.2f} {dep} "
        f"olarak kuruyor; toplam {toplam:.1f} gol bandı {tempo} anlatıyor."
    ]

    k = a.get("kalip")
    if k:
        if (p["ust25"] >= 0.55) == (k["ust25"] >= 0.55) and abs(p["ust25"] - 0.5) > 0.04:
            yon = "Üst" if p["ust25"] >= 0.55 else "Alt"
            cumleler.append(
                f"Alt/Üst cephesinde iki bağımsız sinyal aynı yöne bakıyor: Poisson {_y(p['ust25'])} "
                f"ile, benzer oranla açılan {k['n']} tarihsel maç da {_y(k['ust25'])} ile {yon} 2.5'i destekliyor."
            )
        elif abs(p["ust25"] - k["ust25"]) >= 0.08:
            cumleler.append(
                f"Alt/Üst tarafında sinyaller ayrışıyor (Poisson {_y(p['ust25'])} Üst derken tarihsel "
                f"kalıp {_y(k['ust25'])} diyor) — bu pazara mesafeli durmakta fayda var."
            )
    if p["kg_var"] >= 0.6:
        cumleler.append(f"Karşılıklı gol olasılığı {_y(p['kg_var'])} ile güçlü.")
    elif p["kg_var"] <= 0.45:
        cumleler.append(f"Karşılıklı gol beklentisi zayıf ({_y(p['kg_var'])}).")
    en_olasi = p["skorlar"][0][0] if p["skorlar"] else None
    if en_olasi:
        cumleler.append(f"En olası tekil skor {en_olasi}.")
    return " ".join(cumleler)


def _deger(a: dict) -> str:
    d, o, k = a.get("deger"), a.get("oneri"), a.get("kalip")
    if not d or not o:
        return (
            "Bültendeki 1X2 oranları girildiğinde bu analize oran kalıbı ve değer katmanı da eklenir; "
            "profesyonel karar, olasılıkla oranın kıyasından çıkar."
        )
    cumleler = []
    if k:
        cumleler.append(
            f"Piyasa tarafında, bu oran profiliyle açılan {k['n']} tarihsel maçın dağılımı "
            f"{_y(k['ms1'])} ev / {_y(k['ms0'])} beraberlik / {_y(k['ms2'])} deplasman."
        )

    en_iyi_genel = max(d["satirlar"], key=lambda s: s["ev"])
    secim_adi = SECIM_AD.get(o["secim"], o["secim"])

    if o["karar"] == "degerli":
        cumleler.append(
            f"Değer analizi net bir adres gösteriyor: {secim_adi} @ {o['oran']:.2f}. "
            f"Model bu seçime piyasanın ima ettiğinden daha yüksek olasılık biçiyor; "
            f"beklenen değer +%{o['ev'] * 100:.1f} seviyesinde. Güven {o['yildiz']}/5 — "
            f"kasa payı çeyrek Kelly ile %{o['kelly'] * 100:.1f}'i geçmemeli."
        )
    elif o["karar"] == "sinirda":
        cumleler.append(
            f"En cazip seçim {secim_adi} @ {o['oran']:.2f} görünse de kenar payı ince "
            f"(+%{max(o['ev'], 0) * 100:.1f}); bu, hata payının içinde kalan bir avantaj. "
            f"Oynanacaksa düşük kasa payıyla ve en iyi orandan oynanmalı."
        )
    else:
        if en_iyi_genel["oran"] > ORAN_TAVANI and en_iyi_genel["ev"] >= 0.04:
            cumleler.append(
                f"Kağıt üstünde en yüksek matematiksel değer {SECIM_AD.get(en_iyi_genel['secim'], en_iyi_genel['secim'])} "
                f"@ {en_iyi_genel['oran']:.2f} tarafında görünse de bu bölge sürpriz oran filtresine takılıyor "
                f"({ORAN_TAVANI:.2f} üzeri): yüksek oranlar piyasada sistematik pahalı fiyatlanır ve "
                f"backtest bu tuzağı net biçimde doğruladı. Profesyonel duruş bu maçı pas geçmek."
            )
        else:
            cumleler.append(
                "Bu oranlarla masada gerçek bir değer yok; en iyi seçim bile eksi beklenen değerde. "
                "Uzun vadede kazandıran, değersiz maça para bağlamamaktır — bu maç izlemelik."
            )
    return " ".join(cumleler)


def _riskler(a: dict) -> str:
    notlar = list(a["poisson"]["uyarilar"])
    k = a.get("kalip")
    if k and k["n"] < 100:
        notlar.append(f"oran kalıbı örneklemi görece küçük ({k['n']} maç)")
    if k and a.get("deger"):
        p = a["poisson"]
        poisson_zirve = max(["ms1", "ms0", "ms2"], key=lambda x: p[x])
        kalip_zirve = max(["ms1", "ms0", "ms2"], key=lambda x: k[x])
        if poisson_zirve != kalip_zirve:
            notlar.append("model ile tarihsel kalıp aynı sonucu işaret etmiyor")
    h = a["h2h"]
    if h["mac"] == 0:
        notlar.append("iki takımın veri setinde ortak maçı yok")
    if not notlar:
        return ""
    return (
        "Risk notu: " + "; ".join(notlar) + ". Sakatlık, kadro ve motivasyon gibi "
        "sahadan gelen bilgiler bu modelin görüş alanı dışındadır — nihai karar sizin."
    )


def olustur(a: dict) -> list[str]:
    """mac_analizi() çıktısından paragraf listesi üretir (boşlar ayıklanır)."""
    paragraflar = [
        _guc_dengesi(a),
        _h2h(a),
        _gol_beklentisi(a),
        _deger(a),
        _riskler(a),
    ]
    return [p for p in paragraflar if p.strip()]
