# app/main.py
import io
import os
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse, Response
from pydantic import BaseModel
from dotenv import load_dotenv

from serial.serialutil import SerialException
from .serial_client import ArduinoSerial
from .llm_router import quick_rule_based, llm_parse_text_to_command, ParsedCommand

# --- math helpers to resolve math commands to SET ---
def clamp(v: float, lo: int = 1500, hi: int = 10000) -> int:
    return int(max(lo, min(hi, round(v))))

from serial.serialutil import SerialException

def resolve_math_to_set(p: ParsedCommand) -> ParsedCommand:
    """
    دستورات ریاضی را به یک فرمان SET نهایی تبدیل می‌کند.
    پشتیبانی‌شده: ADD, SUB, MUL, DIV, DOUBLE, PLUS (هم‌معنی ADD)

    منطق:
      - اگر cmd یکی از موارد بالا بود، ابتدا وضعیت آردوینو گرفته می‌شود.
      - «پایه» اولویتاً از SET (هدف فعلی) و در غیر این صورت از RPM خوانده می‌شود.
      - عملیات ریاضی اعمال و خروجی به بازه 1500..10000 کلمپ می‌شود.
      - نتیجه به صورت ParsedCommand(cmd="SET", value=...) برگردانده می‌شود.
      - در صورت نبودِ پایه‌ی معتبر یا ورودی نامعتبر، SerialException پرتاب می‌شود.
    """
    if p is None or not getattr(p, "cmd", None):
        return p

    # PLUS را معادل ADD کن
    cmd = p.cmd.upper()
    if cmd == "PLUS":
        cmd = "ADD"

    # اگر دستوری غیرریاضی است، همان را برگردان
    if cmd not in ("ADD", "SUB", "MUL", "DIV", "DOUBLE"):
        return p

    # وضعیت فعلی را بگیر
    st = get_arduino().status()  # مثل {"raw":"RPM:...,PL:...,SET:...,ON:...","RPM":..., "SET":...}

    # پایه: اول SET، بعد RPM
    base = st.get("SET")
    if not isinstance(base, (int, float)) or base <= 0:
        base = st.get("RPM")
    if not isinstance(base, (int, float)) or base < 0:
        raise SerialException("current speed/target unknown; cannot apply math command")

    base = float(base)

    # مقدار کمکی را به float تبدیل کن (اگر لازم است)
    def _require_number(val, name: str) -> float:
        if val is None:
            raise SerialException(f"{name} needs a number")
        try:
            return float(val)
        except Exception:
            raise SerialException(f"{name} value is not a number")

    # اعمال عملگر
    if cmd == "ADD":
        delta = _require_number(p.value, "ADD")
        new_v = base + delta
    elif cmd == "SUB":
        delta = _require_number(p.value, "SUB")
        new_v = base - delta
    elif cmd == "MUL":
        factor = _require_number(p.value, "MUL")
        new_v = base * factor
    elif cmd == "DIV":
        denom = _require_number(p.value, "DIV")
        if denom == 0:
            raise SerialException("DIV by zero")
        new_v = base / denom
    elif cmd == "DOUBLE":
        new_v = base * 2
    else:
        new_v = base

    # کلمپ به بازه مجاز
    return ParsedCommand(cmd="SET", value=clamp(new_v))

load_dotenv()

# -----------------------------
# Environment / Config
# -----------------------------
ARDUINO_PORT = os.getenv("ARDUINO_PORT", "/dev/ttyACM0")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# -----------------------------
# OpenAI-compatible client
# -----------------------------
openai_client = None
try:
    from openai import OpenAI
    if OPENAI_API_KEY:
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None
        )
except Exception:
    openai_client = None

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Voice → LLM → Arduino")
TEMPLATE_DIR = Path(__file__).parent / "templates"
INDEX_HTML_PATH = TEMPLATE_DIR / "index.html"

@app.get("/", response_class=HTMLResponse)
def home():
    if INDEX_HTML_PATH.exists():
        return INDEX_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse("<h3>Upload page missing. Create app/templates/index.html</h3>", status_code=200)

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

# -----------------------------
# Lazy Arduino handle
# -----------------------------
_arduino: Optional[ArduinoSerial] = None

def get_arduino() -> ArduinoSerial:
    global _arduino
    if _arduino is None:
        _arduino = ArduinoSerial(port=ARDUINO_PORT, baudrate=9600, timeout=1.5)
    return _arduino

