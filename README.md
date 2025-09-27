# Voice → LLM → Arduino (Motor Controller)

A tiny end-to-end system that turns spoken or typed natural-language commands (English + Farsi) into safe motor control over serial.
It uses FastAPI for the backend, Whisper for speech-to-text, an optional LLM for robust parsing, a rule-based fallback, and an Arduino sketch that closes the loop and drives a DC motor with RPM feedback, on-device display, and a simple serial protocol.

> این پروژه فرمان‌های متنی و صوتی (فارسی و انگلیسی) را به کنترل موتور تبدیل می‌کند. اگر LLM در دسترس نباشد، مسیر قاعده‌محور فعال می‌شود.

---

## Table of Contents

* [Features](#features)
* [System Architecture](#system-architecture)
* [Hardware](#hardware)
* [Firmware (Arduino)](#firmware-arduino)
* [Backend (FastAPI)](#backend-fastapi)

  * [Command Grammar](#command-grammar)
  * [Parsing Pipeline](#parsing-pipeline)
  * [Math Resolution & Safety](#math-resolution--safety)
  * [REST API](#rest-api)
* [Setup](#setup)

  * [Python environment](#python-environment)
  * [Environment variables](#environment-variables)
  * [Run the server](#run-the-server)
* [Serial Protocol](#serial-protocol)
* [Troubleshooting](#troubleshooting)
* [Testing Ideas](#testing-ideas)
* [Roadmap](#roadmap)
* [License](#license)

---

## Features

* 🎤 **Voice commands** via Whisper (`/voice-command`).
* 🗣️ **Natural language** parsing (English + Farsi), including numbers and common phrases.
* 🧠 **Two-stage parser**:

  * LLM‐based (optional; GPT-compatible).
  * **Rule-based fallback** that covers common phrasings.
* ➕ **Math commands** (ADD/SUB/MUL/DIV/DOUBLE, percentages like “increase 10%”).
* 🧯 **Safety**: all targets are clamped to **1500–10000 RPM**.
* 🔁 **Feedback loop** on Arduino: RPM estimation from pulses, simple controller adjusts PWM.
* 🖥️ **On-device display** (Nokia 5110 / PCD8544): RPM, PWM level (PL), SET, ON/OFF.
* 🔌 **Minimal serial protocol** with human-readable responses and a `STATUS` snapshot.

---

## System Architecture

```
[Mic / WAV/MP3]
        │
        ▼
   Whisper ASR (optional force_lang)
        │ text
        ▼
   LLM Parser (gpt-4o-mini) ──► if unavailable ► Rule-Based Parser
        │ ParsedCommand {cmd,value}
        ▼
   Math Resolution → final SET value (clamped 1500..10000)
        │ "SET N" or ON/OFF/STATUS
        ▼
   ArduinoSerial over /dev/ttyACM*
        │
        ▼
  Arduino: reads command, updates setpoint/PWM, returns STATUS
        │
        └── Nokia 5110 shows RPM/PL/SET/ON
```

---

## Hardware

* **Arduino** (e.g., Uno/Nano).
* **DC motor** + **encoder / hall sensor** (assumed **8 pulses/rev**).
* **Motor driver** (H-bridge; e.g., L298N, PWM on `ENA`).
* **Nokia 5110 LCD (PCD8544)**.

### Pin mapping (as in the sketch)

* **Encoder / Hall**: `D0` → **pin 2** (interrupt), `INPUT_PULLUP`.
* **Motor driver**:

  * `ENA` → **pin 11** (PWM)
  * `IN1` → **pin 10**
  * `IN2` → **pin 9**
* **PCD8544** (Adafruit_PCD8544 constructor order: `SCLK, DIN, DC, CE, RST`)

  * `SCLK` → **7**
  * `DIN`  → **6**
  * `DC`   → **5**
  * `CE`   → **4**
  * `RST`  → **3**

> Adjust wiring if you use other boards/drivers. Ensure shared **GND**.

---

## Firmware (Arduino)

Key points from `*.ino`:

* **RPM estimation** every **400 ms**:

  ```
  rpm = (pulses * (1000/400) / 8) * 60
  ```

  where **8** = pulses/revolution (change if your sensor differs).

* **Simple controller** (bang-bang-ish): if `rpm < setpoint` → `pl += 2`, else `pl -= 2`, then `analogWrite(ENA, pl)`.

* **Commands supported by the sketch**:

  * `SET <speed>` → sets `setpoint` (1500..10000) & turns motor **ON**.
  * `DOUBLE` → doubles base (from `setpoint` or `rpm`), clamped.
  * `ADD <delta>` / `PLUS <delta>` → increases base, clamped.
  * `ON` / `OFF`
  * `STATUS` → prints `RPM:<int>,PL:<int>,SET:<int>,ON:<0|1>`

* **Display** shows: RPM (big), “RPM” label, `PL`, `SET`, and `ON/OFF`.

---

## Backend (FastAPI)

### Command Grammar

The backend normalizes natural language into one of these **allowed commands**:

```json
{"cmd":"SET","value": <int 1500..10000>}
{"cmd":"DOUBLE"}
{"cmd":"ON"}   {"cmd":"OFF"}   {"cmd":"STATUS"}
{"cmd":"ADD","value": <number>}
{"cmd":"SUB","value": <number>}
{"cmd":"MUL","value": <number>}
{"cmd":"DIV","value": <number>}
```

Natural aliases & Farsi examples:

* **SET target**: “up to 2000”, “speed to 2500”, “به ۲۵۰۰ برسه”
* **Increase/Decrease**: “increase by 500”, “کم کن ۲۰۰”
* **Multiply/Divide**: “ضربدر 1.2”, “divide by 2”
* **Percent**: “increase 10%” → `{"cmd":"MUL","value":1.10}`

> If a numeric **target** is mentioned (“to 2500”), **prefer SET**.

### Parsing Pipeline

1. **LLM prompt** (zero-temperature) with `gpt-4o-mini` returns a **single minified JSON** line (strict schema).
2. If LLM is unavailable or fails JSON parsing → **`quick_rule_based()`** uses regexes to detect intents, Farsi/English keywords, percentages, and operators.
3. If still ambiguous → HTTP 400 with `could_not_parse`.

### Math Resolution & Safety

Before hitting the Arduino, **all math-like commands** are resolved to a **final `SET`** against the **current base**:

* Base priority: **current `SET`**, else **current `RPM`** (from `STATUS`).
* Supported: `ADD`, `SUB`, `MUL`, `DIV`, `DOUBLE` (and `PLUS` alias).
* Division by zero → error.
* Final target is **clamped to 1500..10000**.

This guarantees the Arduino receives **“SET N”** or a **non-SET** command (ON/OFF/STATUS) only when appropriate.

---

## REST API

### `POST /voice-command`

Accepts an audio file and (optionally) a forced language code for Whisper.

* **Form fields**:

  * `audio`: file (wav/mp3/m4a…)
  * `force_lang` (optional): e.g., `fa`, `en`

**Example (curl):**

```bash
curl -X POST http://localhost:8000/voice-command \
  -F "audio=@sample-fa.m4a" \
  -F "force_lang=fa"
```

**Success response:**

```json
{
  "ok": true,
  "text": "به ۲۵۰۰ برسه",
  "parsed": {"cmd":"SET","value":2500},
  "sent": "SET 2500",
  "arduino_response": "OK SET 2500",
  "status": {"raw":"RPM:1234,PL:98,SET:2500,ON:1","RPM":1234,"PL":98,"SET":2500,"ON":1}
}
```

**Common errors**:

* `asr_failed: ...` (Whisper config/API)
* `serial_failed: ...` (port/busy)
* `could_not_parse`

---

### `POST /text-command`

Parses plain text input (English/Farsi) and drives the Arduino.

**Body:**

```json
{"text":"increase 10%"}
```

**Example (curl):**

```bash
curl -X POST http://localhost:8000/text-command \
  -H "Content-Type: application/json" \
  -d '{"text":"سرعت رو ببر تا ۳۵۰۰"}'
```

**Response** mirrors `/voice-command`.

---

### `GET /status`

Returns a fresh Arduino status snapshot.

```bash
curl http://localhost:8000/status
```

```json
{"ok": true, "status": {"raw":"RPM:1278,PL:90,SET:3500,ON:1","RPM":1278,"PL":90,"SET":3500,"ON":1}}
```

---

## Setup

### Python environment

* Python **3.10+** recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install fastapi uvicorn python-dotenv pydantic pyserial openai
# (Whisper uses OpenAI Audio API; no extra local deps)
```

### Environment variables

Create a `.env` in `app/` (or project root—match your run path):

```env
# Serial
ARDUINO_PORT=/dev/ttyACM0

# OpenAI compatible endpoint (optional but recommended)
OPENAI_API_KEY=sk-...
# For self-hosted/compatible endpoints:
# OPENAI_BASE_URL=https://api.openai.com/v1

# Whisper model id (server-side)
WHISPER_MODEL=whisper-1
```

> If `OPENAI_API_KEY` is **absent**, the app **skips LLM parsing** and relies on `quick_rule_based()`.

### Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

* Open `GET /` for a basic HTML placeholder (drop your `app/templates/index.html` to customize).
* Ensure the Arduino is connected on `ARDUINO_PORT` and not locked by Serial Monitor/ModemManager.

---

## Serial Protocol

The Arduino sketch supports:

* `SET <n>` → sets target RPM (`1500..10000`), turns **ON**.
* `DOUBLE` → doubles base (`setpoint` or current `rpm`), clamps to range.
* `ADD <delta>` / `PLUS <delta>` → increments base, clamps to range.
* `ON` / `OFF`
* `STATUS` → `RPM:<int>,PL:<int>,SET:<int>,ON:<0|1>`

**Notes:**

* Backend typically converts math commands into a single `SET` before sending.
* Sketch itself also understands `ADD`/`PLUS`/`DOUBLE` for manual testing via serial monitor.

---

## Troubleshooting

* **`serial_failed: could not open port …`**
  Close the Arduino IDE Serial Monitor; on Linux, stop `ModemManager`. Verify `ARDUINO_PORT`.
* **No RPM movement / unstable control**
  Check sensor wiring and pulse count per rev (`8` in code). Adjust controller step (`pl +=/-= 2`) or add a proper PID later.
* **Display blank**
  Verify PCD8544 wiring (5 pins), contrast (`display.setContrast(60)`), and power (some modules need 3.3V).
* **ASR errors**
  Confirm `OPENAI_API_KEY`, `OPENAI_BASE_URL` (if custom), and `WHISPER_MODEL` name.
* **Parser fails with Farsi numerals**
  The rule-based path handles common Persian terms/numerals; if you hit an edge case, try enabling the LLM parser.

---

## Testing Ideas

* ✅ Unit tests for:

  * `quick_rule_based()` (English + Farsi examples, percentages, edge numbers).
  * `resolve_math_to_set()` (ADD/SUB/MUL/DIV/DOUBLE with mocked `status()`).
* 🔁 Integration tests:

  * Mock `ArduinoSerial` to validate sent lines for a suite of inputs.
  * Golden tests for LLM prompt (if using a fixed stubbed response).
* 🔊 Manual:

  * Speak: “increase 10 percent”, “به ۳۰۰۰ برسه”, “double”, “turn off”.

---

## Roadmap

* [ ] PID control on Arduino for smoother tracking.
* [ ] Auto-detect encoder PPR, or make it configurable via command.
* [ ] Web UI: mic record, live status, charts, command history.
* [ ] Dockerfile + CI tests.
* [ ] Multi-language i18n on responses.
* [ ] Safer range negotiation from device → server (dynamic limits).

---

## License

Choose a license (e.g., MIT) and add it as `LICENSE`.
Include attribution for Adafruit libraries and any pinouts referenced.

---

### Acknowledgements

* [Adafruit GFX / PCD8544 libraries] for LCD support.
* OpenAI Whisper API for ASR.
* FastAPI & Pydantic for a clean, typed backend.

---

### Appendix: Example Inputs → Parsed Commands

| User text              | Parsed                                     | Sent to Arduino          |
| ---------------------- | ------------------------------------------ | ------------------------ |
| “up to 2000”           | `{"cmd":"SET","value":2000}`               | `SET 2000`               |
| “increase 10%”         | `{"cmd":"MUL","value":1.1}` → **resolved** | `SET <base*1.1 clamped>` |
| “کم کن ۲۰۰”            | `{"cmd":"SUB","value":200}` → **resolved** | `SET <base-200 clamped>` |
| “double” / “دو برابر”  | `{"cmd":"DOUBLE"}` → **resolved**          | `SET <base*2 clamped>`   |
| “turn off” / “خاموش”   | `{"cmd":"OFF"}`                            | `OFF`                    |
| “status” / “سرعت چنده” | `{"cmd":"STATUS"}`                         | `STATUS`                 |

> **Base** for math = `SET` if present, otherwise `RPM` (from device `STATUS`). If neither is usable, the server errors safely.
