# ============================================================
# Fabrika Güvenlik Sistemi — FastAPI Backend
# pip install fastapi uvicorn httpx python-multipart aiofiles av paho-mqtt
# Çalıştır: uvicorn main:app --reload --port 8000
# ============================================================

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import httpx
import asyncio
import json
import os
import glob
import shutil
import threading
import subprocess
import sys

# ── Opsiyonel kütüphaneler ────────────────────────────────
try:
    import av
    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False

try:
    import paho.mqtt.client as mqtt_lib
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

# ── Sabitler (import'lardan hemen sonra, her şeyden önce) ─
MEDIAMTX_API  = "http://localhost:9997"
RECORDINGS_DIR = "./recordings"
MQTT_BROKER   = "localhost"
MQTT_PORT     = 1883
MQTT_TOPIC    = "factory/sensors/#"

# ── Subprocess süreçleri ─────────────────────────────────
_processes: list = []   # başlatılan süreçler (shutdown'da kapatılır)

# ── Klasörleri oluştur ────────────────────────────────────
os.makedirs("static",         exist_ok=True)
os.makedirs(RECORDINGS_DIR,   exist_ok=True)

# index.html varsa static'e kopyala
for candidate in ["index.html", "../index.html"]:
    if os.path.exists(candidate) and not os.path.exists("static/index.html"):
        shutil.copy(candidate, "static/index.html")
        break

# ── FastAPI ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────
    t = threading.Thread(target=start_mqtt, daemon=True)
    t.start()
    await launch_services()
    yield
    # ── Shutdown ─────────────────────────────────────────
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    for proc in _processes:
        try:
            proc.terminate()
            print(f"[Launcher] Kapatıldı: PID {proc.pid}")
        except Exception:
            pass

