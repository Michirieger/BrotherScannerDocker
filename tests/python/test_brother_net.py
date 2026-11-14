"""script/brother_net.py: Brother's network scan protocol, against a simulated device.

The fake device speaks the protocol as observed on the wire (banner, ESC Q,
ESC I, ESC X, line and JPEG blocks, page end 0x82, end 0x80, "-NG 401" when
busy) with synthetic page data, so no real scans are needed.
"""
import io
import socket
import struct
import subprocess
import threading

import pytest

import brother_net
import escl
import scanner


def packbits_encode(data: bytes) -> bytes:
    """Simple PackBits encoder (runs of 3+ equal bytes, literals otherwise)."""
    out, i = bytearray(), 0
    while i < len(data):
        run = 1
        while i + run < len(data) and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 3:
            out += bytes([257 - run, data[i]])
            i += run
            continue
        start = i
        while i < len(data) and i - start < 128:
            if i + 2 < len(data) and data[i] == data[i + 1] == data[i + 2]:
                break
            i += 1
        out += bytes([i - start - 1]) + data[start:i]
    return bytes(out)


def block(kind: int, page: int, payload: bytes = b"") -> bytes:
    header = bytes([kind, 0x07, 0x00, page, 0x00, 0x84, 0, 0, 0, 0])
    if kind == brother_net.BLOCK_PAGE_END:
        return header
    return header + struct.pack("<H", len(payload)) + payload


