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

# ==================== إعداد التسجيل ====================
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
            joined_at TEXT
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

# ==================== /start ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, joined_at) VALUES (?, ?, ?)",
              (user.id, user.username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    c.execute("SELECT welcome_msg FROM settings WHERE id = 1")
    msg = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(msg)

    # إشعار للأدمن عند دخول مستخدم جديد
    await telegram_app.bot.send_message(
        ADMIN_ID,
        f"👤 مستخدم جديد انضم:\nالاسم: {user.username}\nID: {user.id}"
    )

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

# ==================== إدارة المستخدمين + الحظر ====================
async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "list_users" or data == "ban_user_list":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, banned FROM users ORDER BY joined_at DESC")
        users = c.fetchall()
        conn.close()

        for u in users:
            uid, uname, banned = u
            status = "محظور 🚫" if banned else "نشط ✅"
            if data == "list_users":
                keyboard = [
                    [InlineKeyboardButton("💬 إرسال رسالة", callback_data=f"msg_u_{uid}")],
                    [InlineKeyboardButton("🚫 / ✅ حظر / إلغاء حظر", callback_data=f"toggleban_u_{uid}")]
                ]
            else:
                keyboard = [
                    [InlineKeyboardButton("🚫 حظر هذا المستخدم", callback_data=f"ban_u_{uid}")]
                ]
            await query.message.reply_text(
                f"الاسم: {uname}\nID: {uid}\nالحالة: {status}",
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
        await query.message.reply_text("تم تحديث حالة المستخدم 🚫✅")

    elif data.startswith("ban_u_"):
        uid = int(data.replace("ban_u_", ""))
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        await query.message.reply_text("🚫 تم حظر المستخدم بنجاح")

    elif data == "show_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE banned = 0")
        active = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
        banned = c.fetchone()[0]
        conn.close()
        await query.message.reply_text(
            f"📊 الإحصائيات:\nإجمالي المستخدمين: {total}\nالنشطون ✅: {active}\nالمحظورون 🚫: {banned}"
        )

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
    await update.message.reply_text("تم حفظ الرسالة الترحيبية ✔")
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
    user = update.effective_user
    text = update.message.text
    await telegram_app.bot.send_message(
        ADMIN_ID,
        f"📩 رسالة جديدة من {user.username}:\n{text}"
    )
    await update.message.reply_text("تم استلام رسالتك ✅")

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

    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("RAILWAY_STATIC_URL")
    if railway_url:
        webhook_url = f"https://{railway_url}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set successfully to: {webhook_url}")
    else:
        logger.warning("Railway URL not found in environment variables. Please set webhook manually if needed.")

@app.on_event("shutdown")
