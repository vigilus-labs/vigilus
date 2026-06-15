#!/bin/sh
# Vigilus uninstaller
# Usage: curl -fsSL https://vigilus.dev/uninstall.sh | sh -s -- --yes
#    or: /opt/vigilus/uninstall.sh
#
# Reverses what install.sh set up:
#   • stops + removes the system service (systemd system/user, or macOS launchd)
#   • removes the dedicated 'vigilus' system user (system installs)
#   • removes the install directory
#
# By default the secret (.env) and the database (data/) are PRESERVED — moved
# to a timestamped backup outside the install dir — because the .env secret is
# the only thing that can decrypt stored credentials. Use --purge to delete
# everything, including the backup, with no way back.
#
# Shared tooling (Python, Node, git, curl) is never touched — other software
# may depend on it.
#
# Flags / environment overrides:
#   --purge        | VIGILUS_PURGE=1   delete data + config too (no backup)
#   --keep-data    | VIGILUS_KEEP=1    leave install dir on disk, only remove service
#   --yes | -y     | VIGILUS_YES=1     skip the confirmation prompt
#   VIGILUS_INSTALL_DIR=...            override autodetected install location
set -e

# ── Colour helpers (kept identical to install.sh) ─────────────────────────────

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

# ── Argument parsing ──────────────────────────────────────────────────────────

PURGE="${VIGILUS_PURGE:-0}"
KEEP_DATA="${VIGILUS_KEEP:-0}"
ASSUME_YES="${VIGILUS_YES:-0}"

for arg in "$@"; do
    case "$arg" in
        --purge)     PURGE=1 ;;
        --keep-data) KEEP_DATA=1 ;;
        --yes|-y)    ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "Unknown option: $arg (try --help)" ;;
    esac
done

if [ "$PURGE" -eq 1 ] && [ "$KEEP_DATA" -eq 1 ]; then
    die "--purge and --keep-data are mutually exclusive."
fi

# ── Platform detection ────────────────────────────────────────────────────────

detect_os() {
    case "$(uname -s)" in
        Linux)  OS="linux" ;;
        Darwin) OS="macos" ;;
        *)      die "Unsupported operating system: $(uname -s)" ;;
    esac
}

# ── Privilege helpers ─────────────────────────────────────────────────────────

IS_ROOT=0
HAS_SUDO=0
SUDO=""

check_privileges() {
    if [ "$(id -u)" -eq 0 ]; then
        IS_ROOT=1
        SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        HAS_SUDO=1
        SUDO="sudo"
    fi
}

can_use_system() {
    [ "$IS_ROOT" -eq 1 ] || [ "$HAS_SUDO" -eq 1 ]
}

# ── Install directory (same resolution as install.sh) ─────────────────────────

resolve_install_dir() {
    if [ -n "${VIGILUS_INSTALL_DIR:-}" ]; then
        INSTALL_DIR="$VIGILUS_INSTALL_DIR"
    elif [ -d /opt/vigilus ]; then
        INSTALL_DIR="/opt/vigilus"
    elif [ -d "$HOME/.vigilus" ]; then
        INSTALL_DIR="$HOME/.vigilus"
    elif can_use_system; then
        INSTALL_DIR="/opt/vigilus"
    else
        INSTALL_DIR="$HOME/.vigilus"
    fi

    BACKEND_DIR="$INSTALL_DIR/backend"
    ENV_FILE="$BACKEND_DIR/.env"
    DATA_DIR="$BACKEND_DIR/data"
}

# ── Confirmation ──────────────────────────────────────────────────────────────

confirm() {
    if [ "$ASSUME_YES" -eq 1 ]; then
        return 0
    fi
    if [ ! -t 0 ]; then
        die "Refusing to uninstall non-interactively without --yes (this is destructive)."
    fi

    if [ "$PURGE" -eq 1 ]; then
        printf '\n  %s This will %s including the database and the .env secret.\n' \
            "$(_red 'WARNING:')" "$(_bold 'permanently delete everything')"
        printf '  Encrypted credentials will become %s.\n' "$(_red 'unrecoverable')"
    else
        printf '\n  This removes the service and %s.\n' "$INSTALL_DIR"
        printf '  Your .env secret and database will be backed up first.\n'
    fi

    printf '\n  Continue? [y/N] '
    read -r reply
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) printf '\n'; info "Aborted — nothing was changed."; exit 0 ;;
    esac
}

# ── Stop & remove services ────────────────────────────────────────────────────

remove_service_linux_system() {
    SERVICE_FILE="/etc/systemd/system/vigilus.service"
    [ -f "$SERVICE_FILE" ] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0

    info "Stopping system service 'vigilus'..."
    $SUDO systemctl stop vigilus >/dev/null 2>&1 || true
    $SUDO systemctl disable vigilus >/dev/null 2>&1 || true
    $SUDO rm -f "$SERVICE_FILE"
    $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
    $SUDO systemctl reset-failed vigilus >/dev/null 2>&1 || true
    ok "Removed systemd service $SERVICE_FILE"
    REMOVED_SYSTEM_SERVICE=1
}

remove_service_linux_user() {
    SERVICE_FILE="$HOME/.config/systemd/user/vigilus.service"
    [ -f "$SERVICE_FILE" ] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0

    info "Stopping user service 'vigilus'..."
    systemctl --user stop vigilus >/dev/null 2>&1 || true
    systemctl --user disable vigilus >/dev/null 2>&1 || true
    rm -f "$SERVICE_FILE"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user reset-failed vigilus >/dev/null 2>&1 || true
    ok "Removed user systemd service $SERVICE_FILE"
}

remove_service_macos() {
    PLIST_FILE="$HOME/Library/LaunchAgents/dev.vigilus.plist"
    LOG_DIR="$HOME/Library/Logs/vigilus"

    if [ -f "$PLIST_FILE" ]; then
        info "Unloading launchd agent 'dev.vigilus'..."
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        rm -f "$PLIST_FILE"
        ok "Removed launchd agent $PLIST_FILE"
    fi

    if [ -d "$LOG_DIR" ]; then
        rm -rf "$LOG_DIR"
        ok "Removed logs $LOG_DIR"
    fi
}

remove_services() {
    step "Removing system service"

    REMOVED_SYSTEM_SERVICE=0
    case "$OS" in
        linux)
            if can_use_system; then
                remove_service_linux_system
            fi
            remove_service_linux_user
            ;;
        macos)
            remove_service_macos
            ;;
    esac

    if [ "$REMOVED_SYSTEM_SERVICE" -eq 0 ] && [ ! -f "$HOME/.config/systemd/user/vigilus.service" ]; then
        : # nothing reported above is fine
    fi
}

# ── Remove the dedicated system user ──────────────────────────────────────────

remove_service_user() {
    [ "$OS" = "linux" ] || return 0
    can_use_system || return 0
    id vigilus >/dev/null 2>&1 || return 0

    step "Removing 'vigilus' system user"
    $SUDO userdel vigilus >/dev/null 2>&1 || warn "Could not remove user 'vigilus' (may own files outside $INSTALL_DIR)"
    ok "System user 'vigilus' removed"
}

# ── Back up secret + data ─────────────────────────────────────────────────────

backup_data() {
    [ "$PURGE" -eq 1 ] && return 0
    [ -f "$ENV_FILE" ] || [ -d "$DATA_DIR" ] || return 0

    step "Backing up secret and database"

    STAMP=$(date +%Y%m%d-%H%M%S)
    BACKUP_DIR="${HOME}/vigilus-backup-${STAMP}"
    mkdir -p "$BACKUP_DIR"

    if [ -f "$ENV_FILE" ]; then
        # .env may be owned by the vigilus service user on system installs.
        if [ -r "$ENV_FILE" ]; then
            cp "$ENV_FILE" "$BACKUP_DIR/.env"
        else
            $SUDO cp "$ENV_FILE" "$BACKUP_DIR/.env"
            $SUDO chown "$(id -u):$(id -g)" "$BACKUP_DIR/.env"
        fi
        chmod 600 "$BACKUP_DIR/.env" 2>/dev/null || true
    fi

    if [ -d "$DATA_DIR" ]; then
        if [ -r "$DATA_DIR" ]; then
            cp -R "$DATA_DIR" "$BACKUP_DIR/data"
        else
            $SUDO cp -R "$DATA_DIR" "$BACKUP_DIR/data"
            $SUDO chown -R "$(id -u):$(id -g)" "$BACKUP_DIR/data"
        fi
    fi

    ok "Backup saved to $BACKUP_DIR"
}

# ── Remove the install directory ──────────────────────────────────────────────

remove_install_dir() {
    if [ "$KEEP_DATA" -eq 1 ]; then
        step "Keeping install directory"
        info "Left $INSTALL_DIR in place (--keep-data)."
        return 0
    fi

    [ -d "$INSTALL_DIR" ] || { warn "Install directory $INSTALL_DIR not found."; return 0; }

    step "Removing install directory"

    # Don't nuke the whole filesystem if INSTALL_DIR resolved to something odd.
    case "$INSTALL_DIR" in
        /|/home|/root|/opt|"$HOME") die "Refusing to remove suspicious path: $INSTALL_DIR" ;;
    esac

    if [ -w "$(dirname "$INSTALL_DIR")" ] && [ -O "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
    elif can_use_system; then
        $SUDO rm -rf "$INSTALL_DIR"
    else
        rm -rf "$INSTALL_DIR" 2>/dev/null || die "Could not remove $INSTALL_DIR (need elevated privileges)."
    fi

    ok "Removed $INSTALL_DIR"
}

# ── Final banner ──────────────────────────────────────────────────────────────

print_done() {
    printf '\n'
    _tty && printf '\033[0;32m'
    printf '╔══════════════════════════════════════════════════╗\n'
    printf '║           Vigilus has been uninstalled.          ║\n'
    printf '╚══════════════════════════════════════════════════╝\n'
    _tty && printf '\033[0m'
    printf '\n'

    if [ "$PURGE" -eq 1 ]; then
        printf '  Everything was removed, including the database and secret.\n'
    elif [ -n "${BACKUP_DIR:-}" ]; then
        printf '  %s %s\n' "$(_bold 'Backup of secret + database:')" "$(_cyan "$BACKUP_DIR")"
        printf '  Keep the .env safe — it is required to decrypt stored credentials\n'
        printf '  if you ever reinstall.\n'
    fi

    printf '\n'
    printf '  Shared tools (Python, Node.js, git) were left installed.\n'
    printf '\n'
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    detect_os
    check_privileges
    resolve_install_dir

    printf '\n'
    printf '  %s\n' "$(_bold 'Vigilus uninstaller')"
    printf '  OS:      %s\n' "$OS"
    printf '  Install: %s\n' "$INSTALL_DIR"
    if [ "$PURGE" -eq 1 ]; then
        printf '  Mode:    %s\n' "$(_red 'purge (delete everything)')"
    elif [ "$KEEP_DATA" -eq 1 ]; then
        printf '  Mode:    keep install dir (service only)\n'
    else
        printf '  Mode:    remove (backup secret + data first)\n'
    fi

    confirm

    remove_services
    remove_service_user
    backup_data
    remove_install_dir
    print_done
}

main "$@"
