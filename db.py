"""
db.py — طبقة قاعدة البيانات الكاملة للنظام الجديد (PR GRAM style)
SQLite + دوال مساعدة لكل الجداول.
"""
import sqlite3
import os
from datetime import datetime

DB_NAME = os.environ.get("DB_PATH", "bot_data.db")

CATEGORY_LABELS = {
    "group": "🔮 مجموعة",
    "channel": "📢 قناة",
    "bot": "🤖 بوت",
    "post": "👁️ منشور",
    "interaction": "🔥 تفاعلات",
    "premium": "⚡ شحن بريميوم",
}
CATEGORY_ORDER = ["group", "channel", "bot", "post", "interaction", "premium"]
AUTO_VERIFIABLE = {"group", "channel"}  # يتحقق منها تلقائياً عبر get_chat_member
MANUAL_VERIFIABLE = {"bot", "interaction"}  # تحتاج سكرين شوت + مراجعة صاحب المهمة
INSTANT_VERIFIABLE = {"post"}  # تُحتسب تلقائياً بعد مهلة عرض قصيرة

DEFAULT_CATEGORY_PRICES = {
    # (min_price, suggested_price, max_price)
    "group": (1000, 1343, 0),
    "channel": (750, 1810, 0),
    "bot": (900, 1200, 2500),
    "post": (100, 300, 0),
    "interaction": (500, 1000, 0),
}