app = FastAPI(title="Fabrika Güvenlik Sistemi", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── In-memory state ───────────────────────────────────────
cameras_db: dict = {
    "kamera1": {"id": "kamera1", "name": "Üretim Hattı A", "location": "Bölge 1", "active": False},
    "kamera2": {"id": "kamera2", "name": "Depo Girişi",    "location": "Bölge 2", "active": False},
    "test":    {"id": "test",    "name": "Test Kamerası",  "location": "Laptop",  "active": False},
}

alarms_db:          list = []
active_connections: list = []   # WebSocket bağlantıları
convert_jobs:       dict = {}   # "camId/file" -> status
sensor_data:        dict = {}   # son sensör değerleri (MQTT'den gelir)
ai_detections:      dict = {}   # camId -> son AI tespitleri
mqtt_client               = None

# ── Modeller ──────────────────────────────────────────────
class CameraUpdate(BaseModel):
    name:     Optional[str] = None
    location: Optional[str] = None

class AlarmCreate(BaseModel):
    camera_id: str
    level:     str    # info | warning | danger
    message:   str

# ── Yardımcı: alarm oluştur ve yayınla ───────────────────
async def push_alarm(camera_id: str, level: str, message: str):
    entry = {
        "id":           len(alarms_db) + 1,
        "camera_id":    camera_id,
        "camera_name":  cameras_db.get(camera_id, {}).get("name", camera_id),
        "level":        level,
        "message":      message,
        "timestamp":    datetime.now().isoformat(),
        "acknowledged": False,
    }
    alarms_db.append(entry)
    await broadcast_ws({"type": "alarm", **entry})
    return entry

# ── WebSocket broadcast ───────────────────────────────────
async def broadcast_ws(data: dict):
    dead = []
    for ws in list(active_connections):   # kopya üzerinde iterate et
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in active_connections:      # thread-safe remove
            active_connections.remove(ws)

# ── MQTT ─────────────────────────────────────────────────
def on_mqtt_message(client, userdata, msg):
    """MQTT mesajı geldiğinde çalışır — thread'den asyncio'ya köprü"""
    try:
        data = json.loads(msg.payload.decode())
    except Exception:
        return

    sensor_data.update(data)
    device_id = data.get("device_id", "sensor_main")

    # Alarm eşikleri
    alerts = []
    temp = data.get("temperature", 0)
    gas  = data.get("gas", 0)
    hum  = data.get("humidity", 0)
    vib  = data.get("vibration", 0)
    smk  = data.get("smoke", 0)

    if temp >= 80:   alerts.append(("danger",  f"🌡️ KRİTİK sıcaklık: {temp:.1f}°C — {device_id}"))
    elif temp >= 70: alerts.append(("warning", f"🌡️ Yüksek sıcaklık: {temp:.1f}°C — {device_id}"))
    if gas  >= 400:  alerts.append(("danger",  f"☁️ KRİTİK gaz: {gas:.0f}ppm — {device_id}"))
    elif gas >= 200: alerts.append(("warning", f"☁️ Yüksek gaz: {gas:.0f}ppm — {device_id}"))
    if smk  >= 70:   alerts.append(("danger",  f"🔥 KRİTİK duman: {smk:.1f}% — {device_id}"))
    elif smk >= 40:  alerts.append(("warning", f"🔥 Duman algılandı: {smk:.1f}% — {device_id}"))
    if vib  >= 17:   alerts.append(("danger",  f"📳 Aşırı titreşim: {vib:.1f}mm/s — {device_id}"))
    if data.get("motion") == 1:
        alerts.append(("info", f"🚨 Hareket algılandı — {device_id}"))

    # asyncio event loop'a görev ekle (thread-safe)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Sensör verisini UI'a gönder
        asyncio.run_coroutine_threadsafe(
            broadcast_ws({"type": "iot_data", "data": data, "device_id": device_id}),
            loop
        )
        # Alarmları gönder
        for level, message in alerts:
            cam_id = "kamera1" if temp > 80 else "iot"
            asyncio.run_coroutine_threadsafe(
                push_alarm(cam_id, level, message),
                loop
            )

def start_mqtt():
    global mqtt_client
    if not MQTT_AVAILABLE:
        print("[MQTT] paho-mqtt kurulu değil: pip install paho-mqtt")
        return
    try:
        mqtt_client = mqtt_lib.Client(client_id="factory_security")
        mqtt_client.on_message = on_mqtt_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.subscribe(MQTT_TOPIC)
        mqtt_client.loop_start()
        print(f"[MQTT] Bağlandı: {MQTT_BROKER}:{MQTT_PORT} → {MQTT_TOPIC}")
    except Exception as e:
        print(f"[MQTT] Bağlantı başarısız (broker çalışmıyor olabilir): {e}")

# ── Startup / Shutdown ────────────────────────────────────
# startup → lifespan ile yönetiliyor

async def launch_services():
    """Tüm servisleri sırayla başlat"""
    base = os.path.dirname(os.path.abspath(__file__))
    py   = sys.executable  # Aktif Python yorumlayıcısı

    services = [
        {
            "name":    "MediaMTX",
            "cmd":     [os.path.join(base, "mediamtx.exe"), os.path.join(base, "mediamtx.yml")],
            "delay":   2,
            "windows": True,   # Sadece Windows
        },
        {
            "name":    "MQTT Simülatör",
            "cmd":     [py, os.path.join(base, "mqtt_simulator.py")],
            "delay":   3,
            "windows": False,  # Her platformda
        },
        {
            "name":    "AI Dedektör",
            "cmd":     [py, os.path.join(base, "ai_detector.py")],
            "delay":   2,
            "windows": False,
        },
    ]

    for svc in services:
        # Windows kontrolü
        if svc["windows"] and sys.platform != "win32":
            print(f"[Launcher] {svc['name']} atlandı (Windows değil)")
            continue

        cmd = svc["cmd"]

        # Dosya var mı kontrol et
        if not os.path.exists(cmd[0]):
            print(f"[Launcher] {svc['name']} bulunamadı: {cmd[0]}")
            continue

        try:
            # Windows'ta yeni pencerede aç
            if sys.platform == "win32":
                proc = subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=base,
                )
            else:
                proc = subprocess.Popen(cmd, cwd=base)

            _processes.append(proc)
            print(f"[Launcher] ✓ {svc['name']} başlatıldı (PID: {proc.pid})")
        except Exception as e:
            print(f"[Launcher] ✗ {svc['name']} başlatılamadı: {e}")

        # Sıradaki servis için bekle
        await asyncio.sleep(svc["delay"])

# shutdown → lifespan ile yönetiliyor

# ── Frontend ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    for p in ["static/index.html", "index.html"]:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return HTMLResponse("<h2>index.html bulunamadı</h2>", status_code=404)

