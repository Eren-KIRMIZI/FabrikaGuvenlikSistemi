"""
Fabrika Güvenlik — AI Yangın & Duman Tespiti
Roboflow Serverless API
"""
import cv2, time, threading, requests, numpy as np, sys
from datetime import datetime
from pathlib import Path

try:
    from inference_sdk import InferenceHTTPClient
except ImportError:
    print("HATA: pip install inference-sdk"); sys.exit(1)

# ── Ayarlar ───────────────────────────────────────────────
ROBOFLOW_API_KEY = "XZzYthFm5rJtxptcPkP8"
ROBOFLOW_MODEL   = "fire-b4gzb/2"
FASTAPI_URL      = "http://localhost:8000/api"
HLS_BASE         = "http://localhost:8888"
FRAME_SKIP       = 6       # Her N. frame analiz et
CONF_THRESH      = 0.35    # Güven eşiği — ateş için düşük tut
MIN_FRAMES       = 2       # Kaç frame üst üste → alarm
ALARM_COOLDOWN   = 15      # Saniye
SNAPSHOT_DIR     = "./snapshots"

# RTSP — cv2.VideoCapture için en güvenilir protokol
RTSP_BASE = "rtsp://localhost:8554"
CAMERAS = {
    "kamera1": f"{RTSP_BASE}/kamera1",
    "kamera2": f"{RTSP_BASE}/kamera2",
    "test":    f"{RTSP_BASE}/test",
}

Path(SNAPSHOT_DIR).mkdir(exist_ok=True)

CLIENT = InferenceHTTPClient(
    api_url="https://serverless.roboflow.com",
    api_key=ROBOFLOW_API_KEY,
)

CLASS_MAP = {
    "fire":  ("danger",  "🔥 YANGIN TESPİT EDİLDİ"),
    "Fire":  ("danger",  "🔥 YANGIN TESPİT EDİLDİ"),
    "smoke": ("warning", "💨 DUMAN TESPİT EDİLDİ"),
    "Smoke": ("warning", "💨 DUMAN TESPİT EDİLDİ"),
    "flame": ("danger",  "🔥 ALEV TESPİT EDİLDİ"),
    "Flame": ("danger",  "🔥 ALEV TESPİT EDİLDİ"),
}

def get_alarm(cls, conf):
    e = CLASS_MAP.get(cls) or CLASS_MAP.get(cls.lower() if cls else "")
    if e:
        return e[0], f"{e[1]} (%{int(conf*100)})"
    return None, None

last_alarm = {}

def send_alarm(cam, level, message, snapshot=None):
    now = time.time()
    key = f"{cam}_{level}"
    if now - last_alarm.get(key, 0) < ALARM_COOLDOWN:
        return
    last_alarm[key] = now
    msg = f"{message} | {cam} | {datetime.now().strftime('%H:%M:%S')}"
    if snapshot: msg += f" | 📸snap"
    try:
        requests.post(f"{FASTAPI_URL}/alarms",
            json={"camera_id": cam, "level": level, "message": msg}, timeout=3)
        icon = "🚨" if level=="danger" else "⚠️"
        print(f"{icon} [{cam}] {msg}")
    except Exception as e:
        print(f"[ALARM ERR] {e}")

def send_detection_to_ui(cam_id, preds, annotated_frame):
    """Sadece predictions gönder — frame YOK (donma önlendi)"""
    if not preds:
        return  # Tespit yoksa gönderme — bant genişliği tasarrufu
    try:
        payload = {
            "camera_id":   cam_id,
            "predictions": preds,
            "timestamp":   datetime.now().isoformat(),
            "has_fire":    any(p.get("class","").lower() in ("fire","flame") for p in preds),
            "has_smoke":   any(p.get("class","").lower() == "smoke" for p in preds),
        }
        requests.post(f"{FASTAPI_URL}/ai/detection", json=payload, timeout=3)
    except Exception as e:
        print(f"[UI SEND ERR] {e}")

