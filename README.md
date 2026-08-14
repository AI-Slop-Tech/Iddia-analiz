# ⚽ İddaa Analiz Sistemi

Ücretsiz tarihsel veriyle çalışan, profesyonel yaklaşımlı maç analiz aracı.
**Ücretli API gerektirmez** — skorlar ve bahis oranları, herkese açık
[football-data.co.uk](https://www.football-data.co.uk/data.php) arşivinden indirilir
(1993'e kadar giden maç sonuçları + Bet365/Pinnacle/piyasa ortalaması oranları).

Sistem dört bağımsız sinyal üretir ve bunları tek bir öneriye bağlar:

| Sinyal | Ne yapar |
|---|---|
| 📊 **Form** | İki takımın son 10 maçı + saha bazlı form (evinde/deplasmanda) |
| 🔁 **H2H** | İki takımın birbirine karşı tüm geçmişi (gol, üst/alt, KG eğilimleri) |
| 📈 **Oran kalıbı** | Bugünkü orana benzer oranla açılmış **geçmiş 10 yılın tüm maçlarında** gerçekte ne olduğu — "aynı oranlar geçmişte ne getirdi?" |
| 🎯 **Poisson modeli** | Zaman ağırlıklı hücum/savunma güçlerinden gol beklentisi, skor olasılık matrisi, MS/Alt-Üst/KG olasılıkları |

Üstüne **değer (value) analizi**: modelin olasılığı, oranın içerdiği (marjdan
arındırılmış) olasılıkla karşılaştırılır. Pozitif beklenen değer yoksa sistem
açıkça **"pas geç"** der — profesyonel bahisçiliğin ilk kuralı budur.

İsteğe bağlı **Gemini AI katmanı**, istatistik raporunu deneyimli bir analist
ağzından yazılmış yorum ve kupon önerisine çevirir.

---

## Kurulum

```bash
pip install -r requirements.txt        # pandas + requests, hepsi bu
```

## Hızlı başlangıç

```bash
# 1) Veriyi indir (ilk seferde ~1 dk; sonrası önbellekten)
python tahmin.py guncelle

# 2) Bültendeki oranlarla maçı analiz et  (sıra: MS1 MS0 MS2)
python tahmin.py analiz --ev Galatasaray --dep Fenerbahce --oran 2.04 3.55 3.45

# Oran vermeden de çalışır (form + H2H + Poisson):
python tahmin.py analiz --ev "beşiktaş" --dep trabzon

# Gemini yorumu ile:
export GEMINI_API_KEY="..."            # ücretsiz: https://aistudio.google.com/apikey
python tahmin.py analiz --ev Alanyaspor --dep Konyaspor --oran 2.20 3.30 3.30 --gemini
```

Takım adlarında Türkçe karakter ve kısaltma serbesttir: `başakşehir`,
`göztepe`, `bayern münih`, `man city`, `real` gibi girişler otomatik eşlenir.

## 🌐 Web arayüzü

```bash
python tahmin.py web          # http://127.0.0.1:8000
```

Koyu temalı, tek sayfalık modern panel; üç ayrı bölümden oluşur:

- **📈 Oran Analizi** — takımdan bağımsız: 1X2 oranını girin, geçmişte benzer
  oranla açılan tüm maçların gerçek dağılımını, gol/üst-alt/KG eğilimlerini,
  en sık skorları, örnek maçları ve orana göre beklenen değer sinyalini görün.
- **🎯 Takım Analizi** — iki takımı seçin (oran girmek isteğe bağlı): form
  serileri, aralarındaki maçlar, Poisson tahmini; oran girilirse oran kalıbı,
  değer tablosu ve yıldızlı öneri banner'ı eklenir.
- **🕰 Geçmiş Maçlar** — eski maçları açılış oranlarıyla listeleyin;
  "📈 bu oranı analiz et" düğmesi o maçın oranını Oran Analizi'ne taşır.

Veri hiç indirilmemişse panel tek tıkla indirme önerir; üstteki
**Veriyi Güncelle** düğmesi arşivi tazeler. Arayüz `iddaa/static/index.html`
içindedir (bağımlılıksız, tek dosya); JSON API uçları `iddaa/web.py` başında
listelenmiştir.

## Komutlar

| Komut | Açıklama |
|---|---|
| `guncelle [--ligler T1 E0 ...] [--sezon 11] [--yenile]` | Veri indir/güncelle. Varsayılan: T1 E0 SP1 D1 I1 F1, son 10 yıl + bu sezon |
| `analiz --ev X --dep Y [--oran 1 X 2] [--gemini] [--tolerans 0.02]` | Maç analizi ve rapor |
| `takimlar [--lig T1]` | Veri setindeki resmi takım adları (● = güncel takım) |
| `durum` | İndirilen veri setinin özeti |
| `web [--port 8000] [--host 127.0.0.1]` | Modern web panelini başlat |

Desteklenen ligler: `T1` Süper Lig, `E0` Premier League, `E1` Championship,
`SP1` La Liga, `D1` Bundesliga, `I1` Serie A, `F1` Ligue 1, `N1` Eredivisie,
`P1` Primeira Liga, `B1` Belçika, `G1` Yunanistan, `SC0` İskoçya.

## Örnek çıktı (gerçek veriyle)

```
📈 ORAN KALIBI — geçmişte benzer oranla açılan maçlarda ne oldu?
──────────────────────────────────────────────────────────────────
  2016'den bu yana 948 benzer maç bulundu  (6 lig, tolerans ±2.0 puan)
    Ev kazandı    █████████░░░░░░░░░░░░░  %41
    Beraberlik    ███████░░░░░░░░░░░░░░░  %31
    Dep. kazandı  ██████░░░░░░░░░░░░░░░░  %28
    Maç başı gol: 2.77   |   Üst 2.5: %52   |   KG Var: %58
    En sık skorlar: 1-1 (%15)  2-1 (%9)  0-0 (%8)  1-0 (%7)  2-0 (%7)

💰 DEĞER ANALİZİ  (model olasılığı vs oranın içerdiği olasılık)
──────────────────────────────────────────────────────────────────
  Model karışımı: %50 Poisson + %50 oran kalıbı   |   Bahisçi marjı: %6.2
    Seçim   Oran    Piyasa    Model     Beklenen değer
    MS1    2.04     %46      %43      -13.0%
    MS0    3.55     %27      %27       -4.5%
    MS2    3.45     %27      %30       +5.0%  ✅

🧠 SONUÇ & ÖNERİ
──────────────────────────────────────────────────────────────────
  ✅ DEĞERLİ BAHİS: MS 2 (deplasman) @ 3.45   Güven: ★★☆☆☆
     Beklenen değer +5.0%. Önerilen kasa payı (çeyrek Kelly): %0.5
```

## Metodoloji

**Oran kalıbı eşleştirme.** Verdiğiniz 1X2 oranları önce bahis marjından
arındırılıp olasılığa çevrilir. Arşivdeki ~21.000 maç aynı şekilde
olasılıklaştırılır ve üç olasılığı da ±2 puan içinde kalan maçlar seçilir
(örnek azsa tolerans kademeli genişler). Böylece "bahisçilerin bu maça biçtiği
gömlek"le geçmişte açılan yüzlerce maçın gerçek sonuç dağılımı elde edilir.
Olasılık uzayında eşleştirme yapıldığı için farklı yılların/bahisçilerin marj
farkları kalıbı bozmaz.

**Poisson modeli.** Her takım için zaman ağırlıklı (yarı ömür 1 yıl — yani
geçen sezonki maç bu sezonkinin yarısı kadar sayılır) iç saha/deplasman
hücum ve savunma güçleri hesaplanır, lig ortalamalarıyla çarpılarak gol
beklentileri (λ) bulunur. 0-8 gollük skor matrisi üzerinden MS, Alt/Üst 2.5,
KG ve en olası skor olasılıkları türetilir. Bu, profesyonel oran yapıcıların
da temel aldığı klasik yaklaşımdır.

**Değer (value) ve Kelly.** Model olasılığı `p`, oran `o` için beklenen değer
`EV = p·o − 1`. Model, Poisson ile oran kalıbının karışımıdır (kalıp örneklemi
büyüdükçe ağırlığı %50'ye kadar çıkar). EV ≥ %4 ise "değerli", %1-4 arası
"sınırda", altı "pas". Önerilen kasa payı çeyrek Kelly ile hesaplanır ve %5
ile sınırlanır. **Uzun vadede para kazandıran şey doğru tahmin değil, pozitif
beklenen değerli oranları oynamaktır** — sistem bu yüzden çoğu maçta "pas" der.

**Güven yıldızı (1-5).** Poisson zirvesi, kalıp zirvesi ve form yönü aynı
seçimi işaret ettikçe ve EV büyüdükçe yıldız artar; veri az olan takımlarda
(yeni çıkan takımlar gibi) bir yıldız düşülür.

## Gemini AI yorumu (opsiyonel)

Sistem Gemini olmadan da tam çalışır. Yorum katmanı için:

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresinden ücretsiz anahtar alın (kredi kartı gerekmez).
2. `export GEMINI_API_KEY="..."` — isterseniz `GEMINI_MODEL` ile model seçin (varsayılan `gemini-2.5-flash`).
3. `analiz` komutuna `--gemini` ekleyin.

Gemini'ye maçın tüm istatistik raporu gönderilir; "20 yıllık analist" personasıyla
maç yorumu, en güçlü 3 sinyal, güven notlu kupon önerileri ve uzak durulacak
seçenekler yazdırılır. Prompt `iddaa/gemini_yorum.py` içinde — dilediğiniz gibi
özelleştirin.

## Güncel oranları nereden alacağım?

Sistem *tarihsel* oranları otomatik indirir; oynanmamış maçın *bültendeki*
oranını ise Nesine/Bilyoner/iddaa bülteninden bakıp `--oran` ile elle
girersiniz (10 saniyelik iş). Bülten sitelerini otomatik kazımak hem kullanım
şartlarına aykırı hem de kırılgan olduğu için bilinçli olarak eklenmedi.

## Sınırlar ve yol haritası

- Model sakatlık/ceza, kadro değeri, hakem, hava, motivasyon (küme düşme,
  şampiyonluk) gibi faktörleri **görmez** — Gemini yorumu bile yalnızca verilen
  istatistiklere dayanır. Nihai karar her zaman sizindir.
- Fikir listesi: Dixon-Coles düzeltmesi, Elo reytingleri, xG verisi entegrasyonu,
  geçmişe dönük strateji testi (backtest + ROI), Streamlit web arayüzü,
  alt ligler ve kupa maçları.

## ⚠️ Sorumlu oyun

Bu yazılım eğitim ve analiz amaçlıdır; kazanç garantisi yoktur ve hiçbir çıktı
yatırım/bahis tavsiyesi değildir. Bahis 18 yaş altına yasaktır. Kaybetmeyi göze
alamayacağınız parayla oynamayın. Türkiye'de yasal bahis yalnızca lisanslı
platformlar üzerinden oynanabilir.
