# Dockerized Brother Scanner

A scan server for network Brother scanners in a Docker container, so it also runs on a NAS such as a Synology DiskStation. The scanner's shortcut buttons, a web UI and a REST API start scans; the pages land as PDFs on a mounted volume, optionally with blank pages removed, OCR'd by a separate service, uploaded by FTPS and announced by Telegram. Duplex documents work on simplex ADFs: scan the front pages, turn the stack, scan the rear pages, and both sides are merged into one PDF. It runs on amd64 and arm64 (Raspberry Pi, Home Assistant), and several people can share one scanner, each with their own destination on the panel and their own folder.

![The web UI](doc/gui-main.png)

## Quick start

```yaml
services:
  brother-scanner:
    image: ghcr.io/philippmundhenk/brotherscannerdocker:v2
    volumes:
      - /path/on/host:/scans
    ports:
      - 54925:54925/udp # mandatory, for scanner tools
      - 54921:54921 # mandatory, for scanner tools
      - 161:161/udp # mandatory, for scanner tools
      - 8080:8080 # web UI and API
    environment:
      - NAME=Scanner
      - MODEL=MFC-L2700DW
      - IPADDRESS=192.168.1.10 # the scanner
      - HOST_IPADDRESS=192.168.1.20 # this host, as the scanner reaches it
      - UID=1000 # owner of the scans on the volume
      - GID=1000
      - TZ=Europe/Berlin
      - WEBSERVER=true
      - PORT=8080
    restart: unless-stopped
```

The scanner then shows "Scanner" as a Scan-to-PC destination, and the web UI is at `http://<host>:8080`. The `v2` tag follows the newest 2.x release; `stable` is the newest release overall, `latest` the development build (see [image tags](doc/setup.md#image-tags)). Every further setting is optional; see [Setup](doc/setup.md) for the host-networking variant, permissions and a full example, and [Configuration](doc/configuration.md) for all options.

## Several people, Paperless-ngx

`SCAN_TARGETS` puts several destinations into the scanner's Scan-to-PC list, e.g. one per person, each with its own folder. `COPY_TO_DIR` hands every finished PDF to a folder such as the consume folder of [Paperless-ngx](https://docs.paperless-ngx.com/) (written under a temporary name, so the consumer never sees a partial file):

```yaml
    environment:
      - SKEY_DAEMON=openskey # needed for several names
      - SCAN_TARGETS=PersonA=/consume/person_a;PersonB=/consume/person_b
      - COPY_TO_DIR=/consume # scans from the web UI and the API
    volumes:
      - /path/to/paperless/consume:/consume
```

On the scanner: **Scan → File → PersonB → Start**, and the PDF lands in `person_b`. For single-sided documents a scan can be finished at once instead of waiting for rear pages (`POST /api/scanner/finish`, or the Image button with `IMAGE_BUTTON=finish`). Details, and how Paperless-ngx assigns the owner per folder: [Several people: scan targets](doc/setup.md#several-people-scan-targets).

## Raspberry Pi and other arm64 hosts

Brother publishes its scanner drivers for amd64 only. On arm64 the image speaks Brother's network scan protocol itself (`SCANNER_DRIVER=brother-net`, chosen automatically) and uses [openskey](https://github.com/PhilippMundhenk/openskey) for the buttons, so the scanner's buttons, the web UI and the API work as on amd64. eSCL/AirScan is available as a fallback (`SCANNER_DRIVER=escl`). See [Scanner driver](doc/setup.md#scanner-driver). A Home Assistant add-on is in [`homeassistant/brother_scanner`](homeassistant/brother_scanner/DOCS.md).

## Migrating from v1 to v2

- **Settings can live in a file and a settings page.** Everything that used to be an environment variable can also be a `KEY=VALUE` line in `/scans/.config/scanner.conf` on the volume, and with `ALLOW_GUI_SETTINGS=true` the web UI gets a settings page that edits that file. Nothing has to change: your compose file keeps working as it is.
- **The environment wins.** A value set in the compose file is shown read-only on the settings page with an `environment` badge. To manage it on the page instead, press "Copy environment values into the file" there, remove the entry from the compose file and restart.
- **Web UI and API are new.** The old `scan.php` and `active.php` still answer (deprecated); `list.php` and `download.php` are gone. The REST API with jobs, parameters and status is described in [Web UI and API](doc/web-ui.md).
- **OCR goes through a queue**, uploads never run while the scanner is busy, and an OCR result is validated before the original may be removed.

Details and the rest of the changes: [Migration guide](doc/migration.md).

## Documentation

- [Setup](doc/setup.md): bridged or host networking, permissions, scanner buttons, several people (scan targets), handing scans to Paperless-ngx, scanner driver (brscan4, brscan5, brother-net, eSCL), the button daemon (Brother's `brscan-skey` or the open [openskey](https://github.com/PhilippMundhenk/openskey)), duplex, calibration, file sizes, full compose example
- [Configuration](doc/configuration.md): every option, the configuration file, the settings page, precedence, secrets
- [Web UI and API](doc/web-ui.md): the pages, thumbnails, the REST API, the API token, deprecated endpoints
- [OCR](doc/ocr.md): the OCR service, the queue, validation of results
- [Uploads and notifications](doc/notifications.md): FTPS upload, inotify over SSH, Telegram
- [Home Assistant](doc/home-assistant.md): the integration and the dashboard card, or the built-in `rest` platform
- [Customizing the scan scripts](doc/customizing.md)
- [Development and testing](doc/development.md)
- [Migration guide](doc/migration.md)
- [Changelog](CHANGELOG.md)

This is an independent project, not affiliated with Brother Industries, Ltd.; the Brother driver packages are downloaded from Brother at image build time (amd64). `brother-net` was written from network captures of the author's own device, without Brother code.
