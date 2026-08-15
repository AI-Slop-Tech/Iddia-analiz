"""Komut satırı arayüzü."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import analiz, rapor, veri


def _cmd_guncelle(args: argparse.Namespace) -> int:
    ligler = args.ligler or veri.VARSAYILAN_LIGLER
    bilinmeyen = [l for l in ligler if l not in veri.LIGLER]
    if bilinmeyen:
        print(f"Bilinmeyen lig kodu: {', '.join(bilinmeyen)}")
        print("Geçerli kodlar: " + ", ".join(f"{k} ({v})" for k, v in veri.LIGLER.items()))
        return 1
    print(f"Veri indiriliyor: {', '.join(ligler)} — son {args.sezon} sezon\n")
    ozet = veri.indir(ligler, sezon_sayisi=args.sezon, yenile=args.yenile)
    print(
        f"\nBitti: {ozet['indirilen']} dosya indirildi, "
        f"{ozet['onbellek']} dosya önbellekten kullanıldı, {len(ozet['hata'])} hata."
    )
    if ozet["hata"]:
        print("(Bazı eski sezon/lig kombinasyonları kaynakta olmayabilir; bu normaldir.)")
    return 0


def _cmd_durum(args: argparse.Namespace) -> int:  # noqa: ARG001
    df = veri.veriyi_yukle()
    oranli = df["oran_ev"].notna().mean()
    print(f"Toplam maç      : {len(df):,}")
    print(f"Tarih aralığı   : {df['Tarih'].min():%d.%m.%Y} → {df['Tarih'].max():%d.%m.%Y}")
    print(f"Oran kapsamı    : maçların %{oranli * 100:.1f}'inde 1X2 oranı var")
    print("\nLig bazında maç sayısı:")
    for lig, adet in df["Div"].value_counts().items():
        print(f"  {lig:<4} {veri.LIGLER.get(lig, ''):<28} {adet:>6,} maç")
    return 0


def _cmd_takimlar(args: argparse.Namespace) -> int:
    df = veri.veriyi_yukle()
    liste = veri.takim_listesi(df, lig=args.lig)
    aktif_sinir = df["Tarih"].max() - pd.Timedelta(days=365)
    print(f"{'Takım':<22} {'Lig':<5} {'Maç':>5}  Son maç")
    print("─" * 50)
    for satir in liste.itertuples():
        isaret = "●" if satir.son_mac >= aktif_sinir else " "
        print(f"{isaret} {satir.Takim:<20} {satir.lig:<5} {satir.mac:>5}  {satir.son_mac:%m.%Y}")
    print("\n● = son 1 yılda maçı olan (güncel) takımlar")
    return 0


def _cmd_analiz(args: argparse.Namespace) -> int:
    df = veri.veriyi_yukle()
    try:
        ev = veri.takim_bul(df, args.ev)
        dep = veri.takim_bul(df, args.dep)
    except ValueError as hata:
        print(f"Hata: {hata}")
        return 1

    oranlar = tuple(args.oran) if args.oran else None
    if oranlar and (min(oranlar) <= 1.0):
        print("Hata: oranlar 1.00'den büyük olmalı (sıra: MS1 MS0 MS2).")
        return 1
    ust_alt = tuple(args.alt_ust) if args.alt_ust else None

    sonuc = analiz.mac_analizi(
        df, ev, dep, oranlar=oranlar, tolerans=args.tolerans,
        elo=analiz.elo_hesapla(df), ust_alt=ust_alt,
    )
    lig_adi = veri.LIGLER.get(sonuc["poisson"]["lig"], sonuc["poisson"]["lig"])
    metin = rapor.rapor_olustur(sonuc, lig_adi=lig_adi)
    print(metin)

    if not oranlar:
        print("\nİpucu: --oran 2.10 3.40 3.20 verirseniz oran kalıbı ve değer analizi de eklenir.")

    if args.gemini:
        from . import gemini_yorum

        print("\n🤖 Gemini yorumu isteniyor...\n")
        try:
            print(gemini_yorum.yorum_al(metin))
        except Exception as hata:  # noqa: BLE001 - AI katmanı çökerse rapor yine de verilmiş olur
            print(f"Gemini yorumu alınamadı: {hata}")
            return 1
    return 0


def _cmd_bulten(args: argparse.Namespace) -> int:
    df = veri.veriyi_yukle()
    fik, _ = veri.fikstur_yukle(ligler=list(df["Div"].unique()), yenile=args.yenile)
    if args.lig:
        fik = fik[fik["Div"] == args.lig]
    if fik.empty:
        print("Fikstürde uygun maç yok.")
        return 0

    elo = analiz.elo_hesapla(df) if args.tara else None
    for gun, grup in fik.groupby(fik["Tarih"].dt.date):
        print(f"\n📅 {gun.strftime('%d.%m.%Y')} — {len(grup)} maç")
        print("─" * 74)
        for s in grup.itertuples():
            oran_metni = (
                f"{s.oran_ev:>5.2f} {s.oran_berabere:>5.2f} {s.oran_dep:>5.2f}"
                if not pd.isna(s.oran_ev) else "  oran yok  "
            )
            print(f"  {s.Tarih:%H:%M}  {s.Div:<4} {s.HomeTeam} - {s.AwayTeam:<22} {oran_metni}")
            if args.tara and not pd.isna(s.oran_ev):
                ust_alt = None
                if not (pd.isna(s.oran_ust25) or pd.isna(s.oran_alt25)):
                    ust_alt = (float(s.oran_ust25), float(s.oran_alt25))
                a = analiz.mac_analizi(
                    df, s.HomeTeam, s.AwayTeam,
                    oranlar=(float(s.oran_ev), float(s.oran_berabere), float(s.oran_dep)),
                    elo=elo, ust_alt=ust_alt, lig_ipucu=s.Div,
                )
                o = a["oneri"]
                isaret = {"degerli": "✅", "sinirda": "🟡", "pas": "⛔"}[o["karar"]]
                print(
                    f"         {isaret} {rapor.SECIM_AD.get(o['secim'], o['secim'])}"
                    f" @ {o['oran']:.2f}  EV {o['ev'] * 100:+.1f}%"
                    f"  {'★' * o['yildiz']}{'☆' * (5 - o['yildiz'])}"
                )
    print("\nAyrıntılı rapor için: python tahmin.py analiz --ev ... --dep ... --oran ...")
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    from . import backtest

    df = veri.veriyi_yukle()
    if args.lig and args.lig not in df["Div"].unique():
        print(f"'{args.lig}' için veri yok. Geçerli ligler: {', '.join(sorted(df['Div'].unique()))}")
        return 1
    print(
        f"🧪 Backtest: son {args.sezon} sezon, "
        f"{veri.LIGLER.get(args.lig, args.lig) if args.lig else 'tüm ligler'}, "
        f"eşik +%{args.esik * 100:.0f} — hesaplanıyor (~10-30 sn)..."
    )
    maks_oran = None if args.maks_oran == 0 else args.maks_oran
    r = backtest.backtest_calistir(
        df, sezon_sayisi=args.sezon, lig=args.lig, esik=args.esik, maks_oran=maks_oran
    )
    if "hata" in r:
        print(f"Hata: {r['hata']}")
        return 1

    o, p = r["ozet"], r["parametreler"]
    print(f"\nTest dönemi   : {', '.join(p['sezonlar'])} sezonları — {p['test_mac']} maç değerlendirildi")
    print(f"Oynanan bahis : {o['bahis']}  (eşik +%{p['esik'] * 100:.0f} üzeri değer)")
    if o["bahis"]:
        print(f"İsabet        : %{o['isabet'] * 100:.1f}   |   Ortalama oran: {o['ort_oran']:.2f}")
        print(f"Açılış oranıyla   : {o['kar']:+8.1f} birim  →  ROI %{o['roi'] * 100:+.2f}")
        print(f"En iyi oranla     : {o['kar_maks']:+8.1f} birim  →  ROI %{o['roi_maks'] * 100:+.2f}"
              "   (aynı kuponlar, piyasadaki en yüksek orandan)")
        print(f"Maks. düşüş   : {o['maks_dusus']:.1f} birim")
    t = r["taban"]
    print(f"\nKıyas ({t['mac']} maç): hep ev sahibi %{t['ms1_roi'] * 100:+.1f} ROI, "
          f"hep favori %{t['favori_roi'] * 100:+.1f} ROI")

    print("\nEşik-ROI tablosu (aynı koşu, farklı eşikler):")
    print("  Eşik    Bahis   İsabet    ROI(açılış)   ROI(en iyi)")
    for e in r["esik_tablosu"]:
        if e["bahis"]:
            print(
                f"  +%{e['esik'] * 100:<5.0f}{e['bahis']:>6}   %{e['isabet'] * 100:>5.1f}"
                f"   %{e['roi'] * 100:+8.2f}   %{e['roi_maks'] * 100:+8.2f}"
            )

    print("\nSeçim kırılımı:")
    for k in r["secim_kirilim"]:
        print(f"  {k['ad']:<8} {k['bahis']:>5} bahis   isabet %{k['isabet'] * 100:>5.1f}   ROI %{k['roi'] * 100:+6.2f}")
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    try:
        from .web import calistir
    except ModuleNotFoundError as hata:
        if "flask" in str(hata).lower():
            print("Web arayüzü için Flask gerekli: pip install flask")
            return 1
        raise
    print(f"🌐 İddaa Analiz paneli: http://{args.host}:{args.port}  (kapatmak için Ctrl+C)")
    try:
        calistir(host=args.host, port=args.port)
    except ModuleNotFoundError as hata:
        if "flask" in str(hata).lower():
            print("Web arayüzü için Flask gerekli: pip install flask")
            return 1
        raise
    return 0


def arg_ayristirici() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tahmin.py",
        description="İddaa Analiz Sistemi — ücretsiz tarihsel veriyle profesyonel maç analizi",
    )
    alt = p.add_subparsers(dest="komut", required=True)

    g = alt.add_parser("guncelle", help="Maç ve oran verilerini indir/güncelle")
    g.add_argument("--ligler", nargs="+", metavar="LIG", help=f"Lig kodları (varsayılan: {' '.join(veri.VARSAYILAN_LIGLER)})")
    g.add_argument("--sezon", type=int, default=11, help="Kaç sezon geriye gidilsin (varsayılan: 11 = son 10 yıl + bu sezon)")
    g.add_argument("--yenile", action="store_true", help="Önbelleği yok sayıp her şeyi yeniden indir")
    g.set_defaults(fn=_cmd_guncelle)

    d = alt.add_parser("durum", help="İndirilen veri setinin özetini göster")
    d.set_defaults(fn=_cmd_durum)

    t = alt.add_parser("takimlar", help="Veri setindeki takım adlarını listele")
    t.add_argument("--lig", help="Tek lige filtrele (ör. T1)")
    t.set_defaults(fn=_cmd_takimlar)

    a = alt.add_parser("analiz", help="Bir maçı analiz et")
    a.add_argument("--ev", required=True, help="Ev sahibi takım")
    a.add_argument("--dep", required=True, help="Deplasman takımı")
    a.add_argument("--oran", nargs=3, type=float, metavar=("MS1", "MS0", "MS2"), help="Bültendeki 1X2 oranları")
    a.add_argument("--alt-ust", nargs=2, type=float, metavar=("UST", "ALT"), help="Üst/Alt 2.5 oranları (opsiyonel)")
    a.add_argument("--tolerans", type=float, default=0.02, help="Oran kalıbı benzerlik toleransı (varsayılan 0.02)")
    a.add_argument("--gemini", action="store_true", help="Rapora Gemini AI yorumu ekle (GEMINI_API_KEY gerekir)")
    a.set_defaults(fn=_cmd_analiz)

    b = alt.add_parser("bulten", help="Önümüzdeki günlerin maçlarını oranlarıyla listele")
    b.add_argument("--tara", action="store_true", help="Her maçı analiz edip öneri ekle")
    b.add_argument("--lig", help="Tek lige filtrele (ör. T1)")
    b.add_argument("--yenile", action="store_true", help="Fikstür önbelleğini yok say")
    b.set_defaults(fn=_cmd_bulten)

    bt = alt.add_parser("backtest", help="Stratejiyi geçmiş sezonlarda test et (ROI raporu)")
    bt.add_argument("--sezon", type=int, default=3, help="Son kaç sezon test edilsin (varsayılan 3)")
    bt.add_argument("--lig", help="Tek lige daralt (ör. T1); boşsa tüm ligler")
    bt.add_argument("--esik", type=float, default=0.04, help="Bahis eşiği: beklenen değer (varsayılan 0.04)")
    bt.add_argument("--maks-oran", type=float, default=3.60,
                    help="Sürpriz oran filtresi: bu oranın üstü oynanmaz (varsayılan 3.60, 0 = kapalı)")
    bt.set_defaults(fn=_cmd_backtest)

    w = alt.add_parser("web", help="Modern web arayüzünü başlat")
    w.add_argument("--host", default="127.0.0.1", help="Dinlenecek adres (varsayılan 127.0.0.1)")
    w.add_argument("--port", type=int, default=8000, help="Port (varsayılan 8000)")
    w.set_defaults(fn=_cmd_web)

    return p


def main(argv: list[str] | None = None) -> int:
    args = arg_ayristirici().parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as hata:
        print(f"Hata: {hata}")
        return 1
    except KeyboardInterrupt:
        print("\nİptal edildi.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