# ── Kamera CRUD ───────────────────────────────────────────
@app.get("/api/cameras")
async def list_cameras():
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            r = await client.get(f"{MEDIAMTX_API}/v3/paths/list")
            mtx_paths = {p["name"]: p for p in r.json().get("items", [])}
        except Exception:
            mtx_paths = {}

    result = []
    for cam_id, cam in cameras_db.items():
        path_info = mtx_paths.get(cam_id, {})
        source    = path_info.get("source") or {}
        result.append({
            **cam,
            "streaming": source.get("type") is not None,
            "readers":   len(path_info.get("readers") or []),
            "hls_url":   f"http://localhost:8888/{cam_id}/index.m3u8",
            "whip_url":  f"http://localhost:8889/{cam_id}/whip",
            "whep_url":  f"http://localhost:8889/{cam_id}/whep",
            "rtmp_url":  f"rtmp://localhost:1935/{cam_id}",
        })
    return result

@app.patch("/api/cameras/{camera_id}")
async def update_camera(camera_id: str, data: CameraUpdate):
    if camera_id not in cameras_db:
        raise HTTPException(404, "Kamera bulunamadı")
    if data.name:     cameras_db[camera_id]["name"]     = data.name
    if data.location: cameras_db[camera_id]["location"] = data.location
    return cameras_db[camera_id]

# ── MediaMTX Proxy ────────────────────────────────────────
@app.get("/api/streams/status")
async def streams_status():
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            r = await client.get(f"{MEDIAMTX_API}/v3/paths/list")
            return r.json()
        except Exception as e:
            raise HTTPException(503, f"MediaMTX erişilemiyor: {e}")

# ── Kayıtlar ──────────────────────────────────────────────
@app.get("/api/recordings")
async def list_recordings(camera_id: Optional[str] = None):
    recordings = []
    pattern    = f"{RECORDINGS_DIR}/{camera_id}/*" if camera_id else f"{RECORDINGS_DIR}/**/*"
    extensions = (".ts", ".mp4", ".m4v")

    for path in glob.glob(pattern, recursive=True):
        if not path.endswith(extensions):
            continue
        # Windows / Linux uyumlu path ayrıştırma
        norm  = path.replace("\\", "/")
        parts = norm.split("/")
        cam   = parts[-2] if len(parts) >= 2 else "unknown"
        stat  = os.stat(path)
        recordings.append({
            "file":         os.path.basename(path),
            "camera_id":    cam,
            "camera_name":  cameras_db.get(cam, {}).get("name", cam),
            "size_mb":      round(stat.st_size / 1024 / 1024, 2),
            "created":      datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "download_url": f"/api/recordings/download/{cam}/{os.path.basename(path)}",
        })

    recordings.sort(key=lambda x: x["created"], reverse=True)
    return recordings

@app.get("/api/recordings/download/{camera_id}/{filename}")
async def download_recording(camera_id: str, filename: str):
    path = f"{RECORDINGS_DIR}/{camera_id}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404, "Dosya bulunamadı")
    media = "video/MP2T" if filename.endswith(".ts") else "video/mp4"
    return FileResponse(path, media_type=media, filename=filename)

@app.delete("/api/recordings/{camera_id}/{filename}")
async def delete_recording(camera_id: str, filename: str):
    path = f"{RECORDINGS_DIR}/{camera_id}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404, "Dosya bulunamadı")
    os.remove(path)
    return {"status": "deleted"}

# ── TS → MP4 Dönüşüm (PyAV) ──────────────────────────────
# BUG FIX: /convert/ endpoint'i /download/ ile çakışıyordu.
# Çözüm: endpoint sırası önemli — download önce tanımlanmalı.

