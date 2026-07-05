#!/usr/bin/env python3
"""
NetBird → Zitadel User Sync Bridge
Polls NetBird for active users and creates them in Zitadel.
Auto-rotates the NetBird service token before expiry using an admin token.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# State (synced_users.json)
# ---------------------------------------------------------------------------
def load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"synced": {}}
    with open(p) as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# NetBird token rotation
# ---------------------------------------------------------------------------
def get_netbird_tokens(cfg: dict, admin_token: str) -> list:
    url = f"{cfg['netbird']['api_url']}/users/{cfg['netbird']['service_user_id']}/tokens"
    r = requests.get(url, headers=_nb_headers(admin_token), timeout=10)
    r.raise_for_status()
    return r.json()


def rotate_netbird_token(cfg: dict, admin_token: str, old_token_id: str) -> tuple[str, str]:
    """Creates a new 365-day service token and deletes the old one. Returns (new_token, new_token_id)."""
    base = f"{cfg['netbird']['api_url']}/users/{cfg['netbird']['service_user_id']}/tokens"

    # Create new token
    payload = {"name": cfg["netbird"]["service_token_name"], "expires_in": 365}
    r = requests.post(base, headers=_nb_headers(admin_token), json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()
    new_token = data["plain_token"]
    new_token_id = data["personal_access_token"]["id"]
    new_expiry = data["personal_access_token"]["expiration_date"]

    log.info("Rotated NetBird service token → new ID %s, expires %s", new_token_id, new_expiry)

    # Delete old token
    r2 = requests.delete(f"{base}/{old_token_id}", headers=_nb_headers(admin_token), timeout=10)
    if r2.status_code not in (200, 204):
        log.warning("Failed to delete old token %s: %s", old_token_id, r2.text)
    else:
        log.info("Deleted old NetBird service token %s", old_token_id)

    return new_token, new_token_id, new_expiry


def maybe_rotate_token(cfg: dict, config_path: str) -> str:
    """Check expiry and rotate if within rotate_days_before. Returns current valid service token."""
    admin_token = cfg["netbird"]["admin_token"]
    try:
        tokens = get_netbird_tokens(cfg, admin_token)
    except Exception as e:
        log.error("Failed to list NetBird tokens: %s", e)
        return cfg["netbird"]["service_token"]

    # Find our service token
    token_name = cfg["netbird"]["service_token_name"]
    current_id = None
    current_expiry = None
    for t in tokens:
        if t["name"] == token_name:
            current_id = t["id"]
            current_expiry = t["expiration_date"]
            break

    if not current_id:
        log.warning("Service token '%s' not found in NetBird — skipping rotation check", token_name)
        return cfg["netbird"]["service_token"]

    expiry_dt = datetime.fromisoformat(current_expiry.replace("Z", "+00:00"))
    days_left = (expiry_dt - datetime.now(timezone.utc)).days
    rotate_threshold = cfg["netbird"].get("rotate_days_before", 30)

    log.info("NetBird service token expires in %d days (threshold: %d)", days_left, rotate_threshold)

    if days_left <= rotate_threshold:
        log.info("Token within rotation threshold — rotating...")
        try:
            new_token, new_id, new_expiry = rotate_netbird_token(cfg, admin_token, current_id)
            # Persist new token back to config file
            cfg["netbird"]["service_token"] = new_token
            with open(config_path) as f:
                raw = yaml.safe_load(f)
            raw["netbird"]["service_token"] = new_token
            with open(config_path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
            log.info("Config updated with new service token.")
            return new_token
        except Exception as e:
            log.error("Token rotation failed: %s — continuing with existing token", e)

    return cfg["netbird"]["service_token"]


# ---------------------------------------------------------------------------
# NetBird users
# ---------------------------------------------------------------------------
def _nb_headers(token: str) -> dict:
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def fetch_netbird_users(cfg: dict, token: str) -> list:
    url = f"{cfg['netbird']['api_url']}/users"
    r = requests.get(url, headers=_nb_headers(token), timeout=10)
    r.raise_for_status()
    return r.json()


def filter_syncable_users(users: list) -> list:
    """Return only active, non-service, non-empty-email human users."""
    result = []
    for u in users:
        if u.get("is_service_user"):
            continue
        if u.get("status") != "active":
            continue
        email = u.get("email", "").strip()
        if not email:
            continue
        result.append(u)
    return result


# ---------------------------------------------------------------------------
# Zitadel users
# ---------------------------------------------------------------------------
def _zitadel_headers(pat: str) -> dict:
    return {"Authorization": f"Bearer {pat}", "Content-Type": "application/json"}


def create_zitadel_user(cfg: dict, nb_user: dict) -> str:
    """Create a human user in Zitadel. Returns the new userId."""
    domain = cfg["zitadel"]["domain"]
    pat = cfg["zitadel"]["pat"]

    name = nb_user.get("name", "").strip()
    parts = name.split(" ", 1)
    given = parts[0] if parts else name
    family = parts[1] if len(parts) > 1 else ""
    email = nb_user["email"]

    payload = {
        "profile": {
            "givenName": given,
            "familyName": family if family else given,
            "displayName": name if name else email,
        },
        "email": {
            "email": email,
            "isVerified": False,  # Zitadel will send verification email
        },
    }

    r = requests.post(
        f"{domain}/v2/users/human",
        headers=_zitadel_headers(pat),
        json=payload,
        timeout=10,
    )

    if r.status_code == 409:
        log.info("User %s already exists in Zitadel (409) — marking synced", email)
        # Try to find their userId
        return _find_zitadel_user_id(cfg, email) or "already-exists"

    r.raise_for_status()
    user_id = r.json().get("userId", "")
    log.info("Created Zitadel user %s → userId %s", email, user_id)
    return user_id


def _find_zitadel_user_id(cfg: dict, email: str) -> str | None:
    """Search Zitadel for an existing user by email."""
    domain = cfg["zitadel"]["domain"]
    pat = cfg["zitadel"]["pat"]
    payload = {
        "queries": [
            {"emailQuery": {"emailAddress": email, "method": "TEXT_QUERY_METHOD_EQUALS"}}
        ]
    }
    try:
        r = requests.post(
            f"{domain}/v2/users",
            headers=_zitadel_headers(pat),
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("result", [])
        if results:
            return results[0].get("userId")
    except Exception as e:
        log.warning("Could not find existing Zitadel user for %s: %s", email, e)
    return None


# ---------------------------------------------------------------------------
# Main sync pass
# ---------------------------------------------------------------------------
def run_sync(cfg: dict, config_path: str, state_path: str) -> None:
    log.info("--- Sync pass starting ---")

    # 1. Maybe rotate NetBird service token
    service_token = maybe_rotate_token(cfg, config_path)

    # 2. Fetch NetBird users
    try:
        nb_users = fetch_netbird_users(cfg, service_token)
    except requests.HTTPError as e:
        log.error("Failed to fetch NetBird users: %s", e)
        return

    syncable = filter_syncable_users(nb_users)
    log.info("NetBird active human users: %d", len(syncable))

    # 3. Load state
    state = load_state(state_path)
    synced = state.setdefault("synced", {})

    # 4. Sync new users
    new_count = 0
    for u in syncable:
        email = u["email"]
        if email in synced:
            continue

        log.info("New user detected: %s (%s)", u.get("name"), email)
        try:
            zitadel_id = create_zitadel_user(cfg, u)
            synced[email] = {
                "zitadel_id": zitadel_id,
                "netbird_id": u["id"],
                "name": u.get("name", ""),
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
            new_count += 1
        except Exception as e:
            log.error("Failed to create Zitadel user for %s: %s", email, e)

    # 5. Save state
    save_state(state_path, state)
    log.info("Sync pass complete. New users synced: %d", new_count)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    config_path = os.environ.get("BRIDGE_CONFIG", "/etc/netbird-zitadel-bridge/config.yml")
    state_path = os.environ.get("BRIDGE_STATE", "/var/lib/netbird-zitadel-bridge/synced_users.json")

    if not Path(config_path).exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)

    cfg = load_config(config_path)
    run_sync(cfg, config_path, state_path)


if __name__ == "__main__":
    main()
