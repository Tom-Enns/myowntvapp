# MyOwnTVApp

A self-hosted web app for watching live sports and TV streams. Built with FastAPI, it scrapes live event listings, proxies HLS streams, and supports AirPlay casting to Apple TV.

**This is an experimental/personal project.** It was built for learning and personal use. No guarantees of reliability, legality in your jurisdiction, or continued maintenance.

## Features

- Live sports: NBA, MLB, NHL, NFL, NCAAF, NCAAB, Soccer, PPV
- Live TV channels
- HLS stream proxying with playlist rewriting
- Team logos fetched from TheSportsDB
- AirPlay casting to Apple TV (via pyatv)
- Mobile-responsive UI with native iOS AirPlay support
- ffmpeg-based stream remuxing for Apple TV compatibility
- Status log showing real-time progress during stream loading

## Screenshots

The app displays a dark, card-based UI with team logos and game times. On mobile, categories scroll horizontally and the video player fills the screen with native AirPlay controls.

---

## Quick Start (Docker)

### Option 1: Docker Compose (Recommended)

1. Clone the repo:
   ```bash
   git clone https://github.com/Tom-Enns/myowntvapp.git
   cd myowntvapp
   ```

2. Start the app:
   ```bash
   docker compose up -d
   ```

3. Open `http://<your-server-ip>:1919` in your browser.

### Option 2: Docker Run

```bash
docker run -d \
  --name myowntvapp \
  --network host \
  -v ./data:/app/data \
  ghcr.io/tom-enns/myowntvapp:latest
```

> **Note:** `--network host` is required for Apple TV discovery (mDNS) and so the Apple TV can reach the proxy server. If you don't need AirPlay casting, you can use `-p 1919:1919` instead.

### Option 3: Pull from GitHub Container Registry

```bash
docker pull ghcr.io/tom-enns/myowntvapp:latest
```

---

## Unraid Installation

### Using Community Applications (Docker)

1. Go to the **Docker** tab in Unraid
2. Click **Add Container**
3. Fill in the following:

| Field | Value |
|-------|-------|
| **Name** | `myowntvapp` |
| **Repository** | `ghcr.io/tom-enns/myowntvapp:latest` |
| **Network Type** | `host` |
| **WebUI** | `http://[IP]:[PORT:1919]` |

4. Add a **Path** mapping:

| Container Path | Host Path | Description |
|---------------|-----------|-------------|
| `/app/data` | `/mnt/user/appdata/myowntvapp` | App data (credentials) |

5. Click **Apply**
6. Access the app at `http://<unraid-ip>:1919`

### Using Docker Compose on Unraid

If you have the **Compose Manager** plugin installed:

1. Create a new stack called `myowntvapp`
2. Paste this compose file:

```yaml
services:
  myowntvapp:
    image: ghcr.io/tom-enns/myowntvapp:latest
    container_name: myowntvapp
    network_mode: host
    volumes:
      - /mnt/user/appdata/myowntvapp:/app/data
    restart: unless-stopped
```

3. Click **Compose Up**

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `1919` | Server port |
| `PUBLIC_HOST` | auto-detect | Public host:port for stream URLs (e.g. `192.168.1.50:1919`) |
| `CREDENTIAL_FILE` | `data/credentials.json` | Path to AirPlay credentials file |
| `EXTRACT_TIMEOUT_S` | `45` | Stream extraction timeout in seconds |
| `FFMPEG_BIN` | auto-detect | Path to ffmpeg binary |

---

## Development

### Local Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 1919
```

Requires `ffmpeg` installed locally (`brew install ffmpeg` on macOS).

### Build Docker Image Locally

```bash
docker build -t myowntvapp .
docker run --network host -v ./data:/app/data myowntvapp
```

---

## Tech Stack

- **Backend:** Python, FastAPI, aiohttp, BeautifulSoup
- **Frontend:** Vanilla JS, HLS.js (Chrome/Firefox), native HLS (Safari/iOS)
- **Streaming:** ffmpeg for HLS remuxing, m3u8 playlist rewriting
- **Casting:** pyatv for AirPlay protocol
- **Container:** Python 3.12 slim + ffmpeg

---

## Disclaimer

This project is for educational and personal use only. It does not host or distribute any content. Users are responsible for ensuring their use complies with applicable laws and terms of service. The authors assume no liability for misuse.
