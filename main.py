import os
import logging
import sqlite3
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
            verify_channel_id TEXT DEFAULT NULL,
            ad_price INTEGER DEFAULT 100
        )
    """)
    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # جدول المهام (متعدد)
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            link TEXT,
            points INTEGER DEFAULT 0,
            done_msg TEXT DEFAULT '🎉 تم إكمال المهمة بنجاح! تم إضافة النقاط إلى حسابك.',
            channel_id TEXT DEFAULT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    # جدول تتبع تقدّم المستخدم بكل مهمة (لمنع تكرار أخذ نفس المهمة)
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_progress (
            user_id INTEGER,
            task_id INTEGER,
            sent_at TEXT,
            completed_at TEXT DEFAULT NULL,
            PRIMARY KEY (user_id, task_id)
        )
    """)

    # جدول الإعلانات
    c.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            ad_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            content TEXT,
            price_paid INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    # ترحيل (migration) لقواعد بيانات قديمة
    migrations = [
        ("users", "gate_sent_at", "TEXT DEFAULT NULL"),
        ("settings", "verify_channel_id", "TEXT DEFAULT NULL"),
        ("settings", "ad_price", "INTEGER DEFAULT 100"),
    ]
    for table, column, col_def in migrations:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


MIN_WAIT_SECONDS = 8

# ==================== حالات المحادثة ====================
WAITING_WELCOME_MSG = 1
WAITING_AFTER_PHOTO_MSG = 2
WAITING_BROADCAST_MSG = 3
WAITING_DIRECT_TEXT = 4
WAITING_BAN_NAME = 5
WAITING_VERIFY_LINK = 6
WAITING_VERIFY_FAIL_MSG = 7
WAITING_FIRST_SUB_MSG = 8
WAITING_GIFT_POINTS = 13
WAITING_DAILY_GIFT_POINTS = 14
WAITING_VERIFY_CHANNEL = 15

WAITING_TASK_ADD_TEXT = 17
WAITING_TASK_ADD_LINK = 18
WAITING_TASK_ADD_POINTS = 19
WAITING_TASK_ADD_DONE = 20
WAITING_TASK_ADD_CHANNEL = 21
WAITING_TASK_EDIT_FIELD = 22
WAITING_GIFT_ALL_POINTS = 23
WAITING_AD_PRICE = 24
WAITING_AD_CONTENT = 25

# ==================== دوال مساعدة عامة ====================

def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               first_sub_msg, daily_gift_points, verify_channel_id, ad_price
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
        "ad_price": row[7],
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


