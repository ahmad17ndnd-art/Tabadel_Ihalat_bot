import os
import re
import logging
import sqlite3
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButtonRequestUsers, KeyboardButtonRequestChat
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== الإعدادات الرئيسية ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1922499737"))

if not BOT_TOKEN:
    raise RuntimeError("لازم تضيف متغير البيئة BOT_TOKEN قبل تشغيل البوت")

telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
app = FastAPI()

# ==================== قاعدة البيانات ====================
DB_NAME = os.environ.get("DB_PATH", "bot_data.db")

MIN_WAIT_SECONDS = 8
USERS_PAGE_SIZE = 8

# فئات الإعلانات
CATEGORY_LABELS = {
    "channel": "📣 قناة",
    "group": "👥 مجموعة",
    "bot": "🤖 بوت",
    "post": "📝 منشور",
    "interaction": "❤️ تفاعل",
}
CATEGORY_ORDER = ["channel", "group", "bot", "post", "interaction"]
# الفئات التي يمكن التحقق منها بشكل حقيقي عبر تليجرام (لو البوت أدمن فيها)
AUTO_VERIFIABLE_CATEGORIES = {"channel", "group"}

# الأسعار هنا تمثل (المكافأة لكل شخص)
DEFAULT_CATEGORY_PRICES = {
    "channel": (10, 50),
    "group": (10, 50),
    "bot": (15, 60),
    "post": (5, 30),
    "interaction": (5, 20),
}


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # جدول المستخدمين
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            banned INTEGER DEFAULT 0,
            joined_at TEXT,
            points INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            ref_by INTEGER DEFAULT NULL,
            clicked_verify_link INTEGER DEFAULT 0,
            last_gift_at TEXT DEFAULT NULL,
            gate_sent_at TEXT DEFAULT NULL
        )
    """)

    # جدول الإعدادات العامة
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT '👋 أهلاً بك في بوت النقاط والإحالات الفخم!',
            after_photo_msg TEXT DEFAULT '📸 تم استلام الصورة بنجاح، شكراً لمشاركتك!',
            verify_link TEXT DEFAULT 'https://t.me/ATF_AIRDROP_bot',
            verify_fail_msg TEXT DEFAULT '❌ لم يتم التفعيل.\nيرجى فتح رابط التفعيل أولاً ثم الضغط على زر "✅ أنا فعلت الحساب".',
            first_sub_msg TEXT DEFAULT 'عذراً عزيزي عليك الاشتراك بالبوت التالي',
            daily_gift_points INTEGER DEFAULT 50,
            verify_channel_id TEXT DEFAULT NULL
        )
    """)
    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # جدول أسعار فئات الإعلانات (حد أدنى / أقصى يحدده الأدمن للمكافأة)
    c.execute("""
        CREATE TABLE IF NOT EXISTS category_prices (
            category TEXT PRIMARY KEY,
            min_price INTEGER DEFAULT 10,
            max_price INTEGER DEFAULT 50
        )
    """)
    for cat, (mn, mx) in DEFAULT_CATEGORY_PRICES.items():
        c.execute(
            "INSERT OR IGNORE INTO category_prices (category, min_price, max_price) VALUES (?, ?, ?)",
            (cat, mn, mx)
        )

    # جدول الإعلانات (لوحة تبادل الإعلانات) - تم إضافة target_count و current_count
    c.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            ad_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            owner_username TEXT,
            category TEXT,
            link TEXT,
            description TEXT,
            post_price INTEGER DEFAULT 0,
            reward_points INTEGER DEFAULT 0,
            target_count INTEGER DEFAULT 0,
            current_count INTEGER DEFAULT 0,
            verify_mode TEXT DEFAULT 'manual',
            channel_id TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    # جدول تتبع تنفيذ المستخدمين للإعلانات (إثبات، مراجعة، دفع)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ad_completions (
            ad_id INTEGER,
            user_id INTEGER,
            status TEXT DEFAULT 'sent',
            proof_file_id TEXT DEFAULT NULL,
            sent_at TEXT,
            submitted_at TEXT DEFAULT NULL,
            resolved_at TEXT DEFAULT NULL,
            PRIMARY KEY (ad_id, user_id)
        )
    """)

    # ترحيل (migration) لقواعد بيانات قديمة
    migrations = [
        ("users", "gate_sent_at", "TEXT DEFAULT NULL"),
        ("settings", "verify_channel_id", "TEXT DEFAULT NULL"),
        ("ads", "target_count", "INTEGER DEFAULT 0"),
        ("ads", "current_count", "INTEGER DEFAULT 0"),
    ]
    for table, column, col_def in migrations:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


# ==================== حالات المحادثة ====================
WAITING_WELCOME_MSG = 1
WAITING_AFTER_PHOTO_MSG = 2
WAITING_BROADCAST_MSG = 3
WAITING_DIRECT_TEXT = 4
WAITING_VERIFY_LINK = 6
WAITING_VERIFY_FAIL_MSG = 7
WAITING_FIRST_SUB_MSG = 8
WAITING_GIFT_POINTS = 13
WAITING_DAILY_GIFT_POINTS = 14
WAITING_VERIFY_CHANNEL = 15
WAITING_GIFT_ALL_POINTS = 23

WAITING_AD_LINK = 30
WAITING_AD_DESC = 31
WAITING_AD_REWARD = 32
WAITING_AD_QUANTITY = 37  # حالة جديدة لإدخال عدد الأشخاص
WAITING_AD_PRICE_INPUT = 33
WAITING_AD_VERIFY_CHANNEL = 34
WAITING_CATEGORY_PRICE = 35
WAITING_AD_REVIEW_NOTE = 36

# ==================== دوال مساعدة عامة ====================

def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               first_sub_msg, daily_gift_points, verify_channel_id
        FROM settings WHERE id = 1
        """
    )
    row = c.fetchone()
    conn.close()
    return {
        "welcome_msg": row[0],
        "after_photo_msg": row[1],
        "verify_link": row[2],
        "verify_fail_msg": row[3],
        "first_sub_msg": row[4],
        "daily_gift_points": row[5],
        "verify_channel_id": row[6],
    }


def set_gate_sent(user_id: int):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET gate_sent_at = ? WHERE user_id = ?", (now_str, user_id))
    conn.commit()
    conn.close()


def get_gate_sent(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT gate_sent_at FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def seconds_since(timestamp_str):
    if not timestamp_str:
        return None
    try:
        then = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - then).total_seconds()
    except Exception:
        return None


