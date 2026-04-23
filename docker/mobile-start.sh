#!/usr/bin/env bash
# mobile-start.sh — Start Android mobile testing bridges for Ares
#
# Works on both macOS and Linux.
# On macOS: socat binds to 192.168.64.1 (Docker Desktop gateway)
# On Linux: socat binds to 0.0.0.0 (standard Docker bridge)
#
# Usage:
#   ./mobile-start.sh          — emulator mode (default)
#   ./mobile-start.sh --usb    — physical device via USB
#   ./mobile-start.sh --stop   — kill socat bridges

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}▶${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }
die()     { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── OS detection ──────────────────────────────────────────────────────────────
OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
    DOCKER_HOST_IP=$(grep -E "^ANDROID_ADB_SERVER_HOST=" .env 2>/dev/null | cut -d= -f2 || echo "192.168.64.1")
    DOCKER_HOST_IP="${DOCKER_HOST_IP:-192.168.64.1}"
    SOCAT_BIND="bind=${DOCKER_HOST_IP},"
else
    DOCKER_HOST_IP="172.17.0.1"
    SOCAT_BIND=""
fi

# ── Homebrew PATH ─────────────────────────────────────────────────────────────
[[ -d /opt/homebrew/bin ]] && export PATH="/opt/homebrew/bin:$PATH"
[[ -d /usr/local/bin    ]] && export PATH="/usr/local/bin:$PATH"

# ── Args ──────────────────────────────────────────────────────────────────────
USB_MODE=false
STOP_MODE=false
if [[ $# -gt 0 ]]; then
    for arg in "$@"; do
        case "$arg" in
            --usb)  USB_MODE=true ;;
            --stop) STOP_MODE=true ;;
            *)      die "Unknown argument: $arg. Usage: $0 [--usb|--stop]" ;;
        esac
    done
fi

# ── Stop mode ─────────────────────────────────────────────────────────────────
if [[ "$STOP_MODE" == "true" ]]; then
    info "Stopping mobile bridges..."
    pkill -f "socat.*TCP-LISTEN:5038"  2>/dev/null && success "ADB bridge stopped"   || warn "ADB bridge was not running"
    pkill -f "socat.*TCP-LISTEN:27042" 2>/dev/null && success "Frida bridge stopped" || warn "Frida bridge was not running"
    adb forward --remove-all 2>/dev/null || true
    sleep 2
    echo
    info "Emulator is still running. To stop it:"
    echo "  adb -s emulator-5554 emu kill"
    exit 0
fi

echo
echo "────────────────────────────────────────────────────────────"
echo "  Ares Mobile Testing — Bridge Setup (${OS})"
echo "  Docker host IP: ${DOCKER_HOST_IP}"
echo "────────────────────────────────────────────────────────────"
echo

# ── Dependency checks ─────────────────────────────────────────────────────────
command -v adb   >/dev/null 2>&1 || die "adb not found. Install: brew install android-platform-tools"
command -v socat >/dev/null 2>&1 || die "socat not found. Install: brew install socat"

# ── Device detection ──────────────────────────────────────────────────────────
if [[ "$USB_MODE" == "true" ]]; then
    info "USB mode — waiting for device..."
    adb wait-for-device 2>/dev/null &
    WAIT_PID=$!
    sleep 5
    kill "$WAIT_PID" 2>/dev/null || true

    DEVICE_SERIAL=$(adb devices | grep -v "^List" | grep "device$" | head -1 | awk '{print $1}')
    [[ -n "$DEVICE_SERIAL" ]] || die "No USB device found. Enable USB debugging and authorize this computer."
    success "USB device: $DEVICE_SERIAL"
else
    DEVICE_SERIAL="emulator-5554"

    if ! adb devices 2>/dev/null | grep -q "emulator-5554.*device"; then
        ANDROID_SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
        EMULATOR_BIN="$ANDROID_SDK/emulator/emulator"
        [[ -x "$EMULATOR_BIN" ]] || die "Android emulator not found at $EMULATOR_BIN. Run setup.sh --android first."

        AVDMANAGER="$ANDROID_SDK/cmdline-tools/latest/bin/avdmanager"
        "$AVDMANAGER" list avd 2>/dev/null | grep -q "ares-android" \
            || die "AVD 'ares-android' not found. Run: bash setup.sh --android"

        if [[ ! -x "${JAVA_HOME:-}/bin/java" ]]; then
            for _jdk in \
                "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
                "/Applications/Android Studio.app/Contents/jre/Contents/Home"; do
                [[ -x "$_jdk/bin/java" ]] && { export JAVA_HOME="$_jdk"; break; }
            done
        fi
        [[ -x "${JAVA_HOME:-}/bin/java" ]] || die "Java not found. Open Android Studio at least once."

        info "Starting ares-android emulator (headless)..."
        nohup "$EMULATOR_BIN" \
            -avd ares-android \
            -no-window -no-audio -no-boot-anim \
            -gpu swiftshader_indirect \
            -memory 3072 \
            >/tmp/ares-avd.log 2>&1 &

        echo -n "  Waiting for emulator to boot"
        for i in $(seq 1 60); do
            BOOT=$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null || true)
            BOOT="${BOOT//[[:space:]]/}"
            [[ "$BOOT" == "1" ]] && { echo; break; }
            echo -n "."; sleep 5
        done
        [[ "$BOOT" == "1" ]] || { echo; warn "Emulator still booting. Log: /tmp/ares-avd.log"; }
    fi

    adb devices 2>/dev/null | grep -q "emulator-5554.*device" \
        && success "Emulator: emulator-5554 (Android $(adb -s emulator-5554 shell getprop ro.build.version.release 2>/dev/null | tr -d '[:space:]'))" \
        || die "emulator-5554 not in 'device' state. Check: adb devices"
