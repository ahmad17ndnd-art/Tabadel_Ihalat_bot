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

    # جدول المستخدمين (فخم + نقاط + إحالات + تفعيل وهمي + حالة المهمة)
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
            task_opened INTEGER DEFAULT 0,
            task_completed INTEGER DEFAULT 0
        )
    """)

    # جدول الإعدادات (رسائل + رابط تفعيل + رسالة فشل + رسائل إضافية + مهمة واحدة + اسم زر المهام)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            welcome_msg TEXT DEFAULT '👋 أهلاً بك في بوت النقاط والإحالات الفخم!',
            after_photo_msg TEXT DEFAULT '📸 تم استلام الصورة بنجاح، شكراً لمشاركتك!',
            verify_link TEXT DEFAULT 'https://t.me/ATF_AIRDROP_bot',
            verify_fail_msg TEXT DEFAULT '❌ لم يتم التفعيل.\nيرجى فتح رابط التفعيل أولاً ثم الضغط على زر "✅ أنا فعلت الحساب".',
            activation_msg TEXT DEFAULT '🔐 حالة حسابك الحالية: {status}\n\nلتفعيل الحساب (نظام تفعيل وهمي فخم):\n1️⃣ اضغط زر "🔗 فتح رابط التفعيل".\n2️⃣ افتح الرابط واشترك هناك (اختياري).\n3️⃣ ارجع للبوت واضغط زر "✅ أنا فعلت الحساب".\n\nعند الضغط على زر التفعيل، سيتم منحك نقاط وتفعيل حسابك داخل هذا البوت.',
            first_entry_msg TEXT DEFAULT '👋 أهلاً بك في أول دخول لك إلى البوت الفخم!',
            first_sub_msg TEXT DEFAULT '📌 للاشتراك الإجباري: يرجى الانضمام إلى القناة/البوت المطلوب ثم العودة لإكمال الاستخدام.',
            tasks_button_name TEXT DEFAULT '📝 المهام',
            task_title TEXT DEFAULT '🎯 مهمة اليوم',
            task_text TEXT DEFAULT 'اشترك بالقناة لتحصل على المكافأة',
            task_url TEXT DEFAULT 'https://t.me/example',
            task_points INTEGER DEFAULT 100,
            task_type TEXT DEFAULT 'subscribe'
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
WAITING_ACTIVATION_MSG = 8
WAITING_FIRST_ENTRY_MSG = 9
WAITING_FIRST_SUB_MSG = 10
WAITING_TASKS_BUTTON_NAME = 11
WAITING_TASK_MISSION_TEXT = 12

# ==================== دوال مساعدة ====================

def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg,
               activation_msg, first_entry_msg, first_sub_msg,
               tasks_button_name, task_title, task_text, task_url, task_points, task_type
        FROM settings WHERE id = 1
    """)
    row = c.fetchone()
    conn.close()
    return {
        "welcome_msg": row[0],
        "after_photo_msg": row[1],
        "verify_link": row[2],
        "verify_fail_msg": row[3],
        "activation_msg": row[4],
        "first_entry_msg": row[5],
        "first_sub_msg": row[6],
        "tasks_button_name": row[7],
        "task_title": row[8],
        "task_text": row[9],
        "task_url": row[10],
        "task_points": row[11],
        "task_type": row[12],
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


def get_task_status(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT task_opened, task_completed FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, 0
    return row[0], row[1]


def set_task_opened(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET task_opened = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_task_completed(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET task_completed = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ==================== واجهة المستخدم الفخمة ====================

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    points, referrals, verified = get_user_info(user.id)
    rank, total_users = get_user_rank(user.id)
    settings = get_settings()

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

    tasks_button_name = settings["tasks_button_name"]

    keyboard = [
        [InlineKeyboardButton("🔐 تفعيل الحساب", callback_data="user_activate_menu")],
        [InlineKeyboardButton("💰 نقاطي", callback_data="user_points")],
        [InlineKeyboardButton("👥 إحالاتي", callback_data="user_referrals")],
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="user_stats")],
        [InlineKeyboardButton("🏆 أفضل 20 مستخدم", callback_data="user_top")],
        [InlineKeyboardButton(tasks_button_name, callback_data="user_task_menu")],
    ]

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
    conn.close()

    # إحالات + نقاط لصاحب الإحالة
    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)

    settings = get_settings()

    # رسالة أول دخول (قبل أي شيء)
    await update.message.reply_text(settings["first_entry_msg"])
    # رسالة الاشتراك الإجباري
    await update.message.reply_text(settings["first_sub_msg"])
    # الرسالة الترحيبية
    await update.message.reply_text(settings["welcome_msg"])

    await send_main_menu(update, context)

    # إشعار للأدمن
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
        [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
        [InlineKeyboardButton("📸 تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
        [InlineKeyboardButton("🔗 تعديل رابط التفعيل", callback_data="change_verify_link")],
        [InlineKeyboardButton("⚠️ تعديل رسالة فشل التفعيل", callback_data="change_verify_fail_msg")],
        [InlineKeyboardButton("✏️ تعديل رسالة التفعيل", callback_data="change_activation_msg")],
        [InlineKeyboardButton("✏️ تعديل رسالة أول دخول", callback_data="change_first_entry_msg")],
        [InlineKeyboardButton("✏️ تعديل رسالة الاشتراك الإجباري", callback_data="change_first_sub_msg")],
        [InlineKeyboardButton("✏️ تعديل اسم زر المهام", callback_data="change_tasks_button_name")],
        [InlineKeyboardButton("✏️ تعديل مهمة المهام", callback_data="change_task_mission")],
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

    # قائمة المستخدمين للحظر بالزر
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
                [InlineKeyboardButton("🚫 حظر هذا المستخدم", callback_data=f"ban_u_{uid}")]
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

    # عرض كل المستخدمين مرتبين حسب النقاط
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
                [InlineKeyboardButton("🚫 / ✅ حظر / إلغاء حظر", callback_data=f"toggleban_u_{uid}")]
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

    # قائمة المستخدمين لإرسال رسالة فردية
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

    # إحصائيات تفصيلية
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

    # أفضل 20 مستخدم بالنقاط (للأدمن)
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
    await update.callback_query.message.reply_text("أرسل رابط البوت/المصدر الذي تريد استخدامه للتفعيل (وهمي):")
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
    await update.callback_query.message.reply_text("أرسل رسالة الفشل التي تظهر إذا لم يتم التفعيل (فخمة):")
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


async def ask_activation_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل نص رسالة التفعيل (تظهر في قائمة تفعيل الحساب):")
    return WAITING_ACTIVATION_MSG


async def save_activation_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET activation_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رسالة التفعيل ✔")
    return ConversationHandler.END


async def ask_first_entry_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رسالة أول دخول (تظهر قبل أي شيء):")
    return WAITING_FIRST_ENTRY_MSG


async def save_first_entry_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET first_entry_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رسالة أول دخول ✔")
    return ConversationHandler.END


async def ask_first_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رسالة الاشتراك الإجباري (الرسالة الأولى):")
    return WAITING_FIRST_SUB_MSG


async def save_first_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET first_sub_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ رسالة الاشتراك الإجباري ✔")
    return ConversationHandler.END


async def ask_tasks_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الاسم الجديد لزر المهام (مثال: 📝 المهام اليومية):")
    return WAITING_TASKS_BUTTON_NAME


async def save_tasks_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET tasks_button_name = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("تم حفظ اسم زر المهام ✔")
    return ConversationHandler.END


async def ask_task_mission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    txt = (
        "أرسل الآن تفاصيل المهمة بالشكل التالي (كل سطر لوحده):\n\n"
        "1️⃣ عنوان المهمة (مثال: 🎯 اشترك بالقناة)\n"
        "2️⃣ نص المهمة (مثال: اشترك بالقناة لتحصل على المكافأة)\n"
        "3️⃣ الرابط (مثال: https://t.me/yourchannel)\n"
        "4️⃣ عدد النقاط (مثال: 150)\n"
        "5️⃣ نوع المهمة (subscribe / bot / wheel / other)\n\n"
        "أرسلهم في رسالة واحدة، كل سطر لوحده."
    )
    await update.callback_query.message.reply_text(txt)
    return WAITING_TASK_MISSION_TEXT


async def save_task_mission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip().split("\n")
    if len(msg) < 5:
        await update.message.reply_text("❌ يجب أن ترسل 5 أسطر كما هو موضح في التعليمات.")
        return ConversationHandler.END

    title = msg[0].strip()
    text = msg[1].strip()
    url = msg[2].strip()
    try:
        points = int(msg[3].strip())
    except Exception:
        points = 0
    task_type = msg[4].strip()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        UPDATE settings
        SET task_title = ?, task_text = ?, task_url = ?, task_points = ?, task_type = ?
        WHERE id = 1
    """, (title, text, url, points, task_type))
    conn.commit()
    conn.close()

    await update.message.reply_text("✔ تم حفظ تفاصيل المهمة بنجاح")
    return ConversationHandler.END


# ==================== بث رسائل جماعية ====================

async def ask_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل الآن نص الرسالة الجماعية (سترسل لكل المستخدمين غير المحظورين):")
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


# ==================== استقبال الصور ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1].file_id

    settings = get_settings()
    await update.message.reply_text(settings["after_photo_msg"])

    # نقاط مقابل إرسال صورة
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

    # رسائل الأدمن لا تُحسب نقاط
    if user.id == ADMIN_ID:
        await update.message.reply_text(f"استلمت رسالتك: {text}")
        return

    # نقاط مقابل إرسال رسالة
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
        activation_msg = settings["activation_msg"].replace("{status}", status)
        keyboard = [
            [InlineKeyboardButton("🔗 فتح رابط التفعيل", callback_data="user_open_verify_link")],
            [InlineKeyboardButton("✅ أنا فعلت الحساب", callback_data="user_confirm_verify")],
        ]
        await query.message.reply_text(activation_msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "user_open_verify_link":
        set_clicked_verify_link(user.id)
        await query.message.reply_text(
            f"🔗 افتح هذا الرابط (اختياري للتفعيل الوهمي):\n{settings['verify_link']}\n\n"
            f"بعدها ارجع للبوت واضغط زر \"✅ أنا فعلت الحساب\"."
        )
        return

    if data == "user_confirm_verify":
        if verified:
            await query.message.reply_text("🔐 حسابك مفعّل بالفعل ✅")
            return

        clicked = get_clicked_verify_link(user.id)
        if not clicked:
            await query.message.reply_text(settings["verify_fail_msg"])
            return

        set_verified(user.id)
        add_points(user.id, 200)
        await query.message.reply_text(
            "🔐 تم تفعيل حسابك بنجاح ✅\n"
            "💰 حصلت على 200 نقطة كمكافأة على التفعيل!"
        )
        return

    if data == "user_task_menu":
        task_opened, task_completed = get_task_status(user.id)
        task_title = settings["task_title"]
        task_text = settings["task_text"]
        task_points = settings["task_points"]

        if task_completed:
            txt = (
                f"{task_title}\n\n"
                f"{task_text}\n\n"
                f"✅ لقد أنجزت هذه المهمة سابقًا وحصلت على مكافأتك.\n"
                f"💰 المكافأة: {task_points} نقطة"
            )
            await query.message.reply_text(txt)
            return

        if not task_opened:
            txt = (
                f"{task_title}\n\n"
                f"{task_text}\n\n"
                f"اضغط على الزر لفتح المهمة:"
            )
            keyboard = [
                [InlineKeyboardButton("🔓 فتح المهمة", callback_data="user_task_open")]
            ]
            await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        else:
            txt = (
                f"{task_title}\n\n"
                f"{task_text}\n\n"
                f"يمكنك الآن سحب المكافأة إذا أنجزت المهمة:"
            )
            keyboard = [
                [InlineKeyboardButton("🎁 سحب المكافأة", callback_data="user_task_claim")]
            ]
            await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    if data == "user_task_open":
        task_opened, task_completed = get_task_status(user.id)
        if task_completed:
            await query.message.reply_text("✅ لقد أنجزت هذه المهمة سابقًا وحصلت على مكافأتك.")
            return

        if not task_opened:
            set_task_opened(user.id)
            await query.message.reply_text(
                f"🔓 تم فتح المهمة.\n"
                f"🔗 رابط المهمة:\n{settings['task_url']}\n\n"
                f"بعد إنجاز المهمة، ارجع واضغط \"🎁 سحب المكافأة\"."
            )
        else:
            await query.message.reply_text(
                f"🔗 رابط المهمة:\n{settings['task_url']}\n\n"
                f"بعد إنجاز المهمة، اضغط \"🎁 سحب المكافأة\"."
            )
        return

    if data == "user_task_claim":
        task_opened, task_completed = get_task_status(user.id)
        if task_completed:
            await query.message.reply_text("✅ لقد سحبت المكافأة من هذه المهمة سابقًا، ولا يمكنك سحبها مرة أخرى.")
            return

        if not task_opened:
            await query.message.reply_text("❌ يجب أولاً فتح المهمة عبر زر \"🔓 فتح المهمة\" قبل سحب المكافأة.")
            return

        set_task_completed(user.id)
        add_points(user.id, settings["task_points"])
        await query.message.reply_text(
            f"🎁 تم سحب المكافأة بنجاح!\n"
            f"💰 حصلت على {settings['task_points']} نقطة.\n"
            f"✅ هذه المهمة لن تظهر لك مرة أخرى."
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

activation_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_activation_msg, pattern="^change_activation_msg$")],
    states={WAITING_ACTIVATION_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_activation_msg)]},
    fallbacks=[]
)

first_entry_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_first_entry_msg, pattern="^change_first_entry_msg$")],
    states={WAITING_FIRST_ENTRY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_first_entry_msg)]},
    fallbacks=[]
)

first_sub_msg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_first_sub_msg, pattern="^change_first_sub_msg$")],
    states={WAITING_FIRST_SUB_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_first_sub_msg)]},
    fallbacks=[]
)

tasks_button_name_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_tasks_button_name, pattern="^change_tasks_button_name$")],
    states={WAITING_TASKS_BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_tasks_button_name)]},
    fallbacks=[]
)

task_mission_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_task_mission, pattern="^change_task_mission$")],
    states={WAITING_TASK_MISSION_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_mission)]},
    fallbacks=[]
)

# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(CallbackQueryHandler(
    admin_navigation_click,
    pattern="^(list_users|list_users_msg|toggleban_u_.*|show_stats|ban_user_list|ban_u_.*|admin_top20)$"
))
telegram_app.add_handler(CallbackQueryHandler(
    user_navigation_click,
    pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_open_verify_link|user_confirm_verify|user_task_menu|user_task_open|user_task_claim)$"
))

telegram_app.add_handler(welcome_conv)
telegram_app.add_handler(after_photo_conv)
telegram_app.add_handler(broadcast_conv)
telegram_app.add_handler(direct_msg_conv)
telegram_app.add_handler(ban_user_conv)
telegram_app.add_handler(verify_link_conv)
telegram_app.add_handler(verify_fail_msg_conv)
telegram_app.add_handler(activation_msg_conv)
telegram_app.add_handler(first_entry_msg_conv)
telegram_app.add_handler(first_sub_msg_conv)
telegram_app.add_handler(tasks_button_name_conv)
telegram_app.add_handler(task_mission_conv)

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
