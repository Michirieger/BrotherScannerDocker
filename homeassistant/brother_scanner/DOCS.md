# Brother Scanner

Scan with the **Scan** button on a Brother printer/scanner and get a PDF in a
folder of Home Assistant, by default the consume folder of the Paperless-ngx
add-on (`/share/paperless/consume`).

## How it works

- **Buttons:** [openskey](https://github.com/PhilippMundhenk/openskey) registers
  this host on the device as a "Scan to PC" destination (SNMP) and receives the
  button presses (UDP 54925).
- **Scanning:** on aarch64 (Raspberry Pi, Home Assistant Green/Yellow) the
  device is scanned over eSCL/AirScan with sane-airscan, because Brother
  publishes its scanner drivers for x86 only. On amd64 the Brother drivers are
  used when they know the model (`scanner_driver: auto`), or eSCL when you
  choose `escl`.
- **Output:** pages are combined to one PDF, kept in the add-on, and copied to
  `copy_to_dir` (written under a temporary name and renamed, so Paperless never
  picks up a partial file).

## Options

| Option | Meaning |
| --- | --- |
| `ipaddress` | IP address of the scanner (required) |
| `host_ipaddress` | Address of Home Assistant the scanner sends button events to; detected when empty |
| `model` | Model name, e.g. `MFC-L2710DW` |
| `name` | Name shown on the scanner display (no spaces) |
| `scanner_driver` | `auto`, `escl`, `brscan4`, `brscan5` |
| `copy_to_dir` | Folder that receives every finished PDF; empty to disable |
| `resolution` | dpi |
| `scan_mode` / `scan_source` | Optional, e.g. `True Gray` / `FlatBed` (Brother names work with eSCL too) |
| `rear_page_wait_seconds` | How long a front-side scan waits for the rear pages (manual duplex) before the PDF is made; `0` for no wait |
| `webserver` / `port` | Web UI and REST API (used by the `ha-brother-scanner` integration) |
| `api_token` | Optional bearer token for the API |

Every other setting of BrotherScannerDocker (README) can be set in the web
UI's settings page.

## Requirements

- The scanner must reach Home Assistant on UDP 54925 (same network).
- For Paperless-ngx: the add-on must be running; it consumes and removes the
  files from `/share/paperless/consume`.
