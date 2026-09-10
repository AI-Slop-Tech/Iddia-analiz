"""Masaüstü penceresi (Tkinter) — danisma.py'nin ince kabuğu.

Burada iş mantığı YOK: her hesap danisma.py'den ya da iddaa paketinden gelir.
Uzun işler (arşiv yükleme, doküman kurma) ayrı iş parçacığında çalışır, pencere
donmaz; sonuç kuyrukla ana döngüye taşınır.

Üç sekme:
  Maç      — maçı seç, dokümanı kur, dört danışmana kopyala
  Cevaplar — gelen cevabı yapıştır, ayıkla, deftere kaydet
  Karne    — kaynak başına ileriye dönük sayım
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd

from iddaa import veri
from masaustu import danisma

BASLIK = "İddaa Danışma — dört danışmana sor, cevapları ölç"
ZEMIN = "#12161f"
KART = "#1a2030"
METIN = "#e6ecf7"
SOLUK = "#8ea0bf"
VURGU = "#34d399"
UYARI = "#f87171"


class Uygulama(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(BASLIK)
        self.geometry("1180x820")
        self.minsize(900, 620)
        self.configure(bg=ZEMIN)
        self.kuyruk: queue.Queue = queue.Queue()
        self.df = None
        self.elo = None
        self.fikstur = None
        self.secili = None          # (satır, oranlar) — dokümanı kurulan maç
        self.dokuman = ""
        self._stil()
        self._duzen()
        self.after(120, self._kuyruk_isle)
        self._is_baslat("veri", self._veri_yukle)

    # ─────────────────────────────────────────────── görünüm
    def _stil(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", background=ZEMIN, foreground=METIN, fieldbackground=KART)
        s.configure("TNotebook", background=ZEMIN, borderwidth=0)
        s.configure("TNotebook.Tab", background=KART, foreground=SOLUK, padding=(16, 8))
        s.map("TNotebook.Tab", background=[("selected", ZEMIN)], foreground=[("selected", VURGU)])
        s.configure("TFrame", background=ZEMIN)
        s.configure("TLabel", background=ZEMIN, foreground=METIN)
        s.configure("Soluk.TLabel", foreground=SOLUK)
        s.configure("Uyari.TLabel", foreground=UYARI)
        s.configure("TButton", background=KART, foreground=METIN, padding=(10, 6), borderwidth=0)
        s.map("TButton", background=[("active", "#243049")])
        s.configure("Vurgu.TButton", background=VURGU, foreground="#08110c")
        s.configure("Treeview", background=KART, fieldbackground=KART, foreground=METIN, rowheight=24)
        s.configure("Treeview.Heading", background=ZEMIN, foreground=SOLUK)
        # Açılır listeler: clam temasının varsayılanı koyu zeminde koyu yazı veriyor,
        # gün/maç seçimi okunmuyordu. Alan ve açılan listenin rengi elle veriliyor.
        s.configure("TCombobox", fieldbackground=KART, background=KART, foreground=METIN,
                    arrowcolor=SOLUK, selectbackground=KART, selectforeground=METIN)
        s.map("TCombobox", fieldbackground=[("readonly", KART)], foreground=[("readonly", METIN)],
              selectbackground=[("readonly", KART)], selectforeground=[("readonly", METIN)])
        self.option_add("*TCombobox*Listbox.background", KART)
        self.option_add("*TCombobox*Listbox.foreground", METIN)
        self.option_add("*TCombobox*Listbox.selectBackground", "#243049")
        self.option_add("*TCombobox*Listbox.selectForeground", VURGU)
        s.configure("TEntry", fieldbackground=KART, foreground=METIN, insertcolor=METIN)

    def _yazi_kutusu(self, ana, yukseklik=20):
        cerceve = ttk.Frame(ana)
        kutu = tk.Text(cerceve, height=yukseklik, wrap="word", bg=KART, fg=METIN,
                       insertbackground=METIN, relief="flat", padx=10, pady=8,
                       font=("TkFixedFont", 10))
        kaydir = ttk.Scrollbar(cerceve, command=kutu.yview)
        kutu.configure(yscrollcommand=kaydir.set)
        kutu.pack(side="left", fill="both", expand=True)
        kaydir.pack(side="right", fill="y")
        return cerceve, kutu

    def _duzen(self):
        ust = ttk.Frame(self)
        ust.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(ust, text="İddaa Danışma", font=("TkDefaultFont", 15, "bold")).pack(side="left")
        self.durum_etiket = ttk.Label(ust, text="arşiv yükleniyor…", style="Soluk.TLabel")
        self.durum_etiket.pack(side="right")

        self.defter = ttk.Notebook(self)
        self.defter.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self._sekme_mac()
        self._sekme_cevap()
        self._sekme_karne()

    def _sekme_mac(self):
        f = ttk.Frame(self.defter)
        self.defter.add(f, text="  Maç  ")

        secim = ttk.Frame(f)
        secim.pack(fill="x", pady=(10, 6))
        ttk.Label(secim, text="Gün:").pack(side="left")
        self.gun_kutu = ttk.Combobox(secim, width=12, state="readonly")
        self.gun_kutu.pack(side="left", padx=(6, 12))
        self.gun_kutu.bind("<<ComboboxSelected>>", lambda _e: self._maclari_doldur())
        ttk.Label(secim, text="Maç:").pack(side="left")
        self.mac_kutu = ttk.Combobox(secim, width=52, state="readonly")
        self.mac_kutu.pack(side="left", padx=(6, 12))
        self.hazirla_dugme = ttk.Button(secim, text="Dokümanı hazırla", style="Vurgu.TButton",
                                        command=self._dokuman_hazirla)
        self.hazirla_dugme.pack(side="left")

        orta = ttk.Frame(f)
        orta.pack(fill="both", expand=True)
        cerceve, self.dokuman_kutu = self._yazi_kutusu(orta, 26)
        cerceve.pack(side="left", fill="both", expand=True)

        yan = ttk.Frame(orta, width=230)
        yan.pack(side="right", fill="y", padx=(12, 0))
        ttk.Label(yan, text="Danışmanlar", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(yan, text="Metin panoya kopyalanır ve sayfa açılır.\nYapıştır, gönder, cevabı "
                            "\"Cevaplar\" sekmesine yapıştır.", style="Soluk.TLabel",
                  wraplength=210, justify="left").pack(anchor="w", pady=(0, 10))
        for anahtar, bilgi in danisma.HEDEFLER.items():
            ttk.Button(yan, text=f"{bilgi.get('kisa', bilgi['ad'])}'e sor",
                       command=lambda a=anahtar: self._danismana_gonder(a)).pack(fill="x", pady=3)
        ttk.Button(yan, text="Dokümanı kopyala",
                   command=lambda: self._panoya(self.dokuman, "Doküman")).pack(fill="x", pady=(12, 3))
        self.mac_not = ttk.Label(yan, text="", style="Soluk.TLabel", wraplength=210, justify="left")
        self.mac_not.pack(anchor="w", pady=(10, 0))

    def _sekme_cevap(self):
        f = ttk.Frame(self.defter)
        self.defter.add(f, text="  Cevaplar  ")

        ust = ttk.Frame(f)
        ust.pack(fill="x", pady=(10, 6))
        ttk.Label(ust, text="Kaynak:").pack(side="left")
        self.kaynak_kutu = ttk.Combobox(ust, width=22, state="readonly",
                                        values=[b["ad"] for b in danisma.HEDEFLER.values()])
        self.kaynak_kutu.current(0)
        self.kaynak_kutu.pack(side="left", padx=(6, 16))
        ttk.Button(ust, text="Cevabı ayıkla", command=self._cevap_ayikla).pack(side="left")
        ttk.Button(ust, text="Deftere kaydet", style="Vurgu.TButton",
                   command=self._cevap_kaydet).pack(side="left", padx=8)

        cerceve, self.cevap_kutu = self._yazi_kutusu(f, 14)
        cerceve.pack(fill="both", expand=True, pady=(0, 8))

        alan = ttk.Frame(f)
        alan.pack(fill="x")
        ttk.Label(alan, text="Sonuç:").grid(row=0, column=0, sticky="w")
        self.sonuc_kutu = ttk.Combobox(alan, width=8, state="readonly", values=["MS1", "MS0", "MS2"])
        self.sonuc_kutu.grid(row=0, column=1, padx=(6, 16))
        ttk.Label(alan, text="Skor:").grid(row=0, column=2, sticky="w")
        self.skor_giris = ttk.Entry(alan, width=8)
        self.skor_giris.grid(row=0, column=3, padx=(6, 16))
        ttk.Label(alan, text="Güven %:").grid(row=0, column=4, sticky="w")
        self.guven_giris = ttk.Entry(alan, width=6)
        self.guven_giris.grid(row=0, column=5, padx=(6, 16))
        ttk.Label(alan, text="Pazar:").grid(row=0, column=6, sticky="w")
        self.pazar_giris = ttk.Entry(alan, width=18)
        self.pazar_giris.grid(row=0, column=7, padx=(6, 0))
        self.cevap_not = ttk.Label(f, text="", style="Soluk.TLabel", wraplength=980, justify="left")
        self.cevap_not.pack(anchor="w", pady=(8, 0))

    def _sekme_karne(self):
        f = ttk.Frame(self.defter)
        self.defter.add(f, text="  Karne  ")
        ust = ttk.Frame(f)
        ust.pack(fill="x", pady=(10, 6))
        ttk.Button(ust, text="Yenile / sonuçlandır", command=self._karne_yenile).pack(side="left")
        self.karne_not = ttk.Label(ust, text="", style="Soluk.TLabel")
        self.karne_not.pack(side="left", padx=12)

        kolonlar = ("kaynak", "n", "sonuclu", "tutan", "isabet", "skor", "guven", "model")
        basliklar = ("Danışman", "Cevap", "Sonuçlu", "Tutan", "İsabet", "Tam skor",
                     "Ort. güven", "Model (aynı maçlar)")
        genislikler = (180, 70, 80, 70, 140, 100, 100, 160)
        self.karne_tablo = ttk.Treeview(f, columns=kolonlar, show="headings", height=7)
        for k, b, g in zip(kolonlar, basliklar, genislikler):
            self.karne_tablo.heading(k, text=b)
            self.karne_tablo.column(k, width=g, anchor="center", stretch=(k == "kaynak"))
        self.karne_tablo.pack(fill="x", pady=(0, 10))

        ttk.Label(f, text=danisma.NOT, style="Soluk.TLabel", wraplength=1080,
                  justify="left").pack(anchor="w", pady=(0, 8))
        cerceve, self.kayit_kutu = self._yazi_kutusu(f, 12)
        cerceve.pack(fill="both", expand=True)

    # ─────────────────────────────────────────── iş parçacığı köprüsü
    def _is_baslat(self, ad, fn):
        def sar():
            try:
                self.kuyruk.put((ad, fn(), None))
            except Exception as hata:  # noqa: BLE001
                self.kuyruk.put((ad, None, f"{hata}\n{traceback.format_exc(limit=3)}"))
        threading.Thread(target=sar, daemon=True).start()

    def _kuyruk_isle(self):
        try:
            while True:
                ad, sonuc, hata = self.kuyruk.get_nowait()
                if hata:
                    self.durum_etiket.configure(text=f"hata: {str(hata).splitlines()[0][:60]}")
                    messagebox.showerror("Hata", str(hata)[:500])
                elif ad == "veri":
                    self._veri_geldi(sonuc)
                elif ad == "dokuman":
                    self._dokuman_geldi(sonuc)
                elif ad == "karne":
                    self._karne_ciz(sonuc)
        except queue.Empty:
            pass
        self.after(150, self._kuyruk_isle)

    # ─────────────────────────────────────────────── veri
    def _veri_yukle(self):
        from iddaa import analiz
        df = veri.veriyi_yukle()
        elo = analiz.elo_hesapla(df)
        try:
            fik = veri.fikstur_yukle()
            fik = fik[0] if isinstance(fik, tuple) else fik
        except Exception:  # noqa: BLE001 — fikstür gelmezse elle takım girilebilir
            fik = None
        return df, elo, fik

    def _veri_geldi(self, paket):
        self.df, self.elo, self.fikstur = paket
        self.durum_etiket.configure(
            text=f"{len(self.df):,} maç yüklendi".replace(",", ".")
                 + ("" if self.fikstur is not None else " · fikstür yok"))
        if self.fikstur is None or self.fikstur.empty:
            return
        simdi = veri.simdi_tr()
        ileri = self.fikstur[self.fikstur["Tarih"] > simdi]
        gunler = sorted(ileri["Tarih"].dt.strftime("%d.%m.%Y").unique(),
                        key=lambda g: pd.to_datetime(g, dayfirst=True))[:10]
        self.gun_kutu.configure(values=list(gunler))
        if gunler:
            self.gun_kutu.current(0)
            self._maclari_doldur()

    def _maclari_doldur(self):
        if self.fikstur is None:
            return
        gun = self.gun_kutu.get()
        satirlar = self.fikstur[self.fikstur["Tarih"].dt.strftime("%d.%m.%Y") == gun]
        self._gun_satirlari = []
        etiketler = []
        for _i, r in satirlar.iterrows():
            etiketler.append(f"{r['Tarih'].strftime('%H:%M')}  {r['HomeTeam']} – {r['AwayTeam']}"
                             f"  ({r.get('LigAdi') or r.get('Div') or ''})")
            self._gun_satirlari.append(r)
        self.mac_kutu.configure(values=etiketler)
        if etiketler:
            self.mac_kutu.current(0)

    # ─────────────────────────────────────────────── doküman
    def _dokuman_hazirla(self):
        if self.df is None:
            messagebox.showinfo("Bekle", "Arşiv hâlâ yükleniyor.")
            return
        i = self.mac_kutu.current()
        if i < 0 or not getattr(self, "_gun_satirlari", None):
            messagebox.showinfo("Maç seç", "Önce gün ve maç seç.")
            return
        r = self._gun_satirlari[i]
        self.hazirla_dugme.configure(state="disabled")
        self.durum_etiket.configure(text="doküman kuruluyor…")
        self.dokuman_kutu.delete("1.0", "end")
        self.dokuman_kutu.insert("1.0", "Veriler toplanıyor (10-40 sn)…")

        def is_():
            from iddaa import oneri
            oranlar, _maks, _ua = oneri.fikstur_oranlari(r)
            metin = danisma.mac_dokumani(
                self.df, self.elo, r["HomeTeam"], r["AwayTeam"], r["Tarih"],
                oranlar=oranlar, lig=(r.get("LigAdi") or r.get("Div")))
            return r, oranlar, metin

        self._is_baslat("dokuman", is_)

    def _dokuman_geldi(self, paket):
        r, oranlar, metin = paket
        self.secili = (r, oranlar)
        self.dokuman = metin
        self.dokuman_kutu.delete("1.0", "end")
        self.dokuman_kutu.insert("1.0", metin)
        self.hazirla_dugme.configure(state="normal")
        self.durum_etiket.configure(text=f"{r['HomeTeam']} – {r['AwayTeam']} hazır")
        self.mac_not.configure(text=f"{r['HomeTeam']} – {r['AwayTeam']}\n"
                                    f"{r['Tarih'].strftime('%d.%m.%Y %H:%M')}")

    def _panoya(self, metin, ad="Metin"):
        if not metin:
            messagebox.showinfo("Boş", "Önce dokümanı hazırla.")
            return False
        self.clipboard_clear()
        self.clipboard_append(metin)
        self.update()
        self.durum_etiket.configure(text=f"{ad} panoya kopyalandı ({len(metin)} karakter)")
        return True

    def _danismana_gonder(self, anahtar):
        if not self.dokuman or not self.secili:
            messagebox.showinfo("Doküman yok", "Önce bir maç seçip dokümanı hazırla.")
            return
        r, _o = self.secili
        metin = danisma.istem_metni(self.dokuman, anahtar, r["HomeTeam"], r["AwayTeam"])
        if not self._panoya(metin, danisma.HEDEFLER[anahtar]["ad"]):
            return
        webbrowser.open(danisma.HEDEFLER[anahtar]["url"])
        ek = ("\n\n" + danisma._REDDIT_UYARI) if anahtar == "reddit" else ""
        messagebox.showinfo(
            danisma.HEDEFLER[anahtar]["ad"],
            "Metin panoya kopyalandı ve sayfa açıldı.\n\n"
            "1) Sayfaya yapıştır (Ctrl+V) ve gönder\n"
            "2) Gelen cevabı kopyala\n"
            "3) \"Cevaplar\" sekmesine yapıştırıp kaydet" + ek)

    # ─────────────────────────────────────────────── cevap
    def _kaynak_anahtari(self):
        ad = self.kaynak_kutu.get()
        for k, b in danisma.HEDEFLER.items():
            if b["ad"] == ad:
                return k
        return "chatgpt"

    def _cevap_ayikla(self):
        c = danisma.cevap_ayikla(self.cevap_kutu.get("1.0", "end"))
        if c["sonuc"] in ("MS1", "MS0", "MS2"):
            self.sonuc_kutu.set(c["sonuc"])
        self.skor_giris.delete(0, "end")
        if c["skor"]:
            self.skor_giris.insert(0, c["skor"])
        self.guven_giris.delete(0, "end")
        if c["guven"] is not None:
            self.guven_giris.insert(0, f"{c['guven']*100:.0f}")
        self.pazar_giris.delete(0, "end")
        if c["pazar"]:
            self.pazar_giris.insert(0, c["pazar"])
        eksik = [a for a, v in (("sonuç", c["sonuc"]), ("skor", c["skor"])) if not v]
        self.cevap_not.configure(
            text="Cevap okundu." if not eksik
            else f"Şunlar okunamadı: {', '.join(eksik)} — aşağıdan elle seç.")

    def _cevap_kaydet(self):
        if not self.secili:
            messagebox.showinfo("Maç yok", "Önce Maç sekmesinde bir maç hazırla.")
            return
        r, _o = self.secili
        guven = None
        try:
            g = self.guven_giris.get().strip().rstrip("%")
            guven = (float(g) / 100.0) if g else None
        except ValueError:
            guven = None
        cevap = {
            "sonuc": self.sonuc_kutu.get() or None,
            "skor": self.skor_giris.get().strip() or None,
            "guven": guven,
            "pazar": self.pazar_giris.get().strip() or None,
            "ham": self.cevap_kutu.get("1.0", "end").strip(),
        }
        model_p = None
        try:
            from iddaa import analiz
            poi = analiz.poisson_tahmini(self.df, r["HomeTeam"], r["AwayTeam"],
                                         lig_ipucu=r.get("Div"))
            model_p = {"MS1": round(poi["ms1"], 4), "MS0": round(poi["ms0"], 4),
                       "MS2": round(poi["ms2"], 4)}
        except Exception:  # noqa: BLE001 — model kıyası olmadan da kayıt tutulur
            model_p = None
        try:
            danisma.kaydet(self._kaynak_anahtari(), r["HomeTeam"], r["AwayTeam"], r["Tarih"],
                           cevap, lig=str(r.get("LigAdi") or r.get("Div") or ""), model_p=model_p)
        except ValueError as hata:
            self.cevap_not.configure(text=f"KAYDEDİLMEDİ: {hata}", style="Uyari.TLabel")
            messagebox.showwarning("Kaydedilmedi", str(hata))
            return
        self.cevap_not.configure(text="Deftere kaydedildi. Maç bitince otomatik sonuçlanır.",
                                 style="Soluk.TLabel")
        self._karne_yenile()

    # ─────────────────────────────────────────────── karne
    def _karne_yenile(self):
        self.karne_not.configure(text="sonuçlandırılıyor…")
        self._is_baslat("karne", lambda: (danisma.sonuclandir(self.df),
                                          danisma.karne(danisma.defter_oku())))

    def _karne_ciz(self, paket):
        defter, k = paket
        for satir in self.karne_tablo.get_children():
            self.karne_tablo.delete(satir)

        def yz(x, b=0):
            return "—" if x is None else f"%{x*100:.{b}f}"

        for kaynak, d in k.items():
            self.karne_tablo.insert("", "end", values=(
                d["ad"], d["n"], d["sonuclu"], d["tutan"],
                yz(d["isabet"]) + ("" if d["yargi"] else " (yargı yok)"),
                f"{yz(d['skor_isabet'])} ({d['skor_n']})",
                yz(d["dedi_guven"]),
                f"{yz(d['model_isabet'])} ({d['model_n']})"))
        toplam = sum(d["n"] for d in k.values())
        sonuclu = sum(d["sonuclu"] for d in k.values())
        self.karne_not.configure(text=f"{toplam} cevap · {sonuclu} sonuçlandı")
        self.kayit_kutu.delete("1.0", "end")
        satirlar = []
        for kayit in defter[:40]:
            isaret = {True: "✓", False: "✗", None: "…"}[kayit.get("isabet")]
            satirlar.append(
                f"{isaret} {kayit['tarih']} {kayit['ev']} – {kayit['dep']} · "
                f"{danisma.HEDEFLER.get(kayit['kaynak'], {}).get('ad', kayit['kaynak'])}: "
                f"{kayit.get('sonuc') or '?'} {kayit.get('skor') or ''}"
                + (f" (güven %{kayit['guven']*100:.0f})" if kayit.get("guven") else "")
                + (f" → gerçek {kayit['gercek_skor']}" if kayit.get("gercek_skor") else " → bekliyor"))
        self.kayit_kutu.insert("1.0", "\n".join(satirlar) or "Henüz kayıt yok.")


def calistir():
    Uygulama().mainloop()
