# Mobile Finding Verification Protocol — Shared

After all discovery phases complete, run a structured verification pass before writing
the report. Only VERIFIED findings go in the final report.

---

## Why Verify?

Mobile pentest tools produce false positives. MobSF flags components as "exported" when
they're actually protected. ADB `am start` may fail silently. Frida hooks may report
empty results because there was no app activity. Verification closes this gap.

---

## Finding Format (raw-findings.json)

Each finding discovered during testing is appended to `$OUTDIR/evidence/raw-findings.json`:

```json
{
  "id": "mob-01",
  "title": "Auth bypass via exported activity: PostLoginActivity",
  "claim": "Activity launches without credentials",
  "severity": "CRITICAL",
  "category": "Broken Access Control",
  "owasp_mobile": "M1",
  "poc_command": "am start -n com.example.app/.PostLoginActivity",
  "poc_result": "<shell output from initial discovery>",
  "evidence_screenshot": "$OUTDIR/screenshots/bypass-postlogin.png",
  "source": "dynamic",
  "verifiable": true
}
```

**`verifiable: true`** = has a concrete PoC command that can be re-run.  
**`verifiable: false`** = static-only finding (manifest flag, hardcoded string) — auto-verified.

---

## Verification Loop

```python
import json, os, datetime

outdir = open("/tmp/engagement.env").read()
outdir = [l.split("=",1)[1].strip() for l in outdir.splitlines() if l.startswith("OUTDIR=")][0]

raw = json.load(open(f"{outdir}/evidence/raw-findings.json"))
verified, rejected, escalated = [], [], []

for finding in raw:
    fid = finding["id"]
    result_dir = f"{outdir}/verification/{fid}"
    os.makedirs(result_dir, exist_ok=True)

    # Static findings: manifest flags, hardcoded strings — always verified
    if not finding.get("verifiable", False):
        finding["final_decision"] = "VERIFIED"
        finding["verification_method"] = "static-only"
        finding["verified_at"] = datetime.datetime.utcnow().isoformat()
        verified.append(finding)
        continue

    # Re-run PoC
    poc_cmd = finding["poc_command"]
    result = mcp_adb_shell(command=poc_cmd)
    with open(f"{result_dir}/poc-output.txt", "w") as f:
        f.write(f"Command: {poc_cmd}\n\nOutput:\n{result}")

    # Screenshot for UI-level bypasses
    if any(k in finding.get("category","").lower() for k in ["activity","bypass","access"]):
        shot = mcp_adb_screenshot(output_name=f"{result_dir}/verification-screenshot.png")
        finding["verification_screenshot"] = f"{result_dir}/verification-screenshot.png"

    result_str = str(result)
    if "error" in result_str.lower() or "exception" in result_str.lower() or "not found" in result_str.lower():
        finding["final_decision"] = "REJECTED"
        finding["rejection_reason"] = result_str[:200]
        rejected.append(finding)
    elif any(s in result_str for s in ["result=0", "Displayed", "completed", "rows"]):
        finding["final_decision"] = "VERIFIED"
        finding["poc_rerun_output"] = result_str[:500]
        finding["verified_at"] = datetime.datetime.utcnow().isoformat()
        verified.append(finding)
    else:
        finding["final_decision"] = "ESCALATED"
        finding["escalation_reason"] = f"Ambiguous output: {result_str[:200]}"
        escalated.append(finding)

summary = {
    "engagement_id": outdir.split("/")[-1],
    "verified": len(verified), "rejected": len(rejected), "escalated": len(escalated),
    "total": len(raw),
    "verified_findings": verified,
    "rejected_findings": rejected,
    "escalated_findings": escalated,
    "run_at": datetime.datetime.utcnow().isoformat(),
}
with open(f"{outdir}/verification/summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"Verification: {len(verified)} VERIFIED / {len(rejected)} REJECTED / {len(escalated)} ESCALATED")
```

---

## Decision Rules

| ADB output | Decision |
|------------|----------|
| Static flag (debuggable=true, allowBackup=true, cleartext) | VERIFIED — it's in the manifest |
| Hardcoded string found in DEX/resources | VERIFIED — it's in the binary |
| `am start` → `Displayed .ActivityName` in logcat | VERIFIED |
| `am broadcast` → `result=0` | VERIFIED |
| Content provider → returns rows | VERIFIED |
| Any `error`, `exception`, `not found` | REJECTED |
| Empty output with no error | ESCALATED |
| Ambiguous output (unclear if real activity rendered) | ESCALATED |

---

## Report Sections from Verification

Use `summary.json` to populate the report:

```python
summary = json.load(open(f"{outdir}/verification/summary.json"))

# Only these go in the main findings section:
for f in summary["verified_findings"]:
    # include f["title"], f["severity"], f["poc_command"], f["verification_screenshot"]

# These get a separate section:
for f in summary["escalated_findings"]:
    # include f["title"], f["escalation_reason"], evidence paths

# These are excluded:
# summary["rejected_findings"] — false positives, logged only
```
