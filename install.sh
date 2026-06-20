#!/bin/sh
# Vigilus installer
# Usage: curl -fsSL https://vigilus.dev/install.sh | sh
#
# Installs Vigilus and registers it as a system service.
# Supports: Ubuntu 22.04+, Debian 12+, RHEL/Fedora, Arch Linux, macOS 13+
#
# Environment overrides (set before running):
#   VIGILUS_INSTALL_DIR   — where to install   (default: /opt/vigilus or ~/.vigilus)
#   VIGILUS_PORT          — listen port         (default: 8000)
#   VIGILUS_BRANCH        — git branch          (default: main)
set -e

REPO_URL="https://github.com/vigilus-labs/vigilus.git"
VIGILUS_BRANCH="${VIGILUS_BRANCH:-main}"
VIGILUS_PORT="${VIGILUS_PORT:-8000}"
MIN_PYTHON_MINOR=11   # require 3.11+
MIN_NODE_MAJOR=18     # require 18+

# ── Colour helpers ────────────────────────────────────────────────────────────

_tty() { [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; }

_bold()   { _tty && printf '\033[1m%s\033[0m'  "$1" || printf '%s' "$1"; }
_green()  { _tty && printf '\033[0;32m%s\033[0m' "$1" || printf '%s' "$1"; }
_yellow() { _tty && printf '\033[0;33m%s\033[0m' "$1" || printf '%s' "$1"; }
_red()    { _tty && printf '\033[0;31m%s\033[0m' "$1" || printf '%s' "$1"; }
_cyan()   { _tty && printf '\033[0;36m%s\033[0m' "$1" || printf '%s' "$1"; }

info()  { printf '  %s %s\n' "$(_cyan '→')" "$1"; }
step()  { printf '\n%s %s\n' "$(_bold '▶')" "$(_bold "$1")"; }
ok()    { printf '  %s %s\n' "$(_green '✓')" "$1"; }
warn()  { printf '  %s %s\n' "$(_yellow '!')" "$1"; }
die()   { printf '\n%s %s\n\n' "$(_red 'Error:')" "$1" >&2; exit 1; }

# ── Platform detection ────────────────────────────────────────────────────────

detect_os() {
    OS=""
    DISTRO=""
    PKG_MGR=""

    case "$(uname -s)" in
        Linux)
            OS="linux"
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                DISTRO=$(. /etc/os-release && printf '%s' "${ID:-}")
                DISTRO_LIKE=$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")
            fi
            case "$DISTRO" in
                ubuntu|debian|linuxmint|pop)
                    PKG_MGR="apt";;
                rhel|centos|almalinux|rocky)
                    PKG_MGR="dnf";;
                fedora)
                    PKG_MGR="dnf";;
                arch|manjaro|endeavouros)
                    PKG_MGR="pacman";;
                opensuse*|sles)
                    PKG_MGR="zypper";;
                *)
                    # Fall back to ID_LIKE
                    case "$DISTRO_LIKE" in
                        *debian*|*ubuntu*) PKG_MGR="apt";;
                        *rhel*|*fedora*)   PKG_MGR="dnf";;
                        *arch*)            PKG_MGR="pacman";;
                        *)                 PKG_MGR="unknown";;
                    esac
                    ;;
            esac
            ;;
        Darwin)
            OS="macos"
            DISTRO="macos"
            PKG_MGR="brew"
            ;;
        *)
            die "Unsupported operating system: $(uname -s)"
            ;;
    esac
}

# ── Privilege helpers ─────────────────────────────────────────────────────────

IS_ROOT=0
HAS_SUDO=0

check_privileges() {
    if [ "$(id -u)" -eq 0 ]; then
        IS_ROOT=1
        SUDO=""
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        HAS_SUDO=1
        SUDO="sudo"
    elif command -v sudo >/dev/null 2>&1; then
        warn "sudo is available but requires a password."
        warn "You may be prompted during installation."
        HAS_SUDO=1
        SUDO="sudo"
    fi
}

can_use_system_install() {
    [ "$IS_ROOT" -eq 1 ] || [ "$HAS_SUDO" -eq 1 ]
}

# ── Install directory ─────────────────────────────────────────────────────────

