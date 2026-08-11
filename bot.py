import os
import re
import math
import logging
import secrets
import traceback
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButtonRequestUsers, KeyboardButtonRequestChat, ChatAdministratorRights,
    LabeledPrice, BotCommand, MenuButtonCommands, MessageOriginChannel
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, ChatMemberHandler, ContextTypes, filters, ApplicationHandlerStop
)

import db
import config
from config import ADMIN_IDS, is_admin

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

if not config.BOT_TOKEN:
    raise RuntimeError("لازم تضيف متغير البيئة BOT_TOKEN قبل تشغيل البوت")

telegram_app = ApplicationBuilder().token(config.BOT_TOKEN).build()
app = FastAPI()


# ==================== لوحة المفاتيح السفلية الرئيسية ====================

def main_reply_keyboard():
    return config.main_reply_keyboard()


def back_kb(cb="back_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=cb)]])


def fmt_lira(n):
    return f"{n:,}".replace(",", ",")


async def send_step(update, context, text, reply_markup=None, key="step"):
    """
    يرسل رسالة جديدة أول مرة فقط، وبعدها يعدّل نفس الرسالة (edit) بدل إرسال رسالة جديدة
    في كل خطوة — يقلّل تكديس الرسائل بالمحادثة. لازم تنادي clear_step(context, key) لما تخلص الخطوات.
    """
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get(f"stepmsg:{key}")
    if msg_id:
        try:
            await telegram_app.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=reply_markup
            )
            return
        except Exception:
            pass  # الرسالة انحذفت أو ما قدرنا نعدلها — نرسل وحدة جديدة بالأسفل
    if update.callback_query:
        sent = await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        sent = await update.message.reply_text(text, reply_markup=reply_markup)
    context.user_data[f"stepmsg:{key}"] = sent.message_id


def clear_step(context, key="step"):
    context.user_data.pop(f"stepmsg:{key}", None)


