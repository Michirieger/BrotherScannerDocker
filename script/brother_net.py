#!/usr/bin/env python3
"""Brother network scan protocol (TCP 54921), for hosts without Brother's driver.

Brother's SANE backend (brscan4) is published for i386/amd64 only. This module
speaks its network protocol directly, so the same Scan-to-PC session works on
any architecture: the device shows "connecting to PC" after a button press and
waits for exactly this connection. Written from captures of the protocol on
the wire (an MFC-L2710DW talking to brscan4) and checked against the images
Brother's backend produced from the same data: grayscale and black & white are
byte-identical, colour pixel-identical. Framing as also described for the USB
transport by https://github.com/dmikushin/brscan.

Session (every connection starts with the device's banner "+OK 200\\r\\n";
"-NG 401\\r\\n" means busy, try again shortly):

    ESC Q \\n 0x80                    status query, own connection (brscan4
                                     sends it first; the reply is not needed)
    ESC I \\n R=x,y \\n M=mode \\n 0x80  inquiry -> 0x00, u16le length, text
                                     "xres,yres,source,width_mm,width_px,
                                     height_mm,height_px," (source 1 feeder,
                                     2 glass; heights 0 for the feeder)
    ESC X \\n R= M= C= [J=] B= N= A=0,0,w,h S=NORMAL_SCAN P=0 \\n 0x80
                                     start the scan: the device then sends
                                     every page (the whole feeder, or the
                                     glass), each ended by 0x82, and 0x80

How this was written: by observing the traffic between the author's own
device and Brother's driver on the network (the behaviour the EU Software
Directive / German UrhG section 69d(3) allows to study), with no Brother code
decompiled or copied. Not affiliated with or endorsed by Brother Industries.

Blocks: a 10-byte header (byte 0 the type, byte 3 the page number), then for
data blocks a u16le length and the payload:

    0x40  one raw line                0x42  one PackBits-compressed line
    0x64  a piece of the page's JPEG (colour)
    0x82  page end (header only)      0x80  no more pages (a single byte)

The device sends only the lines the page really has; like Brother's backend
the page is padded to the requested height (white).
"""

import argparse
import io
import os
import socket
import struct
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

PORT = 54921
BANNER_OK = b"+OK 200"
BANNER_BUSY = b"-NG 401"

# mode -> (M=, C=, extra parameters, bits per pixel)
MODES: Dict[str, Tuple[str, str, Dict[str, str], int]] = {
    "color": ("CGRAY", "JPEG", {"J": "MID"}, 24),
    "gray": ("GRAY64", "RLENGTH", {}, 8),
    "lineart": ("TEXT", "RLENGTH", {}, 1),
}
MODE_NAMES = {
    "color": "color", "24bit color": "color", "24bit color[fast]": "color",
    "gray": "gray", "grayscale": "gray", "true gray": "gray", "gray[error diffusion]": "gray",
    "lineart": "lineart", "black & white": "lineart", "black and white": "lineart",
}

BLOCK_RAW_LINE = 0x40
BLOCK_PACKBITS_LINE = 0x42
BLOCK_JPEG = 0x64
BLOCK_PAGE_END = 0x82
STATUS_NO_MORE_PAGES = 0x80
HEADER_SIZE = 10
MAX_BLOCK = 0x10000


class ScanError(Exception):
    """The device refused or broke off the scan."""


class DeviceBusy(ScanError):
    """The device answered "-NG 401": busy, for example with another job."""


def mode_key(name: str) -> str:
    """Our mode key for a mode name (ours, eSCL-style or Brother's)."""
    key = MODE_NAMES.get(name.strip().lower())
    if key is None:
        raise ValueError(f"unknown scan mode {name!r}")
    return key


def command(cmd: bytes, params: Optional[List[Tuple[str, str]]] = None) -> bytes:
    """ESC <cmd> LF, one "K=V" LF per parameter, 0x80."""
    body = b"".join(f"{key}={value}\n".encode("ascii") for key, value in (params or []))
    return b"\x1b" + cmd + b"\n" + body + b"\x80"


