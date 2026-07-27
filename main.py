import os
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# إعداد التسجيل للمساعدة في إيجاد الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==================== إعدادات سيرفر الويب (لترضية Railway) ====================
web_app = FastAPI()

@web_app.get("/")
def home():
    return {"status": "Telegram Bot is running smoothly!"}

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(web_app, host="0.0.0.0", port=port)

# ==================== الإعدادات الرئيسية ====================
BOT_TOKEN = "8397243265:AAE4YmfFO--0bjx_ATwWirFu_djos9iuoOI"
ADMIN_ID = 1922499737

# ==================== إدارة قاعدة البيانات (SQLite) ====================
DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            joined_at TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            bot_blocked INTEGER DEFAULT 0
        )
    ''')
    # جدول إعدادات البوت
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # رسائل افتراضية
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_msg', 'أهلاً بك في بوت تبادل الإحالات! يرجى إرسال صورة التأكيد الآن.')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('after_photo_msg', 'تم استلام الصورة بنجاح! الآن يرجى إرسال رابط الإحالة الخاص بك.')")
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""

def set_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_or_update_user(user_id, full_name, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    is_new = cursor.fetchone() is None
    if is_new:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO users (user_id, full_name, username, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, full_name, username, now)
        )
    else:
        cursor.execute("UPDATE users SET full_name = ?, username = ? WHERE user_id = ?", (full_name, username, user_id))
    conn.commit()
    conn.close()
    return is_new

def is_user_banned(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row and row[0] == 1

def toggle_ban_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    new_status = 1 if (row and row[0] == 0) else 0
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, user_id))
    conn.commit()
    conn.close()
    return new_status == 1

def mark_bot_blocked(user_id, is_blocked):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET bot_blocked = ? WHERE user_id = ?", (1 if is_blocked else 0, user_id))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d 00:00:00")
    week_str = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_str = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (today_str,))
    today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (week_str,))
    week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (month_str,))
    month = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE bot_blocked = 1")
    blocked_bot = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned_by_admin = cursor.fetchone()[0]

    conn.close()
    return {
        "total": total,
        "today": today,
        "week": week,
        "month": month,
        "blocked_bot": blocked_bot,
        "banned_by_admin": banned_by_admin
    }

def get_recent_users(limit=15):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, is_banned FROM users WHERE user_id != ? ORDER BY joined_at DESC LIMIT ?", (ADMIN_ID, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_active_user_ids():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# حالات إدخال البيانات في لوحة التحكم والمحادثات
WAITING_WELCOME_MSG = 1
WAITING_AFTER_PHOTO_MSG = 2
WAITING_BROADCAST_MSG = 3
WAITING_DIRECT_TEXT = 4


# ==================== استقبال المستخدمين والعمليات الأساسية ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if is_user_banned(user.id):
        return

    is_new = add_or_update_user(user.id, user.full_name, user.username or "")
    mark_bot_blocked(user.id, False)

    if is_new and user.id != ADMIN_ID:
        admin_notice = (
            f"👤 **عضو جديد انضم للبوت!**\n\n"
            f"▪️ الاسم: {user.full_name}\n"
            f"▪️ اليوزر: @{user.username if user.username else 'لا يوجد'}\n"
            f"▪️ الآيدي: `{user.id}`"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_notice, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to notify admin: {e}")

    await update.message.reply_text(get_setting("welcome_msg"))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        return

    photo_file = update.message.photo[-1].file_id

    caption = f"📸 **وصلتك صورة تأكيد من:**\nالاسم: {user.full_name}\nالآيدي: `{user.id}`"
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file, caption=caption, parse_mode="Markdown")

    await update.message.reply_text(get_setting("after_photo_msg"))


async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        return

    text = update.message.text

    if user.id == ADMIN_ID:
        return

    msg_to_admin = (
        f"🔗 **وصلك رابط / نص جديد من:**\n"
        f"الاسم: {user.full_name}\n"
        f"الآيدي: `{user.id}`\n\n"
        f"الرسالة:\n{text}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=msg_to_admin, parse_mode="Markdown")
    await update.message.reply_text("✅ تم استلام رابطك/رسالتك وإرسالها للإدارة.")


# ==================== لوحة التحكم للأدمن (Admin Panel) ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    stats = get_stats()
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="show_stats")],
        [InlineKeyboardButton("👥 قائمة المستخدمين والتحكم", callback_data="list_users")],
        [InlineKeyboardButton("📢 إرسال رسالة جماعية (إذاعة)", callback_data="broadcast")],
        [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
        [InlineKeyboardButton("✏️ تعديل رسالة ما بعد الصورة", callback_data="change_after_photo")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🛠 **لوحة تحكم الأدمن الرئيسي**\n\n"
        f"👥 **إجمالي المشتركين:** `{stats['total']}`\n"
        f"📅 **اليوم:** `{stats['today']}` | 🗓 **الأسبوع:** `{stats['week']}`",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "show_stats":
        s = get_stats()
        text = (
            f"📊 **إحصائيات البوت التفصيلية:**\n\n"
            f"▪️ المنضمون اليوم: `{s['today']}`\n"
            f"▪️ المنضمون هذا الأسبوع: `{s['week']}`\n"
            f"▪️ المنضمون هذا الشهر: `{s['month']}`\n"
            f"▪️ إجمالي المستخدمين في البوت: `{s['total']}`\n\n"
            f"🚫 قاموا بحظر البوت (Stopped): `{s['blocked_bot']}`\n"
            f"🔨 المحظورون بواسطة الأدمن: `{s['banned_by_admin']}`"
        )
        keyboard = [[InlineKeyboardButton("🔙 العودة للوحة الرئيسية", callback_data="main_admin_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    elif query.data == "main_admin_menu":
        stats = get_stats()
        keyboard = [
            [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="show_stats")],
            [InlineKeyboardButton("👥 قائمة المستخدمين والتحكم", callback_data="list_users")],
            [InlineKeyboardButton("📢 إرسال رسالة جماعية (إذاعة)", callback_data="broadcast")],
            [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
            [InlineKeyboardButton("✏️ تعديل رسالة ما بعد الصورة", callback_data="change_after_photo")]
        ]
        await query.edit_message_text(
            f"🛠 **لوحة تحكم الأدمن الرئيسي**\n\n"
            f"👥 **إجمالي المشتركين:** `{stats['total']}`\n"
            f"📅 **اليوم:** `{stats['today']}` | 🗓 **الأسبوع:** `{stats['week']}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    elif query.data == "change_welcome":
        await query.edit_message_text("أرسل الآن **الرسالة الترحيبية الجديدة**:")
        return WAITING_WELCOME_MSG

    elif query.data == "change_after_photo":
        await query.edit_message_text("أرسل الآن **الرسالة الجديدة التي تظهر بعد إرسال الصورة**:")
        return WAITING_AFTER_PHOTO_MSG

    elif query.data == "broadcast":
        await query.edit_message_text("أرسل **الرسالة الجماعية** التي ترغب بإرسالها لكافة المستخدمين:")
        return WAITING_BROADCAST_MSG

    elif query.data == "list_users":
        users = get_recent_users(15)
        if not users:
            await query.edit_message_text("❌ لا يوجد مستخدمين مسجلين بعد.")
            return ConversationHandler.END

        keyboard = []
        for u_id, u_name, banned in users:
            status = "🔴 (محظور)" if banned else "🟢"
            keyboard.append([InlineKeyboardButton(f"{status} {u_name}", callback_data=f"manage_u_{u_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="main_admin_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("اختر مستخدماً لمراسلته أو حظره (مرتبين من الأحدث للأقدم):", reply_markup=reply_markup)
        return


async def user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("manage_u_"):
        target_id = int(data.replace("manage_u_", ""))
        context.user_data['target_user_id'] = target_id
        banned = is_user_banned(target_id)

        ban_btn_text = "🔓 إلغاء الحظر" if banned else "🔨 حظر هذا المستخدم"
        keyboard = [
            [InlineKeyboardButton("✉️ إرسال رسالة له", callback_data=f"msg_u_{target_id}")],
            [InlineKeyboardButton(ban_btn_text, callback_data=f"toggleban_u_{target_id}")],
            [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="list_users")]
        ]
        await query.edit_message_text(f"التحكم في المستخدم `{target_id}`:\nالحالة الحالية: {'محظور 🔴' if banned else 'نشط 🟢'}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("toggleban_u_"):
        target_id = int(data.replace("toggleban_u_", ""))
        new_ban_state = toggle_ban_user(target_id)
        msg = "🔴 تم حظر المستخدم بنجاح!" if new_ban_state else "🟢 تم إلغاء حظر المستخدم بنجاح!"
        await query.edit_message_text(f"{msg}\n\nالآيدي: `{target_id}`", parse_mode="Markdown")
        return ConversationHandler.END

    elif data.startswith("msg_u_"):
        target_id = int(data.replace("msg_u_", ""))
        context.user_data['target_user_id'] = target_id
        await query.edit_message_text(f"أرسل الآن الرسالة التي تريد توجيهها مباشرة للمستخدم `{target_id}`:", parse_mode="Markdown")
        return WAITING_DIRECT_TEXT


async def save_welcome_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("welcome_msg", update.message.text)
    await update.message.reply_text("✅ تم حفظ الرسالة الترحيبية الجديدة في قاعدة البيانات بنجاح!")
    return ConversationHandler.END


async def save_after_photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("after_photo_msg", update.message.text)
    await update.message.reply_text("✅ تم حفظ رسالة ما بعد الصورة بنجاح!")
    return ConversationHandler.END


async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text
    user_ids = get_all_active_user_ids()
    
    success = 0
    failed = 0

    for u_id in user_ids:
        try:
            await context.bot.send_message(chat_id=u_id, text=msg_text)
            mark_bot_blocked(u_id, False)
            success += 1
        except Exception:
            mark_bot_blocked(u_id, True)
            failed += 1

    await update.message.reply_text(
        f"📣 **تم الانتهاء من الإذاعة!**\n\n✅ نجح الإرسال لـ: {success}\n❌ فشل/قام بحظر البوت: {failed}"
    )
    return ConversationHandler.END


async def process_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_id = context.user_data.get('target_user_id')
    text_to_send = update.message.text

    try:
        await context.bot.send_message(chat_id=target_id, text=text_to_send)
        await update.message.reply_text(f"✅ تم إرسال الرسالة بنجاح للمستخدم `{target_id}`.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الرسالة. السبب: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END


# ==================== تشغيل البوت والسيرفر ====================

def main():
    init_db()  # تهيئة قاعدة البيانات عند بدء التشغيل

    # تشغيل سيرفر الويب في خيط منفصل لفتح البورت المطلوب لمنصة Railway
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # استخدام app للتيليجرام و web_app للـ FastAPI لتجنب تداخل المتغيرات
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    admin_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_button_click, pattern="^(change_welcome|change_after_photo|broadcast|list_users|show_stats|main_admin_menu)$"),
            CallbackQueryHandler(user_management, pattern="^(manage_u_|toggleban_u_|msg_u_)")
        ],
        states={
            WAITING_WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_welcome_msg)],
            WAITING_AFTER_PHOTO_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_after_photo_msg)],
            WAITING_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_broadcast)],
            WAITING_DIRECT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_direct_message)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(admin_conv_handler)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_or_link))

    print("Bot and Web Server are running...")
    app.run_polling()


if __name__ == "__main__":
    main()
