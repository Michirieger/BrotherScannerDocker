#!/usr/bin/env python3
"""eSCL (AirScan) scanning through the sane-airscan backend.

Brother publishes its SANE drivers (brscan4, brscan5) for i386/amd64 only, so
on arm64 hosts (Raspberry Pi, Home Assistant Green/Yellow, ...) the image
scans over eSCL instead: SCANNER_DRIVER=escl, or automatically when the
Brother drivers are not installed. The entrypoint configures sane-airscan with
the device at IPADDRESS (or ESCL_URL) in a private SANE configuration
directory, so `scanimage` keeps working unchanged and the whole pipeline (pnm
pages, PDF conversion, blank page removal, OCR, uploads) is shared with the
Brother drivers.

This module holds what differs between the drivers:
  * option names: the API, the settings and scantoocr.py speak the Brother
    names ("True Gray", "FlatBed", "Automatic Document Feeder(...)"),
    sane-airscan lists "Gray", "Flatbed", "ADF", ...; they are translated here;
  * the paper source: sane-airscan defaults to the flatbed, Brother's drivers
    take paper from the ADF when it is loaded. Without an explicit source the
    ADF is used when the device reports it loaded (eSCL ScannerStatus);
  * readiness: after a button press the device may still be busy with the
    Scan-to-PC dialog; a job is only started once the device reports Idle.
"""

import os
import re
import time
from typing import List, Optional
from xml.etree import ElementTree

SCANNER_DRIVER_PATH = "/tmp/SCANNER_DRIVER"
ESCL = "escl"

ESCL_FLATBED = "Flatbed"
ESCL_ADF = "ADF"
ESCL_ADF_DUPLEX = "ADF Duplex"

# Brother option values (brscan4/brscan5) -> sane-airscan option values
_MODE_MAP = {
    "true gray": "Gray",
    "gray": "Gray",
    "grayscale": "Gray",
    "gray[error diffusion]": "Gray",
    "24bit color": "Color",
    "24bit color[fast]": "Color",
    "color": "Color",
    "black & white": "Lineart",
    "black and white": "Lineart",
    "lineart": "Lineart",
}


def active_driver(path: str = SCANNER_DRIVER_PATH) -> str:
    """The driver the entrypoint selected (brscan4, brscan5 or escl)."""
    try:
        with open(path) as handle:
            return handle.read().strip().lower()
    except OSError:
        return ""


def is_escl(path: str = SCANNER_DRIVER_PATH) -> bool:
    return active_driver(path) == ESCL


def map_mode(value: str) -> str:
    """Brother scan mode name -> sane-airscan name; unknown values pass through."""
    return _MODE_MAP.get(value.strip().lower(), value)


def map_source(value: str) -> str:
    """Brother paper source name -> sane-airscan name; unknown values pass through."""
    text = value.strip().lower()
    if "flatbed" in text or text == "platen":
        return ESCL_FLATBED
    if "duplex" in text:
        return ESCL_ADF_DUPLEX
    if "feeder" in text or text == "adf":
        return ESCL_ADF
    return value


def translate_args(args: List[str]) -> List[str]:
    """scanimage arguments with --mode/--source values translated for sane-airscan."""
    translated = []
    for arg in args:
        if arg.startswith("--mode="):
            arg = "--mode=" + map_mode(arg[len("--mode="):])
        elif arg.startswith("--source="):
            arg = "--source=" + map_source(arg[len("--source="):])
        translated.append(arg)
    return translated


def escl_url() -> str:
    """Base URL of the device's eSCL service: ESCL_URL, else http://IPADDRESS/eSCL."""
    url = os.environ.get("ESCL_URL", "").strip()
    if url:
        return url.rstrip("/")
    return f"http://{os.environ.get('IPADDRESS', '').strip()}/eSCL"


def parse_status(xml_text: str) -> dict:
    """State and AdfState from an eSCL ScannerStatus document (namespaces ignored)."""
    status = {"state": None, "adf": None}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return status
    for element in root.iter():
        tag = re.sub(r"^\{.*\}", "", element.tag)
        if tag == "State" and status["state"] is None:
            status["state"] = (element.text or "").strip()
        elif tag == "AdfState":
            status["adf"] = (element.text or "").strip()
    return status


def scanner_status(timeout: float = 5.0) -> Optional[dict]:
    """The device's ScannerStatus, or None when it cannot be read."""
    try:
        import requests  # in the image (requirements.txt); imported late for the tests
        response = requests.get(f"{escl_url()}/ScannerStatus", timeout=timeout)
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001 - any failure means "unknown"
        print(f"  eSCL: could not read ScannerStatus: {error}")
        return None
    return parse_status(response.text)


def adf_loaded(status: Optional[dict]) -> bool:
    return bool(status and status.get("adf") == "ScannerAdfLoaded")


def wait_until_idle(timeout_s: Optional[float] = None, poll_s: float = 1.0) -> Optional[dict]:
    """Poll ScannerStatus until State is Idle (or the timeout passes) and return the last status.

    ESCL_IDLE_TIMEOUT seconds, default 30. A device that cannot be read is not
    waited for: sane-airscan reports the real error when it opens the device.
    """
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("ESCL_IDLE_TIMEOUT", "30"))
        except ValueError:
            timeout_s = 30.0
    deadline = time.monotonic() + timeout_s
    status = scanner_status()
    while status is not None and status.get("state") not in (None, "Idle"):
        if time.monotonic() >= deadline:
            print(f"  eSCL: device still {status.get('state')} after {timeout_s:g}s, scanning anyway")
            break
        time.sleep(poll_s)
        status = scanner_status()
    return status


def default_source(status: Optional[dict]) -> str:
    """ADF when paper is loaded there, else the flatbed (Brother driver behaviour)."""
    return ESCL_ADF if adf_loaded(status) else ESCL_FLATBED


def int_setting(name: str, default: int) -> int:
    """A non-negative whole number from the environment, else `default`."""
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value >= 0 else default


def settle_after_button() -> None:
    """Give the device ESCL_BUTTON_DELAY seconds (default 5) after a button press.

    The Scan-to-PC press puts the device into its "connecting to PC" state;
    an eSCL job that arrives in the same moment is cancelled at the device.
    Button-started scans are recognised by the event openskey hands on
    (SKEY_FUNC); API scans start at once.
    """
    if not os.environ.get("SKEY_FUNC", "").strip():
        return
    delay = int_setting("ESCL_BUTTON_DELAY", 5)
    if delay:
        print(f"  eSCL: button press, waiting {delay}s for the device before scanning")
        time.sleep(delay)
