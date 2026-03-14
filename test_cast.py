import requests

res = requests.post("http://localhost:8000/api/extract", json={"url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"})
data = res.json()
print("Extract Response:", data)
session_id = data.get("session_id")
if session_id:
    dev_res = requests.get("http://localhost:8000/api/devices")
    devices = dev_res.json().get("devices", [])
    print("Devices:", devices)
    
    # Target actual Apple TV
    target_dev = next((d for d in devices if 'TV' in d['name'] or 'Apple' in d['name'] or 'Master' in d['name']), None)
    
    if target_dev:
        dev_id = target_dev["identifier"]
        print(f"Casting to {target_dev['name']} ({dev_id})...")
        cast_res = requests.post("http://localhost:8000/api/cast", json={"session_id": session_id, "device_id": dev_id})
        print("Cast Response:", cast_res.json())
    else:
        print("No Apple TV found")
