#!/usr/bin/env python3
"""
APK SAST MCP server — deterministic extraction for LLM-driven rule reasoning.

Architecture (Option C):
  This server extracts structured evidence from a decompiled APK.
  The calling skill's LLM decides is_vulnerable, severity, confidence,
  and false_positive_analysis. No verdicts here — only evidence.

System deps: apktool (required), jadx (optional, for Java source)
  apt-get install -y apktool
  # jadx: https://github.com/skylot/jadx/releases

Tools:
  decompile_apk      run apktool + optional jadx, return paths
  parse_manifest     structured AndroidManifest.xml JSON
  build_call_graph   Smali invoke-* → {callees, callers} per in-scope class
  grep_smali         regex over in-scope Smali files, scoped to app package
  run_rule_context   aggregate all evidence for one rule (primary tool)
  list_rules         all rules with MASVS refs
  get_masvs          MASVS v2.0 control lookup
"""

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("apk-sast")

PENTEST_OUTPUT = Path(os.environ.get("PENTEST_OUTPUT", "/pentest-output"))
ANDROID_NS = "http://schemas.android.com/apk/res/android"

# ── MASVS v2.0 ──────────────────────────────────────────────────────────────

MASVS: dict[str, dict] = {
    "MASVS-STORAGE-1": {
        "title": "The app securely stores sensitive data",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-STORAGE-1/",
    },
    "MASVS-STORAGE-2": {
        "title": "The app prevents leakage of sensitive data via backups",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-STORAGE-2/",
    },
    "MASVS-CRYPTO-1": {
        "title": "The app employs current strong cryptography and uses it according to industry best practices",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-CRYPTO-1/",
    },
    "MASVS-CRYPTO-2": {
        "title": "The app performs key management according to industry best practices",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-CRYPTO-2/",
    },
    "MASVS-AUTH-1": {
        "title": "The app uses secure authentication and authorization protocols",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-AUTH-1/",
    },
    "MASVS-AUTH-2": {
        "title": "The app performs server-side authentication when accessing sensitive functions or resources",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-AUTH-2/",
    },
    "MASVS-AUTH-3": {
        "title": "The app secures sensitive operations with additional authentication",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-AUTH-3/",
    },
    "MASVS-NETWORK-1": {
        "title": "The app secures all network traffic according to the current best practices",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-NETWORK-1/",
    },
    "MASVS-NETWORK-2": {
        "title": "The app verifies the identity of the remote endpoint",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-NETWORK-2/",
    },
    "MASVS-PLATFORM-1": {
        "title": "The app uses IPC mechanisms securely",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-PLATFORM-1/",
    },
    "MASVS-PLATFORM-2": {
        "title": "The app uses WebViews securely",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-PLATFORM-2/",
    },
    "MASVS-PLATFORM-3": {
        "title": "The app uses the appropriate APIs and does not use deprecated APIs",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-PLATFORM-3/",
    },
    "MASVS-CODE-1": {
        "title": "The app requires an up-to-date platform version",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-CODE-1/",
    },
    "MASVS-CODE-2": {
        "title": "The app only uses software components without known vulnerabilities",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-CODE-2/",
    },
    "MASVS-CODE-4": {
        "title": "The app validates and sanitizes all untrusted inputs",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-CODE-4/",
    },
    "MASVS-RESILIENCE-1": {
        "title": "The app validates the integrity of the platform it runs on",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-RESILIENCE-1/",
    },
    "MASVS-RESILIENCE-2": {
        "title": "The app implements anti-tampering mechanisms",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-RESILIENCE-2/",
    },
    "MASVS-PRIVACY-1": {
        "title": "The app minimizes access to sensitive data and resources",
        "url": "https://mas.owasp.org/MASVS/controls/MASVS-PRIVACY-1/",
    },
}

# ── Library blocklist ────────────────────────────────────────────────────────

LIBRARY_BLOCKLIST = [
    "android/", "androidx/", "com/google/", "kotlin/", "kotlinx/",
    "okhttp3/", "okio/", "retrofit2/", "com/squareup/",
    "io/reactivex/", "rx/", "com/facebook/", "com/twitter/",
    "com/amazonaws/", "com/firebase/", "com/crashlytics/",
    "org/apache/", "org/jetbrains/", "org/bouncycastle/",
    "com/jakewharton/", "io/coil/", "com/bumptech/",
    "dagger/", "hilt/", "javax/", "java/", "sun/",
]