resolve_install_dir() {
    if [ -n "${VIGILUS_INSTALL_DIR:-}" ]; then
        INSTALL_DIR="$VIGILUS_INSTALL_DIR"
    elif can_use_system_install; then
        INSTALL_DIR="/opt/vigilus"
    else
        INSTALL_DIR="$HOME/.vigilus"
    fi

    BACKEND_DIR="$INSTALL_DIR/backend"
    FRONTEND_DIR="$INSTALL_DIR/frontend"
    ENV_FILE="$BACKEND_DIR/.env"
    DATA_DIR="$BACKEND_DIR/data"
}

# ── Dependency checks ─────────────────────────────────────────────────────────

check_cmd() { command -v "$1" >/dev/null 2>&1; }

python_minor_version() {
    # Returns the minor version number of python3, or 0 if not installed/too old
    py_ver=$(python3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    if [ -z "$py_ver" ]; then
        printf '0'
        return
    fi
    printf '%s' "$py_ver" | cut -d. -f2
}

node_major_version() {
    node_ver=$(node --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
    printf '%s' "${node_ver:-0}"
}

# ── System package installation ───────────────────────────────────────────────

pkg_install() {
    case "$PKG_MGR" in
        apt)
            $SUDO apt-get install -y -qq "$@" >/dev/null
            ;;
        dnf)
            $SUDO dnf install -y -q "$@" >/dev/null
            ;;
        pacman)
            $SUDO pacman -S --noconfirm --needed -q "$@" >/dev/null
            ;;
        zypper)
            $SUDO zypper install -y -q "$@" >/dev/null
            ;;
        brew)
            brew install -q "$@" >/dev/null
            ;;
        *)
            warn "Unknown package manager — skipping auto-install of: $*"
            ;;
    esac
}

ensure_base_deps() {
    step "Checking base dependencies"

    case "$PKG_MGR" in
        apt)
            $SUDO apt-get update -qq >/dev/null
            pkg_install git curl
            ;;
        dnf)
            pkg_install git curl
            ;;
        pacman)
            $SUDO pacman -Sy --noconfirm -q >/dev/null 2>&1
            pkg_install git curl
            ;;
        brew)
            if ! check_cmd brew; then
                die "Homebrew is required on macOS. Install it first:\n  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            fi
            pkg_install git
            ;;
    esac

    ok "git and curl available"
}

ensure_python() {
    step "Checking Python 3.11+"

    minor=$(python_minor_version)
    if [ "$minor" -ge "$MIN_PYTHON_MINOR" ] 2>/dev/null; then
        ok "Python 3.${minor} found"
        PYTHON_BIN="python3"
        return
    fi

    info "Python 3.${MIN_PYTHON_MINOR}+ not found (have: 3.${minor:-?}). Installing..."

    case "$PKG_MGR" in
        apt)
            # Try system package first (Debian 12 / Ubuntu 24.04 ship 3.11+)
            if apt-cache show python3.12 >/dev/null 2>&1; then
                pkg_install python3.12 python3.12-venv python3.12-dev
                PYTHON_BIN="python3.12"
            elif apt-cache show python3.11 >/dev/null 2>&1; then
                pkg_install python3.11 python3.11-venv python3.11-dev
                PYTHON_BIN="python3.11"
            else
                # Fall back to deadsnakes PPA (Ubuntu 20.04 / 22.04)
                info "Adding deadsnakes PPA for Python 3.12..."
                pkg_install software-properties-common
                $SUDO add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
                $SUDO apt-get update -qq >/dev/null
                pkg_install python3.12 python3.12-venv python3.12-dev
                PYTHON_BIN="python3.12"
            fi
            ;;
        dnf)
            pkg_install python3.11 python3.11-devel
            PYTHON_BIN="python3.11"
            ;;
        pacman)
            pkg_install python
            PYTHON_BIN="python3"
            ;;
        brew)
            brew install -q python@3.12 >/dev/null
            PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3"
            ;;
        *)
            die "Cannot install Python automatically. Please install Python 3.11+ and re-run."
            ;;
    esac

    minor=$(python_minor_version)
    ok "Python 3.${minor} ready ($PYTHON_BIN)"
}

