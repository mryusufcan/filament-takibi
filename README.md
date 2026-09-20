# 🧵 Filament Takibi

3D yazıcı filament stoğunu takip etmek için basit bir web arayüzü. Kurulum, sunucu veya hesap gerektirmez. Tek bir `index.html` dosyasıdır.

**Canlı sayfa:** https://KULLANICI-ADIN.github.io/filament-takibi/

## Özellikler

- **Makara takibi:** Marka, malzeme (PLA, PETG, ABS, ASA, TPU…), renk, filament ağırlığı, boş makara ağırlığı, fiyat ve not.
- **Hızlı düşme:** Baskıdan sonra kullanılan gramı yaz, kalan miktar güncellenir.
- **Tartıdan hesaplama:** Makarayı tartıya koy, çıkan değeri yaz. Boş makara ağırlığı çıkarılarak kalan filament hesaplanır.
- **Azalan uyarısı:** Kalan %20'nin altına inince makara işaretlenir.
- **Grafik özet:** Malzemeye ve renge göre toplam kalan filament çubuk grafiklerle görünür.
- **Arama, filtre ve sıralama:** Marka, renk veya malzemeye göre bul.
- **CSV yedek:** Listeyi indir, gerektiğinde geri yükle. Excel'de açılır.
- **GitHub'a kayıt (isteğe bağlı):** Liste bu depodaki `data.json` dosyasına kaydedilir, böylece telefon ve bilgisayarda aynı liste görünür.
- **Telefon uyumlu**, açık ve koyu temaya uyum sağlar.

## Kullanım

1. Sayfayı aç ve **+ Yeni makara** ile ilk makaranı ekle.
2. Baskıdan sonra makara kartındaki **Düş** kutusuna kullanılan gramı yaz.
3. Emin olmak istersen **Tartı** kutusuna makara dahil ağırlığı yazıp **Hesapla**'ya bas.

Ek bir ayar yapmazsan veriler yalnızca kullandığın tarayıcıda saklanır.

## Kendi sitene kurulum

1. Bu depoyu fork'la veya `index.html` dosyasını kendi depona yükle.
2. **Settings → Pages** bölümünde **Deploy from a branch** seç, **main** ve **/(root)** ile kaydet.
3. Birkaç dakika sonra sayfa `https://KULLANICI-ADIN.github.io/DEPO-ADIN/` adresinde açılır.

## GitHub'a kayıt kurulumu

1. GitHub'da **Settings → Developer settings → Personal access tokens → Fine-grained tokens** yolundan yeni bir token oluştur.
2. **Repository access** için sadece bu depoyu seç.
3. **Repository permissions → Contents** için **Read and write** ver.
4. Sayfada **☁ GitHub'a bağla** düğmesine bas. Kullanıcı adını, depo adını ve tokeni gir.

Değişiklikler birkaç saniye içinde `data.json` dosyasına yazılır.

## Gizlilik ve güvenlik

- Token yalnızca kullandığın tarayıcıda saklanır ve sadece seçtiğin depoya erişir.
- Depo **public** ise `data.json` dosyası herkes tarafından görülebilir. Listenin gizli kalması için veriyi ayrı bir **private** depoya kaydet.
- Düzenli olarak **⬇ Yedek indir (CSV)** ile yedek almanı öneririm.

## Teknik notlar

- Saf HTML, CSS ve JavaScript. Kütüphane veya derleme adımı yok.
- Veri `localStorage` içinde tutulur, GitHub bağlıysa ayrıca GitHub API üzerinden `data.json` dosyasına yazılır.
