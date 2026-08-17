"""Tahmin günlüğü.

Günü Tara ve Tahmin Tablosu, ürettikleri tahminleri maç BAŞLAMADAN buraya
kaydeder; maç oynandıktan sonra Sonuçlar sekmesi gerçek skorla karşılaştırıp
sistemin karnesini çıkarır. Başlamış maça asla yazılmadığı için karne gerçek
anlamda ileriye dönüktür (sonucu görüp tahmin değiştirme yolu yoktur).

Kayıtlar data/tahminler.json içinde tutulur; anahtar "tarih|ev|dep".
"""

from __future__ import annotations

import json
import os
import threading

from .veri import VERI_KLASORU

_DOSYA = os.path.join(VERI_KLASORU, "tahminler.json")
_KILIT = threading.Lock()


def _anahtar(k: dict) -> str:
    return f"{k['tarih']}|{k['ev']}|{k['dep']}"


def _oku() -> dict:
    try:
        with open(_DOSYA, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def yukle() -> dict:
    """Tüm kayıtlar: {"tarih|ev|dep": {...tahmin alanları...}}."""
    with _KILIT:
        return _oku()


def kaydet(kayitlar: list[dict]) -> int:
    """Kayıtları ekler/birleştirir (aynı maça gelen alanlar üst üste yazılır)."""
    if not kayitlar:
        return 0
    with _KILIT:
        mevcut = _oku()
        for k in kayitlar:
            mevcut.setdefault(_anahtar(k), {}).update(k)
        os.makedirs(VERI_KLASORU, exist_ok=True)
        gecici = _DOSYA + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(mevcut, f, ensure_ascii=False, indent=1)
        os.replace(gecici, _DOSYA)
        return len(kayitlar)
