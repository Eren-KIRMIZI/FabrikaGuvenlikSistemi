# Fabrika Güvenlik Sistemi

Endüstriyel tesisler için gerçek zamanlı video izleme, yapay zeka destekli yangın ve duman tespiti ile IoT sensör entegrasyonunu bir arada sunan açık kaynaklı bir güvenlik platformudur. Sistem, tarayıcı tabanlı çalışır; ek bir istemci uygulaması kurulumu gerektirmez.

---

https://github.com/user-attachments/assets/b1f598c0-0686-4f9d-abe6-aede1d4d9fcb


https://github.com/user-attachments/assets/3e63ad3e-cb87-445e-a7a1-1760855fcece

![test_fire_20260318_114206](https://github.com/user-attachments/assets/35125787-7390-45af-8db9-0fdb6365e7fe)

## Genel Bakış

Sistem üç temel bileşenden oluşur. Birincisi, WebRTC protokolü üzerinden kamera görüntülerini düşük gecikmeyle canlı olarak izleyen video akış katmanıdır. İkincisi, her kameranın RTSP akışını sürekli analiz ederek yangın ve duman tespiti yapan yapay zeka modülüdür. Üçüncüsü ise sıcaklık, nem, gaz, titreşim ve duman sensörlerinden gelen verileri MQTT protokolü üzerinden işleyen IoT entegrasyon katmanıdır.

Tespit edilen herhangi bir tehlike anında web arayüzüne, alarm günlüğüne ve bildirim sistemine iletilir. Tüm kamera kayıtları `.ts` formatında diske yazılır; isteğe bağlı olarak `.mp4` formatına dönüştürülebilir.

---

## Neden WebRTC

Endüstriyel güvenlik sistemlerinde gecikme doğrudan müdahale süresini etkiler. Yaygın olarak kullanılan HLS protokolü, video segmentlerini parçalara bölerek ilettiğinden tarayıcıda tipik olarak 3 ila 10 saniye gecikmeye yol açar. Bu süre, bir yangın veya güvenlik ihlali gibi anlık müdahale gerektiren durumlarda kabul edilebilir değildir.

WebRTC, tarayıcılar arasında doğrudan eşten eşe (peer-to-peer) bağlantı kurarak video ve sesi gerçek zamanlı iletir. Bu projede ölçülen gecikme değerleri tutarlı biçimde 200 milisaniyenin altında seyretmektedir; bu oran HLS ile kıyaslandığında 15 ila 50 kat daha hızlı anlamına gelir.

| Protokol | Tipik Gecikme | Kullanım Senaryosu |
|---|---|---|
| HLS (standart) | 6 – 30 saniye | Video yayını, içerik dağıtımı |
| Low Latency HLS | 2 – 5 saniye | Canlı yayın platformları |
| WebRTC (bu proje) | 100 – 300 ms | Güvenlik izleme, gerçek zamanlı kontrol |

WebRTC'nin bu projede tercih edilmesinin ikinci nedeni, ek sunucu yazılımı kurulumu gerektirmeksizin standart bir web tarayıcısından hem yayın yapılabilmesi hem de izlenebilmesidir. Kamera görevi gören cihaz WHIP protokolüyle MediaMTX'e stream gönderir; izleyici tarayıcı ise WHEP protokolüyle aynı stream'i doğrudan alır. Yayın ve izleme için herhangi bir ek uygulama yüklemesi gerekmez.

---

## Özellikler

- Gerçek zamanlı video akışı (WHIP/WHEP, ~200ms gecikme)
- Yangın ve duman tespiti (YOLOv8 tabanlı, Roboflow üzerinde çalışır)
- HLS çıkışı ve RTMP desteği
- Otomatik video kaydı ve MP4 dönüştürme
- IoT sensör izleme ve eşik tabanlı alarm üretimi
- MQTT entegrasyonu (gerçek sensörler veya simülatör)
- Tarayıcı tabanlı yönetim arayüzü
- WebSocket ile anlık bildirimler
- Çoklu kamera desteği

---

## Kullanılan Teknolojiler

### Arka Uç

| Teknoloji | Sürüm | Kullanım Amacı |
|---|---|---|
| Python | 3.11 | Ana uygulama dili |
| FastAPI | 0.115 | REST API ve WebSocket sunucusu |
| Uvicorn | 0.30 | ASGI web sunucusu |
| httpx | 0.27 | MediaMTX API istemcisi |
| paho-mqtt | 2.1 | MQTT broker bağlantısı |
| PyAV | 13.1 | TS dosyalarını MP4'e dönüştürme |

### Video Altyapısı

| Teknoloji | Sürüm | Kullanım Amacı |
|---|---|---|
| MediaMTX | v1.17.0 | WebRTC, RTSP, HLS, RTMP sunucusu |
| WebRTC (WHIP/WHEP) | — | Düşük gecikmeli tarayıcı yayını ve izleme |
| HLS (Low Latency) | — | Geniş uyumlu ikincil protokol |

### Yapay Zeka

| Teknoloji | Kullanım Amacı |
|---|---|
| Roboflow Serverless API | Bulut tabanlı model çalıştırma |
| YOLOv8 | Nesne tespit mimarisi |
| fire-b4gzb/2 | Yangın ve duman tespiti için eğitilmiş model |
| OpenCV | RTSP akışından frame okuma ve bounding box çizimi |

Kullanılan model: [Roboflow — fire-b4gzb v2](https://universe.roboflow.com/studentwork/fire-b4gzb/model/2)

### Ön Yüz

| Teknoloji | Kullanım Amacı |
|---|---|
| HTML / CSS / JavaScript | Tek dosya tarayıcı arayüzü |
| WebRTC API | Tarayıcıdan yayın ve izleme |
| HLS.js | Tarayıcıda HLS oynatma |
| WebSocket | Anlık alarm ve kamera durum güncellemeleri |

---

## Sistem Gereksinimleri

- Python 3.11 veya üzeri
- Windows 10/11, macOS veya Linux
- MediaMTX v1.17.0 (bağımsız binary, kurulum gerektirmez)
- MQTT broker (isteğe bağlı; simülatör ile test edilebilir)
- Roboflow API anahtarı (yangın tespiti için)

---

## Kurulum

### 1. Bağımlılıkları Yükle

```bash
pip install fastapi uvicorn httpx python-multipart aiofiles av paho-mqtt inference-sdk opencv-python-headless pillow
```

### 2. MediaMTX İndir

[MediaMTX v1.17.0](https://github.com/bluenviron/mediamtx/releases/tag/v1.17.0) sayfasından işletim sisteminize uygun binary dosyasını indirip proje klasörüne çıkartın.

### 3. Sistemi Başlat

```bash
uvicorn main:app --port 8000
```

Uvicorn başladığında MediaMTX, MQTT simülatörü ve AI dedektör otomatik olarak ayrı pencereler açılarak başlatılır.

Tarayıcıda `http://localhost:8000` adresini açın.

---

## Proje Yapısı

```
fab/
├── main.py               # FastAPI uygulaması, tüm API endpoint'leri
├── ai_detector.py        # Roboflow tabanlı yangın/duman tespit servisi
├── mqtt_simulator.py     # Gerçek MQTT broker olmadan sensör simülasyonu
├── mediamtx.yml          # MediaMTX yapılandırması
├── mediamtx.exe          # MediaMTX binary (Windows)
├── requirements.txt
├── recordings/           # Otomatik video kayıtları (.ts)
├── snapshots/            # Tespit anlarında alınan ekran görüntüleri
└── static/
    └── index.html        # Tarayıcı arayüzü
```

---

## Portlar

| Port | Servis |
|---|---|
| 8000 | FastAPI — ana uygulama ve arayüz |
| 8554 | RTSP — kamera akışları (AI dedektör bağlantısı) |
| 8888 | HLS — tarayıcı uyumlu video çıkışı |
| 8889 | WebRTC WHIP/WHEP — düşük gecikmeli yayın |
| 1935 | RTMP — harici kaynak girişi |
| 9997 | MediaMTX yönetim API'si |
| 1883 | MQTT broker |

---

## Yapay Zeka Modülü

AI dedektör her kameranın RTSP akışını sürekli okur. Her altı frame'de bir Roboflow API'sine gönderilen frame analiz edilir. Güven skoru yüzde 35'in üzerinde olan tespitler değerlendirmeye alınır. Aynı sınıf iki ardışık frame'de tespit edilirse alarm üretilir. Aynı kamera ve alarm seviyesi için minimum 15 saniyelik bekleme süresi uygulanır.

Tespit edildiğinde bounding box çizilmiş frame `snapshots/` klasörüne kaydedilir ve alarm FastAPI aracılığıyla WebSocket üzerinden arayüze iletilir.

---

## IoT Entegrasyonu

Gerçek bir MQTT broker ve sensör donanımı yoksa `mqtt_simulator.py` betiği sinüs fonksiyonu tabanlı gerçekçi sensör verisi üreterek sistemi test etmek için kullanılabilir.

Gerçek entegrasyon için herhangi bir MQTT istemcisinin `factory/sensors/#` topiğine aşağıdaki formatta JSON mesaj göndermesi yeterlidir:

```json
{
  "device_id": "sensor_1",
  "temperature": 45.2,
  "humidity": 60.1,
  "gas": 120,
  "vibration": 3.4,
  "smoke": 8.0,
  "motion": 0
}
```

---

## Lisans

MIT
