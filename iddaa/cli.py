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

    sonuc = analiz.mac_analizi(df, ev, dep, oranlar=oranlar, tolerans=args.tolerans)
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
    a.add_argument("--tolerans", type=float, default=0.02, help="Oran kalıbı benzerlik toleransı (varsayılan 0.02)")
    a.add_argument("--gemini", action="store_true", help="Rapora Gemini AI yorumu ekle (GEMINI_API_KEY gerekir)")
    a.set_defaults(fn=_cmd_analiz)

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
