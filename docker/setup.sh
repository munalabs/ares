#!/usr/bin/env bash
# Ares — Docker Compose setup script
# Builds images, generates secrets, starts the stack, extracts MoBSF API key.
# Run from the docker/ directory:  cd docker && ./setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}▶${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*" >&2; }
die()     { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── Prerequisites ─────────────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] && die "Do not run setup.sh as root. Run as a normal user with Docker access."

info "Checking prerequisites..."
command -v docker  >/dev/null 2>&1 || die "Docker not found. Install from https://docs.docker.com/engine/install/"
command -v openssl >/dev/null 2>&1 || die "openssl not found."
if ! docker info >/dev/null 2>&1; then
    die "Docker not accessible as $USER.
  Add yourself to the docker group and re-login:
    sudo usermod -aG docker \$USER
    newgrp docker          # or log out and back in
  Then re-run this script."
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not found. Update Docker Desktop or install the compose plugin."
success "Prerequisites OK (running as $USER)"

# ── Anthropic token ───────────────────────────────────────────────────────────

echo
echo "Ares requires an Anthropic OAuth token (sk-ant-oat01-...) or API key (sk-ant-api03-)."
echo "Get one via 'claude login' (OAuth) or https://console.anthropic.com/settings/keys (API key)."
echo
read -rsp "  ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY; echo
[[ -n "$ANTHROPIC_API_KEY" ]] || die "ANTHROPIC_API_KEY cannot be empty."
[[ "$ANTHROPIC_API_KEY" =~ ^sk-ant- ]] || die "Unexpected key format. Expected sk-ant-oat01-... or sk-ant-api03-..."

# ── Discord (optional) ────────────────────────────────────────────────────────

echo
read -rp "Enable Discord gateway? [y/N] " DISCORD_ENABLE
DISCORD_ENABLE="${DISCORD_ENABLE:-n}"
DISCORD_BOT_TOKEN=""
DISCORD_ALLOWED_USERS=""
DISCORD_FREE_RESPONSE_CHANNELS=""
if [[ "$DISCORD_ENABLE" =~ ^[Yy]$ ]]; then
    echo "  Create a Discord bot at https://discord.com/developers/applications"
    read -rsp "  DISCORD_BOT_TOKEN: "                              DISCORD_BOT_TOKEN;    echo
    read -rp  "  DISCORD_ALLOWED_USERS (your user ID): "          DISCORD_ALLOWED_USERS
    read -rp  "  DISCORD_FREE_RESPONSE_CHANNELS (forum channel): " DISCORD_FREE_RESPONSE_CHANNELS
    [[ -n "$DISCORD_BOT_TOKEN" ]]              || die "DISCORD_BOT_TOKEN cannot be empty."
    [[ -n "$DISCORD_ALLOWED_USERS" ]]          || die "DISCORD_ALLOWED_USERS cannot be empty."
    [[ -n "$DISCORD_FREE_RESPONSE_CHANNELS" ]] || die "DISCORD_FREE_RESPONSE_CHANNELS cannot be empty."
    success "Discord configured"
else
    warn "Discord skipped — SwarmClaw web UI only (http://localhost:3456)"
fi

# ── Output directory ──────────────────────────────────────────────────────────

