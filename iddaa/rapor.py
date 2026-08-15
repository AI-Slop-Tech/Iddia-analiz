"""Terminal raporu: analiz sonuçlarını okunabilir Türkçe rapora dönüştürür."""

from __future__ import annotations

GENISLIK = 66
SECIM_AD = {
    "MS1": "MS 1 (ev sahibi)",
    "MS0": "MS 0 (beraberlik)",
    "MS2": "MS 2 (deplasman)",
    "ÜST 2.5": "Üst 2.5 gol",
    "ALT 2.5": "Alt 2.5 gol",
}


def _bar(p: float, genislik: int = 22) -> str:
    dolu = round(p * genislik)
    return "█" * dolu + "░" * (genislik - dolu)


def _baslik(metin: str) -> str:
    return f"\n{metin}\n" + "─" * GENISLIK


def _yuzde(p: float) -> str:
    return f"%{p * 100:.0f}"


def _form_blok(form: dict, saha_form: dict, etiket: str) -> list[str]:
    s = [
        f"  {form['takim']}  ({etiket})",
        f"    Son {form['mac']} maç : {form['seri'] or '-'}   {form['puan']} puan"
        f"   | attığı {form['gol_ort']:.1f} - yediği {form['yenilen_ort']:.1f} /maç",
    ]
    if saha_form["mac"]:
        s.append(
            f"    {'Evinde' if etiket == 'ev sahibi' else 'Deplasmanda'} son {saha_form['mac']}: "
            f"{saha_form['seri']}   {saha_form['puan']} puan"
            f"   | attığı {saha_form['gol_ort']:.1f} - yediği {saha_form['yenilen_ort']:.1f}"
        )
    for m in form["son_maclar"][:3]:
        s.append(f"      {m['tarih'].strftime('%d.%m.%Y')}  {m['ev']} {m['skor']} {m['dep']}  [{m['sonuc']}]")
    return s


