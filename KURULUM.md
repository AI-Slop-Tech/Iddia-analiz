# 💻 Kendi Bilgisayarında Çalıştırma (Windows / Mac / Linux)

Sistemi kendi makinende çalıştırmanın iki büyük avantajı var:

- **API anahtarın tek bir IP'den kullanılır.** Aynı anahtarı hem sunucudan
  hem başka bir yerden kullanmak, sağlayıcının "anahtar paylaşımı" sayıp
  hesabı askıya almasına yol açabiliyor. Yerelde bu risk yok.
- Anahtarı kimseye vermene gerek kalmaz; makineden dışarı çıkmaz.

---

## ⚠️ ÖNCE BUNU OKU — Türkiye'den erişim engeli

Veri kaynağı `football-data.co.uk` bahis oranı yayınladığı için
**Türkiye'den erişime kapalıdır.** Bu, bu projenin çalışması için zorunlu
kaynak — arşiv de bülten de oradan geliyor.

Kurulumdan sonra ilk iş bunu test etmek:

```bash
python tahmin.py baglanti
```

- `✅ erişim var` → harika, hiçbir şey yapmana gerek yok
- `❌ erişilemedi` → **VPN aç** (yurt dışı sunucu seç) ve komutu tekrarla

**Hızlı çözüm:** VPN aç, indirme bitene kadar açık kalsın (2-3 dakika),
sonra kapatabilirsin. Arşiv diske iner, bir daha inmez.

**Kalıcı çözüm:** aşağıdaki bölüm — 5 dakika, ücretsiz, bir daha VPN
gerekmez.

---

## 🔓 VPN'siz kalıcı çözüm — ücretsiz Cloudflare Worker

Kaynağın önüne kendi ücretsiz "aynanı" koyuyorsun. `workers.dev` adresi
Türkiye'den erişilebilir olduğu için engel devre dışı kalır.

