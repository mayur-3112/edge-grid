#!/usr/bin/env bash
# The Edge Grid - one-command environment setup.
#
# Reproduces the full toolchain on a clean Ubuntu/Debian machine, including the
# two workarounds that are not obvious and cost real time to find:
#
#   1. py-libp2p pins fastecdsa==2.3.2, which ships no wheel for CPython 3.12,
#      so pip tries to build it and needs gmp.h from libgmp-dev. On a machine
#      without sudo we fetch the .deb and extract the headers locally.
#   2. That build then links against the static libgmp.a it just extracted and
#      fails with "relocation ... can not be used when making a shared object".
#      The fix is to point the linker at the SHARED system libgmp.so.10 instead.
#
# Usage:  ./setup.sh          (full setup)
#         ./setup.sh --check  (verify an existing environment)

set -euo pipefail
cd "$(dirname "$0")"
REPO="$PWD"
VENV="$REPO/.venv"
PY="$VENV/bin/python"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m%s\033[0m\n' "$*"; }
die()   { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

check() {
  echo "== checking environment =="
  local ok=0
  "$PY" -c 'import libp2p' 2>/dev/null && green "  libp2p        OK" || { warn "  libp2p        MISSING"; ok=1; }
  "$PY" -c 'import trio, fastapi, pydantic, psutil, matplotlib, pandas' 2>/dev/null \
    && green "  python deps   OK" || { warn "  python deps   MISSING"; ok=1; }
  "$PY" -c 'from edgegrid.identity import Identity; Identity.generate()' 2>/dev/null \
    && green "  edgegrid      OK" || { warn "  edgegrid      MISSING"; ok=1; }
  curl -sf --max-time 2 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" >/dev/null \
    && green "  ollama        UP" || warn "  ollama        DOWN (start it: ollama serve)"
  [ -d "$REPO/contracts/node_modules" ] \
    && green "  hardhat       OK" || warn "  hardhat       MISSING (cd contracts && npm install)"
  return $ok
}

if [ "${1:-}" = "--check" ]; then check; exit $?; fi

# --- 1. python venv ---------------------------------------------------------
echo "== 1/5 python venv =="
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
green "  venv ready at $VENV"

# --- 2. gmp headers for fastecdsa (see header comment) ----------------------
echo "== 2/5 gmp headers (py-libp2p -> fastecdsa==2.3.2) =="
if ! "$PY" -c 'import fastecdsa' 2>/dev/null; then
  GMP_INC=""
  if [ -f /usr/include/gmp.h ] || [ -f /usr/include/x86_64-linux-gnu/gmp.h ]; then
    green "  system gmp.h found"
  else
    warn "  no gmp.h; fetching libgmp-dev locally (no sudo required)"
    TMP="$(mktemp -d)"
    ( cd "$TMP" && apt-get download libgmp-dev >/dev/null 2>&1 ) \
      || die "could not fetch libgmp-dev; install it with: sudo apt install libgmp-dev"
    dpkg-deb -x "$TMP"/libgmp-dev*.deb "$TMP/root"
    GMP_INC="$(dirname "$(find "$TMP/root" -name gmp.h | head -1)")"
    green "  extracted headers to $GMP_INC"
  fi
  # Link against the SHARED system libgmp, never the static .a from the deb.
  SO="$(ls /usr/lib/*/libgmp.so.10 2>/dev/null | head -1)"
  [ -n "$SO" ] || die "libgmp.so.10 not found; install libgmp10"
  LINKDIR="$(mktemp -d)"; ln -sf "$SO" "$LINKDIR/libgmp.so"
  CFLAGS="${GMP_INC:+-I$GMP_INC}" LDFLAGS="-L$LINKDIR" LIBRARY_PATH="$LINKDIR" \
    "$VENV/bin/pip" install -q "fastecdsa==2.3.2" || die "fastecdsa build failed"
  green "  fastecdsa 2.3.2 built"
else
  green "  fastecdsa already present"
fi

# --- 3. python packages -----------------------------------------------------
echo "== 3/5 python packages =="
"$VENV/bin/pip" install -q -r requirements.txt
green "  installed from requirements.txt"

# --- 4. hardhat -------------------------------------------------------------
echo "== 4/5 solidity toolchain =="
if command -v npm >/dev/null 2>&1; then
  ( cd contracts && npm install --no-audit --no-fund --loglevel=error --legacy-peer-deps >/dev/null )
  green "  hardhat installed"
else
  warn "  npm not found - skipping contracts (settlement falls back to the python ledger)"
fi

# --- 5. ollama model --------------------------------------------------------
echo "== 5/5 inference model =="
if command -v ollama >/dev/null 2>&1; then
  MODEL="${OLLAMA_MODEL:-qwen3-vl:2b-instruct}"
  ollama list 2>/dev/null | grep -q "${MODEL%%:*}" \
    && green "  $MODEL present" \
    || { warn "  pulling $MODEL"; ollama pull "$MODEL"; }
else
  warn "  ollama not installed - see https://ollama.com"
fi

[ -f .env ] || { cp .env.example .env; green "  wrote .env from .env.example"; }

echo
check
echo
green "Setup complete. Try:  $PY -m pytest tests/ -q"
