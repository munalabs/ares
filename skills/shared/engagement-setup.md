# Engagement Directory Setup — Shared Protocol

Every engagement (web or mobile) MUST create a per-engagement output directory before
writing any files. This ensures all reports, evidence, and screenshots are isolated
under `/pentest-output/<engagement-id>/` and never mixed with other engagements.

---

## Standard Setup Block

Paste this at the start of Phase 0 in any pentest skill:

```bash
source /tmp/engagement.env 2>/dev/null || true

if [ -z "$OUTDIR" ]; then
    # SLUG: for web → target hostname; for mobile → app package or APK filename
    SLUG="replace-with-target-slug"
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    ENGAGEMENT_ID="${SLUG}_${TIMESTAMP}"
    OUTDIR="/pentest-output/${ENGAGEMENT_ID}"
    mkdir -p "$OUTDIR/evidence" "$OUTDIR/screenshots" "$OUTDIR/pocs" "$OUTDIR/verification"
    printf 'OUTDIR=%s\nENGAGEMENT_ID=%s\n' "$OUTDIR" "$ENGAGEMENT_ID" > /tmp/engagement.env
    echo "Engagement: $ENGAGEMENT_ID"
    echo "Output:     $OUTDIR"
fi
```

Always `source /tmp/engagement.env` at the top of every subsequent execute/terminal block.

---

## Slug Derivation

**Web app:**
```bash
SLUG=$(python3 -c "
import re
t = 'https://target.example.com'
t = re.sub(r'^https?://', '', t).split('/')[0].split(':')[0]
print(re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-'))
")
```

**Mobile (package name known):**
```bash
SLUG=$(echo "com.example.app" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-\+/-/g')
```

**Mobile (APK filename, before package is known):**
```bash
SLUG=$(basename "app.apk" .apk | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g')
```

**Rename after package name is discovered:**
```bash
source /tmp/engagement.env
PACKAGE="com.example.app"
NEW_SLUG=$(echo "$PACKAGE" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-\+/-/g')
SUFFIX="${ENGAGEMENT_ID##*_}"   # preserve the timestamp
NEW_OUTDIR="/pentest-output/${NEW_SLUG}_${SUFFIX}"
mv "$OUTDIR" "$NEW_OUTDIR" 2>/dev/null || true
OUTDIR="$NEW_OUTDIR"
ENGAGEMENT_ID="${NEW_SLUG}_${SUFFIX}"
sed -i "s|OUTDIR=.*|OUTDIR=$OUTDIR|; s|ENGAGEMENT_ID=.*|ENGAGEMENT_ID=$ENGAGEMENT_ID|" /tmp/engagement.env
echo "Renamed: $OUTDIR"
```

---

## Directory Structure

```
/pentest-output/<engagement-id>/
├── evidence/          # raw tool output, JSON data, MobSF raw JSON
├── screenshots/       # ADB screenshots, browser screenshots
├── pocs/              # proof-of-concept scripts (.sh, .py, .curl)
├── verification/      # per-finding verification state (see mobile-verification.md)
│   └── mob-01/
│       ├── poc-output.txt
│       └── verification-screenshot.png
└── <report>.md        # final report at root of engagement dir
```

---

## Restore on Sandbox Reset

If `/tmp/engagement.env` is missing mid-engagement, restore from the checkpoint:

```python
import glob, json, os

checkpoints = glob.glob('/pentest-output/*/checkpoint.json')
if checkpoints:
    checkpoints.sort(key=lambda p: json.load(open(p)).get('last_checkpoint',''), reverse=True)
    cp = json.load(open(checkpoints[0]))
    outdir = cp['outdir']
    with open('/tmp/engagement.env', 'w') as f:
        f.write(f"OUTDIR={outdir}\nENGAGEMENT_ID={cp['engagement_id']}\n")
    print(f"Restored: {outdir}")
else:
    print("No checkpoint found — check /pentest-output/ manually")
```

See `skills/cybersec/pentest-engagement-state/SKILL.md` for the full checkpoint protocol.

---

## Rules

- **Never write files directly to `/pentest-output/`** — always to `$OUTDIR/`
- **Always `source /tmp/engagement.env`** at the top of every execute/terminal block
- **MEDIA paths** for delivery use `$OUTDIR/file.md`, not `/pentest-output/file.md`
- **If OUTDIR already set** (resume case): skip creation, just source the env