# ── Rule definitions ─────────────────────────────────────────────────────────

RULES: dict[str, dict] = {
    "strandhogg": {
        "display_name": "StrandHogg (Task Affinity Attack)",
        "masvs_id": "MASVS-PLATFORM-1",
        "suggested_severity": "High",
        "description": (
            "Activities with taskAffinity pointing to a different package + launchMode "
            "singleTask/singleInstance can be overlaid by a malicious app to capture "
            "credentials (StrandHogg 1.0 / CVE-2019-14702). "
            "StrandHogg 2.0 (CVE-2020-0096) affects all apps on Android < 10 regardless "
            "of manifest attributes — flag any target below API 29."
        ),
        "smali_patterns": [],
        "manifest_checks": ["taskAffinity", "launchMode"],
        "call_graph_relevant": False,
    },
    "pending_intent_mutable": {
        "display_name": "Mutable PendingIntent",
        "masvs_id": "MASVS-PLATFORM-1",
        "suggested_severity": "High",
        "description": (
            "PendingIntent without FLAG_IMMUTABLE (required from API 31+, recommended from 23+) "
            "allows a receiving app to modify the intent's action, extras, or component before "
            "it fires. A malicious app that receives the PendingIntent can redirect it to "
            "an arbitrary component or inject payload into extras. "
            "Check whether the caller passes FLAG_IMMUTABLE or 0 as flags argument."
        ),
        "smali_patterns": [
            r"Landroid/app/PendingIntent;->getActivity",
            r"Landroid/app/PendingIntent;->getBroadcast",
            r"Landroid/app/PendingIntent;->getService",
            r"Landroid/app/PendingIntent;->getForegroundService",
            r"FLAG_MUTABLE",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
    "biometric_without_crypto": {
        "display_name": "Biometric Auth Without CryptoObject",
        "masvs_id": "MASVS-AUTH-3",
        "suggested_severity": "High",
        "description": (
            "BiometricPrompt.authenticate() without a CryptoObject means auth confirmation "
            "is not bound to any cryptographic key operation. An attacker with OS-level "
            "access can confirm authentication in software without a real biometric match. "
            "Look for authenticate() calls: the two-arg form (no CryptoObject) is the "
            "vulnerable pattern. The deprecated FingerprintManager has the same flaw. "
            "Use call graph to check if CryptoObject is constructed and passed."
        ),
        "smali_patterns": [
            r"Landroidx/biometric/BiometricPrompt;->authenticate",
            r"Landroid/hardware/fingerprint/FingerprintManager;->authenticate",
            r"Landroid/hardware/biometrics/BiometricPrompt;->authenticate",
            r"BiometricPrompt\$CryptoObject",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
    "zip_slip": {
        "display_name": "Zip Slip (Path Traversal in Archive Extraction)",
        "masvs_id": "MASVS-CODE-4",
        "suggested_severity": "Critical",
        "description": (
            "ZipEntry.getName() used as a file path without normalizing '../' sequences "
            "allows a crafted archive to write files outside the intended directory. "
            "If the app processes attacker-controlled archives (downloads, user uploads, "
            "OTA updates), this can overwrite arbitrary files. "
            "Check via call graph whether getName() result is sanitized before file writes "
            "(File() constructor, FileOutputStream, etc.)."
        ),
        "smali_patterns": [
            r"Ljava/util/zip/ZipEntry;->getName",
            r"Ljava/util/zip/ZipInputStream",
            r"Ljava/util/zip/ZipFile",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
    "fragment_injection": {
        "display_name": "Fragment Injection via PreferenceActivity",
        "masvs_id": "MASVS-PLATFORM-1",
        "suggested_severity": "High",
        "description": (
            "Exported PreferenceActivity subclasses without isValidFragment() override allow "
            "any installed app to load arbitrary fragments via the "
            "android.intent.extra.PREFERENCE_FRAGMENT intent extra. "
            "Always vulnerable on API < 19. Fixed on API >= 19 only if isValidFragment() "
            "returns false for all but the expected fragments. "
            "Check manifest for exported activities inheriting from PreferenceActivity."
        ),
        "smali_patterns": [
            r"Landroid/preference/PreferenceActivity;",
            r"isValidFragment",
        ],
        "manifest_checks": ["exported"],
        "call_graph_relevant": False,
    },
    "unsafe_reflection": {
        "display_name": "Unsafe Reflection",
        "masvs_id": "MASVS-CODE-4",
        "suggested_severity": "Medium",
        "description": (
            "Class.forName() or Method.invoke() with externally-influenced class/method "
            "names allows arbitrary code path invocation, security check bypass, or "
            "deserialization gadget triggering. "
            "Use call graph to trace whether the class name argument originates from "
            "user input, intent extras, network response, or file content."
        ),
        "smali_patterns": [
            r"Ljava/lang/Class;->forName",
            r"Ljava/lang/reflect/Method;->invoke",
            r"Ljava/lang/ClassLoader;->loadClass",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
    "insecure_deserialization": {
        "display_name": "Insecure Java Deserialization",
        "masvs_id": "MASVS-CODE-4",
        "suggested_severity": "Critical",
        "description": (
            "ObjectInputStream.readObject() on untrusted data enables gadget chain attacks. "
            "With Apache Commons Collections, Spring, or similar libraries on the classpath, "
            "this is typically RCE. Custom readObject/readResolve implementations are "
            "especially dangerous. "
            "Use call graph to determine what feeds the InputStream — "
            "network socket, file, SharedPreferences, or IPC is critical context."
        ),
        "smali_patterns": [
            r"Ljava/io/ObjectInputStream;->readObject",
            r"Ljava/io/ObjectInputStream;",
            r"readResolve\(\)Ljava/lang/Object",
            r"readObject\(Ljava/io/ObjectInputStream",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
    "jetpack_compose_saveable_leak": {
        "display_name": "Sensitive Data in rememberSaveable",
        "masvs_id": "MASVS-STORAGE-1",
        "suggested_severity": "Medium",
        "description": (
            "rememberSaveable persists Compose state to the saved instance state bundle, "
            "which is included in Android backups (when allowBackup=true) and accessible "
            "to other apps on rooted devices. "
            "Passwords, tokens, and PII should use remember() instead — "
            "scoped to the composition, not persisted. "
            "Confirm whether the saved values are sensitive by examining what's passed "
            "to rememberSaveable() in context."
        ),
        "smali_patterns": [
            r"rememberSaveable",
            r"Landroidx/compose/runtime/saveable",
            r"SaveableStateRegistry",
        ],
        "manifest_checks": ["allowBackup"],
        "call_graph_relevant": False,
    },
    "exported_no_permission": {
        "display_name": "Exported Component Without Permission Protection",
        "masvs_id": "MASVS-PLATFORM-1",
        "suggested_severity": "High",
        "description": (
            "A component with android:exported=true (or implicit export via intent-filter "
            "on API < 31) without android:permission is accessible to any installed app. "
            "Activities can be started arbitrarily; services can be bound; "
            "receivers intercept broadcasts; providers expose data. "
            "Check what each component does — sensitive operations or data access "
            "without caller verification is the exploitable condition."
        ),
        "smali_patterns": [],
        "manifest_checks": ["exported", "permission"],
        "call_graph_relevant": False,
    },
    "tapjacking": {
        "display_name": "Tapjacking / UI Overlay Attack",
        "masvs_id": "MASVS-PLATFORM-1",
        "suggested_severity": "Medium",
        "description": (
            "Activities without filterTouchesWhenObscured=true are vulnerable to overlay "
            "attacks where a malicious app draws a transparent window over the legitimate UI "
            "to intercept taps on sensitive controls (confirm payment, grant permission, "
            "enter credentials). "
            "Presence of setFilterTouchesWhenObscured(true) in code is the mitigation — "
            "absence is the finding."
        ),
        "smali_patterns": [
            r"setFilterTouchesWhenObscured",
            r"filterTouchesWhenObscured",
            r"FLAG_WINDOW_IS_PARTIALLY_OBSCURED",
        ],
        "manifest_checks": [],
        "call_graph_relevant": False,
    },
    "intent_scheme_webview": {
        "display_name": "Intent Scheme in WebView",
        "masvs_id": "MASVS-PLATFORM-2",
        "suggested_severity": "High",
        "description": (
            "WebViews that process intent:// URLs without shouldOverrideUrlLoading() "
            "validation allow a malicious web page to launch arbitrary Android intents — "
            "starting exported activities, sending broadcasts, accessing content providers. "
            "Check whether shouldOverrideUrlLoading returns true for intent:// scheme "
            "or delegates to Intent.parseUri() with safety flags "
            "(URI_INTENT_SCHEME + SAFE_INTENT)."
        ),
        "smali_patterns": [
            r"intent://",
            r"Landroid/webkit/WebViewClient;->shouldOverrideUrlLoading",
            r"Landroid/content/Intent;->parseUri",
        ],
        "manifest_checks": [],
        "call_graph_relevant": True,
    },
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _smali_dirs(base: Path) -> list[Path]:
    return sorted([d for d in base.glob("smali*") if d.is_dir()])


def _pkg_prefix(package: str) -> str:
    return package.replace(".", "/")


def _in_scope(rel: str, pkg_prefix: str) -> bool:
    if pkg_prefix and not rel.startswith(pkg_prefix):
        return False
    return not any(rel.startswith(lib) for lib in LIBRARY_BLOCKLIST)


def _detect_package(base: Path) -> str:
    manifest = base / "AndroidManifest.xml"
    if not manifest.exists():
        return ""
    try:
        return ET.parse(manifest).getroot().get("package", "")
    except ET.ParseError:
        return ""


def _parse_component_elem(elem: ET.Element) -> dict:
    ns = ANDROID_NS
    intent_filters = []
    for ifilter in elem.findall("intent-filter"):
        intent_filters.append({
            "actions": [a.get(f"{{{ns}}}name", "") for a in ifilter.findall("action")],
            "categories": [c.get(f"{{{ns}}}name", "") for c in ifilter.findall("category")],
            "data": [
                {k: d.get(f"{{{ns}}}{k}", "") for k in ("scheme", "host", "pathPrefix", "mimeType")}
                for d in ifilter.findall("data")
            ],
        })
    return {
        "name": elem.get(f"{{{ns}}}name", ""),
        "exported": elem.get(f"{{{ns}}}exported", ""),
        "permission": elem.get(f"{{{ns}}}permission", ""),
        "taskAffinity": elem.get(f"{{{ns}}}taskAffinity", ""),
        "launchMode": elem.get(f"{{{ns}}}launchMode", ""),
        "intent_filters": intent_filters,
    }


# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def decompile_apk(apk_path: str, output_dir: str = "") -> str:
    """
    Decompile an APK with apktool (always) and jadx (if available).
    Returns: {decompile_dir, smali_dirs, manifest_path, jadx_dir, package_name, output_dir}.
    Run this first — all other tools require decompile_dir.
    """
    apk = Path(apk_path)
    if not apk.exists():
        return json.dumps({"error": f"APK not found: {apk_path}"})

    out = Path(output_dir) if output_dir else PENTEST_OUTPUT / f"sast_{apk.stem}"
    out.mkdir(parents=True, exist_ok=True)
    apktool_dir = out / "apktool"

    # Re-run apktool if manifest is missing or not valid XML (catches partial/failed runs).
    manifest_ok = False
    manifest_path = apktool_dir / "AndroidManifest.xml"
    if manifest_path.exists():
        try:
            ET.parse(manifest_path)
            manifest_ok = True
        except ET.ParseError:
            pass  # binary or corrupt — re-decompile

    if not manifest_ok:
        r = subprocess.run(
            ["apktool", "d", str(apk), "-o", str(apktool_dir), "-f"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return json.dumps({"error": f"apktool failed: {r.stderr[-600:]}"})

    package = _detect_package(apktool_dir)
    smali_dirs = [str(d) for d in _smali_dirs(apktool_dir)]

    jadx_dir = None
    if subprocess.run(["which", "jadx"], capture_output=True).returncode == 0:
        jadx_out = out / "jadx"
        if not jadx_out.exists():
            subprocess.run(["jadx", "-d", str(jadx_out), str(apk)],
                           capture_output=True, timeout=300)
        if jadx_out.exists():
            jadx_dir = str(jadx_out)

    return json.dumps({
        "decompile_dir": str(apktool_dir),
        "smali_dirs": smali_dirs,
        "manifest_path": str(apktool_dir / "AndroidManifest.xml"),
        "jadx_dir": jadx_dir,
        "package_name": package,
        "output_dir": str(out),
    })


@mcp.tool()
def parse_manifest(decompile_dir: str) -> str:
    """
    Parse AndroidManifest.xml from an apktool decompile directory.
    Returns structured JSON: package, sdk versions, permissions, activities,
    services, receivers, providers with exported status and intent filters.
    """
    base = Path(decompile_dir)
    manifest_path = base / "AndroidManifest.xml"
    if not manifest_path.exists():
        return json.dumps({"error": f"AndroidManifest.xml not found in {decompile_dir}"})

    try:
        root = ET.parse(manifest_path).getroot()
    except ET.ParseError as e:
        return json.dumps({"error": f"XML parse error: {e}"})

    ns = ANDROID_NS
    result: dict = {
        "package": root.get("package", ""),
        "uses_sdk": {},
        "permissions": [],
        "application": {},
        "activities": [],
        "services": [],
        "receivers": [],
        "providers": [],
    }

    sdk = root.find("uses-sdk")
    if sdk is not None:
        result["uses_sdk"] = {
            "min": sdk.get(f"{{{ns}}}minSdkVersion", ""),
            "target": sdk.get(f"{{{ns}}}targetSdkVersion", ""),
        }

    result["permissions"] = [
        p.get(f"{{{ns}}}name", "") for p in root.findall("uses-permission")
    ]

    app = root.find("application")
    if app is not None:
        result["application"] = {
            "debuggable": app.get(f"{{{ns}}}debuggable", ""),
            "allowBackup": app.get(f"{{{ns}}}allowBackup", ""),
            "usesCleartextTraffic": app.get(f"{{{ns}}}usesCleartextTraffic", ""),
            "networkSecurityConfig": app.get(f"{{{ns}}}networkSecurityConfig", ""),
        }
        for tag, key in [("activity", "activities"), ("service", "services"),
                          ("receiver", "receivers")]:
            for elem in app.findall(tag):
                result[key].append(_parse_component_elem(elem))
        for provider in app.findall("provider"):
            comp = _parse_component_elem(provider)
            comp.update({
                "authorities": provider.get(f"{{{ns}}}authorities", ""),
                "readPermission": provider.get(f"{{{ns}}}readPermission", ""),
                "writePermission": provider.get(f"{{{ns}}}writePermission", ""),
                "grantUriPermissions": provider.get(f"{{{ns}}}grantUriPermissions", ""),
            })
            result["providers"].append(comp)

    return json.dumps(result)


@mcp.tool()
def build_call_graph(decompile_dir: str, max_files: int = 3000) -> str:
    """
    Build a caller→callees and callee→callers map from Smali invoke-* instructions.
    Scoped to app package only (excludes third-party libraries).
    Returns: {package, files_processed, callees: {class: [classes]}, callers: {class: [classes]}}.
    For large apps, lower max_files (default 3000) to reduce build time.
    """
    base = Path(decompile_dir)
    package = _detect_package(base)
    if not package:
        return json.dumps({"error": "Cannot detect package name from manifest"})

    pkg = _pkg_prefix(package)
    invoke_re = re.compile(
        r"invoke-(?:virtual|interface|static|direct|super|polymorphic)(?:/range)?"
        r"\s+\{[^}]*\},\s+(L[^;]+);"
    )

    callees_map: dict[str, set] = {}
    callers_map: dict[str, set] = {}
    processed = 0

    for smali_d in _smali_dirs(base):
        for smali_file in smali_d.rglob("*.smali"):
            if processed >= max_files:
                break
            rel = str(smali_file.relative_to(smali_d))
            if not _in_scope(rel, pkg):
                continue
            caller = "L" + rel.replace(".smali", "") + ";"
            try:
                content = smali_file.read_text(errors="ignore")
            except OSError:
                continue
            for m in invoke_re.finditer(content):
                callee = m.group(1) + ";"
                if callee == caller:
                    continue
                if any(callee[1:].startswith(lib) for lib in LIBRARY_BLOCKLIST):
                    continue
                callees_map.setdefault(caller, set()).add(callee)
                callers_map.setdefault(callee, set()).add(caller)
            processed += 1

    return json.dumps({
        "package": package,
        "files_processed": processed,
        "callees": {k: list(v) for k, v in callees_map.items()},
        "callers": {k: list(v)[:10] for k, v in callers_map.items()},
    })


@mcp.tool()
def grep_smali(
    decompile_dir: str,
    pattern: str,
    context_lines: int = 2,
    max_matches: int = 15,
) -> str:
    """
    Regex search over in-scope Smali files (app package only, no third-party libs).
    Returns: {matches: [{file, line_num, match, context_before, context_after}], truncated}.
    """
    base = Path(decompile_dir)
    package = _detect_package(base)
    pkg = _pkg_prefix(package) if package else ""

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return json.dumps({"error": f"Invalid regex: {e}"})

    matches = []
    for smali_d in _smali_dirs(base):
        if len(matches) >= max_matches:
            break
        for smali_file in smali_d.rglob("*.smali"):
            if len(matches) >= max_matches:
                break
            rel = str(smali_file.relative_to(smali_d))
            if not _in_scope(rel, pkg):
                continue
            try:
                lines = smali_file.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if not compiled.search(line):
                    continue
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                matches.append({
                    "file": f"smali/{rel}",
                    "line_num": i + 1,
                    "match": line.strip(),
                    "context_before": [l.strip() for l in lines[start:i]],
                    "context_after": [l.strip() for l in lines[i + 1:end]],
                })
                if len(matches) >= max_matches:
                    break

    return json.dumps({"matches": matches, "truncated": len(matches) >= max_matches})


@mcp.tool()
def run_rule_context(decompile_dir: str, rule_name: str) -> str:
    """
    Collect all deterministic evidence for one SAST rule.
    Returns structured JSON — the LLM skill reasons over this to decide:
      is_vulnerable, severity, confidence, false_positive_analysis.

    Primary tool: call for each rule after decompile_apk.
    Available rules: see list_rules().
    """
    rule = RULES.get(rule_name)
    if not rule:
        return json.dumps({
            "error": f"Unknown rule '{rule_name}'",
            "available": list(RULES.keys()),
        })

    base = Path(decompile_dir)
    if not base.exists():
        return json.dumps({"error": f"decompile_dir not found: {decompile_dir}"})

    package = _detect_package(base)
    masvs_id = rule["masvs_id"]

    ctx: dict = {
        "rule": rule_name,
        "display_name": rule["display_name"],
        "description": rule["description"],
        "suggested_severity": rule["suggested_severity"],
        "masvs": {"id": masvs_id, **MASVS.get(masvs_id, {})},
        "package": package,
        "manifest_findings": [],
        "code_matches": [],
        "call_graph_context": {},
    }

    # ── Manifest checks ──────────────────────────────────────────────────────
    if rule["manifest_checks"]:
        try:
            mdata = json.loads(parse_manifest(decompile_dir))
            components = (
                [{"_type": "activity", **c} for c in mdata.get("activities", [])]
                + [{"_type": "service", **c} for c in mdata.get("services", [])]
                + [{"_type": "receiver", **c} for c in mdata.get("receivers", [])]
                + [{"_type": "provider", **c} for c in mdata.get("providers", [])]
            )

            if rule_name == "strandhogg":
                sdk_target = int(mdata.get("uses_sdk", {}).get("target", "99") or "99")
                if sdk_target < 29:
                    ctx["manifest_findings"].append({
                        "note": f"targetSdk={sdk_target} < 29 — vulnerable to StrandHogg 2.0 regardless of taskAffinity"
                    })
                for comp in components:
                    ta = comp.get("taskAffinity", "")
                    lm = comp.get("launchMode", "")
                    if ta and ta != package:
                        ctx["manifest_findings"].append({
                            "type": comp["_type"], "component": comp["name"],
                            "taskAffinity": ta, "launchMode": lm or "(standard)",
                            "exported": comp.get("exported", ""),
                            "note": "taskAffinity differs from own package",
                        })
                    elif lm in ("1", "2", "singleTask", "singleInstance"):
                        ctx["manifest_findings"].append({
                            "type": comp["_type"], "component": comp["name"],
                            "taskAffinity": ta or "(default)", "launchMode": lm,
                            "exported": comp.get("exported", ""),
                            "note": "singleTask/singleInstance launchMode",
                        })

            elif rule_name == "exported_no_permission":
                for comp in components:
                    exported = comp.get("exported", "")
                    has_filter = bool(comp.get("intent_filters"))
                    # implicit export: no exported attr + has intent-filter (API < 31 behavior)
                    is_exported = exported == "true" or (exported == "" and has_filter)
                    if is_exported and not comp.get("permission", ""):
                        ctx["manifest_findings"].append({
                            "type": comp["_type"], "component": comp["name"],
                            "exported": exported or "(implicit — has intent-filter)",
                            "permission": "(none)",
                            "intent_filters": len(comp.get("intent_filters", [])),
                        })

            elif rule_name == "fragment_injection":
                sdk_min = int(mdata.get("uses_sdk", {}).get("min", "0") or "0")
                if sdk_min < 19:
                    ctx["manifest_findings"].append({
                        "note": f"minSdk={sdk_min} < 19 — always vulnerable to fragment injection"
                    })
                for act in mdata.get("activities", []):
                    if act.get("exported", "") == "true":
                        ctx["manifest_findings"].append({
                            "component": act["name"],
                            "exported": "true",
                            "note": "Exported activity — check if it subclasses PreferenceActivity",
                        })

            elif rule_name == "jetpack_compose_saveable_leak":
                app = mdata.get("application", {})
                ctx["manifest_findings"].append({
                    "allowBackup": app.get("allowBackup") or "(not set — defaults to true on API < 31)",
                })

        except Exception as e:
            ctx["manifest_findings"] = [{"error": str(e)}]

    # ── Code pattern grep ────────────────────────────────────────────────────
    for pattern in rule["smali_patterns"]:
        result = json.loads(grep_smali(decompile_dir, pattern, context_lines=2, max_matches=8))
        for m in result.get("matches", []):
            m["pattern"] = pattern
            ctx["code_matches"].append(m)
        if len(ctx["code_matches"]) >= 15:
            ctx["code_matches_truncated"] = True
            break

    # ── Call graph context (rules where caller context matters) ──────────────
    if rule["call_graph_relevant"] and ctx["code_matches"]:
        matched_classes: set[str] = set()
        for m in ctx["code_matches"][:5]:
            f = m.get("file", "")
            # smali/com/example/Foo.smali → Lcom/example/Foo;
            rel = re.sub(r"^smali[^/]*/", "", f.replace("\\", "/"))
            cls = "L" + re.sub(r"\.smali$", "", rel) + ";"
            matched_classes.add(cls)

        if matched_classes:
            cg = json.loads(build_call_graph(decompile_dir, max_files=2000))
            for cls in matched_classes:
                ctx["call_graph_context"][cls] = {
                    "callers": cg.get("callers", {}).get(cls, [])[:5],
                    "callees": cg.get("callees", {}).get(cls, [])[:5],
                }

    return json.dumps(ctx, indent=2)


@mcp.tool()
def list_rules() -> str:
    """List all available SAST rules with MASVS references and suggested severity."""
    return json.dumps([
        {
            "rule": name,
            "display_name": r["display_name"],
            "masvs_id": r["masvs_id"],
            "masvs_title": MASVS.get(r["masvs_id"], {}).get("title", ""),
            "suggested_severity": r["suggested_severity"],
            "has_manifest_checks": bool(r["manifest_checks"]),
            "has_code_patterns": bool(r["smali_patterns"]),
            "call_graph_relevant": r["call_graph_relevant"],
        }
        for name, r in RULES.items()
    ])


@mcp.tool()
def get_masvs(masvs_id: str) -> str:
    """Look up a MASVS v2.0 control by ID (e.g. MASVS-PLATFORM-1)."""
    ctrl = MASVS.get(masvs_id)
    if not ctrl:
        return json.dumps({"error": f"Unknown: '{masvs_id}'", "available": list(MASVS.keys())})
    return json.dumps({"id": masvs_id, **ctrl})


if __name__ == "__main__":
    mcp.run(transport="stdio")