# -----------------------------
# Schemas
# -----------------------------
class TextCommandIn(BaseModel):
    text: str

# -----------------------------
# Helpers
# -----------------------------
def run_whisper(file_bytes: bytes, filename: str, force_lang: Optional[str] = None) -> str:
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY/OPENAI_BASE_URL تنظیم نشده یا کلاینت در دسترس نیست.")
    audio = io.BytesIO(file_bytes)
    audio.name = filename
    tr = openai_client.audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=audio,
        language=force_lang
    )
    return tr.text.strip()

def to_serial_line(parsed: ParsedCommand) -> str:
    if parsed.cmd == "SET":
        return f"SET {int(parsed.value)}"
    return parsed.cmd

def ask_status_if_needed(parsed: ParsedCommand) -> Optional[dict]:
    if parsed.cmd in ("DOUBLE", "SET", "ON", "OFF", "ADD", "SUB", "MUL", "DIV", "PLUS"):
        try:
            return get_arduino().status()
        except Exception:
            return None
    return None

# -----------------------------
# Endpoints
# -----------------------------
@app.post("/voice-command")
async def voice_command(
    audio: UploadFile = File(...),
    force_lang: Optional[str] = Form(None)
):
    # 1) ASR

    line = None
    try:
        audio_bytes = await audio.read()
        text = run_whisper(audio_bytes, audio.filename, force_lang)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"asr_failed: {type(e).__name__}: {e}"},
            status_code=500
        )

    # 2) Parse
    parsed: Optional[ParsedCommand] = None
    try:
        if openai_client:
            parsed = llm_parse_text_to_command(openai_client, text)
    except Exception:
        parsed = None
    if parsed is None:
        parsed = quick_rule_based(text)
    if parsed is None:
        return JSONResponse({"ok": False, "text": text, "error": "could_not_parse"}, status_code=400)

    # 3) Send to Arduino
    try:
        parsed = resolve_math_to_set(parsed)
        line = to_serial_line(parsed)
        resp = get_arduino().send_command(line)
        extra = ask_status_if_needed(parsed)
    except SerialException as e:
        return JSONResponse(
            {
                "ok": False,
                "text": text,
                "parsed": parsed.model_dump(),
                "sent": line,
                "error": f"serial_failed: {e}",
                "hint": "پورت مشغول/در دسترس نیست. Serial Monitor/ModemManager را ببند، یا ARDUINO_PORT را اصلاح کن."
            },
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "text": text,
                "parsed": parsed.model_dump(),
                "sent": line,
                "error": f"serial_failed: {type(e).__name__}: {e}",
            },
            status_code=500
        )

    return {
        "ok": True,
        "text": text,
        "parsed": parsed.model_dump(),
        "sent": line,
        "arduino_response": resp,
        "status": extra or {}
    }

@app.post("/text-command")
async def text_command(payload: TextCommandIn):
    line = None
    parsed: Optional[ParsedCommand] = None
    try:
        if openai_client:
            parsed = llm_parse_text_to_command(openai_client, payload.text)
    except Exception:
        parsed = None
    if parsed is None:
        parsed = quick_rule_based(payload.text)
    if parsed is None:
        return JSONResponse({"ok": False, "error": "could_not_parse"}, status_code=400)

    try:
        parsed = resolve_math_to_set(parsed)
        line = to_serial_line(parsed)
        resp = get_arduino().send_command(line)
        extra = ask_status_if_needed(parsed)
    except SerialException as e:
        return JSONResponse(
            {"ok": False, "parsed": parsed.model_dump(), "sent": line, "error": f"serial_failed: {e}"},
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "parsed": parsed.model_dump(), "sent": line, "error": f"serial_failed: {type(e).__name__}: {e}"},
            status_code=500
        )

    return {
        "ok": True,
        "parsed": parsed.model_dump(),
        "sent": line,
        "arduino_response": resp,
        "status": extra or {}
    }

@app.get("/status")
async def status():
    try:
        st = get_arduino().status()
        return {"ok": True, "status": st}
    except SerialException as e:
        return JSONResponse({"ok": False, "error": f"serial_failed: {e}"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"serial_failed: {type(e).__name__}: {e}"}, status_code=500)
