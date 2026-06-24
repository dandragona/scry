#!/bin/sh
# scry installer — fetches the `scry` CLI (and its DeepSeek adapter) onto your PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh
#
# scry is one stdlib-only Python file, so there is nothing to build and no
# dependencies to install. It installs into a USER-OWNED directory and never uses
# sudo — by default ~/.local/bin, like rustup / uv / pipx — so install, update, and
# run all work without elevation. Override the source or destination if you like:
#
#   SCRY_REPO=youruser/scry INSTALL_DIR=~/bin sh install.sh
#
set -eu

# SCRY_REPO / SCRY_REF are the canonical names (shared with `scry update`); the bare
# REPO / REF are kept as fallbacks for backward compatibility.
REPO="${SCRY_REPO:-${REPO:-dandragona/scry}}"
REF="${SCRY_REF:-${REF:-main}}"
# User-owned by default — never a system dir. A root-owned CLI is exactly what makes
# you reach for sudo just to install/update/run it, so we don't go there.
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${REF}}"

err()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
note() { printf '\033[33mnote:\033[0m %s\n' "$1"; }

command -v python3 >/dev/null 2>&1 || err "python3 not found (scry needs Python 3.9+)."
command -v curl    >/dev/null 2>&1 || err "curl not found."

# Create the install dir AS YOU — no sudo, ever. If it can't be made writable, that's
# a misconfigured INSTALL_DIR, not a license to escalate into a system directory.
mkdir -p "$INSTALL_DIR" 2>/dev/null || true
[ -w "$INSTALL_DIR" ] || err "$INSTALL_DIR isn't writable by $(id -un). Set INSTALL_DIR to a directory you own, e.g.  INSTALL_DIR=\$HOME/.local/bin sh install.sh"

# Download one Python script from the repo and install it (executable AND world-
# readable) into INSTALL_DIR. Used for both `scry` and its `scry-deepseek` sibling.
install_file() {
  name="$1"
  dest="${INSTALL_DIR%/}/$name"
  tmp="$(mktemp)"
  curl -fsSL "$RAW_BASE/$name" -o "$tmp" \
    || { rm -f "$tmp"; err "download failed from $RAW_BASE/$name"; }
  head -n1 "$tmp" | grep -q 'python3' \
    || { rm -f "$tmp"; err "downloaded $name doesn't look like a scry script."; }
  mv "$tmp" "$dest" || { rm -f "$tmp"; err "could not install $name to $INSTALL_DIR"; }
  # 755, not `+x`: scry is a Python script the interpreter must READ to run, so it
  # has to be world-readable (a bare `chmod +x` on a 0600 tempfile yields 0711).
  chmod 755 "$dest" || err "could not make $dest executable"
  printf '  -> %s\n' "$dest"
}

# scry also ships Claude Code skills (/scry, /scry-plan). Drop each into the user's
# personal skills dir (under $HOME, never needs sudo; honors CLAUDE_CONFIG_DIR).
# Best-effort: a failure here is only a note, never an aborted install.
install_skill() {
  name="$1"
  skill_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/$name"
  tmp="$(mktemp)"
  if ! curl -fsSL "$RAW_BASE/.claude/skills/$name/SKILL.md" -o "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    note "could not fetch the /$name Claude skill — skipped."
    return 0
  fi
  if mkdir -p "$skill_dir" 2>/dev/null && mv "$tmp" "$skill_dir/SKILL.md" 2>/dev/null; then
    printf '  -> %s\n' "$skill_dir/SKILL.md"
  else
    rm -f "$tmp"
    note "could not write the /$name Claude skill to $skill_dir — skipped."
  fi
}

printf 'Installing scry from %s\n' "$REPO@$REF"
install_file scry
# DeepSeek and GLM have no subscription CLI; scry shells out to these API-key
# adapters, which it resolves *next to the scry binary*. Install them alongside or
# the provider shows up as "not found" even with its API key set.
install_file scry-deepseek
install_file scry-glm

printf '\nInstalling the /scry + /scry-plan Claude Code skills\n'
install_skill scry
install_skill scry-plan

# The optional local web UI (`scry web`) needs a few third-party Python packages.
# Per the project's default, install them up front (best-effort, never fatal) so the
# UI works out of the box; the core CLI stays stdlib-only and runs even if this fails.
# Skip with SCRY_NO_WEB=1.
install_web_deps() {
  [ "${SCRY_NO_WEB:-0}" = "1" ] && { note "SCRY_NO_WEB=1 — skipping web UI deps."; return 0; }
  printf '\nInstalling the optional web UI dependencies (FastAPI + uvicorn)\n'
  if python3 -m pip install --user --quiet \
      "fastapi>=0.110" "uvicorn[standard]>=0.29" "python-multipart>=0.0.9" 2>/dev/null; then
    printf '  -> fastapi, uvicorn, python-multipart\n'
  else
    note "could not install web UI deps automatically — run \`scry web\` to see the install hint, or \`pip install 'scry[web]'\`."
  fi
}
install_web_deps

printf '\n\033[32m✓ installed\033[0m %s\n' "$("${INSTALL_DIR%/}/scry" --version 2>/dev/null || echo scry)"

# PATH guidance — we PRINT the line to add and never touch your shell files.
case ":$PATH:" in
  *":${INSTALL_DIR%/}:"*) : ;;   # already on PATH — nothing to do
  *)
    rc="your shell profile"
    case "${SHELL:-}" in
      */zsh)  rc="$HOME/.zshrc" ;;
      */bash) rc="$HOME/.bashrc" ;;
    esac
    note "${INSTALL_DIR%/} is not on your PATH."
    printf '  Add it by appending this line to %s, then restart your shell:\n' "$rc"
    printf '    export PATH="%s:$PATH"\n' "${INSTALL_DIR%/}"
    ;;
esac

# Warn about an older scry that would SHADOW this one (e.g. a previous root-owned
# /usr/local/bin install). Never auto-removed — that's the user's call (and sudo).
existing="$(command -v scry 2>/dev/null || true)"
if [ -n "$existing" ] && [ "$existing" != "${INSTALL_DIR%/}/scry" ]; then
  note "another scry is earlier on your PATH and will shadow this one: $existing"
  printf '  Remove it, e.g.:  rm %s   (sudo if it is root-owned)\n' "$existing"
fi

printf '\nNext: run \033[1mscry --check\033[0m to verify your model CLIs are logged in.\n'
printf 'For the API-key providers, set \033[1mDEEPSEEK_API_KEY\033[0m / \033[1mGLM_API_KEY\033[0m in \033[1m~/.config/scry/.env\033[0m (see .env.example).\n'
printf 'In Claude Code, run \033[1m/scry <prompt>\033[0m to consult the panel, or \033[1m/scry-plan <request>\033[0m to plan.\n'
printf 'Local web UI: run \033[1mscry web\033[0m from a repo clone (or \033[1mpip install -e \047.[web]\047\033[0m) to launch the sleek browser UI.\n'
