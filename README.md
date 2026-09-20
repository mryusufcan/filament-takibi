# 🧵 Filament Takibi

3D yazıcı filament stoğunu takip etmek için basit bir web arayüzü. Kurulum, sunucu veya hesap gerektirmez. Tek bir `index.html` dosyasıdır.

**Canlı sayfa:** https://mryusufcan.github.io/filament-takibi/

## Özellikler

- **Makara takibi:** Marka, malzeme (PLA, PETG, ABS, ASA, TPU…), renk, filament ağırlığı, boş makara ağırlığı, fiyat ve not.
- **Hızlı kullanım kaydı:** Baskıdan sonra kullandığın gramı yaz veya +10, +25, +50, +100 g düğmelerine bas. Kalan miktar güncellenir.
- **Tartıdan güncelleme:** Makarayı tartıya koy, çıkan değeri yaz. Boş makara ağırlığı çıkarılarak kalan filament hesaplanır.
- **Azalan uyarısı:** Kalan %20'nin altına inince makara işaretlenir.
- **Sade görünüm:** Kartlarda sadece renk, kalan gram ve ilerleme çubuğu görünür. Ayrıntılar **İşlemler** düğmesiyle açılır. Grafikler ve geçmiş de açılır bölümlerdir. Yeni makara formunda sadece marka, malzeme ve renk zorunludur, diğer alanlar **Gelişmiş** başlığı altındadır.
- **Grafik özet:** Malzemeye ve renge göre toplam kalan filament çubuk grafiklerle görünür. Renk grafiği malzeme türüne göre filtrelenebilir (ör. sadece PLA renkleri).
- **Geçmiş:** Kullanım, tartıdan güncelleme, makara ekleme ve elle düzenleme kayıtları günlere göre listelenir (ör. "Dün · Siyah · −150 g"). Son 7 ve 30 günde kullanılan toplam gram da görünür.
- **Arama, filtre ve sıralama:** Marka, renk veya malzemeye göre bul.
- **CSV yedek:** Listeyi indir, gerektiğinde geri yükle. Excel'de açılır. Geçmiş kayıtları CSV'ye dahil değildir.
- **GitHub'a kayıt (isteğe bağlı):** Liste bu depodaki `data.json` dosyasına kaydedilir, böylece telefon ve bilgisayarda aynı liste görünür.
- **Telefon uyumlu**, açık ve koyu temaya uyum sağlar.

## Kullanım

1. Sayfayı aç ve **+ Yeni makara** ile ilk makaranı ekle. Marka, malzeme ve renk yeterli.
2. Baskıdan sonra makara kartında **İşlemler**'e bas, kullandığın gramı yaz ve **Kullandım**'a bas.
3. Emin olmak istersen makarayı tartıp toplam ağırlığı yaz ve **Tartıdan güncelle**'ye bas.
4. Sayfanın altındaki **Geçmiş** bölümünden hangi renkten ne zaman ne kadar kullandığını takip et.

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

Değişiklikler birkaç saniye içinde `data.json` dosyasına yazılır. Makaralar ve geçmiş kayıtları bu dosyada saklanır. Bir makara silinirse onun geçmişi de silinir. Her makara için son 100 kayıt tutulur.

## Gizlilik ve güvenlik

- Token yalnızca kullandığın tarayıcıda saklanır ve sadece seçtiğin depoya erişir.
- Depo **public** ise `data.json` dosyası herkes tarafından görülebilir. Listenin gizli kalması için veriyi ayrı bir **private** depoya kaydet.
- Düzenli olarak **⬇ Yedek indir (CSV)** ile yedek almanı öneririm.

## Teknik notlar

- Saf HTML, CSS ve JavaScript. Kütüphane veya derleme adımı yok.
- Veri `localStorage` içinde tutulur, GitHub bağlıysa ayrıca GitHub API üzerinden `data.json` dosyasına yazılır.
