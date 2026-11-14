#!/usr/bin/env bash
# shellcheck shell=bash
# Configuration file support (#124). Sourced by runScanner.sh and testable
# with bats.
#
# The file holds KEY=VALUE lines with the same names as the environment
# variables in the README. Precedence: a variable set in the container
# environment (compose file) wins over the file, the file wins over the
# built-in default. Lines starting with # and blank lines are ignored; one
# pair of surrounding double quotes is stripped from a value.

config_file_path() {
  echo "${SCANNER_CONFIG_FILE:-/scans/.config/scanner.conf}"
}

config_key_valid() {
  [[ "$1" =~ ^[A-Z][A-Z0-9_]*$ ]]
}

# record_locked_keys <file>: write the names of every variable that is set in
# the environment right now, one per line. The settings page shows these as
# read-only and the scripts never let the file override them.
record_locked_keys() {
  env | cut -d= -f1 | grep -E '^[A-Z][A-Z0-9_]*$' | sort >"$1"
}

# load_config_file <file> <locked-list>: export every valid KEY=VALUE from the
# file whose KEY is not listed in <locked-list>. Prints what it did.
load_config_file() {
  local file="$1" locked="$2" line key value loaded=0 ignored=0
  if [[ ! -r "$file" ]]; then
    echo "config: no configuration file at $file"
    return 0
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line// /}" || "$line" == \#* ]] && continue
    if [[ "$line" != *=* ]]; then
      echo "config: ignoring line without '=': $line"
      continue
    fi
    key="${line%%=*}"
    value="${line#*=}"
    key="${key// /}"
    if ! config_key_valid "$key"; then
      echo "config: ignoring invalid key: $key"
      continue
    fi
    if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ -r "$locked" ]] && grep -qx "$key" "$locked"; then
      echo "config: $key is set by the environment, file value ignored"
      ignored=$((ignored + 1))
      continue
    fi
    export "$key=$value"
    loaded=$((loaded + 1))
  done <"$file"
  echo "config: $loaded setting(s) loaded from $file, $ignored overridden by the environment"
}

# registration_known <cfg> <name>: true when the driver resolved the model of
# the device registered as <name> to a product id. Both brsaneconfig4 and
# brsaneconfig5 accept any model name and write "Unknown" for one they do not
# support (the backend then runs a generic profile with wrong geometry, #75),
# so this is how the entrypoint tells which driver a model belongs to (#173).
registration_known() {
  local cfg="$1" name="$2" line
  [[ -r "$cfg" ]] || return 1
  line=$(grep -E "^DEVICE=${name} ," "$cfg" | tail -1)
  [[ -n "$line" ]] || return 1
  [[ "$line" != *" , Unknown , "* ]]
}

# load_addon_options [<options.json>]: export the options of a Home Assistant
# add-on (/data/options.json, written by the Supervisor) as environment
# variables, upper-cased (scan_mode -> SCAN_MODE). Booleans become true/false;
# empty values and null are skipped so the built-in defaults apply. Called
# before record_locked_keys, so the add-on configuration counts as environment
# and wins over the configuration file. Does nothing outside an add-on.
load_addon_options() {
  local file="${1:-/data/options.json}" assignments
  [[ -r "$file" ]] || return 0
  assignments=$(python3 - "$file" <<'PY'
import json, re, shlex, sys
with open(sys.argv[1]) as handle:
    options = json.load(handle)
for key, value in options.items():
    name = key.upper()
    if not re.match(r"^[A-Z][A-Z0-9_]*$", name) or value is None:
        continue
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, list):
        # scan_targets: [{name, copy_to_dir}] -> "Name=/dir;Other=/dir"
        value = ";".join(
            f"{item.get('name', '')}={item.get('copy_to_dir', '') or ''}" if isinstance(item, dict) else str(item)
            for item in value
        )
    value = str(value)
    if value == "":
        continue
    print(f"export {name}={shlex.quote(value)}")
PY
  ) || { echo "add-on: could not read $file"; return 0; }
  eval "$assignments"
  echo "add-on: $(grep -c '^export ' <<<"$assignments") option(s) loaded from $file"
}

# configure_escl <name> <url> <dir>: a private SANE configuration directory
# with only the sane-airscan backend and the device at <url> (discovery off),
# for SCANNER_DRIVER=escl. scanimage then finds exactly this device as its
# default when SANE_CONFIG_DIR=<dir>; the system configuration (and the
# Brother backends registered there) stays untouched.
configure_escl() {
  local name="$1" url="$2" dir="$3"
  mkdir -p "$dir"
  echo "airscan" >"$dir/dll.conf"
  {
    echo "[devices]"
    echo "\"$name\" = $url, eSCL"
    echo ""
    echo "[options]"
    echo "discovery = disable"
  } >"$dir/airscan.conf"
  chmod 755 "$dir" && chmod 644 "$dir/dll.conf" "$dir/airscan.conf"
}
