#!/usr/bin/env python3
"""
Webhook URL updater — runs after cloudflared starts.
Gets the new trycloudflare.com URL, updates .env, and registers with Meta.
"""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ENV_FILE = "/opt/kitchenos/.env"
CLOUDFLARED_API = "http://localhost:4040/api/tunnels"


def get_tunnel_url():
    for _ in range(30):
        try:
            with urllib.request.urlopen(CLOUDFLARED_API, timeout=2) as r:
                for t in json.loads(r.read()).get("tunnels", []):
                    u = t.get("public_url", "")
                    if u.startswith("https://"):
                        return u
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("No tunnel URL after 30s")


def read_env(key):
    if not os.path.exists(ENV_FILE):
        return ""
    for line in open(ENV_FILE):
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def update_env(new_url):
    content = open(ENV_FILE).read() if os.path.exists(ENV_FILE) else ""
    if "WEBHOOK_URL=" in content:
        content = re.sub(r"^WEBHOOK_URL=.*$", f"WEBHOOK_URL={new_url}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\nWEBHOOK_URL={new_url}\n"
    open(ENV_FILE, "w").write(content)
    logger.info(f"WEBHOOK_URL set to {new_url}")


def update_meta(url, app_id, secret, verify_token):
    if not app_id or not secret:
        logger.warning("META_APP_ID/SECRET not set, skipping Meta update")
        return
    webhook_url = f"{url}/instagram/webhook"
    params = urllib.parse.urlencode({
        "object": "instagram",
        "callback_url": webhook_url,
        "verify_token": verify_token,
        "fields": "messages",
        "access_token": f"{app_id}|{secret}",
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/{app_id}/subscriptions",
            data=params, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            logger.info(f"Meta webhook updated: {result}")
    except urllib.error.HTTPError as e:
        logger.error(f"Meta update failed: {e.code} {e.read().decode()}")
    except Exception as e:
        logger.error(f"Meta update error: {e}")


if __name__ == "__main__":
    url = get_tunnel_url()
    update_env(url)
    update_meta(url, read_env("META_APP_ID"), read_env("META_APP_SECRET"), read_env("INSTAGRAM_VERIFY_TOKEN"))
    os.system("systemctl restart kitchenos")
    logger.info(f"Done: {url}")
    print(url)