async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يمنع أي شخص غير أدمن من استخدام البوت وقت ما يكون موقوف مؤقتاً."""
    user = update.effective_user
    if not user or config.is_admin(user.id):
        return
    settings = db.get_settings()
    if settings.get("bot_enabled", 1):
        return
    tpl = db.get_template("maintenance_msg")
    text = tpl["content"] if tpl else "🛠️ البوت متوقف مؤقتاً للصيانة، حاول لاحقاً."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(text)
    raise ApplicationHandlerStop


# ==================== /start ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    ref_by = None
    admin_link_code = None
    cheque_code = None
    if args:
        arg = args[0]
        if arg.startswith("arl_"):
            admin_link_code = arg[4:]
        elif arg.startswith("cheque_"):
            cheque_code = arg[len("cheque_"):]
        else:
            try:
                ref_by = int(arg)
                if ref_by == user.id:
                    ref_by = None
            except ValueError:
                ref_by = None

    is_new = db.ensure_user(user.id, user.username, user.full_name, ref_by=ref_by)

    if cheque_code:
        await start_cheque_redeem(update, context, cheque_code)
        return

    # بوابة الاشتراك الإجباري (إذا مفعّلة من الأدمن)
    if not config.is_admin(user.id):
        settings = db.get_settings()
        if settings.get("mandatory_gate_enabled") and settings.get("mandatory_sub_channel"):
            is_member = await check_is_member(settings["mandatory_sub_channel"], user.id)
            if not is_member:
                context.user_data["pending_start"] = {
                    "ref_by": ref_by, "admin_link_code": admin_link_code, "is_new": is_new
                }
                await send_mandatory_gate(update, context, settings["mandatory_sub_channel"])
                return

    await finish_start(update, context, is_new, ref_by, admin_link_code)


async def check_is_member(chat_id, user_id):
    try:
        member = await telegram_app.bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def send_mandatory_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, channel):
    tpl = db.get_template("mandatory_gate_msg")
    text = tpl["content"] if tpl else "📌 عليك الاشتراك بالقناة/المجموعة التالية أولاً."
    try:
        chat = await telegram_app.bot.get_chat(channel)
        link = f"https://t.me/{chat.username}" if chat.username else (getattr(chat, "invite_link", None))
    except Exception:
        link = None
    keyboard = []
    if link:
        keyboard.append([InlineKeyboardButton("🔗 فتح الرابط", url=link)])
    keyboard.append([InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="gate_check_sub")])
    target = update.effective_message
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def gate_check_sub_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    settings = db.get_settings()
    channel = settings.get("mandatory_sub_channel")
    is_member = await check_is_member(channel, update.effective_user.id) if channel else True
    if not is_member:
        await query.message.reply_text("❌ لسا ما اشتركت. اشترك ثم اضغط الزر مجدداً.")
        return
    pending = context.user_data.pop("pending_start", {})
    await finish_start(update, context, pending.get("is_new", False), pending.get("ref_by"), pending.get("admin_link_code"))


async def finish_start(update: Update, context: ContextTypes.DEFAULT_TYPE, is_new, ref_by, admin_link_code):
    user = update.effective_user

    if is_new and ref_by:
        settings = db.get_settings()
        referred = db.get_user(ref_by)
        if referred:
            reward = settings["ref_reward_regular"]
            db.add_balance(ref_by, reward, kind="referral", note=f"إحالة مستخدم {user.id}")
            db.set_field("users", "user_id", ref_by, "referrals_count", referred["referrals_count"] + 1)
            db.set_field("users", "user_id", ref_by, "referral_earnings", referred["referral_earnings"] + reward)
            try:
                await telegram_app.bot.send_message(
                    ref_by, f"🎉 شخص جديد سجّل عبر رابط إحالتك!\n💰 حصلت على {fmt_lira(reward)} ليرة."
                )
            except Exception:
                pass

    if is_new and admin_link_code:
        ok, result = db.use_admin_ref_link(admin_link_code, user.id)
        if ok:
            db.add_balance(user.id, result, kind="admin_ref_link", note=f"رابط إحالة خاص {admin_link_code}")
            await update.effective_message.reply_text(f"🎁 مرحباً! حصلت على {fmt_lira(result)} ليرة من رابط الدعوة الخاص.")

    await send_welcome(update, context)
    await send_join_group_button(update, context)

    if is_new:
        for admin_id in ADMIN_IDS:
            try:
                await telegram_app.bot.send_message(
                    admin_id, f"👤 مستخدم جديد: {user.username or user.full_name} (ID: {user.id})"
                )
            except Exception:
                pass


async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.username or user.full_name
    tpl = db.get_template("welcome_msg")
    raw = tpl["content"] if tpl else "👋 أهلاً بك يا {name} في PR GRAM!"
    text = raw.replace("{name}", name)
    target = update.effective_message
    await target.reply_text(text, reply_markup=main_reply_keyboard())


async def send_join_group_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 انضم للمجموعة الرئيسية", callback_data="show_main_group_promo")]])
    await update.effective_message.reply_text("👇", reply_markup=keyboard)


async def show_main_group_promo_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tpl = db.get_template("main_group_promo_msg")
    text = tpl["content"] if tpl else "📢 انضم لمجموعتنا الرئيسية!"
    await query.message.reply_text(text)


# ==================== قسم "📢 الترويج" ====================

async def show_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    # نظّف أي بقايا من محاولة نشر سابقة ما اكتملت، حتى ما تختلط ببعض
    context.user_data.pop("draft_task", None)
    context.user_data["awaiting"] = None
    clear_step(context, key="price_flow")
    u = db.get_user(user.id)
    text = f"📊 ماذا تريد أن تروّج؟\n💳 رصيدك: {fmt_lira(u['balance'])} ليرة"
    keyboard = [
        [InlineKeyboardButton("🔮 مجموعة", callback_data="promo_cat:group"),
         InlineKeyboardButton("📢 قناة", callback_data="promo_cat:channel")],
        [InlineKeyboardButton("🤖 بوت", callback_data="promo_cat:bot"),
         InlineKeyboardButton("👁️ منشور", callback_data="promo_cat:post")],
        [InlineKeyboardButton("🔥 التفاعلات", callback_data="promo_cat:interaction"),
         InlineKeyboardButton("⚡ شحن بريميوم", callback_data="promo_cat:premium")],
        [InlineKeyboardButton("⚙️ إعدادات المهام التلقائية", callback_data="promo_auto_settings")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_main"),
         InlineKeyboardButton("💼 مهامي", callback_data="my_tasks:0")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if edit:
        await update.callback_query.message.reply_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def promo_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":")[1]

    if cat == "premium":
        await query.message.reply_text(
            "⚡ شحن البريميوم قيد التفعيل حالياً — تواصل مع الإدارة لتفعيل هذه الخدمة يدوياً.",
            reply_markup=back_kb("promo_menu")
        )
        return

    draft = context.user_data.get("draft_task", {})
    draft["category"] = cat
    context.user_data["draft_task"] = draft

    if cat == "bot":
        keyboard = [
            [InlineKeyboardButton(db.BOT_TYPES["normal"], callback_data="bot_type_pick:normal"),
             InlineKeyboardButton(db.BOT_TYPES["conditions"], callback_data="bot_type_pick:conditions")],
            [InlineKeyboardButton(db.BOT_TYPES["webapp"], callback_data="bot_type_pick:webapp")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
        ]
        await query.message.reply_text(
            "🤖 اختر نوع مهمة البوت:\n"
            "▶️ بوت عادي — يكفي الضغط على Start فقط.\n"
            "📝 بوت مع شروط إضافية — يحتاج المستخدم ينفذ خطوات إضافية تحددها أنت.\n"
            "🌐 بوت Web App — يفتح المستخدم تطبيق مصغّر داخل البوت.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await ask_task_price(update, context, cat)


async def bot_type_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_type = query.data.split(":")[1]
    draft = context.user_data.get("draft_task", {"category": "bot"})
    draft["bot_type"] = bot_type
    context.user_data["draft_task"] = draft

    if bot_type == "conditions":
        context.user_data["awaiting"] = "bot_conditions_text"
        await query.message.reply_text(
            "💬 صف شروط المهمة. ما المطلوب من المنفذ بعد تشغيل البوت؟\n"
            "🚫 ممنوع طلب اشتراك بقنوات راعية أو بيانات حساسة (بحد أقصى 200 حرف):"
        )
        return

    await ask_task_price(update, context, "bot")


async def handle_bot_conditions_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()[:200]
    draft = context.user_data.get("draft_task", {})
    draft["extra_conditions"] = text
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = None
    await ask_task_price(update, context, "bot")


async def ask_task_price(update: Update, context: ContextTypes.DEFAULT_TYPE, cat):
    draft = context.user_data.get("draft_task", {"category": cat})
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = "task_price"

    if cat == "bot":
        mn, sug, mx = db.get_bot_type_price(draft.get("bot_type", "normal"))
        unit_noun = "تنفيذ واحد مع البوت"
    else:
        mn, sug, mx = db.get_category_price(cat)
        unit_noun = {
            "group": "مشترك واحد/انضمام إلى مجموعة", "channel": "مشترك واحد",
            "post": "مشاهدة واحدة", "interaction": "تفاعل واحد",
        }[cat]

    sug_line = f"💡 ملاحظة: الطلب مرتفع حالياً، لذا لضمان الظهور بالصفحات الأولى اجعل السعر لا يقل عن {fmt_lira(sug)} ليرة.\n" if sug else ""
    text = (
        f"حدد سعر {unit_noun} لإنشاء مهمة، يجب أن يكون لديك رصيد ليرة كافٍ\n"
        f"🔴 السعر الأدنى للوحدة — {fmt_lira(mn)} ليرة\n"
        f"{sug_line}"
        f"📝 أدخل السعر لـ {unit_noun}:"
    )
    target_msg = update.callback_query.message if update.callback_query else update.message
    await target_msg.reply_text(text, reply_markup=back_kb("promo_menu"))


async def handle_task_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    draft = context.user_data.get("draft_task", {})
    cat = draft.get("category")
    if not text.isdigit():
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح.")
        return
    price = int(text)
    if cat == "bot":
        mn, sug, mx = db.get_bot_type_price(draft.get("bot_type", "normal"))
    else:
        mn, sug, mx = db.get_category_price(cat)
    if price < mn:
        await update.message.reply_text(f"❌ السعر يجب أن يكون {fmt_lira(mn)} ليرة على الأقل. حاول مجدداً:")
        return
    if mx and price > mx:
        await update.message.reply_text(f"❌ السعر يجب ألا يتجاوز {fmt_lira(mx)} ليرة. حاول مجدداً:")
        return

    draft["unit_price"] = price
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = "task_qty"

    settings = db.get_settings()
    commission = settings["commission_percent"]
    user = db.get_user(update.effective_user.id)
    no_commission = user["no_commission_until"] and datetime.strptime(user["no_commission_until"], "%Y-%m-%d %H:%M:%S") > datetime.now()
    effective_commission = 0 if no_commission else commission
    unit_cost_with_commission = math.ceil(price * (1 + effective_commission / 100))
    max_units = user["balance"] // unit_cost_with_commission if unit_cost_with_commission else 0

    text = (
        f"🔔 تنبيه: سيتم احتساب عمولة {effective_commission}% عند إنشاء المهمة.\n"
        f"💵 سعر الوحدة الواحدة: {fmt_lira(price)} ليرة\n"
        f"💰 رصيدك: {fmt_lira(user['balance'])} ليرة\n"
        f"📝 أدخل عدد الوحدات، أو اختر:"
    )
    default_qty = min(10, max_units) if max_units > 0 else 0
    keyboard = []
    if default_qty > 0:
        keyboard.append([InlineKeyboardButton(f"{default_qty}", callback_data=f"qty_pick:{default_qty}")])
    if max_units > 0:
        keyboard.append([InlineKeyboardButton(f"{max_units} (الحد الأقصى لرصيدك)", callback_data=f"qty_pick:{max_units}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def qty_picked_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    qty = int(query.data.split(":")[1])
    await process_task_quantity(update, context, qty, is_callback=True)


async def handle_task_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح أكبر من صفر.")
        return
    await process_task_quantity(update, context, int(text), is_callback=False)


async def process_task_quantity(update, context, qty, is_callback):
    draft = context.user_data.get("draft_task", {})
    price = draft["unit_price"]
    cat = draft["category"]
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    settings = db.get_settings()

    no_commission = user["no_commission_until"] and datetime.strptime(user["no_commission_until"], "%Y-%m-%d %H:%M:%S") > datetime.now()
    effective_commission = 0 if no_commission else settings["commission_percent"]
    total_lira = math.ceil(price * qty * (1 + effective_commission / 100))

    if is_callback:
        msg_send = update.callback_query.message.reply_text
    else:
        msg_send = update.message.reply_text

    draft["target_count"] = qty
    draft["total_cost_lira"] = total_lira
    draft["commission_percent"] = effective_commission
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = None

    if config.is_admin(user_id):
        draft["admin_free"] = True

    if draft.get("admin_free"):
        draft["payment_method"] = "admin_free"
        draft["total_cost_lira"] = 0
        draft["commission_percent"] = 0
        context.user_data["draft_task"] = draft
        await ask_target_chat(update, context)
        return

    stars_amount = max(1, math.ceil((price * qty) / settings["stars_to_lira_rate"]))

    text = (
        "اختر طريقة الدفع لمهمتك:\n"
        f"📢 تنطبق عمولة {settings['commission_percent']}% على مدفوعات الليرة.\n"
        f"💡 يمكنك التبرع بـ {settings['stars_min_no_commission']} ⭐ على الأقل — وسيتم تعطيل العمولة لمدة "
        f"{settings['stars_no_commission_hours']} ساعة.\n"
        f"كل {settings['stars_min_no_commission']} ⭐ إضافية تمدد فترة بدون عمولة {settings['stars_no_commission_hours']} ساعة أخرى."
    )
    keyboard = [
        [InlineKeyboardButton(f"💎 {fmt_lira(total_lira)} ليرة", callback_data="pay_method:lira")],
        [InlineKeyboardButton(f"⭐ {stars_amount} Telegram Stars (-{settings['commission_percent']}%)", callback_data="pay_method:stars")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
    ]
    await msg_send(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== طريقة الدفع ====================

async def pay_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.split(":")[1]
    draft = context.user_data.get("draft_task", {})
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if method == "lira":
        total = draft["total_cost_lira"]
        if total > user["balance"]:
            await query.message.reply_text("❌ رصيدك غير كافٍ لهذه العملية.")
            return
        db.add_balance(user_id, -total, kind="task_payment", note="دفع مهمة ترويج (ليرة)")
        draft["payment_method"] = "lira"
        context.user_data["draft_task"] = draft
        await ask_target_chat(update, context)
        return

    # stars payment
    settings = db.get_settings()
    price, qty = draft["unit_price"], draft["target_count"]
    stars_amount = max(1, math.ceil((price * qty) / settings["stars_to_lira_rate"]))
    draft["payment_method"] = "stars"
    draft["stars_amount"] = stars_amount
    context.user_data["draft_task"] = draft

    payload = f"task_create:{secrets.token_hex(6)}"
    context.user_data["stars_payload"] = payload

    await telegram_app.bot.send_invoice(
        chat_id=user_id,
        title="نشر مهمة ترويج على PR GRAM",
        description=f"دفع {stars_amount} ⭐ لنشر مهمة {db.CATEGORY_LABELS.get(draft['category'], draft['category'])} بدون عمولة.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("Stars Payment", stars_amount)],
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    stars_amount = update.message.successful_payment.total_amount
    user_id = update.effective_user.id
    purpose = payload.split(":")[0] if ":" in payload else "unknown"

    db_conn = db.get_conn()
    c = db_conn.cursor()
    c.execute(
        "INSERT INTO stars_payments (user_id, purpose, payload, stars_amount, status, created_at, paid_at) "
        "VALUES (?,?,?,?, 'paid', ?, ?)",
        (user_id, purpose, payload, stars_amount, db.now_str(), db.now_str())
    )
    db_conn.commit(); db_conn.close()

    payer = update.effective_user
    purpose_label = {"topup": "تعبئة رصيد", "task_create": "دفع مهمة ترويج"}.get(purpose, purpose)
    for admin_id in ADMIN_IDS:
        try:
            await telegram_app.bot.send_message(
                admin_id,
                f"⭐ دفعة نجوم جديدة!\n"
                f"👤 من: {payer.username or payer.full_name} (ID: {payer.id})\n"
                f"💰 المبلغ: {stars_amount} ⭐\n"
                f"📌 الغرض: {purpose_label}"
            )
        except Exception:
            pass

    settings = db.get_settings()
    if stars_amount >= settings["stars_min_no_commission"]:
        extra_periods = stars_amount // settings["stars_min_no_commission"]
        hours = extra_periods * settings["stars_no_commission_hours"]
        current = db.get_user(user_id)
        base = datetime.now()
        if current["no_commission_until"]:
            existing = datetime.strptime(current["no_commission_until"], "%Y-%m-%d %H:%M:%S")
            if existing > base:
                base = existing
        new_until = base + timedelta(hours=hours)
        db.set_field("users", "user_id", user_id, "no_commission_until", new_until.strftime("%Y-%m-%d %H:%M:%S"))

    if purpose == "topup":
        lira_amount = stars_amount * settings["stars_to_lira_rate"]
        db.add_balance(user_id, lira_amount, kind="stars_topup", note=f"تعبئة رصيد عبر {stars_amount} نجمة")
        u = db.get_user(user_id)
        await update.message.reply_text(
            f"✅ تم استلام {stars_amount} ⭐ وتحويلها إلى {fmt_lira(lira_amount)} ليرة.\n💰 رصيدك الحالي: {fmt_lira(u['balance'])} ليرة"
        )
        return

    draft = context.user_data.get("draft_task")
    if purpose == "task_create" and draft:
        draft["payment_method"] = "stars"
        context.user_data["draft_task"] = draft
        await update.message.reply_text(f"✅ تم استلام الدفع ({stars_amount} ⭐). الآن حدد المحادثة المستهدفة:")
        await ask_target_chat(update, context)
    else:
        await update.message.reply_text(f"✅ تم استلام دفعتك ({stars_amount} ⭐) بنجاح، شكراً لك!")


# ==================== اختيار المحادثة المستهدفة ====================

async def ask_target_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft_task", {})
    cat = draft["category"]

    if cat == "bot":
        btn = KeyboardButton(
            "🤖 اختر بوتاً",
            request_users=KeyboardButtonRequestUsers(request_id=1, user_is_bot=True, request_username=True)
        )
        markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        text = "اضغط على الزر لاختيار البوت المستهدف من قائمة محادثاتك."
    elif cat == "post":
        markup = main_reply_keyboard()
        text = "أرسل رابط المنشور أو أعد توجيه منشور من القناة."
        context.user_data["awaiting"] = "post_forward"
    elif cat == "interaction":
        markup = main_reply_keyboard()
        text = "📩 أرسل رابط المنشور الذي تريد إضافة تفاعلات إليه."
        context.user_data["awaiting"] = "interaction_link"
    else:  # group / channel
        required_rights = ChatAdministratorRights(
            is_anonymous=False, can_manage_chat=True, can_delete_messages=False,
            can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False,
            can_change_info=False, can_invite_users=True, can_post_messages=True,
            can_edit_messages=False, can_pin_messages=False,
        )
        btn = KeyboardButton(
            "📢 اختيار قناة/مجموعة",
            request_chat=KeyboardButtonRequestChat(
                request_id=2, chat_is_channel=(cat == "channel"),
                bot_administrator_rights=required_rights
            )
        )
        markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        text = (
            "اضغط على الزر لاختيار المحادثة التي تريد الترويج لها.\n"
            "⚠️ يجب أن يكون البوت مشرفاً في المحادثة/القناة."
        )

    target_msg = update.callback_query.message if update.callback_query else update.message
    await target_msg.reply_text(text, reply_markup=markup)


async def got_shared_chat_or_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # مشاركة مستخدم لإرسال شيك له (مو جزء من تدفق إنشاء مهمة، فما بيحتاج draft_task)
    if getattr(msg, "users_shared", None) and getattr(msg.users_shared, "request_id", None) == 3:
        await handle_cheque_send_user(update, context, msg.users_shared.users[0])
        return

    draft = context.user_data.get("draft_task")
    if not draft:
        await update.message.reply_text(
            "⚠️ يبدو إن محاولة إنشاء المهمة انتهت أو اتلغت. اضغط «📢 الترويج» وابدأ من جديد.",
            reply_markup=main_reply_keyboard()
        )
        return

    if getattr(msg, "chat_shared", None):
        chat_id = msg.chat_shared.chat_id
        draft["target_chat_id"] = str(chat_id)
        try:
            chat = await telegram_app.bot.get_chat(chat_id)
            draft["target_chat_title"] = chat.title
        except Exception:
            draft["target_chat_title"] = "المحادثة المختارة"
            chat = None

        is_admin_bot = await is_bot_admin_of(str(chat_id))
        if not is_admin_bot:
            await msg.reply_text("جاري التحقق من الصلاحيات...", reply_markup=main_reply_keyboard())
            if chat is not None and chat.username:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 الذهاب للمحادثة", url=f"https://t.me/{chat.username}")],
                    [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
                ])
                await msg.reply_text(
                    "⚠️ البوت لسا مو مشرف بهاي المحادثة. افتحها من الزر تحت، ضيف البوت كمشرف يدوياً "
                    "(Add Admin)، وبعدين ارجع اضغط «📢 الترويج» وأعد اختيار نفس المحادثة.",
                    reply_markup=keyboard
                )
            else:
                await msg.reply_text(
                    "⚠️ البوت لسا مو مشرف بهاي المحادثة، وما قدرنا نولّد رابط تلقائي لأنها خاصة.\n"
                    "روح لإعدادات المحادثة يدوياً وضيف البوت كمشرف (Add Admin)، وبعدين أعد اختيار نفس المحادثة.",
                    reply_markup=back_kb("promo_menu")
                )
            return

        # توليد رابط صحيح دائماً: يوزر عام، وإلا رابط الدعوة الحالي، وإلا ننشئ رابط دعوة جديد
        link = None
        if chat is not None and chat.username:
            link = f"https://t.me/{chat.username}"
        else:
            link = getattr(chat, "invite_link", None) if chat is not None else None
            if not link:
                try:
                    link = await telegram_app.bot.export_chat_invite_link(chat_id)
                except Exception as e:
                    logger.error(f"export_chat_invite_link failed for {chat_id}: {e}")
                    link = None
        draft["link"] = link

        if not link:
            await msg.reply_text(
                "⚠️ ما قدرت أولّد رابط دعوة لهذه المحادثة (تأكد إن البوت مشرف وعنده صلاحية دعوة مستخدمين عبر رابط). "
                "أعد اختيار المحادثة بعد تعديل الصلاحيات.",
                reply_markup=main_reply_keyboard()
            )
            return

        context.user_data["draft_task"] = draft
        keyboard = [
            [InlineKeyboardButton("🚀 نشر المهمة", callback_data="launch_task")],
            [InlineKeyboardButton("➕ إنشاء رابط بطلب انضمام", callback_data="task_join_request")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
        ]
        await msg.reply_text(f"✅ تم اختيار: {draft['target_chat_title']}", reply_markup=main_reply_keyboard())
        await msg.reply_text("جاهز للإطلاق:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if getattr(msg, "users_shared", None):
        shared_user = msg.users_shared.users[0]
        bot_user_id = shared_user.user_id
        # الطريقة الموثوقة: يوزر البوت من نفس بيانات الاختيار (بفضل request_username=True).
        # get_chat() بالـ ID غالباً بيفشل لبوت البوت ما تكلم معه من قبل، فما نعتمد عليها إلا كـ fallback.
        username = getattr(shared_user, "username", None)
        if not username:
            try:
                bot_chat = await telegram_app.bot.get_chat(bot_user_id)
                username = bot_chat.username
            except Exception as e:
                logger.error(f"get_chat fallback failed for bot {bot_user_id}: {e}")
                username = None

        draft["bot_username"] = username
        draft["link"] = f"https://t.me/{username}" if username else None
        context.user_data["draft_task"] = draft

        if not draft["link"]:
            await msg.reply_text(
                "❌ ما قدرت أجيب يوزر هالبوت. تأكد إنه بوت عام وله @username صحيح، وجرب تختاره من جديد.",
                reply_markup=main_reply_keyboard()
            )
            return

        existing = db.get_task_by_owner_and_bot(update.effective_user.id, username)
        await msg.reply_text(f"تم إضافة البوت @{username}.", reply_markup=main_reply_keyboard())

        if existing:
            keyboard = [
                [InlineKeyboardButton("✍️ تعديل المهمة", callback_data=f"task_detail:{existing['task_id']}:0")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
            ]
            await msg.reply_text(
                "❌ توجد مهمة لهذا البوت بالفعل! يمكنك الانتقال إليها وإضافة تنفيذات جديدة.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        keyboard = [
            [InlineKeyboardButton("🚀 نشر المهمة", callback_data="launch_task")],
            [InlineKeyboardButton("➕ إضافة رابط إحالة", callback_data="task_add_ref_link")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
        ]
        await msg.reply_text("اختر الإجراء التالي:", reply_markup=InlineKeyboardMarkup(keyboard))


async def is_bot_admin_of(chat_id):
    try:
        me = await telegram_app.bot.get_me()
        member = await telegram_app.bot.get_chat_member(chat_id, me.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"admin check failed: {e}")
        return False


async def handle_post_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft_task")
    if not draft:
        await update.message.reply_text("⚠️ محاولة إنشاء المهمة انتهت. اضغط «📢 الترويج» وابدأ من جديد.")
        return
    msg = update.message
    # تليجرام استبدل forward_from_chat/forward_from_message_id بكائن forward_origin موحّد
    origin = msg.forward_origin
    fwd_chat = origin.chat if isinstance(origin, MessageOriginChannel) else None
    fwd_message_id = origin.message_id if isinstance(origin, MessageOriginChannel) else None

    if not fwd_chat or fwd_chat.type != "channel":
        if msg.text and msg.text.startswith("http"):
            draft["link"] = msg.text.strip()
            draft["target_chat_title"] = "منشور (رابط)"
            context.user_data["draft_task"] = draft
            context.user_data["awaiting"] = None
            await launch_task_now(update, context)
            return
        await update.message.reply_text("❌ لقد أعدت توجيه منشور ليس من قناة! حاول مجدداً.")
        return

    is_admin_bot = await is_bot_admin_of(str(fwd_chat.id))
    if not is_admin_bot:
        if fwd_chat.username:
            keyboard = [[InlineKeyboardButton("➕ إضافة البوت إلى القناة", url=f"https://t.me/{fwd_chat.username}")],
                        [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")]]
            await update.message.reply_text("❌ البوت ليس مشرفاً في هذه المحادثة! أضفه ثم أعد المحاولة.",
                                             reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(
                "❌ البوت ليس مشرفاً في هذه القناة، وما قدرنا نولّد رابط تلقائي لأنها خاصة.\n"
                "روح لإعدادات القناة يدوياً وضيف البوت كمشرف، وبعدين أعد توجيه المنشور من جديد.",
                reply_markup=back_kb("promo_menu")
            )
        return

    draft["target_chat_id"] = str(fwd_chat.id)
    draft["target_chat_title"] = fwd_chat.title
    draft["source_message_id"] = fwd_message_id
    draft["link"] = f"https://t.me/{fwd_chat.username}/{fwd_message_id}" if fwd_chat.username else None
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = None
    await launch_task_now(update, context)


async def handle_interaction_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft_task")
    if not draft:
        await update.message.reply_text("⚠️ محاولة إنشاء المهمة انتهت. اضغط «📢 الترويج» وابدأ من جديد.")
        return
    link = update.message.text.strip()
    draft["link"] = link
    draft["target_chat_title"] = "منشور تفاعل"
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = None

    keyboard = [
        [InlineKeyboardButton("🎲 تفاعلات عشوائية", callback_data="interaction_mode:random")],
        [InlineKeyboardButton("⭐ تفاعل ثابت", callback_data="interaction_mode:fixed")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
    ]
    await update.message.reply_text("اختر نوع التفاعل المطلوب:", reply_markup=InlineKeyboardMarkup(keyboard))


async def interaction_mode_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":")[1]
    draft = context.user_data.get("draft_task", {})
    draft["interaction_mode"] = mode
    context.user_data["draft_task"] = draft
    await launch_task_now(update, context)


async def task_join_request_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "💡 هل أنت متأكد أنك تريد إنشاء رابط بطلبات الانضمام؟\n"
        "لن يُمنح الوصول إلى قناتك إلا بعد الموافقة على كل طلب، وسيتم احتساب الدفع بعد الموافقة.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، تأكيد", callback_data="confirm_join_request")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")],
        ])
    )


async def confirm_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("draft_task", {})
    draft["join_request_mode"] = 1
    # لازم رابط بطلب انضمام مخصص، مش رابط الدعوة العادي
    try:
        jr_link = await telegram_app.bot.create_chat_invite_link(
            draft["target_chat_id"], creates_join_request=True, name=f"PR GRAM task"
        )
        draft["link"] = jr_link.invite_link
    except Exception as e:
        logger.error(f"create_chat_invite_link (join request) failed: {e}")
        await query.message.reply_text("⚠️ ما قدرت أنشئ رابط بطلب انضمام (تأكد إن البوت عنده صلاحية دعوة مستخدمين). رح يتم النشر برابط الدعوة العادي بدالها.")
    context.user_data["draft_task"] = draft
    await launch_task_now(update, context)


async def launch_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await launch_task_now(update, context)


async def launch_task_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft_task")
    if not draft:
        target_msg = update.callback_query.message if update.callback_query else update.message
        await target_msg.reply_text("⚠️ محاولة إنشاء المهمة انتهت أو اتلغت. اضغط «📢 الترويج» وابدأ من جديد.")
        return
    user = update.effective_user
    cat = draft["category"]
    requires_review = 1 if cat in db.MANUAL_VERIFIABLE else 0
    target_msg = update.callback_query.message if update.callback_query else update.message

    if cat in ("bot", "interaction", "group", "channel") and not draft.get("link"):
        await target_msg.reply_text(
            "❌ ما فيه رابط صالح لهاي المهمة، فما رح تنشر (حتى ما تظهر معطّلة للمستخدمين). "
            "اضغط «📢 الترويج» وأعد اختيار الهدف من جديد."
        )
        return

    try:
        task_id = db.create_task(
            owner_id=user.id, owner_username=user.username or user.full_name, category=cat,
            unit_price=draft["unit_price"], target_count=draft["target_count"],
            total_cost=draft.get("total_cost_lira", 0), payment_method=draft.get("payment_method", "lira"),
            commission_percent=draft.get("commission_percent", 0), target_chat_id=draft.get("target_chat_id"),
            target_chat_title=draft.get("target_chat_title"), link=draft.get("link"),
            bot_username=draft.get("bot_username"), extra_conditions=draft.get("extra_conditions"),
            requires_review=requires_review, join_request_mode=draft.get("join_request_mode", 0),
            interaction_mode=draft.get("interaction_mode"), fixed_emoji=draft.get("fixed_emoji"),
            bot_type=draft.get("bot_type"), source_message_id=draft.get("source_message_id"),
            referral_link=draft.get("referral_link"),
        )
    except Exception as e:
        logger.error(f"launch_task_now failed: {e} | draft={draft}")
        await target_msg.reply_text(
            "❌ صار خطأ أثناء نشر المهمة (بيانات ناقصة على الأغلب). لم يُخصم أي رصيد.\n"
            "اضغط «📢 الترويج» وأعد المحاولة من البداية بدون ما ترجع بمنتصف الخطوات."
        )
        return

    context.user_data.pop("draft_task", None)
    context.user_data["awaiting"] = None

    if draft.get("payment_method") == "admin_free":
        summary = f"✅ تم نشر المهمة № {task_id} مجاناً بصلاحية الأدمن.\nتابع أخبارنا للمزيد."
    else:
        cost_text = f"{fmt_lira(draft.get('total_cost_lira', 0))} ليرة" if draft.get("payment_method") == "lira" else f"{draft.get('stars_amount', 0)} ⭐"
        tpl = db.get_template("task_published_msg")
        raw = tpl["content"] if tpl else "✅ تم نشر المهمة № {task_id}.\nتم خصم {cost} من رصيدك.\nتابع أخبارنا للمزيد."
        summary = raw.replace("{task_id}", str(task_id)).replace("{cost}", cost_text)
    await target_msg.reply_text(
        summary,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✍️ تعديل المهمة", callback_data=f"task_detail:{task_id}:0")]])
    )


async def task_add_ref_link_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "task_ref_link"
    tpl = db.get_template("bot_referral_prompt_msg")
    text = tpl["content"] if tpl else "أرسل رابط الإحالة لبوتك:"
    await query.message.reply_text(text)


async def handle_task_ref_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft_task")
    if not draft:
        await update.message.reply_text("⚠️ محاولة إنشاء المهمة انتهت. اضغط «📢 الترويج» وابدأ من جديد.")
        return
    link = update.message.text.strip()
    expected_username = draft.get("bot_username")

    # استخراج اسم البوت من الرابط المُرسل (يدعم t.me/username?start=... و /username فقط)
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", link)
    sent_username = m.group(1) if m else None

    if not sent_username or not expected_username or sent_username.lower() != expected_username.lower():
        tpl = db.get_template("bot_referral_invalid_msg")
        raw = tpl["content"] if tpl else "❌ الرابط المرسل غير صحيح.\nتأكد من أنه يخص البوت الذي اخترته.\n@{bot_username}: يجب أن يحتوي الرابط على اسم المستخدم."
        await update.message.reply_text(raw.replace("{bot_username}", expected_username or "?"))
        return

    draft["referral_link"] = link
    context.user_data["draft_task"] = draft
    context.user_data["awaiting"] = None
    await launch_task_now(update, context)


# ==================== "💼 مهامي" وإدارتها ====================

async def show_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    tasks = db.get_tasks_by_owner(user_id)
    if not tasks:
        await query.message.reply_text("📋 ما نشرت أي مهمة لهلق. اضغط «📢 الترويج» للبدء.", reply_markup=back_kb("promo_menu"))
        return
    per_page = 8
    start_i = page * per_page
    page_tasks = tasks[start_i:start_i + per_page]
    status_emoji = {"active": "🟢", "completed": "✅", "paused": "⏸️", "deleted": "🔴"}
    keyboard = []
    for t in page_tasks:
        emoji = status_emoji.get(t["status"], "🟡")
        cat_label = db.CATEGORY_LABELS.get(t['category'], t['category'])
        if t["category"] == "bot":
            cat_label += f" ({db.BOT_TYPES.get(t['bot_type'], 'بلا نوع!')})"
        label = f"{emoji} {cat_label} #{t['task_id']} — {t['current_count']}/{t['target_count']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"task_detail:{t['task_id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"my_tasks:{page-1}"))
    if start_i + per_page < len(tasks):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"my_tasks:{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="promo_menu")])
    await query.message.reply_text("هنا تدير مهامك:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_task_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, task_id_s, page_s = query.data.split(":")
    task_id, page = int(task_id_s), int(page_s)
    t = db.get_task(task_id)
    if not t or t["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذه المهمة غير متاحة لك.")
        return

    bot_type_line = f"\n🤖 نوع البوت: {db.BOT_TYPES.get(t['bot_type'], t['bot_type'] or 'غير محدد')}" if t["category"] == "bot" else ""
    text = (
        f"📌 مهمة #{task_id} — {db.CATEGORY_LABELS.get(t['category'], t['category'])}{bot_type_line}\n"
        f"👥 التقدم: {t['current_count']} / {t['target_count']}\n"
        f"💵 سعر الوحدة: {fmt_lira(t['unit_price'])} ليرة | الحالة: {t['status']}\n"
        f"🔗 الرابط: {t['link'] or 'لا يوجد'}\n"
        f"فلاتر الوصول:\n"
        f"• نوع الحساب: {t['filter_account_type']}\n"
        f"• الجمهور: {t['filter_audience']}"
    )
    notif_label = "❌ تعطيل الإشعار" if t["notify_owner"] else "✅ تفعيل الإشعار"
    keyboard = [
        [InlineKeyboardButton("➕ إضافة تنفيذ", callback_data=f"task_add:{task_id}")],
        [InlineKeyboardButton("🔴 حذف", callback_data=f"task_delete:{task_id}:{page}"),
         InlineKeyboardButton("⏸️ إيقاف مؤقت", callback_data=f"task_pause:{task_id}:{page}")],
        [InlineKeyboardButton("✍️ تعديل السعر", callback_data=f"task_editprice:{task_id}")],
        [InlineKeyboardButton(f"✅ نوع الحساب: {t['filter_account_type']}", callback_data=f"task_filter_acc:{task_id}")],
        [InlineKeyboardButton(f"🌐 الجمهور: {t['filter_audience']}", callback_data=f"task_filter_aud:{task_id}")],
        [InlineKeyboardButton(notif_label, callback_data=f"task_notif:{task_id}:{page}")],
        [InlineKeyboardButton("🔎 عرض التنفيذات", callback_data=f"task_completions:{task_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"my_tasks:{page}")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def task_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    parts = data.split(":")
    action = parts[0]
    task_id = int(parts[1])
    t = db.get_task(task_id)
    if not t or t["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ غير مسموح.")
        return

    if action == "task_delete":
        db.delete_task(task_id)
        await query.message.reply_text(f"🔴 تم حذف المهمة #{task_id}.")
    elif action == "task_pause":
        new_status = "paused" if t["status"] == "active" else "active"
        db.set_task_status(task_id, new_status)
        await query.message.reply_text(f"⏸️ الحالة الجديدة للمهمة #{task_id}: {new_status}")
    elif action == "task_notif":
        db.update_task_field(task_id, "notify_owner", 0 if t["notify_owner"] else 1)
        await query.message.reply_text("🔔 تم تحديث إعداد الإشعارات.")
    elif action == "task_filter_acc":
        options = ["all", "premium_only", "no_premium"]
        current = options.index(t["filter_account_type"]) if t["filter_account_type"] in options else 0
        new_val = options[(current + 1) % len(options)]
        db.update_task_field(task_id, "filter_account_type", new_val)
        await query.message.reply_text(f"✅ نوع الحساب المستهدف الآن: {new_val}")
    elif action == "task_filter_aud":
        options = ["all", "new_users_only", "active_users_only"]
        current = options.index(t["filter_audience"]) if t["filter_audience"] in options else 0
        new_val = options[(current + 1) % len(options)]
        db.update_task_field(task_id, "filter_audience", new_val)
        await query.message.reply_text(f"🌐 الجمهور المستهدف الآن: {new_val}")
    elif action == "task_completions":
        conn = db.get_conn(); c = conn.cursor()
        c.execute("SELECT user_id, status FROM task_completions WHERE task_id=? ORDER BY started_at DESC LIMIT 30", (task_id,))
        rows = c.fetchall(); conn.close()
        if not rows:
            await query.message.reply_text("لا يوجد تنفيذات بعد لهذه المهمة.")
        else:
            txt = "\n".join([f"👤 {uid} — {status}" for uid, status in rows])
            await query.message.reply_text(f"🔎 آخر التنفيذات لمهمة #{task_id}:\n\n{txt}")
    elif action == "task_add":
        context.user_data["awaiting"] = f"task_add_units:{task_id}"
        await query.message.reply_text("📝 كم عدد الوحدات الإضافية التي تريد شراءها؟")
    elif action == "task_editprice":
        context.user_data["awaiting"] = f"task_edit_price:{task_id}"
        await query.message.reply_text("✍️ أرسل سعر الوحدة الجديد:")


async def handle_task_add_units(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ رقم غير صالح.")
        return
    extra = int(text)
    t = db.get_task(task_id)
    user = db.get_user(update.effective_user.id)
    settings = db.get_settings()
    cost = math.ceil(t["unit_price"] * extra * (1 + settings["commission_percent"] / 100))
    if cost > user["balance"]:
        await update.message.reply_text(f"❌ رصيدك غير كافٍ. التكلفة المطلوبة: {fmt_lira(cost)} ليرة.")
        context.user_data["awaiting"] = None
        return
    db.add_balance(update.effective_user.id, -cost, kind="task_topup", note=f"إضافة تنفيذ لمهمة #{task_id}")
    db.update_task_field(task_id, "target_count", t["target_count"] + extra)
    if t["status"] == "completed":
        db.set_task_status(task_id, "active")
    context.user_data["awaiting"] = None
    await update.message.reply_text(f"✅ تم إضافة {extra} وحدة للمهمة #{task_id}. تم خصم {fmt_lira(cost)} ليرة.")


async def handle_task_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ رقم غير صالح.")
        return
    db.update_task_field(task_id, "unit_price", int(text))
    context.user_data["awaiting"] = None
    await update.message.reply_text(f"✅ تم تحديث سعر الوحدة للمهمة #{task_id} إلى {fmt_lira(int(text))} ليرة.")


# ==================== مراقبة إلغاء الاشتراك المبكر (chat_member updates) ====================

async def chat_member_watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu:
        return
    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    was_in = old_status in ("member", "administrator", "creator", "restricted")
    now_out = new_status in ("left", "kicked")
    if not (was_in and now_out):
        return

    user_id = cmu.new_chat_member.user.id
    chat_id = cmu.chat.id
    watch = db.get_subscription_watch(user_id, chat_id)
    if not watch:
        return  # ما في مهمة نشطة مرتبطة بهاي المحادثة لهالمستخدم

    expires = datetime.strptime(watch["expires_at"], "%Y-%m-%d %H:%M:%S")
    db.remove_subscription_watch(user_id, chat_id)
    if datetime.now() >= expires:
        return  # خلصت فترة المراقبة أصلاً، مسموح يلغي الاشتراك بدون عقوبة

    task = db.get_task(watch["task_id"])
    fine = task["unit_price"] if task else 0
    db.create_violation(user_id, chat_id, cmu.chat.title, watch["task_id"], fine)

    settings = db.get_settings()
    try:
        await telegram_app.bot.send_message(
            user_id,
            f"⚠️ لاحظنا إنك ألغيت اشتراكك بـ«{cmu.chat.title}» قبل ما تمر {settings['sub_watch_days']} أيام على كسب النقاط منها.\n"
            f"تم إيقافك عن قسم «🗂️ الأرباح» بالكامل لحد ما تحل هالمخالفة."
        )
    except Exception:
        pass


async def show_violations_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, violations):
    text = "🚫 لقد ألغيت الاشتراك بالقنوات/المجموعات التالية قبل مرور فترة المراقبة:\n"
    keyboard = []
    for v in violations:
        text += f"\n• {v['chat_title'] or v['chat_id']} — غرامة {fmt_lira(v['fine_amount'])} ليرة"
        keyboard.append([InlineKeyboardButton(
            f"⚙️ {v['chat_title'] or v['chat_id']}", callback_data=f"viol_open:{v['violation_id']}"
        )])
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def check_earn_violations_and_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرجع True إذا كان محظور وعُرضت له شاشة المخالفات (يوقف الاستمرار)، وإلا False."""
    if config.is_admin(update.effective_user.id):
        return False
    violations = db.get_pending_violations(update.effective_user.id)
    if not violations:
        return False
    await show_violations_screen(update, context, violations)
    return True


