#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <docker-volume-name> <existing-host-destination>" >&2
  exit 2
fi

source_volume=$1
destination=$2

if [ ! -d "$destination" ]; then
  echo "destination must already exist: $destination" >&2
  exit 2
fi

echo "Copying from Docker volume '$source_volume' to '$destination'. No source data is deleted."
docker run --rm \
  --volume "$source_volume:/source:ro" \
  --volume "$destination:/destination" \
  alpine:3.22 sh -c '
    set -eu
    if [ -n "$(find /destination -mindepth 1 -print -quit)" ]; then
      echo "refusing to copy: destination is not empty" >&2
      exit 3
    fi
    if [ -z "$(find /source -mindepth 1 -print -quit)" ]; then
      echo "source volume is empty; nothing to copy"
      exit 0
    fi
    cp -a /source/. /destination/
    sync
  '

echo "Copy complete. Verify file counts and checksums before changing Compose configuration."
