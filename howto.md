# How-To: MQTT Broker + Live Map

This guide covers two parts: stand up a MeshCore MQTT broker and point the live map at it.
Current version: `1.1.2` (see `VERSIONS.md`).

## 1) MQTT broker (meshcore-mqtt-broker)

Use the broker repo from Michael Hart.

Install dependencies:

```bash
apt install git curl npm
```

Clone the repo:

```bash
git clone https://github.com/michaelhart/meshcore-mqtt-broker
cd meshcore-mqtt-broker
```

Create the data folder:

```bash
mkdir data
```

Create your `.env`:

```bash
cp .env.example .env
```

Edit `.env` and update the database path (use an absolute path that matches your checkout). While you are there, fill out the rest of the `.env` to your liking, and make sure `AUTH_EXPECTED_AUDIENCE` is set to the hostname of your MQTT server:

```bash
# Abuse Detection - Persistence
ABUSE_PERSISTENCE_PATH=/home/user/meshcore-mqtt-broker/data/abuse-detection.db
```

Build and run:

```bash
npm install
npm run build
npm start
```

---

### Run it as a systemd service (optional)

Create `/etc/systemd/system/meshcore-mqtt.service`:

```bash
[Unit]
Description=MeshCore MQTT Broker
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/meshcore-mqtt-broker

# Start the broker with node + tsx directly
ExecStart=/home/user/.nvm/versions/node/v22.21.1/bin/node /home/user/meshcore-mqtt-broker/node_modules/.bin/tsx src/server.ts

Restart=always
RestartSec=5
Environment=NODE_ENV=production

# Optional logging to files (you can drop these if you prefer journalctl only)
StandardOutput=append:/var/log/meshcore-mqtt.log
StandardError=append:/var/log/meshcore-mqtt-error.log

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-mqtt
```

---

### Cloudflare Tunnel for MQTT over TLS (optional)

Follow the broker docs for Cloudflare Tunnel setup:

https://github.com/michaelhart/meshcore-mqtt-broker/blob/main/docs/cloudflare-tunnels.md

Note: You can run plain TCP on port 1883, but using a tunnel (or another TLS front) avoids exposing the port on the host. If you do use TCP, make sure you lock it down.

## 2) Configure the live map

Clone the repo and enter it:

```bash
git clone https://github.com/yellowcooln/meshcore-mqtt-live-map
cd meshcore-mqtt-live-map
```

Copy `.env.example` to `.env` and update MQTT settings. The map expects MQTT over WebSockets by default:

```env
MQTT_HOST=your-mqtt-host
MQTT_PORT=443
MQTT_USERNAME=youruser
MQTT_PASSWORD=yourpass
MQTT_TRANSPORT=websockets
MQTT_WS_PATH=/
MQTT_TLS=true
MQTT_TOPIC=meshcore/#
```

Optional: enable the coverage layer by setting `COVERAGE_API_URL` (the Coverage button hides itself when blank):

```env
COVERAGE_API_URL=https://coverage.example.com
```

If you are using plain TCP MQTT, set:

```env
MQTT_TRANSPORT=tcp
MQTT_TLS=false
MQTT_PORT=1883
```

## 3) Run the map

Background (detached):

```bash
docker compose up -d --build
```

Foreground (watch logs):

```bash
docker compose up --build
```

Open the UI at `http://localhost:8080`.

## 4) Verify data flow

- `/snapshot` should show devices once adverts arrive.
- `/stats` shows ingest totals.

Example:

```bash
curl -s http://localhost:8080/stats | jq
```

Notes:
- The map only places nodes once it decodes adverts that include a valid location.
- If no nodes appear, verify the MQTT topic and that the broker is sending `/packets` data with raw hex payloads.