def rapor_olustur(a: dict, lig_adi: str = "") -> str:
    """mac_analizi() çıktısından tam metin raporu üretir."""
    ev, dep = a["ev"], a["dep"]
    p = a["poisson"]

    s = []
    s.append("═" * GENISLIK)
    s.append("⚽  İDDAA ANALİZ RAPORU".center(GENISLIK))
    s.append(f"{ev}  vs  {dep}".center(GENISLIK))
    if lig_adi:
        s.append(lig_adi.center(GENISLIK))
    if a["oranlar"]:
        o = a["oranlar"]
        s.append(f"Oranlar: 1 → {o[0]:.2f}   X → {o[1]:.2f}   2 → {o[2]:.2f}".center(GENISLIK))
    s.append("═" * GENISLIK)

    # -------- form
    s.append(_baslik("📊 FORM DURUMU  (seri soldan sağa eski→yeni | G galibiyet, B beraberlik, M mağlubiyet)"))
    s += _form_blok(a["form_ev"], a["form_ev_saha"], "ev sahibi")
    s.append("")
    s += _form_blok(a["form_dep"], a["form_dep_saha"], "deplasman")

    # -------- H2H
    s.append(_baslik("🔁 ARALARINDAKİ MAÇLAR (H2H)"))
    h = a["h2h"]
    if not h["mac"]:
        s.append("  Veri setinde karşılaşma kaydı yok (farklı liglerde oynuyor olabilirler).")
    else:
        s.append(
            f"  {h['mac']} maç:  {ev} {h['ev_galibiyet']} G  |  {h['beraberlik']} B  |  {dep} {h['dep_galibiyet']} G"
        )
        s.append(
            f"  Maç başı gol: {h['gol_ort']:.2f}   |   Üst 2.5: {_yuzde(h['ust25'])}   |   KG Var: {_yuzde(h['kg_var'])}"
        )
        for m in h["son_maclar"]:
            s.append(f"      {m['tarih'].strftime('%d.%m.%Y')}  {m['ev']} {m['skor']} {m['dep']}")

    # -------- oran kalıbı
    if a["oranlar"]:
        s.append(_baslik("📈 ORAN KALIBI — geçmişte benzer oranla açılan maçlarda ne oldu?"))
        k = a["kalip"]
        if not k:
            s.append("  Bu oran profiline yeterince benzeyen geçmiş maç bulunamadı.")
        else:
            s.append(
                f"  {k['ilk_tarih'].year}'den bu yana {k['n']} benzer maç bulundu"
                f"  ({k['lig_sayisi']} lig, olasılık toleransı ±{k['tolerans'] * 100:.1f} puan)"
            )
            s.append(f"    Ev kazandı    {_bar(k['ms1'])}  {_yuzde(k['ms1'])}")
            s.append(f"    Beraberlik    {_bar(k['ms0'])}  {_yuzde(k['ms0'])}")
            s.append(f"    Dep. kazandı  {_bar(k['ms2'])}  {_yuzde(k['ms2'])}")
            s.append(
                f"    Maç başı gol: {k['gol_ort']:.2f}   |   Üst 2.5: {_yuzde(k['ust25'])}"
                f"   |   KG Var: {_yuzde(k['kg_var'])}"
            )
            skorlar = "  ".join(f"{skor} ({_yuzde(oran)})" for skor, _, oran in k["skorlar"][:5])
            s.append(f"    En sık skorlar: {skorlar}")

    # -------- Poisson
    s.append(_baslik("🎯 POISSON MODEL TAHMİNİ  (zaman ağırlıklı hücum/savunma gücü)"))
    s.append(f"  Gol beklentisi:  {ev} {p['lambda_ev']:.2f}  -  {p['lambda_dep']:.2f} {dep}")
    s.append(f"    MS 1          {_bar(p['ms1'])}  {_yuzde(p['ms1'])}")
    s.append(f"    MS 0          {_bar(p['ms0'])}  {_yuzde(p['ms0'])}")
    s.append(f"    MS 2          {_bar(p['ms2'])}  {_yuzde(p['ms2'])}")
    s.append(
        f"  Üst 2.5: {_yuzde(p['ust25'])}   |   Alt 2.5: {_yuzde(p['alt25'])}"
        f"   |   KG Var: {_yuzde(p['kg_var'])}"
    )
    s.append("  En olası skorlar: " + "  ".join(f"{skor} ({_yuzde(pr)})" for skor, pr in p["skorlar"][:5]))
    if a.get("elo"):
        e = a["elo"]
        s.append(
            f"  Elo reytingi: {ev} {e['ev']:.0f}  -  {e['dep']:.0f} {dep}"
            f"   (fark {e['fark']:+.0f})"
        )
    for u in p["uyarilar"]:
        s.append(f"  ⚠ {u}")

    # -------- değer analizi
    if a["deger"]:
        d = a["deger"]
        s.append(_baslik("💰 DEĞER ANALİZİ  (model olasılığı vs oranın içerdiği olasılık)"))
        w_poisson = 1 - d["w_kalip"] - d.get("w_piyasa", 0)
        s.append(
            f"  Model karışımı: %{w_poisson * 100:.0f} Poisson"
            f" + %{d['w_kalip'] * 100:.0f} oran kalıbı"
            f" + %{d.get('w_piyasa', 0) * 100:.0f} piyasa"
            f"   |   Bahisçi marjı: %{d['marj'] * 100:.1f}"
        )
        s.append("    Seçim      Oran    Piyasa    Model     Beklenen değer")
        for satir in d["satirlar"]:
            isaret = "✅" if satir["ev"] >= 0.04 else ("🟡" if satir["ev"] >= 0.01 else "  ")
            s.append(
                f"    {satir['secim']:<9}{satir['oran']:>5.2f}"
                f"   {_yuzde(satir['piyasa']):>5}    {_yuzde(satir['model']):>5}"
                f"     {satir['ev'] * 100:+6.1f}%  {isaret}"
            )

    # -------- öneri
    if a["oneri"]:
        o = a["oneri"]
        s.append(_baslik("🧠 SONUÇ & ÖNERİ"))
        yildizlar = "★" * o["yildiz"] + "☆" * (5 - o["yildiz"])
        if o["karar"] == "degerli":
            s.append(f"  ✅ DEĞERLİ BAHİS: {SECIM_AD[o['secim']]} @ {o['oran']:.2f}   Güven: {yildizlar}")
            s.append(f"     Beklenen değer {o['ev'] * 100:+.1f}%. Önerilen kasa payı (çeyrek Kelly): %{o['kelly'] * 100:.1f}")
        elif o["karar"] == "sinirda":
            s.append(f"  🟡 SINIRDA: {SECIM_AD[o['secim']]} @ {o['oran']:.2f}   Güven: {yildizlar}")
            s.append(f"     Beklenen değer {o['ev'] * 100:+.1f}% — küçük bir avantaj var ama hata payının içinde.")
        else:
            s.append(f"  ⛔ DEĞER YOK — bu oranlarla en iyi seçim bile {o['ev'] * 100:+.1f}% beklenen değerde.")
            s.append("     Profesyonel yaklaşım: bu maçı PAS geçmek. Değer yoksa bahis de yok.")

    s.append("")
    s.append("─" * GENISLIK)
    s.append("ℹ  Bu rapor istatistiksel bir modeldir, garanti içermez. 18+ | Sorumlu oyun.")
    s.append("─" * GENISLIK)
    return "\n".join(s)
