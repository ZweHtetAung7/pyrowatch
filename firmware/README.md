# PyroWatch Beacon (ESP32)

A physical early-warning device for one California location: a home in Paradise, a fire station,
a campsite. It shows that location's live PyroWatch risk on LEDs and on a servo-driven
"fire danger" dial. It sounds an alarm when risk climbs to Very high or Extreme, or when satellites
detect new fires nearby. With a DHT22 attached it is also a **ground station**: its
temperature/humidity readings appear on the PyroWatch map.

Satellites watch from orbit, and beacons report from the ground.

## Parts

| Part | Required? |
|---|---|
| ESP32 DevKit (any ESP32-WROOM board) | yes |
| 3 LEDs (green, yellow, red) + 3 × 220 Ω resistors | yes |
| Passive buzzer | recommended |
| SG90 micro servo + a cardboard dial face (LOW → EXTREME) | recommended: it's the eye-catcher |
| DHT11 temperature/humidity sensor | optional (makes it a ground station) |
| SSD1306 0.96" OLED, I2C | optional |

## Wiring (ESP32 DevKit V1)

| Part | ESP32 pin |
|---|---|
| Green LED (+ 220 Ω to GND) | GPIO 25 |
| Yellow LED (+ 220 Ω to GND) | GPIO 26 |
| Red LED (+ 220 Ω to GND) | GPIO 27 |
| Buzzer + / − | GPIO 14 / GND |
| Servo signal (orange) / power (red) / ground (brown) | GPIO 13 / 5V (VIN) / GND |
| DHT22 data / VCC / GND | GPIO 4 / 3V3 / GND (10 kΩ pull-up data → 3V3 if your module has none) |
| OLED SDA / SCL / VCC / GND | GPIO 21 / GPIO 22 / 3V3 / GND |
| Silence-alarm button | on-board BOOT button (GPIO 0), nothing to wire |

Pins are set in `config.h`, so change them there if your board differs. Set `USE_DHT`, `USE_OLED`
and `USE_SERVO` to 0 for parts you don't have.

## Flash it

1. Arduino IDE → Boards Manager → install **esp32 by Espressif**. Board: **ESP32 Dev Module**.
2. Library Manager → install **ArduinoJson** (v7), plus **DHT sensor library** (Adafruit),
   **Adafruit SSD1306** + **Adafruit GFX Library** and **ESP32Servo** for the parts you use.
3. Copy `config.example.h` to `config.h`. Fill in Wi-Fi, `SERVER_URL` and the location to watch.
   - Developing on venue Wi-Fi: run the server with `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
     and use your laptop's LAN IP (`http://192.168.x.x:8000`). The ESP32 and laptop must be on the same
     network; some venue networks isolate clients, so a phone hotspot is the fallback.
   - After deploying to Vultr: `https://your-domain`.
4. Upload, then open Serial Monitor at 115200 baud. It prints every poll and every reading it posts.

## Test without waiting for a real fire

```bash
curl "http://localhost:8000/api/beacon?lat=39.7596&lon=-121.6219"
```
```bash
curl -X POST http://localhost:8000/api/stations/test-1 -H 'content-type: application/json' -d '{"lat": 34.15, "lon": -118.14, "temp_c": 31.5, "rh_pct": 9, "label": "Pasadena test"}'
```

For a live demo of the alarm, point `WATCH_LAT`/`WATCH_LON` at the area the map currently ranks
highest. Or run the server in demo mode (`PYROWATCH_DATA_MODE=demo`), where synthetic fires appear
around high-risk areas. The OLED shows **[DEMO]** whenever any data is synthetic.

## The Arduino

The ESP32 does everything. An Arduino UNO R4 **WiFi** could run a second beacon; the sketch needs
small changes (`WiFiS3` and `ArduinoHttpClient` in place of `WiFi`/`HTTPClient`). A classic UNO
has no Wi-Fi, so keep it for a second display driven over serial from the ESP32 if you have time.
