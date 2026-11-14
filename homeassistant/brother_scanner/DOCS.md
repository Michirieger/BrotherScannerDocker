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

## Several people: scan targets

Normally the scanner lists one Scan-to-PC destination (`name`, "Scanner") and
every scan lands in `copy_to_dir`. With `scan_targets` the scanner lists one
destination **per entry**, e.g. one per person in a household, and every
destination has its **own folder**:

```yaml
scan_targets:
  - name: PersonA
    copy_to_dir: /share/paperless/consume/person_a
  - name: PersonB
    copy_to_dir: /share/paperless/consume/person_b
```

On the scanner: **Scan → File → pick your name → Start**. The PDF is made as
usual and then copied to that name's folder.

- **Names:** 1-15 letters, digits, `_` or `-` (what the panel shows). An entry
  without `copy_to_dir` uses the add-on's `copy_to_dir`.
- **One scan, one owner:** the name is stored with the scan. The rear pages
  (**E-mail**) or finishing early (**Image**, see below) complete the scan of
  whoever scanned the front pages, under whichever name they are pressed.
- **Scans from Home Assistant or the web UI** have no name and go to
  `copy_to_dir`.
- The folders are created when the add-on starts.

### With Paperless-ngx

Paperless-ngx then sees one subfolder per person. Let it assign the owner:

1. In the Paperless-ngx add-on enable `consumer_recursive` (reads subfolders).
2. In Paperless-ngx add a workflow per person: trigger **Consumption
   started**, path filter `*/consume/person_b/*`, action **assign owner**
   (and permissions if the documents should stay private).

## Single-sided documents without waiting

After the front pages every scan waits `rear_page_wait_seconds` for a rear
scan (manual duplex: turn the stack, scan with **E-mail**). To go on at once:

- the **Finish without rear pages** button of the Home Assistant integration,
  or `POST /api/scanner/finish`;
- with `image_button: finish`, **Scan → Image → Start** on the scanner, then
  **Stop**: the panel waits for a scan that does not come.

## Requirements

- The scanner must reach Home Assistant on UDP 54925 (button presses), i.e. be in the same network.
- For Paperless-ngx: the add-on must be running; it consumes and removes the
  files from `/share/paperless/consume`.
