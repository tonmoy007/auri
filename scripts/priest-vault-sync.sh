#!/usr/bin/env bash
# Sync the study vault to the server with an allowlist rsync, after a preflight.
#
# Usage:
#   PRIEST_SYNC_HOST=user@host PRIEST_VAULT_DIR=/app/data/priest/vault \
#     scripts/priest-vault-sync.sh [--dry-run] [--prune] /path/to/religion-study
#
# Environment:
#   PRIEST_VAULT_DIR   required. Absolute destination directory, at least two levels deep.
#   PRIEST_SYNC_HOST   optional. [user@]host for rsync over SSH. Unset syncs to a local
#                      directory, which is how the tests run it.
#   PRIEST_SSH_PORT    optional. SSH port (digits only).
#   PRIEST_SSH_KEY     optional. Path to an SSH identity file.
#   PRIEST_PYTHON      optional. Python used for the preflight (default python3).
#
# Only *.md files under the given religion-study folder are sent. Every hidden file or
# folder (.obsidian, .smart-env, .claudian, .claude, .omc, .git, .DS_Store), assets/,
# and every other file type is excluded, and the preflight runs first and refuses to
# continue on any finding (denied path, symlink, secret, key, email, phone, no type).
# --prune also removes notes that no longer exist at the source, but only inside
# PRIEST_VAULT_DIR, and only when that ends in /priest/vault: the exclude rules protect
# every non-.md file there, and --delete-excluded is never used. --dry-run shows what would happen and changes nothing.
set -euo pipefail

die() { echo "priest-vault-sync: $*" >&2; exit "${2:-2}"; }
usage() { sed -n '2,8p' "$0" >&2; exit 2; }

DRY_RUN=0
PRUNE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --prune) PRUNE=1; shift ;;
    -h|--help) usage ;;
    --) shift; break ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || usage

SRC="${1%/}"
[ -d "$SRC" ] || die "not a directory: the source must be the religion-study folder"
[ "$(basename "$SRC")" = "religion-study" ] \
  || die "the source folder must be named religion-study (never the parent vault)"

DEST="${PRIEST_VAULT_DIR:-}"
[ -n "$DEST" ] || die "PRIEST_VAULT_DIR is not set"
[[ "$DEST" =~ ^/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+/?$ ]] \
  || die "PRIEST_VAULT_DIR must be an absolute path at least two levels deep, with only letters, digits, . _ -"
[[ "/$DEST/" != *"/../"* ]] || die "PRIEST_VAULT_DIR must not contain .."
DEST="${DEST%/}"
# --delete removes every *.md at the destination that is not in the source, so it must
# only ever point at the vault folder itself, never a parent such as a home directory.
if [ "$PRUNE" -eq 1 ] && [[ "$DEST" != */priest/vault ]]; then
  die "--prune needs PRIEST_VAULT_DIR to end in /priest/vault, so it can only delete inside the vault"
fi

HOST="${PRIEST_SYNC_HOST:-}"
if [ -n "$HOST" ]; then
  [[ "$HOST" =~ ^[A-Za-z0-9][A-Za-z0-9._@-]*$ ]] || die "PRIEST_SYNC_HOST is not a valid [user@]host"
fi
PORT="${PRIEST_SSH_PORT:-}"
[[ -z "$PORT" || "$PORT" =~ ^[0-9]{1,5}$ ]] || die "PRIEST_SSH_PORT must be digits"
KEY="${PRIEST_SSH_KEY:-}"
[[ -z "$KEY" || "$KEY" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "PRIEST_SSH_KEY must be an absolute path"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! "${PRIEST_PYTHON:-python3}" "$REPO_ROOT/backend/scripts/priest_vault_preflight.py" "$SRC"; then
  die "preflight failed, nothing was sent" 1
fi

ARGS=(-rt --prune-empty-dirs
  --exclude='.*' --exclude='assets/'
  --include='*/' --include='*.md' --exclude='*')
[ "$DRY_RUN" -eq 1 ] && ARGS+=(--dry-run --itemize-changes)
[ "$PRUNE" -eq 1 ] && ARGS+=(--delete)

if [ -n "$HOST" ]; then
  SSH_CMD="ssh -o BatchMode=yes"
  [ -n "$PORT" ] && SSH_CMD+=" -p $PORT"
  [ -n "$KEY" ] && SSH_CMD+=" -i $KEY"
  ARGS+=(-e "$SSH_CMD")
  [ "$DRY_RUN" -eq 0 ] && ARGS+=(--rsync-path="mkdir -p $DEST && rsync")
  TARGET="$HOST:$DEST/"
else
  [ "$DRY_RUN" -eq 0 ] && mkdir -p "$DEST"
  TARGET="$DEST/"
fi

rsync "${ARGS[@]}" "$SRC/" "$TARGET"
