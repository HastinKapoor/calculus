#!/usr/bin/env bash
set -e

IMAGE_NAME="asplos-artifact"

docker build -t "$IMAGE_NAME" .

docker run --rm -it \
  --mount type=bind,src="$(pwd)",target=/artifact \
  -w /artifact \
  "$IMAGE_NAME"