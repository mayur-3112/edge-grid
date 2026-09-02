#!/bin/sh
# The kubo image binds its HTTP API to 127.0.0.1 *inside* the container, where a
# published port cannot reach it. Rebind to 0.0.0.0 within the container's own
# network namespace; the compose file publishes that port to the host's loopback
# only, so the API is still not reachable from the LAN.
set -e
ipfs config Addresses.API /ip4/0.0.0.0/tcp/5001
ipfs config Addresses.Gateway /ip4/0.0.0.0/tcp/8080
