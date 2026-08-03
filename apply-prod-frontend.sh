#!/usr/bin/env bash
# Switches the FicAtlas frontend from dev mode to a production build.
# Safe to re-run; makes a timestamped backup of docker-compose.yml first.
set -euo pipefail

cd ~/ficatlas

COMPOSE=docker-compose.yml
if [ ! -f "$COMPOSE" ]; then
  echo "ERROR: $COMPOSE not found in $(pwd). Aborting."; exit 1
fi

# 1. Back up the compose file.
BACKUP="${COMPOSE}.bak.$(date +%Y%m%d-%H%M%S)"
cp "$COMPOSE" "$BACKUP"
echo "Backed up $COMPOSE -> $BACKUP"

# 2. Patch the frontend service with Python (precise, indentation-aware).
python3 - "$COMPOSE" <<'PY'
import sys, re, io

path = sys.argv[1]
with open(path) as f:
    lines = f.readlines()

out = []
in_frontend = False
frontend_indent = None
removed = []

def indent_of(s):
    return len(s) - len(s.lstrip(" "))

i = 0
while i < len(lines):
    line = lines[i]
    stripped = line.strip()

    # Detect entering the frontend service block (a "frontend:" key).
    if re.match(r"^\s*frontend:\s*$", line):
        in_frontend = True
        frontend_indent = indent_of(line)
        out.append(line)
        i += 1
        continue

    if in_frontend:
        cur_indent = indent_of(line) if stripped else None
        # A new key at the same indent as "frontend:" ends the block.
        if stripped and cur_indent is not None and cur_indent <= frontend_indent:
            in_frontend = False
            out.append(line)
            i += 1
            continue

        # Remove the dev command line.
        if re.match(r"^\s*command:\s*npm\s+run\s+dev\s*$", line):
            removed.append(stripped); i += 1; continue

        # Remove a "volumes:" key and all its list items (deeper indent).
        if re.match(r"^\s*volumes:\s*$", line):
            vol_indent = indent_of(line)
            removed.append(stripped)
            i += 1
            # Skip following lines that are more-indented (the volume entries).
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1; continue
                if indent_of(nxt) > vol_indent:
                    removed.append(nxt.strip()); i += 1; continue
                break
            continue

    out.append(line)
    i += 1

with open(path, "w") as f:
    f.writelines(out)

if removed:
    print("Removed from frontend service:")
    for r in removed:
        print("   -", r)
else:
    print("No dev command/volumes found under frontend (already patched?).")
PY

echo
echo "Patched $COMPOSE. Diff vs backup:"
diff "$BACKUP" "$COMPOSE" || true

echo
echo "Rebuilding frontend (this runs 'npm run build' and may take a few minutes)..."
sudo docker compose up --build -d frontend

echo
echo "Done. Tail logs with:  sudo docker compose logs -f frontend"
echo "Look for a production 'Ready' line, then test offline on the https://...ts.net URL."
