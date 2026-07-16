#!/usr/bin/env bash
# Auri - Setup development environment
set -euo pipefail

AURI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$AURI_DIR"

echo "=== Auri Dev Setup ==="

# 1. MCP Server Configuration
echo ""
echo "--- MCP Servers ---"
python3 scripts/gen-mcp-config.py
echo "MCP server block appended to ~/.hermes/config.yaml"
echo ""

# 2. Check prerequisites
echo "--- Prerequisites ---"
for cmd in node python3 docker; do
    if command -v $cmd &>/dev/null; then
        echo "  ✓ $cmd"
    else
        echo "  ✗ $cmd (install manually)"
    fi
done

# 3. Notes
echo ""
echo "--- Next Steps ---"
echo "  1. Start PostgreSQL: brew services start postgresql"
echo "  2. Create database: createdb auri"
echo "  3. Restart Hermes: hermes --profile default"
echo "  4. Verify MCP tools appear in available tool list"

echo ""
echo "=== Done ==="
