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
BOT_TOKEN = "8397243265:AAE4YmfFO--0bjx_ATwWirFu_djos9iuoOI"
ADMIN_ID = 1922499737

telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
app = FastAPI()

# ==================== قاعدة البيانات ====================
DB_NAME = "bot_data.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

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
            last_gift_at TEXT DEFAULT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT '👋 أهلاً بك في بوت النقاط والإحالات الفخم!',
            after_photo_msg TEXT DEFAULT '📸 تم استلام الصورة بنجاح، شكراً لمشاركتك!',
            verify_link TEXT DEFAULT 'https://t.me/ATF_AIRDROP_bot',
            verify_fail_msg TEXT DEFAULT '❌ لم يتم التفعيل.\nيرجى فتح رابط التفعيل أولاً ثم الضغط على زر "✅ أنا فعلت الحساب".',
            first_sub_msg TEXT DEFAULT 'عذراً عزيزي عليك الاشتراك بالبوت التالي',
            task_text TEXT DEFAULT '📌 هذه هي المهمة الحالية، قم بتنفيذها لتحصل على نقاط.',
            task_link TEXT DEFAULT 'https://t.me/example_task',
            task_points INTEGER DEFAULT 100,
            task_done_msg TEXT DEFAULT '🎉 تم إكمال المهمة بنجاح! تم إضافة النقاط إلى حسابك.',
            daily_gift_points INTEGER DEFAULT 50
        )
    """)

    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    conn.commit()
    conn.close()


# ==================== حالات المحادثة ====================
WAITING_WELCOME_MSG = 1
WAITING_AFTER_PHOTO_MSG = 2
WAITING_BROADCAST_MSG = 3
WAITING_DIRECT_TEXT = 4
WAITING_BAN_NAME = 5
WAITING_VERIFY_LINK = 6
WAITING_VERIFY_FAIL_MSG = 7
WAITING_FIRST_SUB_MSG = 8
WAITING_TASK_TEXT = 9
WAITING_TASK_LINK = 10
WAITING_TASK_POINTS = 11
WAITING_TASK_DONE_MSG = 12
WAITING_GIFT_POINTS = 13
WAITING_DAILY_GIFT_POINTS = 14

# ==================== دوال مساعدة ====================

def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               first_sub_msg, task_text, task_link, task_points,
               task_done_msg, daily_gift_points
        FROM settings WHERE id = 1
    """)
    row = c.fetchone()
    conn.close()
    return {
        "welcome_msg": row[0],
        "after_photo_msg": row[1],
        "verify_link": row[2],
        "verify_fail_msg": row[3],
        "first_sub_msg": row[4],
        "task_text": row[5],
        "task_link": row[6],
        "task_points": row[7],
        "task_done_msg": row[8],
        "daily_gift_points": row[9],
    }


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


def set_clicked_verify_link(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET clicked_verify_link = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_clicked_verify_link(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT clicked_verify_link FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


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
    ]

    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="show_stats")])
        keyboard.append([InlineKeyboardButton("📋 نقاط كل مستخدم", callback_data="list_users")])
        keyboard.append([InlineKeyboardButton("🏆 أفضل 20 (أدمن)", callback_data="admin_top20")])
        keyboard.append([InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")])
        keyboard.append([InlineKeyboardButton("📝 المهام", callback_data="admin_tasks_menu")])
        keyboard.append([InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")])
        keyboard.append([InlineKeyboardButton("💬 إرسال رسالة لمستخدم", callback_data="list_users_msg")])
        keyboard.append([InlineKeyboardButton("🎁 إرسال هدية نقاط", callback_data="admin_send_gift_menu")])
        keyboard.append([InlineKeyboardButton("🚫 إدارة الحظر", callback_data="ban_user_list")])
        keyboard.append([InlineKeyboardButton("🔗 تعديل رابط البداية", callback_data="change_start_link")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


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

    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)

    settings = get_settings()
    clicked = get_clicked_verify_link(user.id)

    if not clicked:
        keyboard = [
            [InlineKeyboardButton("🔗 فتح البوت", url=settings["verify_link"])],
            [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="user_confirm_verify")]
        ]
        await update.message.reply_text(
            "👋 أهلاً بك!\n\n"
            "قبل الدخول إلى البوت، افتح الرابط ثم اضغط على زر التحقق.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await send_main_menu(update, context)


# ==================== دالة تعديل رابط البداية ====================

async def ask_start_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رابط البداية الجديد:")
    return WAITING_VERIFY_LINK


async def save_start_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET verify_link = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✔ تم حفظ رابط البداية الجديد بنجاح")
    return ConversationHandler.END


# ==================== دالة الهدايا ====================

async def ask_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = int(update.callback_query.data.replace("gift_u_", ""))
    context.user_data["gift_target"] = target
    await update.callback_query.message.reply_text("🎁 أرسل عدد النقاط التي تريد منحها:")
    return WAITING_GIFT_POINTS


async def process_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        points = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح.")
        return WAITING_GIFT_POINTS

    target = context.user_data.get("gift_target")
    if not target:
        await update.message.reply_text("❌ خطأ داخلي، أعد المحاولة.")
        return ConversationHandler.END

    add_points(target, points)
    await update.message.reply_text(f"✔ تم إرسال {points} نقطة للمستخدم بنجاح.")
    return ConversationHandler.END


gift_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_gift_points, pattern="^gift_u_")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)
telegram_app.add_handler(gift_points_conv)


# ==================== تشغيل البوت على Railway ====================

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
