/*
  PyroWatch Beacon (ESP32)

  A physical early-warning device for one California location:
    - polls PyroWatch's /api/beacon for the watched location's live risk and nearby fires
    - green / yellow / red LEDs + a servo "fire danger" dial show the risk level
    - buzzer alarm when risk climbs to Very high/Extreme, or new satellite fire detections
      appear within ALERT_RADIUS_KM; press the BOOT button to silence it
    - optional OLED shows the area, score, nearby fires and the top risk driver
    - optional DHT22 makes it a ground station: it posts temperature/humidity to
      /api/stations/<id>, which appears on the PyroWatch map

  Libraries (Arduino Library Manager): ArduinoJson (v7), and only if enabled in config.h:
  "DHT sensor library" (Adafruit), "Adafruit SSD1306" + "Adafruit GFX Library", "ESP32Servo".
  Board: "ESP32 Dev Module" (Espressif esp32 core, 2.x or 3.x).
*/
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

#if USE_DHT
#include <DHT.h>
DHT dht(PIN_DHT, DHT_TYPE);
#endif
#if USE_OLED
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;
#endif
#if USE_SERVO
#include <ESP32Servo.h>
Servo dial;
int dialAngle = 0;
#endif

const char* LEVELS[] = {"LOW", "MODERATE", "HIGH", "VERY HIGH", "EXTREME"};

struct Status {
  bool ok = false;
  String area;
  int risk = -1;
  int level = -1;
  int rank = 0, of = 0;
  int firesNearby = 0;
  float nearestKm = -1;
  String topFactor;
  bool demo = false;
} st;

int lastLevel = -1, lastFires = -1;
bool alarmOn = false;
unsigned long alarmStarted = 0, lastPoll = 0, lastPost = 0, lastSensor = 0, lastBeep = 0;
float tempC = NAN, rhPct = NAN;

// ------------------------------------------------------------------ networking

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Wi-Fi: connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? String(" ok, IP ") + WiFi.localIP().toString() : String(" FAILED"));
}

// Returns the HTTP status code (<= 0 on connection failure); response body in `out`.
int request(const String& url, const char* method, const String& body, String& out) {
  WiFiClientSecure secure;  // declared before HTTPClient so they outlive it
  WiFiClient plain;
  HTTPClient http;
  bool https = url.startsWith("https://");
  if (https) secure.setInsecure();  // hackathon shortcut: skips certificate checks
  if (!(https ? http.begin(secure, url) : http.begin(plain, url))) return -1;
  http.setTimeout(15000);
  if (strlen(STATION_TOKEN) > 0) http.addHeader("X-Station-Token", STATION_TOKEN);
  int code;
  if (strcmp(method, "POST") == 0) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
  } else {
    code = http.GET();
  }
  out = code > 0 ? http.getString() : String();
  http.end();
  return code;
}

void startAlarm(const char* why) {
  Serial.printf("ALARM: %s\n", why);
  alarmOn = true;
  alarmStarted = millis();
  lastBeep = 0;
}

void pollStatus() {
  String url = String(SERVER_URL) + "/api/beacon?lat=" + String(WATCH_LAT, 4) + "&lon=" + String(WATCH_LON, 4) +
               "&radius_km=" + String(ALERT_RADIUS_KM);
  String body;
  int code = request(url, "GET", "", body);
  if (code != 200) {
    st.ok = false;
    Serial.printf("poll failed: HTTP %d %s\n", code, body.c_str());
    return;
  }
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    st.ok = false;
    Serial.println("poll: bad JSON");
    return;
  }
  st.ok = true;
  st.area = (const char*)(doc["area"] | "?");
  st.risk = doc["risk"] | -1;
  st.level = doc["level"] | -1;
  st.rank = doc["rank"] | 0;
  st.of = doc["of"] | 0;
  st.firesNearby = doc["fires_nearby"] | 0;
  st.nearestKm = doc["nearest_fire_km"].isNull() ? -1.0f : doc["nearest_fire_km"].as<float>();
  st.topFactor = (const char*)(doc["top_factor"] | "");
  st.demo = doc["demo"] | false;
  Serial.printf("%s: risk %d (%s), rank %d/%d, %d fires within %d km%s\n", st.area.c_str(), st.risk,
                st.level >= 0 ? LEVELS[st.level] : "?", st.rank, st.of, st.firesNearby, ALERT_RADIUS_KM,
                st.demo ? " [DEMO DATA]" : "");

  if (lastLevel >= 0 && st.level >= 3 && st.level > lastLevel) startAlarm("risk escalated");
  if (lastFires >= 0 && st.firesNearby > lastFires) startAlarm("new fire detections nearby");
  lastLevel = st.level;
  lastFires = st.firesNearby;
}

void readSensor() {
#if USE_DHT
  float h = dht.readHumidity(), t = dht.readTemperature();
  if (!isnan(h) && !isnan(t)) {
    rhPct = h;
    tempC = t;
  } else {
    Serial.println("DHT read failed (check wiring / DHT_TYPE)");
  }
#endif
}