fi

# ── ADB socat bridge ──────────────────────────────────────────────────────────
echo
info "ADB bridge (${DOCKER_HOST_IP}:5038 → localhost:5037)..."
pkill -f "socat.*TCP-LISTEN:5038" 2>/dev/null || true
sleep 3

adb devices >/dev/null 2>&1

nohup socat TCP-LISTEN:5038,${SOCAT_BIND}reuseaddr,fork TCP:127.0.0.1:5037 \
    >/tmp/ares-socat-adb.log 2>&1 &
ADB_SOCAT_PID=$!
sleep 2
kill -0 "$ADB_SOCAT_PID" 2>/dev/null \
    && success "ADB bridge: PID $ADB_SOCAT_PID (${DOCKER_HOST_IP}:5038 → 127.0.0.1:5037)" \
    || die "ADB socat failed — check /tmp/ares-socat-adb.log"

# ── Frida server ──────────────────────────────────────────────────────────────
echo
info "Frida server on device..."

if [[ "$DEVICE_SERIAL" == "emulator-5554" ]]; then
    ADB_USER=$(adb -s "$DEVICE_SERIAL" shell whoami 2>/dev/null || true)
    ADB_USER="${ADB_USER//[[:space:]]/}"
    if [[ "$ADB_USER" != "root" ]]; then
        adb -s "$DEVICE_SERIAL" root 2>/dev/null || true
        sleep 3
    fi
fi

FRIDA_ON_DEVICE=$(adb -s "$DEVICE_SERIAL" shell "ls /data/local/tmp/frida-server" 2>/dev/null || true)
FRIDA_ON_DEVICE="${FRIDA_ON_DEVICE//[[:space:]]/}"
if [[ -z "$FRIDA_ON_DEVICE" ]]; then
    DEVICE_ARCH=$(adb -s "$DEVICE_SERIAL" shell getprop ro.product.cpu.abi 2>/dev/null || true)
    DEVICE_ARCH="${DEVICE_ARCH//[[:space:]]/}"
    case "$DEVICE_ARCH" in
        arm64-v8a)   FRIDA_ARCH="arm64" ;;
        armeabi-v7a) FRIDA_ARCH="arm" ;;
        x86_64)      FRIDA_ARCH="x86_64" ;;
        x86)         FRIDA_ARCH="x86" ;;
        *)           FRIDA_ARCH="arm64" ;;
    esac

    FRIDA_BIN="/opt/mcp/frida-server/frida-server-android-${FRIDA_ARCH}"

    if docker exec ares-hermes test -f "$FRIDA_BIN" 2>/dev/null; then
        info "Pushing frida-server ($FRIDA_ARCH) from hermes to device..."
        TMPFILE="/tmp/frida-server-${FRIDA_ARCH}"
        docker cp "ares-hermes:${FRIDA_BIN}" "$TMPFILE"
        adb -s "$DEVICE_SERIAL" push "$TMPFILE" /data/local/tmp/frida-server
        adb -s "$DEVICE_SERIAL" shell chmod 755 /data/local/tmp/frida-server
        rm -f "$TMPFILE"
        success "frida-server pushed"
    else
        warn "frida-server binary not found in hermes ($FRIDA_BIN). Rebuild the image."
    fi
fi