def get_user_info(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points, referrals, verified FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, 0, 0
    return row[0], row[1], row[2]


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


# ==================== دوال مساعدة: المهام ====================

def add_task(text, link, points, done_msg, channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (text, link, points, done_msg, channel_id, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (text, link, points, done_msg, channel_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def get_all_tasks(active_only=False):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if active_only:
        c.execute("SELECT task_id, text, link, points, done_msg, channel_id, active FROM tasks WHERE active = 1 ORDER BY task_id")
    else:
        c.execute("SELECT task_id, text, link, points, done_msg, channel_id, active FROM tasks ORDER BY task_id")
    rows = c.fetchall()
    conn.close()
    return rows


def get_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT task_id, text, link, points, done_msg, channel_id, active FROM tasks WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    return row


def update_task_field(task_id, field, value):
    allowed = {"text", "link", "points", "done_msg", "channel_id"}
    if field not in allowed:
        return
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f"UPDATE tasks SET {field} = ? WHERE task_id = ?", (value, task_id))
    conn.commit()
    conn.close()


def toggle_task_active(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT active FROM tasks WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        c.execute("UPDATE tasks SET active = ? WHERE task_id = ?", (new_val, task_id))
        conn.commit()
    conn.close()


def delete_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    c.execute("DELETE FROM task_progress WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def set_task_progress_sent(user_id, task_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO task_progress (user_id, task_id, sent_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, task_id) DO UPDATE SET sent_at = excluded.sent_at",
        (user_id, task_id, now_str)
    )
    conn.commit()
    conn.close()


def get_task_progress(user_id, task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT sent_at, completed_at FROM task_progress WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    row = c.fetchone()
    conn.close()
    return row  # (sent_at, completed_at) or None


def set_task_completed(user_id, task_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE task_progress SET completed_at = ? WHERE user_id = ? AND task_id = ?", (now_str, user_id, task_id))
    conn.commit()
    conn.close()


# ==================== دوال مساعدة: الإعلانات ====================

def create_ad(user_id, username, content, price_paid):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO ads (user_id, username, content, price_paid, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (user_id, username, content, price_paid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    ad_id = c.lastrowid
    conn.commit()
    conn.close()
    return ad_id


def get_ad(ad_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT ad_id, user_id, username, content, price_paid, status FROM ads WHERE ad_id = ?", (ad_id,))
    row = c.fetchone()
    conn.close()
    return row


def set_ad_status(ad_id, status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ads SET status = ? WHERE ad_id = ?", (status, ad_id))
    conn.commit()
    conn.close()


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
        [InlineKeyboardButton("🔐 تفعيل الحساب", callback_data="user_activate_menu")],
        [InlineKeyboardButton("💰 نقاطي", callback_data="user_points")],
        [InlineKeyboardButton("👥 إحالاتي", callback_data="user_referrals")],
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="user_stats")],
        [InlineKeyboardButton("🏆 أفضل 20 مستخدم", callback_data="user_top")],
        [InlineKeyboardButton("🎁 الهدية اليومية", callback_data="user_daily_gift")],
        [InlineKeyboardButton("📝 المهام", callback_data="user_tasks_menu")],
        [InlineKeyboardButton("📢 شراء إعلان", callback_data="buy_ad")],
    ]

    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="show_stats")])
        keyboard.append([InlineKeyboardButton("📋 نقاط كل مستخدم", callback_data="list_users")])
        keyboard.append([InlineKeyboardButton("🏆 أفضل 20 (أدمن)", callback_data="admin_top20")])
        keyboard.append([InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")])
        keyboard.append([InlineKeyboardButton("📝 المهام", callback_data="admin_tasks_menu")])
        keyboard.append([InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")])
        keyboard.append([InlineKeyboardButton("💬 إرسال رسالة لمستخدم", callback_data="list_users_msg")])
        keyboard.append([InlineKeyboardButton("🎁 إرسال هدية نقاط لمستخدم", callback_data="admin_send_gift_menu")])
        keyboard.append([InlineKeyboardButton("🎁 إرسال هدية نقاط للجميع", callback_data="gift_all")])
        keyboard.append([InlineKeyboardButton("💵 تغيير سعر الإعلان", callback_data="change_ad_price")])
        keyboard.append([InlineKeyboardButton("🚫 إدارة الحظر", callback_data="ban_user_list")])

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
    if verified:
        await send_main_menu(update, context)
    else:
        await send_subscription_gate(update, context)

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
        [InlineKeyboardButton("📋 عرض كل المستخدمين (حسب النقاط)", callback_data="list_users")],
        [InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("💬 إرسال رسالة لمستخدم", callback_data="list_users_msg")],
        [InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")],
        [InlineKeyboardButton("📝 المهام", callback_data="admin_tasks_menu")],
        [InlineKeyboardButton("🎁 إرسال هدية نقاط لمستخدم", callback_data="admin_send_gift_menu")],
        [InlineKeyboardButton("🎁 إرسال هدية نقاط للجميع", callback_data="gift_all")],
        [InlineKeyboardButton("💵 تغيير سعر الإعلان", callback_data="change_ad_price")],
        [InlineKeyboardButton("🚫 حظر مستخدم بالاسم", callback_data="ban_user")],
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

    # منيو المهام (للأدمن) - أصبح يدعم عدة مهام
    if data == "admin_tasks_menu":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مهمة جديدة", callback_data="task_add")],
            [InlineKeyboardButton("📋 عرض / تعديل المهام", callback_data="task_list")],
        ]
        await query.message.reply_text("📝 إدارة المهام (أدمن):", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # عرض قائمة المهام مع أزرار تعديل لكل مهمة لحالها
    if data == "task_list":
        tasks = get_all_tasks(active_only=False)
        if not tasks:
            await query.message.reply_text("لا يوجد مهام مضافة بعد. اضغط ➕ إضافة مهمة جديدة.")
            return
        for task_id, text, link, points, done_msg, channel_id, active in tasks:
            status = "🟢 مفعّلة" if active else "🔴 معطّلة"
            keyboard = [
                [InlineKeyboardButton("✏️ النص", callback_data=f"edittask_text_{task_id}"),
                 InlineKeyboardButton("🔗 الرابط", callback_data=f"edittask_link_{task_id}")],
                [InlineKeyboardButton("💰 النقاط", callback_data=f"edittask_points_{task_id}"),
                 InlineKeyboardButton("🎉 رسالة الإتمام", callback_data=f"edittask_done_{task_id}")],
                [InlineKeyboardButton("🔒 قناة التحقق", callback_data=f"edittask_channel_{task_id}")],
                [InlineKeyboardButton("⏸️ تفعيل/تعطيل", callback_data=f"task_toggle_{task_id}"),
                 InlineKeyboardButton("🗑 حذف", callback_data=f"task_delete_{task_id}")],
            ]
            await query.message.reply_text(
                f"📌 مهمة #{task_id} ({status})\n"
                f"النص: {text}\n"
                f"الرابط: {link}\n"
                f"النقاط: {points}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data.startswith("task_toggle_"):
        task_id = int(data.replace("task_toggle_", ""))
        toggle_task_active(task_id)
        await query.message.reply_text("✔ تم تحديث حالة المهمة (تفعيل/تعطيل)")
        return

    if data.startswith("task_delete_"):
        task_id = int(data.replace("task_delete_", ""))
        delete_task(task_id)
        await query.message.reply_text("🗑 تم حذف المهمة نهائياً")
        return

    # منيو إرسال الهدايا
    if data == "admin_send_gift_menu":
        await query.message.reply_text(
            "🎁 لإرسال هدية نقاط لمستخدم:\n"
            "ادخل إلى قائمة المستخدمين واختر زر \"🎁 إرسال هدية\" من عند المستخدم المطلوب."
        )
        return

    # قائمة المستخدمين للحظر بالزر + الهدايا
    if data == "ban_user_list":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, points FROM users ORDER BY points DESC")
        users = c.fetchall()
        conn.close()

        if not users:
            await query.message.reply_text("لا يوجد مستخدمين مسجلين.")
            return

        for uid, uname, pts in users:
            keyboard = [
                [InlineKeyboardButton("🚫 حظر هذا المستخدم", callback_data=f"ban_u_{uid}")],
                [InlineKeyboardButton("🎁 إرسال هدية نقاط", callback_data=f"gift_u_{uid}")]
            ]
            await query.message.reply_text(
                f"👤 {uname}\nID: {uid}\n💰 النقاط: {pts}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data.startswith("ban_u_"):
        uid = int(data.replace("ban_u_", ""))
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        await query.message.reply_text("🚫 تم حظر المستخدم بنجاح")
        return

    if data == "list_users":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, banned, points, referrals, verified FROM users ORDER BY points DESC")
        users = c.fetchall()
        conn.close()

        if not users:
            await query.message.reply_text("لا يوجد مستخدمين مسجلين.")
            return

        for uid, uname, banned, points, refs, ver in users:
            status = "محظور 🚫" if banned else "نشط ✅"
            vstatus = "مفعّل ✅" if ver else "غير مفعّل ❌"
            keyboard = [
                [InlineKeyboardButton("💬 إرسال رسالة", callback_data=f"msg_u_{uid}")],
                [InlineKeyboardButton("🚫 / ✅ حظر / إلغاء حظر", callback_data=f"toggleban_u_{uid}")],
                [InlineKeyboardButton("🎁 إرسال هدية نقاط", callback_data=f"gift_u_{uid}")]
            ]
            await query.message.reply_text(
                f"👤 {uname}\nID: {uid}\n"
                f"الحالة: {status}\n"
                f"التفعيل: {vstatus}\n"
                f"💰 النقاط: {points}\n"
                f"👥 الإحالات: {refs}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data == "list_users_msg":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, points FROM users ORDER BY points DESC")
        users = c.fetchall()
        conn.close()

        if not users:
            await query.message.reply_text("لا يوجد مستخدمين مسجلين.")
            return

        for uid, uname, pts in users:
            keyboard = [
                [InlineKeyboardButton("💬 إرسال رسالة لهذا المستخدم", callback_data=f"msg_u_{uid}")]
            ]
            await query.message.reply_text(
                f"👤 {uname}\nID: {uid}\n💰 النقاط: {pts}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data.startswith("toggleban_u_"):
        uid = int(data.replace("toggleban_u_", ""))
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT banned FROM users WHERE user_id = ?", (uid,))
        row = c.fetchone()
        if row is None:
            conn.close()
            await query.message.reply_text("المستخدم غير موجود.")
            return
        banned = row[0]
        new_status = 0 if banned else 1
        c.execute("UPDATE users SET banned = ? WHERE user_id = ?", (new_status, uid))
        conn.commit()
        conn.close()
        await query.message.reply_text("تم تحديث حالة المستخدم 🚫✅")
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
        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات البوت الفخم:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"✅ النشطون: {active}\n"
            f"🚫 المحظورون: {banned}\n"
            f"🔐 المفعّلون: {verified}\n"
            f"💰 إجمالي النقاط: {total_points}"
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

    # ==== موافقة / رفض الإعلانات ====
    if data.startswith("ad_approve_"):
        ad_id = int(data.replace("ad_approve_", ""))
        ad = get_ad(ad_id)
        if not ad:
            await query.message.reply_text("❌ الإعلان غير موجود.")
            return
        _, uid, uname, content, price_paid, status = ad
        if status != "pending":
            await query.message.reply_text("هذا الإعلان تمت معالجته مسبقاً.")
            return

        set_ad_status(ad_id, "approved")
        user_ids = get_all_active_user_ids()
        sent = 0
        for target_id in user_ids:
            try:
                await telegram_app.bot.send_message(target_id, f"📢 إعلان:\n\n{content}")
                sent += 1
            except Exception as e:
                logger.error(f"Error sending ad to {target_id}: {e}")

        try:
            await telegram_app.bot.send_message(uid, "✅ تم نشر إعلانك بنجاح!")
        except Exception as e:
            logger.error(f"Failed to notify ad owner {uid}: {e}")

        await query.message.reply_text(f"✔ تم نشر الإعلان #{ad_id} لعدد {sent} مستخدم")
        return

    if data.startswith("ad_reject_"):
        ad_id = int(data.replace("ad_reject_", ""))
        ad = get_ad(ad_id)
        if not ad:
            await query.message.reply_text("❌ الإعلان غير موجود.")
            return
        _, uid, uname, content, price_paid, status = ad
        if status != "pending":
            await query.message.reply_text("هذا الإعلان تمت معالجته مسبقاً.")
            return

        set_ad_status(ad_id, "rejected")
        if price_paid:
            add_points(uid, price_paid)

        try:
            await telegram_app.bot.send_message(
                uid,
                f"❌ تم رفض إعلانك.\n💰 تم إرجاع {price_paid} نقطة إلى رصيدك."
            )
        except Exception as e:
            logger.error(f"Failed to notify ad owner {uid}: {e}")

        await query.message.reply_text(f"🚫 تم رفض الإعلان #{ad_id} وإرجاع نقاط صاحبه")
        return


# ==================== حظر بالاسم ====================

async def ask_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الآن اسم المستخدم الذي تريد حظره:")
    return WAITING_BAN_NAME


async def process_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ?", (name,))
    result = c.fetchone()

    if not result:
        conn.close()
        await update.message.reply_text("❌ لم يتم العثور على مستخدم بهذا الاسم")
        return ConversationHandler.END

    uid = result[0]
    c.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🚫 تم حظر المستخدم {name} بنجاح")
    return ConversationHandler.END


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
        "عشان يصير التحقق حقيقي عبر تليجرام.\n\n"
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


# ==================== إضافة مهمة جديدة (خطوات متتالية) ====================

async def ask_task_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📝 أرسل نص المهمة الجديدة:")
    return WAITING_TASK_ADD_TEXT


async def task_add_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task_text"] = update.message.text
    await update.message.reply_text("🔗 الآن أرسل رابط المهمة:")
    return WAITING_TASK_ADD_LINK


async def task_add_got_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task_link"] = update.message.text.strip()
    await update.message.reply_text("💰 الآن أرسل عدد نقاط هذه المهمة:")
    return WAITING_TASK_ADD_POINTS


async def task_add_got_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pts = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح. أرسل عدد النقاط مجدداً:")
        return WAITING_TASK_ADD_POINTS
    context.user_data["new_task_points"] = pts
    await update.message.reply_text("🎉 الآن أرسل رسالة إتمام المهمة (تظهر للمستخدم بعد إنجازها):")
    return WAITING_TASK_ADD_DONE


async def task_add_got_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task_done"] = update.message.text
    await update.message.reply_text(
        "🔒 (اختياري) أرسل معرّف قناة/جروب للتحقق الحقيقي من إنجاز هذه المهمة "
        "(مثال: @my_channel) أو أرسل - لتجاوز هذه الخطوة (تحقق تلقائي بالوقت فقط):"
    )
    return WAITING_TASK_ADD_CHANNEL


async def task_add_got_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    channel_id = None if val in ("-", "الغاء", "إلغاء", "clear") else val

    add_task(
        context.user_data.get("new_task_text"),
        context.user_data.get("new_task_link"),
        context.user_data.get("new_task_points"),
        context.user_data.get("new_task_done"),
        channel_id
    )

    for k in ["new_task_text", "new_task_link", "new_task_points", "new_task_done"]:
        context.user_data.pop(k, None)

    await update.message.reply_text("✅ تمت إضافة المهمة الجديدة بنجاح!")
    return ConversationHandler.END


# ==================== تعديل حقل من حقول مهمة موجودة ====================

FIELD_LABELS = {
    "text": "نص المهمة",
    "link": "رابط المهمة",
    "points": "نقاط المهمة",
    "done_msg": "رسالة إتمام المهمة",
    "channel_id": "قناة التحقق (أرسل - لإلغائها)",
}


async def ask_edit_task_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # data format: edittask_<field>_<task_id>
    parts = query.data.split("_")
    field = parts[1]
    task_id = int(parts[2])
    context.user_data["edit_task_id"] = task_id
    context.user_data["edit_task_field"] = field
    label = FIELD_LABELS.get(field, field)
    await query.message.reply_text(f"✏️ أرسل القيمة الجديدة لـ «{label}» للمهمة #{task_id}:")
    return WAITING_TASK_EDIT_FIELD


async def save_edit_task_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.get("edit_task_id")
    field = context.user_data.get("edit_task_field")
    if not task_id or not field:
        await update.message.reply_text("حدث خطأ، حاول مجدداً من قائمة المهام.")
        return ConversationHandler.END

    value = update.message.text.strip()

    if field == "points":
        try:
            value = int(value)
        except ValueError:
            await update.message.reply_text("❌ الرجاء إرسال رقم صحيح لعدد النقاط.")
            return ConversationHandler.END

    if field == "channel_id":
        value = None if value in ("-", "الغاء", "إلغاء", "clear") else value

    update_task_field(task_id, field, value)

    context.user_data.pop("edit_task_id", None)
    context.user_data.pop("edit_task_field", None)

    await update.message.reply_text(f"✔ تم تحديث {FIELD_LABELS.get(field, field)} للمهمة #{task_id}")
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


# ==================== رسالة فردية ====================

async def ask_direct_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = int(update.callback_query.data.replace("msg_u_", ""))
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
    uid = int(query.data.replace("gift_u_", ""))
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


# ==================== تغيير سعر الإعلان ====================

async def ask_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    settings = get_settings()
    await update.callback_query.message.reply_text(
        f"💵 السعر الحالي للإعلان: {settings['ad_price']} نقطة\nأرسل السعر الجديد:"
    )
    return WAITING_AD_PRICE


async def save_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح للسعر.")
        return ConversationHandler.END

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET ad_price = ? WHERE id = 1", (price,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✔ تم تحديث سعر الإعلان: {price} نقطة")
    return ConversationHandler.END


# ==================== شراء إعلان (من المستخدم) ====================

async def ask_buy_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    settings = get_settings()
    price = settings["ad_price"]
    points, _, _ = get_user_info(user.id)

    if points < price:
        await query.message.reply_text(
            f"❌ رصيدك من النقاط غير كافٍ لشراء إعلان.\n"
            f"💵 سعر الإعلان: {price} نقطة\n"
            f"💰 رصيدك الحالي: {points} نقطة"
        )
        return ConversationHandler.END

    await query.message.reply_text(
        f"📢 سعر الإعلان: {price} نقطة (سيتم خصمها فوراً).\n"
        f"أرسل الآن نص/محتوى الإعلان الذي تريد نشره:"
    )
    return WAITING_AD_CONTENT


async def process_ad_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    content = update.message.text
    settings = get_settings()
    price = settings["ad_price"]

    points, _, _ = get_user_info(user.id)
    if points < price:
        await update.message.reply_text("❌ رصيدك أصبح غير كافٍ، حاول مرة أخرى لاحقاً.")
        return ConversationHandler.END

    deduct_points(user.id, price)
    ad_id = create_ad(user.id, user.username or user.full_name, content, price)

    await update.message.reply_text(
        f"✅ تم إرسال إعلانك للمراجعة من الإدارة.\n💰 تم خصم {price} نقطة من رصيدك.\n"
        f"سيتم إعلامك فور الموافقة أو الرفض."
    )

    try:
        keyboard = [
            [InlineKeyboardButton("✅ نشر", callback_data=f"ad_approve_{ad_id}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"ad_reject_{ad_id}")]
        ]
        await telegram_app.bot.send_message(
            ADMIN_ID,
            f"📢 طلب إعلان جديد #{ad_id}\n"
            f"من: {user.username or user.full_name} (ID: {user.id})\n"
            f"💵 السعر المدفوع: {price} نقطة\n\n"
            f"المحتوى:\n{content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about new ad: {e}")

    return ConversationHandler.END


# ==================== استقبال الصور ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1].file_id

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

    if data == "user_tasks_menu":
        tasks = get_all_tasks(active_only=True)
        if not tasks:
            await query.message.reply_text("📝 لا يوجد مهام متاحة حالياً، تابعنا قريباً!")
            return

        for task_id, text, link, task_points, done_msg, channel_id, active in tasks:
            progress = get_task_progress(user.id, task_id)
            if progress and progress[1]:  # completed_at موجود
                continue  # المستخدم خلص هاي المهمة، لا تعرضها مجدداً

            keyboard = [
                [InlineKeyboardButton("🔗 فتح رابط المهمة", url=link)],
                [InlineKeyboardButton("✔ أنجزت المهمة", callback_data=f"task_done_{task_id}")]
            ]
            set_task_progress_sent(user.id, task_id)
            await query.message.reply_text(
                f"📝 مهمة #{task_id}:\n\n{text}\n\n"
                f"💰 نقاط المهمة: {task_points}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data.startswith("task_done_"):
        task_id = int(data.replace("task_done_", ""))
        task = get_task(task_id)
        if not task or not task[6]:  # غير موجودة أو معطّلة
            await query.message.reply_text("❌ هذه المهمة لم تعد متاحة.")
            return

        _, text, link, task_points, done_msg, channel_id, active = task

        progress = get_task_progress(user.id, task_id)
        if progress and progress[1]:
            await query.message.reply_text("✅ لقد أنجزت هذه المهمة من قبل.")
            return

        if channel_id:
            ok = await is_real_member(channel_id, user.id)
            if not ok:
                await query.message.reply_text("❌ لسا ما اشتركت/أنجزت المهمة، جرب كمان مرة بعد ما تخلّصها.")
                return
        else:
            sent_at = progress[0] if progress else None
            elapsed = seconds_since(sent_at)
            if elapsed is None or elapsed < MIN_WAIT_SECONDS:
                await query.message.reply_text("⏳ تأكد إنك فتحت الرابط وأنجزت المهمة، وحاول بعد كم ثانية.")
                return

        add_points(user.id, task_points)
        set_task_completed(user.id, task_id)
        await query.message.reply_text(done_msg)
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
    entry_points=[CallbackQueryHandler(ask_direct_msg, pattern="^msg_u_")],
    states={WAITING_DIRECT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_direct_message)]},
    fallbacks=[]
)

ban_user_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_ban_user, pattern="^ban_user$")],
    states={WAITING_BAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ban_user)]},
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
    entry_points=[CallbackQueryHandler(ask_gift_points, pattern="^gift_u_")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)

gift_all_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_gift_all_points, pattern="^gift_all$")],
    states={WAITING_GIFT_ALL_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_all_points)]},
    fallbacks=[]
)

ad_price_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_ad_price, pattern="^change_ad_price$")],
    states={WAITING_AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ad_price)]},
    fallbacks=[]
)

