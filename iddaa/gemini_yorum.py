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

PROMPT = """Sen 20 yıllık deneyime sahip profesyonel bir futbol analisti ve iddaa danışmanısın.
Aşağıda bir maç için istatistik motorumun ürettiği rapor var: iki takımın formu,
aralarındaki maçlar, geçmişte benzer oranla açılan maçların sonuç dağılımı (oran kalıbı),
Poisson modelinin olasılıkları ve değer (value) analizi.

GÖREVİN — raporu yorumlayıp şu bölümleri yaz:

## 🎙 Maçın Hikâyesi
2-3 paragraf profesyonel yorum: form, güç dengesi, istatistiksel eğilimler.

## 🔑 En Güçlü 3 Sinyal
Verideki en anlamlı 3 bulguyu madde madde, rakamlarıyla açıkla.

## 🎫 Kupon Önerileri
1. En güvenli tekli (güven notu /10 + tek cümle gerekçe)
2. Mantıklı alternatif veya kombine (güven notu /10 + gerekçe)
3. Düşük olasılıklı ama oransal değeri olan sürpriz (güven notu /10 + gerekçe)

## 🚫 Uzak Durulmalı
Bu maçta oynanmaması gereken seçenekler ve nedenleri.

## ⚖️ Kapanış
İki cümlelik özet + kısa sorumlu bahis hatırlatması.

KURALLAR: Yalnızca verilen rapora dayan, rakam uydurma. Veri desteklemiyorsa "kesin",
"garanti" gibi ifadeler kullanma. Türkçe yaz.

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
        "generationConfig": {"temperature": 0.4},
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
