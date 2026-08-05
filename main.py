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
BOT_TOKEN = "ضع_توكن_بوتك_هنا"
ADMIN_ID = 1922499737

telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
app = FastAPI()

# ==================== قاعدة البيانات ====================
DB_NAME = "bot_data.db"

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
            last_gift_at TEXT DEFAULT NULL
        )
    """)

    # جدول الإعدادات
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT '👋 أهلاً بك في بوت النقاط والإحالات الفخم!',
            after_photo_msg TEXT DEFAULT '📸 تم استلام الصورة بنجاح، شكراً لمشاركتك!',
            verify_link TEXT DEFAULT 'https://t.me/example',
            verify_fail_msg TEXT DEFAULT '❌ لم يتم التفعيل.',
            first_sub_msg TEXT DEFAULT 'عذراً عليك الاشتراك أولاً.',
            daily_gift_points INTEGER DEFAULT 50
        )
    """)

    # جدول المهام المتعددة
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            link TEXT,
            points INTEGER,
            done_msg TEXT
        )
    """)

    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    conn.commit()
    conn.close()

# ==================== دوال الإعدادات ====================

def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               first_sub_msg, daily_gift_points
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
        "daily_gift_points": row[5],
    }

# ==================== دوال المهام ====================

def get_all_tasks():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, text, link, points, done_msg FROM tasks ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    tasks = []
    for row in rows:
        tasks.append({
            "id": row[0],
            "text": row[1],
            "link": row[2],
            "points": row[3],
            "done_msg": row[4],
        })
    return tasks

def add_task(text, link, points, done_msg):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (text, link, points, done_msg) VALUES (?, ?, ?, ?)",
        (text, link, points, done_msg)
    )
    conn.commit()
    conn.close()

def get_task_by_id(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, text, link, points, done_msg FROM tasks WHERE id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "text": row[1],
        "link": row[2],
        "points": row[3],
        "done_msg": row[4],
    }

# ==================== دوال المستخدم ====================

def get_user_info(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points, referrals, verified FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, 0, 0
    return row[0], row[1], row[2]

def add_points(user_id, amount):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def add_referral(ref_by):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_by,))
    conn.commit()
    conn.close()

def set_verified(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def set_clicked_verify_link(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET clicked_verify_link = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_clicked_verify_link(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT clicked_verify_link FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0
# ==================== القائمة الرئيسية ====================

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    points, referrals, verified = get_user_info(user.id)

    level = "🥉 مبتدئ"
    if points >= 500:
        level = "🥈 نشط"
    if points >= 1000:
        level = "🥇 VIP"
    if points >= 2000:
        level = "👑 Super VIP"

    text = (
        f"✨ بوت النقاط والإحالات ✨\n\n"
        f"👤 المستخدم: {user.username or user.full_name}\n"
        f"💰 نقاطك: {points}\n"
        f"👥 إحالاتك: {referrals}\n"
        f"🔐 حالة الحساب: {'✅ مفعّل' if verified else '❌ غير مفعّل'}\n"
        f"🏆 مستواك: {level}\n\n"
        f"اختر من القائمة:"
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
        keyboard.append([InlineKeyboardButton("🧑‍💼 لوحة الإدارة", callback_data="admin_panel")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== /start ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    ref_by = None
    if args:
        try:
            ref_by = int(args[0])
        except:
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

    await update.message.reply_text(settings["first_sub_msg"])
    await update.message.reply_text(settings["welcome_msg"])

    await send_main_menu(update, context)


# ==================== نظام التفعيل الجديد ====================

async def user_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    await query.answer()

    points, referrals, verified = get_user_info(user.id)
    settings = get_settings()

    # نقاطي
    if data == "user_points":
        await query.message.reply_text(f"💰 نقاطك: {points}")
        return

    # إحالاتي
    if data == "user_referrals":
        ref_link = f"https://t.me/{(await telegram_app.bot.get_me()).username}?start={user.id}"
        await query.message.reply_text(
            f"👥 إحالاتك: {referrals}\n\n"
            f"🔗 رابط الإحالة:\n{ref_link}"
        )
        return

    # إحصائيات
    if data == "user_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
        verified_count = c.fetchone()[0]
        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات البوت:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"🔐 المفعّلون: {verified_count}"
        )
        return

    # أفضل 20
    if data == "user_top":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()

        text = "🏆 أفضل 20 مستخدم:\n\n"
        medals = ["🥇", "🥈", "🥉"]

        for i, (uname, pts) in enumerate(rows):
            medal = medals[i] if i < len(medals) else "🔹"
            text += f"{medal} {uname} — {pts} نقطة\n"

        await query.message.reply_text(text)
        return

    # منيو التفعيل
    if data == "user_activate_menu":
        txt = (
            f"🔐 حالة حسابك: {'✅ مفعّل' if verified else '❌ غير مفعّل'}\n\n"
            f"لتفعيل الحساب:\n"
            f"1️⃣ اضغط زر فتح رابط التفعيل.\n"
            f"2️⃣ ارجع واضغط زر أنا فعلت الحساب."
        )

        keyboard = [
            [InlineKeyboardButton("🔗 فتح رابط التفعيل", url=settings["verify_link"])],
            [InlineKeyboardButton("✅ أنا فعلت الحساب", callback_data="user_confirm_verify")],
        ]

        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # تأكيد التفعيل
    if data == "user_confirm_verify":
        if verified:
            await query.message.reply_text("🔐 حسابك مفعّل بالفعل.")
            return

        set_verified(user.id)
        add_points(user.id, 200)

        await query.message.reply_text(
            "🔐 تم تفعيل حسابك بنجاح!\n💰 تمت إضافة 200 نقطة."
        )
        return

    # الهدية اليومية
    if data == "user_daily_gift":
        settings = get_settings()
        daily_pts = settings["daily_gift_points"]

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT last_gift_at FROM users WHERE user_id = ?", (user.id,))
        row = c.fetchone()
        conn.close()

        if row and row[0]:
            last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - last).total_seconds() < 86400:
                await query.message.reply_text("❌ لقد أخذت هديتك اليومية مسبقاً.")
                return

        add_points(user.id, daily_pts)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET last_gift_at = ? WHERE user_id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user.id))
        conn.commit()
        conn.close()

        await query.message.reply_text(f"🎁 تمت إضافة {daily_pts} نقطة!")
        return

    # عرض المهام المتعددة
    if data == "user_tasks_menu":
        tasks = get_all_tasks()

        if not tasks:
            await query.message.reply_text("لا توجد مهام حالياً.")
            return

        for task in tasks:
            keyboard = [
                [InlineKeyboardButton("🔗 فتح المهمة", url=task["link"])],
                [InlineKeyboardButton("✔ أنجزت المهمة", callback_data=f"user_task_done_{task['id']}")]
            ]

            await query.message.reply_text(
                f"📝 المهمة:\n{task['text']}\n\n"
                f"💰 نقاط المهمة: {task['points']}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    # تنفيذ مهمة
    if data.startswith("user_task_done_"):
        task_id = int(data.replace("user_task_done_", ""))
        task = get_task_by_id(task_id)

        if not task:
            await query.message.reply_text("❌ هذه المهمة لم تعد موجودة.")
            return

        add_points(user.id, task["points"])
        await query.message.reply_text(task["done_msg"])
        return
# ==================== لوحة الإدارة ====================

async def admin_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    # لوحة الإدارة الرئيسية
    if data == "admin_panel":
        keyboard = [
            [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("📝 المهام", callback_data="admin_tasks_menu")],
            [InlineKeyboardButton("🎁 إرسال هدية نقاط", callback_data="admin_gift_menu")],
        ]
        await query.message.reply_text("🧑‍💼 لوحة الإدارة:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # إحصائيات
    if data == "admin_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
        verified_count = c.fetchone()[0]
        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات البوت:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"🔐 المفعّلون: {verified_count}"
        )
        return

    # قائمة المهام
    if data == "admin_tasks_menu":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مهمة جديدة", callback_data="task_add_new")],
            [InlineKeyboardButton("📋 عرض كل المهام", callback_data="task_list_all")],
        ]
        await query.message.reply_text("📝 إدارة المهام:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # عرض كل المهام
    if data == "task_list_all":
        tasks = get_all_tasks()
        if not tasks:
            await query.message.reply_text("❌ لا توجد مهام.")
            return

        for t in tasks:
            await query.message.reply_text(
                f"🆔 ID: {t['id']}\n"
                f"📝 نص المهمة: {t['text']}\n"
                f"🔗 الرابط: {t['link']}\n"
                f"💰 النقاط: {t['points']}\n"
                f"🎉 رسالة الإنجاز: {t['done_msg']}"
            )
        return

    # قائمة الهدايا
    if data == "admin_gift_menu":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username, points FROM users ORDER BY points DESC")
        users = c.fetchall()
        conn.close()

        for uid, uname, pts in users:
            keyboard = [
                [InlineKeyboardButton("🎁 إرسال هدية", callback_data=f"gift_u_{uid}")]
            ]
            await query.message.reply_text(
                f"👤 {uname}\nID: {uid}\n💰 نقاطه: {pts}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return


# ==================== إضافة مهمة جديدة ====================

WAITING_NEW_TASK_TEXT = 100
WAITING_NEW_TASK_LINK = 101
WAITING_NEW_TASK_POINTS = 102
WAITING_NEW_TASK_DONE_MSG = 103

async def ask_new_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📝 أرسل نص المهمة:")
    return WAITING_NEW_TASK_TEXT

async def save_new_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task_text"] = update.message.text
    await update.message.reply_text("🔗 أرسل رابط المهمة:")
    return WAITING_NEW_TASK_LINK

async def save_new_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task_link"] = update.message.text.strip()
    await update.message.reply_text("💰 أرسل عدد النقاط:")
    return WAITING_NEW_TASK_POINTS

async def save_new_task_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pts = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ أرسل رقم صحيح.")
        return ConversationHandler.END

    context.user_data["new_task_points"] = pts
    await update.message.reply_text("🎉 أرسل رسالة الإنجاز:")
    return WAITING_NEW_TASK_DONE_MSG

async def save_new_task_done_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = context.user_data["new_task_text"]
    link = context.user_data["new_task_link"]
    points = context.user_data["new_task_points"]
    done_msg = update.message.text

    add_task(text, link, points, done_msg)

    await update.message.reply_text("✔ تم إضافة المهمة بنجاح.")
    return ConversationHandler.END


new_task_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_new_task_text, pattern="^task_add_new$")],
    states={
        WAITING_NEW_TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_task_text)],
        WAITING_NEW_TASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_task_link)],
        WAITING_NEW_TASK_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_task_points)],
        WAITING_NEW_TASK_DONE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_task_done_msg)],
    },
    fallbacks=[]
)

telegram_app.add_handler(new_task_conv)


# ==================== إصلاح هدية النقاط ====================

WAITING_GIFT_POINTS = 200

async def ask_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = int(update.callback_query.data.replace("gift_u_", ""))
    context.user_data["gift_target"] = target
    await update.callback_query.message.reply_text("🎁 أرسل عدد النقاط:")
    return WAITING_GIFT_POINTS

async def process_gift_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get("gift_target")
    if not target:
        await update.message.reply_text("❌ خطأ داخلي.")
        return ConversationHandler.END

    try:
        pts = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ أرسل رقم صحيح.")
        return ConversationHandler.END

    add_points(target, pts)

    try:
        await telegram_app.bot.send_message(target, f"🎁 وصلك هدية نقاط: +{pts}")
    except:
        pass

    await update.message.reply_text("✔ تم إرسال الهدية.")
    return ConversationHandler.END


gift_points_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_gift_points, pattern="^gift_u_")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)

telegram_app.add_handler(gift_points_conv)


# ==================== استقبال الصور ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    settings = get_settings()

    await update.message.reply_text(settings["after_photo_msg"])
    add_points(user.id, 20)


# ==================== استقبال النصوص ====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if user.id != ADMIN_ID:
        add_points(user.id, 10)

    await update.message.reply_text(f"📩 استلمت رسالتك:\n{text}")


# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(admin_navigation_click, pattern="^(admin_panel|admin_stats|admin_tasks_menu|task_list_all|admin_gift_menu|gift_u_.*)$"))
telegram_app.add_handler(CallbackQueryHandler(user_navigation_click, pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_confirm_verify|user_daily_gift|user_tasks_menu|user_task_done_.*)$"))

telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


# ==================== FastAPI ====================

@app.get("/")
def home():
    return {"status": "Bot is running"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
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
        logger.info(f"Webhook set to: {webhook_url}")

@app.on_event("shutdown")
async def shutdown_event():
    await telegram_app.stop()
    await telegram_app.shutdown()