def packbits_decode(data: bytes) -> bytes:
    """PackBits: n < 128 copies n+1 bytes, n > 128 repeats the next byte 257-n times, 128 is a no-op."""
    out = bytearray()
    i, size = 0, len(data)
    while i < size:
        n = data[i]
        i += 1
        if n < 128:
            out += data[i:i + n + 1]
            i += n + 1
        elif n > 128:
            if i >= size:
                raise ScanError("truncated PackBits run")
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


def parse_inquiry(text: bytes) -> Dict[str, int]:
    """The ESC I answer "xres,yres,source,width_mm,width_px,height_mm,height_px,"."""
    fields = [f for f in text.rstrip(b"\x00").decode("ascii", "replace").split(",")]
    numbers = [int(f) if f.strip().lstrip("-").isdigit() else 0 for f in fields[:7]]
    numbers += [0] * (7 - len(numbers))
    keys = ("xres", "yres", "source", "width_mm", "width_px", "height_mm", "height_px")
    return dict(zip(keys, numbers))


def scan_window(resolution: int, width_mm: float, height_mm: float, inquiry: Dict[str, int]) -> Tuple[int, int]:
    """Window in pixels: the width limited to what the device offers and, as
    Brother's backend does, a multiple of 16; the height limited to the glass
    when the device reports one."""
    width = int(width_mm / 25.4 * resolution)
    if inquiry.get("width_px"):
        width = min(width, inquiry["width_px"])
    width -= width % 16
    height = int(height_mm / 25.4 * resolution)
    if inquiry.get("height_px"):
        height = min(height, inquiry["height_px"])
    if width <= 0 or height <= 0:
        raise ValueError("empty scan window")
    return width, height


