#!/bin/sh
# One-time broker setup: writes pet.conf from the template and (re)creates the password file
# from MQTT_USER / MQTT_PASS in bridge/.env. Safe to rerun.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
ENV_FILE="$REPO/bridge/.env"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy bridge/.env.example and set MQTT_USER / MQTT_PASS)"; exit 1; }
MQTT_USER=$(sed -n 's/^MQTT_USER=//p' "$ENV_FILE" | tail -1)
MQTT_PASS=$(sed -n 's/^MQTT_PASS=//p' "$ENV_FILE" | tail -1)
[ -n "$MQTT_USER" ] && [ -n "$MQTT_PASS" ] || { echo "MQTT_USER / MQTT_PASS not set in $ENV_FILE"; exit 1; }
command -v mosquitto_passwd >/dev/null || { echo "mosquitto_passwd not found: brew install mosquitto"; exit 1; }
sed "s#__REPO__#$REPO#g" "$HERE/pet.conf.template" > "$HERE/pet.conf"
rm -f "$HERE/passwd"
mosquitto_passwd -c -b "$HERE/passwd" "$MQTT_USER" "$MQTT_PASS"
chmod 600 "$HERE/passwd"
mkdir -p "$REPO/bridge/var"
echo "wrote $HERE/pet.conf and $HERE/passwd for user '$MQTT_USER'"
