"""Sistem Önerisi: günün tüm maç ve pazarlarını ölçülmüş karneyle sıralayıp kupon kurar.

Bu modül YENİ bir katmandır; mevcut bülten/radar/sağlam akışlarına dokunmaz.
Farkı: bülten maç başına TEK öneri verir, burada bütün maçların bütün
fiyatlanabilen pazarları tek havuzda toplanır, ÖLÇÜLMÜŞ güvenilirliğe göre
sıralanır ve hedef orana ulaşan kupon kurulur.

────────────────────────────────────────────────────────────────────────
ÖLÇÜM (deney22) — nasıl yapıldı, neden güvenilir:
  • 10.000 test maçı, eğitim/test AYRIMI ile: model yalnız 01.07.2023'ten
    ÖNCEKİ maçlarla kuruldu, karne o tarihten SONRAKİ maçlarda ölçüldü.
    Böylece "geleceği görerek" şişmiş bir karne çıkmadı.
  • Her maçta guvenli_secimler() ile üretilen tüm pazarlar kaydedildi
    (71.474 seçim), sonra gerçek skorla karşılaştırıldı.
  • "ayırt gücü" = üst çeyrek gerçek oranı − alt çeyrek gerçek oranı.
    Sıfıra yakınsa model o pazarda maça özel bilgi taşımıyor, yalnız lig
    ortalamasını tekrarlıyor demektir; böyle pazarlar öneriye GİRMEZ.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math

# pazar: ölçülen örneklem, modelin dediği ortalama, gerçekleşen oran, ayırt gücü.
# Sayılar deney22'den birebir üretildi (elle yazılmadı).
#
# ÖLÇÜM SIRASINDA YAKALANAN HATA: ilk turda ust_alt demeti motora ters
# ((alt, üst)) geçilmişti; ÜST/ALT 2.5 satırları çöp çıkmıştı (ALT 2.5 için
# "dedi %54 → gerçek %43"). Motorun sırası (ÜST, ALT) — düzeltilip yeniden
# ölçüldü, doğru değerler aşağıda. Eski tur çöpe atıldı.
PAZAR_KARNE: dict[str, dict] = {
    "2Y GOL VAR": {"n": 6408, "dedi": 0.7768, "gercek": 0.7745, "fark": -0.0023, "ayirt": +0.0655},
    "ÇŞ 12": {"n": 10000, "dedi": 0.7405, "gercek": 0.7383, "fark": -0.0022, "ayirt": +0.1064},
    "ÇŞ 1X": {"n": 9218, "dedi": 0.7212, "gercek": 0.7238, "fark": +0.0026, "ayirt": +0.2626},
    "İY 0.5 ÜST": {"n": 6408, "dedi": 0.6925, "gercek": 0.7122, "fark": +0.0197, "ayirt": +0.0749},
    "KORNER ALT 11.5": {"n": 5728, "dedi": 0.7184, "gercek": 0.7001, "fark": -0.0183, "ayirt": +0.0964},
    "İY 1.5 ALT": {"n": 6312, "dedi": 0.6839, "gercek": 0.6559, "fark": -0.0280, "ayirt": +0.0856},
    "ÇŞ X2": {"n": 6931, "dedi": 0.6361, "gercek": 0.6451, "fark": +0.0090, "ayirt": +0.2431},
    "KORNER ÜST 8.5": {"n": 5728, "dedi": 0.6393, "gercek": 0.6285, "fark": -0.0108, "ayirt": +0.1096},
    "MS1": {"n": 3069, "dedi": 0.6048, "gercek": 0.6194, "fark": +0.0146, "ayirt": +0.2673},
    "MS2": {"n": 782, "dedi": 0.5842, "gercek": 0.6087, "fark": +0.0245, "ayirt": +0.1949},
    "KORNER ALT 10.5": {"n": 5728, "dedi": 0.6091, "gercek": 0.6016, "fark": -0.0075, "ayirt": +0.1013},
    "ÜST 2.5": {"n": 3104, "dedi": 0.5609, "gercek": 0.5818, "fark": +0.0209, "ayirt": +0.1649},
    "HER İKİ YARI GOL VAR": {"n": 5755, "dedi": 0.5529, "gercek": 0.5668, "fark": +0.0139, "ayirt": +0.1134},
    "KG VAR": {"n": 7337, "dedi": 0.5564, "gercek": 0.5487, "fark": -0.0077, "ayirt": +0.0578},
    "ALT 2.5": {"n": 3061, "dedi": 0.5546, "gercek": 0.5446, "fark": -0.0100, "ayirt": +0.0667},
    "KG YOK": {"n": 2663, "dedi": 0.5345, "gercek": 0.5167, "fark": -0.0178, "ayirt": +0.0586},
    "KORNER ÜST 9.5": {"n": 5728, "dedi": 0.5136, "gercek": 0.5150, "fark": +0.0014, "ayirt": +0.0922},
    "HER İKİ YARI GOL YOK": {"n": 653, "dedi": 0.5162, "gercek": 0.4992, "fark": -0.0169, "ayirt": +0.0184},
    "KORNER ALT 9.5": {"n": 5728, "dedi": 0.4864, "gercek": 0.4850, "fark": -0.0014, "ayirt": +0.0915},
    "KORNER ÜST 10.5": {"n": 5728, "dedi": 0.3909, "gercek": 0.3984, "fark": +0.0075, "ayirt": +0.0999},
    "KORNER ALT 8.5": {"n": 5728, "dedi": 0.3607, "gercek": 0.3715, "fark": +0.0108, "ayirt": +0.1089},
    "KORNER ÜST 11.5": {"n": 5728, "dedi": 0.2816, "gercek": 0.2999, "fark": +0.0183, "ayirt": +0.0964},
}

# KORNER NEDEN BURADA VAR: daha önce korner "kalıp" (oran deseni) yaklaşımıyla
# denenmiş ve REDDEDİLMİŞTİ — 9.5 üstü için %57.9 diyip %51.1 tutturmuştu.
# Burada ölçülen BAŞKA bir yaklaşım: takımların zaman ağırlıklı korner
# üretim/yeme oranlarından Poisson beklentisi (analiz.korner_beklentisi).
# İlk kez ölçüldü ve kalibrasyonu iyi çıktı (sapma ≤1.8 puan, ayırt +9…+11).
# Yani reddedilen kalıp yöntemi değil, bu yöntem kullanılıyor.

# ÖLÇÜLEN STRATEJİ KARNESİ (bölüm D/E) — sekmedeki dürüstlük notları buradan:
#   ≥2.00 kombine, eşik %60: 335 kupon · dedi %38.4 · GERÇEK %43.0 · ROI +2.5% (±6.6)
#   ≥2.00 kombine, eşik %65: 136 kupon · dedi %40.5 · GERÇEK %49.3 · ROI +11.6% (±9.9)
#   ≥2.00 tek bacak (değerli): 696 bahis · GERÇEK %49.3 · ROI +1.6%
# Hepsinin ortak dersi: kupon çarpım kuralının söylediğinden biraz DAHA İYİ
# tutuyor, ama ROI'ler kendi hata payları içinde sıfırdan ayırt edilemiyor.
# Yani "kâr garantisi" değil, "başabaşa yakın, ölçülmüş bir seçim disiplini".
STRATEJI_KARNE = {
    "kombine60": {"n": 335, "dedi": 0.384, "gercek": 0.430, "roi": +0.025, "hata": 0.066},
    "kombine65": {"n": 136, "dedi": 0.405, "gercek": 0.493, "roi": +0.116, "hata": 0.099},
    "tekli": {"n": 696, "gercek": 0.493, "roi": +0.016, "hata": 0.039},
}

MIN_ORNEK = 500       # altında karne istatistiksel olarak gürültüdür
MAKS_SAPMA = 0.030    # model ile gerçek arası kabul edilen en büyük fark
MIN_AYIRT = 0.050     # bunun altında model maça özel bilgi taşımıyor demektir

# Arşivde OYUNCU verisi yok (football-data.co.uk maç bazlı: skor, korner, kart,
# şut). "X oyuncusu gol atar" pazarı bu yüzden fiyatlanamaz — tahmin üretmek
# uydurmak olurdu. Kadro/dakika/xG içeren ücretli bir kaynak gerekir.
FIYATLANAMAZ = {
    "oyuncu golü": "arşivde oyuncu verisi yok (maç bazlı veri: skor, korner, kart, şut)",
    "asist / kart göreni": "aynı sebep — oyuncu bazlı olay verisi yok",
    "ilk golü atan": "aynı sebep; ayrıca dakika verisi de yok",
}


def karne(pazar: str) -> dict | None:
    return PAZAR_KARNE.get(pazar)


def guvenilir(pazar: str) -> tuple[bool, str]:
    """Pazar öneri havuzuna girebilir mi? (girer_mi, gerekçe)"""
    k = PAZAR_KARNE.get(pazar)
    if not k:
        return False, "ölçülmedi"
    if k["n"] < MIN_ORNEK:
        return False, f"örneklem küçük (n={k['n']})"
    if abs(k["fark"]) > MAKS_SAPMA:
        yon = "abartıyor" if k["fark"] < 0 else "eksik tahmin"
        return False, f"kalibrasyon bozuk: {yon} ({k['fark']*100:+.1f} puan)"
    if k["ayirt"] < MIN_AYIRT:
        return False, f"ayırt gücü yok ({k['ayirt']*100:+.1f} puan)"
    return True, "ölçümü geçti"


def duzeltilmis(pazar: str, p: float) -> float:
    """Modelin olasılığına ölçülen sistematik sapmayı ekler.

    Kodda zaten uygulanan yaklaşımın aynısı (KG_DUZELTME gibi): model bir
    pazarda sürekli eksik/fazla tahmin ediyorsa, karnede ölçülen farkı
    geri koyarız. Düzeltme ±5 puanla sınırlı — karne farkı bundan büyükse
    o pazar zaten güvenilir sayılmıyor.
    """
    k = PAZAR_KARNE.get(pazar)
    if not k:
        return p
    return max(0.01, min(0.99, p + max(-0.05, min(0.05, k["fark"]))))


def _bacak(aday: dict) -> dict:
    """Havuz kaydını arayüzün beklediği bacak sözlüğüne çevirir."""
    k = PAZAR_KARNE.get(aday["pazar"])
    p = aday["p"]
    oran = aday.get("oran")
    return {
        "mac_id": aday.get("mac_id"),
        "ev_ad": aday["ev_ad"],
        "dep_ad": aday["dep_ad"],
        "saat": aday.get("saat", ""),
        "lig": aday.get("lig", ""),
        "pazar": aday["pazar"],
        "p": float(p),
        "adil": round(1.0 / max(p, 1e-6), 2),
        "oran": float(oran) if oran else None,
        "ev": (float(p) * float(oran) - 1.0) if oran else None,
        "karne": ({"n": k["n"], "dedi": k["dedi"], "gercek": k["gercek"],
                   "ayirt": k["ayirt"], "guvenilir": True} if k else None),
        "gerekce": aday.get("gerekce", []),
    }


TABAN_ESIK = 0.40     # havuz tabanı; kupon eşiği bunun üstünde ayrıca uygulanır
TEKLI_MIN_P = 0.45    # ölçümde (bölüm E) bu tabanla %49.3 isabet / %+1.6 getiri çıktı


def havuz_kur(maclar: list[dict], esik: float = TABAN_ESIK) -> tuple[list[dict], int]:
    """Maçlardan gelen seçim listelerini tek havuzda toplar, ölçüm süzgecinden geçirir.

    maclar: [{"mac_id","ev_ad","dep_ad","saat","lig","secenekler":[{pazar,p,oran,gerekce}]}]
    Döner: (havuz, elenen_sayisi)
    """
    havuz, elenen = [], 0
    for m in maclar:
        for s in m.get("secenekler", []):
            gecer, _neden = guvenilir(s["pazar"])
            if not gecer:
                elenen += 1
                continue
            p = duzeltilmis(s["pazar"], float(s["p"]))
            if p < esik:
                continue
            k = PAZAR_KARNE[s["pazar"]]
            gerekce = list(s.get("gerekce") or [])
            gerekce.append(f"model %{float(s['p'])*100:.0f} → ölçüm düzeltmesiyle %{p*100:.0f}")
            gerekce.append(f"bu pazar {k['n']:,} maçta %{k['gercek']*100:.1f} tuttu".replace(",", "."))
            havuz.append({**m, **s, "p": p, "gerekce": gerekce})
    # en güvenilirden başla; eşitlikte fiyatı olan (dolayısıyla EV'si ölçülebilen) önde
    havuz.sort(key=lambda x: (-x["p"], -(x.get("oran") or 0)))
    return havuz, elenen


def _fiyat(aday: dict) -> float:
    """Bacağın hesapta kullanılan oranı: gerçek fiyat varsa o, yoksa adil oran."""
    return float(aday.get("oran") or (1.0 / max(aday["p"], 1e-6)))


def kupon_kur(havuz: list[dict], hedef: float = 2.0, maks_bacak: int = 3,
              esik: float = 0.60) -> dict | None:
    """Hedef toplam orana ulaşan EN YÜKSEK OLASILIKLI kuponu arar.

    Neden açgözlü seçim yetmiyor: "en yüksek olasılıklıyı sırayla ekle" üç
    bacakta 1.64'te kalıp hedefi ıskalayabiliyor. Doğru soru "hedefi geçen
    kombinasyonlar içinde hangisinin tutma olasılığı en yüksek" — bu bir
    sırt çantası problemi, sıralamayla çözülmez. Aday listesi budanıp
    (kombinasyon sayısı kontrol altında) budamalı arama yapılır.

    Kural: her maçtan EN FAZLA bir bacak. Aynı maçtan iki seçim birbirine
    bağımlıdır (aynı skorun iki yüzü) — çarpım kuralı orada yalan söyler.
    Hedefe ulaşmak için asla eşik altı seçim eklenmez; ulaşılamıyorsa kupon
    kurulmaz ve bu açıkça söylenir.

    MATEMATİK NOTU: fiyatı bilinmeyen bacaklarda adil oran (1/p) kullanılır;
    öyle bacaklarla kurulan 2.00'lik kuponun olasılığı tam olarak %50 çıkar
    (çarpım birebir tersidir). Yani değer, ancak GERÇEK fiyatı adil oranın
    üstünde olan bacaklardan gelir.
    """
    adaylar = [a for a in havuz if a["p"] >= esik]
    if not adaylar:
        return None
    # verim = bir bacağın orana kattığı birim başına koruduğu olasılık;
    # adil fiyatlı bacakta tam olarak -1, değerli bacakta -1'den büyüktür.
    def verim(a: dict) -> float:
        o = _fiyat(a)
        return math.log(a["p"]) / math.log(o) if o > 1.0001 else -99.0

    adaylar.sort(key=verim, reverse=True)
    # kombinasyon patlamasını engelle: derinlik arttıkça aday listesi kısalır
    sinir = {1: 200, 2: 120, 3: 90, 4: 40, 5: 28, 6: 22}.get(maks_bacak, 30)
    adaylar = adaylar[:sinir]

    en_iyi: dict | None = None

    def dfs(basla: int, secili: list, maclar: set, oran: float, olasilik: float):
        nonlocal en_iyi
        if oran >= hedef and secili:
            if en_iyi is None or olasilik > en_iyi["p"]:
                en_iyi = {"bacaklar": list(secili), "oran": oran, "p": olasilik}
            return          # daha fazla bacak olasılığı yalnız düşürür
        if len(secili) >= maks_bacak:
            return
        for i in range(basla, len(adaylar)):
            a = adaylar[i]
            mid = a.get("mac_id")
            if mid is not None and mid in maclar:
                continue
            yeni_p = olasilik * a["p"]
            if en_iyi is not None and yeni_p <= en_iyi["p"]:
                continue    # buradan daha iyi bir sonuç çıkamaz (olasılık yalnız azalır)
            secili.append(a)
            if mid is not None:
                maclar.add(mid)
            dfs(i + 1, secili, maclar, oran * _fiyat(a), yeni_p)
            secili.pop()
            if mid is not None:
                maclar.discard(mid)

    dfs(0, [], set(), 1.0, 1.0)
    if not en_iyi:
        return None
    bacaklar, oran, p = en_iyi["bacaklar"], en_iyi["oran"], en_iyi["p"]
    fiyatsiz = sum(1 for b in bacaklar if not b.get("oran"))
    uyari = None
    if fiyatsiz:
        uyari = (f"{fiyatsiz} bacağın gerçek fiyatı elimizde yok (bültende o pazarın oranı "
                 "gelmiyor); toplam oran orada ADİL oranla hesaplandı. Bahis sitesindeki "
                 "fiyat adil oranın altındaysa kuponun gerçek getirisi burada yazandan "
                 "düşük olur — fiyatı sitede kontrol et.")
    return {
        "bacaklar": [_bacak(b) for b in bacaklar],
        "oran": round(oran, 2),
        "p": float(p),
        "ev": float(p * oran - 1.0),
        "basabas": float(1.0 / oran),
        "uyari": uyari,
    }


def tekli_degerler(havuz: list[dict], min_oran: float = 2.0, adet: int = 5) -> list[dict]:
    """Tek başına min_oran üstü, modele göre fiyatı cömert olan seçimler.

    Kupon eşiğinden bağımsız kendi tabanı vardır: gerçek fiyatı 2.00+ olan bir
    seçimin model olasılığı zaten %50 civarındadır, kupon eşiği (%60) burada
    her şeyi elerdi. Taban ölçümden geliyor (bölüm E: %45 tabanla 696 bahiste
    %49.3 isabet, %+1.6 getiri).
    """
    secilen = []
    for a in havuz:
        oran = a.get("oran")
        if not oran or oran < min_oran or a["p"] < TEKLI_MIN_P:
            continue
        if a["p"] * oran <= 1.0:      # değer yoksa öneri de yok
            continue
        secilen.append(a)
    secilen.sort(key=lambda x: -(x["p"] * x["oran"]))
    return [_bacak(a) for a in secilen[:adet]]


KARNE_NOT = ("10.000 maçta ölçüldü. Model yalnız 01.07.2023 öncesi arşivle kuruldu, "
             "karne o tarihten sonraki maçlarda çıkarıldı — yani 'geleceği görerek' "
             "şişmiş sayılar değil. 'Ayırt gücü' modelin en güvendiği çeyrek ile en az "
             "güvendiği çeyrek arasındaki gerçek fark: sıfıra yakınsa model o pazarda "
             "maça özel bir şey bilmiyor demektir.")


def _strateji_notu() -> str:
    """Bu stratejinin geçmişte gerçekte ne yaptığı — süslemesiz."""
    a, b, t = (STRATEJI_KARNE["kombine60"], STRATEJI_KARNE["kombine65"],
               STRATEJI_KARNE["tekli"])
    return (
        "📊 <b>Bu sekmenin stratejisi geçmişte ne yaptı:</b> aynı kurallarla kurulan "
        f"≥2.00 kombineler, güven eşiği %60'ta {a['n']} kuponda "
        f"<b>%{a['gercek']*100:.0f}</b> tuttu (çarpım kuralı %{a['dedi']*100:.0f} demişti), "
        f"getiri %{a['roi']*100:+.1f}; eşik %65'te {b['n']} kuponda "
        f"<b>%{b['gercek']*100:.0f}</b> tuttu, getiri %{b['roi']*100:+.1f}. "
        f"Tek bacak ≥2.00 değer seçimleri ise {t['n']} bahiste %{t['gercek']*100:.0f} "
        f"tuttu, getiri %{t['roi']*100:+.1f}. "
        "<b>Dürüst okuma:</b> kuponlar çarpım kuralının dediğinden biraz daha iyi "
        "tutuyor, ama getirilerin hepsi kendi hata payı içinde — yani "
        "<b>kâr garantisi değil, başabaşa yakın ölçülmüş bir disiplin</b>. "
        "Eşiği yükseltmek isabeti artırır, kupon sayısını azaltır."
    )


def notlar(oransiz: int = 0) -> list[str]:
    """Sekmenin altındaki dürüstlük notları."""
    n = [
        "🎯 <b>'Garanti maç' diye bir şey yok</b> — bu sekme de onu vaat etmiyor. "
        "Matematik acımasız: 2.00 oran zaten 'iki denemeden biri tutar' demektir. "
        "Yüksek oran ile yüksek kesinlik aynı anda olmaz; burada yapılan, "
        "<b>aynı orana ulaşan en yüksek olasılıklı yolu</b> seçmektir.",
        "📋 Sıralama <b>ölçüme</b> dayanır, tahmine değil: her pazar 10.000 maçlık "
        "eğitim/test ayrımından geçti. Kalibrasyonu bozuk ya da ayırt gücü sıfır çıkan "
        "pazarlar <b>öneri havuzuna hiç alınmadı</b> — tablodaki ⛔ satırları onlar.",
        "🔗 Kombine bacakları hep <b>farklı maçlardan</b> seçilir. Aynı maçtan iki "
        "seçim birbirine bağımlıdır (aynı skorun iki yüzü); orada olasılıkları çarpmak "
        "kuponu olduğundan güvenli gösterir.",
        _strateji_notu(),
    ]
    eksik = " · ".join(f"<b>{ad}</b>: {sebep}" for ad, sebep in FIYATLANAMAZ.items())
    n.append("🚫 Fiyatlanamayan pazarlar (istense de eklenemez): " + eksik +
             ". Bu pazarlar için tahmin üretmek uydurmak olurdu; kadro, dakika ve "
             "xG içeren ücretli bir veri kaynağı gerekir.")
    if oransiz:
        n.append(f"ℹ️ Günün {oransiz} maçında oran bulunamadığı için o maçlar "
                 "değerlendirmeye girmedi.")
    return n


def karne_tablosu() -> list[dict]:
    """Arayüzdeki 'neyin ne kadar güvenilir olduğu' tablosu."""
    satirlar = []
    for pazar, k in PAZAR_KARNE.items():
        gecer, neden = guvenilir(pazar)
        satirlar.append({"pazar": pazar, "n": k["n"], "dedi": k["dedi"],
                         "gercek": k["gercek"], "fark": k["fark"], "ayirt": k["ayirt"],
                         "guvenilir": gecer, "neden": None if gecer else neden})
    satirlar.sort(key=lambda x: (not x["guvenilir"], -x["gercek"]))
    return satirlar