class Connection:
    """One TCP connection to the scan port; refuses anything but "+OK 200"."""

    def __init__(self, host: str, port: int = PORT, timeout: float = 30.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        banner = self._read_line()
        if banner.startswith(BANNER_BUSY):
            self.close()
            raise DeviceBusy("device busy (-NG 401)")
        if not banner.startswith(BANNER_OK):
            self.close()
            raise ScanError(f"unexpected greeting {banner[:40]!r}")

    def _read_line(self) -> bytes:
        line = bytearray()
        while not line.endswith(b"\r\n"):
            if len(line) > 64:
                break
            line += self.read(1)
        return bytes(line)

    def read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ScanError("device closed the connection")
            data += chunk
        return bytes(data)

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def drain(self, wait: float = 1.0) -> bytes:
        """Whatever the device sends within `wait` seconds (for replies of unknown length)."""
        self.sock.settimeout(wait)
        data = bytearray()
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return bytes(data)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def connect(host: str, port: int = PORT, attempts: int = 10, delay: float = 2.0, timeout: float = 30.0) -> Connection:
    """Connect, retrying while the device reports busy (as brscan4 does)."""
    for attempt in range(attempts):
        try:
            return Connection(host, port, timeout)
        except DeviceBusy:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise DeviceBusy("device busy")  # not reached


def query_status(host: str, port: int = PORT) -> bytes:
    """ESC Q on its own connection, as brscan4 opens every session."""
    conn = connect(host, port)
    try:
        conn.send(command(b"Q"))
        return conn.drain(1.5)
    finally:
        conn.close()


def read_page(conn: Connection, mode: str) -> Optional[Tuple[int, List[bytes], bytes]]:
    """One page: (page number, lines, jpeg). None when the device has no more pages."""
    lines: List[bytes] = []
    jpeg = bytearray()
    page = 0
    while True:
        kind = conn.read(1)[0]
        if kind == STATUS_NO_MORE_PAGES:
            if lines or jpeg:
                raise ScanError("scan ended in the middle of a page")
            return None
        if kind not in (BLOCK_RAW_LINE, BLOCK_PACKBITS_LINE, BLOCK_JPEG, BLOCK_PAGE_END):
            raise ScanError(f"scan ended by the device (status 0x{kind:02x})")
        header = bytes([kind]) + conn.read(HEADER_SIZE - 1)
        page = header[3]
        if kind == BLOCK_PAGE_END:
            return page, lines, bytes(jpeg)
        length = struct.unpack("<H", conn.read(2))[0]
        payload = conn.read(length)
        if kind == BLOCK_JPEG:
            jpeg += payload
        elif kind == BLOCK_PACKBITS_LINE:
            lines.append(packbits_decode(payload))
        else:
            lines.append(payload)


def write_pnm(path: str, mode: str, width: int, height: int, lines: List[bytes]) -> None:
    """Gray (P5) or black & white (P4) page, padded to `height` with white."""
    if mode == "gray":
        stride, white, magic = width, b"\xff", b"P5"
    else:
        stride, white, magic = (width + 7) // 8, b"\x00", b"P4"
    with open(path, "wb") as handle:
        handle.write(magic + f"\n{width} {height}\n".encode() + (b"255\n" if mode == "gray" else b""))
        for line in lines[:height]:
            handle.write(line[:stride].ljust(stride, white))
        for _ in range(max(0, height - len(lines))):
            handle.write(white * stride)


def write_color(path: str, width: int, height: int, jpeg: bytes) -> None:
    """Colour page: the JPEG as it came (.jpg), else a PPM padded to `height` (GraphicsMagick)."""
    if path.lower().endswith((".jpg", ".jpeg")):
        with open(path, "wb") as handle:
            handle.write(jpeg)
        return
    subprocess.run(
        ["gm", "convert", "jpeg:-", "-background", "white", "-extent", f"{width}x{height}", f"ppm:{path}"],
        input=jpeg, check=True,
    )


def scan(host: str, output_batch: str, mode: str = "color", resolution: int = 300,
         width_mm: float = 215.0, height_mm: float = 297.0, port: int = PORT,
         log=print) -> List[str]:
    """Scan every page the device offers (feeder, else glass) into output_batch % n.

    Returns the files written. Raises ScanError when the device has nothing to
    scan or breaks off.
    """
    key = mode_key(mode)
    m, compression, extra, _bits = MODES[key]
    query_status(host, port)
    conn = connect(host, port)
    files: List[str] = []
    try:
        conn.send(command(b"I", [("R", f"{resolution},{resolution}"), ("M", m)]))
        conn.read(1)  # 0x00
        length = struct.unpack("<H", conn.read(2))[0]
        inquiry = parse_inquiry(conn.read(length))
        width, height = scan_window(resolution, width_mm, height_mm, inquiry)
        source = {1: "feeder", 2: "glass"}.get(inquiry["source"], f"source {inquiry['source']}")
        log(f"  brother-net: {source}, {key} at {resolution} dpi, window {width}x{height} px")
        params = [("R", f"{resolution},{resolution}"), ("M", m), ("C", compression)]
        params += list(extra.items())
        params += [("B", "50"), ("N", "50"), ("A", f"0,0,{width},{height}"), ("S", "NORMAL_SCAN"), ("P", "0")]
        # one ESC X scans everything: every page in the feeder (or the glass),
        # each ended by 0x82, then 0x80
        conn.send(command(b"X", params))
        while True:
            page = read_page(conn, key)
            if page is None:
                break
            _number, lines, jpeg = page
            path = output_batch % (len(files) + 1) if "%" in output_batch else output_batch
            if key == "color":
                write_color(path, width, height, jpeg)
            else:
                write_pnm(path, key, width, height, lines)
            files.append(path)
            log(f"  brother-net: page {len(files)} ({len(lines) or 'jpeg'} lines) -> {path}")
            if "%" not in output_batch:
                break
    finally:
        conn.close()
    if not files:
        raise ScanError("nothing to scan (feeder empty)")
    return files


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Scan from a Brother device over its network protocol (TCP 54921).")
    parser.add_argument("--host", required=True)
    parser.add_argument("--mode", default="color")
    parser.add_argument("--resolution", type=int, default=300)
    parser.add_argument("--width-mm", type=float, default=215.0)
    parser.add_argument("--height-mm", type=float, default=297.0)
    parser.add_argument("--batch", required=True, help="output pattern, e.g. page%%04d.pnm")
    args = parser.parse_args(argv)
    try:
        scan(args.host, args.batch, args.mode, args.resolution, args.width_mm, args.height_mm)
    except (ScanError, OSError, ValueError) as error:
        print(f"brother-net: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