FRIDA_RUNNING=$(adb -s "$DEVICE_SERIAL" shell "ps -A 2>/dev/null" 2>/dev/null | grep "frida-server" | grep -v grep | head -1 || true)
if [[ -n "$FRIDA_RUNNING" ]]; then
    FRIDA_LISTENING=$(adb -s "$DEVICE_SERIAL" shell "ss -tlnp 2>/dev/null | grep 27042" 2>/dev/null || true)
    if echo "$FRIDA_LISTENING" | grep -q "0.0.0.0"; then
        success "frida-server already running (0.0.0.0:27042)"
    else
        warn "frida-server running but bound to loopback only — restarting with -l 0.0.0.0"
        adb -s "$DEVICE_SERIAL" shell "pkill frida-server 2>/dev/null || true" 2>/dev/null || true
        sleep 2
        adb -s "$DEVICE_SERIAL" shell "nohup /data/local/tmp/frida-server -l 0.0.0.0 >/dev/null 2>&1 &" || true
        sleep 3
        success "frida-server restarted (0.0.0.0:27042)"
    fi
else
    info "Starting frida-server..."
    adb -s "$DEVICE_SERIAL" shell "nohup /data/local/tmp/frida-server -l 0.0.0.0 >/dev/null 2>&1 &" || true
    sleep 4
    FRIDA_RUNNING=$(adb -s "$DEVICE_SERIAL" shell "ps -A 2>/dev/null" 2>/dev/null | grep "frida-server" | grep -v grep | head -1 || true)
    [[ -n "$FRIDA_RUNNING" ]] \
        && success "frida-server started (0.0.0.0:27042)" \
        || die "frida-server failed to start. Check: adb -s $DEVICE_SERIAL shell 'logcat | grep frida'"
fi

# ── Frida socat bridge ────────────────────────────────────────────────────────
echo
info "Frida bridge (${DOCKER_HOST_IP}:27042 → localhost:27042)..."

# Clean up stale forward and any process holding the port
pkill -f "socat.*TCP-LISTEN:27042" 2>/dev/null || true
adb -s "$DEVICE_SERIAL" forward --remove tcp:27042 2>/dev/null || true
sleep 2

adb -s "$DEVICE_SERIAL" forward tcp:27042 tcp:27042
success "ADB forward: $DEVICE_SERIAL tcp:27042 → device tcp:27042"

nohup socat TCP-LISTEN:27042,${SOCAT_BIND}reuseaddr,fork TCP:127.0.0.1:27042 \
    >/tmp/ares-socat-frida.log 2>&1 &
FRIDA_SOCAT_PID=$!
sleep 2
kill -0 "$FRIDA_SOCAT_PID" 2>/dev/null \
    && success "Frida bridge: PID $FRIDA_SOCAT_PID (${DOCKER_HOST_IP}:27042 → 127.0.0.1:27042)" \
    || die "Frida socat failed — check /tmp/ares-socat-frida.log"

# ── Connectivity test from hermes ─────────────────────────────────────────────
echo
info "Verifying connectivity from hermes container..."

ADB_RESULT=$(docker exec ares-hermes \
    /usr/lib/android-sdk/platform-tools/adb \
    -H "${DOCKER_HOST_IP}" -P 5038 -s "$DEVICE_SERIAL" \
    shell getprop ro.product.model 2>&1 || true)
ADB_RESULT="${ADB_RESULT//[[:space:]]/}"
[[ -n "$ADB_RESULT" ]] \
    && success "ADB from hermes: $DEVICE_SERIAL → $ADB_RESULT" \
    || warn "ADB from hermes: no response. Check ANDROID_ADB_SERVER_HOST in docker/.env"

FRIDA_RESULT=$(docker exec ares-hermes /usr/bin/python3 -c "
import frida, os, socket
host = os.environ.get('FRIDA_TCP_HOST', '${DOCKER_HOST_IP}')
port = int(os.environ.get('FRIDA_TCP_PORT', '27042'))
try:
    s = socket.create_connection((host, port), timeout=3)
    s.close()
    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(f'{host}:{port}')
    print(f'OK {dev.id}')
except Exception as e:
    print(f'ERROR {e}')
" 2>&1 || true)
FRIDA_RESULT="${FRIDA_RESULT//[[:space:]]/}"
if [[ "$FRIDA_RESULT" == OK* ]]; then
    success "Frida from hermes: connected to frida-server"
else
    warn "Frida from hermes: $FRIDA_RESULT"
    warn "Check: docker exec ares-hermes env | grep FRIDA_TCP"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────────────────────"
echo "  Mobile bridges ready."
echo "  OS:          ${OS}"
echo "  Device:      $DEVICE_SERIAL"
echo "  ADB bridge:  ${DOCKER_HOST_IP}:5038 → host ADB server (PID $ADB_SOCAT_PID)"
echo "  Frida:       ${DOCKER_HOST_IP}:27042 → frida-server on device (PID $FRIDA_SOCAT_PID)"
echo
echo "  These bridges stop when this terminal session closes."
echo "  To keep them alive, run this script from tmux or screen."
echo "  To stop bridges: ./mobile-start.sh --stop"
echo "────────────────────────────────────────────────────────────"
echo
