# Brother Scanner

Scan with the **Scan** button on a Brother printer/scanner and get a PDF in a
folder of Home Assistant, by default the consume folder of the Paperless-ngx
add-on (`/share/paperless/consume`).

## How it works

- **Buttons:** [openskey](https://github.com/PhilippMundhenk/openskey) registers
  this host on the device as a "Scan to PC" destination (SNMP) and receives the
  button presses (UDP 54925).
- **Scanning:** on aarch64 (Raspberry Pi, Home Assistant Green/Yellow), where
  Brother publishes no drivers, the add-on speaks Brother's network scan
  protocol itself (`brother-net`): a button press works as with Brother's
  driver. On amd64 the Brother drivers are used when they know the model
  (`scanner_driver: auto`). `escl` (eSCL/AirScan) is the fallback.
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
| `scanner_driver` | `auto`, `brother-net`, `escl`, `brscan4`, `brscan5` |
| `copy_to_dir` | Folder that receives every finished PDF; empty to disable |
| `scan_targets` | Several names on the scanner panel, each with its own folder, e.g. one per person (see below) |
| `image_button` | `finish`: the **Image** button on the scanner finishes the current scan at once instead of waiting for rear pages (see below) |
| `resolution` | dpi |
| `scan_mode` / `scan_source` | Optional, e.g. `True Gray` / `FlatBed` (Brother names work with eSCL too) |
| `rear_page_wait_seconds` | How long a front-side scan waits for the rear pages (manual duplex) before the PDF is made; `0` for no wait |
| `webserver` / `port` | Web UI and REST API (used by the `ha-brother-scanner` integration) |
| `api_token` | Optional bearer token for the API |

Every other setting of BrotherScannerDocker (README) can be set in the web
UI's settings page.

## Several people

With `scan_targets` every person gets their own entry in the scanner's
Scan-to-PC list and their own folder:

```yaml
scan_targets:
  - name: PersonA
    copy_to_dir: /share/paperless/consume/person_a
  - name: PersonB
    copy_to_dir: /share/paperless/consume/person_b
```

On the scanner: Scan → File → pick your name → Start. One openskey daemon
registers all names. In Paperless-ngx enable `consumer_recursive` and add a
workflow per folder ("Consumption started", path filter
`*/consume/person_b/*`) that assigns the owner. Scans started from the web UI
or the Home Assistant buttons go to `copy_to_dir`.

## Single-sided documents without waiting

After the front pages every scan waits `rear_page_wait_seconds` for a rear
scan (manual duplex: turn the stack, scan with **E-mail**). To go on at once:

- the **Finish without rear pages** button of the Home Assistant integration,
  or `POST /api/scanner/finish`;
- with `image_button: finish`, the **Image** button on the scanner. The panel
  then waits for a scan that does not come; press Cancel (Stop) and scan the
  next document.

## Requirements

- The scanner must reach Home Assistant on UDP 54925 (and 54926, ... for further `scan_targets`), i.e. be in the same network.
- For Paperless-ngx: the add-on must be running; it consumes and removes the
  files from `/share/paperless/consume`.
