#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Auri — MCP Server Setup Script
# Adds MCP servers to ~/.hermes/config.yaml
# ─────────────────────────────────────────────
set -euo pipefail

CONFIG="$HOME/.hermes/config.yaml"
BACKUP="$HOME/.hermes/config.yaml.bak.$(date +%Y%m%d_%H%M%S)"

# ── Pre-flight checks ────────────────────────
echo "🔍 Pre-flight checks..."

for cmd in npx uvx python3; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "❌ $cmd not found. Please install it first."
    exit 1
  fi
done

if ! python3 -c "import mcp" 2>/dev/null; then
  echo "📦 Installing mcp Python SDK..."
  pip install mcp
fi

echo "✅ npx:     $(npx --version)"
echo "✅ uvx:     $(uvx --version 2>/dev/null || echo ok)"
echo "✅ mcp SDK: $(python3 -c 'import mcp; print(mcp.__version__)' 2>/dev/null || echo installed)"

# ── Detect GitHub token ──────────────────────
GH_TOKEN="${GITHUB_PERSONAL_ACCESS_TOKEN:-${GH_TOKEN:-}}"
if [[ -z "$GH_TOKEN" && -f "$HOME/.config/gh/hosts.yml" ]]; then
  GH_TOKEN=$(grep -A1 'oauth_token' "$HOME/.config/gh/hosts.yml" 2>/dev/null | tail -1 | sed 's/.*: *//' | tr -d '"' || true)
fi
if [[ -z "$GH_TOKEN" ]]; then
  echo "⚠️  No GitHub token found. GitHub MCP will be SKIPPED."
  echo "   Set GITHUB_PERSONAL_ACCESS_TOKEN or GH_TOKEN and re-run."
fi

# ── Detect Auri project root ─────────────────
AURI_DIR="${AURI_DIR:-$HOME/projects/auri}"
if [[ ! -d "$AURI_DIR/.git" ]]; then
  echo "⚠️  $AURI_DIR not a git repo — will still use it for filesystem scope."
fi

# ── Backup config ────────────────────────────
cp "$CONFIG" "$BACKUP"
echo "📦 Config backed up to: $BACKUP"

# ── Check if mcp_servers already exists ──────
if grep -q '^mcp_servers:' "$CONFIG" 2>/dev/null; then
  echo "⚠️  mcp_servers already in config — appending to existing block."
  echo "   Remove the old 'mcp_servers:' block if you want a clean install."
fi

# ── Append MCP server config ─────────────────
cat >> "$CONFIG" << 'YAMLBLOCK'

# ── Auri MCP Servers ─────────────────────────
mcp_servers:
  postgres:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost:5432/auri"]
    timeout: 60
YAMLBLOCK

if [[ -n "$GH_TOKEN" ]]; then
  cat >> "$CONFIG" << YAMLBLOCK
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GH_TOKEN}"
    timeout: 60
YAMLBLOCK
fi

cat >> "$CONFIG" << YAMLBLOCK
  docker:
    command: "npx"
    args: ["-y", "@hypnosis/docker-mcp-server"]
    timeout: 60
  git:
    command: "uvx"
    args: ["mcp-server-git", "--repository", "${AURI_DIR}"]
    timeout: 60
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "${AURI_DIR}"]
    timeout: 30
  fetch:
    command: "uvx"
    args: ["mcp-server-fetch"]
YAMLBLOCK

# ── Summary ──────────────────────────────────
echo ""
echo "✅ MCP servers added to $CONFIG"
echo ""
echo "── Server Summary ────"
echo "  postgres    → Database schema & query access"
echo "  github      → Repository management & PRs${GH_TOKEN:+ (authenticated)}${GH_TOKEN:- (⚠️  SKIPPED)}"
echo "  docker      → Container & Compose management"
echo "  git         → Git operations (scoped to $AURI_DIR)"
echo "  filesystem  → File read/write (scoped to $AURI_DIR)"
echo "  fetch       → Web research during development"
echo ""
echo "🔄 Restart Hermes Agent to load these tools:"
echo "   hermes --profile default"
echo ""
echo "📋 To verify after restart:"
echo "   Look for 'mcp_*' prefixed tools in your available tool list."
