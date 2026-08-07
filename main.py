import os
import re
import logging
import sqlite3
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButtonRequestUser, KeyboardButtonRequestChat
)
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

MIN_WAIT_SECONDS = 8
USERS_PAGE_SIZE = 8

# فئات الإعلانات
CATEGORY_LABELS = {
    "channel": "📣 قناة",
    "group": "👥 مجموعة",
    "bot": "🤖 بوت",
    "post": "📝 منشور",
    "interaction": "❤️ تفاعل",
}
CATEGORY_ORDER = ["channel", "group", "bot", "post", "interaction"]
AUTO_VERIFIABLE_CATEGORIES = {"channel", "group"}

# الأسعار هنا تعني (المكافأة لكل شخص)
DEFAULT_CATEGORY_PRICES = {
    "channel": (10, 50),
    "group": (10, 50),
    "bot": (15, 60),
    "post": (5, 30),
    "interaction": (5, 20),
}


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
            last_gift_at TEXT DEFAULT NULL,
            gate_sent_at TEXT DEFAULT NULL
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
            daily_gift_points INTEGER DEFAULT 50,
            verify_channel_id TEXT DEFAULT NULL
        )
    """)
    c.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS category_prices (
            category TEXT PRIMARY KEY,
            min_price INTEGER DEFAULT 10,
            max_price INTEGER DEFAULT 50
        )
    """)
    for cat, (mn, mx) in DEFAULT_CATEGORY_PRICES.items():
        c.execute(
            "INSERT OR IGNORE INTO category_prices (category, min_price, max_price) VALUES (?, ?, ?)",
            (cat, mn, mx)
        )

    c.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            ad_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            owner_username TEXT,
            category TEXT,
            link TEXT,
            description TEXT,
            post_price INTEGER DEFAULT 0,
            reward_points INTEGER DEFAULT 0,
            target_count INTEGER DEFAULT 0,
            current_count INTEGER DEFAULT 0,
            verify_mode TEXT DEFAULT 'manual',
            channel_id TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ad_completions (
            ad_id INTEGER,
            user_id INTEGER,
            status TEXT DEFAULT 'sent',
            proof_file_id TEXT DEFAULT NULL,
            sent_at TEXT,
            submitted_at TEXT DEFAULT NULL,
            resolved_at TEXT DEFAULT NULL,
            PRIMARY KEY (ad_id, user_id)
        )
    """)

    migrations = [
        ("users", "gate_sent_at", "TEXT DEFAULT NULL"),
        ("settings", "verify_channel_id", "TEXT DEFAULT NULL"),
        ("ads", "target_count", "INTEGER DEFAULT 0"),
        ("ads", "current_count", "INTEGER DEFAULT 0"),
    ]
    for table, column, col_def in migrations:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

# ==================== حالات المحادثة ====================
WAITING_WELCOME_MSG, WAITING_AFTER_PHOTO_MSG, WAITING_BROADCAST_MSG = 1, 2, 3
WAITING_DIRECT_TEXT, WAITING_VERIFY_LINK, WAITING_VERIFY_FAIL_MSG = 4, 6, 7
WAITING_FIRST_SUB_MSG, WAITING_GIFT_POINTS, WAITING_DAILY_GIFT_POINTS = 8, 13, 14
WAITING_VERIFY_CHANNEL, WAITING_GIFT_ALL_POINTS = 15, 23

WAITING_AD_TARGET = 30
WAITING_AD_DESC = 31
WAITING_AD_REWARD = 32
WAITING_AD_QUANTITY = 33
WAITING_AD_CONFIRM = 34
WAITING_CATEGORY_PRICE = 35
WAITING_AD_REVIEW_NOTE = 36


# ==================== دوال مساعدة عامة ====================
def get_settings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT welcome_msg, after_photo_msg, verify_link, verify_fail_msg, first_sub_msg, daily_gift_points, verify_channel_id FROM settings WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return {"welcome_msg": row[0], "after_photo_msg": row[1], "verify_link": row[2], "verify_fail_msg": row[3], "first_sub_msg": row[4], "daily_gift_points": row[5], "verify_channel_id": row[6]}

def get_user_info(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points, referrals, verified FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return (row[0], row[1], row[2]) if row else (0, 0, 0)

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

def get_category_price(category: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT min_price, max_price FROM category_prices WHERE category = ?", (category,))
    row = c.fetchone()
    conn.close()
    return (row[0], row[1]) if row else DEFAULT_CATEGORY_PRICES.get(category, (10, 50))


# ==================== إدارة الإعلانات ====================
def create_ad(owner_id, owner_username, category, link, description, post_price, reward_points, target_count, verify_mode):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """INSERT INTO ads (owner_id, owner_username, category, link, description, post_price, reward_points, target_count, verify_mode, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (owner_id, owner_username, category, link, description, post_price, reward_points, target_count, verify_mode, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    ad_id = c.lastrowid
    conn.commit()
    conn.close()
    return ad_id

def get_ad(ad_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM ads WHERE ad_id = ?", (ad_id,))
    row = c.fetchone()
    if not row: return None
    keys = [desc[0] for desc in c.description]
    conn.close()
    return dict(zip(keys, row))

def update_ad_status(ad_id, status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ads SET status = ? WHERE ad_id = ?", (status, ad_id))
    conn.commit()
    conn.close()

def increment_ad_count(ad_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ads SET current_count = current_count + 1 WHERE ad_id = ?", (ad_id,))
    c.execute("SELECT target_count, current_count FROM ads WHERE ad_id = ?", (ad_id,))
    target, current = c.fetchone()
    if current >= target:
        c.execute("UPDATE ads SET status = 'completed' WHERE ad_id = ?", (ad_id,))
    conn.commit()
    conn.close()

def get_ads_for_earn(category):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # جلب الإعلانات النشطة وترتيبها من الأكثر نقاطاً للأقل
    c.execute(
        "SELECT * FROM ads WHERE status = 'active' AND category = ? AND current_count < target_count ORDER BY reward_points DESC",
        (category,)
    )
    rows = c.fetchall()
    keys = [desc[0] for desc in c.description]
    conn.close()
    return [dict(zip(keys, r)) for r in rows]

# ==================== الإثباتات ====================
def get_completion(ad_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT status, proof_file_id FROM ad_completions WHERE ad_id = ? AND user_id = ?", (ad_id, user_id))
    row = c.fetchone()
    conn.close()
    return row

def open_ad_for_user(ad_id, user_id):
    row = get_completion(ad_id, user_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if row is None or row[0] == "rejected":
        c.execute(
            "INSERT INTO ad_completions (ad_id, user_id, status, sent_at) VALUES (?, ?, 'sent', ?) "
            "ON CONFLICT(ad_id, user_id) DO UPDATE SET status = 'sent', sent_at = excluded.sent_at, proof_file_id = NULL",
            (ad_id, user_id, now_str)
        )
        conn.commit()
        conn.close()
        return "sent"
    conn.close()
    return row[0]

def set_completion_proof(ad_id, user_id, file_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ad_completions SET status = 'proof_submitted', proof_file_id = ?, submitted_at = ? WHERE ad_id = ? AND user_id = ?", (file_id, now_str, ad_id, user_id))
    conn.commit()
    conn.close()

def set_completion_status(ad_id, user_id, status):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE ad_completions SET status = ?, resolved_at = ? WHERE ad_id = ? AND user_id = ?", (status, now_str, ad_id, user_id))
    conn.commit()
    conn.close()

def get_latest_pending_proof_request(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT ac.ad_id FROM ad_completions ac JOIN ads a ON ac.ad_id = a.ad_id WHERE ac.user_id = ? AND ac.status = 'sent' ORDER BY ac.sent_at DESC LIMIT 1",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_latest_review_request(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT ad_id FROM ad_completions WHERE user_id = ? AND status = 'review_requested' ORDER BY sent_at DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

# ==================== القوائم الرئيسية ====================
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    points, referrals, verified = get_user_info(user.id)

    text = (
        f"✨ الواجهة الرئيسية لبوت المهام والإحالات ✨\n\n"
        f"👤 المستخدم: {user.username or user.full_name}\n"
        f"💰 نقاطك: {points}\n"
        f"👥 إحالاتك: {referrals}\n\n"
        f"اختر من القائمة التالية:"
    )

    keyboard = [
        [InlineKeyboardButton("💰 اربح نقاط (تنفيذ مهام)", callback_data="earn_menu")],
        [InlineKeyboardButton("📢 نشر إعلان", callback_data="publish_ad")],
        [InlineKeyboardButton("👥 رابط إحالتي", callback_data="user_referrals")],
        [InlineKeyboardButton("📋 إعلاناتي", callback_data="my_ads")],
        [InlineKeyboardButton("🎁 الهدية اليومية", callback_data="user_daily_gift")]
    ]
    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🧑‍💼 لوحة الإدارة", callback_data="admin_panel_inline")])

    target = update.effective_message or update.callback_query.message
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref_by = int(args[0]) if args and args[0].isdigit() else None

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, joined_at, ref_by) VALUES (?, ?, ?, ?)",
              (user.id, user.username or user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ref_by))
    conn.commit()
    conn.close()

    if ref_by and ref_by != user.id:
        add_referral(ref_by)
        add_points(ref_by, 150)

    # يعرض الواجهة الرئيسية فوراً كما طلبت
    await send_main_menu(update, context)


# ==================== نشر إعلان (Conversation) ====================
async def publish_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = []
    for cat in CATEGORY_ORDER:
        keyboard.append([InlineKeyboardButton(f"نشر {CATEGORY_LABELS[cat]}", callback_data=f"adcat:{cat}")])
    await query.message.reply_text("📢 اختر نوع الإعلان الذي تريد نشره:", reply_markup=InlineKeyboardMarkup(keyboard))

async def ad_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":")[1]
    context.user_data["new_ad"] = {"category": cat}
    
    # استخدام أزرار الـ Request لاختيار البوت أو القناة مباشرة من تليجرام
    if cat == "bot":
        btn = KeyboardButton("🤖 إضافة بوت", request_user=KeyboardButtonRequestUser(request_id=1, user_is_bot=True))
        reply_markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("👇 اضغط على الزر بالأسفل لاختيار البوت من قائمة محادثاتك:", reply_markup=reply_markup)
    elif cat in ["channel", "group"]:
        btn = KeyboardButton("📢 اختيار قناة/مجموعة", request_chat=KeyboardButtonRequestChat(request_id=2, chat_is_channel=(cat=="channel")))
        reply_markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("👇 اضغط على الزر بالأسفل لاختيار القناة أو المجموعة:", reply_markup=reply_markup)
    else:
        await query.message.reply_text("🔗 أرسل الآن رابط المنشور/التفاعل:")
    
    return WAITING_AD_TARGET

async def ad_target_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_data = context.user_data["new_ad"]
    link = ""

    # التقاط الـ ID إذا استخدم المستخدم الأزرار المدمجة
    if update.message.user_shared:
        link = f"tg://user?id={update.message.user_shared.user_id}"
    elif update.message.chat_shared:
        link = f"tg://resolve?domain={update.message.chat_shared.chat_id}"
    elif update.message.text:
        link = update.message.text.strip()
    
    if not link:
        await update.message.reply_text("❌ لم يتم التعرف على الرابط أو البوت. حاول مرة أخرى.")
        return WAITING_AD_TARGET

    ad_data["link"] = link
    # إزالة كيبورد الأزرار السفلية إن وجدت
    await update.message.reply_text(
        "📝 الآن أرسل وصف الإعلان (ماذا تريد من الشخص أن يفعل؟ مثلاً: ادخل للبوت وأكمل الكابتشا):",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_AD_DESC

async def ad_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ad"]["description"] = update.message.text
    cat = context.user_data["new_ad"]["category"]
    mn, mx = get_category_price(cat)
    context.user_data["new_ad"]["min_p"] = mn
    context.user_data["new_ad"]["max_p"] = mx
    
    await update.message.reply_text(f"💰 حدد سعر الإعلان (المكافأة التي سيأخذها كل شخص ينفذ المهمة).\nالحد المسموح لهذه الفئة: من {mn} إلى {mx} نقطة:")
    return WAITING_AD_REWARD

async def ad_reward_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reward = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح.")
        return WAITING_AD_REWARD

    ad_data = context.user_data["new_ad"]
    if reward < ad_data["min_p"] or reward > ad_data["max_p"]:
        await update.message.reply_text(f"❌ المكافأة يجب أن تكون بين {ad_data['min_p']} و {ad_data['max_p']} نقطة.")
        return WAITING_AD_REWARD

    ad_data["reward_points"] = reward
    user_points, _, _ = get_user_info(update.effective_user.id)
    max_people = user_points // reward

    if max_people <= 0:
        await update.message.reply_text(f"❌ نقاطك ({user_points}) لا تكفي لنشر هذا الإعلان.\nتحتاج على الأقل {reward} نقطة لشخص واحد.")
        return ConversationHandler.END

    await update.message.reply_text(f"👥 الحد الأقصى للأشخاص الذين يمكنك إضافتهم بنقاطك هو: {max_people} شخص.\nكم عدد الأشخاص الذين تريدهم أن يدخلوا؟ أرسل الرقم:")
    return WAITING_AD_QUANTITY

async def ad_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_count = int(update.message.text.strip())
        if target_count <= 0: raise ValueError
    except:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح أكبر من صفر.")
        return WAITING_AD_QUANTITY

    ad_data = context.user_data["new_ad"]
    reward = ad_data["reward_points"]
    user_points, _, _ = get_user_info(update.effective_user.id)
    
    total_cost = reward * target_count
    if total_cost > user_points:
        await update.message.reply_text(f"❌ نقاطك لا تكفي. التكلفة: {total_cost}، ونقاطك: {user_points}.\nأرسل عدداً أقل:")
        return WAITING_AD_QUANTITY

    ad_data["target_count"] = target_count
    ad_data["total_cost"] = total_cost

    keyboard = [
        [InlineKeyboardButton("✅ تأكيد النشر", callback_data="confirm_publish")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_publish")]
    ]
    await update.message.reply_text(
        f"📊 ملخص الإعلان:\n"
        f"الفئة: {CATEGORY_LABELS[ad_data['category']]}\n"
        f"المكافأة للشخص: {reward} نقطة\n"
        f"العدد المطلوب: {target_count} أشخاص\n"
        f"💸 إجمالي الخصم: {total_cost} نقطة\n\nهل أنت متأكد؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_AD_CONFIRM

async def ad_confirm_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_publish":
        await query.message.reply_text("تم الإلغاء.")
        context.user_data.pop("new_ad", None)
        return ConversationHandler.END

    user = update.effective_user
    ad_data = context.user_data["new_ad"]
    
    user_points, _, _ = get_user_info(user.id)
    if user_points < ad_data["total_cost"]:
        await query.message.reply_text("❌ حدث خطأ: نقاطك لم تعد تكفي.")
        return ConversationHandler.END

    deduct_points(user.id, ad_data["total_cost"])
    
    ad_id = create_ad(
        owner_id=user.id,
        owner_username=user.username or user.full_name,
        category=ad_data["category"],
        link=ad_data["link"],
        description=ad_data["description"],
        post_price=ad_data["total_cost"],
        reward_points=ad_data["reward_points"],
        target_count=ad_data["target_count"],
        verify_mode="manual"
    )

    await query.message.reply_text(f"✅ تم نشر إعلانك بنجاح! رقم الإعلان: #{ad_id}")
    
    # نرسل للأدمن للموافقة
    try:
        keyboard = [[
            InlineKeyboardButton("✅ قبول ونشر", callback_data=f"adnew:approve:{ad_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"adnew:reject:{ad_id}")
        ]]
        await telegram_app.bot.send_message(
            ADMIN_ID,
            f"📢 طلب إعلان جديد #{ad_id}\nمن: {user.username}\nالتكلفة: {ad_data['total_cost']} | العدد: {ad_data['target_count']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except: pass

    context.user_data.pop("new_ad", None)
    return ConversationHandler.END


# ==================== قسم الربح (Earn) ====================
async def show_earn_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = []
    for cat in CATEGORY_ORDER:
        keyboard.append([InlineKeyboardButton(CATEGORY_LABELS[cat], callback_data=f"earncat:{cat}")])
    await query.message.reply_text("💰 اختر القسم الذي تريد الربح منه:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_earn_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":")[1]
    user = update.effective_user

    ads = get_ads_for_earn(cat)
    shown = 0
    for ad in ads:
        if ad["owner_id"] == user.id: continue
        comp = get_completion(ad["ad_id"], user.id)
        if comp and comp[0] in ("approved", "sent", "proof_submitted", "review_requested"):
            continue
        
        keyboard = [[InlineKeyboardButton("🎯 الدخول للمهمة", callback_data=f"earnad:{ad['ad_id']}")]]
        await query.message.reply_text(
            f"📌 مهمة بـ {ad['reward_points']} نقطة\n"
            f"📝 المطلوب: {ad['description']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        shown += 1

    if shown == 0:
        await query.message.reply_text("لا يوجد مهام متاحة في هذا القسم حالياً.")

async def show_earn_ad_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ad_id = int(query.data.split(":")[1])
    ad = get_ad(ad_id)
    user = update.effective_user

    if not ad or ad["status"] != "active" or ad["current_count"] >= ad["target_count"]:
        await query.message.reply_text("❌ هذا الإعلان اكتمل أو لم يعد متاحاً.")
        return

    open_ad_for_user(ad_id, user.id)

    keyboard = [[InlineKeyboardButton("🔗 اذهب للرابط/البوت", url=ad["link"])]]
    await query.message.reply_text(
        f"📝 وصف المهمة:\n{ad['description']}\n\n"
        f"🎁 المكافأة: {ad['reward_points']} نقطة\n\n"
        f"⚠️ **ادخل إلى البوت/الرابط ونفذ المطلوب، ثم ارسل سكرين شوت (صورة) إلى هنا مباشرة كإثبات.**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== استقبال الصور (إثبات المهام) ====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1].file_id

    # هل المستخدم يرد على طلب إعادة محاولة؟
    review_ad_id = get_latest_review_request(user.id)
    if review_ad_id:
        ad = get_ad(review_ad_id)
        set_completion_proof(review_ad_id, user.id, photo)
        keyboard = [
            [InlineKeyboardButton("✅ دفع", callback_data=f"adpay:{review_ad_id}:{user.id}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"adrejc:{review_ad_id}:{user.id}")],
            [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"adrev:{review_ad_id}:{user.id}")]
        ]
        await telegram_app.bot.send_photo(
            ad["owner_id"], photo,
            caption=f"🧾 رد جديد بعد إعادة المحاولة لمهمة #{review_ad_id}\nمن: {user.username}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✔ تم إرسال إثباتك لصاحب الإعلان.")
        return

    # هل المستخدم يرسل إثبات لمهمة جديدة فتحها للتو؟
    pending_ad_id = get_latest_pending_proof_request(user.id)
    if pending_ad_id:
        ad = get_ad(pending_ad_id)
        set_completion_proof(pending_ad_id, user.id, photo)
        keyboard = [
            [InlineKeyboardButton("✅ دفع", callback_data=f"adpay:{pending_ad_id}:{user.id}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"adrejc:{pending_ad_id}:{user.id}")],
            [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"adrev:{pending_ad_id}:{user.id}")]
        ]
        await telegram_app.bot.send_photo(
            ad["owner_id"], photo,
            caption=f"🧾 إثبات جديد لمهمة #{pending_ad_id}\nمن: {user.username}\nقم بالدفع أو اطلب إعادة محاولة.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✔ تم إرسال السكرين شوت لصاحب الإعلان، سيتم إضافة النقاط بعد مراجعته.")
        return

    await update.message.reply_text("عذراً، لم تفتح مهمة لترسل إثباتاً لها.")

# ==================== قرارات صاحب الإعلان (دفع / رفض / مراجعة) ====================
async def ad_owner_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    _, ad_id_s, uid_s = data.split(":")
    ad_id, uid = int(ad_id_s), int(uid_s)
    ad = get_ad(ad_id)
    
    if not ad or ad["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الإجراء غير متاح لك.")
        return

    if data.startswith("adpay:"):
        add_points(uid, ad["reward_points"])
        set_completion_status(ad_id, uid, "approved")
        increment_ad_count(ad_id)
        await query.edit_message_caption(caption=f"✅ تم الدفع بنجاح للمستخدم.")
        try: await telegram_app.bot.send_message(uid, f"🎉 تم قبول إثباتك وحصلت على {ad['reward_points']} نقطة.")
        except: pass
        return

    if data.startswith("adrejc:"):
        set_completion_status(ad_id, uid, "rejected")
        await query.edit_message_caption(caption="🚫 تم رفض الإثبات.")
        try: await telegram_app.bot.send_message(uid, f"❌ تم رفض إثباتك للمهمة. يرجى تحري الدقة.")
        except: pass
        return

async def ask_review_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ad_id_s, uid_s = query.data.split(":")
    context.user_data["review_ad"] = {"ad_id": int(ad_id_s), "uid": int(uid_s)}
    await query.message.reply_text("✏️ أرسل رسالة توضح سبب إعادة المحاولة (مثال: أكمل الكابتشا أولاً):")
    return WAITING_AD_REVIEW_NOTE

async def save_review_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_id = context.user_data["review_ad"]["ad_id"]
    uid = context.user_data["review_ad"]["uid"]
    reason = update.message.text
    
    ad = get_ad(ad_id)
    set_completion_status(ad_id, uid, "review_requested")

    keyboard = [[InlineKeyboardButton("الذهاب للبوت مرة أخرى 🔗", url=ad["link"])]]
    try:
        await telegram_app.bot.send_message(
            uid,
            f"⚠️ صاحب الإعلان يطلب منك إعادة المحاولة.\nالسبب: {reason}\n\nبعد التنفيذ ارسل سكرين شوت هنا مجدداً.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✔ تم إرسال التنبيه للمستخدم لإعادة المحاولة.")
    except:
        await update.message.reply_text("خطأ في الإرسال.")
        
    context.user_data.pop("review_ad", None)
    return ConversationHandler.END


# ==================== أزرار القائمة (Callback) ====================
async def user_navigation_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    await query.answer()

    if data == "user_referrals":
        ref_link = f"https://t.me/Tabadel_Ihalat_bot?start={user.id}"
        _, refs, _ = get_user_info(user.id)
        await query.message.reply_text(f"👥 إحالاتك: {refs}\n🔗 رابط إحالتك:\n{ref_link}")
    
    elif data == "my_ads":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT ad_id, category, reward_points, target_count, current_count, status FROM ads WHERE owner_id = ?", (user.id,))
        ads = c.fetchall()
        conn.close()
        if not ads:
            await query.message.reply_text("لم تنشر أي إعلان بعد.")
            return
        for ad in ads:
            await query.message.reply_text(f"📌 إعلان #{ad[0]} ({CATEGORY_LABELS[ad[1]]})\nالمكافأة: {ad[2]} | المنجز: {ad[4]}/{ad[3]}\nالحالة: {ad[5]}")

    elif data == "admin_panel_inline" and user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("📢 الإعلانات بانتظار الموافقة", callback_data="adlist:pending")]
        ]
        await query.message.reply_text("لوحة الإدارة:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    
    if data == "adlist:pending":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT ad_id, owner_username, description, post_price FROM ads WHERE status = 'pending'")
        rows = c.fetchall()
        conn.close()
        for r in rows:
            keyboard = [[
                InlineKeyboardButton("✅ قبول", callback_data=f"adnew:approve:{r[0]}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"adnew:reject:{r[0]}")
            ]]
            await query.message.reply_text(f"إعلان #{r[0]}\nمن: {r[1]}\n{r[2]}\nالتكلفة المدفوعة: {r[3]}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adnew:"):
        _, action, ad_id = data.split(":")
        ad = get_ad(ad_id)
        if action == "approve":
            update_ad_status(ad_id, "active")
            await query.message.reply_text(f"تم قبول الإعلان #{ad_id}")
            try: await telegram_app.bot.send_message(ad["owner_id"], f"🎉 تم قبول إعلانك وبدأ المستخدمون برؤيته!")
            except: pass
        else:
            update_ad_status(ad_id, "rejected")
            add_points(ad["owner_id"], ad["post_price"]) # إعادة النقاط
            await query.message.reply_text(f"تم رفض الإعلان وإرجاع النقاط.")
            try: await telegram_app.bot.send_message(ad["owner_id"], f"❌ تم رفض إعلانك وإرجاع {ad['post_price']} نقطة.")
            except: pass


# ==================== تجميع الهاندلرز ====================
ad_create_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ad_category_chosen, pattern="^adcat:")],
    states={
        WAITING_AD_TARGET: [MessageHandler(filters.TEXT | filters.StatusUpdate.USER_SHARED | filters.StatusUpdate.CHAT_SHARED, ad_target_received)],
        WAITING_AD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_desc_received)],
        WAITING_AD_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_reward_received)],
        WAITING_AD_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_quantity_received)],
        WAITING_AD_CONFIRM: [CallbackQueryHandler(ad_confirm_publish, pattern="^(confirm_publish|cancel_publish)$")],
    },
    fallbacks=[],
    per_message=False
)

ad_review_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_review_note, pattern="^adrev:")],
    states={WAITING_AD_REVIEW_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_review_note)]},
    fallbacks=[]
)

telegram_app.add_handler(CommandHandler("start", start))

telegram_app.add_handler(CallbackQueryHandler(publish_ad_start, pattern="^publish_ad$"))
telegram_app.add_handler(CallbackQueryHandler(show_earn_menu, pattern="^earn_menu$"))
telegram_app.add_handler(CallbackQueryHandler(show_earn_category, pattern="^earncat:"))
telegram_app.add_handler(CallbackQueryHandler(show_earn_ad_detail, pattern="^earnad:"))
telegram_app.add_handler(CallbackQueryHandler(ad_owner_action, pattern="^(adpay|adrejc):"))
telegram_app.add_handler(CallbackQueryHandler(user_navigation_click, pattern="^(user_referrals|my_ads|admin_panel_inline)$"))
telegram_app.add_handler(CallbackQueryHandler(admin_actions, pattern="^(adlist:|adnew:)"))

telegram_app.add_handler(ad_create_conv)
telegram_app.add_handler(ad_review_conv)

telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))


# ==================== FastAPI ====================
@app.get("/")
def home():
    return {"status": "Telegram Bot is running smoothly!"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"status": "ok"}

@app.on_event("startup")
async def startup_event():
    init_db()
    await telegram_app.initialize()
    await telegram_app.start()
    railway_url = os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if railway_url:
        await telegram_app.bot.set_webhook(url=f"https://{railway_url}/webhook")

@app.on_event("shutdown")
async def shutdown_event():
    await telegram_app.stop()
    await telegram_app.shutdown()
