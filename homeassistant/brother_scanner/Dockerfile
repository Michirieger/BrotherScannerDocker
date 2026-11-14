ARG BUILD_FROM
FROM ${BUILD_FROM}

# The image's health check probes PORT (default 80); in the add-on the port is
# an option, so read it from /data/options.json. The Supervisor reports the
# add-on as started once this passes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl --fail --silent --max-time 5 \
  "http://127.0.0.1:$(python3 -c 'import json; print(json.load(open("/data/options.json")).get("port", 8080))')/api/scanner/status" >/dev/null || exit 1

# runScanner.sh exports the add-on options (/data/options.json) itself.
CMD ["bash", "-c", "/opt/brother/runScanner.sh"]