void postReading() {
#if USE_DHT
  if (isnan(tempC) || isnan(rhPct)) return;
  JsonDocument doc;
  doc["lat"] = STATION_LAT;
  doc["lon"] = STATION_LON;
  doc["temp_c"] = tempC;
  doc["rh_pct"] = rhPct;
  doc["label"] = STATION_LABEL;
  String body, out;
  serializeJson(doc, body);
  int code = request(String(SERVER_URL) + "/api/stations/" + STATION_ID, "POST", body, out);
  Serial.printf("posted reading %.1f C / %.0f%% RH -> HTTP %d\n", tempC, rhPct, code);
#endif
}

// ------------------------------------------------------------------ outputs

void setLeds(unsigned long now) {
  bool blink = (now / 400) % 2;
  bool g = false, y = false, r = false;
  if (!st.ok) {
    y = (now / 1000) % 2;  // slow yellow blink: no data
  } else if (st.level <= 1) {
    g = true;
  } else if (st.level == 2) {
    y = true;
  } else if (st.level == 3) {
    r = true;
  } else {
    r = blink;  // extreme: flashing red
  }
  digitalWrite(PIN_LED_GREEN, g);
  digitalWrite(PIN_LED_YELLOW, y);
  digitalWrite(PIN_LED_RED, r || (alarmOn && blink));
}

void runAlarm(unsigned long now) {
  if (!alarmOn) return;
  if (now - alarmStarted > 60000) {  // give up after a minute
    alarmOn = false;
    return;
  }
  if (now - lastBeep > 3000) {  // three rising chirps every 3 s
    lastBeep = now;
    for (int i = 0; i < 3; i++) {
      tone(PIN_BUZZER, 2200 + i * 500, 120);
      delay(170);
    }
    noTone(PIN_BUZZER);
  }
}

void moveDial() {
#if USE_SERVO
  if (!st.ok) return;
  // Dial face: LOW on the left (0 deg) to EXTREME on the right (180 deg), like a fire-danger sign.
  int target = (int)map(st.risk, 0, 100, 0, 180);
  target = constrain(target, 0, 180);
  int step = target > dialAngle ? 2 : -2;
  while (abs(target - dialAngle) > 2) {
    dialAngle += step;
    dial.write(dialAngle);
    delay(15);
  }
#endif
}

void drawScreen() {
#if USE_OLED
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("PYROWATCH");
  if (st.demo) oled.print(" [DEMO]");
  if (!st.ok) {
    oled.setCursor(0, 20);
    oled.print(WiFi.status() == WL_CONNECTED ? "Server unreachable" : "No Wi-Fi");
    oled.display();
    return;
  }
  oled.setCursor(0, 10);
  oled.print(st.area.substring(0, 21));
  oled.setTextSize(2);
  oled.setCursor(0, 22);
  oled.print(st.risk);
  oled.setTextSize(1);
  oled.setCursor(40, 22);
  oled.print(st.level >= 0 ? LEVELS[st.level] : "?");
  oled.setCursor(40, 31);
  oled.printf("#%d of %d", st.rank, st.of);
  oled.setCursor(0, 42);
  if (st.firesNearby > 0) oled.printf("Fires <%dkm: %d (%.0fkm)", ALERT_RADIUS_KM, st.firesNearby, st.nearestKm);
  else oled.printf("No fires within %dkm", ALERT_RADIUS_KM);
  oled.setCursor(0, 54);
#if USE_DHT
  if (!isnan(tempC)) oled.printf("Here: %.1fC %.0f%%RH", tempC, rhPct);
  else oled.print(st.topFactor.substring(0, 21));
#else
  oled.print(st.topFactor.substring(0, 21));
#endif
  oled.display();
#endif
}

// ------------------------------------------------------------------ main

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nPyroWatch Beacon: watching %s (%.4f, %.4f)\n", WATCH_LABEL, WATCH_LAT, WATCH_LON);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  const int leds[] = {PIN_LED_GREEN, PIN_LED_YELLOW, PIN_LED_RED};
  for (int pin : leds) {  // lamp test
    digitalWrite(pin, HIGH);
    delay(150);
    digitalWrite(pin, LOW);
  }
  tone(PIN_BUZZER, 2600, 80);
#if USE_DHT
  dht.begin();
#endif
#if USE_OLED
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!oledOk) Serial.println("OLED not found at 0x3C");
#endif
#if USE_SERVO
  dial.attach(PIN_SERVO, 500, 2400);
  dial.write(0);
#endif
  connectWiFi();
  drawScreen();
}

void loop() {
  unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED) connectWiFi();

  if (lastPoll == 0 || now - lastPoll > POLL_SECONDS * 1000UL) {
    lastPoll = now;
    pollStatus();
    moveDial();
    drawScreen();
  }
  if (lastSensor == 0 || now - lastSensor > 5000UL) {
    lastSensor = now;
    readSensor();
  }
  if (lastPost == 0 || now - lastPost > POST_SECONDS * 1000UL) {
    lastPost = now;
    postReading();
    drawScreen();
  }
  if (digitalRead(PIN_BUTTON) == LOW && alarmOn) {
    alarmOn = false;
    noTone(PIN_BUZZER);
    Serial.println("alarm silenced");
  }
  setLeds(now);
  runAlarm(now);
  delay(20);
}
