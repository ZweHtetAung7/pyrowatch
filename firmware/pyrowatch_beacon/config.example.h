#pragma once
// Copy this file to config.h (same folder) and fill it in. config.h is gitignored.

// ---- Wi-Fi + server ---------------------------------------------------------------------
#define WIFI_SSID      "your-wifi"
#define WIFI_PASSWORD  "your-password"
// While developing: your laptop's LAN IP running uvicorn with --host 0.0.0.0, e.g. "http://192.168.1.50:8000"
// After deploying: "https://your-domain"
#define SERVER_URL     "http://192.168.1.50:8000"

// ---- What the beacon watches: a California location (a home, a fire station, a campsite) -----
#define WATCH_LABEL     "Paradise, CA"
#define WATCH_LAT       39.7596
#define WATCH_LON       -121.6219
#define ALERT_RADIUS_KM 50          // alarm when new fire detections appear within this radius
#define POLL_SECONDS    60

// ---- Ground station: where the beacon physically is (sensor readings are tagged with this) ----
#define STATION_ID     "ub-hacking-1"
#define STATION_LABEL  "UB Hacking beacon"
#define STATION_LAT    43.0008
#define STATION_LON    -78.7890
#define STATION_TOKEN  ""           // must match STATION_TOKEN in the server's .env if that is set
#define POST_SECONDS   60

// ---- Which parts are connected (1 = yes, 0 = no) --------------------------------------------
#define USE_DHT    1                // DHT22 or DHT11 temperature/humidity sensor
#define DHT_TYPE   DHT11            // Elegoo has DHT11
#define USE_OLED   1                // SSD1306 128x64 I2C display at address 0x3C
#define USE_SERVO  1                // micro servo (SG90) driving a "fire danger" dial

// ---- Pins (ESP32 DevKit V1) ------------------------------------------------------------------
#define PIN_LED_GREEN   25
#define PIN_LED_YELLOW  26
#define PIN_LED_RED     27
#define PIN_BUZZER      14          // passive buzzer (active buzzers also work, but only one pitch)
#define PIN_DHT         4
#define PIN_SERVO       13
#define PIN_BUTTON      0           // the on-board BOOT button: press to silence the alarm
// OLED: SDA = GPIO 21, SCL = GPIO 22 (ESP32 default I2C pins)
