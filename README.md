# 🧵 Filament Takibi

3D yazıcı filament stoğunu takip etmek için hazırlanmış, kurulumsuz ve hafif bir web uygulaması.

**Canlı kullanım:**  
https://mryusufcan.github.io/filament-takibi/

## Özellikler

- 🧵 **Makara takibi**
  - Marka
  - Malzeme: PLA, PLA+, PETG, ABS, ASA, TPU ve Diğer
  - Renk adı ve renk seçimi
  - Makaradaki filament miktarı
  - Kalan filament miktarı
  - Boş makara ağırlığı
  - Fiyat
  - Not

- ⚖️ **Tartı ile kalan miktarı hesaplama**
  - Makarayı toplam ağırlığıyla tart
  - Boş makara ağırlığını düşerek kalan filament miktarını otomatik hesapla

- ➖ **Hızlı filament kullanımı**
  - Kullanılan gramı manuel gir
  - +10 g, +25 g, +50 g ve +100 g hızlı düğmelerini kullan

- 📊 **Stok özeti**
  - Toplam makara sayısı
  - Toplam kalan filament
  - %20'nin altına düşen makaraların sayısı

- 📈 **Grafikler**
  - Malzemeye göre kalan filament
  - Renge göre kalan filament
  - Malzeme bazında filtreleme

- 🔎 **Arama, filtreleme ve sıralama**
  - Marka, renk, malzeme veya nota göre arama
  - Malzemeye göre filtre
  - Kalan miktara veya eklenme tarihine göre sıralama

- 🕒 **Geçmiş**
  - Makara ekleme
  - Filament kullanımı
  - Tartı ile güncelleme
  - Manuel düzenleme
  - Son 7 ve 30 günde kullanılan filament özeti

- 💾 **CSV yedekleme**
  - Filament listesini CSV olarak dışa aktar
  - CSV yedeğini tekrar içe aktar
  - Excel ile açılabilir

- 📱 **Mobil uyumlu**
  - Telefon, tablet ve masaüstünde kullanılabilir
  - Açık/koyu tema desteği

## Kullanım

1. **+ Yeni makara** butonuna bas.
2. Marka, malzeme ve renk bilgilerini gir.
3. Gerekirse gelişmiş bölümden filament ağırlığı, boş makara ağırlığı, fiyat ve not ekle.
4. Baskıdan sonra **İşlemler → Kullandım** bölümünden kullandığın gramı düş.
5. İstersen makarayı tartıp **Tartıdan güncelle** ile gerçek kalan miktarı hesaplat.
6. **Geçmiş** bölümünden yapılan işlemleri takip et.
7. Düzenli olarak **Yedek indir (CSV)** ile verilerini yedekle.

## Veri saklama

Uygulama harici bir sunucu veya veritabanı gerektirmez.

Veriler varsayılan olarak tarayıcının `localStorage` alanında tutulur. Bu nedenle:

- Veriler kullandığın tarayıcıya özeldir.
- Başka bir cihazda otomatik olarak görünmez.
- Tarayıcı verileri silinirse kayıtlar da kaybolabilir.
- Bu nedenle düzenli CSV yedeği alınması önerilir.

## GitHub Pages ile yayınlama

Bu proje tek bir `index.html` dosyasıyla çalışır.

1. Bu depoyu GitHub'a yükle veya forkla.
2. `index.html` dosyasının depo kök dizininde olduğundan emin ol.
3. GitHub'da **Settings → Pages** bölümünü aç.
4. **Deploy from a branch** seç.
5. Branch olarak `main`, klasör olarak `/(root)` seç.
6. Kaydet.

GitHub Pages etkinleştirildikten sonra uygulama şu yapıda yayınlanır:

`https://KULLANICI-ADIN.github.io/DEPO-ADIN/`

## Teknik yapı

- HTML5
- CSS3
- Vanilla JavaScript
- Harici JavaScript kütüphanesi yok
- Build/derleme adımı yok
- Backend yok
- Veriler `localStorage` ile saklanır
- CSV içe/dışa aktarma desteği bulunur

## Lisans

Bu proje kişisel kullanım amacıyla geliştirilmiştir. İstersen depoya ayrıca bir lisans dosyası ekleyebilirsin.