@app.post("/api/recordings/convert/{camera_id}/{filename}")
async def convert_to_mp4(camera_id: str, filename: str):
    if not AV_AVAILABLE:
        raise HTTPException(501, "PyAV kurulu değil: pip install av")

    src = f"{RECORDINGS_DIR}/{camera_id}/{filename}"
    if not os.path.exists(src):
        raise HTTPException(404, "Kayıt dosyası bulunamadı")
    if not filename.endswith(".ts"):
        raise HTTPException(400, "Sadece .ts dosyaları dönüştürülebilir")

    dst     = src.replace(".ts", ".mp4")
    job_key = f"{camera_id}/{filename}"

    if convert_jobs.get(job_key) == "running":
        return {"status": "already_running", "output": os.path.basename(dst)}

    convert_jobs[job_key] = "running"

    def do_convert():
        try:
            inp = av.open(src)
            out = av.open(dst, "w", format="mp4")
            stream_map = {}

            for s in inp.streams:
                if s.type == "video":
                    # Codec adını al, yoksa codec_name kullan
                    codec = s.codec_context.name if hasattr(s, "codec_context") else "h264"
                    out_s = out.add_stream(codec, rate=s.average_rate or 30)
                    out_s.width   = s.codec_context.width
                    out_s.height  = s.codec_context.height
                    out_s.pix_fmt = s.codec_context.pix_fmt or "yuv420p"
                    stream_map[s.index] = out_s
                elif s.type == "audio":
                    try:
                        # Önce template ile dene (yeni PyAV)
                        out_s = out.add_stream(template=s)
                    except TypeError:
                        # Eski PyAV — codec adıyla ekle
                        acodec = s.codec_context.name if hasattr(s, "codec_context") else "aac"
                        out_s = out.add_stream(acodec)
                    stream_map[s.index] = out_s

            # Packet kopyalama — remux (re-encode YOK, hızlı)
            active = [s for s in inp.streams if s.type in ("video", "audio")]
            for pkt in inp.demux(*active):
                if pkt.dts is None:
                    continue
                if pkt.stream.index not in stream_map:
                    continue
                pkt.stream = stream_map[pkt.stream.index]
                out.mux(pkt)

            inp.close()
            out.close()
            convert_jobs[job_key] = "done"
        except Exception as e:
            convert_jobs[job_key] = f"error: {e}"
            try:
                inp.close()
            except Exception:
                pass
            try:
                out.close()
            except Exception:
                pass

    threading.Thread(target=do_convert, daemon=True).start()
    return {"status": "started", "output": os.path.basename(dst), "job": job_key}

@app.get("/api/recordings/convert-status/{camera_id}/{filename}")
async def convert_status(camera_id: str, filename: str):
    return {"status": convert_jobs.get(f"{camera_id}/{filename}", "not_started")}

# ── Alarm API ─────────────────────────────────────────────
@app.get("/api/alarms")
async def list_alarms(limit: int = 50):
    return alarms_db[-limit:][::-1]

@app.post("/api/alarms")
async def create_alarm(alarm: AlarmCreate):
    return await push_alarm(alarm.camera_id, alarm.level, alarm.message)

@app.patch("/api/alarms/{alarm_id}/ack")
async def acknowledge_alarm(alarm_id: int):
    for alarm in alarms_db:
        if alarm["id"] == alarm_id:
            alarm["acknowledged"] = True
            return alarm
    raise HTTPException(404, "Alarm bulunamadı")

# ── MQTT Sensör durumu ────────────────────────────────────
@app.get("/api/sensors")
async def get_sensors():
    """Son MQTT sensör değerlerini döndür"""
    return {
        "mqtt_available":  MQTT_AVAILABLE,
        "mqtt_connected":  mqtt_client is not None and mqtt_client.is_connected() if mqtt_client else False,
        "broker":          f"{MQTT_BROKER}:{MQTT_PORT}",
        "topic":           MQTT_TOPIC,
        "last_data":       sensor_data,
        "timestamp":       datetime.now().isoformat(),
    }

# ── WebSocket ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_connections.append(ws)
    try:
        cams = await list_cameras()
        await ws.send_json({"type": "cameras_update", "cameras": cams})
        while True:
            await asyncio.sleep(3)
            cams = await list_cameras()
            await ws.send_json({"type": "cameras_update", "cameras": cams})
    except WebSocketDisconnect:
        if ws in active_connections:
            active_connections.remove(ws)
    except Exception:
        if ws in active_connections:
            active_connections.remove(ws)

# ── AI Tespitleri ────────────────────────────────────────
@app.post("/api/ai/detection")
async def receive_detection(data: dict):
    """ai_detector.py buraya POST atar — UI'a WebSocket ile iletilir"""
    cam_id = data.get("camera_id", "unknown")
    ai_detections[cam_id] = data
    # WebSocket üzerinden UI'a anlık gönder
    await broadcast_ws({"type": "ai_detection", **data})
    return {"status": "ok"}

@app.get("/api/ai/detections")
async def get_detections():
    return ai_detections

# ── Sağlık ────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    async with httpx.AsyncClient(timeout=2) as client:
        try:
            await client.get(f"{MEDIAMTX_API}/v3/paths/list")
            mediamtx_ok = True
        except Exception:
            mediamtx_ok = False
    return {
        "status":          "ok",
        "mediamtx":        "up" if mediamtx_ok else "down",
        "mqtt":            "up" if (mqtt_client and mqtt_client.is_connected()) else "down",
        "mqtt_available":  MQTT_AVAILABLE,
        "av_available":    AV_AVAILABLE,
        "cameras":         len(cameras_db),
        "active_alarms":   sum(1 for a in alarms_db if not a["acknowledged"]),
        "timestamp":       datetime.now().isoformat(),
    }