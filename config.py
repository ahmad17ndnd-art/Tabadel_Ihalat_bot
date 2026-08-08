import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")

ADMIN_IDS = set()
for part in os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "")).split(","):
    part = part.strip()
    if part.isdigit():
        ADMIN_IDS.add(int(part))
if not ADMIN_IDS:
    ADMIN_IDS = {1922499737}


def is_admin(user_id):
    return user_id in ADMIN_IDS


def fmt_lira(n):
    return f"{n:,}"
