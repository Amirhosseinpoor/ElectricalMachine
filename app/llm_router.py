# app/llm_router.py
from typing import Literal, Optional
from pydantic import BaseModel

# دستورات مجاز
AllowedCmd = Literal[
    "SET", "DOUBLE", "ON", "OFF", "STATUS",
    "ADD", "SUB", "MUL", "DIV", "INCREASE", "DECREASE",
]

class ParsedCommand(BaseModel):
    cmd: AllowedCmd
    # برای SET و فرامین ریاضی لازم است که float باشد
    value: Optional[float] = None

# --- Rule-based fallback ---
def quick_rule_based(text: str) -> ParsedCommand | None:
    t = text.lower().strip()
    import re

    # --- SET با عدد (اولویت بالا) ---
    # نمونه‌های: "up to 2000", "speed to 2500", "به ۲۵۰۰ برسه"
    m_up_to = re.search(r"\bup\s*to\s*(\d{3,5})\b", t)
    m_num = re.search(r"(\d{3,5})", t)
    if m_up_to:
        val = int(m_up_to.group(1))
        val = max(1500, min(10000, val))
        return ParsedCommand(cmd="SET", value=val)

    if m_num and any(k in t for k in [
        "set", "reach", "to", "speed", "سرعت", "برسه",
        "increase", "decrease", "add", "subtract", "کم", "اضافه", "ببر"
    ]):
        val = int(m_num.group(1))
        val = max(1500, min(10000, val))
        return ParsedCommand(cmd="SET", value=val)

    # روشن/خاموش
    if any(k in t for k in ["turn off", "switch off", "stop", "خاموش"]):
        return ParsedCommand(cmd="OFF")
    if any(k in t for k in ["turn on", "switch on", "start", "روشن"]):
        return ParsedCommand(cmd="ON")

    # دوبرابر
    if any(k in t for k in ["double", "دو برابر", "2x", "twice"]):
        return ParsedCommand(cmd="DOUBLE")

    # درصد → MUL
    m_pct_inc = re.search(r"(?:increase|افزایش|زیاد|ببر)\s+(\d+)\s*%|(\d+)\s*%\s*(?:more|بیشتر)", t)
    m_pct_dec = re.search(r"(?:decrease|کاهش|کم|بیار پایین)\s+(\d+)\s*%|(\d+)\s*%\s*(?:less|کمتر)", t)
    if m_pct_inc:
        p = float(next(g for g in m_pct_inc.groups() if g))
        return ParsedCommand(cmd="MUL", value=1.0 + p/100.0)
    if m_pct_dec:
        p = float(next(g for g in m_pct_dec.groups() if g))
        return ParsedCommand(cmd="MUL", value=1.0 - p/100.0)

    # ADD / SUB
    m_add = re.search(r"(?:add|increase|افزایش|اضافه کن|ببر بالا)\s+(-?\d+)", t)
    m_sub = re.search(r"(?:subtract|decrease|کم کن|بیار پایین)\s+(-?\d+)", t)
    if m_add:
        return ParsedCommand(cmd="ADD", value=float(m_add.group(1)))
    if m_sub:
        return ParsedCommand(cmd="SUB", value=float(m_sub.group(1)))

    # MUL / DIV
    m_mul = re.search(r"(?:multiply|times|ضرب(?:در)?|x)\s+(?:by\s+)?([0-9]+(?:\.[0-9]+)?)", t)
    m_div = re.search(r"(?:divide|تقسیم(?: بر)?)\s+(?:by\s+)?([0-9]+(?:\.[0-9]+)?)", t)
    if m_mul:
        return ParsedCommand(cmd="MUL", value=float(m_mul.group(1)))
    if m_div:
        v = float(m_div.group(1))
        if v == 0:
            v = 1.0
        return ParsedCommand(cmd="DIV", value=v)

    # STATUS (فقط اگر عدد/فعل تنظیمی نبود)
    if not m_num and any(k in t for k in ["status", "what's the speed", "سرعت چنده", "rpm"]):
        return ParsedCommand(cmd="STATUS")

    return None

# --- LLM prompt ---
LLM_PROMPT_TEMPLATE = """\
You are a strict command parser for a motor controller.

Allowed commands (respond in JSON ONLY, single line):
- {{"cmd":"SET","value":<int>}}            # 1500 <= value <= 10000
- {{"cmd":"DOUBLE"}}
- {{"cmd":"ON"}} / {{"cmd":"OFF"}} / {{"cmd":"STATUS"}}
- {{"cmd":"ADD","value":<number>}}
- {{"cmd":"SUB","value":<number>}}
- {{"cmd":"MUL","value":<number>}}
- {{"cmd":"DIV","value":<number>}}

Also accept natural aliases and Farsi phrases:
- INCREASE/DECREASE ~ ADD/SUB
- e.g. "increase by 500", "add 300", "کم کن ۲۰۰", "ضربدر 1.2", "تقسیم بر 2"
- Percent: "increase 10%" => {{"cmd":"MUL","value":1.10}}
- If the text mentions a numeric target (e.g., "up to 2000", "speed to 2500"), prefer SET to that value.

Rules:
- Output exactly one minified JSON line with keys exactly as shown.
- Do not clamp math commands. Clamp for SET only (server will clamp).
- If ambiguous, choose the most likely single command.

User text: {user_text}
"""

def llm_parse_text_to_command(openai_client, user_text: str) -> ParsedCommand | None:
    prompt = LLM_PROMPT_TEMPLATE.format(user_text=user_text)
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        line = res.choices[0].message.content.strip()
        import json
        data = json.loads(line)  # {"cmd": "...", "value": ...}
        cmd = data.get("cmd")
        val = data.get("value", None)

        # هم‌معنی‌ها
        if cmd == "INCREASE":
            cmd = "ADD"
        if cmd == "DECREASE":
            cmd = "SUB"

        if cmd == "SET":
            if isinstance(val, (int, float)):
                v = int(val)
                v = max(1500, min(10000, v))
                return ParsedCommand(cmd="SET", value=v)
            return None

        if cmd in ("ADD", "SUB", "MUL", "DIV"):
            if isinstance(val, (int, float)):
                return ParsedCommand(cmd=cmd, value=float(val))
            return None

        if cmd in ("DOUBLE", "ON", "OFF", "STATUS"):
            return ParsedCommand(cmd=cmd)
    except Exception:
        return None
    return None