async def is_real_member(channel_id: str, user_id: int) -> bool:
    try:
        member = await telegram_app.bot.get_chat_member(channel_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error(f"get_chat_member failed for {channel_id} / {user_id}: {e}")
        return False


async def is_bot_admin_of(channel_id: str) -> bool:
    try:
        member = await telegram_app.bot.get_chat_member(channel_id, telegram_app.bot.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"bot admin check failed for {channel_id}: {e}")
        return False


def get_user_info(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points, referrals, verified FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, 0, 0
    return row[0], row[1], row[2]


def get_user_by_id(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, points, referrals, verified, banned, joined_at FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0], "username": row[1], "points": row[2], "referrals": row[3],
        "verified": row[4], "banned": row[5], "joined_at": row[6],
    }


def add_points(user_id: int, amount: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def deduct_points(user_id: int, amount: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def add_referral(ref_by: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_by,))
    conn.commit()
    conn.close()


def set_verified(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_rank(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, points FROM users ORDER BY points DESC")
    rows = c.fetchall()
    conn.close()
    rank = None
    for i, (uid, pts) in enumerate(rows, start=1):
        if uid == user_id:
            rank = i
            break
    total = len(rows)
    return rank, total


def get_last_gift_time(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_gift_at FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_last_gift_time(user_id: int):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET last_gift_at = ? WHERE user_id = ?", (now_str, user_id))
    conn.commit()
    conn.close()


def get_all_active_user_ids():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned = 0")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def count_all_users():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    conn.close()
    return total


def get_users_page(offset: int, limit: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, points, banned FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    )
    rows = c.fetchall()
    conn.close()
    return rows


def toggle_ban(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    new_val = 0 if row[0] else 1
    c.execute("UPDATE users SET banned = ? WHERE user_id = ?", (new_val, user_id))
    conn.commit()
    conn.close()
    return new_val


# ==================== دوال مساعدة: أسعار الفئات ====================

def get_category_price(category: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT min_price, max_price FROM category_prices WHERE category = ?", (category,))
    row = c.fetchone()
    conn.close()
    if not row:
        return DEFAULT_CATEGORY_PRICES.get(category, (10, 50))
    return row[0], row[1]


def set_category_price(category: str, min_price: int, max_price: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE category_prices SET min_price = ?, max_price = ? WHERE category = ?",
        (min_price, max_price, category)
    )
    conn.commit()
    conn.close()


# ==================== دوال مساعدة: الإعلانات ====================

def create_ad(owner_id, owner_username, category, link, description, post_price, reward_points, target_count, verify_mode, channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """INSERT INTO ads (owner_id, owner_username, category, link, description, post_price,
                             reward_points, target_count, verify_mode, channel_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (owner_id, owner_username, category, link, description, post_price, reward_points, target_count,
         verify_mode, channel_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    ad_id = c.lastrowid
    conn.commit()
    conn.close()
    return ad_id


def get_ad(ad_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """SELECT ad_id, owner_id, owner_username, category, link, description, post_price,
                  reward_points, target_count, current_count, verify_mode, channel_id, status, created_at
           FROM ads WHERE ad_id = ?""",
        (ad_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["ad_id", "owner_id", "owner_username", "category", "link", "description", "post_price",
            "reward_points", "target_count", "current_count", "verify_mode", "channel_id", "status", "created_at"]
    return dict(zip(keys, row))


def update_ad_status(ad_id, status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ads SET status = ? WHERE ad_id = ?", (status, ad_id))
    conn.commit()
    conn.close()


def get_ads_by_status(status, category=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if category:
        # إضافة ترتيب من الأكثر نقاطاً إلى الأقل، وعدم إظهار الإعلانات التي اكتمل عددها
        c.execute(
            """SELECT ad_id, owner_id, owner_username, category, link, description, post_price, reward_points,
                      target_count, current_count, verify_mode, channel_id, status, created_at
               FROM ads WHERE status = ? AND category = ? AND current_count < target_count
               ORDER BY reward_points DESC, created_at DESC""",
            (status, category)
        )
    else:
        c.execute(
            """SELECT ad_id, owner_id, owner_username, category, link, description, post_price, reward_points,
                      target_count, current_count, verify_mode, channel_id, status, created_at
               FROM ads WHERE status = ? ORDER BY created_at DESC""",
            (status,)
        )
    rows = c.fetchall()
    conn.close()
    keys = ["ad_id", "owner_id", "owner_username", "category", "link", "description", "post_price",
            "reward_points", "target_count", "current_count", "verify_mode", "channel_id", "status", "created_at"]
    return [dict(zip(keys, r)) for r in rows]


def get_ads_by_owner(owner_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT ad_id, category, link, post_price, reward_points, target_count, current_count, status FROM ads WHERE owner_id = ? ORDER BY created_at DESC",
        (owner_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== دوال مساعدة: تنفيذ الإعلانات (إثبات/مراجعة) ====================

def get_completion(ad_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT status, proof_file_id, sent_at FROM ad_completions WHERE ad_id = ? AND user_id = ?",
        (ad_id, user_id)
    )
    row = c.fetchone()
    conn.close()
    return row


def open_ad_for_user(ad_id, user_id):
    row = get_completion(ad_id, user_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if row is None or row[0] == "rejected":
        c.execute(
            "INSERT INTO ad_completions (ad_id, user_id, status, sent_at) VALUES (?, ?, 'sent', ?) "
            "ON CONFLICT(ad_id, user_id) DO UPDATE SET status = 'sent', sent_at = excluded.sent_at, "
            "proof_file_id = NULL, submitted_at = NULL, resolved_at = NULL",
            (ad_id, user_id, now_str)
        )
        conn.commit()
        conn.close()
        return "sent"
    conn.close()
    return row[0]


def set_completion_proof(ad_id, user_id, file_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE ad_completions SET status = 'proof_submitted', proof_file_id = ?, submitted_at = ? "
        "WHERE ad_id = ? AND user_id = ?",
        (file_id, now_str, ad_id, user_id)
    )
    conn.commit()
    conn.close()


def set_completion_status(ad_id, user_id, status):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE ad_completions SET status = ?, resolved_at = ? WHERE ad_id = ? AND user_id = ?",
        (status, now_str, ad_id, user_id)
    )
    conn.commit()
    conn.close()


def get_latest_pending_proof_request(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """SELECT ac.ad_id FROM ad_completions ac
           JOIN ads a ON ac.ad_id = a.ad_id
           WHERE ac.user_id = ? AND ac.status = 'sent' AND a.verify_mode = 'manual'
           ORDER BY ac.sent_at DESC LIMIT 1""",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def get_latest_review_request(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT ad_id FROM ad_completions WHERE user_id = ? AND status = 'review_requested' ORDER BY sent_at DESC LIMIT 1",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def user_ad_status(ad_id, user_id):
    row = get_completion(ad_id, user_id)
    return row[0] if row else None


# ==================== واجهة المستخدم الفخمة ====================

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    points, referrals, verified = get_user_info(user.id)
    rank, total_users = get_user_rank(user.id)

    status = "✅ مفعّل" if verified else "❌ غير مفعّل"
    level = "🥉 مبتدئ"
    if points >= 500:
        level = "🥈 نشط"
    if points >= 1000:
        level = "🥇 VIP"
    if points >= 2000:
        level = "👑 Super VIP"

    rank_text = "غير مصنّف بعد" if not rank else f"#{rank} من أصل {total_users} مستخدم"

    text = (
        f"✨ بوت النقاط والإحالات الفخم ✨\n\n"
        f"👤 المستخدم: {user.username or user.full_name}\n"
        f"💰 نقاطك: {points}\n"
        f"👥 إحالاتك: {referrals}\n"
        f"🔐 حالة الحساب: {status}\n"
        f"🏆 مستواك: {level}\n"
        f"📊 ترتيبك العام: {rank_text}\n\n"
        f"اختر من القائمة التالية:"
    )

    keyboard = [
        [InlineKeyboardButton("💰 اربح نقاط", callback_data="earn_menu")],
        [InlineKeyboardButton("📢 نشر إعلان", callback_data="publish_ad")],
        [InlineKeyboardButton("🔐 تفعيل الحساب", callback_data="user_activate_menu")],
        [InlineKeyboardButton("💰 نقاطي", callback_data="user_points")],
        [InlineKeyboardButton("👥 إحالاتي", callback_data="user_referrals")],
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="user_stats")],
        [InlineKeyboardButton("🏆 أفضل 20 مستخدم", callback_data="user_top")],
        [InlineKeyboardButton("🎁 الهدية اليومية", callback_data="user_daily_gift")],
        [InlineKeyboardButton("📋 إعلاناتي", callback_data="my_ads")],
    ]

    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="show_stats")])
        keyboard.append([InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="ubrowse:0")])
        keyboard.append([InlineKeyboardButton("🏆 أفضل 20 (أدمن)", callback_data="admin_top20")])
        keyboard.append([InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")])
        keyboard.append([InlineKeyboardButton("📢 إدارة الإعلانات", callback_data="admin_ads_menu")])
        keyboard.append([InlineKeyboardButton("💵 أسعار فئات الإعلانات", callback_data="admin_cat_prices")])
        keyboard.append([InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")])
        keyboard.append([InlineKeyboardButton("🎁 إرسال هدية نقاط للجميع", callback_data="gift_all")])

    target = update.effective_message or update.callback_query.message
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def send_subscription_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    settings = get_settings()
    keyboard = [
        [InlineKeyboardButton("🔗 فتح الرابط", url=settings["verify_link"])],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="user_confirm_verify")],
    ]
    set_gate_sent(user.id)
    target = update.effective_message or update.callback_query.message
    await target.reply_text(settings["first_sub_msg"], reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== /start مع إحالات ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    ref_by = None
    if args:
        try:
            ref_by = int(args[0])
        except Exception:
            ref_by = None

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username, joined_at, ref_by) VALUES (?, ?, ?, ?)",
        (user.id, user.username or user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ref_by)
    )
    conn.commit()
    conn.close()

    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)

    _, _, verified = get_user_info(user.id)
    
    # نظهر الواجهة الرئيسية مباشرةً
    await send_main_menu(update, context)

    try:
        if user.id != ADMIN_ID:
            await telegram_app.bot.send_message(
                ADMIN_ID,
                f"👤 مستخدم جديد دخل:\nالاسم: {user.username or user.full_name}\nID: {user.id}"
            )
    except Exception as e:
        logger.error(f"Failed to notify admin about new user: {e}")


# ==================== لوحة الإدارة الفخمة ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="show_stats")],
        [InlineKeyboardButton("🏆 أفضل 20 مستخدم بالنقاط", callback_data="admin_top20")],
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="ubrowse:0")],
        [InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")],
        [InlineKeyboardButton("📢 إدارة الإعلانات", callback_data="admin_ads_menu")],
        [InlineKeyboardButton("💵 أسعار فئات الإعلانات", callback_data="admin_cat_prices")],
        [InlineKeyboardButton("🎁 إرسال هدية نقاط للجميع", callback_data="gift_all")],
    ]

    await update.message.reply_text(
        "🧑‍💼 لوحة الإدارة الفخمة:\nاختر ما تريد من الخيارات التالية:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    # منيو تعديل الرسائل
    if data == "admin_messages_menu":
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
            [InlineKeyboardButton("📸 تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
            [InlineKeyboardButton("🔗 تعديل الرابط الإجباري", callback_data="change_verify_link")],
            [InlineKeyboardButton("📩 تعديل الرسالة الإجباريّة", callback_data="change_first_sub_msg")],
            [InlineKeyboardButton("🎁 تعديل نقاط الهدية اليومية", callback_data="change_daily_gift_points")],
            [InlineKeyboardButton("🔒 قناة التحقق الحقيقي (اختياري)", callback_data="change_verify_channel")],
        ]
        await query.message.reply_text(
            "اختر الرسالة / الإعداد الذي تريد تعديله:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "show_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE banned = 0")
        active = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
        banned = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
        verified = c.fetchone()[0]
        c.execute("SELECT SUM(points) FROM users")
        total_points = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM ads WHERE status = 'pending'")
        pending_ads = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM ads WHERE status = 'active'")
        active_ads = c.fetchone()[0]
        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات البوت الفخم:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"✅ النشطون: {active}\n"
            f"🚫 المحظورون: {banned}\n"
            f"🔐 المفعّلون: {verified}\n"
            f"💰 إجمالي النقاط: {total_points}\n\n"
            f"📢 إعلانات قيد المراجعة: {pending_ads}\n"
            f"📢 إعلانات نشطة: {active_ads}"
        )
        return

    if data == "admin_top20":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()

        if not rows:
            await query.message.reply_text("لا يوجد بيانات كافية لعرض أفضل المستخدمين.")
            return

        text = "🏆 أفضل 20 مستخدم بالنقاط (عرض الأدمن):\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (uname, pts) in enumerate(rows):
            medal = medals[i] if i < len(medals) else "🔹"
            text += f"{medal} {uname} — {pts} نقطة\n"

        await query.message.reply_text(text)
        return

    # ==== أسعار فئات الإعلانات ====
    if data == "admin_cat_prices":
        keyboard = []
        for cat in CATEGORY_ORDER:
            mn, mx = get_category_price(cat)
            keyboard.append([InlineKeyboardButton(
                f"{CATEGORY_LABELS[cat]} ({mn} - {mx})", callback_data=f"catprice:{cat}"
            )])
        await query.message.reply_text(
            "💵 حدود المكافأة لكل فئة إعلانات حالياً (اضغط لتعديل فئة):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ==== إدارة الإعلانات ====
    if data == "admin_ads_menu":
        keyboard = [
            [InlineKeyboardButton("🕓 قيد المراجعة", callback_data="adlist:pending")],
            [InlineKeyboardButton("🟢 الإعلانات النشطة", callback_data="adlist:active")],
        ]
        await query.message.reply_text("📢 إدارة لوحة تبادل الإعلانات:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("adlist:"):
        which = data.split(":")[1]
        status = "pending" if which == "pending" else "active"
        ads = get_ads_by_status(status)
        if not ads:
            await query.message.reply_text("لا يوجد إعلانات في هذا القسم حالياً.")
            return
        for ad in ads:
            verify_txt = "✅ تحقق حقيقي تلقائي" if ad["verify_mode"] == "auto" else "🧾 إثبات بالصورة"
            text = (
                f"📌 إعلان #{ad['ad_id']} — {CATEGORY_LABELS.get(ad['category'], ad['category'])}\n"
                f"👤 صاحب الإعلان: {ad['owner_username']} (ID: {ad['owner_id']})\n"
                f"🔗 الرابط: {ad['link']}\n"
                f"📝 الوصف: {ad['description']}\n"
                f"💵 التكلفة المدفوعة: {ad['post_price']} نقطة\n"
                f"🎁 مكافأة الشخص: {ad['reward_points']} نقطة\n"
                f"👥 العدد: {ad['current_count']}/{ad['target_count']}\n"
                f"🔍 طريقة التحقق: {verify_txt}"
            )
            if status == "pending":
                keyboard = [[
                    InlineKeyboardButton("✅ قبول ونشر", callback_data=f"adnew:approve:{ad['ad_id']}"),
                    InlineKeyboardButton("❌ رفض", callback_data=f"adnew:reject:{ad['ad_id']}")
                ]]
            else:
                keyboard = [[InlineKeyboardButton("⏹ إيقاف الإعلان", callback_data=f"adstop:{ad['ad_id']}")]]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("adnew:"):
        _, action, ad_id_s = data.split(":")
        ad_id = int(ad_id_s)
        ad = get_ad(ad_id)
        if not ad or ad["status"] != "pending":
            await query.message.reply_text("❌ هذا الإعلان غير موجود أو تمت معالجته مسبقاً.")
            return
        if action == "approve":
            update_ad_status(ad_id, "active")
            try:
                await telegram_app.bot.send_message(
                    ad["owner_id"],
                    f"🎉 تم قبول إعلانك #{ad_id} ونشره!\nرح يبلش المستخدمين يشوفوه بقسم «💰 اربح نقاط»."
                )
            except Exception as e:
                logger.error(f"Failed to notify ad owner {ad['owner_id']}: {e}")
            await query.message.reply_text(f"✔ تم قبول ونشر الإعلان #{ad_id}")
        else:
            update_ad_status(ad_id, "rejected")
            if ad["post_price"]:
                add_points(ad["owner_id"], ad["post_price"])
            try:
                await telegram_app.bot.send_message(
                    ad["owner_id"],
                    f"❌ تم رفض إعلانك #{ad_id}.\n💰 تم إرجاع {ad['post_price']} نقطة إلى رصيدك."
                )
            except Exception as e:
                logger.error(f"Failed to notify ad owner {ad['owner_id']}: {e}")
            await query.message.reply_text(f"🚫 تم رفض الإعلان #{ad_id} وإرجاع نقاط صاحبه")
        return

    if data.startswith("adstop:"):
        ad_id = int(data.split(":")[1])
        ad = get_ad(ad_id)
        if not ad:
            await query.message.reply_text("❌ الإعلان غير موجود.")
            return
        update_ad_status(ad_id, "stopped")
        try:
            await telegram_app.bot.send_message(ad["owner_id"], f"⏹ تم إيقاف إعلانك #{ad_id} من قبل الإدارة.")
        except Exception:
            pass
        await query.message.reply_text(f"⏹ تم إيقاف الإعلان #{ad_id}")
        return

    # ==== إدارة المستخدمين (تصفح مصغّر) ====
    if data.startswith("ubrowse:"):
        page = int(data.split(":")[1])
        await show_users_page(query, page)
        return

    if data.startswith("usel:"):
        _, uid_s, page_s = data.split(":")
        await show_user_detail(query, int(uid_s), int(page_s))
        return

    if data.startswith("toggleban:"):
        _, uid_s, page_s = data.split(":")
        uid = int(uid_s)
        new_val = toggle_ban(uid)
        if new_val is None:
            await query.message.reply_text("المستخدم غير موجود.")
        else:
            txt = "🚫 تم حظر المستخدم" if new_val == 1 else "✅ تم إلغاء حظر المستخدم"
            await query.message.reply_text(txt)
        return


async def show_users_page(query, page: int):
    total = count_all_users()
    offset = page * USERS_PAGE_SIZE
    rows = get_users_page(offset, USERS_PAGE_SIZE)

    if not rows:
        await query.message.reply_text("لا يوجد مستخدمين مسجلين.")
        return

    keyboard = []
    for uid, uname, pts, banned in rows:
        mark = "🚫" if banned else "✅"
        label = f"{mark} {uname or uid} • {pts}pt"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"usel:{uid}:{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"ubrowse:{page - 1}"))
    if offset + USERS_PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"ubrowse:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    total_pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    await query.message.reply_text(
        f"👥 قائمة المستخدمين (مرتبة بتاريخ الانضمام) — صفحة {page + 1} من {total_pages}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_user_detail(query, uid: int, page: int):
    info = get_user_by_id(uid)
    if not info:
        await query.message.reply_text("المستخدم غير موجود.")
        return
    status = "محظور 🚫" if info["banned"] else "نشط ✅"
    vstatus = "مفعّل ✅" if info["verified"] else "غير مفعّل ❌"
    text = (
        f"👤 {info['username']}\nID: {info['user_id']}\n"
        f"الحالة: {status}\nالتفعيل: {vstatus}\n"
        f"💰 النقاط: {info['points']}\n👥 الإحالات: {info['referrals']}\n"
        f"🗓 تاريخ الانضمام: {info['joined_at']}"
    )
    ban_label = "✅ إلغاء الحظر" if info["banned"] else "🚫 حظر المستخدم"
    keyboard = [
        [InlineKeyboardButton("💬 رسالة", callback_data=f"msgu:{uid}")],
        [InlineKeyboardButton(ban_label, callback_data=f"toggleban:{uid}:{page}")],
        [InlineKeyboardButton("🎁 هدية نقاط", callback_data=f"giftu:{uid}")],
        [InlineKeyboardButton("⬅️ رجوع للقائمة", callback_data=f"ubrowse:{page}")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== تعديل الرسائل والإعدادات ====================

async def ask_welcome_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الرسالة الترحيبية الجديدة (فخمة):")
    return WAITING_WELCOME_MSG


async def save_welcome_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET welcome_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ الرسالة الترحيبية ✔")
    return ConversationHandler.END


async def ask_after_photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الرسالة التي تظهر بعد الصورة (فخمة):")
    return WAITING_AFTER_PHOTO_MSG


async def save_after_photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET after_photo_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رسالة بعد الصورة ✔")
    return ConversationHandler.END


async def ask_verify_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رابط البوت/المصدر الذي تريد استخدامه للتفعيل:")
    return WAITING_VERIFY_LINK


async def save_verify_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET verify_link = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رابط التفعيل ✔")
    return ConversationHandler.END


async def ask_verify_fail_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رسالة الفشل التي تظهر إذا لم يتم التفعيل:")
    return WAITING_VERIFY_FAIL_MSG


async def save_verify_fail_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET verify_fail_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رسالة الفشل ✔")
    return ConversationHandler.END


async def ask_first_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الرسالة الإجباريّة الجديدة (الرسالة الأولى عند الدخول):")
    return WAITING_FIRST_SUB_MSG


async def save_first_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET first_sub_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✔ تم حفظ الرسالة الإجباريّة بنجاح")
    return ConversationHandler.END


async def ask_daily_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل عدد نقاط الهدية اليومية الجديدة:")
    return WAITING_DAILY_GIFT_POINTS


async def save_daily_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pts = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح لعدد النقاط.")
        return ConversationHandler.END

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET daily_gift_points = ? WHERE id = 1", (pts,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✔ تم حفظ نقاط الهدية اليومية: {pts} نقطة")
    return ConversationHandler.END


async def ask_verify_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🔒 أرسل معرّف القناة/الجروب (مثال: @my_channel أو -1001234567890) "
        "عشان يصير التحقق حقيقي عبر تليجرام لبوابة الدخول.\n\n"
        "⚠️ شرط: لازم تضيف البوت أدمن بهاي القناة/الجروب وإلا ما رح يشتغل.\n\n"
        "لإلغاء التحقق الحقيقي والرجوع للتحقق التلقائي، أرسل: -"
    )
    return WAITING_VERIFY_CHANNEL


async def save_verify_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    val = None if val in ("-", "الغاء", "إلغاء", "clear") else val
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET verify_channel_id = ? WHERE id = 1", (val,))
    conn.commit()
    conn.close()
    if val:
        await update.message.reply_text(f"✔ تم تفعيل التحقق الحقيقي على: {val}")
    else:
        await update.message.reply_text("✔ تم إلغاء التحقق الحقيقي، رجعنا للتحقق التلقائي.")
    return ConversationHandler.END


# ==================== تعديل سعر فئة إعلان ====================

async def ask_category_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":")[1]
    context.user_data["edit_cat"] = cat
    mn, mx = get_category_price(cat)
    await query.message.reply_text(
        f"💵 السعر الحالي للمكافأة (للمستخدم الواحد) لفئة {CATEGORY_LABELS.get(cat, cat)}: {mn} - {mx}\n"
        f"أرسل الحد الأدنى والحد الأقصى الجديد مفصولين بمسافة، مثال:\n10 50"
    )
    return WAITING_CATEGORY_PRICE


async def save_category_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = context.user_data.get("edit_cat")
    if not cat:
        await update.message.reply_text("حدث خطأ، حاول مجدداً.")
        return ConversationHandler.END

    parts = re.split(r"[\s\-]+", update.message.text.strip())
    parts = [p for p in parts if p]
    if len(parts) != 2:
        await update.message.reply_text("❌ الصيغة غير صحيحة. أرسل رقمين مفصولين بمسافة، مثال: 10 50")
        return ConversationHandler.END
    try:
        mn, mx = int(parts[0]), int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال أرقام صحيحة.")
        return ConversationHandler.END
    if mn > mx or mn < 0:
        await update.message.reply_text("❌ يجب أن يكون الحد الأدنى أصغر من أو يساوي الحد الأقصى.")
        return ConversationHandler.END

    set_category_price(cat, mn, mx)
    context.user_data.pop("edit_cat", None)
    await update.message.reply_text(f"✔ تم تحديث سعر فئة {CATEGORY_LABELS.get(cat, cat)}: {mn} - {mx}")
    return ConversationHandler.END


# ==================== بث رسائل جماعية ====================

async def ask_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة الجماعية (سترسل لكل المستخدمين غير المحظورين):")
    return WAITING_BROADCAST_MSG


async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    user_ids = get_all_active_user_ids()

    sent = 0
    for uid in user_ids:
        try:
            await telegram_app.bot.send_message(uid, msg)
            sent += 1
        except Exception as e:
            logger.error(f"Error sending to {uid}: {e}")

    await update.message.reply_text(f"تم إرسال الرسالة إلى {sent} مستخدم ✔")
    return ConversationHandler.END


# ==================== رسالة فردية لمستخدم محدد ====================

async def ask_direct_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = int(update.callback_query.data.split(":")[1])
    context.user_data["target_user"] = user_id
    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة التي تريد إرسالها لهذا المستخدم:")
    return WAITING_DIRECT_TEXT


async def process_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    target = context.user_data.get("target_user")
    if not target:
        await update.message.reply_text("لم يتم تحديد المستخدم المستهدف.")
        return ConversationHandler.END

    try:
        await telegram_app.bot.send_message(target, msg)
        await update.message.reply_text("تم إرسال الرسالة ✔")
    except Exception as e:
        await update.message.reply_text(f"خطأ أثناء الإرسال: {e}")

    return ConversationHandler.END


# ==================== هدية نقاط من الأدمن (لمستخدم واحد) ====================

async def ask_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split(":")[1])
    context.user_data["gift_target"] = uid
    await query.message.reply_text("🎁 أرسل الآن عدد النقاط التي تريد منحها لهذا المستخدم كهدية:")
    return WAITING_GIFT_POINTS


async def process_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get("gift_target")
    if not target:
        await update.message.reply_text("لم يتم تحديد المستخدم المستهدف للهدية.")
        return ConversationHandler.END

    try:
        pts = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح لعدد النقاط.")
        return ConversationHandler.END

    add_points(target, pts)

    try:
        await telegram_app.bot.send_message(
            target,
            f"🎁 وصلك هدية نقاط من الأدمن: +{pts} نقطة\nاستمتع!"
        )
    except Exception as e:
        logger.error(f"Failed to send gift to {target}: {e}")

    await update.message.reply_text(f"✔ تم إرسال هدية {pts} نقطة للمستخدم {target}")
    return ConversationHandler.END


# ==================== هدية نقاط لجميع المستخدمين ====================

async def ask_gift_all_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🎁 أرسل عدد النقاط التي تريد منحها لكل المستخدمين غير المحظورين دفعة واحدة:"
    )
    return WAITING_GIFT_ALL_POINTS


async def process_gift_all_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pts = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح لعدد النقاط.")
        return ConversationHandler.END

    user_ids = get_all_active_user_ids()
    for uid in user_ids:
        add_points(uid, pts)
        try:
            await telegram_app.bot.send_message(
                uid,
                f"🎁 وصلتك هدية نقاط من الإدارة لكل المستخدمين: +{pts} نقطة\nاستمتع!"
            )
        except Exception as e:
            logger.error(f"Failed to notify {uid} about gift-all: {e}")

    await update.message.reply_text(f"✔ تم منح {pts} نقطة لعدد {len(user_ids)} مستخدم")
    return ConversationHandler.END


# ==================== نشر إعلان (لوحة تبادل الإعلانات) ====================

async def ask_ad_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = []
    for cat in CATEGORY_ORDER:
        keyboard.append([InlineKeyboardButton(f"نشر {CATEGORY_LABELS[cat]}", callback_data=f"adcat:{cat}")])
    await query.message.reply_text("📢 اختر نوع الإعلان الذي تريد نشره:", reply_markup=InlineKeyboardMarkup(keyboard))

async def ad_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":")[1]
    context.user_data["new_ad"] = {"category": cat}
    
    # استخدام أزرار الـ Request لاختيار البوت أو القناة مباشرة من تليجرام
    if cat == "bot":
        btn = KeyboardButton("🤖 إضافة بوت", request_user=KeyboardButtonRequestUser(request_id=1, user_is_bot=True))
        reply_markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text(
            f"✅ اخترت فئة: {CATEGORY_LABELS[cat]}\n\n👇 اضغط على الزر بالأسفل لاختيار البوت من قائمة محادثاتك:",
            reply_markup=reply_markup
        )
    elif cat in ["channel", "group"]:
        btn = KeyboardButton("📢 اختيار قناة/مجموعة", request_chat=KeyboardButtonRequestChat(request_id=2, chat_is_channel=(cat=="channel")))
        reply_markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text(
            f"✅ اخترت فئة: {CATEGORY_LABELS[cat]}\n\n👇 اضغط على الزر بالأسفل لاختيار القناة أو المجموعة:",
            reply_markup=reply_markup
        )
    else:
        await query.message.reply_text(f"✅ اخترت فئة: {CATEGORY_LABELS[cat]}\n\n🔗 أرسل الآن رابط ما تريد الإعلان عنه:")
    
    return WAITING_AD_LINK


async def ad_got_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_data = context.user_data.get("new_ad", {})
    link = ""

    # التقاط الـ ID إذا استخدم المستخدم الأزرار المدمجة (request_user/chat)
    if update.message.user_shared:
        link = f"tg://user?id={update.message.user_shared.user_id}"
    elif update.message.chat_shared:
        link = f"tg://resolve?domain={update.message.chat_shared.chat_id}"
    elif update.message.text:
        link = update.message.text.strip()
    
    if not link:
        await update.message.reply_text("❌ لم يتم التعرف على الرابط أو البوت. حاول مرة أخرى.")
        return WAITING_AD_LINK

    ad_data["link"] = link
    
    # إزالة كيبورد الأزرار السفلية إن وجدت
    await update.message.reply_text(
        "📝 الآن أرسل وصف/نص الإعلان (ماذا تريد من الشخص أن يفعله؟):",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_AD_DESC


async def ad_got_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ad"]["description"] = update.message.text
    
    cat = context.user_data["new_ad"]["category"]
    mn, mx = get_category_price(cat)
    context.user_data["new_ad"]["min_p"] = mn
    context.user_data["new_ad"]["max_p"] = mx
    
    await update.message.reply_text(
        f"🎁 حدد المكافأة (عدد النقاط) التي تريد إعطاءها لكل شخص ينفذ المهمة.\n"
        f"يجب أن تكون بين {mn} و {mx} نقطة:"
    )
    return WAITING_AD_REWARD


async def ad_got_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_data = context.user_data.get("new_ad", {})
    try:
        reward = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح.")
        return WAITING_AD_REWARD

    mn, mx = ad_data.get("min_p", 10), ad_data.get("max_p", 50)
    if reward < mn or reward > mx:
        await update.message.reply_text(f"❌ المكافأة يجب أن تكون بين {mn} و {mx} نقطة. حاول مجدداً:")
        return WAITING_AD_REWARD

    context.user_data["new_ad"]["reward_points"] = reward
    
    user_points, _, _ = get_user_info(update.effective_user.id)
    max_people = user_points // reward

    if max_people <= 0:
        await update.message.reply_text(
            f"❌ نقاطك الحالية ({user_points}) لا تكفي لنشر هذا الإعلان.\n"
            f"تحتاج على الأقل {reward} نقطة لشخص واحد."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"👥 الحد الأقصى للأشخاص الذين يمكنك إضافتهم بنقاطك هو: {max_people} شخص.\n"
        f"كم عدد الأشخاص الذين تريدهم أن ينفذوا المهمة؟ أرسل الرقم:"
    )
    return WAITING_AD_QUANTITY


async def ad_got_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_count = int(update.message.text.strip())
        if target_count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح أكبر من صفر.")
        return WAITING_AD_QUANTITY

    ad_data = context.user_data["new_ad"]
    reward = ad_data["reward_points"]
    user_points, _, _ = get_user_info(update.effective_user.id)
    
    total_cost = reward * target_count
    if total_cost > user_points:
        await update.message.reply_text(
            f"❌ نقاطك لا تكفي. إجمالي التكلفة سيكون {total_cost}، ونقاطك الحالية {user_points}.\n"
            f"الرجاء إرسال عدد أقل:"
        )
        return WAITING_AD_QUANTITY

    ad_data["target_count"] = target_count
    ad_data["post_price"] = total_cost

    cat = ad_data["category"]
    if cat in AUTO_VERIFIABLE_CATEGORIES:
        await update.message.reply_text(
            "🔒 هل تريد تحقق حقيقي 100%؟ إذا نعم، أضف البوت أدمن على القناة/المجموعة ثم أرسل معرّفها "
            "(مثال: @my_channel أو -1001234567890).\n"
            "أو أرسل - للتجاوز والاعتماد على إثبات الصورة (سكرين شوت) بدالها."
        )
        return WAITING_AD_VERIFY_CHANNEL

    return await finalize_ad_creation(update, context)


async def ad_got_verify_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if val in ("-", "الغاء", "إلغاء", "clear"):
        context.user_data["new_ad"]["verify_mode"] = "manual"
        context.user_data["new_ad"]["channel_id"] = None
        return await finalize_ad_creation(update, context)

    ok = await is_bot_admin_of(val)
    if ok:
        context.user_data["new_ad"]["verify_mode"] = "auto"
        context.user_data["new_ad"]["channel_id"] = val
        await update.message.reply_text("✅ تم التأكد إن البوت أدمن هناك، رح يصير التحقق تلقائي وحقيقي 100%!")
    else:
        context.user_data["new_ad"]["verify_mode"] = "manual"
        context.user_data["new_ad"]["channel_id"] = None
        await update.message.reply_text(
            "⚠️ ما قدرت أتأكد إن البوت أدمن هناك، رح نعتمد إثبات الصورة (سكرين شوت) بدالها."
        )

    return await finalize_ad_creation(update, context)


async def finalize_ad_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    new_ad = context.user_data.get("new_ad", {})
    price = new_ad.get("post_price", 0)

    points, _, _ = get_user_info(user.id)
    if points < price:
        await update.message.reply_text(
            f"❌ رصيدك من النقاط غير كافٍ لنشر هذا الإعلان.\n💵 السعر الإجمالي: {price} نقطة\n💰 رصيدك: {points} نقطة"
        )
        context.user_data.pop("new_ad", None)
        return ConversationHandler.END

    deduct_points(user.id, price)
    ad_id = create_ad(
        owner_id=user.id,
        owner_username=user.username or user.full_name,
        category=new_ad["category"],
        link=new_ad["link"],
        description=new_ad["description"],
        post_price=price,
        reward_points=new_ad["reward_points"],
        target_count=new_ad["target_count"],
        verify_mode=new_ad.get("verify_mode", "manual"),
        channel_id=new_ad.get("channel_id"),
    )

    context.user_data.pop("new_ad", None)

    await update.message.reply_text(
        f"✅ تم إرسال إعلانك #{ad_id} للمراجعة من الإدارة.\n💰 تم خصم {price} نقطة من رصيدك.\n"
        f"سيتم إعلامك فور الموافقة أو الرفض."
    )

    try:
        ad = get_ad(ad_id)
        verify_txt = "✅ تحقق حقيقي تلقائي" if ad["verify_mode"] == "auto" else "🧾 إثبات بالصورة"
        keyboard = [[
            InlineKeyboardButton("✅ قبول ونشر", callback_data=f"adnew:approve:{ad_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"adnew:reject:{ad_id}")
        ]]
        await telegram_app.bot.send_message(
            ADMIN_ID,
            f"📢 طلب إعلان جديد #{ad_id} — {CATEGORY_LABELS.get(ad['category'], ad['category'])}\n"
            f"من: {ad['owner_username']} (ID: {ad['owner_id']})\n"
            f"🔗 الرابط: {ad['link']}\n"
            f"📝 الوصف: {ad['description']}\n"
            f"💵 إجمالي التكلفة: {ad['post_price']} نقطة\n"
            f"🎁 مكافأة للشخص: {ad['reward_points']} نقطة | العدد المطلوب: {ad['target_count']}\n"
            f"🔍 طريقة التحقق: {verify_txt}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about new ad: {e}")

    return ConversationHandler.END


# ==================== إعلاناتي (لصاحب الإعلان) ====================

async def show_my_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    ads = get_ads_by_owner(user.id)
    if not ads:
        await query.message.reply_text("📋 ما نشرت أي إعلان لهلق. اضغط «📢 نشر إعلان» للبدء.")
        return

    status_labels = {
        "pending": "🕓 قيد المراجعة",
        "active": "🟢 نشط",
        "rejected": "❌ مرفوض",
        "stopped": "⏹ متوقف",
        "completed": "✅ مكتمل"
    }
    for ad_id, category, link, price, reward, target, current, status in ads:
        text = (
            f"📌 إعلان #{ad_id} — {CATEGORY_LABELS.get(category, category)}\n"
            f"🔗 الرابط: {link}\n"
            f"💵 التكلفة: {price} نقطة | 🎁 المكافأة: {reward} نقطة\n"
            f"👥 العدد المنجز: {current} من أصل {target}\n"
            f"الحالة: {status_labels.get(status, status)}"
        )
        keyboard = None
        if status == "active":
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ إيقاف إعلاني", callback_data=f"adownerstop:{ad_id}")]])
        await query.message.reply_text(text, reply_markup=keyboard)


async def owner_stop_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ad_id = int(query.data.split(":")[1])
    ad = get_ad(ad_id)
    if not ad or ad["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ لا يمكنك إيقاف هذا الإعلان.")
        return
    update_ad_status(ad_id, "stopped")
    await query.message.reply_text(f"⏹ تم إيقاف إعلانك #{ad_id}.")


# ==================== زر اربح نقاط ====================

async def show_earn_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = []
    for cat in CATEGORY_ORDER:
        keyboard.append([InlineKeyboardButton(CATEGORY_LABELS[cat], callback_data=f"earncat:{cat}")])
    await query.message.reply_text("💰 اربح نقاط — اختر نوع المهام التي بدك تشوفها:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_earn_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    cat = query.data.split(":")[1]

    ads = get_ads_by_status("active", category=cat)
    shown = 0
    for ad in ads:
        if ad["owner_id"] == user.id:
            continue
        status = user_ad_status(ad["ad_id"], user.id)
        if status in ("approved", "sent", "proof_submitted", "review_requested"):
            continue
        keyboard = [[InlineKeyboardButton("🎯 فتح المهمة", callback_data=f"earnad:{ad['ad_id']}")]]
        await query.message.reply_text(
            f"📌 مهمة بـ {ad['reward_points']} نقطة ({CATEGORY_LABELS.get(cat, cat)})\n"
            f"📝 المطلوب: {ad['description']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        shown += 1

    if shown == 0:
        await query.message.reply_text("لا يوجد مهام متاحة بهاي الفئة حالياً، تابعنا قريباً!")


async def show_earn_ad_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    ad_id = int(query.data.split(":")[1])
    ad = get_ad(ad_id)
    if not ad or ad["status"] != "active" or ad["current_count"] >= ad["target_count"]:
        await query.message.reply_text("❌ هذه المهمة لم تعد متاحة أو اكتمل العدد المطلوب.")
        return

    open_ad_for_user(ad_id, user.id)

    keyboard = [
        [InlineKeyboardButton("🔗 اذهب للرابط/البوت", url=ad["link"])]
    ]
    if ad["verify_mode"] == "auto":
        keyboard.append([InlineKeyboardButton("✅ نفذت المطلوب", callback_data=f"earnconfirm:{ad_id}")])
        verify_hint = "بعد التنفيذ اضغط الزر تحت وراح نتحقق تلقائياً."
    else:
        verify_hint = "⚠️ **ادخل إلى البوت/الرابط ونفذ المطلوب، ثم ارسل سكرين شوت (صورة) إلى هنا مباشرة كإثبات.**"

    await query.message.reply_text(
        f"📌 مهمة بـ {ad['reward_points']} نقطة\n\n"
        f"📝 وصف المهمة:\n{ad['description']}\n\n"
        f"{verify_hint}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def earn_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    ad_id = int(query.data.split(":")[1])
    ad = get_ad(ad_id)
    if not ad or ad["status"] != "active":
        await query.message.reply_text("❌ هذا الإعلان لم يعد متاحاً.")
        return

    row = get_completion(ad_id, user.id)
    if row and row[0] == "approved":
        await query.message.reply_text("✅ لقد أنجزت هذا الإعلان وأخذت مكافأتك من قبل.")
        return

    if ad["verify_mode"] == "auto" and ad["channel_id"]:
        sent_at = row[2] if row else None
        elapsed = seconds_since(sent_at)
        if elapsed is None or elapsed < MIN_WAIT_SECONDS:
            await query.message.reply_text("⏳ تأكد إنك نفذت المطلوب فعلاً، وحاول بعد كم ثانية.")
            return
        ok = await is_real_member(ad["channel_id"], user.id)
        if not ok:
            await query.message.reply_text("❌ لسا ما اشتركت/انضميت، جرب كمان مرة بعد ما تخلّص.")
            return
            
        add_points(user.id, ad["reward_points"])
        set_completion_status(ad_id, user.id, "approved")
        
        # زيادة عداد المنجزين
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE ads SET current_count = current_count + 1 WHERE ad_id = ?", (ad_id,))
        c.execute("SELECT target_count, current_count FROM ads WHERE ad_id = ?", (ad_id,))
        tgt, cur = c.fetchone()
        if cur >= tgt:
            c.execute("UPDATE ads SET status = 'completed' WHERE ad_id = ?", (ad_id,))
        conn.commit()
        conn.close()

        await query.message.reply_text(f"🎉 تم التحقق تلقائياً! حصلت على {ad['reward_points']} نقطة.")
        return

    await query.message.reply_text("🧾 قم بإرسال سكرين شوت (صورة) يثبت إنك نفذت المطلوب.")


# ==================== استقبال الصور ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1].file_id

    # 1) رد على طلب إعادة محاولة
    review_ad_id = get_latest_review_request(user.id)
    if review_ad_id:
        ad = get_ad(review_ad_id)
        set_completion_proof(review_ad_id, user.id, photo)
        try:
            keyboard = [
                [InlineKeyboardButton("✅ دفع", callback_data=f"adpay:{review_ad_id}:{user.id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"adrejc:{review_ad_id}:{user.id}")],
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"adrev:{review_ad_id}:{user.id}")]
            ]
            await telegram_app.bot.send_photo(
                ad["owner_id"], photo,
                caption=f"🧾 رد جديد بعد إعادة المحاولة — مهمة #{review_ad_id}\nمن: {user.username or user.full_name}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to forward review reply: {e}")
        await update.message.reply_text("✔ تم إرسال ردك لصاحب الإعلان، بانتظار الموافقة والدفع.")
        return

    # 2) إثبات صورة بانتظار سكرين شوت
    pending_ad_id = get_latest_pending_proof_request(user.id)
    if pending_ad_id:
        ad = get_ad(pending_ad_id)
        set_completion_proof(pending_ad_id, user.id, photo)
        try:
            keyboard = [
                [InlineKeyboardButton("✅ دفع", callback_data=f"adpay:{pending_ad_id}:{user.id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"adrejc:{pending_ad_id}:{user.id}")],
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"adrev:{pending_ad_id}:{user.id}")]
            ]
            await telegram_app.bot.send_photo(
                ad["owner_id"], photo,
                caption=(
                    f"🧾 إثبات جديد — مهمة #{pending_ad_id} ({CATEGORY_LABELS.get(ad['category'], ad['category'])})\n"
                    f"من: {user.username or user.full_name}\n"
                    f"يرجى الدفع للمستخدم أو طلب إعادة محاولة."
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to forward proof to ad owner: {e}")
        await update.message.reply_text("✔ تم إرسال إثباتك لصاحب الإعلان، رح توصلك النقاط بمجرد الدفع لك.")
        return

    # 3) السلوك الافتراضي (غير مرتبط بإعلان)
    settings = get_settings()
    await update.message.reply_text(settings["after_photo_msg"])
    add_points(user.id, 20)

    try:
        if user.id != ADMIN_ID:
            await telegram_app.bot.send_photo(
                ADMIN_ID,
                photo,
                caption=f"📸 صورة جديدة من {user.username or user.full_name} (ID: {user.id})"
            )
    except Exception as e:
        logger.error(f"Failed to send photo to admin: {e}")


# ==================== استقبال النصوص ====================

async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if user.id == ADMIN_ID:
        await update.message.reply_text(f"استلمت رسالتك: {text}")
        return

    add_points(user.id, 10)

    try:
        await telegram_app.bot.send_message(
            ADMIN_ID,
            f"📩 رسالة جديدة من {user.username or user.full_name}:\n{text}"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about message: {e}")

    await update.message.reply_text(f"استلمت رسالتك: {text}")


# ==================== ردود صاحب الإعلان (دفع/رفض/مراجعة) ====================

async def ad_owner_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    _, ad_id_s, uid_s = data.split(":")
    ad_id, uid = int(ad_id_s), int(uid_s)
    ad = get_ad(ad_id)
    if not ad or ad["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الإجراء غير متاح لك.")
        return

    if data.startswith("adpay:"):
        add_points(uid, ad["reward_points"])
        set_completion_status(ad_id, uid, "approved")
        
        # تحديث عدد المنجزين للإعلان
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE ads SET current_count = current_count + 1 WHERE ad_id = ?", (ad_id,))
        c.execute("SELECT target_count, current_count FROM ads WHERE ad_id = ?", (ad_id,))
        tgt, cur = c.fetchone()
        if cur >= tgt:
            c.execute("UPDATE ads SET status = 'completed' WHERE ad_id = ?", (ad_id,))
        conn.commit()
        conn.close()

        # إزالة الأزرار وإشعار صاحب الإعلان
        await query.edit_message_caption(caption=f"✅ تم الدفع بنجاح للمستخدم.")
        try:
            await telegram_app.bot.send_message(
                uid, f"🎉 تم قبول إثباتك لمهمة #{ad_id}!\n💰 حصلت على {ad['reward_points']} نقطة."
            )
        except Exception:
            pass
        return

    if data.startswith("adrejc:"):
        set_completion_status(ad_id, uid, "rejected")
        await query.edit_message_caption(caption="🚫 تم رفض الإثبات.")
        try:
            await telegram_app.bot.send_message(
                uid, f"❌ تم رفض إثباتك لمهمة #{ad_id}.\nتأكد إنك نفذت المطلوب صح وجرب مرة ثانية."
            )
        except Exception:
            pass
        return


async def ask_review_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ad_id_s, uid_s = query.data.split(":")
    ad = get_ad(int(ad_id_s))
    if not ad or ad["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الإجراء غير متاح لك.")
        return ConversationHandler.END

    context.user_data["review_ad_id"] = int(ad_id_s)
    context.user_data["review_uid"] = int(uid_s)
    await query.message.reply_text("✏️ أرسل رسالة توضح سبب إعادة المحاولة (مثال: يرجى إكمال الكابتشا أولاً):")
    return WAITING_AD_REVIEW_NOTE


async def save_review_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_id = context.user_data.get("review_ad_id")
    uid = context.user_data.get("review_uid")
    if not ad_id or not uid:
        await update.message.reply_text("حدث خطأ، حاول مجدداً.")
        return ConversationHandler.END

    note = update.message.text
    ad = get_ad(ad_id)
    set_completion_status(ad_id, uid, "review_requested")

    keyboard = [[InlineKeyboardButton("الذهاب للبوت مرة أخرى 🔗", url=ad["link"])]]
    try:
        await telegram_app.bot.send_message(
            uid,
            f"⚠️ صاحب الإعلان يطلب منك إعادة المحاولة:\n\n{note}\n\n"
            f"بعد التنفيذ الصحيح، ارسل سكرين شوت هنا مجدداً.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✔ تم إرسال طلب إعادة المحاولة للمستخدم.")
    except Exception as e:
        await update.message.reply_text(f"خطأ أثناء الإرسال: {e}")

    context.user_data.pop("review_ad_id", None)
    context.user_data.pop("review_uid", None)
    return ConversationHandler.END


# ==================== واجهة المستخدم (Callback) ====================

async def user_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    await query.answer()

    points, referrals, verified = get_user_info(user.id)
    settings = get_settings()

    if data == "user_points":
        level = "🥉 مبتدئ"
        if points >= 500:
            level = "🥈 نشط"
        if points >= 1000:
            level = "🥇 VIP"
        if points >= 2000:
            level = "👑 Super VIP"

        rank, total_users = get_user_rank(user.id)
        rank_text = "غير مصنّف بعد" if not rank else f"#{rank} من أصل {total_users} مستخدم"

        await query.message.reply_text(
            f"💰 نقاطك الحالية: {points}\n"
            f"👥 إحالاتك: {referrals}\n"
            f"🏆 مستواك: {level}\n"
            f"📊 ترتيبك العام: {rank_text}"
        )
        return

    if data == "user_referrals":
        ref_link = f"https://t.me/Tabadel_Ihalat_bot?start={user.id}"
        await query.message.reply_text(
            f"👥 إحالاتك: {referrals}\n\n"
            f"🔗 رابط الإحالة الخاص بك:\n{ref_link}"
        )
        return

    if data == "user_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
        verified_count = c.fetchone()[0]
        c.execute("SELECT SUM(points) FROM users")
        total_points = c.fetchone()[0] or 0
        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات البوت:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"🔐 المفعّلون: {verified_count}\n"
            f"💰 إجمالي النقاط: {total_points}"
        )
        return

    if data == "user_top":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()

        if not rows:
            await query.message.reply_text("لا يوجد بيانات كافية لعرض أفضل المستخدمين.")
            return

        text = "🏆 أفضل 20 مستخدم بالنقاط:\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (uname, pts) in enumerate(rows):
            medal = medals[i] if i < len(medals) else "🔹"
            text += f"{medal} {uname} — {pts} نقطة\n"

        await query.message.reply_text(text)
        return

    if data == "user_activate_menu":
        status = "✅ مفعّل" if verified else "❌ غير مفعّل"
        txt = f"🔐 حالة حسابك الحالية: {status}"
        if verified:
            await query.message.reply_text(txt)
        else:
            keyboard = [
                [InlineKeyboardButton("🔗 فتح الرابط", url=settings["verify_link"])],
                [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="user_confirm_verify")],
            ]
            set_gate_sent(user.id)
            await query.message.reply_text(
                txt + "\n\n" + settings["first_sub_msg"],
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data == "user_confirm_verify":
        if verified:
            await query.message.reply_text("🔐 حسابك مفعّل بالفعل ✅")
            return

        channel_id = settings.get("verify_channel_id")
        if channel_id:
            ok = await is_real_member(channel_id, user.id)
            if not ok:
                await query.message.reply_text(settings["verify_fail_msg"])
                return
        else:
            elapsed = seconds_since(get_gate_sent(user.id))
            if elapsed is None or elapsed < MIN_WAIT_SECONDS:
                await query.message.reply_text(
                    "⏳ تأكد إنك فتحت الرابط فعلاً، وحاول بعد كم ثانية."
                )
                return

        set_verified(user.id)
        add_points(user.id, 200)
        await query.message.reply_text(settings["welcome_msg"])
        await query.message.reply_text(
            "🔐 تم تفعيل حسابك بنجاح ✅\n"
            "💰 حصلت على 200 نقطة كمكافأة على التفعيل!"
        )
        await send_main_menu(update, context)
        return

    if data == "user_daily_gift":
        daily_pts = settings["daily_gift_points"]
        last = get_last_gift_time(user.id)

        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                now = datetime.now()
                diff = now - last_dt
                if diff.total_seconds() < 86400:
                    remaining = timedelta(seconds=86400 - diff.total_seconds())
                    hours = remaining.seconds // 3600
                    minutes = (remaining.seconds % 3600) // 60
                    await query.message.reply_text(
                        f"❌ لقد حصلت على هديتك اليومية بالفعل.\n"
                        f"⏳ يمكنك المحاولة بعد {hours} ساعة و {minutes} دقيقة تقريبًا."
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing last_gift_at for {user.id}: {e}")

        add_points(user.id, daily_pts)
        set_last_gift_time(user.id)
        await query.message.reply_text(
            f"🎁 هديتك اليومية وصلت!\n"
            f"💰 تم إضافة {daily_pts} نقطة إلى حسابك."
        )
        return


# ==================== ConversationHandlers ====================

welcome_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_welcome_msg, pattern="^change_welcome$")],
    states={WAITING_WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_welcome_msg)]},
    fallbacks=[]
)

after_photo_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_after_photo_msg, pattern="^change_after_photo$")],
    states={WAITING_AFTER_PHOTO_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_after_photo_msg)]},
    fallbacks=[]
)

broadcast_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_broadcast_msg, pattern="^broadcast$")],
    states={WAITING_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_broadcast)]},
    fallbacks=[]
)

direct_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_direct_msg, pattern="^msgu:")],
    states={WAITING_DIRECT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_direct_message)]},
    fallbacks=[]
)

verify_link_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_verify_link, pattern="^change_verify_link$")],
    states={WAITING_VERIFY_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_verify_link)]},
    fallbacks=[]
)

verify_fail_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_verify_fail_msg, pattern="^change_verify_fail_msg$")],
    states={WAITING_VERIFY_FAIL_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_verify_fail_msg)]},
    fallbacks=[]
)

first_sub_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_first_sub_msg, pattern="^change_first_sub_msg$")],
    states={WAITING_FIRST_SUB_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_first_sub_msg)]},
    fallbacks=[]
)

daily_gift_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_daily_gift_points, pattern="^change_daily_gift_points$")],
    states={WAITING_DAILY_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_daily_gift_points)]},
    fallbacks=[]
)

verify_channel_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_verify_channel, pattern="^change_verify_channel$")],
    states={WAITING_VERIFY_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_verify_channel)]},
    fallbacks=[]
)

gift_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_gift_points, pattern="^giftu:")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)

gift_all_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_gift_all_points, pattern="^gift_all$")],
    states={WAITING_GIFT_ALL_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_all_points)]},
    fallbacks=[]
)

category_price_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_category_price, pattern="^catprice:")],
    states={WAITING_CATEGORY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_category_price)]},
    fallbacks=[]
)

ad_create_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_ad_category, pattern="^publish_ad$")],
    states={
        WAITING_AD_LINK: [MessageHandler(filters.TEXT | filters.StatusUpdate.USER_SHARED | filters.StatusUpdate.CHAT_SHARED, ad_got_link)],
        WAITING_AD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_got_desc)],
        WAITING_AD_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_got_reward)],
        WAITING_AD_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_got_quantity)],
        WAITING_AD_VERIFY_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_got_verify_channel)],
    },
    fallbacks=[],
    per_message=False
)

ad_review_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_review_note, pattern="^adrev:")],
    states={WAITING_AD_REVIEW_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_review_note)]},
    fallbacks=[]
)

# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(
    CallbackQueryHandler(
        admin_navigation_click,
        pattern="^(admin_messages_menu|show_stats|admin_top20|admin_cat_prices|admin_ads_menu|"
                "adlist:.*|adnew:.*|adstop:.*|ubrowse:.*|usel:.*|toggleban:.*)$"
    )
)
telegram_app.add_handler(
    CallbackQueryHandler(
        user_navigation_click,
        pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|"
                "user_confirm_verify|user_daily_gift)$"
    )
)

telegram_app.add_handler(CallbackQueryHandler(ad_category_chosen, pattern="^adcat:"))
telegram_app.add_handler(CallbackQueryHandler(show_my_ads, pattern="^my_ads$"))
telegram_app.add_handler(CallbackQueryHandler(owner_stop_ad, pattern="^adownerstop:"))

telegram_app.add_handler(CallbackQueryHandler(show_earn_menu, pattern="^earn_menu$"))
telegram_app.add_handler(CallbackQueryHandler(show_earn_category, pattern="^earncat:"))
telegram_app.add_handler(CallbackQueryHandler(show_earn_ad_detail, pattern="^earnad:"))
telegram_app.add_handler(CallbackQueryHandler(earn_confirm, pattern="^earnconfirm:"))

telegram_app.add_handler(CallbackQueryHandler(ad_owner_action, pattern="^(adpay|adrejc):"))

telegram_app.add_handler(welcome_conv)
telegram_app.add_handler(after_photo_conv)
telegram_app.add_handler(broadcast_conv)
telegram_app.add_handler(direct_msg_conv)
telegram_app.add_handler(verify_link_conv)
telegram_app.add_handler(verify_fail_msg_conv)
telegram_app.add_handler(first_sub_msg_conv)
telegram_app.add_handler(daily_gift_points_conv)
telegram_app.add_handler(verify_channel_conv)
telegram_app.add_handler(gift_points_conv)
telegram_app.add_handler(gift_all_conv)
telegram_app.add_handler(category_price_conv)
telegram_app.add_handler(ad_create_conv)
telegram_app.add_handler(ad_review_conv)

telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_or_link))

# ==================== FastAPI ====================

@app.get("/")
def home():
    return {"status": "Telegram Bot is running smoothly!"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return {"status": "error", "message": str(e)}


# ==================== تشغيل البوت على Railway ====================

@app.on_event("startup")
async def startup_event():
    init_db()
    await telegram_app.initialize()
    await telegram_app.start()

    railway_url = os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if railway_url:
        webhook_url = f"https://{railway_url}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set successfully to: {webhook_url}")
    else:
        logger.warning("Railway URL not found in environment variables. Please set webhook manually if needed.")


@app.on_event("shutdown")
async def shutdown_event():
    await telegram_app.stop()
    await telegram_app.shutdown()