echo
DEFAULT_OUTPUT="$HOME/ares-pentest-output"
read -rp "  Pentest output directory [${DEFAULT_OUTPUT}]: " PENTEST_OUTPUT
PENTEST_OUTPUT="${PENTEST_OUTPUT:-$DEFAULT_OUTPUT}"
# Resolve to absolute path — mkdir first so cd works on both macOS and Linux
# (realpath -m is GNU-only; BSD realpath on macOS lacks the -m flag)
mkdir -p "$PENTEST_OUTPUT"
PENTEST_OUTPUT="$(cd "$PENTEST_OUTPUT" && pwd)"
[[ "$PENTEST_OUTPUT" == /* ]] || die "Output path must be absolute."
# No chmod: Docker (root) writes into user-owned dirs unconditionally.
# Root-created files default to 644 — readable by the host user.
# Reclaim ownership later if needed:
#   docker run --rm -v "$PENTEST_OUTPUT":/out alpine chown -R $(id -u):$(id -g) /out
success "Output directory: $PENTEST_OUTPUT"

# ── Android / ADB (optional) ──────────────────────────────────────────────────

# ── Android pre-flight check ──────────────────────────────────────────────────
# Detect platform and check all Android dependencies BEFORE asking any questions.
# This gives the user clear install instructions upfront rather than mid-setup.

HOST_OS="$(uname -s)"    # Darwin | Linux
HOST_ARCH="$(uname -m)"  # x86_64 | arm64

# Defaults (overridden by detection below)
ADB_SERIAL_VAL="localhost:5555"
ANDROID_ADB_SERVER_HOST_VAL="host.docker.internal"
FRIDA_SERVER_ARCH_VAL="x86_64"
COMPOSE_ANDROID_PROFILE=""

echo
echo "────────────────────────────────────────────────────────────"
echo "  Android testing (optional)"
echo "────────────────────────────────────────────────────────────"

# ── ADB ───────────────────────────────────────────────────────────────────────
ADB_OK=false
if command -v adb >/dev/null 2>&1; then
    ADB_OK=true
    success "adb: $(command -v adb)"
else
    warn "adb: not found"
    if [[ "$HOST_OS" == "Darwin" ]]; then
        echo "       → brew install android-platform-tools"
    else
        echo "       → sudo apt install android-sdk-platform-tools"
    fi
fi

# ── Emulator / KVM ────────────────────────────────────────────────────────────
EMU_OK=false
ANDROID_SDK=""
EMULATOR_BIN=""
AVDMANAGER_BIN=""
SDKMANAGER_BIN=""

if [[ "$HOST_OS" == "Darwin" ]]; then
    # macOS: Android Studio SDK — no /dev/kvm needed (Apple Hypervisor Framework)
    for candidate in \
        "${ANDROID_HOME:-}" \
        "$HOME/Library/Android/sdk" \
        "/usr/local/lib/android/sdk" \
        "/opt/homebrew/lib/android/sdk"; do
        if [[ -n "$candidate" && -x "$candidate/emulator/emulator" ]]; then
            ANDROID_SDK="$candidate"; break
        fi
    done

    if [[ -n "$ANDROID_SDK" ]]; then
        EMULATOR_BIN="$ANDROID_SDK/emulator/emulator"
        AVDMANAGER_BIN="$ANDROID_SDK/cmdline-tools/latest/bin/avdmanager"
        SDKMANAGER_BIN="$ANDROID_SDK/cmdline-tools/latest/bin/sdkmanager"
        EMU_OK=true
        success "Android SDK: $ANDROID_SDK"
        success "Emulator:    $EMULATOR_BIN"
        # Check for existing AVD
        if "$AVDMANAGER_BIN" list avd 2>/dev/null | grep -q "ares-android"; then
            success "AVD:         ares-android (exists)"
        else
            warn    "AVD:         ares-android not found (will be created on setup)"
        fi
    else
        warn "Android Studio SDK not found"
        echo "       → brew install --cask android-studio"
        echo "         Open Android Studio → More Actions → SDK Manager"
        echo "         SDK Tools tab → check Android Emulator → Apply"
        echo "         SDK Platforms tab → check Android 14 (API 34) → Apply"
        echo "         Re-run setup.sh after installation."
    fi

    # Frida arch follows chip
    if [[ "$HOST_ARCH" == "arm64" ]]; then
        FRIDA_SERVER_ARCH_VAL="arm64"
        ADB_SERIAL_VAL="emulator-5554"
        echo
        info "Platform: macOS Apple Silicon → ARM64 AVD, serial emulator-5554"
    else
        FRIDA_SERVER_ARCH_VAL="x86_64"
        ADB_SERIAL_VAL="emulator-5554"
        echo
        info "Platform: macOS Intel → x86_64 AVD, serial emulator-5554"
    fi

else
    # Linux: Docker emulator via --profile android (requires /dev/kvm)
    if [[ -e /dev/kvm ]]; then
        EMU_OK=true
        success "/dev/kvm: available (Docker emulator supported)"
        if ! id -nG "$USER" | grep -qw kvm; then
            warn "User $USER not in kvm group:"
            echo "       → sudo usermod -aG kvm $USER && newgrp kvm"
        fi
    else
        warn "/dev/kvm: not found"
        echo "       → Docker emulator requires bare metal or nested virtualization"
        echo "         For physical/WiFi device testing ADB still works without KVM"
    fi
    ADB_SERIAL_VAL="localhost:5555"
    echo
    info "Platform: Linux → Docker emulator (--profile android), serial localhost:5555"
fi

# ── ADB server instruction ─────────────────────────────────────────────────────
echo
echo "  IMPORTANT: The host ADB server must listen on all interfaces so the"
echo "  Hermes container can reach it. Run this in a separate terminal now:"
echo
echo "    adb kill-server && adb -a -P 5037 nodaemon server start"
echo
echo "  (On Linux docker0 IP is the host; on macOS host.docker.internal resolves it)"
echo "────────────────────────────────────────────────────────────"

# ── Ask ───────────────────────────────────────────────────────────────────────
echo
if [[ "$ADB_OK" == "false" ]]; then
    warn "adb not installed — install it and re-run setup.sh to enable Android testing."
    ADB_ENABLE="n"
else
    read -rp "  Configure Android testing? [y/N] " ADB_ENABLE
    ADB_ENABLE="${ADB_ENABLE:-n}"
fi

if [[ "$ADB_ENABLE" =~ ^[Yy]$ ]]; then
    echo
    read -rp "  ADB_SERIAL [${ADB_SERIAL_VAL}]: " _input
    ADB_SERIAL_VAL="${_input:-$ADB_SERIAL_VAL}"

    echo "  (host.docker.internal works on macOS + Linux Docker Desktop;"
    echo "   on bare-metal Linux use 172.17.0.1 if host.docker.internal doesn't resolve)"
    read -rp "  ANDROID_ADB_SERVER_HOST [${ANDROID_ADB_SERVER_HOST_VAL}]: " _input
    ANDROID_ADB_SERVER_HOST_VAL="${_input:-$ANDROID_ADB_SERVER_HOST_VAL}"

    echo
    echo "  Frida server architecture (must match your Android device CPU):"
    echo "    x86_64 — Linux Docker emulator, Android Studio AVD on Intel Mac"
    echo "    arm64  — Android Studio AVD on Apple Silicon, most physical phones"
    read -rp "  FRIDA_SERVER_ARCH [${FRIDA_SERVER_ARCH_VAL}]: " _input
    FRIDA_SERVER_ARCH_VAL="${_input:-$FRIDA_SERVER_ARCH_VAL}"
    [[ "$FRIDA_SERVER_ARCH_VAL" == "x86_64" || "$FRIDA_SERVER_ARCH_VAL" == "arm64" ]] \
        || die "FRIDA_SERVER_ARCH must be x86_64 or arm64, got: $FRIDA_SERVER_ARCH_VAL"

    # Injection guards
    [[ "$ADB_SERIAL_VAL" =~ ^[A-Za-z0-9._:/-]+$ ]] \
        || die "ADB_SERIAL contains unexpected characters: $ADB_SERIAL_VAL"
    [[ "$ANDROID_ADB_SERVER_HOST_VAL" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "ANDROID_ADB_SERVER_HOST contains unexpected characters: $ANDROID_ADB_SERVER_HOST_VAL"

    # Restart ADB server bound to all interfaces
    adb kill-server 2>/dev/null || true
    if adb -a -P 5037 nodaemon server start >/dev/null 2>&1; then
        success "Host ADB server listening on 0.0.0.0:5037"
    else
        warn "Could not start ADB server — start it manually:"
        warn "  adb kill-server && adb -a -P 5037 nodaemon server start"
    fi

    success "ADB configured (serial: $ADB_SERIAL_VAL, frida: $FRIDA_SERVER_ARCH_VAL)"

    # ── Emulator setup ────────────────────────────────────────────────────────
    echo
    read -rp "  Set up Android emulator? [y/N] " EMU_ENABLE
    EMU_ENABLE="${EMU_ENABLE:-n}"

    if [[ "$EMU_ENABLE" =~ ^[Yy]$ ]]; then
        if [[ "$HOST_OS" == "Darwin" ]]; then
            if [[ "$EMU_OK" == "false" ]]; then
                warn "Android Studio SDK not found — install it first (instructions above)."
            else
                [[ "$HOST_ARCH" == "arm64" ]] \
                    && SYS_IMG="system-images;android-34;google_apis;arm64-v8a" \
                    || SYS_IMG="system-images;android-34;google_apis;x86_64"

                if ! "$SDKMANAGER_BIN" --list_installed 2>/dev/null | grep -q "$SYS_IMG"; then
                    info "Installing Android system image ($SYS_IMG)..."
                    yes | "$SDKMANAGER_BIN" --licenses > /dev/null 2>&1 || true
                    "$SDKMANAGER_BIN" "$SYS_IMG" \
                        || die "System image install failed. Check SDK Manager output."
                    success "System image installed"
                else
                    success "System image already installed"
                fi

                if ! "$AVDMANAGER_BIN" list avd 2>/dev/null | grep -q "ares-android"; then
                    info "Creating ares-android AVD..."
                    echo "no" | "$AVDMANAGER_BIN" create avd \
                        --name ares-android \
                        --package "$SYS_IMG" \
                        --device "pixel_6" \
                        --force > /dev/null
                    success "AVD created"
                else
                    success "AVD ares-android already exists"
                fi

                info "Starting ares-android emulator (headless)..."
                nohup "$EMULATOR_BIN" \
                    -avd ares-android \
                    -no-window -no-audio -no-boot-anim \
                    -gpu swiftshader_indirect \
                    -memory 3072 \
                    > /tmp/ares-avd.log 2>&1 &
                EMU_PID=$!

                echo -n "  Waiting for emulator to boot"
                BOOTED=false
                for i in $(seq 1 60); do
                    BOOT_PROP=$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '[:space:]')
                    if [[ "$BOOT_PROP" == "1" ]]; then
                        echo; BOOTED=true; break
                    fi
                    echo -n "."; sleep 5
                done
                if [[ "$BOOTED" == "true" ]]; then
                    success "Emulator booted (PID $EMU_PID)"
                    ADB_SERIAL_VAL="emulator-5554"
                else
                    echo
                    warn "Emulator still booting — first start can take 2-3 minutes."
                    warn "Check: adb devices  (should show emulator-5554)"
                    warn "Log:   /tmp/ares-avd.log"
                fi
            fi

        else
            # Linux: Docker handles the emulator via --profile android
            if [[ "$EMU_OK" == "false" ]]; then
                warn "/dev/kvm not available — Docker emulator skipped."
                warn "For physical device testing, connect via USB and set ADB_SERIAL."
            else
                COMPOSE_ANDROID_PROFILE="--profile android"
                info "Android emulator will start with Docker (--profile android)"
                ADB_SERIAL_VAL="localhost:5555"
            fi
        fi
    fi
else
    warn "Android testing skipped."
fi

# ── Generate secrets ──────────────────────────────────────────────────────────

ZAP_API_KEY="$(openssl rand -hex 16)"
success "ZAP API key generated"

# ── Write .env ────────────────────────────────────────────────────────────────
# Use printf '%s' to write each value literally — no shell expansion of user input.

info "Writing .env..."

# Start fresh with a static header (single-quoted heredoc = no expansion)
cat > .env << 'ENVEOF'
# Generated by setup.sh — do not commit this file.
ENVEOF

# Append dynamic values safely with printf (handles $, backticks, spaces, etc.)
{
    printf '\nANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY"
    printf 'HERMES_STREAM_STALE_TIMEOUT=900\n'
    if [[ "$DISCORD_ENABLE" =~ ^[Yy]$ ]]; then
        printf '\nDISCORD_BOT_TOKEN=%s\n'                 "$DISCORD_BOT_TOKEN"
        printf 'DISCORD_ALLOWED_USERS=%s\n'               "$DISCORD_ALLOWED_USERS"
        printf 'DISCORD_FREE_RESPONSE_CHANNELS=%s\n'      "$DISCORD_FREE_RESPONSE_CHANNELS"
    fi
    printf '\nZAP_API_KEY=%s\n'        "$ZAP_API_KEY"
    printf 'MOBSF_API_KEY=pending\n'
    printf '\nPENTEST_OUTPUT=%s\n'     "$PENTEST_OUTPUT"
    if [[ "$ADB_ENABLE" =~ ^[Yy]$ ]]; then
        printf '\nADB_SERIAL=%s\n'                  "$ADB_SERIAL_VAL"
        printf 'ANDROID_ADB_SERVER_HOST=%s\n'       "$ANDROID_ADB_SERVER_HOST_VAL"
        printf 'FRIDA_SERVER_ARCH=%s\n'             "$FRIDA_SERVER_ARCH_VAL"
    else
        printf '\n# ADB_SERIAL=localhost:5555\n'
        printf '# ANDROID_ADB_SERVER_HOST=host.docker.internal\n'
        printf '# FRIDA_SERVER_ARCH=x86_64\n'
    fi
} >> .env

chmod 600 .env
success ".env written (mode 600)"

# ── Build images ──────────────────────────────────────────────────────────────

echo
info "Building ares-hermes and ares-tools images (this takes a few minutes)..."
docker compose --project-name ares build
success "Images built"

# ── Start MoBSF first to extract its API key ──────────────────────────────────

info "Starting MoBSF to generate its API key..."
docker compose --project-name ares up -d mobsf

echo -n "  Waiting for MoBSF to become healthy"
for i in $(seq 1 72); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' ares-mobsf 2>/dev/null || echo "waiting")
    if [[ "$STATUS" == "healthy" ]]; then
        echo
        break
    fi
    echo -n "."
    sleep 5
    if [[ $i -eq 72 ]]; then
        echo
        die "MoBSF did not become healthy after 6 minutes. Check: docker logs ares-mobsf"
    fi
done
success "MoBSF healthy"

# Extract the key MoBSF generated internally
MOBSF_API_KEY=$(docker logs ares-mobsf 2>&1 | grep -oP 'REST API Key:\s*\K\S+' | tail -1)
if [[ -z "$MOBSF_API_KEY" ]]; then
    MOBSF_API_KEY=$(docker logs ares-mobsf 2>&1 | grep -oP 'Api Key\s*:\s*\K\S+' | tail -1)
fi
[[ -n "$MOBSF_API_KEY" ]] || die "Could not extract MoBSF API key. Run: docker logs ares-mobsf"

# Validate — MoBSF keys are hex strings; reject anything surprising
[[ "$MOBSF_API_KEY" =~ ^[a-fA-F0-9]+$ ]] || die "MoBSF key has unexpected format: $MOBSF_API_KEY"

# Replace the placeholder using | as delimiter (safe against keys containing /)
sed -i "s|^MOBSF_API_KEY=.*|MOBSF_API_KEY=${MOBSF_API_KEY}|" .env
success "MoBSF API key extracted and saved"

# ── Start the full stack ───────────────────────────────────────────────────────

info "Starting full stack..."
# shellcheck disable=SC2086  # $COMPOSE_ANDROID_PROFILE is intentionally unquoted (may be empty)
docker compose --project-name ares $COMPOSE_ANDROID_PROFILE up -d
success "Stack started"

# ── Connect Docker Android emulator to host ADB server (Linux only) ──────────

if [[ -n "$COMPOSE_ANDROID_PROFILE" ]] && command -v adb >/dev/null 2>&1; then
    echo -n "  Waiting for Android emulator ADB port"
    for i in $(seq 1 36); do
        if adb connect localhost:5555 2>&1 | grep -q "connected"; then
            echo; success "Android emulator connected (localhost:5555)"; break
        fi
        echo -n "."; sleep 5
        if [[ $i -eq 36 ]]; then
            echo
            warn "Emulator ADB not reachable yet. Run manually: adb connect localhost:5555"
        fi
    done
fi

# ── Wait for Hermes ───────────────────────────────────────────────────────────

echo -n "  Waiting for Hermes to start"
for i in $(seq 1 30); do
    if docker inspect --format='{{.State.Status}}' ares-hermes 2>/dev/null | grep -q running; then
        echo; break
    fi
    echo -n "."; sleep 3
done

# ── Wait for SwarmClaw ────────────────────────────────────────────────────────

echo -n "  Waiting for SwarmClaw to start"
SWARMCLAW_READY=false
for i in $(seq 1 90); do
    if curl -sf http://localhost:3456/api/healthz >/dev/null 2>&1; then
        echo; SWARMCLAW_READY=true; break
    fi
    echo -n "."; sleep 5
done
if [[ "$SWARMCLAW_READY" == "false" ]]; then
    echo
    warn "SwarmClaw not reachable yet (npm install on first run takes ~60s)."
    warn "Re-run setup.sh once it's up to complete the SwarmClaw seed."
fi

# ── Seed SwarmClaw ────────────────────────────────────────────────────────────
# Writes Hermes endpoint + Ares Pentest agent + skips setup wizard directly
# into the SwarmClaw SQLite DB. HTTP API requires session auth, so direct DB
# write via better-sqlite3 (already bundled with SwarmClaw) is the right path.

if [[ "$SWARMCLAW_READY" == "true" ]]; then
    info "Seeding SwarmClaw config..."
    sleep 3  # let DB migrations complete
    docker cp swarmclaw-seed.js ares-swarmclaw:/tmp/swarmclaw-seed.js
    if docker exec -e HERMES_API_URL="http://hermes:8643" ares-swarmclaw \
        node /tmp/swarmclaw-seed.js; then
        success "SwarmClaw seeded — Ares Pentest agent ready"
    else
        warn "SwarmClaw seed failed — configure manually at http://localhost:3456"
    fi
fi

# ── Verify ────────────────────────────────────────────────────────────────────

echo
info "Verifying stack..."
echo
docker compose --project-name ares ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo

SWARMCLAW_HTTP=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:3456 2>/dev/null || echo "unreachable")
[[ "$SWARMCLAW_HTTP" == "200" ]] \
    && success "SwarmClaw:  http://localhost:3456" \
    || warn    "SwarmClaw:  not yet reachable ($SWARMCLAW_HTTP) — retry in ~60s"

MOBSF_HTTP=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Authorization: ${MOBSF_API_KEY}" \
    "http://localhost:8100/api/v1/scans?page=1" 2>/dev/null || echo "unreachable")
[[ "$MOBSF_HTTP" == "200" ]] \
    && success "MoBSF:      http://localhost:8100" \
    || warn    "MoBSF:      not reachable ($MOBSF_HTTP)"

echo
echo "────────────────────────────────────────────────────────────"
echo "  Ares is running."
echo
echo "  Web UI:    http://localhost:3456"
echo "  Output:    ${PENTEST_OUTPUT}"
echo
echo "  Start an engagement:"
echo "    Open http://localhost:3456 → Ares Pentest → new chat → send:"
echo '    "Full web app assessment on https://target.example.com'
echo '    Scope: target.example.com. Auth: admin/pass. Go."'
echo "────────────────────────────────────────────────────────────"
