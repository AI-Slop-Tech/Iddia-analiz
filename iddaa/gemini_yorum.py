"""Opsiyonel Gemini katmanı: istatistik raporunu profesyonel analist yorumuna çevirir.

Ücretsiz API anahtarı: https://aistudio.google.com/apikey
Kullanım: GEMINI_API_KEY ortam değişkenini ayarlayıp `analiz` komutuna --gemini ekleyin.
Sistem Gemini olmadan da tam çalışır; bu katman sadece yorum ekler.
"""

from __future__ import annotations

import os

import requests

VARSAYILAN_MODEL = "gemini-2.5-flash"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """Sen üst düzey bir futbol istihbarat biriminde çalışan kıdemli maç analistisin;
raporların bahis fonlarına satılıyor. Aşağıdaki istatistik motoru çıktısını kullanarak
bu maç için profesyonel, bahis odaklı bir analiz raporu yaz.

MUTLAK KURALLAR:
- Yalnızca verilen verilere dayan; raporda olmayan hiçbir rakam/olay uydurma
  (sakatlık, transfer, hava durumu, motivasyon gibi dış bilgiler raporda yoksa yazma).
- "Kesin", "garanti", "banko", "yatır" kelimeleri yasak; olasılık diliyle konuş.
- Değer yoksa dürüstçe söyle — profesyonelliğin ölçüsü pas diyebilmektir.
- Türkçe yaz; akıcı, kendinden emin ama ölçülü bir analist sesi kullan. 500-800 kelime.

BİÇİM (başlıkları aynen kullan):

## 📌 Maç Künyesi
Tek paragraf: eşleşme, lig, verinin öne çıkardığı ana hikâye.

## 📊 Verinin Okuması
2-3 paragraf: güç dengesi (Elo), form ve saha kırılımı, seri notları, aralarındaki
maçların eğilimi, gol beklentisi ve oran kalıbının anlattığı. Rakamları cümle içinde
kullan; sinyaller çelişiyorsa çelişkiyi açıkça yaz.

## 🎯 Pazar Pazar Görüş
Her satır: Görüş — güven (1-5 yıldız) — tek cümle gerekçe.
- Maç Sonucu (1X2):
- Çifte Şans:
- Alt/Üst 2.5:
- Karşılıklı Gol:
- Skor bandı (en olası 2-3 skor):

## 🎫 Kupon Masası
1) GÜVENLİ TEKLİ — seçim (+oran varsa) · güven X/10 · kasa payı önerisi (%1-2) · gerekçe
2) DEĞER OYUNU — değer analizinin işaret ettiği seçim; değer yoksa "bu maçta değer yok" yaz
3) KOMBİNE FİKRİ — bu maçtan iki pazarın mantıklı birleşimi ve hangi tip kupona uyacağı
Değerli hiçbir şey yoksa üçünü de zorlamak yerine "izlemelik maç" kararını savun.

## ⚠️ Riskler ve Uzak Durulacaklar
Madde madde: çelişen sinyaller, küçük örneklemler, sürpriz oran tuzağına girenler,
bu maçta oynanmaması gereken pazarlar.

## 🧾 Kapanış
İki cümle özet + tek cümle sorumlu oyun notu.

=== İSTATİSTİK RAPORU ===
{rapor}
"""


def yorum_al(rapor_metni: str, zaman_asimi: int = 90) -> str:
    from . import veri

    anahtar = veri.gizli_anahtar("GEMINI_API_KEY", "gemini_api_key")
    if not anahtar:
        raise RuntimeError(
            "GEMINI_API_KEY ortam değişkeni tanımlı değil.\n"
            "  1) https://aistudio.google.com/apikey adresinden ücretsiz anahtar alın\n"
            "  2) export GEMINI_API_KEY='...' (Windows: set GEMINI_API_KEY=...)\n"
            "  3) Komutu tekrar çalıştırın."
        )
    model = os.environ.get("GEMINI_MODEL", VARSAYILAN_MODEL)

    govde = {
        "contents": [{"parts": [{"text": PROMPT.format(rapor=rapor_metni)}]}],
        "generationConfig": {"temperature": 0.55, "maxOutputTokens": 4096},
    }
    yanit = requests.post(
        URL.format(model=model), params={"key": anahtar}, json=govde, timeout=zaman_asimi
    )

    if yanit.status_code in (400, 403):
        raise RuntimeError("Gemini API anahtarı geçersiz görünüyor (HTTP %d)." % yanit.status_code)
    if yanit.status_code == 429:
        raise RuntimeError("Gemini ücretsiz kota sınırına takıldınız, biraz sonra tekrar deneyin.")
    yanit.raise_for_status()

    veri = yanit.json()
    try:
        parcalar = veri["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parcalar).strip()
    except (KeyError, IndexError) as h:
        raise RuntimeError(f"Gemini yanıtı çözümlenemedi: {veri}") from h
