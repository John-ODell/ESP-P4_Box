#!/bin/sh
# Run the broker in the foreground with the pet config. Milestone 7 turns this into a login item.
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/pet.conf" ] || { echo "run setup.sh first"; exit 1; }
exec mosquitto -c "$HERE/pet.conf"