**1.** [dash.cloudflare.com](https://dash.cloudflare.com) → ücretsiz hesap aç
(kredi kartı istemez).

**2.** Sol menüden **Workers & Pages** → **Create** → **Start with Hello
World!** → **Deploy**.

**3.** Açılan sayfada **Edit code** (veya **</> Edit**) düğmesine bas.
Editördeki tüm kodu sil, yerine bunu yapıştır:

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

**4.** Sağ üstten **Deploy** (veya **Save and deploy**).

**5.** Sayfada Worker'ının adresi yazar, şuna benzer:
`https://hello-world.kullaniciadin.workers.dev` — **bu adresi kopyala.**

**6.** Proje klasöründe `ayarlar.ornek.txt` dosyasını kopyala, adını
**`ayarlar.txt`** yap. Not Defteri ile aç ve şu satırı ekle (başındaki
`#` olmadan, kendi adresinle):

```
IDDAA_KAYNAK_TABAN=https://hello-world.kullaniciadin.workers.dev
```

**7.** `BASLAT.bat`'a tekrar çift tıkla. Artık VPN'siz çalışır.

> Ücretsiz kota günde 100.000 istek. Bu proje ilk kurulumda ~242, sonra
> günde birkaç istek yapar — kotaya yaklaşman mümkün değil.

---

## ⚡ EN KOLAY YOL — çift tıkla, bitsin (Windows)

Terminal komutlarıyla uğraşmak istemiyorsan: proje klasöründeki
**`BASLAT.bat`** dosyasına **çift tıkla.** Hepsi bu.

Dosya sırayla şunları kendisi yapar:

1. Python'u bulur (`py` veya `python`) — yoksa ne yapman gerektiğini yazar
2. Gerekli paketleri kurar
3. Veri kaynağına erişimi test eder — engelliyse "VPN aç" der ve durur
4. Arşiv yoksa indirir (ilk sefer 2-3 dakika)
5. Paneli başlatır ve tarayıcıyı açar

Bir hata olursa pencere **kapanmaz**, ne olduğunu okursun.

> **Not defteri açılıyorsa:** dosyaya sağ tık → **Birlikte aç** →
> **Windows Komut İşlemcisi**. `.bat` uzantısı yanlış programa
> bağlanmış demektir.

Aşağıdaki adımlar bu dosyanın elle yapılışıdır — `BASLAT.bat` çalışıyorsa
onlara ihtiyacın yok.

---

## 1. Python kur

**Windows:** [python.org/downloads](https://www.python.org/downloads/) →
Python 3.12 indir. Kurulumda **"Add python.exe to PATH"** kutusunu
İŞARETLE (en alttaki kutu — atlanırsa komutlar çalışmaz).

**Mac:** `brew install python@3.12`
**Linux:** çoğu dağıtımda hazır gelir.

Kontrol:

```bash
python --version      # Windows
python3 --version     # Mac/Linux
```

`3.10` veya üstü görmen yeterli.

---

## 1.5. ⚠️ Komutları NEREYE yazacaksın

Bu en sık yapılan hata: komutlar **PowerShell'e** yazılır, Python'un
kendi içine değil.

Ekranda gördüğün işarete bak:

| gördüğün | neredesin | doğru mu? |
|---|---|---|
| `>>>` | Python yorumlayıcısının **içindesin** | ❌ komutlar burada çalışmaz |
| `PS C:\Users\...>` | PowerShell | ✅ doğru yer |
| `C:\Users\...>` | CMD | ✅ doğru yer |

`>>>` görüyorsan Python'un içindesin demektir. `python tahmin.py baglanti`
yazarsan şu hatayı alırsın:

```
SyntaxError: invalid syntax
```

Çünkü orası Python **kodu** yazılan yer, komut yazılan yer değil.

**Çıkmak için:** `exit()` yaz, Enter.
**Doğru yeri açmak için:** Başlat menüsüne `PowerShell` yaz ve aç.

---

## 2. Projeyi indir

**Git varsa:**

```bash
git clone https://github.com/AI-Slop-Tech/Iddia-analiz.git
cd Iddia-analiz
```

**Git yoksa:** GitHub sayfasında yeşil **Code** düğmesi → **Download ZIP**
→ masaüstüne çıkar → o klasöre gir.

---

## 3. Gerekli paketleri kur

Önce **proje klasörünün içinde** olduğundan emin ol. PowerShell'de:

```powershell
cd "$env:USERPROFILE\Desktop\Iddia-analiz-main"    # klasörünü nereye çıkardıysan
dir tahmin.py                                       # dosyayı listelemeli
```

`tahmin.py` listeleniyorsa doğru yerdesin. "bulunamadı" diyorsa yanlış
klasördesin — `dir` ile bak, doğru klasöre `cd` ile gir.

Sonra:

```bash
pip install -r requirements.txt
```

Windows'ta `pip` bulunamazsa: `python -m pip install -r requirements.txt`

Kurulanlar: pandas, requests, flask, tzdata. Hepsi standart, birkaç saniye
sürer.

---

## 4. Bağlantıyı test et

```bash
python tahmin.py baglanti
```

`❌` görürsen VPN'i açıp tekrar dene. `✅` görmeden devam etme — sonraki
adım kaynağa bağlanmayı gerektiriyor.

---

## 5. Veriyi indir (ilk sefer 2-3 dakika)

```bash
python tahmin.py guncelle
```

26 sezonluk arşiv iner (~250 bin maç, oranlarıyla). `data/` klasörüne
kaydedilir; bir daha inmez, sonraki çalıştırmalarda yalnız güncel sezon ve
fikstür tazelenir.

Kontrol:

```bash
python tahmin.py durum
```

---

## 6. API anahtarını tanımla (isteğe bağlı ama önerilir)

Anahtarsız da çalışır — ana ~22 ligin maçları oranlarıyla gelir. Anahtar
eklersen Şampiyonlar Ligi, kupalar ve dünya ligleri de bültene girer.

**Windows (PowerShell):**

```powershell
$env:APIFOOTBALL_KEY = "buraya_anahtarin"
python tahmin.py web
```

**Windows (kalıcı — her seferinde yazmamak için):**

```powershell
setx APIFOOTBALL_KEY "buraya_anahtarin"
```

Sonra PowerShell'i **kapat, yeniden aç** (setx yalnız yeni pencerelerde
geçerli).

**Mac / Linux:**

```bash
export APIFOOTBALL_KEY="buraya_anahtarin"
python3 tahmin.py web
```

Kalıcı olması için `~/.zshrc` veya `~/.bashrc` dosyasının sonuna aynı
satırı ekle.

> **En kolayı:** proje klasöründeki `ayarlar.ornek.txt` dosyasını kopyalayıp
> adını `ayarlar.txt` yap, Not Defteri ile aç, ilgili satırın başındaki `#`
> işaretini sil ve anahtarını yaz. `BASLAT.bat` bu dosyayı otomatik okur —
> `setx` ile uğraşmana gerek kalmaz.
>
> Anahtarı panelden de kaydedebilirsin (⚙️ Ayarlar). O da `data/ayarlar.json`
> dosyasına yazılır ve kalıcıdır. Ortam değişkeni önceliklidir.

---

## 7. Paneli aç

```bash
python tahmin.py web
```

Tarayıcıda: **http://127.0.0.1:8000**

Kapatmak için terminalde `Ctrl + C`.

---

## 8. Çalıştığını doğrula

Panelde ⚙️ **Ayarlar** → **📊 Bülten kapsamı** kutusuna bak:

```
📄 football-data.co.uk (oranlı)   +26 maç
⚽ API-Football (dünya)          +180 maç
TOPLAM BÜLTEN                     206 maç
```

Anahtar sorunluysa **🔑 Anahtarı Test Et** düğmesi kaynağın kendi cevabını
gösterir: hesap aktif mi, kota ne kadar dolmuş, bugün kaç maç görüyor.

---

## Sık karşılaşılan sorunlar

| Belirti | Sebep / çözüm |
|---|---|
| `python: command not found` | PATH'e eklenmemiş. Python'u kaldırıp "Add to PATH" işaretli kur. |
| `SyntaxError: invalid syntax` | Python'un **içindesin** (`>>>` işareti). `exit()` yaz, PowerShell aç. Bkz. adım 1.5 |
| Komut yazınca **Not Defteri açılıyor** | Windows dosyayı çalıştırmak yerine açıyor. Komutun başında `py ` veya `python ` olmalı. Ya da `BASLAT.bat`'a çift tıkla. |
| "Bu dosyayı nasıl açmak istersiniz?" | Aynı sebep. `.py` dosyası çalıştırılmıyor, açılıyor. `py tahmin.py ...` şeklinde yaz. |
| `can't open file 'tahmin.py'` | Yanlış klasördesin. `dir tahmin.py` ile kontrol et, `cd` ile proje klasörüne gir. |
| `baglanti` → `❌ erişilemedi` | Türkiye engeli. VPN aç. |
| `guncelle` yarıda kesiliyor | Bağlantı koptu; komutu tekrar çalıştır — kaldığı yerden devam eder. |
| Panel açılıyor ama maç yok | Önce `guncelle` çalıştırdın mı? Sonra ⚙️ → 🔄 Kapsamı Yenile. |
| `Address already in use` | 8000 portu dolu. `python tahmin.py web --port 8090` |
| Anahtar askıya alındı | Aynı anahtarı iki ayrı yerden (sunucu + yerel) kullanma. Bir yerde kalsın. |

---

## Günlük kullanım

Kurulum bitti; her seferinde tek satır:

```bash
python tahmin.py web
```

Fikstür ve güncel sezon otomatik tazelenir, `guncelle`'yi tekrar
çalıştırmana gerek yok.
