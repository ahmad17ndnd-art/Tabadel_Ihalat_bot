import os
import logging
import sqlite3
from datetime import datetime

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
            ref_by INTEGER DEFAULT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT 'أهلاً بك 👋',
            after_photo_msg TEXT DEFAULT 'تم استلام الصورة بنجاح 📸'
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

# ==================== دوال مساعدة ====================
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

def set_verified(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_referral(ref_by: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_by,))
    conn.commit()
    conn.close()

# ==================== واجهة المستخدم ====================
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    points, referrals, verified = get_user_info(user.id)

    status = "✅ مفعّل" if verified else "❌ غير مفعّل"
    level = "مبتدئ"
    if points >= 500:
        level = "نشط"
    if points >= 1000:
        level = "VIP"
    if points >= 2000:
        level = "Super VIP"

    text = (
        f"👋 أهلاً {user.username or user.full_name}\n\n"
        f"💰 نقاطك: {points}\n"
        f"👥 إحالاتك: {referrals}\n"
        f"🔐 حالة الحساب: {status}\n"
        f"🏆 مستواك: {level}\n\n"
        f"اختر من القائمة التالية:"
    )

    keyboard = [
        [InlineKeyboardButton("🔐 تفعيل الحساب", callback_data="user_activate")],
        [InlineKeyboardButton("💰 نقاطي", callback_data="user_points")],
        [InlineKeyboardButton("👥 إحالاتي", callback_data="user_referrals")],
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="user_stats")],
        [InlineKeyboardButton("🏆 أفضل المستخدمين", callback_data="user_top")],
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== ترحيب /start مع إحالات ====================
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

    # إذا جاء من إحالة
    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)  # نقاط لصاحب الإحالة

    c.execute("SELECT welcome_msg FROM settings WHERE id = 1")
    msg = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(msg)
    await send_main_menu(update, context)

    # إشعار فوري للأدمن عند دخول مستخدم جديد
    try:
        if user.id != ADMIN_ID:
            await telegram_app.bot.send_message(
                ADMIN_ID,
                f"👤 مستخدم جديد دخل:\nالاسم: {user.username or user.full_name}\nID: {user.id}"
            )
    except Exception as e:
        logger.error(f"Failed to notify admin about new user: {e})

# ==================== لوحة الإدارة ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="show_stats")],
        [InlineKeyboardButton("📢 إرسال رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("💬 إرسال رسالة لمستخدم", callback_data="list_users")],
        [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
        [InlineKeyboardButton("📸 تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
        [InlineKeyboardButton("🚫 حظر مستخدم بالاسم", callback_data="ban_user_list")],
    ]

    await update.message.reply_text("لوحة الإدارة:", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== إدارة المستخدمين ====================
async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "ban_user_list":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username FROM users ORDER BY joined_at DESC")
        users = c.fetchall()
        conn.close()

        if not users:
            await query.message.reply_text("لا يوجد مستخدمين مسجلين.")
            return

        for uid, uname in users:
            keyboard = [
                [InlineKeyboardButton("🚫 حظر هذا المستخدم", callback_data=f"ban_u_{uid}")]
            ]
            await query.message.reply_text(
                f"الاسم: {uname}\nID: {uid}",
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
        c.execute("SELECT user_id, username, banned, points, referrals, verified FROM users ORDER BY joined_at DESC")
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
                [InlineKeyboardButton("🚫 / ✅ حظر / إلغاء حظر", callback_data=f"toggleban_u_{uid}")]
            ]
            await query.message.reply_text(
                f"الاسم: {uname}\nID: {uid}\nالحالة: {status}\nالتفعيل: {vstatus}\nالنقاط: {points}\nالإحالات: {refs}",
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
            f"📊 الإحصائيات التفصيلية:\n\n"
            f"إجمالي المستخدمين: {total}\n"
            f"النشطون ✅: {active}\n"
            f"المحظورون 🚫: {banned}\n"
            f"المفعّلون 🔐: {verified}\n"
            f"إجمالي النقاط 💰: {total_points}"
        )
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
        await update.message.reply_text("❌ لم يتم العثور على مستخدم بهذا الاسم")
        return ConversationHandler.END

    uid = result[0]

    c.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🚫 تم حظر المستخدم {name} بنجاح")
    return ConversationHandler.END

# ==================== تعديل الرسالة الترحيبية ====================
async def ask_welcome_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الرسالة الترحيبية الجديدة:")
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

# ==================== تعديل رسالة بعد الصورة ====================
async def ask_after_photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الرسالة التي تظهر بعد الصورة:")
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

# ==================== بث رسائل جماعية ====================
async def ask_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة الجماعية:")
    return WAITING_BROADCAST_MSG

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned = 0")
    users = c.fetchall()
    conn.close()

    sent = 0
    for u in users:
        try:
            await telegram_app.bot.send_message(u[0], msg)
            sent += 1
        except Exception as e:
            logger.error(f"Error sending to {u[0]}: {e}")

    await update.message.reply_text(f"تم إرسال الرسالة إلى {sent} مستخدم ✔")
    return ConversationHandler.END

# ==================== رسالة فردية ====================
async def ask_direct_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = int(update.callback_query.data.replace("msg_u_", ""))
    context.user_data["target_user"] = user_id
    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة:")
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

# ==================== استقبال الصور ====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1].file_id

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT after_photo_msg FROM settings WHERE id = 1")
    msg = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(msg)

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

    if data == "user_points":
        level = "مبتدئ"
        if points >= 500:
            level = "نشط"
        if points >= 1000:
            level = "VIP"
        if points >= 2000:
            level = "Super VIP"

        await query.message.reply_text(
            f"💰 نقاطك الحالية: {points}\n"
            f"👥 إحالاتك: {referrals}\n"
            f"🏆 مستواك: {level}"
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
        c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()

        if not rows:
            await query.message.reply_text("لا يوجد بيانات كافية لعرض أفضل المستخدمين.")
            return

        text = "🏆 أفضل المستخدمين بالنقاط:\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (uname, pts) in enumerate(rows):
            medal = medals[i] if i < len(medals) else "🔹"
            text += f"{medal} {uname} — {pts} نقطة\n"

        await query.message.reply_text(text)
        return

    if data == "user_activate":
        if verified:
            await query.message.reply_text("🔐 حسابك مفعّل بالفعل ✅")
            return

        set_verified(user.id)
        add_points(user.id, 200)
        await query.message.reply_text(
            "🔐 تم تفعيل حسابك بنجاح ✅\n"
            "💰 حصلت على 200 نقطة كمكافأة!"
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
    entry_points=[CallbackQueryHandler(ask_direct_msg, pattern="^msg_u_")],
    states={WAITING_DIRECT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_direct_message)]},
    fallbacks=[]
)

ban_user_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_ban_user, pattern="^ban_user$")],
    states={WAITING_BAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ban_user)]},
    fallbacks=[]
)

# ==================== تسجيل الهاندلرز ====================
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(CallbackQueryHandler(admin_navigation_click, pattern="^(list_users|toggleban_u_.*|show_stats|ban_user_list|ban_u_.*)$"))
telegram_app.add_handler(CallbackQueryHandler(user_navigation_click, pattern="^(user_points|user_referrals|user_stats|user_top|user_activate)$"))

telegram_app.add_handler(welcome_conv)
telegram_app.add_handler(after_photo_conv)
telegram_app.add_handler(broadcast_conv)
telegram_app.add_handler(direct_msg_conv)
telegram_app.add_handler(ban_user_conv)

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
