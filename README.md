# netbird-zitadel-bridge

A lightweight polling service that automatically syncs active users from [NetBird](https://netbird.io) to [Zitadel](https://zitadel.com).

## How it works

1. Every 5 minutes, the bridge polls the NetBird API for active human users.
2. Any user not yet in Zitadel gets created as a human user (unverified email — Zitadel sends a verification email automatically).
3. Synced users are tracked in a local state file to avoid duplicate creation.
4. The NetBird service token auto-rotates 30 days before expiry using an admin token.

## Sync direction

```
NetBird (active user) ──► Zitadel (human user created)
                       ✗   (no reverse sync)
```

Users created in Zitadel are NOT synced back to NetBird. This is intentional.

## Requirements

- Python 3.10+
- `python3-requests`, `python3-yaml` (or via `pip install requests pyyaml`)
- A NetBird service user with read access + an admin token that can manage its tokens
- A Zitadel machine user (service account) with `Org User Manager` role + PAT

## Setup

### 1. NetBird: Create a service user

1. Go to NetBird dashboard → **Users** → **Service Users**
2. Create a new service user (e.g. `sso.yourdomain.com`)
3. Generate a token with 365-day expiry — this is `service_token`
4. Note the service user ID — this is `service_user_id`
5. For auto-rotation, you also need an admin user's PAT — this is `admin_token`

### 2. Zitadel: Create a machine user

1. Go to Zitadel console → **Users** → **Service Users**
2. Create a new machine user (e.g. `netbird-bridge`)
3. Go to your **Organization** → **Members** → add `netbird-bridge` with role `Org User Manager`
4. Open the machine user → **Personal Access Tokens** → generate one — this is `pat`

### 3. Install

```bash
# Clone the repo
git clone https://github.com/yourorg/netbird-zitadel-bridge
cd netbird-zitadel-bridge

# Copy and edit config
cp config.yml.example config.yml
nano config.yml  # fill in all values

# Install (requires root for systemd)
sudo bash install.sh
```

### 4. Verify

```bash
# Check timer is running
systemctl status netbird-zitadel-bridge.timer

# Run manually
sudo systemctl start netbird-zitadel-bridge.service

# Watch logs
journalctl -u netbird-zitadel-bridge -f
```

## Configuration

All config lives in `config.yml` (never committed — see `.gitignore`).

| Key | Description |
|-----|-------------|
| `netbird.api_url` | Base URL of your NetBird management API |
| `netbird.service_user_id` | NetBird service user ID |
| `netbird.service_token_name` | Token name as shown in NetBird dashboard |
| `netbird.service_token` | Current service user token (auto-updated on rotation) |
| `netbird.admin_token` | Admin PAT used only for token rotation (365-day, rotate manually once/year) |
| `netbird.rotate_days_before` | Days before expiry to trigger rotation (default: 30) |
| `zitadel.domain` | Base URL of your Zitadel instance |
| `zitadel.pat` | PAT for the `netbird-bridge` machine user |

## Token rotation

The bridge auto-rotates the NetBird service token before expiry:

- Checks expiry on every poll pass
- When `rotate_days_before` days are left, creates a new 365-day token using `admin_token`
- Deletes the old token
- Writes the new token back to `config.yml`

The only manual step: rotate `admin_token` once a year in the NetBird dashboard.

## State file

Synced users are tracked in `BRIDGE_STATE` (default: `/var/lib/netbird-zitadel-bridge/synced_users.json`).

Each entry records: `zitadel_id`, `netbird_id`, `name`, `synced_at`.

The state file is **not** part of the repository.

## Environment variables

| Variable | Default |
|----------|---------|
| `BRIDGE_CONFIG` | `/etc/netbird-zitadel-bridge/config.yml` |
| `BRIDGE_STATE` | `/var/lib/netbird-zitadel-bridge/synced_users.json` |

## License

MIT
