# Honey Chain — LIVE MODE

This is the live-data build of Honey Chain.

## What is actually live?

- **OpenAI Copilot:** real OpenAI Responses API when `OPENAI_API_KEY` is configured.
- **Weather:** real external weather from Open-Meteo when the computer has internet access.
- **Hive telemetry:** real only when an ESP32/gateway sends data to `POST /api/telemetry`.
- **Seeded hive values:** demo values, explicitly labelled `DEMO` until real sensor telemetry arrives.
- **Simulator:** test-only data, explicitly labelled `SIMULATOR`.
- **Ledger:** local tamper-evident SHA-256 hash chain. It is not a public blockchain network.

OpenAI's current model catalog lists GPT-5.6 Luna (`gpt-5.6-luna`) as available through the Responses API and Client SDKs. See the official OpenAI model documentation: https://platform.openai.com/docs/models/gpt-4-turbo-and-gpt-4

## 1. Add your OpenAI API key

In VS Code, open the project folder. Copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```

Open `.env` and set:

```env
OPENAI_API_KEY=YOUR_REAL_OPENAI_KEY
OPENAI_MODEL=gpt-5.6-luna
```

The key is read by the Python backend. Do not put the key in HTML or JavaScript.

## 2. Start the app

```powershell
python app.py
```

Open:

http://127.0.0.1:5000

Do not double-click `index.html`; the app must be opened through the Python server.

## 3. Real weather

Open **Live Weather** and press **Fetch live weather**. Weather is retrieved from Open-Meteo for the configured Honey Chain clusters.

## 4. Real ESP32 data

Send JSON to the computer running Honey Chain:

```http
POST http://YOUR-PC-IP:5000/api/telemetry
Content-Type: application/json
```

Example:

```json
{
  "id": "REAL-HIVE-01",
  "cluster": "Chikkaballapur",
  "temp": 35.6,
  "humidity": 62,
  "weight": 23.8,
  "activity": 119
}
```

Once recent sensor telemetry is received, the header changes to `SENSOR LIVE`.

For ESP32 on the same Wi-Fi network, use the PC's LAN IP rather than `127.0.0.1`.

## 5. AI Copilot

Without a key, Copilot uses a local rule-based fallback.
With a valid OpenAI key, the UI shows `LLM` and requests go through the real Responses API.

The Copilot receives the current Honey Chain state (hives, batches, weather and alerts) as context. It is decision support, not a disease diagnosis or food-safety certification.

## 6. QR verification

The QR action currently uses the online `api.qrserver.com` renderer, so that button requires internet access. The public verification page itself is served by the Honey Chain backend.

## 7. Built-in test

Run:

```powershell
python self_test.py
```

The test uses a temporary SQLite database and a local mock OpenAI endpoint. It does not use your real API key or modify the production database.


## Important: open the app through the server

Use `python app.py` or `run_windows.bat`, then open `http://127.0.0.1:5000`. Do **not** double-click `index.html`. The server serves `/static/app.js` and `/static/style.css`; opening the HTML file directly bypasses the Python backend and leaves the page at `CONNECTING`.

## If the browser ever shows an unstyled page
Do not use a file path or double-click `index.html`. Start the server with `python app.py` and open `http://127.0.0.1:5000/`. The HTML now auto-redirects from `file://` to the local Flask server when the server is running.