def draw_boxes(frame, preds):
    out = frame.copy()
    for p in preds:
        cls  = p.get("class", "?")
        conf = p.get("confidence", 0)
        x  = int(p["x"] - p["width"]  / 2)
        y  = int(p["y"] - p["height"] / 2)
        w  = int(p["width"])
        h  = int(p["height"])
        level, _ = get_alarm(cls, conf)
        color = (0,0,255) if level=="danger" else (0,140,255)

        # Kutu
        cv2.rectangle(out, (x,y), (x+w,y+h), color, 3)

        # Etiket arka planı
        label = f"{cls} %{int(conf*100)}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(out, (x, y-th-10), (x+tw+8, y), color, -1)
        cv2.putText(out, label, (x+4, y-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

    # Uyarı banner
    if preds:
        has_fire = any(p.get("class","").lower() in ("fire","flame") for p in preds)
        banner   = "🔥 YANGIN ALGILANDI!" if has_fire else "💨 DUMAN ALGILANDI!"
        color    = (0,0,255) if has_fire else (0,140,255)
        cv2.rectangle(out, (0,0), (out.shape[1], 44), color, -1)
        cv2.putText(out, banner, (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,255,255), 2)
    return out

def save_snapshot(frame, cam, label):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{SNAPSHOT_DIR}/{cam}_{label}_{ts}.jpg"
    cv2.imwrite(path, frame)
    return path

def run_inference(frame):
    try:
        result = CLIENT.infer(frame, model_id=ROBOFLOW_MODEL)
        return [p for p in result.get("predictions", []) if p.get("confidence",0) >= CONF_THRESH]
    except Exception as e:
        print(f"[Roboflow ERR] {e}")
        return []

def camera_worker(cam_id, stream_url):
    consecutive = {}
    frame_count = 0
    print(f"[{cam_id}] Başlatılıyor...")

    while True:
        # RTSP için OpenCV backend ayarları
        cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)        # Buffer minimize — düşük latency
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)

        if not cap.isOpened():
            print(f"[{cam_id}] Stream açılamadı — yayın aktif mi? 10s bekleniyor")
            print(f"[{cam_id}] URL: {stream_url}")
            time.sleep(10)
            continue
        print(f"[{cam_id}] ✓ RTSP bağlandı → {stream_url}")

        while True:
            ret, frame = cap.read()
            if not ret:
                print(f"[{cam_id}] Frame yok — yeniden bağlanıyor")
                break

            frame_count += 1
            if frame_count % FRAME_SKIP != 0:
                continue

            preds = run_inference(frame)

            # Her durumda UI'a gönder (tespit var/yok)
            annotated = draw_boxes(frame, preds) if preds else frame
            threading.Thread(
                target=send_detection_to_ui,
                args=(cam_id, preds, annotated),
                daemon=True
            ).start()

            # Tespit yoksa consecutive sıfırla
            detected_now = set()
            for p in preds:
                cls  = p.get("class", "")
                conf = p.get("confidence", 0)
                level, _ = get_alarm(cls, conf)
                if level:
                    detected_now.add(cls)
                    print(f"[{cam_id}] 👁 {cls} %{int(conf*100)}")

            for cls in list(consecutive.keys()):
                if cls not in detected_now:
                    consecutive[cls] = 0

            for cls in detected_now:
                consecutive[cls] = consecutive.get(cls, 0) + 1
                if consecutive[cls] >= MIN_FRAMES:
                    level, message = get_alarm(cls, 0.9)
                    snapshot = save_snapshot(annotated, cam_id, cls)
                    send_alarm(cam_id, level, message, snapshot)
                    consecutive[cls] = 0

        cap.release()
        time.sleep(5)

def test_roboflow():
    print("[Roboflow] Test ediliyor...")
    try:
        CLIENT.infer(np.zeros((100,100,3), dtype=np.uint8), model_id=ROBOFLOW_MODEL)
        print(f"[Roboflow] ✓ OK | {ROBOFLOW_MODEL}")
        return True
    except Exception as e:
        print(f"[Roboflow] ✗ {e}")
        return False

def main():
    print("="*50)
    print("  Fabrika AI — Yangın & Duman Tespit")
    print(f"  Eşik: %{int(CONF_THRESH*100)} | Min frame: {MIN_FRAMES}")
    print("="*50)

    if not test_roboflow():
        print("API hatası — Enter ile devam")
        try: input()
        except KeyboardInterrupt: return

    for cam_id, url in CAMERAS.items():
        threading.Thread(target=camera_worker, args=(cam_id,url), daemon=True).start()
        print(f"[AI] ✓ {cam_id} izleniyor")

    print(f"\n{len(CAMERAS)} kamera aktif. Ctrl+C ile dur.\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n[AI] Durduruldu.")

# ── Test modu ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",  action="store_true")
    parser.add_argument("--image", type=str)
    args, _ = parser.parse_known_args()

    if args.test or args.image:
        print("="*50 + "\n  AI TEST MODU\n" + "="*50)
        if not test_roboflow(): sys.exit(1)

        if args.image:
            frame = cv2.imread(args.image)
        else:
            print("[Test] Ateş resmi indiriliyor...")
            import urllib.request
            try:
                urllib.request.urlretrieve(
                    "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/Campfire.jpg/320px-Campfire.jpg",
                    "test_fire.jpg"
                )
                frame = cv2.imread("test_fire.jpg")
                print("[Test] ✓ test_fire.jpg hazır")
            except Exception as e:
                print(f"[Test] İndirme hatası: {e}"); sys.exit(1)

        preds = run_inference(frame)
        if not preds:
            print("[Test] Tespit yok — CONF_THRESH düşür veya farklı resim dene")
        else:
            print(f"\n{len(preds)} nesne tespit edildi:\n")
            for p in preds:
                cls, conf = p.get("class","?"), p.get("confidence",0)
                level, msg = get_alarm(cls, conf)
                print(f"  {'🔥' if level=='danger' else '⚠️'}  {cls:12} %{int(conf*100):3}  →  {msg or '—'}")
            annotated = draw_boxes(frame, preds)
            cv2.imwrite("test_result.jpg", annotated)
            print("\n✓ test_result.jpg kaydedildi — aç ve bounding box'ları gör!")
    else:
        main()