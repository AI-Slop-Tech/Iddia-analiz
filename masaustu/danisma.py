"""Danışma mantığı: maç dokümanı, danışman istemleri, cevap defteri ve karne.

Bu modülde ARAYÜZ YOK — hepsi saf fonksiyon, tamamı test edilebilir. Tkinter
penceresi (pencere.py) yalnız bunları çağırır.

Akış:
  1. mac_dokumani(...)  → elimizdeki her şeyi tek Markdown dokümana yazar
  2. istem_metni(...)   → ChatGPT / Gemini / Grok / Reddit için hazır metin
  3. (kullanıcı yapıştırır, gönderir, cevabı geri yapıştırır)
  4. cevap_ayikla(...)  → cevaptan SONUÇ / SKOR / GÜVEN çeker
  5. kaydet(...)        → data/ai_defteri.json (maç başlamadan; hindsight yok)
  6. sonuclandir(df)    → maç bitince doğru/yanlış işaretler
  7. karne(defter)      → kaynak başına ileriye dönük isabet + bizim modelle kıyas

Dürüstlük kuralları (kod bunları zorlar):
  • Kayıt yalnız kickoff'tan ÖNCE alınır — sonrasında ValueError("hindsight").
  • AI/Reddit cevabı ölçülmemiş görüştür; olasılık modeline asla girmez,
    yalnız sayılır. 20 sonuçlu cevaptan önce karne yargı vermez.
  • Dokümana yalnız ÖLÇÜMÜ GEÇEN pazarlar girer (sistem.guvenilir), her birinin
    karnesi (n / dedi / gerçek) yanında yazar.
  • Kadro/sakatlık yalnız API-Football'dan gelir ve "ölçülmedi" etiketlidir;
    arşivde oyuncu verisi yoktur.
  • Otomatik sohbet sürme / otomatik Reddit postu YOK: program panoya kopyalar,
    göndermeyi kullanıcı yapar.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

import pandas as pd

from iddaa import analiz, sistem, veri

DOSYA = os.path.join(veri.VERI_KLASORU, "ai_defteri.json")
EN_FAZLA_KAYIT = 2000
MIN_YARGI = 20          # bu kadar sonuçlu cevaptan önce karne yorum yapmaz
_KILIT = threading.Lock()

SONUC_ADLARI = {"MS1": "ev sahibi kazanır", "MS0": "berabere", "MS2": "deplasman kazanır"}


# ─────────────────────────────────────────────────────────── doküman

def _yuzde(x, basamak: int = 0) -> str:
    return "—" if x is None else f"%{float(x)*100:.{basamak}f}"


def _bin(n) -> str:
    """Binlik ayracı nokta: 4237 → '4.237'. (Cümledeki virgüllere dokunmaz.)"""
    return f"{int(n or 0):,}".replace(",", ".")


def _form_satiri(f: dict | None) -> str:
    if not f or not f.get("mac"):
        return "veri yok"
    return (f"{f['seri']} · {f['mac']} maçta {f['puan']} puan · "
            f"attığı {f['gol_ort']:.2f} / yediği {f['yenilen_ort']:.2f} (maç başı)")


def _mac_listesi(maclar: list | None, adet: int = 6) -> list[str]:
    cikti = []
    for m in (maclar or [])[:adet]:
        t = m.get("tarih")
        tarih = pd.Timestamp(t).strftime("%d.%m.%Y") if t is not None else ""
        cikti.append(f"  - {tarih} {m.get('ev')} {m.get('skor')} {m.get('dep')}".rstrip())
    return cikti or ["  - kayıt yok"]


def _pinnacle_bul(ev: str, dep: str, tarih):
    try:
        indeks = veri.pinnacle_indeksi(veri.pinnacle_oranlari())
        return veri.pinnacle_esle(indeks, ev, dep, tarih) if indeks else None
    except Exception:  # noqa: BLE001 — keskin fiyat yoksa doküman yine yazılır
        return None


def _canli_bul(ev: str, dep: str, tarih, df=None):
    try:
        gun = pd.Timestamp(tarih).strftime("%Y-%m-%d")
        indeks = veri.canli_indeksi(veri.canli_skorlar(gun))
        if not indeks:
            return None
        cozucu = veri.takim_cozucu(df, hizli=True) if df is not None else None
        return veri.canli_esle(indeks, ev, dep, pd.Timestamp(tarih), cozucu=cozucu)
    except Exception:  # noqa: BLE001
        return None


def _pazar_satirlari(pazarlar: dict, esik: float, adet: int) -> list[str]:
    """Yalnız ÖLÇÜMÜ GEÇEN pazarlar, olasılığa göre; her satırda karnesi."""
    adaylar = []
    for pazar, p in (pazarlar or {}).items():
        uygun, _neden = sistem.guvenilir(pazar)
        if not uygun or p is None or float(p) < esik:
            continue
        adaylar.append((float(p), pazar))
    adaylar.sort(reverse=True)
    satirlar = []
    for p, pazar in adaylar[:adet]:
        k = sistem.karne(pazar) or {}
        bant = sistem.bant_karnesi(pazar, p)
        olcum = (f"bandında {_bin(bant[0])} maç: model {_yuzde(bant[1], 1)} dedi, "
                 f"gerçek {_yuzde(bant[2], 1)}"
                 if bant else
                 f"tüm dağılımda {_bin(k.get('n'))} maç, sapma "
                 f"{(k.get('fark') or 0)*100:+.1f} puan")
        satirlar.append(f"  - {pazar}: {_yuzde(p, 1)} · adil oran {1/max(p,1e-6):.2f} · "
                        f"sitede beklenen ~{sistem.gercekci_fiyat(p):.2f} · ölçüm: {olcum}")
    return satirlar or ["  - eşiği geçen ölçülmüş pazar yok"]


def mac_dokumani(df, elo, ev: str, dep: str, tarih, oranlar=None, lig=None,
                 af: bool = True, pazar_esik: float = 0.55, pazar_adet: int = 22) -> str:
    """Elimizdeki her şeyi tek Markdown dokümanda toplar (danışmanlara gidecek metin)."""
    t = pd.Timestamp(tarih)
    a = analiz.mac_analizi(df, ev, dep, oranlar=tuple(oranlar) if oranlar else None,
                           elo=elo, lig_ipucu=lig)
    poi = a["poisson"]
    try:
        korner = analiz.korner_beklentisi(df, ev, dep, lig)
    except Exception:  # noqa: BLE001
        korner = None
    try:
        kart = analiz.kart_beklentisi(df, ev, dep, lig)
    except Exception:  # noqa: BLE001
        kart = None
    try:
        pazarlar = analiz.tum_pazarlar(poi, korner, kart)
    except Exception:  # noqa: BLE001
        pazarlar = {}

    s: list[str] = []
    s.append(f"# {ev} – {dep}")
    s.append(f"{t.strftime('%d.%m.%Y %H:%M')} · {lig or 'lig bilinmiyor'} · "
             f"doküman {veri.simdi_tr().strftime('%d.%m.%Y %H:%M')} tarihinde üretildi")
    s.append("")
    s.append("Aşağıdaki sayılar 365 binden fazla maçlık arşivden ve örneklem dışı ölçülmüş bir "
             "modelden geliyor. Tahmin senden isteniyor; verileri uydurma, yalnız burada yazanları kullan.")
    s.append("")

    # ① piyasa
    s.append("## 1) Piyasa")
    if oranlar:
        toplam = sum(1 / float(o) for o in oranlar if o)
        s.append(f"- Bülten 1X2: **{float(oranlar[0]):.2f} / {float(oranlar[1]):.2f} / "
                 f"{float(oranlar[2]):.2f}** (kitapçı marjı ~%{(toplam-1)*100:.1f})")
    else:
        s.append("- Bülten oranı yok")
    pin = _pinnacle_bul(ev, dep, t)
    if pin and (pin.get("adil") or {}):
        adil = pin["adil"]
        s.append("- Pinnacle (dünyanın en keskin kitapçısı) **marjsız** olasılıkları: "
                 + ", ".join(f"{k} {_yuzde(v, 1)}" for k, v in list(adil.items())[:8]))
        s.append("  (piyasa uzun vadede modelden daha iyi kalibre olur — modelle çeliştiğinde piyasayı ciddiye al)")
    else:
        s.append("- Pinnacle keskin fiyatı bu maç için yok")
    s.append("")

    # ② model
    s.append("## 2) Model (Poisson + Dixon-Coles, arşivden)")
    s.append(f"- Beklenen gol: **{ev} {poi['lambda_ev']:.2f} – {dep} {poi['lambda_dep']:.2f}**")
    s.append(f"- Maç sonucu: 1 {_yuzde(poi['ms1'], 1)} · X {_yuzde(poi['ms0'], 1)} · 2 {_yuzde(poi['ms2'], 1)}")
    s.append(f"- 2.5 Üst {_yuzde(poi['ust25'], 1)} · 2.5 Alt {_yuzde(poi['alt25'], 1)} · "
             f"KG Var {_yuzde(poi['kg_var'], 1)}")
    # poisson["skorlar"]: en olası 6 skor, (skor, olasılık) ikilileri hâlinde sıralı
    skorlar = [(str(k), float(v)) for k, v in (poi.get("skorlar") or [])[:6]]
    if skorlar:
        s.append("- En olası skorlar: " + " · ".join(f"{k} {_yuzde(v, 1)}" for k, v in skorlar))
    s.append("")

    # ③ ölçümü geçen pazarlar
    s.append("## 3) Ölçülmüş pazarlar (yalnız kalibrasyon kapısını geçenler)")
    s.append("Her satırdaki \"ölçüm\", o pazarın 8.000 maçlık örneklem dışı testte ne yaptığıdır.")
    s += _pazar_satirlari(pazarlar, pazar_esik, pazar_adet)
    s.append("")

    # ④ form
    s.append("## 4) Form")
    s.append(f"- {ev}: {_form_satiri(a.get('form_ev'))}")
    s.append(f"  - kendi sahasında son 5: {_form_satiri(a.get('form_ev_saha'))}")
    s += _mac_listesi((a.get("form_ev") or {}).get("son_maclar"), 5)
    s.append(f"- {dep}: {_form_satiri(a.get('form_dep'))}")
    s.append(f"  - deplasmanda son 5: {_form_satiri(a.get('form_dep_saha'))}")
    s += _mac_listesi((a.get("form_dep") or {}).get("son_maclar"), 5)
    s.append("")

    # ⑤ aralarındaki maçlar
    h2h = a.get("h2h") or {}
    s.append("## 5) Aralarındaki maçlar")
    if h2h.get("mac"):
        s.append(f"- {h2h['mac']} maç · {ev} {h2h.get('ev_galibiyet', 0)} – "
                 f"{h2h.get('beraberlik', 0)} beraberlik – {h2h.get('dep_galibiyet', 0)} {dep}")
        s.append(f"- Gol ortalaması {h2h.get('gol_ort', 0):.2f} · KG Var {_yuzde(h2h.get('kg_var'), 0)}")
        s += _mac_listesi(h2h.get("son_maclar"), 6)
    else:
        s.append("- daha önce karşılaşmamışlar (arşivde kayıt yok)")
    s.append("")

    # ⑥ Elo
    s.append("## 6) Elo")
    if elo and ev in elo and dep in elo:
        s.append(f"- {ev} {elo[ev]:.0f} · {dep} {elo[dep]:.0f} · fark {elo[ev]-elo[dep]:+.0f}")
    else:
        s.append("- Elo puanı yok (takım arşivde yeterince maç oynamamış)")
    s.append("")

    # ⑦ korner / şut / kart
    s.append("## 7) Korner ve kart")
    if korner:
        s.append(f"- Beklenen korner: toplam **{korner['toplam']}** ({ev} {korner['ev']} / "
                 f"{dep} {korner['dep']}) · lig ortalaması {korner['lig_ort']}")
        s.append("  - " + " · ".join(f"{c} Üst {_yuzde(p, 0)}" for c, p in korner["ustler"].items()))
    else:
        s.append("- Korner verisi bu ligde yok (arşivin ~%35'inde korner var)")
    if kart:
        s.append(f"- Beklenen kart: toplam **{kart['toplam']}** ({ev} {kart['ev']} / {dep} {kart['dep']})")
    else:
        s.append("- Kart verisi bu ligde yok")
    s.append("")

    # ⑧ ilk yarı / devre arası
    s.append("## 8) İlk yarı")
    iy = poi.get("iy") or {}
    if iy:
        s.append(f"- İY sonucu: 1 {_yuzde(iy.get('ms1'), 0)} · X {_yuzde(iy.get('ms0'), 0)} · "
                 f"2 {_yuzde(iy.get('ms2'), 0)} · İY 0.5 Üst {_yuzde(iy.get('ust05'), 0)}")
    s.append("- Devre arası tablosu (İY skoruna göre test döneminde GERÇEKTE ne oldu):")
    for skor, d in list(sistem.DEVRE_TABLO.items())[:6]:
        s.append(f"  - İY {skor} ({_bin(d['n'])} maç): MS1 {_yuzde(d['MS1'], 0)} · X {_yuzde(d['MS0'], 0)} · "
                 f"MS2 {_yuzde(d['MS2'], 0)} · 2.5 Üst {_yuzde(d['ÜST 2.5'], 0)} · "
                 f"KG {_yuzde(d['KG VAR'], 0)}")
    s.append("")

    # ⑨ kadro / sakatlık
    s.append("## 9) Kadro ve sakatlıklar")
    kadro = sakat = None
    if af:
        try:
            kadro = veri.af_kadro(ev, dep, t)
            sakat = veri.af_sakatlik(ev, dep, t)
        except Exception:  # noqa: BLE001
            kadro = sakat = None
    if kadro:
        for yan, etiket in (("ev", ev), ("dep", dep)):
            k = kadro.get(yan) or {}
            if not k:
                continue
            isimler = ", ".join(x.get("ad") or "" for x in (k.get("ilk11") or [])[:11])
            s.append(f"- **{etiket}** ({k.get('dizilis') or 'diziliş yok'}): {isimler or 'kadro yok'}")
    else:
        s.append("- Açıklanmış kadro yok (kadrolar genelde maça ~40 dk kala yayımlanır; "
                 "API-Football anahtarı girilmemişse hiç gelmez)")
    if sakat:
        for yan, etiket in (("ev", ev), ("dep", dep)):
            liste = sakat.get(yan) or []
            if liste:
                s.append(f"- **{etiket} eksikler**: " + ", ".join(
                    f"{x.get('ad')} ({x.get('sebep') or x.get('tur') or 'belirtilmemiş'})" for x in liste[:10]))
    else:
        s.append("- Sakatlık/ceza listesi yok")
    s.append("  *(Bu bölüm ÖLÇÜLMEDİ: arşivde oyuncu verisi yok, kadronun sonuca etkisi bizim "
             "tarafımızdan test edilmedi. Yorumlarken bunu bil.)*")
    s.append("")

    # ⑩ canlı durum
    cs = _canli_bul(ev, dep, t, df)
    s.append("## 10) Canlı durum")
    if cs and cs.get("durum") not in (None, "baslamadi"):
        skor = veri.canli_skor_metni(cs) or "?"
        s.append(f"- Maç **{cs['durum']}** · skor {skor}"
                 + (f" · {cs.get('dakika')}" if cs.get("dakika") else "")
                 + (f" · İY {cs['iy_ev']}-{cs['iy_dep']}"
                    if cs.get("iy_ev") is not None and cs.get("iy_dep") is not None else ""))
    else:
        s.append("- Maç henüz başlamadı")
    s.append("")

    # ⑪ modelin kendi sınırları
    s.append("## 11) Bu modelin bilmedikleri")
    s.append("- Sakatlık/kadro (yukarıdaki bölüm hariç), hakem, hava, motivasyon, kupa yorgunluğu, "
             "transfer/teknik direktör değişikliği: model bunları GÖRMÜYOR.")
    s.append("- Ölçüldü ve dürüstçe söylenir: bu model piyasayı düzenli olarak YENMİYOR. "
             "Piyasa fiyatı çoğu zaman modelden daha iyi kalibre.")
    s.append("")
    return "\n".join(s)


# ─────────────────────────────────────────────────────── danışmanlar

CEVAP_BICIMI = ("SONUÇ: <MS1|MS0|MS2> | SKOR: <a-b> | GÜVEN: %<0-100> | "
                "PAZAR: <istersen tek bir pazar, ör. ÜST 2.5> | GEREKÇE: <2-4 cümle>")

HEDEFLER: dict[str, dict] = {
    "chatgpt": {"ad": "ChatGPT", "kisa": "ChatGPT", "url": "https://chatgpt.com/", "tur": "ai"},
    "gemini": {"ad": "Gemini", "kisa": "Gemini", "url": "https://gemini.google.com/app", "tur": "ai"},
    "grok": {"ad": "Grok", "kisa": "Grok", "url": "https://grok.com/", "tur": "ai"},
    "reddit": {"ad": "Reddit (r/soccerbetting)", "kisa": "Reddit",
               "url": "https://www.reddit.com/r/soccerbetting/submit", "tur": "insan"},
}

_AI_BASLIK = (
    "Aşağıda bir futbol maçının elimdeki bütün verisi var: örneklem dışı ölçülmüş model "
    "olasılıkları, keskin piyasa fiyatları, form, aralarındaki maçlar, korner/kart beklentisi, "
    "varsa kadro ve sakatlıklar.\n\n"
    "Senden istediğim: bu verilere bakıp KENDİ tahminini vermen. Kurallar:\n"
    "- Verileri uydurma, yalnız aşağıdakileri kullan; eksik bir şey varsa \"veri yok\" de.\n"
    "- Modelle aynı şeyi söylemek zorunda değilsin; katılmıyorsan nedenini yaz.\n"
    "- \"Garanti\" deme; olasılık dilinde konuş.\n"
    "- Cevabının İLK satırı tam olarak şu biçimde olsun:\n"
    f"  {CEVAP_BICIMI}\n"
    "  Sonra istediğin kadar açıklama yazabilirsin.\n\n"
    "--- MAÇ DOSYASI ---\n")

_REDDIT_UYARI = ("Not: Reddit'e otomatik gönderim YOK — bu metin panona kopyalandı, göndermeden önce "
                 "alt başlığın kurallarını ve flair zorunluluğunu kontrol et. Veri yığını değil, "
                 "kısa ve okunur bir post daha çok cevap alır.")


def _reddit_metni(dokuman: str, ev: str, dep: str) -> str:
    """Reddit için kısa, insan diline yakın post (veri duvarı değil)."""
    sat = dokuman.splitlines()

    def _bul(baslik, adet):
        try:
            i = sat.index(baslik)
        except ValueError:
            return []
        return [x for x in sat[i + 1:i + 1 + adet + 4] if x.startswith("- ")][:adet]

    govde = [f"**{ev} vs {dep}** — modelim ne diyor, siz ne diyorsunuz?", ""]
    govde.append("Kendi modelimi (365k maçlık arşiv, örneklem dışı kalibre edilmiş Poisson) çalıştırdım:")
    govde += _bul("## 2) Model (Poisson + Dixon-Coles, arşivden)", 3)
    piyasa = _bul("## 1) Piyasa", 2)
    if piyasa:
        govde += ["", "Piyasa tarafı:"] + piyasa
    form = _bul("## 4) Form", 1)
    if form:
        govde += ["", "Form:"] + form
    govde += [
        "",
        "Sorum: bu maçta modelin göremediği bir şey var mı (sakatlık, motivasyon, kadro rotasyonu, "
        "hava/saha)? Sizce hangi tarafa/pazara yatmak mantıklı ve neden?",
        "",
        "_Model piyasayı düzenli yenmiyor, o yüzden insan gözüne ihtiyacım var. Ücretli bir servis "
        "satmıyorum, kendi hobim._",
    ]
    return "\n".join(govde)


def istem_metni(dokuman: str, hedef: str, ev: str = "", dep: str = "") -> str:
    """Hedefe göre panoya kopyalanacak metin. Bilinmeyen hedef → ValueError."""
    if hedef not in HEDEFLER:
        raise ValueError(f"Bilinmeyen danışman: {hedef}")
    if HEDEFLER[hedef]["tur"] == "insan":
        return _reddit_metni(dokuman, ev, dep)
    return _AI_BASLIK + dokuman


# ─────────────────────────────────────────────────────── cevap ayıklama

_SONUC_KALIP = re.compile(
    r"\b(?:SONU[ÇC]|RESULT|TAHMİN|TAHMIN)\s*[:=]?\s*"
    r"(MS\s*[102]|1X|X2|12|EV|DEP|BERABERE|HOME|DRAW|AWAY|[102])\b", re.IGNORECASE)
_SKOR_KALIP = re.compile(r"\b(?:SKOR|SCORE)\s*[:=]?\s*(\d{1,2})\s*[-–:]\s*(\d{1,2})\b", re.IGNORECASE)
_SKOR_SERBEST = re.compile(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b")
_GUVEN_KALIP = re.compile(r"\b(?:G[ÜU]VEN|CONFIDENCE)\s*[:=]?\s*%?\s*(\d{1,3})\s*%?", re.IGNORECASE)
_PAZAR_KALIP = re.compile(r"\b(?:PAZAR|MARKET)\s*[:=]?\s*([^|\n]{2,28})", re.IGNORECASE)

_SONUC_HARITA = {
    "MS1": "MS1", "MS0": "MS0", "MS2": "MS2", "1": "MS1", "0": "MS0", "2": "MS2",
    "EV": "MS1", "DEP": "MS2", "BERABERE": "MS0", "HOME": "MS1", "DRAW": "MS0", "AWAY": "MS2",
}


def cevap_ayikla(metin: str) -> dict:
    """Danışman cevabından yapılandırılmış tahmin. Bulunamayan alan None kalır.

    Dönen: {"sonuc", "skor", "guven", "pazar", "ham"} — hiçbiri zorunlu değil,
    kullanıcı arayüzde eksik alanı elle doldurur."""
    metin = str(metin or "")
    sonuc = skor = guven = pazar = None

    m = _SONUC_KALIP.search(metin)
    if m:
        ham = re.sub(r"\s+", "", m.group(1)).upper()
        sonuc = _SONUC_HARITA.get(ham)
        if sonuc is None and ham in ("1X", "X2", "12"):
            sonuc = "ÇŞ " + ham

    m = _SKOR_KALIP.search(metin)
    if not m:
        m = _SKOR_SERBEST.search(metin)
    if m:
        e, d = int(m.group(1)), int(m.group(2))
        if e <= 15 and d <= 15:
            skor = f"{e}-{d}"
            if sonuc is None:
                sonuc = "MS1" if e > d else ("MS0" if e == d else "MS2")

    m = _GUVEN_KALIP.search(metin)
    if m:
        g = int(m.group(1))
        if 0 <= g <= 100:
            guven = g / 100.0

    m = _PAZAR_KALIP.search(metin)
    if m:
        aday = m.group(1).strip(" .*_-")
        if aday and aday.lower() not in ("yok", "none", "-"):
            pazar = aday[:28]

    return {"sonuc": sonuc, "skor": skor, "guven": guven, "pazar": pazar,
            "ham": metin.strip()[:4000]}


# ─────────────────────────────────────────────────────────── defter

def _oku() -> list[dict]:
    try:
        with open(DOSYA, encoding="utf-8") as f:
            veriler = json.load(f)
        return veriler if isinstance(veriler, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _yaz(kayitlar: list[dict]) -> None:
    os.makedirs(os.path.dirname(DOSYA), exist_ok=True)
    gecici = DOSYA + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kayitlar[:EN_FAZLA_KAYIT], f, ensure_ascii=False, indent=1)
    os.replace(gecici, DOSYA)


def defter_oku() -> list[dict]:
    return _oku()


def mac_anahtari(ev: str, dep: str, tarih) -> str:
    return f"{pd.Timestamp(tarih).strftime('%d.%m.%Y')}|{str(ev).strip()}|{str(dep).strip()}"


def kaydet(kaynak: str, ev: str, dep: str, tarih, cevap: dict, lig: str = "",
           model_p: dict | None = None, simdi=None) -> dict:
    """Bir danışmanın cevabını deftere yazar.

    Kickoff geçtiyse ValueError("hindsight ..."): maç başladıktan sonra alınan
    tahmin ölçüm değildir. Aynı (maç, kaynak) ikinci kez yazılırsa üstüne yazar
    (kullanıcı cevabı düzeltebilsin) ama kickoff kuralı yine geçerlidir."""
    if kaynak not in HEDEFLER:
        raise ValueError(f"Bilinmeyen danışman: {kaynak}")
    t = pd.Timestamp(tarih)
    simdi = pd.Timestamp(simdi or veri.simdi_tr())
    if simdi >= t:
        raise ValueError("hindsight: maç başladıktan sonra tahmin kaydedilmez")
    if not (cevap or {}).get("sonuc") and not (cevap or {}).get("skor"):
        raise ValueError("Cevapta ne sonuç ne skor var — en az biri gerekli.")
    kayit = {
        "id": None,      # aşağıda kilit içinde verilir (çakışmasın)
        "anahtar": mac_anahtari(ev, dep, t),
        "kaynak": kaynak,
        "ev": str(ev)[:60], "dep": str(dep)[:60], "lig": str(lig or "")[:40],
        "tarih": t.strftime("%d.%m.%Y"), "saat": t.strftime("%H:%M"),
        "kayit_zamani": simdi.strftime("%d.%m.%Y %H:%M"),
        "sonuc": cevap.get("sonuc"), "skor": cevap.get("skor"),
        "guven": cevap.get("guven"), "pazar": cevap.get("pazar"),
        "ham": (cevap.get("ham") or "")[:4000],
        "model_p": model_p or None,      # aynı maçta bizim modelin dediği (kıyas için)
        "durum": "bekliyor", "gercek_skor": None, "isabet": None, "skor_isabet": None,
    }
    with _KILIT:
        defter = _oku()
        eski = next((k for k in defter if k.get("anahtar") == kayit["anahtar"]
                     and k.get("kaynak") == kaynak), None)
        if eski and not kayit["model_p"]:
            # Cevabı düzeltmek modelin kıyas kaydını silmemeli
            kayit["model_p"] = eski.get("model_p")
        defter = [k for k in defter if k is not eski]
        # Kimlik saat damgasından türer ama ÇAKIŞMAZ: aynı milisaniyede yazılan
        # iki kayıt aynı id alırsa sil() ikisini birden siliyordu.
        kayit["id"] = max([int(time.time() * 1000)]
                          + [int(k.get("id") or 0) + 1 for k in defter])
        defter.insert(0, kayit)
        _yaz(defter)
    return kayit


def sil(kayit_id: int) -> bool:
    with _KILIT:
        defter = _oku()
        yeni = [k for k in defter if k.get("id") != kayit_id]
        if len(yeni) == len(defter):
            return False
        _yaz(yeni)
        return True


def _gercek_skor(df, ev: str, dep: str, tarih, cozucu=None):
    """Biten maçın skoru: önce canlı besleme (bugün/dün), sonra arşiv."""
    t = pd.Timestamp(tarih)
    simdi = veri.simdi_tr()
    if simdi.normalize() - pd.Timedelta(days=1) <= t.normalize() <= simdi.normalize():
        cs = _canli_bul(ev, dep, t, df)
        if (cs and cs.get("durum") == "bitti" and not cs.get("uzatma")
                and cs.get("ev_gol") is not None):
            return int(cs["ev_gol"]), int(cs["dep_gol"])
    if df is None or t.normalize() >= simdi.normalize():
        return None
    try:
        ev_c = cozucu(ev) if cozucu else ev
        dep_c = cozucu(dep) if cozucu else dep
    except Exception:  # noqa: BLE001
        ev_c, dep_c = ev, dep
    aday = df[(df["Tarih"] >= t.normalize() - pd.Timedelta(days=1))
              & (df["Tarih"] <= t.normalize() + pd.Timedelta(days=1))
              & (df["HomeTeam"] == ev_c) & (df["AwayTeam"] == dep_c)]
    if aday.empty:
        return None
    r = aday.iloc[-1]
    return int(r["FTHG"]), int(r["FTAG"])


def sonuclandir(df=None) -> list[dict]:
    """Bekleyen kayıtları biten maçlarla eşler, isabeti işaretler."""
    with _KILIT:
        defter = _oku()
        try:
            cozucu = veri.takim_cozucu(df, hizli=True) if df is not None else None
        except Exception:  # noqa: BLE001
            cozucu = None
        degisti = False
        for k in defter:
            if k.get("durum") != "bekliyor":
                continue
            try:
                t = pd.to_datetime(f"{k['tarih']} {k.get('saat') or '12:00'}", dayfirst=True)
            except (ValueError, TypeError):
                continue
            skor = _gercek_skor(df, k["ev"], k["dep"], t, cozucu)
            if not skor:
                continue
            e, d = skor
            gercek_ms = "MS1" if e > d else ("MS0" if e == d else "MS2")
            k["gercek_skor"] = f"{e}-{d}"
            k["durum"] = "sonuclandi"
            k["isabet"] = (k.get("sonuc") == gercek_ms) if k.get("sonuc") else None
            k["skor_isabet"] = (k.get("skor") == k["gercek_skor"]) if k.get("skor") else None
            k["gercek_sonuc"] = gercek_ms
            degisti = True
        if degisti:
            _yaz(defter)
        return defter


def karne(defter: list[dict] | None = None) -> dict:
    """Kaynak başına ileriye dönük sayım + aynı maçlarda bizim modelin isabeti.

    Bu bir KALİBRASYON ÖLÇÜMÜ DEĞİL, sayımdır: danışmanların cevapları örneklem
    dışı bir teste tabi tutulmadı, sadece maç maç birikiyor."""
    defter = defter if defter is not None else _oku()
    cikti: dict = {}
    for kaynak, bilgi in HEDEFLER.items():
        kayitlar = [k for k in defter if k.get("kaynak") == kaynak]
        sonuclu = [k for k in kayitlar if k.get("durum") == "sonuclandi" and k.get("isabet") is not None]
        tutan = sum(1 for k in sonuclu if k["isabet"])
        skorlu = [k for k in sonuclu if k.get("skor_isabet") is not None]
        guvenli = [k for k in sonuclu if isinstance(k.get("guven"), (int, float))]
        # aynı maçlarda bizim model ne demişti (model_p kaydedilmişse)
        model_dogru = model_n = 0
        for k in sonuclu:
            mp = k.get("model_p") or {}
            if not mp:
                continue
            model_n += 1
            en_iyi = max(mp, key=lambda x: mp[x])
            model_dogru += 1 if en_iyi == k.get("gercek_sonuc") else 0
        cikti[kaynak] = {
            "ad": bilgi["ad"], "tur": bilgi["tur"],
            "n": len(kayitlar), "sonuclu": len(sonuclu), "bekleyen": len(kayitlar) - len(sonuclu),
            "tutan": tutan,
            "isabet": (tutan / len(sonuclu)) if sonuclu else None,
            "skor_isabet": (sum(1 for k in skorlu if k["skor_isabet"]) / len(skorlu)) if skorlu else None,
            "skor_n": len(skorlu),
            "dedi_guven": (sum(k["guven"] for k in guvenli) / len(guvenli)) if guvenli else None,
            "guven_n": len(guvenli),
            "model_isabet": (model_dogru / model_n) if model_n else None,
            "model_n": model_n,
            "yargi": len(sonuclu) >= MIN_YARGI,
        }
    return cikti


NOT = ("Buradaki sayılar ölçüm değil SAYIMDIR: danışmanların (ChatGPT, Gemini, Grok, Reddit) "
       f"cevapları maç başlamadan kaydedilir ve maç bitince doğru/yanlış işaretlenir. {MIN_YARGI} "
       "sonuçlu cevaptan önce hiçbir yargı geçerli değildir. Danışman cevapları olasılık modeline "
       "girmez; kupon kurmaz. Kadro/sakatlık bilgisi API-Football'dan gelir ve etkisi ölçülmemiştir.")
