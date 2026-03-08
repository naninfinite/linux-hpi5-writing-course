#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTENT_ROOT="${HOME}/gbtw/content"
DATA_ROOT="${HOME}/.local/share/gbtw"
VENV_DIR="${ROOT_DIR}/.venv"

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install "${ROOT_DIR}"

mkdir -p \
  "${CONTENT_ROOT}/part1" \
  "${CONTENT_ROOT}/part2" \
  "${CONTENT_ROOT}/part3" \
  "${CONTENT_ROOT}/part4" \
  "${DATA_ROOT}"

cat > "${CONTENT_ROOT}/part1/01-morning-freewrite.md" <<'EOF'
---
# status: active | optional | archived
title: Morning Freewrite
part: 1
module: Finding Your Voice
type: exercise
status: active
---

Write continuously for ten minutes about anything that comes to mind.
Do not stop typing.
EOF

cat > "${CONTENT_ROOT}/part1/02-observation-walk.md" <<'EOF'
---
# status: active | optional | archived
title: Observation Walk
part: 1
module: Finding Your Voice
type: exercise
status: optional
---

List five details from a short walk, then turn them into one paragraph.
EOF

cat > "${CONTENT_ROOT}/part2/01-reading-on-rhythm.md" <<'EOF'
---
# status: active | optional | archived
title: Reading on Rhythm
part: 2
module: Rhythm and Pace
type: reading
status: archived
---

Read this passage slowly. Notice where the line breaks create momentum.
EOF

cat > "${CONTENT_ROOT}/part3/01-daily-pages.md" <<'EOF'
---
# save_mode: session | project
# status: active | optional | archived
title: Daily Pages
part: 3
module: Sustainable Practice
type: long-term
save_mode: session
status: active
---

Return to this prompt daily and write one honest page without editing.
EOF

cat > "${CONTENT_ROOT}/part4/01-final-project.md" <<'EOF'
---
# save_mode: session | project
# status: active | optional | archived
title: Final Project Draft
part: 4
module: Building the Work
type: long-term
save_mode: project
status: active
---

Keep one continuous draft here across the final modules.
EOF

printf 'Installed gbtw sample content in %s\n' "${CONTENT_ROOT}"
printf 'Run %s/bin/gbtw to launch the app\n' "${VENV_DIR}"
