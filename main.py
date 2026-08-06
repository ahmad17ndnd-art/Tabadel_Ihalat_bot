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
# تنويه: التوكن ما عاد مكتوب بالكود، لازم تحطه كمتغير بيئة BOT_TOKEN
# على Railway (Variables) قبل ما تشغل البوت.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1922499737"))

if not BOT_TOKEN:
    raise RuntimeError("لازم تضيف متغير البيئة BOT_TOKEN قبل تشغيل البوت")

telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
app = FastAPI()

# ==================== قاعدة البيانات ====================
# مهم: هاد المسار لازم يكون داخل Volume دائم على Railway (مش مجلد المشروع العادي)
# وإلا رح تنمسح قاعدة البيانات (وكل المستخدمين) مع كل ديبلوي جديد.
# مثال: اعمل Volume وربطه على /data ثم حط DB_PATH=/data/bot_data.db بالمتغيرات
DB_NAME = os.environ.get("DB_PATH", "bot_data.db")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # جدول المستخدمين (فخم + نقاط + إحالات + تفعيل وهمي + هدية يومية)
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

    # جدول الإعدادات (رسائل + رابط تفعيل + رسالة فشل + رسالة إجبارية + مهام + هدية يومية)
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

    # أزرار إضافية للأدمن فقط
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

    target = update.effective_message or update.callback_query.message
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def send_subscription_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة واحدة فيها زرين: فتح الرابط (مباشر) + التحقق من الاشتراك."""
    settings = get_settings()
    keyboard = [
        [InlineKeyboardButton("🔗 فتح الرابط", url=settings["verify_link"])],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="user_confirm_verify")],
    ]
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

    # إحالات + نقاط لصاحب الإحالة
    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)

    # إذا كان مفعّل من قبل منروح مباشرة عالقائمة الرئيسية
    _, _, verified = get_user_info(user.id)
    if verified:
        await send_main_menu(update, context)
    else:
        await send_subscription_gate(update, context)

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
        [InlineKeyboardButton("✏️ تعديل الرسائل", callback_data="admin_messages_menu")],
        [InlineKeyboardButton("📝 المهام", callback_data="admin_tasks_menu")],
        [InlineKeyboardButton("🎁 إرسال هدية نقاط", callback_data="admin_send_gift_menu")],
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

    # منيو تعديل الرسائل
    if data == "admin_messages_menu":
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل الرسالة الترحيبية", callback_data="change_welcome")],
            [InlineKeyboardButton("📸 تعديل رسالة بعد الصورة", callback_data="change_after_photo")],
            [InlineKeyboardButton("🔗 تعديل الرابط الإجباري", callback_data="change_verify_link")],
            [InlineKeyboardButton("📩 تعديل الرسالة الإجباريّة", callback_data="change_first_sub_msg")],
            [InlineKeyboardButton("🎁 تعديل نقاط الهدية اليومية", callback_data="change_daily_gift_points")],
        ]
        await query.message.reply_text(
            "اختر الرسالة / الإعداد الذي تريد تعديله:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # منيو المهام (للأدمن)
    if data == "admin_tasks_menu":
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل نص المهمة", callback_data="task_edit_text")],
            [InlineKeyboardButton("🔗 تعديل رابط المهمة", callback_data="task_edit_link")],
            [InlineKeyboardButton("💰 تعديل نقاط المهمة", callback_data="task_edit_points")],
            [InlineKeyboardButton("🎉 تعديل رسالة إتمام المهمة", callback_data="task_edit_done")],
        ]
        await query.message.reply_text("📝 إدارة المهام (أدمن):", reply_markup=InlineKeyboardMarkup(keyboard))
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


# ==================== مهام ====================

async def ask_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل نص المهمة الجديد:")
    return WAITING_TASK_TEXT


async def save_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET task_text = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✔ تم حفظ نص المهمة")
    return ConversationHandler.END


async def ask_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رابط المهمة الجديد:")
    return WAITING_TASK_LINK


async def save_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET task_link = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✔ تم حفظ رابط المهمة")
    return ConversationHandler.END


async def ask_task_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل عدد النقاط الجديدة للمهمة:")
    return WAITING_TASK_POINTS


async def save_task_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pts = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح لعدد النقاط.")
        return ConversationHandler.END

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET task_points = ? WHERE id = 1", (pts,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✔ تم حفظ نقاط المهمة: {pts} نقطة")
    return ConversationHandler.END


async def ask_task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل رسالة إتمام المهمة الجديدة:")
    return WAITING_TASK_DONE_MSG


async def save_task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE settings SET task_done_msg = ? WHERE id = 1", (msg,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✔ تم حفظ رسالة إتمام المهمة")
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


# ==================== هدية نقاط من الأدمن ====================

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
        txt = f"🔐 حالة حسابك الحالية: {status}"
        if verified:
            await query.message.reply_text(txt)
        else:
            keyboard = [
                [InlineKeyboardButton("🔗 فتح الرابط", url=settings["verify_link"])],
                [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="user_confirm_verify")],
            ]
            await query.message.reply_text(
                txt + "\n\n" + settings["first_sub_msg"],
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data == "user_confirm_verify":
        first_time = not verified
        if not verified:
            set_verified(user.id)
            add_points(user.id, 200)

        if first_time:
            await query.message.reply_text(settings["welcome_msg"])
            await query.message.reply_text(
                "🔐 تم تفعيل حسابك بنجاح ✅\n"
                "💰 حصلت على 200 نقطة كمكافأة على التفعيل!"
            )
            await send_main_menu(update, context)
        else:
            await query.message.reply_text("🔐 حسابك مفعّل بالفعل ✅")
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
    entry_points=[CallbackQueryHandler(ask_gift_points, pattern="^gift_u_")],
    states={WAITING_GIFT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_gift_points)]},
    fallbacks=[]
)

# ==================== تسجيل الهاندلرز ====================

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

telegram_app.add_handler(
    CallbackQueryHandler(
        admin_navigation_click,
        pattern="^(admin_messages_menu|admin_tasks_menu|admin_send_gift_menu|list_users|list_users_msg|toggleban_u_.*|show_stats|ban_user_list|ban_u_.*|admin_top20)$"
    )
)
telegram_app.add_handler(
    CallbackQueryHandler(
        user_navigation_click,
        pattern="^(user_points|user_referrals|user_stats|user_top|user_activate_menu|user_open_verify_link|user_confirm_verify|user_daily_gift|user_tasks_menu|user_task_done)$"
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
telegram_app.add_handler(gift_points_conv)

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
