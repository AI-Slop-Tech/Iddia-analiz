# ⚽ İddaa Analiz Sistemi

Ücretsiz tarihsel veriyle çalışan, profesyonel yaklaşımlı maç analiz aracı.
**Ücretli API gerektirmez** — skorlar ve bahis oranları, herkese açık
[football-data.co.uk](https://www.football-data.co.uk/data.php) arşivinden indirilir
(1993'e kadar giden maç sonuçları + Bet365/Pinnacle/piyasa ortalaması oranları).

Sistem dört bağımsız sinyal üretir ve bunları tek bir öneriye bağlar:

| Sinyal | Ne yapar |
|---|---|
| 📅 **Bülten** | Önümüzdeki günlerin fikstürü **otomatik** çekilir — Bet365, Betfair, Bwin, Paddy Power, SkyBet, BetVictor… oranları + piyasa ortalaması ve en yüksek oranla |
| 📊 **Form** | İki takımın son 10 maçı + saha bazlı form (evinde/deplasmanda) |
| 🔁 **H2H** | İki takımın birbirine karşı tüm geçmişi (gol, üst/alt, KG eğilimleri) |
| 📈 **Oran kalıbı** | Bugünkü orana benzer oranla açılmış **geçmiş 10 yılın tüm maçlarında** gerçekte ne olduğu — "aynı oranlar geçmişte ne getirdi?" |
| 🎯 **Poisson modeli** | Zaman ağırlıklı hücum/savunma güçlerinden gol beklentisi, skor olasılık matrisi, MS/Alt-Üst/KG olasılıkları |
| ♟ **Elo reytingi** | Tüm arşivden hesaplanan takım güç reytingi; öneri güven yıldızına katkı verir |

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
# 0) Veri kaynağına erişimi test et — Türkiye'den engelli, vekil gerekir
python tahmin.py baglanti           # ayrıntı: "Erişim sorunu (Türkiye)"

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

Koyu temalı, tek sayfalık modern panel; dört bölümden oluşur:

- **📅 Bu Hafta** — fikstür ve çok kitapçılı oranlar otomatik gelir, günler
  pill'lerle gezilir. **⚡ Günü Tara** günün tüm maçlarını arşivle eşleştirip
  en iyi piyasa oranına göre değer sırasına dizer (✅/🟡/⛔ + güven yıldızı +
  "N benzer maç"). Her maçın **🔬 Detay**'ında kitapçı oranları panosu
  (en yüksek oran yeşil işaretli) ve tam analiz raporu açılır.
- **📋 Tahmin Tablosu** — seçilen günün *tüm* maçları klasik tahmin matrisi
  düzeninde: İY 0.5 / İY 1.5 / MS 1.5 / 2.5 / 3.5 Alt-Üst, KG, İY-2Y-MS skor
  tahminleri ve İY/MS sonuçları. Renk yönü, koyuluk ve yüzde güveni gösterir.
  İY tahminleri, ligdeki gollerin ilk yarıya düşen payı (arşivdeki gerçek İY
  skorlarından ölçülür, tipik ~%45) ile gol beklentisinin bölüştürülmesinden
  türetilir. Maç detayının altında aynı kolonlarla **benzer oranlı geçmiş
  maçların gerçekleşenleri** listelenir — skor bazlı eşleştirme.
- **📈 Oran Analizi** — takımdan bağımsız: 1X2 oranını girin, geçmişte benzer
  oranla açılan tüm maçların gerçek dağılımını, gol/üst-alt/KG eğilimlerini,
  en sık skorları, örnek maçları ve orana göre beklenen değer sinyalini görün.
- **🎯 Takım Analizi** — iki takımı seçin (oran girmek isteğe bağlı): form
  serileri, aralarındaki maçlar, Elo, Poisson tahmini; oran girilirse oran
  kalıbı, değer tablosu ve yıldızlı öneri banner'ı eklenir.
- **📊 Sonuçlar & Tahmin Karnesi** — oynanan maçların skoru, ilk yarı skoru,
  gerçekleşen 1X2/Alt-Üst/KG rozetleri ve maç istatistikleri (şut, isabetli
  şut, korner, kart). Günü Tara ve Tahmin Tablosu, tahminlerini **maç
  başlamadan** `data/tahminler.json` günlüğüne kaydeder; maç bitince burada
  gerçek skorla notlanır: MS/Ü-A/KG isabet oranları ve değerli seçimlerin
  gerçek kar/zararı — sistemin ileriye dönük, hilesiz karnesi. (Kaynak,
  skorları haftada birkaç kez toplu işler; 1-2 gün gecikme normaldir.
  Canlı skor yayını yoktur — canlı servisler API anahtarı gerektirir,
  yol haritasındadır.)
- **🕰 Geçmiş Maçlar** — eski maçları açılış oranlarıyla listeleyin;
  "📈 bu oranı analiz et" düğmesi o maçın oranını Oran Analizi'ne taşır.
- **🧪 Backtest** — stratejinin geçmiş karnesi: çift senaryolu ROI
  (açılış / en iyi oran), bakiye eğrisi, eşik-ROI tablosu, seçim/lig/sezon
  kırılımları.

**Takvim ve veriler kendiliğinden güncellenir:** gün ilerledikçe geçmiş günler
takvimden düşer, günün pili "Bugün/Yarın" olarak etiketlenir, başlama saati
geçen maçlar "▶ başladı" işareti alır. Sunucu arka planda fikstürü en geç
6 saatte bir, güncel sezon arşivini günde bir kez tazeler; açık kalan sayfa da
10 dakikada bir sessizce yenilenir — gece yarısı geçse bile panel bayatlamaz.

Veri hiç indirilmemişse panel tek tıkla indirme önerir; üstteki
**Veriyi Güncelle** düğmesi arşivi elle tazeler. Arayüz `iddaa/static/index.html`
içindedir (bağımlılıksız, tek dosya, mobil uyumlu); JSON API uçları
`iddaa/web.py` başında listelenmiştir.

## Komutlar

| Komut | Açıklama |
|---|---|
| `guncelle [--ligler T1 E0 ...] [--sezon 11] [--yenile]` | Veri indir/güncelle. Varsayılan: 22 ligin tamamı, son 10 yıl + bu sezon |
| `analiz --ev X --dep Y [--oran 1 X 2] [--gemini] [--tolerans 0.02]` | Maç analizi ve rapor |
| `bulten [--tara] [--lig T1] [--yenile]` | Önümüzdeki günlerin maçlarını oranlarıyla listele; `--tara` her maça öneri ekler |
| `takimlar [--lig T1]` | Veri setindeki resmi takım adları (● = güncel takım) |
| `durum` | İndirilen veri setinin özeti |
| `baglanti` | Veri kaynağına erişimi ve vekil ayarını test et ([Erişim sorunu](#erişim-sorunu-türkiye)) |
| `backtest [--sezon 3] [--lig T1] [--esik 0.04] [--maks-oran 3.60]` | Stratejiyi geçmişte test et: ROI, eşik tablosu, kırılımlar |
| `web [--port 8000] [--host 127.0.0.1]` | Modern web panelini başlat |

Desteklenen ligler (22): Türkiye Süper Lig (`T1`); İngiltere'nin 5 katmanı
(`E0`-`E3`, `EC`); İskoçya'nın 4 katmanı (`SC0`-`SC3`); Almanya, İtalya,
İspanya ve Fransa'nın 2'şer katmanı (`D1/D2`, `I1/I2`, `SP1/SP2`, `F1/F2`);
Hollanda `N1`, Belçika `B1`, Portekiz `P1`, Yunanistan `G1`. Varsayılan
`guncelle` hepsini indirir (~77 bin maç) — böylece haftalık fikstürde görünen
her maçın hem geçmişi hem oran kalıbı örneklemi hazır olur.

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
`EV = p·o − 1`. Model üç bileşenin karışımıdır: **%35 piyasa** (marjdan
arındırılmış oran — piyasa uzun vadede en iyi tekil tahmincidir, karışıma
katmak aşırı özgüveni ve sahte değer sinyallerini azaltır), **%0-25 oran
kalıbı** (örneklem büyüdükçe artar, 200+ maçta tavan) ve **kalan pay Poisson**.
Üst/Alt 2.5 oranı varsa aynı karışım o pazar için de kurulur. EV ≥ %4 ise
"değerli", %1-4 arası "sınırda", altı "pas". Bültende sıralama, seçimin
**piyasadaki en yüksek oranıyla** hesaplanan EV'ye göredir (değerli bahis
en iyi orandan oynanır). Önerilen kasa payı çeyrek Kelly ile hesaplanır ve %5
ile sınırlanır. **Uzun vadede para kazandıran şey doğru tahmin değil, pozitif
beklenen değerli oranları oynamaktır** — sistem bu yüzden çoğu maçta "pas" der.

**Elo reytingi.** Tüm arşiv kronolojik gezilerek her takım için Elo hesaplanır
(K=20, ev avantajı 60 puan, başlangıç 1500). Raporda gösterilir ve güven
yıldızına sinyal olarak katılır.

**Güven yıldızı (1-5).** Poisson zirvesi, kalıp zirvesi, form yönü ve Elo yönü
aynı seçimi işaret ettikçe ve EV büyüdükçe yıldız artar; veri az olan
takımlarda (yeni çıkan takımlar gibi) bir yıldız düşülür.

## 🧪 Backtest — sistemin karnesi (dürüst sonuçlar)

```bash
python tahmin.py backtest --sezon 3            # tüm ligler
python tahmin.py backtest --lig T1 --esik 0.04 # tek lig
```
veya panelde **🧪 Backtest** sekmesi.

Nasıl çalışır: maçlar kronolojik gezilir; her maç **yalnızca kendinden önceki
verilerle** değerlendirilir (bakış sızıntısı yok), canlı modelle birebir aynı
karışım kullanılır, eşiği aşan seçime düz 1 birim oynanır, 3.60 üzeri oranlar
sürpriz filtresine takılır. İki senaryo raporlanır: açılış oranıyla ve aynı
kuponlar piyasadaki **en iyi** oranla.

Örnek koşu (son 3 sezon, 22 lig, 15.063 maç, 5.254 bahis, eşik +%4):

| Strateji | ROI |
|---|---|
| Sistem — açılış oranıyla | **-%8.2** |
| Sistem — en iyi oranla | **-%5.4** |
| Her maç ev sahibi | -%7.9 |
| Her maç favori | -%5.5 |

**Bu negatif sonuç bir hata değil, backtest'in görevi.** Herkese açık veriyle
kurulan bir modelin açılış oranlarında ~%6'lık bahisçi marjını yenememesi
literatürle uyumludur. Sürpriz filtresi olmadan ROI -%12'ydi — filtre tek
başına 4 puan kazandırdı. Sistemin gerçek değeri: sahte "değer" sinyallerini
süzmesi, değersiz maçta **PAS** demesi, en iyi oranı göstererek marj kaybını
azaltması ve her değişikliğin etkisini bu sekmede ölçülebilir kılması. Uzun
vadede artı ROI, ancak piyasada henüz fiyatlanmamış bilgiyle (erken oran,
sakatlık/kadro istihbaratı) ve en iyi oran disipliniyle mümkündür.

## Gemini AI yorumu (opsiyonel)

Sistem Gemini olmadan da tam çalışır. Yorum katmanı için:

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresinden ücretsiz anahtar alın (kredi kartı gerekmez).
2. `export GEMINI_API_KEY="..."` — isterseniz `GEMINI_MODEL` ile model seçin (varsayılan `gemini-2.5-flash`).
3. `analiz` komutuna `--gemini` ekleyin.

Gemini'ye maçın tüm istatistik raporu gönderilir; "20 yıllık analist" personasıyla
maç yorumu, en güçlü 3 sinyal, güven notlu kupon önerileri ve uzak durulacak
seçenekler yazdırılır. Prompt `iddaa/gemini_yorum.py` içinde — dilediğiniz gibi
özelleştirin.

## Güncel oranları nereden alıyor?

Hem *tarihsel* oranlar hem de *önümüzdeki günlerin* fikstür oranları
football-data.co.uk'dan otomatik indirilir. Fikstürde Bet365, Betfair,
Betfair Borsası, Bwin, Paddy Power, SkyBet, BetVictor gibi uluslararası
kitapçıların açılış oranları ile piyasa ortalaması ve en yüksek oran bulunur;
panel bunları maç başına karşılaştırmalı gösterir. Türkiye bülteni (Nesine
vb.) farklıysa oranı Oran Analizi / Takım Analizi sekmesine elle girip aynı
analizi o oranla da alabilirsiniz — bülten sitelerini kazımak kullanım
şartlarına aykırı ve kırılgan olduğu için bilinçli olarak eklenmedi.

> Kaynak Türkiye'den erişime kapalıdır; sunucunuz Türkiye'deyse
> [Erişim sorunu (Türkiye)](#erişim-sorunu-türkiye) bölümüne bakın.

## Erişim sorunu (Türkiye)

`football-data.co.uk` bahis oranı yayınladığı için **Türkiye'den erişime
kapalıdır**. Türkiye'deki bir makineden `guncelle` çalıştırırsanız bağlantı
hatası alırsınız. Önce nerede olduğunuzu test edin:

```bash
python tahmin.py baglanti
```

`✅ erişim var` çıkıyorsa yapacak bir şey yok. `❌ erişilemedi` çıkıyorsa
aşağıdaki üç yoldan birini seçin.

**Neden yerel bir ayna yetmiyor?** Denendi, olmuyor: GitHub'daki iki ayna
(`footballcsv/cache.footballdata`, `datasets/football-datasets`) CSV'leri
yeniden biçimlendirirken **tüm kitapçı oran kolonlarını atıyor** — geriye
`Date, Team 1, FT, HT, Team 2` kalıyor. Bu projenin tamamı oranlar üzerine
kurulu olduğu için aynalar kullanılamaz. Dahası `fixtures.csv` (önümüzdeki
maçlar + güncel oranlar, günlük değişir) **hiçbir yerde aynalanmıyor**;
bülten ve tarama özellikleri onsuz çalışmaz. Yani kaynağa gerçekten erişmek
gerekiyor.

### 1. Sunucuyu yurt dışında çalıştırın (en basit)

Coolify/VPS'iniz Türkiye dışındaysa (Hetzner, DigitalOcean, Contabo...)
hiçbir ayar gerekmez. İnen dosyalar kalıcı volume'de (`/app/data`)
önbelleklenir; eski sezonlar bir daha indirilmez, yalnızca güncel sezon ve
fikstür tazelenir. Panele Türkiye'den erişmenizde sorun yoktur — engel
yalnızca sunucudan kaynağa giden trafiği ilgilendirir.

### 2. `IDDAA_PROXY` — HTTP/SOCKS5 vekil

```bash
export IDDAA_PROXY="http://kullanici:parola@vekil.example.com:8080"
# SOCKS5 için:  pip install "requests[socks]"
export IDDAA_PROXY="socks5h://127.0.0.1:1080"

python tahmin.py baglanti   # ayarı doğrulayın
python tahmin.py guncelle
```

Yalnızca veri kaynağına giden istekler bu vekilden geçer. Değişken boşsa
sistemin standart `HTTP_PROXY` / `HTTPS_PROXY` değişkenleri geçerli olmaya
devam eder; `IDDAA_PROXY` tanımlıysa onları ezer. Adresteki parola log ve
API çıktılarında `***` ile maskelenir.

### 3. `IDDAA_KAYNAK_TABAN` — kendi ters vekiliniz (ücretsiz)

Vekil satın almak istemiyorsanız, ücretsiz bir Cloudflare Worker kaynağın
önüne geçici bir tampon olarak konabilir. Worker kodu:

```js
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const hedef = "https://www.football-data.co.uk" + url.pathname + url.search;
    const yanit = await fetch(hedef, { headers: { "User-Agent": "iddaa-analiz/1.0" } });
    return new Response(yanit.body, {
      status: yanit.status,
      headers: { "Content-Type": yanit.headers.get("Content-Type") || "text/csv" },
    });
  },
};
```

Ardından:

```bash
export IDDAA_KAYNAK_TABAN="https://iddaa-veri.hesabiniz.workers.dev"
python tahmin.py baglanti
```

`workers.dev` alan adı Türkiye'den erişilebilir ve ücretsiz kotası
(günlük 100.000 istek) bu projenin ihtiyacının çok üstündedir — ilk kurulumda
~242, sonrasında günde birkaç istek yapılır.

### Docker / Coolify

Her iki değişken de `docker-compose.yml` içinde tanımlıdır; değeri Coolify'ın
**Environment Variables** ekranından verin, servisi yeniden başlatın. Panelden
`GET /api/baglanti` ile de test edebilirsiniz.

### Erişim yokken ne olur?

`guncelle` 242 dosyayı tek tek denemek yerine ilk başarısız denemeden sonra
(3 tekrar, üstel bekleme) **erken durur** ve ne yapmanız gerektiğini yazar.
O ana kadar inen dosyalar diskte kalır; erişim sağlanınca `guncelle` kaldığı
yerden devam eder. Fikstürde ağ kapalıysa, elde eski bir kopya varsa panel
onunla çalışmaya devam eder.

## Sınırlar ve yol haritası

- Model sakatlık/ceza, kadro değeri, hakem, hava, motivasyon (küme düşme,
  şampiyonluk) gibi faktörleri **görmez** — Gemini yorumu bile yalnızca verilen
  istatistiklere dayanır. Nihai karar her zaman sizindir.
- Fikir listesi: Dixon-Coles düzeltmesi, xG verisi entegrasyonu, kupon takibi
  (oynanan kuponların gerçek sonuçlarla izlenmesi), kapanış oranı analizi,
  kupa/milli ara maçları.

## ⚠️ Sorumlu oyun

Bu yazılım eğitim ve analiz amaçlıdır; kazanç garantisi yoktur ve hiçbir çıktı
yatırım/bahis tavsiyesi değildir. Bahis 18 yaş altına yasaktır. Kaybetmeyi göze
alamayacağınız parayla oynamayın. Türkiye'de yasal bahis yalnızca lisanslı
platformlar üzerinden oynanabilir.
