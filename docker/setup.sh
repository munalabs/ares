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

echo
echo "Android testing (ADB) is optional. Skip if you don't need mobile testing."
read -rp "  Configure ADB for Android testing? [y/N] " ADB_ENABLE
ADB_ENABLE="${ADB_ENABLE:-n}"
ADB_SERIAL_VAL="localhost:5555"
ANDROID_ADB_SERVER_HOST_VAL=""

if [[ "$ADB_ENABLE" =~ ^[Yy]$ ]]; then
    # ── Detect host platform ───────────────────────────────────────────────────
    HOST_OS="$(uname -s)"     # Darwin | Linux
    HOST_ARCH="$(uname -m)"   # x86_64 | arm64 | aarch64

    # ── Default ADB serial and Frida arch by platform ─────────────────────────
    # macOS: Docker emulator unavailable (no /dev/kvm in Docker Desktop VM).
    #   Use Android Studio AVD. AVD exposes ADB on localhost:5554 by default.
    #   Apple Silicon AVDs are ARM64; Intel Macs are x86_64.
    # Linux: Docker emulator available (--profile android, requires /dev/kvm).
    #   Default serial is localhost:5555 (budtmo container ADB port).
    #   x86_64 emulator, so Frida server is x86_64.
    if [[ "$HOST_OS" == "Darwin" ]]; then
        # macOS: AVD serial is emulator-5554 (ADB server manages this — not a TCP address)
        DEFAULT_ADB_SERIAL="emulator-5554"
        DEFAULT_ADB_SERVER_HOST="host.docker.internal"
        # Apple Silicon (arm64) → ARM64 AVD; Intel → x86_64 AVD
        if [[ "$HOST_ARCH" == "arm64" ]]; then
            DEFAULT_FRIDA_ARCH="arm64"
            info "macOS Apple Silicon detected → Android Studio AVD (ARM64), ADB serial emulator-5554"
        else
            DEFAULT_FRIDA_ARCH="x86_64"
            info "macOS Intel detected → Android Studio AVD (x86_64), ADB serial emulator-5554"
        fi
    else
        # budtmo/docker-android exposes ADB over TCP at localhost:5555 — serial is localhost:5555,
        # not emulator-XXXX (that format is only for emulators launched via the emulator binary)
        DEFAULT_ADB_SERIAL="localhost:5555"
        DEFAULT_ADB_SERVER_HOST="host.docker.internal"
        DEFAULT_FRIDA_ARCH="x86_64"
        info "Linux detected → Docker emulator (--profile android), ADB serial localhost:5555"
    fi

    echo
    echo "  ADB bridge: the container talks to the ADB server on this host."
    echo "  The host ADB server must listen on all interfaces:"
    echo "    adb kill-server && adb -a -P 5037 nodaemon server start"
    echo
    echo "  Other serial options:"
    echo "    <device-serial>   — USB phone (from \`adb devices\`)"
    echo "    <device-ip>:5555  — WiFi / Tailscale phone  →  use arm64 for Frida"
    echo
    read -rp "  ADB_SERIAL [${DEFAULT_ADB_SERIAL}]: " ADB_SERIAL_VAL
    ADB_SERIAL_VAL="${ADB_SERIAL_VAL:-$DEFAULT_ADB_SERIAL}"

    read -rp "  ANDROID_ADB_SERVER_HOST [${DEFAULT_ADB_SERVER_HOST}]: " ANDROID_ADB_SERVER_HOST_VAL
    ANDROID_ADB_SERVER_HOST_VAL="${ANDROID_ADB_SERVER_HOST_VAL:-$DEFAULT_ADB_SERVER_HOST}"

    echo
    echo "  Frida server architecture (must match Android device CPU):"
    echo "    x86_64 — Linux Docker emulator, Android Studio AVD on Intel"
    echo "    arm64  — Apple Silicon AVD, most physical phones"
    read -rp "  FRIDA_SERVER_ARCH [${DEFAULT_FRIDA_ARCH}]: " FRIDA_SERVER_ARCH_VAL
    FRIDA_SERVER_ARCH_VAL="${FRIDA_SERVER_ARCH_VAL:-$DEFAULT_FRIDA_ARCH}"
    [[ "$FRIDA_SERVER_ARCH_VAL" == "x86_64" || "$FRIDA_SERVER_ARCH_VAL" == "arm64" ]] \
        || die "FRIDA_SERVER_ARCH must be x86_64 or arm64, got: $FRIDA_SERVER_ARCH_VAL"

    # Sanity-check for injection
    [[ "$ADB_SERIAL_VAL" =~ ^[A-Za-z0-9._:/-]+$ ]] \
        || die "ADB_SERIAL contains unexpected characters: $ADB_SERIAL_VAL"
    [[ "$ANDROID_ADB_SERVER_HOST_VAL" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "ANDROID_ADB_SERVER_HOST contains unexpected characters: $ANDROID_ADB_SERVER_HOST_VAL"

    # ── Start host ADB server listening on all interfaces ─────────────────────
    if command -v adb >/dev/null 2>&1; then
        adb kill-server 2>/dev/null || true
        if adb -a -P 5037 nodaemon server start >/dev/null 2>&1; then
            success "Host ADB server started on 0.0.0.0:5037"
        else
            warn "Could not start ADB server with -a flag. Start it manually:"
            warn "  adb kill-server && adb -a -P 5037 nodaemon server start"
        fi
    else
        if [[ "$HOST_OS" == "Darwin" ]]; then
            warn "adb not found. Install via: brew install android-platform-tools"
        else
            warn "adb not found. Install via: sudo apt install android-sdk-platform-tools"
        fi
        warn "Then run: adb kill-server && adb -a -P 5037 nodaemon server start"
    fi

    success "ADB configured (serial: $ADB_SERIAL_VAL, frida: $FRIDA_SERVER_ARCH_VAL)"
else
    DEFAULT_ADB_SERIAL="localhost:5555"
    DEFAULT_ADB_SERVER_HOST="host.docker.internal"
    DEFAULT_FRIDA_ARCH="x86_64"
    ADB_SERIAL_VAL="$DEFAULT_ADB_SERIAL"
    ANDROID_ADB_SERVER_HOST_VAL="$DEFAULT_ADB_SERVER_HOST"
    FRIDA_SERVER_ARCH_VAL="$DEFAULT_FRIDA_ARCH"
    warn "ADB skipped — mobile testing disabled"
fi

# ── Android emulator ──────────────────────────────────────────────────────────

COMPOSE_ANDROID_PROFILE=""   # set to "--profile android" if Docker emulator is wanted

if [[ "$ADB_ENABLE" =~ ^[Yy]$ ]]; then
    echo
    read -rp "  Set up Android emulator? [y/N] " EMU_ENABLE
    EMU_ENABLE="${EMU_ENABLE:-n}"

    if [[ "$EMU_ENABLE" =~ ^[Yy]$ ]]; then

        if [[ "$HOST_OS" == "Darwin" ]]; then
            # ── macOS: Android Studio AVD via command-line tools ──────────────
            # ADB connects through the host ADB server — no USB passthrough needed.
            # The emulator uses Apple's Hypervisor Framework (no /dev/kvm required).

            # Locate Android SDK — check ANDROID_HOME first, then standard paths
            ANDROID_SDK="${ANDROID_HOME:-}"
            for candidate in \
                "$HOME/Library/Android/sdk" \
                "/usr/local/lib/android/sdk" \
                "/opt/homebrew/lib/android/sdk"; do
                if [[ -z "$ANDROID_SDK" && -d "$candidate/emulator" ]]; then
                    ANDROID_SDK="$candidate"
                fi
            done

            EMULATOR_BIN="${ANDROID_SDK}/emulator/emulator"
            AVDMANAGER_BIN="${ANDROID_SDK}/cmdline-tools/latest/bin/avdmanager"
            SDKMANAGER_BIN="${ANDROID_SDK}/cmdline-tools/latest/bin/sdkmanager"

            if [[ ! -x "$EMULATOR_BIN" ]]; then
                warn "Android emulator not found. Install Android Studio:"
                warn "  brew install --cask android-studio"
                warn "Then open Android Studio → SDK Manager → install:"
                warn "  Android 14 (API 34) system image + emulator"
                warn "Re-run setup.sh after installation to create the AVD automatically."
            else
                # Determine system image for this chip
                if [[ "$HOST_ARCH" == "arm64" ]]; then
                    SYS_IMG="system-images;android-34;google_apis;arm64-v8a"
                else
                    SYS_IMG="system-images;android-34;google_apis;x86_64"
                fi

                # Install system image if missing
                if ! "$SDKMANAGER_BIN" --list_installed 2>/dev/null | grep -q "${SYS_IMG}"; then
                    info "Installing Android system image (${SYS_IMG})..."
                    yes | "$SDKMANAGER_BIN" --licenses > /dev/null 2>&1 || true
                    "$SDKMANAGER_BIN" "$SYS_IMG" \
                        || die "Failed to install system image. Check Android SDK setup."
                    success "System image installed"
                fi

                # Create AVD if it doesn't already exist
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

                # Start emulator headless (background)
                info "Starting ares-android emulator (headless)..."
                nohup "$EMULATOR_BIN" \
                    -avd ares-android \
                    -no-window -no-audio -no-boot-anim \
                    -gpu swiftshader_indirect \
                    -memory 3072 \
                    > /tmp/ares-avd.log 2>&1 &
                EMU_PID=$!

                # Wait for boot — emulator registers with ADB server as it starts
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
                    warn "Emulator still booting — it can take 2-3 minutes on first start."
                    warn "Check: adb devices (should show emulator-5554)"
                    warn "Log: /tmp/ares-avd.log"
                fi
            fi

        else
            # ── Linux: Docker emulator via --profile android ──────────────────
            # budtmo/docker-android provides Android 13 with ADB on port 5555.
            # Requires /dev/kvm — bare metal only (VMs need nested virtualization).

            if [[ ! -e /dev/kvm ]]; then
                warn "/dev/kvm not found — Android Docker emulator requires bare metal or"
                warn "nested virtualization. Skipping emulator setup."
                warn "For physical device testing, connect via USB and set ADB_SERIAL accordingly."
            else
                info "KVM available — Android emulator will start with --profile android"
                COMPOSE_ANDROID_PROFILE="--profile android"

                # Verify kvm group membership — budtmo container runs the emulator as non-root
                if ! id -nG "$USER" | grep -qw kvm; then
                    warn "User $USER is not in the kvm group. The container needs kvm access:"
                    warn "  sudo usermod -aG kvm $USER && newgrp kvm"
                fi

                # budtmo/docker-android connects ADB over TCP on port 5555.
                # After the container starts, connect the host ADB server to it:
                #   adb connect localhost:5555
                # setup.sh does this after docker compose up (see below).
                ADB_SERIAL_VAL="localhost:5555"
            fi
        fi
    fi
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