ensure_node() {
    step "Checking Node.js ${MIN_NODE_MAJOR}+"

    major=$(node_major_version)
    if [ "$major" -ge "$MIN_NODE_MAJOR" ] 2>/dev/null; then
        ok "Node.js ${major} found"
        return
    fi

    info "Node.js ${MIN_NODE_MAJOR}+ not found (have: ${major:-none}). Installing..."

    case "$PKG_MGR" in
        apt)
            # NodeSource setup script
            curl -fsSL "https://deb.nodesource.com/setup_20.x" | $SUDO sh - >/dev/null 2>&1
            pkg_install nodejs
            ;;
        dnf)
            $SUDO dnf module install -y -q nodejs:20 >/dev/null 2>&1 || pkg_install nodejs npm
            ;;
        pacman)
            pkg_install nodejs npm
            ;;
        brew)
            brew install -q node@20 >/dev/null
            brew link --force --overwrite node@20 >/dev/null 2>&1 || true
            ;;
        *)
            die "Cannot install Node.js automatically. Please install Node.js ${MIN_NODE_MAJOR}+ and re-run."
            ;;
    esac

    major=$(node_major_version)
    ok "Node.js ${major} ready"
}

# ── Clone or update ───────────────────────────────────────────────────────────

fetch_source() {
    step "Fetching Vigilus source"

    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Existing installation found — updating..."
        git -C "$INSTALL_DIR" fetch --quiet origin
        git -C "$INSTALL_DIR" checkout --quiet "$VIGILUS_BRANCH"
        git -C "$INSTALL_DIR" reset --hard --quiet "origin/$VIGILUS_BRANCH"
        ok "Updated to latest $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
    else
        if can_use_system_install; then
            $SUDO mkdir -p "$INSTALL_DIR"
            $SUDO chown "$(id -u):$(id -g)" "$INSTALL_DIR"
        else
            mkdir -p "$INSTALL_DIR"
        fi
        info "Cloning from $REPO_URL..."
        git clone --quiet --branch "$VIGILUS_BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
        ok "Cloned to $INSTALL_DIR"
    fi
}

# ── Python venv + backend ─────────────────────────────────────────────────────

# Ensure the stdlib venv/ensurepip support is installed for $PYTHON_BIN. The base
# python3 on Debian/Ubuntu ships without ensurepip — it lives in the matching
# pythonX.Y-venv package — so `python -m venv` would otherwise build a venv with
# no pip. (RHEL/Arch/macOS bundle ensurepip, so this is a no-op there.)
ensure_venv_module() {
    "$PYTHON_BIN" -m ensurepip --version >/dev/null 2>&1 && return 0

    case "$PKG_MGR" in
        apt)
            pyver=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
            if [ -n "$pyver" ] && apt-cache show "python${pyver}-venv" >/dev/null 2>&1; then
                pkg_install "python${pyver}-venv"
            else
                pkg_install python3-venv
            fi
            ;;
        zypper)
            pkg_install python3-venv >/dev/null 2>&1 || true
            ;;
    esac
}

setup_backend() {
    step "Setting up Python backend"

    VENV_DIR="$BACKEND_DIR/.venv"
    PIP="$VENV_DIR/bin/pip"
    VIGILUS_BIN="$VENV_DIR/bin/vigilus"

    # A venv that exists but has no pip was built by a prior run before
    # ensurepip/python3-venv was available. Reusing it is what breaks the
    # install, so detect and rebuild it rather than trusting the directory.
    if [ -d "$VENV_DIR" ] && [ ! -x "$PIP" ]; then
        info "Existing virtual environment is missing pip — rebuilding it..."
        $SUDO rm -rf "$VENV_DIR"
    fi

    if [ ! -d "$VENV_DIR" ]; then
        ensure_venv_module
        info "Creating virtual environment..."
        if ! "$PYTHON_BIN" -m venv "$VENV_DIR" || [ ! -x "$PIP" ]; then
            die "Failed to create a working virtualenv. Install the venv module for your Python (e.g. 'sudo apt-get install python3-venv') and re-run."
        fi
    fi

    info "Installing backend dependencies (this may take a minute)..."
    "$PIP" install --quiet --upgrade pip >/dev/null
    "$PIP" install --quiet -e "$BACKEND_DIR" >/dev/null

    ok "Backend installed ($VIGILUS_BIN)"
}

# ── Frontend build ────────────────────────────────────────────────────────────

build_frontend() {
    step "Building frontend"

    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        info "Installing Node dependencies..."
        npm --prefix "$FRONTEND_DIR" install --silent >/dev/null
    fi

    info "Building production bundle..."
    npm --prefix "$FRONTEND_DIR" run build --silent >/dev/null

    ok "Frontend built → $FRONTEND_DIR/dist"
}

