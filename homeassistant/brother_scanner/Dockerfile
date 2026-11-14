ARG BUILD_FROM
FROM ${BUILD_FROM}

# The image's health check probes the default port; the add-on port is an
# option (/data/options.json), so the Supervisor's own state is used instead.
HEALTHCHECK NONE

# runScanner.sh exports the add-on options (/data/options.json) itself.
CMD ["bash", "-c", "/opt/brother/runScanner.sh"]
