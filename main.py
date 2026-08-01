import os
import logging
import sqlite3
import threading
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
            banned INTEGER DEFAULT 0
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

# ==================== دالة /start ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user.id, user.username))
    conn.commit()

    c.execute("SELECT welcome_msg FROM settings WHERE id = 1")
    msg = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(msg)

# ==================== لوحة الإدارة ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("إرسال رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("إرسال رسالة لمستخدم", callback_data="list_users")],
        [InlineKeyboardButton("تعديل الرسالة الترحيبية", callback_data="change_welcome")],
        [InlineKeyboardButton("تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
    ]

    await update.message.reply_text("لوحة الإدارة:", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== إدارة المستخدمين ====================
async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "list_users":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, banned FROM users")
        users = c.fetchall()
        conn.close()

        for u in users:
            uid, uname, banned = u
            status = "محظور" if banned else "نشط"

            keyboard = [
                [InlineKeyboardButton("إرسال رسالة", callback_data=f"msg_u_{uid}")],
                [InlineKeyboardButton("حظر / إلغاء حظر", callback_data=f"toggleban_u_{uid}")]
            ]

            await query.message.reply_text(
                f"المستخدم: {uname}\nID: {uid}\nالحالة: {status}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data.startswith("toggleban_u_"):
        uid = int(data.replace("toggleban_u_", ""))

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT banned FROM users WHERE user_id = ?", (uid,))
        banned = c.fetchone()[0]

        new_status = 0 if banned else 1
        c.execute("UPDATE users SET banned = ? WHERE user_id = ?", (new_status, uid))
        conn.commit()
        conn.close()

        await query.message.reply_text("تم تحديث حالة المستخدم.")

# ==================== الرسالة الترحيبية ====================
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

    await update.message.reply_text("تم حفظ الرسالة الترحيبية بنجاح ✔")
    return ConversationHandler.END

# ==================== رسالة بعد الصورة ====================
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

# ==================== الرسالة الجماعية ====================
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

# ==================== الرسالة الفردية ====================
async def ask_direct_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    user_id = int(update.callback_query.data.replace("msg_u_", ""))
    context.user_data["target_user"] = user_id

    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة:")
    return WAITING_DIRECT_TEXT

async def process_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    target = context.user_data["target_user"]

    try:
        await telegram_app.bot.send_message(target, msg)
        await update.message.reply_text("تم إرسال الرسالة ✔")
    except Exception as e:
        await update.message.reply_text(f"خطأ أثناء الإرسال: {e}")

    return ConversationHandler.END

# ==================== استقبال الصور ====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT after_photo_msg FROM settings WHERE id = 1")
    msg = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(msg)

# ==================== استقبال النصوص ====================
async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"استلمت رسالتك: {update.message.text}")

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

# ==================== تسجيل الهاندلرز ====================
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(CallbackQueryHandler(admin_navigation_click, pattern="^(list_users|toggleban_u_.*)$"))

telegram_app.add_handler(welcome_conv)
telegram_app.add_handler(after_photo_conv)
telegram_app.add_handler(broadcast_conv)
telegram_app.add_handler(direct_msg_conv)

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

# ==================== تشغيل البوت ====================
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

@app.on_event("shutdown")
async def shutdown_event():
    await telegram_app.stop()
    await telegram_app.shutdown()