# ── Configuration ─────────────────────────────────────────────────────────────

configure() {
    step "Configuring Vigilus"

    mkdir -p "$DATA_DIR"

    if [ -f "$ENV_FILE" ] && grep -q "VIGILUS_SECRET=" "$ENV_FILE" 2>/dev/null; then
        ok ".env already exists — keeping existing secret (credentials stay decryptable)"
        return
    fi

    # Generate a cryptographically strong secret
    if check_cmd openssl; then
        SECRET=$(openssl rand -hex 32)
    else
        # /dev/urandom fallback — base64 without padding chars
        SECRET=$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64 | tr -d '\n/+=' | head -c 64)
    fi

    cat > "$ENV_FILE" << ENVEOF
# Vigilus configuration — do not commit this file
# All variables use the VIGILUS_ prefix; see README for full reference.

VIGILUS_SECRET=${SECRET}

# Uncomment to change the listen port (default 8000):
# VIGILUS_PORT=${VIGILUS_PORT}

# Set to true if Vigilus is behind a TLS-terminating reverse proxy:
# VIGILUS_AUTH_COOKIE_SECURE=true

# Uncomment and set to use PostgreSQL instead of SQLite:
# VIGILUS_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/vigilus
ENVEOF

    ok ".env created with generated secret"
    warn "Keep $ENV_FILE safe — it encrypts all stored credentials"
}

# ── Database init ─────────────────────────────────────────────────────────────

init_database() {
    step "Initialising database"

    VENV_DIR="$BACKEND_DIR/.venv"
    VIGILUS_BIN="$VENV_DIR/bin/vigilus"

    # Load env so vigilus picks up VIGILUS_SECRET
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a

    cd "$BACKEND_DIR"
    "$VIGILUS_BIN" init 2>&1 | grep -v "^INFO" | grep -v "alembic" || true
    cd - >/dev/null

    ok "Database ready"
}

# ── CLI wrapper ───────────────────────────────────────────────────────────────

