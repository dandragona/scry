#!/bin/sh
# scry installer — fetches the `scry` CLI (and its DeepSeek adapter) onto your PATH.
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
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${REF}}"

err() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || err "python3 not found (scry needs Python 3.9+)."
command -v curl    >/dev/null 2>&1 || err "curl not found."

# Ensure the install dir exists, escalating to sudo only when the location needs
# it. /usr/local/bin is on the macOS default PATH but does not exist on a fresh
# machine, so it must be created before anything can move into it. Remember
# whether writes there need sudo so each file install reuses the same decision.
SUDO=""
if mkdir -p "$INSTALL_DIR" 2>/dev/null && [ -w "$INSTALL_DIR" ]; then
  :
else
  printf 'Need sudo to write %s\n' "$INSTALL_DIR"
  sudo mkdir -p "$INSTALL_DIR" || err "could not create $INSTALL_DIR"
  SUDO="sudo"
fi

# Download one Python script from the repo and install it (executable) into
# INSTALL_DIR. Used for both `scry` and its sibling `scry-deepseek` adapter.
install_file() {
  name="$1"
  dest="${INSTALL_DIR%/}/$name"
  tmp="$(mktemp)"
  curl -fsSL "$RAW_BASE/$name" -o "$tmp" \
    || { rm -f "$tmp"; err "download failed from $RAW_BASE/$name"; }
  head -n1 "$tmp" | grep -q 'python3' \
    || { rm -f "$tmp"; err "downloaded $name doesn't look like a scry script."; }
  $SUDO mv "$tmp" "$dest" || { rm -f "$tmp"; err "could not install $name to $INSTALL_DIR"; }
  $SUDO chmod +x "$dest"  || err "could not make $dest executable"
  printf '  -> %s\n' "$dest"
}

printf 'Installing scry from %s\n' "$REPO@$REF"
install_file scry
# The DeepSeek provider has no subscription CLI; scry shells out to this API-key
# adapter, which it resolves *next to the scry binary*. Install it alongside or
# DeepSeek shows up as "not found" even with DEEPSEEK_API_KEY set.
install_file scry-deepseek

printf '\n\033[32m✓ installed\033[0m %s\n' "$("${INSTALL_DIR%/}/scry" --version 2>/dev/null || echo scry)"
case ":$PATH:" in
  *":${INSTALL_DIR%/}:"*) : ;;
  *) printf '\033[33mnote:\033[0m %s is not on your PATH — add it.\n' "$INSTALL_DIR" ;;
esac
printf '\nNext: run \033[1mscry --check\033[0m to verify your model CLIs are logged in.\n'
printf 'For DeepSeek, also set \033[1mDEEPSEEK_API_KEY\033[0m (see .env.example).\n'
