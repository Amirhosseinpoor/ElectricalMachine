# app/serial_client.py
import serial
import threading
import time
from serial.serialutil import SerialException

class ArduinoSerial:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.lock = threading.Lock()

    # --- Private: open port if not open ---
    def _ensure_open(self, retries: int = 3, backoff: float = 0.8):
        if self.ser and self.ser.is_open:
            return
        last_err = None
        for i in range(retries):
            try:
                self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
                time.sleep(2)  # allow board reset
                try:
                    self.ser.reset_input_buffer()
                    self.ser.reset_output_buffer()
                except Exception:
                    pass
                return
            except Exception as e:
                last_err = e
                time.sleep(backoff * (i + 1))
        raise SerialException(f"could not open port {self.port}: {last_err}")

    def close(self):
        with self.lock:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass

    def send_command(self, line: str, read_response: bool = True, lines: int = 1) -> str:
        """Send one line and read up to 'lines' response lines."""
        with self.lock:
            self._ensure_open()
            self.ser.write((line.strip() + "\n").encode("utf-8"))
            if not read_response:
                return ""
            out = []
            t0 = time.time()
            while True:
                resp = self.ser.readline().decode(errors="ignore").strip()
                if resp:
                    out.append(resp)
                    if len(out) >= lines:
                        break
                if time.time() - t0 > (self.timeout + 1.0):
                    break
            return "\n".join(out)

    def status(self) -> dict:
        resp = self.send_command("STATUS")
        data = {"raw": resp}
        try:
            parts = resp.split(",")
            for p in parts:
                k, v = p.split(":")
                data[k] = int(v)
        except Exception:
            pass
        return data
