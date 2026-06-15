#!/bin/sh
# scry installer — fetches the single-file `scry` CLI onto your PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh
#
# scry is one stdlib-only Python file, so there is nothing to build and no
# dependencies to install. Override the source or destination if you like:
#
#   REPO=youruser/scry INSTALL_DIR=~/.local/bin sh install.sh
#
set -eu

REPO="${REPO:-dandragona/scry}"
REF="${REF:-main}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
RAW="https://raw.githubusercontent.com/${REPO}/${REF}/scry"

err() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || err "python3 not found (scry needs Python 3.9+)."
command -v curl    >/dev/null 2>&1 || err "curl not found."

dest="${INSTALL_DIR%/}/scry"
printf 'Installing scry from %s\n  -> %s\n' "$REPO@$REF" "$dest"

tmp="$(mktemp)"
curl -fsSL "$RAW" -o "$tmp" || err "download failed from $RAW"
head -n1 "$tmp" | grep -q 'python3' || err "downloaded file doesn't look like scry."

if [ -w "$INSTALL_DIR" ] 2>/dev/null; then
  mv "$tmp" "$dest"
else
  printf 'Need sudo to write %s\n' "$INSTALL_DIR"
  sudo mv "$tmp" "$dest" || err "could not install to $INSTALL_DIR"
fi
chmod +x "$dest"

printf '\n\033[32m✓ installed\033[0m %s\n' "$(scry --version 2>/dev/null || echo scry)"
case ":$PATH:" in
  *":${INSTALL_DIR%/}:"*) : ;;
  *) printf '\033[33mnote:\033[0m %s is not on your PATH — add it.\n' "$INSTALL_DIR" ;;
esac
printf '\nNext: run \033[1mscry --check\033[0m to verify your model CLIs are logged in.\n'