class FakeDevice:
    """Serves connections one after the other from a list of behaviours."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.commands = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _read_command(self, conn) -> bytes:
        data = bytearray()
        while not data.endswith(b"\x80"):
            chunk = conn.recv(1)
            if not chunk:
                break
            data += chunk
        self.commands.append(bytes(data))
        return bytes(data)

    def _run(self):
        for session in self.sessions:
            conn, _ = self.server.accept()
            try:
                session(self, conn)
            finally:
                conn.close()

    def close(self):
        self.server.close()


def busy(device, conn):
    conn.sendall(b"-NG 401\r\n")


def status(device, conn):
    conn.sendall(b"+OK 200\r\n")
    device._read_command(conn)
    conn.sendall(b"\xc1\x00\x49" + b"\x00" * 72)


def scanning(pages, source=1, width_px=1652, kind=brother_net.BLOCK_PACKBITS_LINE, height_px=0):
    """A scan connection that delivers `pages` (lists of lines, or JPEG bytes)."""
    def run(device, conn):
        conn.sendall(b"+OK 200\r\n")
        device._read_command(conn)  # ESC I
        text = f"200,200,{source},209,{width_px},{291 if height_px else 0},{height_px},".encode() + b"\x00"
        conn.sendall(b"\x00" + struct.pack("<H", len(text)) + text)
        device._read_command(conn)  # ESC X
        stream = bytearray()
        for number, page in enumerate(pages, start=1):
            if isinstance(page, bytes):  # JPEG
                for i in range(0, len(page), 1000):
                    stream += block(brother_net.BLOCK_JPEG, number, page[i:i + 1000])
            else:
                for line in page:
                    if kind == brother_net.BLOCK_PACKBITS_LINE:
                        stream += block(kind, number, packbits_encode(line))
                    else:
                        stream += block(kind, number, line)
            stream += block(brother_net.BLOCK_PAGE_END, number)
        stream += bytes([brother_net.STATUS_NO_MORE_PAGES])
        conn.sendall(bytes(stream))
        try:
            conn.recv(16)
        except OSError:
            pass
    return run


def read_pnm(path):
    data = open(path, "rb").read()
    parts = data.split(b"\n", 3)
    magic, size = parts[0], parts[1].split()
    width, height = int(size[0]), int(size[1])
    body = parts[3] if magic == b"P5" else b"\n".join(parts[2:])
    return magic, width, height, body


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(brother_net.time, "sleep", lambda _s: None)


# --- pure functions ---------------------------------------------------------

def test_packbits_decode_matches_the_encoder():
    for data in (b"", b"\xff" * 300, bytes(range(256)) * 3, b"ab" * 50 + b"\x00" * 7):
        assert brother_net.packbits_decode(packbits_encode(data)) == data
    assert brother_net.packbits_decode(b"\x80\x01ab") == b"ab"  # 128 is a no-op
    with pytest.raises(brother_net.ScanError):
        brother_net.packbits_decode(b"\xfe")


def test_command_format():
    assert brother_net.command(b"Q") == b"\x1bQ\n\x80"
    assert brother_net.command(b"I", [("R", "300,300"), ("M", "CGRAY")]) == b"\x1bI\nR=300,300\nM=CGRAY\n\x80"


def test_inquiry_and_window():
    feeder = brother_net.parse_inquiry(b"300,300,1,209,2479,0,0,\x00")
    assert feeder == {"xres": 300, "yres": 300, "source": 1, "width_mm": 209, "width_px": 2479,
                      "height_mm": 0, "height_px": 0}
    # width capped to the device and a multiple of 16, like Brother's backend
    assert brother_net.scan_window(300, 215, 297, feeder) == (2464, 3507)
    glass = brother_net.parse_inquiry(b"200,200,2,209,1652,291,2291,\x00")
    assert brother_net.scan_window(200, 215, 297, glass) == (1648, 2291)


@pytest.mark.parametrize("name, key", [
    ("24bit Color", "color"), ("Color", "color"), ("True Gray", "gray"), ("Gray", "gray"),
    ("Black & White", "lineart"), ("Lineart", "lineart"),
])
def test_mode_names(name, key):
    assert brother_net.mode_key(name) == key


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        brother_net.mode_key("sepia")


# --- sessions against the fake device ----------------------------------------

def test_gray_pages_from_the_feeder(tmp_path, no_wait):
    width = 1648
    page1 = [bytes([i % 256]) * width for i in range(40)]
    page2 = [bytes(range(256)) * 6 + bytes(width - 1536) for _ in range(30)]
    device = FakeDevice([status, scanning([page1, page2])])
    files = brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.pnm"), "True Gray", 200,
                             215, 297, port=device.port, log=lambda m: None)
    assert [f.split("/")[-1] for f in files] == ["p01.pnm", "p02.pnm"]
    magic, w, h, body = read_pnm(files[0])
    assert (magic, w, h) == (b"P5", width, int(297 / 25.4 * 200))
    assert body[: 40 * width] == b"".join(page1)
    assert set(body[40 * width:]) == {255}, "padded with white to the window"
    assert read_pnm(files[1])[3][: 30 * width] == b"".join(page2)
    assert device.commands[0] == b"\x1bQ\n\x80"
    assert device.commands[1].startswith(b"\x1bI\nR=200,200\nM=GRAY64\n")
    assert b"C=RLENGTH\n" in device.commands[2] and b"A=0,0,1648," in device.commands[2]
    assert len(device.commands) == 3, "one ESC X scans every page"
    device.close()


def test_raw_lines_and_black_and_white(tmp_path, no_wait):
    stride = 1648 // 8
    lines = [bytes([0b10101010]) * stride for _ in range(10)]
    device = FakeDevice([status, scanning([lines], kind=brother_net.BLOCK_RAW_LINE)])
    files = brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.pnm"), "Black & White", 200,
                             215, 297, port=device.port, log=lambda m: None)
    magic, w, h, body = read_pnm(files[0])
    assert (magic, w) == (b"P4", 1648)
    assert body[: 10 * stride] == b"".join(lines)
    assert set(body[10 * stride:]) == {0}, "P4 white is 0"
    assert b"M=TEXT\n" in device.commands[2]
    device.close()


def test_color_jpeg_is_reassembled(tmp_path, no_wait):
    jpeg = b"\xff\xd8" + bytes(range(256)) * 20 + b"\xff\xd9"
    device = FakeDevice([status, scanning([jpeg], source=2, height_px=2291)])
    files = brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.jpg"), "Color", 200,
                             215, 297, port=device.port, log=lambda m: None)
    assert open(files[0], "rb").read() == jpeg
    assert b"C=JPEG\nJ=MID\n" in device.commands[2]
    assert b"A=0,0,1648,2291\n" in device.commands[2], "limited to the glass"
    device.close()


def test_busy_device_is_retried(tmp_path, no_wait):
    device = FakeDevice([busy, status, busy, busy, scanning([[b"\x00" * 1648]])])
    files = brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.pnm"), "Gray", 200,
                             215, 297, port=device.port, log=lambda m: None)
    assert len(files) == 1
    device.close()


def test_nothing_to_scan(tmp_path, no_wait):
    device = FakeDevice([status, scanning([])])
    with pytest.raises(brother_net.ScanError, match="nothing to scan"):
        brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.pnm"), "Gray", 200,
                         215, 297, port=device.port, log=lambda m: None)
    device.close()


def test_cancel_at_the_device(tmp_path, no_wait):
    def cancelled(device, conn):
        conn.sendall(b"+OK 200\r\n")
        device._read_command(conn)
        text = b"200,200,1,209,1652,0,0,\x00"
        conn.sendall(b"\x00" + struct.pack("<H", len(text)) + text)
        device._read_command(conn)
        conn.sendall(block(brother_net.BLOCK_PACKBITS_LINE, 1, packbits_encode(b"\x00" * 1648)) + b"\x83")
    device = FakeDevice([status, cancelled])
    with pytest.raises(brother_net.ScanError, match="0x83"):
        brother_net.scan("127.0.0.1", str(tmp_path / "p%02d.pnm"), "Gray", 200,
                         215, 297, port=device.port, log=lambda m: None)
    device.close()


# --- the scan pipeline --------------------------------------------------------

def test_scan_cmd_uses_brother_net(clean_env, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(escl, "active_driver", lambda path=None: "brother-net")
    monkeypatch.setattr(brother_net, "scan", lambda *a, **k: calls.append((a, k)) or ["x"])
    clean_env.setenv("IPADDRESS", "192.0.2.10")
    clean_env.setenv("RESOLUTION", "200")
    scanner.scan_cmd(io.StringIO(), None, str(tmp_path / "p%04d.pnm"), ["--mode=True Gray", "--source=FlatBed"])
    (host, batch, mode, resolution, width, height), _ = calls[0]
    assert (host, mode, resolution, width, height) == ("192.0.2.10", "True Gray", 200, 215.0, 297.0)


def test_scan_cmd_reports_failures_like_scanimage(clean_env, monkeypatch, tmp_path):
    def fail(*a, **k):
        raise brother_net.ScanError("nothing to scan (feeder empty)")
    monkeypatch.setattr(escl, "active_driver", lambda path=None: "brother-net")
    monkeypatch.setattr(brother_net, "scan", fail)
    with pytest.raises(subprocess.CalledProcessError):
        scanner.scan_cmd(io.StringIO(), None, str(tmp_path / "p%04d.pnm"), [])
