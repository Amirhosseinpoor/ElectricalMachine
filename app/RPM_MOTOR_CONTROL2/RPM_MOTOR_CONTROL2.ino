#include <Adafruit_GFX.h>
#include <Adafruit_PCD8544.h>

Adafruit_PCD8544 display = Adafruit_PCD8544(7, 6, 5, 4, 3);

String inputLine = "";
int setpoint = 0;       // هدف RPM (0 یعنی غیرفعال)
int pl = 100;           // PWM level
bool motorOn = false;   // وضعیت روشن/خاموش

#define ENA 11
#define IN1 10
#define IN2 9
#define D0 2

volatile unsigned long pulseCount = 0;
unsigned long lastTime = 0;
float rpm = 0;

void setup() {
  Serial.begin(9600);
  display.begin();
  display.setContrast(60);
  display.setRotation(0);
  display.clearDisplay();

  pinMode(D0, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(D0), countPulse, RISING);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);

  analogWrite(ENA, 0);   // شروع خاموش
  motorOn = false;

  inputLine.reserve(200);
  display.display();
  delay(500);
  Serial.println("START");
}

void loop() {
  // --- محاسبه RPM هر 400ms
  unsigned long currentTime = millis();
  if (currentTime - lastTime >= 400) {
    noInterrupts();
    unsigned long pulses = pulseCount;
    pulseCount = 0;
    interrupts();

    // بازه 400ms → 2.5 برابر برای یک ثانیه
    // سنسور 8 پالس در دور:
    rpm = (pulses * (1000.0 / 400.0) / 8.0) * 60.0;

    // حلقه کنترل خیلی ساده بر اساس setpoint
    if (motorOn && setpoint > 0) {
      if (rpm < setpoint) {
        pl += 2;
        if (pl > 255) pl = 255;
      } else {
        pl -= 2;
        if (pl < 0) pl = 0;
      }
      analogWrite(ENA, pl);
    } else {
      analogWrite(ENA, 0);
    }

    // نمایشگر
    display.clearDisplay();
    display.setTextColor(BLACK);
    int _rpm = (int)rpm;

    display.setTextSize(2);
    display.setCursor(0, 0);
    display.println(_rpm);

    display.setTextSize(1);
    display.setCursor(40, 20);
    display.println("RPM");

    display.setCursor(0, 30);
    display.println("PL:");
    display.setCursor(18, 30);
    display.println(pl);

    display.setCursor(0, 40);
    display.println("SET:");
    display.setCursor(25, 40);
    display.println(setpoint);

    display.setCursor(65, 40);
    display.println(motorOn ? "ON" : "OFF");

    display.display();

    lastTime = currentTime;
  }

  // --- خواندن دستورات خطی سریال
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      handleCommand(inputLine);
      inputLine = "";
    } else {
      inputLine += c;
    }
  }
}

void countPulse() { pulseCount++; }

static bool inRange(int v) { return v >= 1500 && v <= 10000; }

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd.startsWith("SET")) {
    // SET <speed>
    int sp = parseNumberAfter(cmd, "SET");
    if (inRange(sp)) {
      setpoint = sp;
      motorOn = true; // وقتی setpoint معتبر می‌آید، روشن کن
      Serial.print("OK SET ");
      Serial.println(setpoint);
    } else {
      Serial.println("ERR SET RANGE 1500..10000");
    }
    return;
  }

  if (cmd == "DOUBLE") {
    int base = (int)rpm;
    if (base <= 0 && setpoint > 0) base = setpoint;
    if (base <= 0) {
      Serial.println("ERR DOUBLE NOBASE");
      return;
    }
    long sp = (long)base * 2L;
    if (sp < 1500) sp = 1500;
    if (sp > 10000) sp = 10000;
    setpoint = (int)sp;
    motorOn = true;
    Serial.print("OK DOUBLE ");
    Serial.println(setpoint);
    return;
  }

  // --- ADD / PLUS <delta>
  if (cmd.startsWith("ADD") || cmd.startsWith("PLUS")) {
    // عدد بعد از کلمه ADD یا PLUS را بخوان
    int delta = cmd.startsWith("ADD") ? parseNumberAfter(cmd, "ADD") : parseNumberAfter(cmd, "PLUS");
    if (delta <= 0) {
      Serial.println("ERR ADD NONUM"); // عدد نامعتبر یا وجود ندارد
      return;
    }
    // تعیین پایه: ابتدا setpoint، اگر نبود rpm فعلی
    int base = (setpoint > 0) ? setpoint : (int)rpm;
    if (base <= 0) {
      Serial.println("ERR ADD NOBASE"); // نه setpoint داریم نه rpm مفید
      return;
    }
    long sp = (long)base + (long)delta;
    if (sp < 1500) sp = 1500;
    if (sp > 10000) sp = 10000;
    setpoint = (int)sp;
    motorOn = true;
    Serial.print("OK ADD ");
    Serial.println(setpoint);
    return;
  }

  if (cmd == "ON") {
    motorOn = true;
    Serial.println("OK ON");
    return;
  }

  if (cmd == "OFF") {
    motorOn = false;
    analogWrite(ENA, 0);
    Serial.println("OK OFF");
    return;
  }

  if (cmd == "STATUS") {
    Serial.print("RPM:");
    Serial.print((int)rpm);
    Serial.print(",PL:");
    Serial.print(pl);
    Serial.print(",SET:");
    Serial.print(setpoint);
    Serial.print(",ON:");
    Serial.println(motorOn ? 1 : 0);
    return;
  }

  Serial.println("ERR UNKNOWN");
}

int parseNumberAfter(const String &s, const char *kw) {
  int idx = s.indexOf(kw);
  if (idx < 0) return -1;
  int start = idx + String(kw).length();
  while (start < (int)s.length() && s[start] == ' ') start++;
  String num = "";
  while (start < (int)s.length() && isDigit(s[start])) {
    num += s[start++];
  }
  return num.length() ? num.toInt() : -1;
}