buy_ad_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_buy_ad, pattern="^buy_ad$")],
    states={WAITING_AD_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ad_content)]},
    fallbacks=[]
)

task_add_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_add_text, pattern="^task_add$")],
    states={
        WAITING_TASK_ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_got_text)],
        WAITING_TASK_ADD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_got_link)],
        WAITING_TASK_ADD_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_got_points)],
        WAITING_TASK_ADD_DONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_got_done)],
        WAITING_TASK_ADD_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_got_channel)],
    },
    fallbacks=[]
)

task_edit_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_edit_task_field, pattern="^edittask_(text|link|points|done|channel)_")],
    states={WAITING_TASK_EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edit_task_field)]},
    fallbacks=[]
)

# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(
    CallbackQueryHandler(
        admin_navigation_click,
        pattern="^(admin_messages_menu|admin_tasks_menu|admin_send_gift_menu|list_users|list_users_msg|"
                "toggleban_u_.*|show_stats|ban_user_list|ban_u_.*|admin_top20|task_list|"
                "task_toggle_.*|task_delete_.*|ad_approve_.*|ad_reject_.*)$"
    )
)
telegram_app.add_handler(
    CallbackQueryHandler(
        user_navigation_click,
        pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_open_verify_link|"
                "user_confirm_verify|user_daily_gift|user_tasks_menu|task_done_.*)$"
    )
)

telegram_app.add_handler(welcome_conv)
telegram_app.add_handler(after_photo_conv)
telegram_app.add_handler(broadcast_conv)
telegram_app.add_handler(direct_msg_conv)
telegram_app.add_handler(ban_user_conv)
telegram_app.add_handler(verify_link_conv)
telegram_app.add_handler(verify_fail_msg_conv)
telegram_app.add_handler(first_sub_msg_conv)
telegram_app.add_handler(daily_gift_points_conv)
telegram_app.add_handler(verify_channel_conv)
telegram_app.add_handler(gift_points_conv)
telegram_app.add_handler(gift_all_conv)
telegram_app.add_handler(ad_price_conv)
telegram_app.add_handler(buy_ad_conv)
telegram_app.add_handler(task_add_conv)
telegram_app.add_handler(task_edit_conv)

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
