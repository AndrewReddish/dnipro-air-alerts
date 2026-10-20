# dnipro-air-alerts

A small always-on service. It watches Dnipro alert channels on Telegram and sends a
**Critical Alert** to your iPhone when a post mentions your area. A Critical Alert
plays sound even when the phone is muted or in Sleep/Do Not Disturb.

```
Telegram channels ──(MTProto push, ~1 s)──> monitor.py ──(HTTPS)──> Bark ──> iPhone critical alert
```

- **Python + [Telethon](https://docs.telethon.dev)**: logs in as your own Telegram account and gets new posts pushed to it. Nothing is polled.
- **[Bark](https://github.com/Finb/Bark)**: free, open-source iOS push app with support for iOS Critical Alerts.
- One script, one config file, no database. Runs on any free micro VM.

## Configuration: `config.toml`

| key | meaning |
|---|---|
| `channels` | channel usernames to watch |
| `keywords` | case-insensitive, matched at the **start of a word**, so `Набережн` also hits `Набережній` |
| `exact_words` | case-sensitive **whole words**, for short abbreviations like `ТЕС` (otherwise `тест` would match) |
| `cooldown_seconds` | after a critical alert for a keyword, repeats of it from other channels during this window come as normal notifications instead of another alarm |

Text is normalized before matching. Latin look-alike letters (`i`, `p`, `T`, …) are treated
as their Cyrillic twins, and all apostrophe variants count as one character.

## Setup

### 1. iPhone: Bark
1. Install **Bark** from the App Store and open it.
2. Allow notifications. Under its settings, also allow **Critical Alerts**.
3. Copy your key. It is the part after `https://api.day.app/` in the example URL.

### 2. Telegram API credentials
Log in at <https://my.telegram.org> and open **API development tools**. Create an app (any
name) and note the `api_id` and `api_hash`.

### 3. A free server
Either of these works (both are free forever):
- **Oracle Cloud Always Free**: VM.Standard.E2.1.Micro or Ampere A1, Ubuntu image.
- **Google Cloud e2-micro**, in `us-west1`, `us-central1` or `us-east1`.

The service uses about 50 MB of RAM. Then, on the server:

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/andrewreddish/dnipro-air-alerts.git
cd dnipro-air-alerts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # fill TG_API_ID, TG_API_HASH, BARK_KEY
```

### 4. First run
```bash
.venv/bin/python monitor.py --login         # one-time: phone number + code from Telegram
.venv/bin/python monitor.py --test-notify   # mute the phone and turn on Sleep Focus first: it should still ring
.venv/bin/python monitor.py --backtest 300  # shows which of the last 300 posts per channel would match
```
Use the backtest to tune `config.toml` until the matches fit what you want.

`--login` creates `dnipro.session`, which is effectively a logged-in Telegram session. Keep it
private: it's in `.gitignore`. You can revoke it anytime in Telegram under
**Settings → Devices**. When the service starts, it joins the configured channels with your
account, because Telegram only pushes posts from channels you are subscribed to.

### 5. Run as a service
```bash
# edit User= and the paths in the unit if your user isn't "ubuntu"
sudo cp deploy/dnipro-alerts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dnipro-alerts
journalctl -u dnipro-alerts -f
```
Each start sends a quiet "monitor started" notification, so you can see restarts. systemd
restarts the service if it crashes.

## Development
```bash
pip install -r requirements.txt pytest
pytest
python monitor.py --dry-run   # listen live, log matches instead of notifying
```