async def viol_open_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vid = int(query.data.split(":")[1])
    v = db.get_violation(vid)
    if not v or v["user_id"] != update.effective_user.id or v["status"] != "pending":
        await query.message.reply_text("❌ هذه المخالفة لم تعد متاحة.")
        return
    u = db.get_user(update.effective_user.id)
    text = (
        f"⚙️ مخالفة: {v['chat_title'] or v['chat_id']}\n"
        f"الغرامة: {fmt_lira(v['fine_amount'])} ليرة\n"
        f"رصيدك: {fmt_lira(u['balance'])} ليرة\n\n"
        f"اختر طريقة الحل:"
    )
    keyboard = [
        [InlineKeyboardButton(f"💰 دفع غرامة ({fmt_lira(v['fine_amount'])})", callback_data=f"viol_pay:{vid}")],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data=f"viol_check:{vid}")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def viol_pay_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vid = int(query.data.split(":")[1])
    v = db.get_violation(vid)
    if not v or v["user_id"] != update.effective_user.id or v["status"] != "pending":
        await query.message.reply_text("❌ هذه المخالفة لم تعد متاحة.")
        return
    u = db.get_user(update.effective_user.id)
    if u["balance"] < v["fine_amount"]:
        keyboard = [[InlineKeyboardButton("🛒 شراء نقاط", callback_data="topup_balance")]]
        await query.message.reply_text(
            f"❌ رصيدك غير كافٍ لدفع الغرامة ({fmt_lira(v['fine_amount'])} ليرة).",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    db.add_balance(update.effective_user.id, -v["fine_amount"], kind="violation_fine", note=f"غرامة مخالفة #{vid}")
    db.resolve_violation(vid, "paid")
    await query.message.reply_text("✅ تم دفع الغرامة. تحقق إذا بقيت عندك مخالفات تانية من «🗂️ الأرباح».")


async def viol_check_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vid = int(query.data.split(":")[1])
    v = db.get_violation(vid)
    if not v or v["user_id"] != update.effective_user.id or v["status"] != "pending":
        await query.message.reply_text("❌ هذه المخالفة لم تعد متاحة.")
        return
    is_member = await check_is_member(v["chat_id"], update.effective_user.id)
    if is_member:
        db.resolve_violation(vid, "resubscribed")
        await query.message.reply_text("✅ تم التحقق، رجعت مشترك. تحقق إذا بقيت عندك مخالفات تانية من «🗂️ الأرباح».")
    else:
        await query.message.reply_text("❌ لسا مو مشترك. اشترك بالقناة/المجموعة أولاً ثم جرب التحقق مجدداً.")


# ==================== "🗂️ الأرباح" (كسب الرصيد) ====================

async def show_earn_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_earn_violations_and_block(update, context):
        return

    counts = db.count_tasks_by_category()
    text = (
        f"📢 مهام القنوات: {counts.get('channel', 0)}\n"
        f"👥 مهام المجموعات: {counts.get('group', 0)}\n"
        f"👁️ مهام المشاهدة: {counts.get('post', 0)}\n"
        f"🤖 مهام البوت: {counts.get('bot', 0)}\n"
        f"🔥 مهام التفاعل: {counts.get('interaction', 0)}\n"
        f"👇 اختر طريقة الكسب"
    )
    keyboard = [
        [InlineKeyboardButton("📢 اشترك في القناة", callback_data="earn_cat:channel")],
        [InlineKeyboardButton("🔮 انضم إلى المجموعة", callback_data="earn_cat:group")],
        [InlineKeyboardButton("👁️ عرض المنشورات", callback_data="earn_cat:post")],
        [InlineKeyboardButton("🤖 الانتقال إلى البوت", callback_data="earn_cat:bot")],
        [InlineKeyboardButton("🔥 تفاعلات", callback_data="earn_cat:interaction")],
        [InlineKeyboardButton("👑 القواعد", callback_data="earn_rules")],
    ]
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def earn_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_earn_violations_and_block(update, context):
        return
    cat = query.data.split(":")[1]

    if cat == "bot":
        keyboard = [
            [InlineKeyboardButton(db.BOT_TYPES["normal"], callback_data="earn_bot_type:normal")],
            [InlineKeyboardButton(db.BOT_TYPES["conditions"], callback_data="earn_bot_type:conditions")],
            [InlineKeyboardButton(db.BOT_TYPES["webapp"], callback_data="earn_bot_type:webapp")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")],
        ]
        await query.message.reply_text("🤖 اختر نوع مهام البوتات التي تريد رؤيتها:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await show_earn_tasks_list(update, context, cat)


async def earn_bot_type_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_type = query.data.split(":")[1]
    await show_earn_tasks_list(update, context, "bot", bot_type=bot_type)


async def show_earn_tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE, cat, bot_type=None):
    query = update.callback_query
    user_id = update.effective_user.id
    tasks = db.get_active_tasks(cat, exclude_owner=user_id)
    tasks = [t for t in tasks if db.user_task_status(t["task_id"], user_id) not in ("approved", "in_progress", "submitted", "review_requested")]
    if bot_type:
        tasks = [t for t in tasks if t.get("bot_type") == bot_type]

    if not tasks:
        await query.message.reply_text("لا يوجد مهام متاحة بهاي الفئة حالياً، تابعنا قريباً!", reply_markup=back_kb("earn_menu"))
        return

    if cat in ("channel", "group"):
        tasks = [t for t in tasks if t.get("link")]  # استبعاد أي مهمة بدون رابط صالح
        if not tasks:
            await query.message.reply_text("لا يوجد مهام متاحة بهاي الفئة حالياً، تابعنا قريباً!", reply_markup=back_kb("earn_menu"))
            return
        text = ("اضغط الأزرار لاختيار المهمة.\n"
                "⚠️ يُمنع إلغاء الاشتراك/مغادرة المجموعة قبل 7 أيام وإلا سيتم حظر قدرتك على إكمال المهام.")
        keyboard = []
        for t in tasks[:15]:
            keyboard.append([
                InlineKeyboardButton("🔄 فحص", callback_data=f"earn_check:{t['task_id']}"),
                InlineKeyboardButton(f"↗️ +{fmt_lira(t['unit_price'])} 💰 | اشتراك", url=t["link"])
            ])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")])
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if cat == "post":
        text = "لكسب الليرات شاهد المنشورات بالضغط على الأزرار. انتباه! بعض المنشورات طويلة."
        keyboard = [[InlineKeyboardButton(f"مشاهدة منشور +{fmt_lira(t['unit_price'])} ليرة 💰", callback_data=f"earn_view_post:{t['task_id']}")] for t in tasks[:15]]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")])
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # bot / interaction — manual review
    tasks = [t for t in tasks if t.get("link")]  # استبعاد أي مهمة بدون رابط صالح
    if not tasks:
        await query.message.reply_text("لا يوجد مهام متاحة بهاي الفئة حالياً، تابعنا قريباً!", reply_markup=back_kb("earn_menu"))
        return
    for t in tasks[:10]:
        keyboard = [[InlineKeyboardButton("🎯 فتح المهمة", callback_data=f"earn_open:{t['task_id']}")]]
        cond = f"\n📝 المطلوب: {t['extra_conditions']}" if t["extra_conditions"] else ""
        await query.message.reply_text(
            f"📌 مهمة بـ {fmt_lira(t['unit_price'])} ليرة ({db.CATEGORY_LABELS.get(cat, cat)}){cond}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def earn_check_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    t = db.get_task(task_id)
    if not t or t["status"] != "active":
        await query.message.reply_text("❌ هذه المهمة لم تعد متاحة.")
        return

    try:
        member = await telegram_app.bot.get_chat_member(t["target_chat_id"], user_id)
        is_member = member.status in ("member", "administrator", "creator")
    except Exception:
        is_member = False

    if not is_member:
        await query.message.reply_text("لست مشتركاً في القناة/المحادثة. اشترك ثم حاول.")
        return

    db.start_completion(task_id, user_id)
    db.resolve_completion(task_id, user_id, "approved")
    db.add_balance(user_id, t["unit_price"], kind="task_reward", note=f"مهمة #{task_id}")
    cur, tgt = db.increment_task_progress(task_id)
    settings = db.get_settings()
    db.upsert_subscription_watch(user_id, t["target_chat_id"], task_id, settings["sub_watch_days"])
    u = db.get_user(user_id)
    await query.message.reply_text(
        f"✅ اكتملت المهمة № {task_id}!\n💰 حصلت على {fmt_lira(t['unit_price'])} ليرة\n💰 رصيدك: {fmt_lira(u['balance'])} ليرة\n\n"
        f"⚠️ إذا ألغيت اشتراكك خلال {settings['sub_watch_days']} أيام القادمة، رح تنحظر مؤقتاً من قسم الأرباح."
    )


async def earn_view_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    t = db.get_task(task_id)
    if not t or t["status"] != "active":
        await query.message.reply_text("❌ هذه المهمة لم تعد متاحة.")
        return
    user_id = update.effective_user.id

    if t["target_chat_id"] and t.get("source_message_id"):
        try:
            await telegram_app.bot.forward_message(user_id, t["target_chat_id"], t["source_message_id"])
        except Exception as e:
            logger.error(f"forward_message failed for task {task_id}: {e}")
            if t["link"]:
                await query.message.reply_text(f"🔗 شاهد المنشور هنا: {t['link']}")
            else:
                await query.message.reply_text("⚠️ ما قدرت أعرض المنشور مباشرة، لكن رح تحصل مكافأتك.")
    elif t["link"]:
        await query.message.reply_text(f"🔗 شاهد المنشور هنا: {t['link']}")

    db.start_completion(task_id, user_id)
    db.resolve_completion(task_id, user_id, "approved")
    db.add_balance(user_id, t["unit_price"], kind="task_reward", note=f"مشاهدة منشور #{task_id}")
    cur, tgt = db.increment_task_progress(task_id)
    u = db.get_user(user_id)
    keyboard = [
        [InlineKeyboardButton("➡️ المنشور التالي", callback_data="earn_cat:post")],
        [InlineKeyboardButton("🛑 تبليغ", callback_data=f"earn_report:{task_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")],
    ]
    await query.message.reply_text(
        f"💰 حصلت على {fmt_lira(t['unit_price'])} ليرة مقابل مشاهدة المنشور № {task_id}!\nرصيدك الحالي: {fmt_lira(u['balance'])} ليرة",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def earn_open_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    t = db.get_task(task_id)
    if not t or t["status"] != "active" or t["current_count"] >= t["target_count"]:
        await query.message.reply_text("❌ هذه المهمة لم تعد متاحة أو اكتمل العدد المطلوب.")
        return
    if not t.get("link"):
        await query.message.reply_text("❌ هذه المهمة بلا رابط صالح حالياً، تواصل مع الإدارة.")
        return

    db.start_completion(task_id, user_id)
    keyboard = [
        [InlineKeyboardButton("➡️ الانتقال", url=t["link"])],
        [InlineKeyboardButton("🛑 تبليغ", callback_data=f"earn_report:{task_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")],
    ]
    template_key = {
        "normal": "bot_task_normal_msg", "conditions": "bot_task_conditions_msg", "webapp": "bot_task_webapp_msg"
    }.get(t.get("bot_type"))
    if t["category"] == "bot" and template_key:
        tpl = db.get_template(template_key)
        hint = tpl["content"] if tpl else "📸 بعد التنفيذ أرسل لقطة شاشة واضحة تثبت إنجازك للمهمة."
    else:
        hint = "📸 بعد التنفيذ أرسل لقطة شاشة واضحة تثبت إنجازك للمهمة، مباشرة في هذه المحادثة."
    cond = f"\n\n📝 الشروط: {t['extra_conditions']}" if t.get("extra_conditions") else ""
    await query.message.reply_text(
        f"📌 مهمة بـ {fmt_lira(t['unit_price'])} ليرة\n\n{hint}{cond}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def earn_report_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    for admin_id in ADMIN_IDS:
        try:
            await telegram_app.bot.send_message(admin_id, f"🛑 تبليغ عن مهمة #{task_id} من المستخدم {update.effective_user.id}")
        except Exception:
            pass
    await query.message.reply_text("✔ تم إرسال البلاغ للإدارة، شكراً لك.")


async def earn_rules_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tpl = db.get_template("rules_text")
    await query.message.reply_text(tpl["content"] if tpl else "لا يوجد قواعد مضافة.", reply_markup=back_kb("earn_menu"))


# ==================== استقبال صور الإثبات ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1].file_id

    awaiting = context.user_data.get("awaiting")
    if awaiting and awaiting.startswith("cheque_image:"):
        cheque_id = int(awaiting.split(":")[1])
        ch = db.get_cheque(cheque_id)
        if ch and ch["creator_id"] == user_id:
            db.update_cheque_field(cheque_id, "image_file_id", photo)
            context.user_data["awaiting"] = None
            await update.message.reply_text("✔ تم إضافة الصورة للشيك.")
        return

    task_id = db.get_latest_in_progress(user_id)
    if not task_id:
        # لم يكن هناك طلب إثبات صورة معلق — تجاهل بهدوء
        return

    t = db.get_task(task_id)
    db.submit_proof(task_id, user_id, photo)
    submitter = update.effective_user
    keyboard = [[InlineKeyboardButton("🔍 مراجعة التنفيذ", callback_data=f"review_open:{task_id}:{user_id}")]]
    try:
        await telegram_app.bot.send_message(
            t["owner_id"],
            f"📥 لديك تنفيذات جديدة لمهمة #{task_id}.\n"
            f"راجعها خلال 24 ساعة لتجنب الدفع التلقائي.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"failed to notify owner {t['owner_id']} of new proof for task {task_id}: {e}")
    await update.message.reply_text(
        f"✅ رائع! تم إرسال المهمة № {task_id} إلى صاحبها للمراجعة. إذا لم يقم المؤلف بالمراجعة خلال 24 ساعة فسيتم دفعها تلقائياً.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="earn_menu")]])
    )


async def review_open_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, task_id_s, uid_s = query.data.split(":")
    task_id, uid = int(task_id_s), int(uid_s)
    t = db.get_task(task_id)
    if not t or t["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الإجراء غير متاح لك.")
        return
    completion = db.get_completion(task_id, uid)
    if not completion or not completion.get("proof_file_id"):
        await query.message.reply_text("❌ ما لقيت إثبات لهاي المهمة (ممكن يكون انحذف أو انراجع من قبل).")
        return

    submitter = await telegram_app.bot.get_chat(uid)
    keyboard = [
        [InlineKeyboardButton("✅ دفع", callback_data=f"adpay:{task_id}:{uid}"),
         InlineKeyboardButton("❌ رفض الدفع", callback_data=f"adrejc:{task_id}:{uid}")],
        [InlineKeyboardButton("♻️ إرسال لمراجعة جديدة", callback_data=f"adrev:{task_id}:{uid}")],
    ]
    cond_line = f"\n📋 الشروط: {t['extra_conditions']}" if t.get("extra_conditions") else ""
    caption = (
        f"✅ تم تنفيذ المهمة № {task_id}\n"
        f"🔗 رابط البوت: {t.get('link') or 'لا يوجد'}{cond_line}\n"
        f"👤 من: {submitter.username or submitter.full_name}"
    )
    await telegram_app.bot.send_photo(
        update.effective_user.id, completion["proof_file_id"], caption=caption, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def owner_review_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    action, task_id_s, uid_s = data.split(":")
    task_id, uid = int(task_id_s), int(uid_s)
    t = db.get_task(task_id)
    if not t or t["owner_id"] != update.effective_user.id:
        await query.message.reply_text("❌ غير مسموح.")
        return

    if action == "adpay":
        db.resolve_completion(task_id, uid, "approved")
        db.add_balance(uid, t["unit_price"], kind="task_reward", note=f"مهمة #{task_id}")
        db.increment_task_progress(task_id)
        try:
            await query.edit_message_caption(caption="✅ تم الدفع بنجاح.")
        except Exception:
            pass
        try:
            await telegram_app.bot.send_message(uid, f"🎉 تم قبول إثباتك لمهمة #{task_id}!\n💰 حصلت على {fmt_lira(t['unit_price'])} ليرة.")
        except Exception:
            pass

    elif action == "adrejc":
        db.resolve_completion(task_id, uid, "rejected")
        try:
            await query.edit_message_caption(caption="🚫 تم رفض الإثبات.")
        except Exception:
            pass
        try:
            await telegram_app.bot.send_message(uid, f"❌ تم رفض إثباتك لمهمة #{task_id}.")
        except Exception:
            pass

    elif action == "adrev":
        context.user_data["review_target"] = (task_id, uid)
        context.user_data["awaiting"] = "review_note"
        await query.message.reply_text(
            "♻️ حدّد الخطأ ووصف ما يجب تصحيحه:\n⛔ ممنوع طلب اشتراك بقنوات راعية أو شروط إضافية.\n✅ مسموح: تخطي كابتشا."
        )


async def handle_review_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id, uid = context.user_data.get("review_target", (None, None))
    if not task_id:
        return
    note = update.message.text
    t = db.get_task(task_id)
    db.resolve_completion(task_id, uid, "review_requested", note=note)
    try:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("الذهاب مرة أخرى 🔗", url=t["link"])]]) if t.get("link") else None
        await telegram_app.bot.send_message(
            uid,
            f"⚠️ صاحب الإعلان يطلب منك إعادة المحاولة:\n\n{note}\n\nبعد التنفيذ أرسل لقطة شاشة هنا مجدداً.",
            reply_markup=markup
        )
        await update.message.reply_text("✔ تم إرسال طلب إعادة المحاولة للمستخدم.")
    except Exception:
        pass
    context.user_data["awaiting"] = None
    context.user_data.pop("review_target", None)


# ==================== "📱 حسابي" ====================

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    text = (
        f"💁‍♂️ حسابك:\n"
        f"🔑 معرفي: {user.id}\n"
        f"📈 المستوى: {u['level']} ({u['xp']} XP)\n"
        f"💰 الرصيد: {fmt_lira(u['balance'])} ليرة"
    )
    notif_label = "❌ تعطيل الإشعارات" if u["notifications_enabled"] else "✅ تفعيل الإشعارات"
    keyboard = [
        [InlineKeyboardButton("💰 تعبئة الرصيد", callback_data="topup_balance")],
        [InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="redeem_code_start")],
        [InlineKeyboardButton("🌌 نظام الإحالة", callback_data="referral_info")],
        [InlineKeyboardButton("📈 نظام المستويات", callback_data="levels_info")],
        [InlineKeyboardButton("💼 مهامي", callback_data="my_tasks:0")],
        [InlineKeyboardButton(notif_label, callback_data="toggle_notif")],
    ]
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def toggle_notif_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    u = db.get_user(update.effective_user.id)
    db.set_field("users", "user_id", update.effective_user.id, "notifications_enabled", 0 if u["notifications_enabled"] else 1)
    await query.message.reply_text("✔ تم تحديث إعداد الإشعارات.")


async def referral_info_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    u = db.get_user(update.effective_user.id)
    settings = db.get_settings()
    me = await telegram_app.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={update.effective_user.id}"
    text = (
        f"مقابل كل شخص يضغط على رابط الإحالة، تحصل على:\n"
        f"🌟 {fmt_lira(settings['ref_reward_premium'])} ليرة — إذا كان لديه Premium\n"
        f"🏃 {fmt_lira(settings['ref_reward_regular'])} ليرة — إذا لم يكن لديه\n"
        f"👤 {fmt_lira(settings['ref_reward_mandatory'])} ليرة — إذا اشترك عبر الاشتراك الإلزامي\n\n"
        f"دخل دائم من نشاطهم: +{settings['ref_income_percent_topup']}% من مبالغ التعبئة، +{settings['ref_income_percent_tasks']}% من تنفيذ المهام\n\n"
        f"عدد المدعوين: {u['referrals_count']}\n"
        f"أرباحك من الإحالات: {fmt_lira(u['referral_earnings'])} ليرة\n\n"
        f"رابط الإحالة الخاص بك:\n{ref_link}"
    )
    await query.message.reply_text(text, reply_markup=back_kb("account_menu"))


async def levels_info_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📈 نظام المستويات:\n"
        "🥉 مبتدئ — 0 إلى 499 XP\n🥈 نشط — 500 إلى 999 XP\n🥇 VIP — 1000 إلى 1999 XP\n👑 Super VIP — 2000+ XP"
    )
    await query.message.reply_text(text, reply_markup=back_kb("account_menu"))


async def topup_balance_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "topup_stars_amount"
    await query.message.reply_text("⭐ أدخل عدد نجوم Telegram Stars التي تريد تحويلها إلى رصيد ليرة:")


async def redeem_code_start_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "redeem_gift_code"
    await query.message.reply_text("🎁 أرسل كود الهدية الآن:")


async def handle_redeem_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    context.user_data["awaiting"] = None
    ok, result = db.redeem_gift_code(code, update.effective_user.id)
    if not ok:
        reasons = {
            "invalid": "❌ هذا الكود غير صحيح أو موقوف.",
            "exhausted": "❌ هذا الكود وصل للحد الأقصى من الاستخدام.",
            "already_used": "❌ لقد استخدمت هذا الكود من قبل.",
        }
        await update.message.reply_text(reasons.get(result, "❌ حدث خطأ."))
        return
    db.add_balance(update.effective_user.id, result, kind="gift_code", note=f"كود هدية {code}")
    u = db.get_user(update.effective_user.id)
    await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح!\n💰 حصلت على {fmt_lira(result)} ليرة.\n💰 رصيدك الآن: {fmt_lira(u['balance'])} ليرة")


async def handle_topup_stars_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ رقم غير صالح.")
        return
    stars = int(text)
    context.user_data["awaiting"] = None
    payload = f"topup:{secrets.token_hex(6)}"
    await telegram_app.bot.send_invoice(
        chat_id=update.effective_user.id,
        title="تعبئة رصيد PR GRAM",
        description=f"تحويل {stars} ⭐ إلى رصيد ليرة في حسابك.",
        payload=payload, currency="XTR", prices=[LabeledPrice("Stars Payment", stars)],
    )


# ==================== فحص الاشتراك / التعليمات / روابط مفيدة ====================

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = db.get_settings()
    channel = settings["mandatory_sub_channel"]
    if not channel:
        await update.message.reply_text("✅ لا يوجد اشتراك إلزامي مفعّل حالياً.")
        return
    try:
        member = await telegram_app.bot.get_chat_member(channel, update.effective_user.id)
        ok = member.status in ("member", "administrator", "creator")
    except Exception:
        ok = False
    if ok:
        await update.message.reply_text("✅ أنت مشترك، حسابك جاهز للعمل.")
    else:
        await update.message.reply_text(f"❌ لست مشتركاً بعد. اشترك في {channel} ثم حاول مجدداً.")


async def show_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tpl = db.get_template("instructions_text")
    text = (tpl["content"] if tpl else None) or "📋 التعليمات: استخدم «📢 الترويج» لإنشاء مهام، و«🗂️ الأرباح» لكسب الرصيد."
    await update.message.reply_text(text)


async def show_useful_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tpl = db.get_template("useful_links")
    text = (tpl["content"] if tpl else None) or "🔗 لا يوجد روابط مضافة حالياً."
    await update.message.reply_text(text)


async def show_bot_stats_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.full_stats()
    text = (
        f"🤖 إحصائيات عامة عن PR GRAM:\n"
        f"👥 المستخدمون: {fmt_lira(s['total_users'])}\n"
        f"📢 المهام النشطة: {fmt_lira(s['active_tasks'])}\n"
        f"✅ المهام المكتملة: {fmt_lira(s['completed_tasks'])}"
    )
    await update.message.reply_text(text)


async def show_checks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("cheque_draft", None)
    context.user_data["awaiting"] = None
    clear_step(context, key="cheque_flow")
    text = (
        "💳 تتيح لك الشيكات إرسال نقاط مباشرة داخل الرسائل عبر رابط.\n\n"
        "🔹 الشيك الشخصي — لإرسال النقاط لمستخدم واحد فقط\n"
        "🔹 الشيك المتعدد — لتوزيع النقاط على عدة مستخدمين، مع إمكانية شرط اشتراك\n\n"
        "اختر نوع الشيك:"
    )
    keyboard = [
        [InlineKeyboardButton("👤 شخصي", callback_data="chq_menu:personal")],
        [InlineKeyboardButton("👥 شيك متعدد", callback_data="chq_menu:multi")],
        [InlineKeyboardButton("📋 شيكاتي", callback_data="chq_mine:0")],
    ]
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def chq_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = query.data.split(":")[1]
    label = "عمليات تحقق شخصية" if kind == "personal" else "عمليات تحقق متعددة"
    keyboard = [[InlineKeyboardButton("➕ إنشاء شيك", callback_data=f"chq_new:{kind}")]]
    my_cheques = db.list_cheques_by_owner(update.effective_user.id, kind=kind)
    for ch in my_cheques[:15]:
        mark = "🟢" if ch["status"] == "active" else "⚪"
        keyboard.append([InlineKeyboardButton(
            f"{mark} {fmt_lira(ch['amount'])} — {ch['uses_count']}/{ch['max_uses']}",
            callback_data=f"chq_view:{ch['cheque_id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="checks_menu")])
    await query.message.reply_text(label, reply_markup=InlineKeyboardMarkup(keyboard))


def cheque_min_max(kind, settings):
    if kind == "personal":
        return settings["cheque_min_amount"], settings["cheque_personal_max_amount"]
    return settings["cheque_multi_min_amount"], settings["cheque_multi_max_amount"]


async def chq_new_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = query.data.split(":")[1]
    user = db.get_user(update.effective_user.id)
    settings = db.get_settings()
    context.user_data["cheque_draft"] = {"kind": kind}
    context.user_data["awaiting"] = "cheque_amount"
    mn, mx = cheque_min_max(kind, settings)
    label = "لإرسال النقاط لمستخدم واحد" if kind == "personal" else "لتوزيعها على عدة مستخدمين"
    if config.is_admin(update.effective_user.id):
        limits_line = f"إنت أدمن، ما في حد أدنى ولا أقصى ولا خصم رصيد عليك.\n"
    else:
        limits_line = f"الحد الأدنى: {fmt_lira(mn)} | الحد الأقصى: {fmt_lira(mx)}\nرصيدك: {fmt_lira(user['balance'])}\n"
    text = (
        f"أنشئ شيك {'شخصي' if kind=='personal' else 'متعدد'} {label}.\n\n"
        f"كم نقطة تريد إرسالها {'للمستخدم' if kind=='personal' else 'لكل مستخدم'} عبر الشيك؟\n\n"
        f"{limits_line}"
        f"أدخل مبلغ الشيك:"
    )
    await send_step(update, context, text, key="cheque_flow")


async def handle_cheque_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("❌ رقم غير صالح.")
        return
    amount = int(text)
    draft = context.user_data.get("cheque_draft", {})
    settings = db.get_settings()
    is_admin_user = config.is_admin(update.effective_user.id)
    if not is_admin_user:
        mn, mx = cheque_min_max(draft.get("kind", "personal"), settings)
        if amount < mn or amount > mx:
            await update.message.reply_text(f"❌ المبلغ يجب أن يكون بين {fmt_lira(mn)} و {fmt_lira(mx)}.")
            return
    draft["amount"] = amount
    context.user_data["cheque_draft"] = draft

    if draft["kind"] == "multi":
        context.user_data["awaiting"] = "cheque_maxuses"
        await send_step(update, context, f"مبلغ الشيك: {fmt_lira(amount)} لكل مستخدم\n\n👥 كم عدد الأشخاص المسموح لهم استخدام هالشيك؟", key="cheque_flow")
        return

    draft["max_uses"] = 1
    context.user_data["cheque_draft"] = draft
    await show_cheque_confirmation(update, context)


async def handle_cheque_maxuses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ رقم غير صالح.")
        return
    draft = context.user_data.get("cheque_draft", {})
    draft["max_uses"] = int(text)
    context.user_data["cheque_draft"] = draft
    await show_cheque_confirmation(update, context)


async def show_cheque_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("cheque_draft", {})
    settings = db.get_settings()
    is_admin_user = config.is_admin(update.effective_user.id)
    commission = 0 if is_admin_user else settings["cheque_commission_percent"]
    total = 0 if is_admin_user else math.ceil(draft["amount"] * draft.get("max_uses", 1) * (1 + commission / 100))
    draft["total_cost"] = total
    draft["commission_percent"] = commission
    context.user_data["cheque_draft"] = draft
    context.user_data["awaiting"] = None

    user = db.get_user(update.effective_user.id)
    cost_line = "🎁 مجاني (صلاحية أدمن)" if is_admin_user else (
        f"ستُفرض عمولة {commission}% لإنشاء الشيك.\n"
        f"المبلغ الإجمالي المطلوب: {fmt_lira(total)}\n"
        f"رصيدك الحالي: {fmt_lira(user['balance'])}"
    )
    text = (
        f"{'تحقق شخصي' if draft['kind']=='personal' else 'تحقق متعدد'} 💳\n\n"
        f"قيمة الشيك: {fmt_lira(draft['amount'])}" + (f" × {draft['max_uses']} مستخدم" if draft["kind"] == "multi" else "") + "\n"
        f"{cost_line}\n\n"
        f"يرجى تأكيد صحة البيانات:"
    )
    keyboard = [
        [InlineKeyboardButton("✅ تأكيد", callback_data="chq_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="chq_cancel")],
        [InlineKeyboardButton("✍️ تعديل السعر", callback_data="chq_edit_price")],
    ]
    await send_step(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), key="cheque_flow")


async def chq_edit_price_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("cheque_draft", {})
    context.user_data["awaiting"] = "cheque_amount"
    settings = db.get_settings()
    user = db.get_user(update.effective_user.id)
    await send_step(
        update, context,
        f"أدخل مبلغ الشيك الجديد (الحد الأدنى {fmt_lira(settings['cheque_min_amount'])}، رصيدك {fmt_lira(user['balance'])}):",
        key="cheque_flow"
    )


async def chq_cancel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("cheque_draft", None)
    context.user_data["awaiting"] = None
    clear_step(context, key="cheque_flow")
    await query.message.reply_text("❌ تم إلغاء إنشاء الشيك.", reply_markup=back_kb("checks_menu"))


async def chq_confirm_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("cheque_draft")
    if not draft:
        await query.message.reply_text("⚠️ انتهت محاولة إنشاء الشيك. ابدأ من جديد.")
        return
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if draft["total_cost"] > user["balance"]:
        await query.message.reply_text("❌ رصيدك غير كافٍ لإنشاء هذا الشيك.")
        return

    code = secrets.token_urlsafe(10)
    db.add_balance(user_id, -draft["total_cost"], kind="cheque_create", note=f"إنشاء شيك {code}")
    cheque_id = db.create_cheque(
        code=code, kind=draft["kind"], creator_id=user_id, amount=draft["amount"],
        commission_percent=draft["commission_percent"], total_cost=draft["total_cost"],
        max_uses=draft.get("max_uses", 1)
    )
    context.user_data.pop("cheque_draft", None)
    context.user_data["awaiting"] = None
    clear_step(context, key="cheque_flow")
    await query.message.reply_text(f"✅ تم إنشاء الشيك بنجاح!")
    await show_cheque_panel(update, context, cheque_id)


async def show_cheque_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, cheque_id):
    ch = db.get_cheque(cheque_id)
    if not ch:
        return
    kind_label = "شخصي" if ch["kind"] == "personal" else "متعدد"
    notif_label = "🔕 تعطيل الإشعارات" if ch["notifications_enabled"] else "🔔 تفعيل الإشعارات"
    text = (
        f"💳 شيك {kind_label} #{ch['cheque_id']}\n"
        f"مبلغ الشيك: {fmt_lira(ch['amount'])} لكل استخدام\n"
        f"الاستخدام: {ch['uses_count']}/{ch['max_uses']}\n"
        f"كلمة المرور: {'مفعّلة 🔒' if ch['password'] else 'غير مفعّلة'}\n"
        + (f"شرط الاشتراك: {ch['require_channel'] or 'لا يوجد'}\n" if ch["kind"] == "multi" else "")
        + f"الحالة: {ch['status']}"
    )
    keyboard = [
        [InlineKeyboardButton("🔗 رابط للمستخدم", callback_data=f"chq_link:{cheque_id}")],
        [InlineKeyboardButton("💰 إرسال للمستخدم", callback_data=f"chq_send:{cheque_id}")],
        [InlineKeyboardButton("💬 إضافة تعليق", callback_data=f"chq_comment:{cheque_id}"),
         InlineKeyboardButton("🔒 كلمة المرور", callback_data=f"chq_pass:{cheque_id}")],
        [InlineKeyboardButton("🖼 إضافة صورة", callback_data=f"chq_image:{cheque_id}"),
         InlineKeyboardButton(notif_label, callback_data=f"chq_notif:{cheque_id}")],
    ]
    if ch["kind"] == "multi":
        keyboard.append([InlineKeyboardButton("🔒 شرط اشتراك بقناة", callback_data=f"chq_reqchannel:{cheque_id}")])
    keyboard.append([InlineKeyboardButton("🗑 حذف الشيك", callback_data=f"chq_delete:{cheque_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="checks_menu")])
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def chq_mine_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    cheques = db.list_cheques_by_owner(update.effective_user.id)
    if not cheques:
        await query.message.reply_text("📋 ما عندك أي شيكات لهلق.", reply_markup=back_kb("checks_menu"))
        return
    per_page = 8
    page_items = cheques[page * per_page: page * per_page + per_page]
    keyboard = []
    for ch in page_items:
        emoji = "🟢" if ch["status"] == "active" else "⚪"
        keyboard.append([InlineKeyboardButton(
            f"{emoji} #{ch['cheque_id']} — {fmt_lira(ch['amount'])} ({ch['uses_count']}/{ch['max_uses']})",
            callback_data=f"chq_view:{ch['cheque_id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="checks_menu")])
    await query.message.reply_text("📋 شيكاتك:", reply_markup=InlineKeyboardMarkup(keyboard))


async def chq_view_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الشيك غير متاح لك.")
        return
    await show_cheque_panel(update, context, cheque_id)


async def chq_link_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الشيك غير متاح لك.")
        return
    me = await telegram_app.bot.get_me()
    link = f"https://t.me/{me.username}?start=cheque_{ch['code']}"
    text = (
        f"شيك {'شخصي' if ch['kind']=='personal' else 'متعدد'} 💳\n\n"
        f"مبلغ الشيك: {fmt_lira(ch['amount'])}\n\n"
        f"⚠️ لا تلتقط صور شاشة لشيكك ولا ترسله لأشخاص غير موثوقين!\n"
        f"يمكن للمحتالين استخدام رابط الشيك للوصول إلى عملاتك.\n\n"
        f"رابط الشيك:\n"
        f'<span class="tg-spoiler">{link}</span>'
    )
    await query.message.reply_text(text, parse_mode="HTML")


async def chq_send_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الشيك غير متاح لك.")
        return
    context.user_data["cheque_send_id"] = cheque_id
    btn = KeyboardButton("👤 اختر مستخدماً", request_users=KeyboardButtonRequestUsers(request_id=3, user_is_bot=False, request_username=True))
    markup = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
    await query.message.reply_text(
        "اختر المستخدم يلي بدك ترسله الشيك (لازم يكون بدأ محادثة مع البوت قبل هيك):",
        reply_markup=markup
    )


async def chq_comment_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    context.user_data["awaiting"] = f"cheque_comment:{cheque_id}"
    await query.message.reply_text("💬 اكتب التعليق الذي رح يظهر مع الشيك:")


async def chq_pass_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    settings = db.get_settings()
    context.user_data["awaiting"] = f"cheque_pass:{cheque_id}"
    await query.message.reply_text(
        f"🔒 أدخل كلمة المرور للشيك، والتي ستكون مطلوبة عند التفعيل.\nالحد الأقصى للطول: {settings['cheque_password_max_length']} حرف.\n"
        f"أرسل - لإلغاء كلمة المرور."
    )


async def chq_image_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    context.user_data["awaiting"] = f"cheque_image:{cheque_id}"
    await query.message.reply_text("🖼 أرسل الصورة التي بدك تظهر مع الشيك:")


async def chq_notif_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    db.update_cheque_field(cheque_id, "notifications_enabled", 0 if ch["notifications_enabled"] else 1)
    await query.message.reply_text("✔ تم تحديث إعداد الإشعارات.")
    await show_cheque_panel(update, context, cheque_id)


async def chq_delete_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        await query.message.reply_text("❌ هذا الشيك غير متاح لك.")
        return
    remaining = ch["amount"] * (ch["max_uses"] - ch["uses_count"])
    if remaining > 0:
        db.add_balance(update.effective_user.id, remaining, kind="cheque_refund", note=f"استرجاع شيك #{cheque_id} المحذوف")
    db.update_cheque_field(cheque_id, "status", "deleted")
    await query.message.reply_text(f"🗑 تم حذف الشيك، وتم استرجاع {fmt_lira(remaining)} لرصيدك.")


async def handle_cheque_send_user(update: Update, context: ContextTypes.DEFAULT_TYPE, shared_user):
    cheque_id = context.user_data.pop("cheque_send_id", None)
    if not cheque_id:
        await update.message.reply_text("⚠️ انتهت محاولة إرسال الشيك.", reply_markup=main_reply_keyboard())
        return
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        await update.message.reply_text("❌ هذا الشيك غير متاح لك.", reply_markup=main_reply_keyboard())
        return
    me = await telegram_app.bot.get_me()
    link = f"https://t.me/{me.username}?start=cheque_{ch['code']}"
    target_id = shared_user.user_id
    try:
        await telegram_app.bot.send_message(
            target_id,
            f"🎁 وصلك شيك نقاط من {update.effective_user.username or update.effective_user.full_name}!\n"
            f"💳 القيمة: {fmt_lira(ch['amount'])}\n\n"
            f"اضغط الرابط لتفعيله:\n{link}"
        )
        await update.message.reply_text("✅ تم إرسال الشيك للمستخدم.", reply_markup=main_reply_keyboard())
    except Exception:
        await update.message.reply_text(
            "⚠️ ما قدرت أوصّل الرسالة (المستخدم لازم يكون بدأ محادثة مع البوت قبل هيك). "
            "استخدم «🔗 رابط للمستخدم» وابعتلو الرابط يدوياً بدالها.",
            reply_markup=main_reply_keyboard()
        )


async def chq_reqchannel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    if ch["require_channel"]:
        text = f"🔒 شرط الاشتراك الحالي:\n{ch['require_channel']}"
        keyboard = [
            [InlineKeyboardButton("🗑 حذف الشرط", callback_data=f"chq_reqchannel_del:{cheque_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"chq_view:{cheque_id}")],
        ]
    else:
        text = "🔒 ما فيه شرط اشتراك حالياً على هالشيك. اضغط تحت لإضافة وحدة:"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة شرط اشتراك", callback_data=f"chq_reqchannel_add:{cheque_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"chq_view:{cheque_id}")],
        ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def chq_reqchannel_del_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    db.update_cheque_field(cheque_id, "require_channel", None)
    await query.message.reply_text("🗑 تم حذف شرط الاشتراك.")
    await show_cheque_panel(update, context, cheque_id)


async def chq_reqchannel_add_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cheque_id = int(query.data.split(":")[1])
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    context.user_data["awaiting"] = f"cheque_reqchannel:{cheque_id}"
    await query.message.reply_text("🔒 أرسل معرّف القناة/المجموعة التي يجب الاشتراك بها (مثال: @my_channel):")


async def handle_cheque_reqchannel(update: Update, context: ContextTypes.DEFAULT_TYPE, cheque_id):
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    val = update.message.text.strip()
    db.update_cheque_field(cheque_id, "require_channel", val)
    context.user_data["awaiting"] = None
    await update.message.reply_text("✔ تم تحديث شرط الاشتراك.")


async def handle_cheque_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, cheque_id):
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    db.update_cheque_field(cheque_id, "comment", update.message.text.strip()[:300])
    context.user_data["awaiting"] = None
    await update.message.reply_text("✔ تم إضافة التعليق.")


async def handle_cheque_pass(update: Update, context: ContextTypes.DEFAULT_TYPE, cheque_id):
    ch = db.get_cheque(cheque_id)
    if not ch or ch["creator_id"] != update.effective_user.id:
        return
    text = update.message.text.strip()
    settings = db.get_settings()
    if text in ("-", "الغاء", "إلغاء"):
        db.update_cheque_field(cheque_id, "password", None)
        context.user_data["awaiting"] = None
        await update.message.reply_text("✔ تم إلغاء كلمة المرور عن الشيك.")
        return
    if len(text) > settings["cheque_password_max_length"]:
        await update.message.reply_text(f"❌ كلمة المرور أطول من الحد المسموح ({settings['cheque_password_max_length']} حرف).")
        return
    db.update_cheque_field(cheque_id, "password", text)
    context.user_data["awaiting"] = None
    await update.message.reply_text("✔ تم تعيين كلمة المرور للشيك.")


# ==================== تفعيل الشيك (عبر رابط /start) ====================

CHEQUE_MAX_FAILS_BEFORE_CAPTCHA = 3


async def start_cheque_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE, code):
    ch = db.get_cheque_by_code(code)
    user_id = update.effective_user.id
    if not ch or ch["status"] != "active":
        await update.effective_message.reply_text("❌ هذا الشيك غير موجود أو انتهى.")
        return
    if ch["creator_id"] == user_id:
        await update.effective_message.reply_text("❌ ما فيك تفعّل شيك أنشأته إنت بنفسك.")
        return
    if db.cheque_already_redeemed(ch["cheque_id"], user_id):
        await update.effective_message.reply_text("❌ لقد فعّلت هذا الشيك من قبل.")
        return
    if ch["max_uses"] and ch["uses_count"] >= ch["max_uses"]:
        await update.effective_message.reply_text("❌ وصل هذا الشيك لأقصى عدد استخدام مسموح.")
        return

    if ch["require_channel"]:
        is_member = await check_is_member(ch["require_channel"], user_id)
        if not is_member:
            keyboard = [[InlineKeyboardButton("🔗 الاشتراك", url=f"https://t.me/{ch['require_channel'].lstrip('@')}")],
                        [InlineKeyboardButton("✅ تحقق مجدداً", callback_data=f"chq_redeem_recheck:{code}")]]
            await update.effective_message.reply_text(
                "📌 هذا الشيك يتطلب الاشتراك بقناة معينة أولاً.", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    if ch["password"]:
        context.user_data["awaiting"] = f"cheque_redeem_pass:{code}"
        await update.effective_message.reply_text("🔒 هذا الشيك محمي بكلمة مرور. أدخلها:")
        return

    await finalize_cheque_redeem(update, context, ch)


async def finalize_cheque_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE, ch):
    user_id = update.effective_user.id
    ok, result = db.redeem_cheque(ch["code"], user_id)
    if not ok:
        reasons = {"invalid": "❌ هذا الشيك غير صالح.", "exhausted": "❌ نفذ عدد استخدامات هذا الشيك.",
                   "already_used": "❌ لقد فعّلت هذا الشيك من قبل."}
        await update.effective_message.reply_text(reasons.get(result, "❌ حدث خطأ."))
        return
    db.add_balance(user_id, result, kind="cheque_redeem", note=f"تفعيل شيك {ch['code']}")
    u = db.get_user(user_id)
    caption = f"🎉 تم تفعيل الشيك بنجاح!\n💰 حصلت على {fmt_lira(result)}.\n💰 رصيدك الآن: {fmt_lira(u['balance'])}"
    if ch.get("comment"):
        caption += f"\n\n💬 {ch['comment']}"
    if ch.get("image_file_id"):
        await telegram_app.bot.send_photo(user_id, ch["image_file_id"], caption=caption)
    else:
        await update.effective_message.reply_text(caption)

    if ch["notifications_enabled"]:
        redeemer = update.effective_user
        try:
            await telegram_app.bot.send_message(
                ch["creator_id"],
                f"✅ تم تفعيل شيكك #{ch['cheque_id']} من قبل {redeemer.username or redeemer.full_name}."
            )
        except Exception:
            pass


async def chq_redeem_recheck_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    await start_cheque_redeem(update, context, code)


async def handle_cheque_redeem_password(update: Update, context: ContextTypes.DEFAULT_TYPE, code):
    ch = db.get_cheque_by_code(code)
    if not ch:
        context.user_data["awaiting"] = None
        await update.message.reply_text("❌ هذا الشيك لم يعد موجوداً.")
        return

    if update.message.text.strip() == (ch["password"] or ""):
        context.user_data["awaiting"] = None
        context.user_data["cheque_fail_count"] = 0
        await finalize_cheque_redeem(update, context, ch)
        return

    fails = context.user_data.get("cheque_fail_count", 0) + 1
    context.user_data["cheque_fail_count"] = fails

    if fails >= CHEQUE_MAX_FAILS_BEFORE_CAPTCHA:
        a, b = secrets.randbelow(8) + 1, secrets.randbelow(8) + 1
        context.user_data["cheque_captcha_answer"] = a + b
        context.user_data["cheque_captcha_return_code"] = code
        context.user_data["awaiting"] = "cheque_captcha"
        await update.message.reply_text(
            f"⚠️ عدة محاولات خاطئة متتالية — تحقق أمني بسيط قبل ما تكمل.\nكم ناتج {a} + {b}؟"
        )
        return

    await update.message.reply_text("❌ كلمة المرور غلط. حاول مجدداً:")


async def handle_cheque_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    expected = context.user_data.get("cheque_captcha_answer")
    code = context.user_data.get("cheque_captcha_return_code")
    if not text.isdigit() or int(text) != expected:
        await update.message.reply_text("❌ غلط. حاول تاني:")
        return
    context.user_data["awaiting"] = f"cheque_redeem_pass:{code}" if code else None
    context.user_data["cheque_fail_count"] = 0
    context.user_data.pop("cheque_captcha_answer", None)
    await update.message.reply_text("✅ تم التحقق. تفضل أدخل كلمة المرور الصحيحة:" if code else "✅ تم التحقق.")


# ==================== موجّه الأزرار السفلية (Reply Keyboard) ====================

async def bottom_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    routes = {
        "📢 الترويج": lambda: show_promo_menu(update, context),
        "🗂️ الأرباح": lambda: show_earn_menu(update, context),
        "📱 حسابي": lambda: show_account(update, context),
        "💳 الشيكات": lambda: show_checks_menu(update, context),
        "🤖 روبوتاتنا والإحصائيات": lambda: show_bot_stats_public(update, context),
        "🔍 فحص الاشتراك": lambda: check_subscription(update, context),
        "📋 التعليمات": lambda: show_instructions(update, context),
        "🔗 روابط مفيدة": lambda: show_useful_links(update, context),
    }
    if text in routes:
        await routes[text]()
        return True
    return False


# ==================== موجّه النصوص العام (state machine) ====================

async def generic_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await bottom_menu_router(update, context):
        return

    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return  # لا يوجد إجراء بالانتظار — تجاهل

    if awaiting == "task_price":
        await handle_task_price(update, context)
    elif awaiting == "bot_conditions_text":
        await handle_bot_conditions_text(update, context)
    elif awaiting == "redeem_gift_code":
        await handle_redeem_gift_code(update, context)
    elif awaiting == "task_qty":
        await handle_task_qty(update, context)
    elif awaiting == "post_forward":
        await handle_post_forward(update, context)
    elif awaiting == "interaction_link":
        await handle_interaction_link(update, context)
    elif awaiting == "review_note":
        await handle_review_note(update, context)
    elif awaiting == "topup_stars_amount":
        await handle_topup_stars_amount(update, context)
    elif awaiting.startswith("task_add_units:"):
        await handle_task_add_units(update, context, int(awaiting.split(":")[1]))
    elif awaiting.startswith("task_edit_price:"):
        await handle_task_edit_price(update, context, int(awaiting.split(":")[1]))
    elif awaiting == "cheque_amount":
        await handle_cheque_amount(update, context)
    elif awaiting == "task_ref_link":
        await handle_task_ref_link(update, context)
    elif awaiting == "cheque_maxuses":
        await handle_cheque_maxuses(update, context)
    elif awaiting.startswith("cheque_comment:"):
        await handle_cheque_comment(update, context, int(awaiting.split(":")[1]))
    elif awaiting.startswith("cheque_pass:"):
        await handle_cheque_pass(update, context, int(awaiting.split(":")[1]))
    elif awaiting.startswith("cheque_reqchannel:"):
        await handle_cheque_reqchannel(update, context, int(awaiting.split(":")[1]))
    elif awaiting.startswith("cheque_redeem_pass:"):
        await handle_cheque_redeem_password(update, context, awaiting.split(":", 1)[1])
    elif awaiting == "cheque_captcha":
        await handle_cheque_captcha(update, context)
    elif awaiting.startswith("admin:"):
        await admin_text_router(update, context, awaiting)


# ==================== أزرار التنقل العامة back_main / promo_menu / earn_menu / account_menu ====================

async def generic_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    context.user_data["awaiting"] = None

    if data == "back_main":
        await query.message.reply_text("القائمة الرئيسية:", reply_markup=main_reply_keyboard())
    elif data == "promo_menu":
        await show_promo_menu(update, context, edit=True)
    elif data == "earn_menu":
        await show_earn_menu(update, context)
    elif data == "account_menu":
        await show_account(update, context)
    elif data == "promo_auto_settings":
        await query.message.reply_text("⚙️ إعدادات المهام التلقائية قيد التطوير حالياً.", reply_markup=back_kb("promo_menu"))
    elif data == "checks_menu":
        await show_checks_menu(update, context)


import admin_panel  # noqa: E402  (يسجّل هاندلرز الأدمن على telegram_app بعد تعريف الدوال المشتركة هنا)
from admin_panel import admin_text_router  # noqa: E402


# ==================== تسجيل الهاندلرز ====================

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    يمسك أي خطأ غير متوقع بأي هاندلر بكل البوت (PTB بيبلع الأخطاء الداخلية افتراضياً وما بتوصل
    لـ try/except يلي بالـ webhook) ويبلّغ المستخدم بدل ما يصير الطلب يروح بصمت بدون أي رد،
    وكمان يبعت تفاصيل الخطأ الفعلية للأدمن حتى تنعرف المشكلة بالضبط وتنصلح بسرعة.
    """
    logger.error("Unhandled exception while processing update:", exc_info=context.error)

    tb_text = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    tb_short = tb_text[-1500:]  # آخر جزء من الـ traceback هو الأهم عادةً (مكان الخطأ الفعلي)
    update_str = str(update)[:300] if update else "لا يوجد"

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🐞 خطأ بالبوت:\n\n<code>{tb_short}</code>\n\nUpdate: {update_str}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        if isinstance(update, Update) and update.effective_chat and update.effective_chat.id not in ADMIN_IDS:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ صار خطأ غير متوقع أثناء تنفيذ طلبك. جرب مرة تانية، وإذا تكررت المشكلة بلّغ الإدارة."
            )
    except Exception:
        pass


telegram_app.add_error_handler(global_error_handler)

telegram_app.add_handler(MessageHandler(filters.ALL, maintenance_gate), group=-1)
telegram_app.add_handler(CallbackQueryHandler(maintenance_gate), group=-1)

telegram_app.add_handler(ChatMemberHandler(chat_member_watch_callback, ChatMemberHandler.CHAT_MEMBER))
telegram_app.add_handler(CallbackQueryHandler(viol_open_click, pattern="^viol_open:"))
telegram_app.add_handler(CallbackQueryHandler(viol_pay_click, pattern="^viol_pay:"))
telegram_app.add_handler(CallbackQueryHandler(viol_check_click, pattern="^viol_check:"))

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(gate_check_sub_click, pattern="^gate_check_sub$"))
telegram_app.add_handler(CallbackQueryHandler(show_main_group_promo_click, pattern="^show_main_group_promo$"))
telegram_app.add_handler(CommandHandler("admin", admin_panel.admin_command))

telegram_app.add_handler(CallbackQueryHandler(promo_category_chosen, pattern="^promo_cat:"))
telegram_app.add_handler(CallbackQueryHandler(bot_type_picked, pattern="^bot_type_pick:"))
telegram_app.add_handler(CallbackQueryHandler(redeem_code_start_click, pattern="^redeem_code_start$"))
telegram_app.add_handler(CallbackQueryHandler(qty_picked_callback, pattern="^qty_pick:"))
telegram_app.add_handler(CallbackQueryHandler(pay_method_chosen, pattern="^pay_method:"))
telegram_app.add_handler(CallbackQueryHandler(interaction_mode_chosen, pattern="^interaction_mode:"))
telegram_app.add_handler(CallbackQueryHandler(task_join_request_toggle, pattern="^task_join_request$"))
telegram_app.add_handler(CallbackQueryHandler(task_add_ref_link_click, pattern="^task_add_ref_link$"))
telegram_app.add_handler(CallbackQueryHandler(confirm_join_request, pattern="^confirm_join_request$"))
telegram_app.add_handler(CallbackQueryHandler(launch_task_callback, pattern="^launch_task$"))

telegram_app.add_handler(CallbackQueryHandler(show_my_tasks, pattern="^my_tasks:"))
telegram_app.add_handler(CallbackQueryHandler(show_task_detail, pattern="^task_detail:"))
telegram_app.add_handler(CallbackQueryHandler(task_admin_actions,
    pattern="^(task_delete|task_pause|task_notif|task_filter_acc|task_filter_aud|task_completions|task_add|task_editprice):"))

telegram_app.add_handler(CallbackQueryHandler(earn_category_click, pattern="^earn_cat:"))
telegram_app.add_handler(CallbackQueryHandler(earn_bot_type_click, pattern="^earn_bot_type:"))
telegram_app.add_handler(CallbackQueryHandler(earn_check_click, pattern="^earn_check:"))
telegram_app.add_handler(CallbackQueryHandler(earn_view_post, pattern="^earn_view_post:"))
telegram_app.add_handler(CallbackQueryHandler(earn_open_task, pattern="^earn_open:"))
telegram_app.add_handler(CallbackQueryHandler(earn_report_click, pattern="^earn_report:"))
telegram_app.add_handler(CallbackQueryHandler(earn_rules_click, pattern="^earn_rules$"))
telegram_app.add_handler(CallbackQueryHandler(owner_review_action, pattern="^(adpay|adrejc|adrev):"))
telegram_app.add_handler(CallbackQueryHandler(review_open_click, pattern="^review_open:"))

telegram_app.add_handler(CallbackQueryHandler(toggle_notif_click, pattern="^toggle_notif$"))

telegram_app.add_handler(CallbackQueryHandler(chq_menu_click, pattern="^chq_menu:"))
telegram_app.add_handler(CallbackQueryHandler(chq_new_click, pattern="^chq_new:"))
telegram_app.add_handler(CallbackQueryHandler(chq_edit_price_click, pattern="^chq_edit_price$"))
telegram_app.add_handler(CallbackQueryHandler(chq_cancel_click, pattern="^chq_cancel$"))
telegram_app.add_handler(CallbackQueryHandler(chq_confirm_click, pattern="^chq_confirm$"))
telegram_app.add_handler(CallbackQueryHandler(chq_mine_click, pattern="^chq_mine:"))
telegram_app.add_handler(CallbackQueryHandler(chq_view_click, pattern="^chq_view:"))
telegram_app.add_handler(CallbackQueryHandler(chq_link_click, pattern="^chq_link:"))
telegram_app.add_handler(CallbackQueryHandler(chq_send_click, pattern="^chq_send:"))
telegram_app.add_handler(CallbackQueryHandler(chq_comment_click, pattern="^chq_comment:"))
telegram_app.add_handler(CallbackQueryHandler(chq_pass_click, pattern="^chq_pass:"))
telegram_app.add_handler(CallbackQueryHandler(chq_image_click, pattern="^chq_image:"))
telegram_app.add_handler(CallbackQueryHandler(chq_notif_click, pattern="^chq_notif:"))
telegram_app.add_handler(CallbackQueryHandler(chq_delete_click, pattern="^chq_delete:"))
telegram_app.add_handler(CallbackQueryHandler(chq_reqchannel_click, pattern="^chq_reqchannel:"))
telegram_app.add_handler(CallbackQueryHandler(chq_reqchannel_del_click, pattern="^chq_reqchannel_del:"))
telegram_app.add_handler(CallbackQueryHandler(chq_reqchannel_add_click, pattern="^chq_reqchannel_add:"))
telegram_app.add_handler(CallbackQueryHandler(chq_redeem_recheck_click, pattern="^chq_redeem_recheck:"))
telegram_app.add_handler(CallbackQueryHandler(referral_info_click, pattern="^referral_info$"))
telegram_app.add_handler(CallbackQueryHandler(levels_info_click, pattern="^levels_info$"))
telegram_app.add_handler(CallbackQueryHandler(topup_balance_click, pattern="^topup_balance$"))

telegram_app.add_handler(CallbackQueryHandler(generic_nav_callback, pattern="^(back_main|promo_menu|earn_menu|account_menu|promo_auto_settings|checks_menu)$"))

admin_panel.register_handlers(telegram_app)

telegram_app.add_handler(MessageHandler(
    filters.StatusUpdate.USERS_SHARED | filters.StatusUpdate.CHAT_SHARED, got_shared_chat_or_user
))
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
telegram_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generic_text_router))


# ==================== FastAPI ====================

@app.get("/")
def home():
    return {"status": "PR GRAM style bot is running"}


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
    db.init_db()
    await telegram_app.initialize()
    await telegram_app.start()

    try:
        await telegram_app.bot.set_my_commands([
            BotCommand("start", "▶️ بدء استخدام البوت"),
        ])
        await telegram_app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logger.error(f"failed to set bot commands / menu button: {e}")

    railway_url = os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if railway_url:
        webhook_url = f"https://{railway_url}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.warning("لم يتم العثور على رابط Railway — رجاءً اضبط الـ webhook يدوياً.")


@app.on_event("shutdown")
async def shutdown_event():
    await telegram_app.stop()
    await telegram_app.shutdown()
