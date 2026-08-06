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
    c.execute(
        """
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               first_sub_msg, task_text, task_link, task_points,
               task_done_msg, daily_gift_points
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

    keyboard = [
        [InlineKeyboardButton("🔗 فتح الرابط", url=settings["verify_link"])],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="user_confirm_verify")]
    ]

    await update.message.reply_text(
        settings["welcome_msg"],
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    try:
        if user.id != ADMIN_ID:
            await telegram_app.bot.send_message(
                ADMIN_ID,
                f"👤 مستخدم جديد دخل:\nالاسم: {user.username or user.full_name}\nID: {user.id}"
            )
    except Exception as e:
        logger.error(f"Failed to notify admin about new user: {e}")


# ==================== زر التحقق من الاشتراك ====================

async def user_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    await query.answer()

    points, referrals, verified = get_user_info(user.id)
    settings = get_settings()

    if data == "user_confirm_verify":
        if verified:
            await query.message.reply_text(settings["welcome_msg"])
            await send_main_menu(update, context)
            return

        clicked = get_clicked_verify_link(user.id)
        if not clicked:
            await query.message.reply_text(settings["verify_fail_msg"])
            return

        set_verified(user.id)
        add_points(user.id, 200)

        await query.message.reply_text(settings["welcome_msg"])
        await send_main_menu(update, context)
        return

    # باقي دوال المستخدم تبقى كما هي بدون تعديل
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
        txt = (
            f"🔐 حالة حسابك الحالية: {status}\n\n"
            f"لتفعيل الحساب:\n"
            f"1️⃣ اضغط زر \"🔗 فتح رابط التفعيل\".\n"
            f"2️⃣ افتح الرابط.\n"
            f"3️⃣ ارجع للبوت واضغط زر \"✅ أنا فعلت الحساب\".\n"
        )
        keyboard = [
            [InlineKeyboardButton("🔗 فتح رابط التفعيل", url=settings["verify_link"])],
            [InlineKeyboardButton("✅ أنا فعلت الحساب", callback_data="user_confirm_verify")],
        ]
        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "user_daily_gift":
        settings = get_settings()
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
                        f"⏳ يمكنك المحاولة بعد {hours} ساعة و {minutes} دقيقة."
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
        settings = get_settings()
        keyboard = [
            [InlineKeyboardButton("🔗 فتح رابط المهمة", url=settings["task_link"])],
            [InlineKeyboardButton("✔ أنجزت المهمة", callback_data="user_task_done")]
        ]
        await query.message.reply_text(
            f"📝 المهمة الحالية:\n\n{settings['task_text']}\n\n"
            f"💰 نقاط المهمة: {settings['task_points']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "user_task_done":
        settings = get_settings()
        add_points(user.id, settings["task_points"])
        await query.message.reply_text(settings["task_done_msg"])
        return
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


# ==================== لوحة الإدارة (Callback) ====================

async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "admin_messages_menu":
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل رسالة الترحيب", callback_data="change_welcome")],
            [InlineKeyboardButton("📸 تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
            [InlineKeyboardButton("🔗 تعديل رابط الدخول", callback_data="change_verify_link")],
            [InlineKeyboardButton("⚠️ تعديل رسالة فشل التفعيل", callback_data="change_verify_fail_msg")],
            [InlineKeyboardButton("📩 تعديل الرسالة الإجباريّة", callback_data="change_first_sub_msg")],
            [InlineKeyboardButton("🎁 تعديل نقاط الهدية اليومية", callback_data="change_daily_gift_points")],
        ]
        await query.message.reply_text(
            "اختر الرسالة / الإعداد الذي تريد تعديله:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # باقي لوحة الإدارة كما هي بدون تعديل
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

task_text_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_text, pattern="^task_edit_text$")],
    states={WAITING_TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_text)]},
    fallbacks=[]
)

task_link_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_link, pattern="^task_edit_link$")],
    states={WAITING_TASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_link)]},
    fallbacks=[]
)

task_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_points, pattern="^task_edit_points$")],
    states={WAITING_TASK_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_points)]},
    fallbacks=[]
)

task_done_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_done, pattern="^task_edit_done$")],
    states={WAITING_TASK_DONE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_done)]},
    fallbacks=[]
)

gift_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(lambda u, c: None, pattern="^gift_u_")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)
# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(
    CallbackQueryHandler(
        admin_navigation_click,
        pattern="^(admin_messages_menu|admin_tasks_menu|admin_send_gift_menu|list_users|list_users_msg|toggleban_u_.*|show_stats|ban_user_list|ban_u_.*|admin_top20|gift_u_.*)$"
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(
        user_navigation_click,
        pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_confirm_verify|user_daily_gift|user_tasks_menu|user_task_done)$"
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
telegram_app.add_handler(task_text_conv)
telegram_app.add_handler(task_link_conv)
telegram_app.add_handler(task_points_conv)
telegram_app.add_handler(task_done_conv)

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
# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(
    CallbackQueryHandler(
        admin_navigation_click,
        pattern="^(admin_messages_menu|admin_tasks_menu|admin_send_gift_menu|list_users|list_users_msg|toggleban_u_.*|show_stats|ban_user_list|ban_u_.*|admin_top20|gift_u_.*)$"
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(
        user_navigation_click,
        pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_confirm_verify|user_daily_gift|user_tasks_menu|user_task_done)$"
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
telegram_app.add_handler(task_text_conv)
telegram_app.add_handler(task_link_conv)
telegram_app.add_handler(task_points_conv)
telegram_app.add_handler(task_done_conv)

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
