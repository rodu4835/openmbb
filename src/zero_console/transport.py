"""Serial transport, session logging, and settings-dump parsing.

Works over real pyserial or the simulator (sim.SimPort) — anything with
read/write/close/in_waiting. Enforces the safety blocklist on every send and
saves every command response to its own session file.
"""

import datetime as _dt
import os
import re
import threading
import time

from .safety import BlockedCommandError, command_blocked

BAUD = 38400
NEWLINE = b"\r\n"
PROMPT_RE = re.compile(rb"ZERO MBB>\s*$|MBB>\s*$")

READ_COMMANDS = ["version", "help", "status", "stats", "runtime", "bms",
                 "sevcon", "chargers", "inputs", "outputs", "dash"]
DUMP_COMMANDS = ["eventlogdump", "errorlogdump", "dumplogs"]


class SessionLogger:
    def __init__(self, base_dir=None, tag="session"):
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        # sessions always live in a self-contained "zero-console-sessions"
        # folder under the chosen base (or cwd), so picking any directory never
        # scatters timestamped folders loose into it.
        root = os.path.join(base_dir or os.getcwd(), "zero-console-sessions")
        self.dir = os.path.join(root, "%s_%s" % (stamp, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.raw_path = os.path.join(self.dir, "session_raw.log")
        self.journal_path = os.path.join(self.dir, "writes_journal.txt")
        self._seq = 0
        self._lock = threading.Lock()

    def _ts(self):
        return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def raw(self, direction, data):
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        with self._lock, open(self.raw_path, "a", encoding="utf-8") as f:
            for line in text.splitlines(keepends=True):
                f.write("[%s] %s %r\n" % (self._ts(), direction, line))

    def save_command(self, cmd, response):
        with self._lock:
            self._seq += 1
            seq = self._seq
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cmd)[:40] or "cmd"
        path = os.path.join(self.dir, "%03d_%s.txt" % (seq, slug))
        with open(path, "w", encoding="utf-8") as f:
            f.write("# command: %s\n# time: %s\n\n%s" % (cmd, self._ts(), response))
        return path

    def save_named(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def journal_write(self, name, old, new, ok):
        line = "%s | %s | %s -> %s | %s\n" % (
            _dt.datetime.now().isoformat(timespec="seconds"),
            name, old, new, "VERIFIED" if ok else "UNVERIFIED")
        with self._lock, open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)


class Transport:
    """Sends commands, streams responses with idle-timeout, enforces blocklist."""

    def __init__(self, port, logger):
        self.port = port          # object with .read(n)/.write(bytes)/.in_waiting
        self.logger = logger
        self.lock = threading.Lock()
        self.last_saved_path = None

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass

    def _drain(self, quiet=0.3, max_time=2.0):
        end = time.time() + max_time
        buf = b""
        last = time.time()
        while time.time() < end:
            chunk = self.port.read(4096)
            if chunk:
                buf += chunk
                last = time.time()
            elif time.time() - last > quiet:
                break
            else:
                time.sleep(0.02)
        if buf:
            self.logger.raw("RX", buf)
        return buf

    def listen(self, seconds):
        with self.lock:
            return self._drain(quiet=seconds, max_time=seconds)

    def send_raw_newline(self):
        with self.lock:
            self.logger.raw("TX", NEWLINE)
            self.port.write(NEWLINE)
            return self._drain(quiet=0.6, max_time=3.0)

    def exec_command(self, cmd, idle_timeout=2.0, max_time=30.0, progress_cb=None):
        reason = command_blocked(cmd)
        if reason:
            raise BlockedCommandError(reason)
        with self.lock:
            self._drain(quiet=0.1, max_time=0.3)  # flush stale bytes
            wire = cmd.encode("ascii", errors="replace") + NEWLINE
            self.logger.raw("TX", wire)
            self.port.write(wire)
            buf = b""
            start = time.time()
            last = time.time()
            while True:
                chunk = self.port.read(4096)
                now = time.time()
                if chunk:
                    buf += chunk
                    last = now
                    if progress_cb:
                        progress_cb(len(buf))
                else:
                    got_any = len(buf) > 0
                    if got_any and PROMPT_RE.search(buf[-24:]) and now - last > 0.15:
                        break
                    if got_any and now - last > idle_timeout:
                        break
                    if not got_any and now - start > max(5.0, idle_timeout):
                        break
                    time.sleep(0.02)
                if now - start > max_time:
                    break
        self.logger.raw("RX", buf)
        text = buf.decode("utf-8", errors="replace")
        # strip our own echo and the trailing prompt
        lines = text.splitlines()
        if lines and lines[0].strip().endswith(cmd):
            lines = lines[1:]
        while lines and PROMPT_RE.search(lines[-1].encode()):
            lines = lines[:-1]
        result = "\n".join(lines).strip("\n")
        # spec: every command response gets its own file (transport-level, so
        # headless/scripted use is covered too, not just the GUI)
        self.last_saved_path = self.logger.save_command(cmd, result)
        return result


def parse_settings_dump(text):
    """Tolerant parser for the `set` menu. Returns (dict name -> info, order).

    Known line shapes (2014-2016 firmware; rev 41 may differ - stay tolerant):
      spfront         - Sprocket Teeth Front        :   28
      model           - Model Name                                  DSR
      maxcustsp       - Max Custom Speed            : 98 MPH ( 158 KPH ) ...
    """
    settings = {}
    order = []
    for line in text.splitlines():
        m = re.match(r"^\s{0,4}([A-Za-z_][A-Za-z0-9_]*)\s+-\s+(.*)$", line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).rstrip()
        if name in ("help",):  # avoid swallowing menu screens
            continue
        if ":" in rest:
            desc, _, value = rest.partition(":")
            desc, value = desc.strip(), value.strip()
        else:
            parts = rest.rsplit(None, 1)
            if len(parts) == 2:
                desc, value = parts[0].strip(), parts[1].strip()
            else:
                desc, value = rest, ""
        if name not in settings:
            order.append(name)
        settings[name] = {"desc": desc, "value": value, "raw": line.rstrip()}
    return settings, order


def first_number(value):
    m = re.search(r"-?\d+", str(value))
    return m.group(0) if m else str(value).strip()


def open_real_port(port_name):
    import serial  # lazy: only needed for real hardware
    return serial.Serial(port=port_name, baudrate=BAUD, bytesize=8,
                         parity="N", stopbits=1, timeout=0.05)


def list_serial_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []
