#!/usr/bin/env bash
# One-command installer for Ubuntu/Debian servers:
#   curl -fsSL https://raw.githubusercontent.com/AndrewReddish/dnipro-air-alerts/main/install.sh | bash
# Safe to re-run: it updates the code and keeps your existing settings and login.
set -euo pipefail

REPO_URL="https://github.com/AndrewReddish/dnipro-air-alerts.git"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/dnipro-air-alerts}"
SERVICE=dnipro-alerts

# Under "curl | bash" stdin is the script itself, so talk to the keyboard directly.
TTY="${TTY:-/dev/tty}"
exec 3< "$TTY"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ask() { local answer; read -r -u 3 -p "$1" answer; printf '%s' "$answer"; }

step "Installing system packages (git, python)"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv python3-pip > /dev/null

step "Downloading the monitor into $DIR"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch -q origin "$BRANCH"
    git -C "$DIR" checkout -q "$BRANCH"
    git -C "$DIR" pull -q --ff-only origin "$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$DIR"
fi
cd "$DIR"

step "Installing Python libraries"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ] || [[ "$(ask "Settings are already saved. Type them again? [y/N] ")" =~ ^[Yy] ]]; then
    step "Settings"
    echo "Telegram API credentials are at https://my.telegram.org -> API development tools."
    api_id=$(ask "  Telegram api_id (digits): ")
    api_hash=$(ask "  Telegram api_hash: ")
    echo "Bark key: open the Bark app, it's the part after https://api.day.app/ in the example URL."
    bark_key=$(ask "  Bark key: ")
    # Accept a pasted full URL too: keep only the key part.
    bark_key=${bark_key#*api.day.app/}; bark_key=${bark_key%%/*}; bark_key=${bark_key// /}
    umask 077
    cat > .env <<EOF
TG_API_ID=$api_id
TG_API_HASH=$api_hash
BARK_KEY=$bark_key
EOF
    echo "Saved to $DIR/.env (only you can read it)."
fi

# Stop a running copy so it doesn't hold the Telegram session file during login.
sudo systemctl stop "$SERVICE" 2>/dev/null || true

if [ ! -f dnipro.session ]; then
    step "Telegram login (one time only)"
    echo "Enter your phone number with country code, e.g. +380501234567."
    echo "Telegram will send a code to your Telegram app (not SMS) - type it here."
fi
.venv/bin/python monitor.py --login < "$TTY"

step "Sending a test alarm to your iPhone"
echo "Tip: mute the phone and turn on Sleep Focus first - it should still ring."
.venv/bin/python monitor.py --test-notify
if [[ ! "$(ask "  Did your phone ring? [y/N] ")" =~ ^[Yy] ]]; then
    echo "Check in the Bark app that notifications AND Critical Alerts are allowed"
    echo "(iPhone Settings -> Notifications -> Bark), and that the Bark key is right."
    echo "Then run the install command again (answer 'y' to type the settings again)."
    exit 1
fi

step "Starting the monitor as a background service"
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$DIR|g" deploy/dnipro-alerts.service \
    | sudo tee /etc/systemd/system/$SERVICE.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable -q --now "$SERVICE"
sleep 3
sudo systemctl --no-pager --lines=5 status "$SERVICE" || true

cat <<EOF

$(printf '\033[1;32m')All done! The monitor is running and will start again by itself after reboots.$(printf '\033[0m')
You should get a quiet "Dnipro monitor started" notification now.

Useful commands:
  See live log:        journalctl -u $SERVICE -f        (Ctrl+C to exit)
  Restart:             sudo systemctl restart $SERVICE
  Stop:                sudo systemctl stop $SERVICE
  Edit keywords:       nano $DIR/config.toml   then restart
  Check old posts:     cd $DIR && .venv/bin/python monitor.py --backtest 300
                       (stop the service first, start it again after)
  Update the code:     run the install command again
EOF
