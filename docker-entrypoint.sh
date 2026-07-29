#!/bin/sh
set -eu

data_root="${M3U8_DATA_ROOT:-/data}"
download_root="${M3U8_DOWNLOAD_ROOT:-/downloads}"

for directory in "$data_root" "$download_root"; do
    mkdir -p "$directory"
    if ! chown app:app "$directory"; then
        echo "Unable to grant app user access to $directory. Check the Synology shared-folder ACL." >&2
        exit 1
    fi
done

exec gosu app "$@"