# Put a `vigilus` command on PATH. The console script lives in the venv and
# reads .env (VIGILUS_SECRET) + ./data relative to the backend dir, so a bare
# symlink wouldn't work — the wrapper cd's into the backend dir first. When the
# service runs as the dedicated 'vigilus' user, the wrapper drops to that user so
# the CLI can read/write the data dir it owns.
install_cli_wrapper() {
    # $1 = "system" (exec as the vigilus service user) or "user" (exec as-is).
    _mode="$1"
    _venv="$BACKEND_DIR/.venv"

    if [ "$_mode" = "system" ]; then
        _exec="exec sudo -u vigilus \"${_venv}/bin/vigilus\" \"\$@\""
    else
        _exec="exec \"${_venv}/bin/vigilus\" \"\$@\""
    fi

    # Prefer /usr/local/bin (on PATH); fall back to ~/.local/bin without sudo.
    if [ "${IS_ROOT:-0}" -eq 1 ] || [ "${HAS_SUDO:-0}" -eq 1 ]; then
        _target="/usr/local/bin/vigilus"
        _place="$SUDO"
    elif [ -w /usr/local/bin ]; then
        _target="/usr/local/bin/vigilus"
        _place=""
    else
        mkdir -p "$HOME/.local/bin"
        _target="$HOME/.local/bin/vigilus"
        _place=""
    fi

    $_place tee "$_target" >/dev/null << WRAPEOF
#!/bin/sh
# Vigilus CLI wrapper (generated by install.sh). Runs the venv console script
# from the backend dir so it loads .env (VIGILUS_SECRET) and the ./data store.
cd "${BACKEND_DIR}" || exit 1
${_exec}
WRAPEOF
    $_place chmod +x "$_target"

    case "$_target" in
        "$HOME"/*)
            case ":$PATH:" in
                *":$HOME/.local/bin:"*) : ;;
                *) warn "Add \$HOME/.local/bin to your PATH to use the 'vigilus' command." ;;
            esac
            ;;
    esac

    ok "CLI installed → $_target"
}

# ── Systemd service (Linux with systemd) ──────────────────────────────────────

setup_service_linux() {
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemd not found — no service created."
        warn "Start Vigilus manually: $INSTALL_DIR/start.sh --build"
        return
    fi

    if ! can_use_system_install; then
        warn "No sudo — creating user-level systemd service instead."
        _setup_service_user_systemd
        return
    fi

    _setup_service_system_systemd
}

_setup_service_system_systemd() {
    SERVICE_FILE="/etc/systemd/system/vigilus.service"
    VENV_DIR="$BACKEND_DIR/.venv"

    # Create a dedicated system user if it doesn't exist
    if ! id vigilus >/dev/null 2>&1; then
        info "Creating system user 'vigilus'..."
        $SUDO useradd --system --no-create-home --shell /usr/sbin/nologin vigilus
    fi

    # Grant ownership of the install dir to the service user
    $SUDO chown -R vigilus:vigilus "$INSTALL_DIR"

    $SUDO tee "$SERVICE_FILE" >/dev/null << SVCEOF
[Unit]
Description=Vigilus AI Infrastructure Platform
Documentation=https://vigilus.dev
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=vigilus
Group=vigilus
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/uvicorn vigilus.main:app \\
    --host 0.0.0.0 \\
    --port ${VIGILUS_PORT} \\
    --log-level info
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR}
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
SVCEOF

    $SUDO systemctl daemon-reload
    $SUDO systemctl enable vigilus >/dev/null
    $SUDO systemctl restart vigilus

    ok "systemd service 'vigilus' enabled and started"
    SERVICE_URL="http://$(hostname -f 2>/dev/null || hostname):${VIGILUS_PORT}"

    install_cli_wrapper system
}

_setup_service_user_systemd() {
    SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
    SERVICE_FILE="$SYSTEMD_USER_DIR/vigilus.service"
    VENV_DIR="$BACKEND_DIR/.venv"

    mkdir -p "$SYSTEMD_USER_DIR"

    cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=Vigilus AI Infrastructure Platform
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/uvicorn vigilus.main:app \\
    --host 127.0.0.1 \\
    --port ${VIGILUS_PORT} \\
    --log-level info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SVCEOF

    systemctl --user daemon-reload
    systemctl --user enable vigilus >/dev/null
    systemctl --user restart vigilus

    ok "User systemd service 'vigilus' started (port ${VIGILUS_PORT})"
    SERVICE_URL="http://localhost:${VIGILUS_PORT}"

    install_cli_wrapper user
}

# ── macOS launchd ─────────────────────────────────────────────────────────────

setup_service_macos() {
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_FILE="$PLIST_DIR/dev.vigilus.plist"
    VENV_DIR="$BACKEND_DIR/.venv"
    LOG_DIR="$HOME/Library/Logs/vigilus"

    mkdir -p "$PLIST_DIR" "$LOG_DIR"

    cat > "$PLIST_FILE" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.vigilus</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/uvicorn</string>
        <string>vigilus.main:app</string>
        <string>--host</string><string>127.0.0.1</string>
        <string>--port</string><string>${VIGILUS_PORT}</string>
        <string>--log-level</string><string>info</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${BACKEND_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>VIGILUS_SECRET</key>
        <string>__loaded_from_env_file__</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/vigilus.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/vigilus.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLISTEOF

    # launchd doesn't source EnvironmentFile, so inject VIGILUS_SECRET directly
    SECRET_VAL=$(grep "^VIGILUS_SECRET=" "$ENV_FILE" | cut -d= -f2-)
    # Use a Python one-liner to safely replace the placeholder in the plist
    "$VENV_DIR/bin/python3" - "$PLIST_FILE" "$SECRET_VAL" << 'PYEOF'
import sys
path, secret = sys.argv[1], sys.argv[2]
content = open(path).read().replace('__loaded_from_env_file__', secret)
open(path, 'w').write(content)
PYEOF

    chmod 600 "$PLIST_FILE"
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load -w "$PLIST_FILE"

    ok "launchd service 'dev.vigilus' loaded and started"
    SERVICE_URL="http://localhost:${VIGILUS_PORT}"

    install_cli_wrapper user
}

# ── Service dispatch ──────────────────────────────────────────────────────────

setup_service() {
    step "Setting up system service"

    SERVICE_URL="http://localhost:${VIGILUS_PORT}"

    case "$OS" in
        linux)  setup_service_linux ;;
        macos)  setup_service_macos ;;
    esac
}

# ── Wait for service ──────────────────────────────────────────────────────────

wait_for_service() {
    info "Waiting for Vigilus to come up..."
    i=0
    while [ $i -lt 30 ]; do
        if curl -sf "http://localhost:${VIGILUS_PORT}/api/auth/setup" >/dev/null 2>&1; then
            ok "Vigilus is up"
            return
        fi
        i=$((i + 1))
        sleep 1
    done
    warn "Service did not respond within 30 s — check: journalctl -u vigilus -n 50"
}

# ── Final banner ──────────────────────────────────────────────────────────────

print_success() {
    SETUP_URL="$SERVICE_URL/setup"

    printf '\n'
    _tty && printf '\033[0;32m'
    printf '╔══════════════════════════════════════════════════╗\n'
    printf '║          Vigilus installed successfully!         ║\n'
    printf '╚══════════════════════════════════════════════════╝\n'
    _tty && printf '\033[0m'
    printf '\n'
    printf '  %s  %s\n' "$(_bold 'Open in browser:')" "$(_cyan "$SETUP_URL")"
    printf '\n'
    printf '  Create your admin account at the setup page.\n'
    printf '\n'
    printf '  %s\n' "$(_bold 'Useful commands:')"

    if [ "$OS" = "linux" ] && command -v systemctl >/dev/null 2>&1; then
        printf '    %-32s%s\n' "Service status:"  "systemctl status vigilus"
        printf '    %-32s%s\n' "View logs:"       "journalctl -u vigilus -f"
        printf '    %-32s%s\n' "Restart:"         "systemctl restart vigilus"
        printf '    %-32s%s\n' "Stop:"            "systemctl stop vigilus"
    elif [ "$OS" = "macos" ]; then
        printf '    %-32s%s\n' "View logs:"       "tail -f ~/Library/Logs/vigilus/vigilus.log"
        printf '    %-32s%s\n' "Restart:"         "launchctl kickstart -k gui/\$(id -u)/dev.vigilus"
        printf '    %-32s%s\n' "Stop:"            "launchctl unload ~/Library/LaunchAgents/dev.vigilus.plist"
    fi

    printf '\n'
    printf '  %s\n' "$(_bold 'CLI user management (lockout recovery):')"
    printf '    %-32s%s\n' "List users:"        "vigilus user list"
    printf '    %-32s%s\n' "Create user:"       "vigilus user create <username>"
    printf '    %-32s%s\n' "Reset password:"    "vigilus user reset-password <username>"
    printf '\n'
    printf '  Config: %s\n' "$ENV_FILE"
    printf '  Data:   %s\n' "$DATA_DIR"
    printf '\n'
}

# ── Banner ────────────────────────────────────────────────────────────────────

print_banner() {
    printf '\n'
    _tty && printf '\033[1m'
    printf '  ██╗   ██╗██╗ ██████╗ ██╗██╗     ██╗   ██╗███████╗\n'
    printf '  ██║   ██║██║██╔════╝ ██║██║     ██║   ██║██╔════╝\n'
    printf '  ██║   ██║██║██║  ███╗██║██║     ██║   ██║███████╗\n'
    printf '  ╚██╗ ██╔╝██║██║   ██║██║██║     ██║   ██║╚════██║\n'
    printf '   ╚████╔╝ ██║╚██████╔╝██║███████╗╚██████╔╝███████║\n'
    printf '    ╚═══╝  ╚═╝ ╚═════╝ ╚═╝╚══════╝ ╚═════╝ ╚══════╝\n'
    _tty && printf '\033[0m'
    printf '\n'
    printf '  AI-powered infrastructure platform\n'
    printf '  %s\n' "https://vigilus.dev"
    printf '\n'
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    print_banner
    detect_os
    check_privileges
    resolve_install_dir

    printf '  OS:      %s (%s)\n' "$OS" "${DISTRO:-unknown}"
    printf '  Install: %s\n' "$INSTALL_DIR"
    printf '  Port:    %s\n' "$VIGILUS_PORT"
    printf '  Branch:  %s\n' "$VIGILUS_BRANCH"
    printf '\n'

    ensure_base_deps
    ensure_python
    ensure_node
    fetch_source
    setup_backend
    build_frontend
    configure
    init_database
    setup_service
    wait_for_service
    print_success
}

main "$@"
