"""
MQTT Sensör Simülatörü
======================
Gerçek bir MQTT broker'a bağlanıp sensör verisi yayınlar.
FastAPI/main.py bu veriyi dinler → WebSocket → UI güncellenir.

Kurulum:
    pip install paho-mqtt

Önce MQTT broker başlat (Windows):
    # Docker varsa:
    docker run -it -p 1883:1883 eclipse-mosquitto

    # Ya da Mosquitto Windows kurulumu:
    # https://mosquitto.org/download/

Çalıştır:
    python mqtt_simulator.py
"""

import time
import random
import json
import math
import sys

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("HATA: pip install paho-mqtt")
    sys.exit(1)

BROKER = "localhost"
PORT   = 1883
TOPIC  = "factory/sensors/main"

# Simülasyon parametreleri
t = 0  # zaman sayacı

def generate_sensor_data(t: float) -> dict:
    """Gerçekçi sensör verisi üret — zaman bazlı sinüs + gürültü"""
    # Sıcaklık: 35°C taban + salınım + nadir spike
    base_temp = 35 + 15 * math.sin(t / 30)
    spike_temp = 45 if random.random() < 0.03 else 0
    temperature = round(base_temp + random.uniform(-2, 2) + spike_temp, 1)

    # Nem: %45 taban
    humidity = round(45 + 20 * math.sin(t / 50 + 1) + random.uniform(-3, 3), 1)

    # Gaz: normalde düşük, ara sıra yükseliyor
    gas_base = 80 + 30 * math.sin(t / 20)
    gas_spike = random.uniform(150, 350) if random.random() < 0.05 else 0
    gas = round(max(0, gas_base + random.uniform(-10, 10) + gas_spike), 0)

    # Titreşim: makine çalışıyorsa yüksek
    machine_on = math.sin(t / 10) > 0
    vibration = round(
        (8 + 4 * abs(math.sin(t / 5)) + random.uniform(-1, 1)) if machine_on
        else random.uniform(0.1, 0.5),
        2
    )

    # Duman: normalde 0, ara sıra artıyor
    smoke_spike = random.uniform(30, 80) if random.random() < 0.02 else 0
    smoke = round(max(0, 5 + random.uniform(-2, 2) + smoke_spike), 1)

    # Hareket: %20 ihtimalle var
    motion = 1 if random.random() < 0.20 else 0

    return {
        "device_id":   "sensor_main",
        "temperature": temperature,
        "humidity":    humidity,
        "gas":         int(gas),
        "vibration":   vibration,
        "smoke":       smoke,
        "motion":      motion,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Broker'a bağlandı: {BROKER}:{PORT}")
        print(f"[MQTT] Topic: {TOPIC}")
        print("[MQTT] Simülasyon başlıyor... (Ctrl+C ile dur)\n")
    else:
        print(f"[MQTT] Bağlantı hatası: {rc}")

def on_disconnect(client, userdata, rc):
    print(f"[MQTT] Bağlantı kesildi: {rc}")

client = mqtt.Client(client_id="factory_simulator")
client.on_connect    = on_connect
client.on_disconnect = on_disconnect

try:
    client.connect(BROKER, PORT, 60)
    client.loop_start()
except Exception as e:
    print(f"HATA: Broker'a bağlanılamadı ({BROKER}:{PORT})")
    print(f"Detay: {e}")
    print("\nMosquitto başlatmak için:")
    print("  docker run -it -p 1883:1883 eclipse-mosquitto")
    sys.exit(1)

try:
    while True:
        data = generate_sensor_data(t)
        payload = json.dumps(data)
        result = client.publish(TOPIC, payload)

        # Konsola yazdır
        status = "✓" if result.rc == 0 else "✗"
        print(
            f"{status} [{data['timestamp']}] "
            f"Sıcaklık:{data['temperature']}°C "
            f"Nem:{data['humidity']}% "
            f"Gaz:{data['gas']}ppm "
            f"Titreşim:{data['vibration']}mm/s "
            f"Duman:{data['smoke']}% "
            f"Hareket:{data['motion']}"
        )

        t += 1
        time.sleep(2)

except KeyboardInterrupt:
    print("\n[MQTT] Simülatör durduruldu.")
    client.loop_stop()
    client.disconnect()