def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # ---------- المستخدمون ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level TEXT DEFAULT 'Unknown',
            banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT NULL,
            ban_until TEXT DEFAULT NULL,
            strikes INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            ref_by INTEGER DEFAULT NULL,
            referrals_count INTEGER DEFAULT 0,
            referral_earnings INTEGER DEFAULT 0,
            notifications_enabled INTEGER DEFAULT 1,
            language TEXT DEFAULT 'ar',
            joined_at TEXT,
            last_daily_gift_at TEXT DEFAULT NULL,
            no_commission_until TEXT DEFAULT NULL
        )
    """)

    # ---------- الإعدادات العامة (صف واحد) ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT '👋 أهلاً بك في PR GRAM!',
            rules_text TEXT DEFAULT 'يُمنع إلغاء الاشتراك من القنوات/المحادثات لمدة 7 أيام.',
            commission_percent INTEGER DEFAULT 15,
            daily_gift_points INTEGER DEFAULT 50,
            ref_reward_premium INTEGER DEFAULT 10000,
            ref_reward_regular INTEGER DEFAULT 5000,
            ref_reward_mandatory INTEGER DEFAULT 3000,
            ref_income_percent_topup INTEGER DEFAULT 10,
            ref_income_percent_tasks INTEGER DEFAULT 3,
            stars_to_lira_rate INTEGER DEFAULT 100,
            stars_min_no_commission INTEGER DEFAULT 50,
            stars_no_commission_hours INTEGER DEFAULT 24,
            mandatory_sub_channel TEXT DEFAULT NULL,
            useful_links TEXT DEFAULT NULL,
            instructions_text TEXT DEFAULT NULL
        )
    """)
    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # ---------- أسعار الفئات ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS category_prices (
            category TEXT PRIMARY KEY,
            min_price INTEGER DEFAULT 100,
            suggested_price INTEGER DEFAULT 300,
            max_price INTEGER DEFAULT 0
        )
    """)
    for cat, (mn, sug, mx) in DEFAULT_CATEGORY_PRICES.items():
        c.execute(
            "INSERT OR IGNORE INTO category_prices (category, min_price, suggested_price, max_price) VALUES (?,?,?,?)",
            (cat, mn, sug, mx)
        )

    # ---------- المهام / الحملات ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            owner_username TEXT,
            category TEXT,
            unit_price INTEGER DEFAULT 0,
            target_count INTEGER DEFAULT 0,
            current_count INTEGER DEFAULT 0,
            total_cost INTEGER DEFAULT 0,
            payment_method TEXT DEFAULT 'lira',
            commission_percent INTEGER DEFAULT 15,
            target_chat_id TEXT DEFAULT NULL,
            target_chat_title TEXT DEFAULT NULL,
            link TEXT DEFAULT NULL,
            bot_username TEXT DEFAULT NULL,
            extra_conditions TEXT DEFAULT NULL,
            requires_review INTEGER DEFAULT 0,
            join_request_mode INTEGER DEFAULT 0,
            interaction_mode TEXT DEFAULT NULL,
            fixed_emoji TEXT DEFAULT NULL,
            filter_account_type TEXT DEFAULT 'all',
            filter_audience TEXT DEFAULT 'all',
            notify_owner INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at TEXT
        )
    """)

    # ---------- تنفيذ المهام من طرف المستخدمين ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            task_id INTEGER,
            user_id INTEGER,
            status TEXT DEFAULT 'in_progress',
            proof_file_id TEXT DEFAULT NULL,
            reward_amount INTEGER DEFAULT 0,
            started_at TEXT,
            submitted_at TEXT DEFAULT NULL,
            resolved_at TEXT DEFAULT NULL,
            review_note TEXT DEFAULT NULL,
            PRIMARY KEY (task_id, user_id)
        )
    """)

    # ---------- روابط إحالة خاصة يُنشئها الأدمن بقيمة حرة ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_ref_links (
            link_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            title TEXT,
            reward_amount INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 0,
            uses_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_ref_link_uses (
            link_id INTEGER,
            user_id INTEGER,
            used_at TEXT,
            PRIMARY KEY (link_id, user_id)
        )
    """)

    # ---------- سجل مدفوعات النجوم ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS stars_payments (
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            purpose TEXT,
            payload TEXT,
            stars_amount INTEGER,
            related_task_id INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            paid_at TEXT DEFAULT NULL
        )
    """)

    # ---------- سجل المعاملات (لِلَّإحصائيات) ----------
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            kind TEXT,
            note TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== المستخدمون ====================

def ensure_user(user_id, username, first_name, ref_by=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if c.fetchone():
        c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))
        conn.commit(); conn.close()
        return False
    c.execute(
        "INSERT INTO users (user_id, username, first_name, joined_at, ref_by) VALUES (?,?,?,?,?)",
        (user_id, username, first_name, now_str(), ref_by)
    )
    conn.commit(); conn.close()
    return True


def get_user(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row))


def log_tx(user_id, amount, kind, note=""):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO transactions (user_id, amount, kind, note, created_at) VALUES (?,?,?,?,?)",
              (user_id, amount, kind, note, now_str()))
    conn.commit(); conn.close()


def add_balance(user_id, amount, kind="adjust", note=""):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()
    log_tx(user_id, amount, kind, note)


def set_field(table, user_id_col, user_id, field, value):
    conn = get_conn(); c = conn.cursor()
    c.execute(f"UPDATE {table} SET {field}=? WHERE {user_id_col}=?", (value, user_id))
    conn.commit(); conn.close()


def toggle_ban(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT banned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    new_val = 0 if row[0] else 1
    c.execute("UPDATE users SET banned=? WHERE user_id=?", (new_val, user_id))
    conn.commit(); conn.close()
    return new_val


def get_all_active_user_ids():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned=0")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows


def count_users():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    n = c.fetchone()[0]
    conn.close()
    return n


def get_users_page(offset, limit):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, username, balance, banned FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
              (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== الإعدادات ====================

def get_settings():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE id=1")
    row = c.fetchone()
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row))


def update_setting(field, value):
    conn = get_conn(); c = conn.cursor()
    c.execute(f"UPDATE settings SET {field}=? WHERE id=1", (value,))
    conn.commit(); conn.close()


# ==================== أسعار الفئات ====================

def get_category_price(category):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT min_price, suggested_price, max_price FROM category_prices WHERE category=?", (category,))
    row = c.fetchone()
    conn.close()
    if not row:
        return DEFAULT_CATEGORY_PRICES.get(category, (100, 300, 0))
    return row


def set_category_price(category, mn, sug, mx):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE category_prices SET min_price=?, suggested_price=?, max_price=? WHERE category=?",
              (mn, sug, mx, category))
    conn.commit(); conn.close()


def get_all_category_prices():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT category, min_price, suggested_price, max_price FROM category_prices")
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== روابط الإحالة الخاصة (أدمن) ====================

def create_admin_ref_link(code, title, reward_amount, max_uses, created_by):
    conn = get_conn(); c = conn.cursor()
    c.execute(
        "INSERT INTO admin_ref_links (code, title, reward_amount, max_uses, created_by, created_at) VALUES (?,?,?,?,?,?)",
        (code, title, reward_amount, max_uses, created_by, now_str())
    )
    conn.commit()
    link_id = c.lastrowid
    conn.close()
    return link_id


def get_admin_ref_link_by_code(code):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM admin_ref_links WHERE code=?", (code,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row))


def list_admin_ref_links():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT link_id, code, title, reward_amount, max_uses, uses_count, active FROM admin_ref_links ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return rows


def toggle_admin_ref_link(link_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT active FROM admin_ref_links WHERE link_id=?", (link_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    new_val = 0 if row[0] else 1
    c.execute("UPDATE admin_ref_links SET active=? WHERE link_id=?", (new_val, link_id))
    conn.commit(); conn.close()
    return new_val


def use_admin_ref_link(code, user_id):
    """يرجع (نجاح, المكافأة أو سبب الفشل)"""
    link = get_admin_ref_link_by_code(code)
    if not link or not link["active"]:
        return False, "invalid"
    if link["max_uses"] and link["uses_count"] >= link["max_uses"]:
        return False, "exhausted"
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT 1 FROM admin_ref_link_uses WHERE link_id=? AND user_id=?", (link["link_id"], user_id))
    if c.fetchone():
        conn.close(); return False, "already_used"
    c.execute("INSERT INTO admin_ref_link_uses (link_id, user_id, used_at) VALUES (?,?,?)",
              (link["link_id"], user_id, now_str()))
    c.execute("UPDATE admin_ref_links SET uses_count = uses_count + 1 WHERE link_id=?", (link["link_id"],))
    conn.commit(); conn.close()
    return True, link["reward_amount"]


# ==================== المهام ====================

def create_task(**kw):
    conn = get_conn(); c = conn.cursor()
    fields = ["owner_id", "owner_username", "category", "unit_price", "target_count", "total_cost",
              "payment_method", "commission_percent", "target_chat_id", "target_chat_title", "link",
              "bot_username", "extra_conditions", "requires_review", "join_request_mode",
              "interaction_mode", "fixed_emoji", "status", "created_at"]
    kw.setdefault("status", "active")
    kw["created_at"] = now_str()
    cols = ", ".join(fields)
    placeholders = ", ".join(["?"] * len(fields))
    values = [kw.get(f) for f in fields]
    c.execute(f"INSERT INTO tasks ({cols}) VALUES ({placeholders})", values)
    task_id = c.lastrowid
    conn.commit(); conn.close()
    return task_id


def get_task(task_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row))


def update_task_field(task_id, field, value):
    conn = get_conn(); c = conn.cursor()
    c.execute(f"UPDATE tasks SET {field}=? WHERE task_id=?", (value, task_id))
    conn.commit(); conn.close()


def increment_task_progress(task_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE tasks SET current_count = current_count + 1 WHERE task_id=?", (task_id,))
    c.execute("SELECT target_count, current_count FROM tasks WHERE task_id=?", (task_id,))
    tgt, cur = c.fetchone()
    if cur >= tgt:
        c.execute("UPDATE tasks SET status='completed' WHERE task_id=?", (task_id,))
    conn.commit(); conn.close()
    return cur, tgt


def get_active_tasks(category, exclude_owner=None):
    conn = get_conn(); c = conn.cursor()
    c.execute(
        "SELECT * FROM tasks WHERE status='active' AND category=? AND current_count < target_count ORDER BY unit_price DESC",
        (category,)
    )
    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    conn.close()
    result = [dict(zip(cols, r)) for r in rows]
    if exclude_owner:
        result = [t for t in result if t["owner_id"] != exclude_owner]
    return result


def count_tasks_by_category():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT category, COUNT(*) FROM tasks WHERE status='active' AND current_count < target_count GROUP BY category")
    rows = dict(c.fetchall())
    conn.close()
    return rows


def get_tasks_by_owner(owner_id, status_filter=None):
    conn = get_conn(); c = conn.cursor()
    if status_filter:
        c.execute("SELECT * FROM tasks WHERE owner_id=? AND status=? ORDER BY created_at DESC", (owner_id, status_filter))
    else:
        c.execute("SELECT * FROM tasks WHERE owner_id=? ORDER BY created_at DESC", (owner_id,))
    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]


def delete_task(task_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE tasks SET status='deleted' WHERE task_id=?", (task_id,))
    conn.commit(); conn.close()


def set_task_status(task_id, status):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE tasks SET status=? WHERE task_id=?", (status, task_id))
    conn.commit(); conn.close()


# ==================== تنفيذ المهام ====================

def get_completion(task_id, user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM task_completions WHERE task_id=? AND user_id=?", (task_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row))


def start_completion(task_id, user_id):
    row = get_completion(task_id, user_id)
    conn = get_conn(); c = conn.cursor()
    if row is None or row["status"] == "rejected":
        c.execute(
            "INSERT INTO task_completions (task_id, user_id, status, started_at) VALUES (?,?, 'in_progress', ?) "
            "ON CONFLICT(task_id, user_id) DO UPDATE SET status='in_progress', started_at=excluded.started_at, "
            "proof_file_id=NULL, submitted_at=NULL, resolved_at=NULL, review_note=NULL",
            (task_id, user_id, now_str())
        )
        conn.commit(); conn.close()
        return "in_progress"
    conn.close()
    return row["status"]


def submit_proof(task_id, user_id, file_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE task_completions SET status='submitted', proof_file_id=?, submitted_at=? WHERE task_id=? AND user_id=?",
              (file_id, now_str(), task_id, user_id))
    conn.commit(); conn.close()


def resolve_completion(task_id, user_id, status, note=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE task_completions SET status=?, resolved_at=?, review_note=? WHERE task_id=? AND user_id=?",
              (status, now_str(), note, task_id, user_id))
    conn.commit(); conn.close()


def get_latest_submitted(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT task_id FROM task_completions WHERE user_id=? AND status IN ('submitted','review_requested') "
              "ORDER BY started_at DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def user_task_status(task_id, user_id):
    row = get_completion(task_id, user_id)
    return row["status"] if row else None


def count_completions_by_status():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT status, COUNT(*) FROM task_completions GROUP BY status")
    rows = dict(c.fetchall())
    conn.close()
    return rows


# ==================== إحصائيات شاملة ====================

def full_stats():
    conn = get_conn(); c = conn.cursor()
    stats = {}
    c.execute("SELECT COUNT(*) FROM users"); stats["total_users"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE banned=0"); stats["active_users"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE banned=1"); stats["banned_users"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE verified=1"); stats["verified_users"] = c.fetchone()[0]
    c.execute("SELECT SUM(balance) FROM users"); stats["total_balance"] = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM users WHERE date(joined_at) = date('now')"); stats["new_today"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE status='active'"); stats["active_tasks"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'"); stats["completed_tasks"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks"); stats["total_tasks"] = c.fetchone()[0]
    c.execute("SELECT SUM(total_cost) FROM tasks"); stats["total_spent_on_tasks"] = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM task_completions WHERE status='approved'"); stats["approved_completions"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM task_completions WHERE status IN ('submitted','review_requested')")
    stats["pending_reviews"] = c.fetchone()[0]
    c.execute("SELECT SUM(stars_amount) FROM stars_payments WHERE status='paid'")
    stats["total_stars_revenue"] = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM stars_payments WHERE status='paid'"); stats["stars_payments_count"] = c.fetchone()[0]
    c.execute("SELECT SUM(referrals_count) FROM users"); stats["total_referrals"] = c.fetchone()[0] or 0
    c.execute("SELECT category, COUNT(*) FROM tasks GROUP BY category")
    stats["tasks_by_category"] = dict(c.fetchall())
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    stats["top_balances"] = c.fetchall()
    c.execute("SELECT username, referrals_count FROM users ORDER BY referrals_count DESC LIMIT 10")
    stats["top_referrers"] = c.fetchall()
    conn.close()
    return stats
