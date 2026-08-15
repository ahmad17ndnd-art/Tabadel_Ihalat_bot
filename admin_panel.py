"""
admin_panel.py — لوحة تحكم الأدمن الشاملة:
- التحكم بنقاط/أسعار وعمولة كل فئة مهمة
- إنشاء أي عدد من روابط الإحالة الخاصة بقيمة حرة (مجانية للمستخدم)
- إرسال رسائل جماعية أو لمستخدم محدد
- هدية نقاط لمستخدم أو للجميع
- إحصائيات بيئية متكاملة
- إدارة المستخدمين (حظر/تفاصيل)
"""
import secrets
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

import db
import config

_app = None  # سيتم حقن telegram_app هنا عبر register_handlers()

USERS_PAGE_SIZE = 8


def fmt(n):
    return f"{n:,}"


def back_kb(cb):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=cb)]])


async def send_step(update, context, text, reply_markup=None, key="admstep"):
    """يرسل الخطوة الأولى برسالة جديدة، وبعدها يعدّل نفس الرسالة بكل خطوة تالية بدل ما يكدّس رسائل جديدة."""
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get(f"stepmsg:{key}")
    if msg_id:
        try:
            await _app.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    if update.callback_query:
        sent = await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        sent = await update.message.reply_text(text, reply_markup=reply_markup)
    context.user_data[f"stepmsg:{key}"] = sent.message_id


def clear_step(context, key="admstep"):
    context.user_data.pop(f"stepmsg:{key}", None)


# ==================== أمر /admin ولوحة الدخول ====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not config.is_admin(update.effective_user.id):
        return
    # نضمن ظهور القائمة السفلية الرئيسية حتى لو ما ضغط الأدمن /start من قبل أو انحذفت بمكان تاني
    await update.effective_message.reply_text("🧑‍💼", reply_markup=config.main_reply_keyboard())
    await send_admin_home(update.effective_message)


async def send_admin_home(message):
    s = db.get_settings()
    bot_status = "🟢 يعمل" if s.get("bot_enabled", 1) else "🔴 موقوف"
    gate_status = "🟢 مفعّل" if s.get("mandatory_gate_enabled") else "🔴 موقوف"
    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات بيئية متكاملة", callback_data="adm_stats")],
        [InlineKeyboardButton("💵 أسعار وعمولة الفئات", callback_data="adm_prices")],
        [InlineKeyboardButton("🔗 روابط الإحالة الخاصة", callback_data="adm_reflinks")],
        [InlineKeyboardButton("📢 نشر مهمة (مجاناً)", callback_data="adm_publish_start")],
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_ubrowse:0")],
        [InlineKeyboardButton("📨 رسالة جماعية", callback_data="adm_broadcast")],
        [InlineKeyboardButton("💬 رسالة لمستخدم محدد", callback_data="adm_dm")],
        [InlineKeyboardButton("🎁 الهدايا", callback_data="adm_gifts_menu")],
        [InlineKeyboardButton("🏆 المسابقات", callback_data="adm_comp_menu")],
        [InlineKeyboardButton("📈 تضخيم الإحصائيات العامة", callback_data="adm_boost_menu")],
        [InlineKeyboardButton("⭐ إعدادات النجوم", callback_data="adm_stars_settings")],
        [InlineKeyboardButton("🌐 إعدادات الإحالة", callback_data="adm_ref_settings")],
        [InlineKeyboardButton("💳 إعدادات الشيكات", callback_data="adm_cheque_settings")],
        [InlineKeyboardButton("🚨 حماية إلغاء الاشتراك المبكر", callback_data="adm_protection_settings")],
        [InlineKeyboardButton("🎁 هدية الترحيب للأعضاء الجدد", callback_data="adm_welcome_gift")],
        [InlineKeyboardButton("✏️ النصوص العامة", callback_data="adm_texts")],
        [InlineKeyboardButton(f"⏯️ حالة البوت: {bot_status}", callback_data="adm_toggle_bot")],
        [InlineKeyboardButton(f"🚪 الاشتراك الإجباري: {gate_status}", callback_data="adm_gate_settings")],
    ]
    await message.reply_text("🧑‍💼 لوحة تحكم الأدمن الشاملة:", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    await send_admin_home(query.message)


async def gifts_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    keyboard = [
        [InlineKeyboardButton("🎁 أكواد الهدايا", callback_data="adm_giftcodes")],
        [InlineKeyboardButton("🎁 هدية نقاط لمستخدم", callback_data="adm_gift_user")],
        [InlineKeyboardButton("🎁 هدية نقاط للجميع", callback_data="adm_gift_all")],
        [InlineKeyboardButton("➖ حذف نقاط من مستخدم", callback_data="adm_deduct_user")],
        [InlineKeyboardButton("➖ حذف نقاط من الجميع", callback_data="adm_deduct_all")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")],
    ]
    await query.message.edit_text("🎁 قائمة الهدايا:", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== 📊 إحصائيات بيئية متكاملة ====================

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.full_stats()
    cats_txt = "\n".join([f"  • {db.CATEGORY_LABELS.get(k,k)}: {v}" for k, v in s["tasks_by_category"].items()]) or "  لا يوجد"
    top_bal = "\n".join([f"  🏅 {u or '—'} — {fmt(b)} ليرة" for u, b in s["top_balances"][:5]]) or "  لا يوجد"
    top_ref = "\n".join([f"  🌟 {u or '—'} — {r} إحالة" for u, r in s["top_referrers"][:5]]) or "  لا يوجد"

    text = (
        "📊 الإحصائيات البيئية المتكاملة\n\n"
        "👥 المستخدمون:\n"
        f"  الإجمالي: {fmt(s['total_users'])} | النشطون: {fmt(s['active_users'])} | المحظورون: {fmt(s['banned_users'])}\n"
        f"  المفعّلون: {fmt(s['verified_users'])} | جدد اليوم: {fmt(s['new_today'])}\n\n"
        "💰 الأموال:\n"
        f"  إجمالي أرصدة المستخدمين: {fmt(s['total_balance'])} ليرة\n"
        f"  إجمالي المنفق على المهام: {fmt(s['total_spent_on_tasks'])} ليرة\n"
        f"  إيرادات النجوم: {fmt(s['total_stars_revenue'])} ⭐ ({s['stars_payments_count']} عملية)\n\n"
        "📢 المهام:\n"
        f"  الإجمالي: {fmt(s['total_tasks'])} | النشطة: {fmt(s['active_tasks'])} | المكتملة: {fmt(s['completed_tasks'])}\n"
        f"  تنفيذات مقبولة: {fmt(s['approved_completions'])} | بانتظار المراجعة: {fmt(s['pending_reviews'])}\n"
        f"  حسب الفئة:\n{cats_txt}\n\n"
        "👥 الإحالات:\n"
        f"  إجمالي الإحالات: {fmt(s['total_referrals'])}\n\n"
        f"🏆 أعلى 5 أرصدة:\n{top_bal}\n\n"
        f"🌟 أعلى 5 محيلين:\n{top_ref}"
    )
    await query.message.reply_text(text, reply_markup=back_kb("adm_home"))


# ==================== 💵 أسعار وعمولة الفئات ====================

async def show_prices_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    settings = db.get_settings()
    keyboard = []
    for cat in db.CATEGORY_ORDER:
        if cat in ("premium", "bot"):
            continue
        mn, sug, mx = db.get_category_price(cat)
        keyboard.append([InlineKeyboardButton(
            f"{db.CATEGORY_LABELS[cat]} — أدنى {fmt(mn)} / مقترح {fmt(sug)}", callback_data=f"adm_price_edit:cat:{cat}"
        )])
    for bt in db.BOT_TYPE_ORDER:
        mn, sug, mx = db.get_bot_type_price(bt)
        keyboard.append([InlineKeyboardButton(
            f"{db.BOT_TYPES[bt]} — أدنى {fmt(mn)} / مقترح {fmt(sug)}", callback_data=f"adm_price_edit:bot:{bt}"
        )])
    keyboard.append([InlineKeyboardButton(f"⚙️ العمولة العامة الحالية: {settings['commission_percent']}%", callback_data="adm_commission_edit")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")])
    await query.message.reply_text("💵 تحكم كامل بأسعار وعمولة كل فئة مهمة:", reply_markup=InlineKeyboardMarkup(keyboard))


async def price_edit_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    _, kind, key = query.data.split(":")  # kind = cat | bot
    context.user_data["price_draft"] = {"kind": kind, "key": key}
    context.user_data["awaiting"] = "admin:price_min"
    label = db.CATEGORY_LABELS.get(key, key) if kind == "cat" else db.BOT_TYPES.get(key, key)
    await send_step(update, context, f"✍️ فئة: {label}\n\nاكتب الحد الأدنى الذي تريده:", key="price_flow")


async def commission_edit_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:commission"
    await query.message.reply_text("✍️ أرسل نسبة العمولة الجديدة (رقم فقط، مثال: 15):")


# ==================== 🔗 روابط الإحالة الخاصة ====================

async def show_reflinks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    links = db.list_admin_ref_links()
    keyboard = []
    for link_id, code, title, reward, max_uses, uses, active in links[:15]:
        status = "🟢" if active else "🔴"
        used_txt = f"{uses}/{max_uses if max_uses else '∞'}"
        keyboard.append([InlineKeyboardButton(
            f"{status} {title or code} — {fmt(reward)} ليرة ({used_txt})", callback_data=f"adm_reflink_view:{link_id}"
        )])
    keyboard.append([InlineKeyboardButton("➕ إنشاء رابط إحالة جديد", callback_data="adm_reflink_new")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")])
    text = "🔗 روابط الإحالة الخاصة — أنشئ أي رابط دعوة بقيمة تحددها أنت مجاناً للمستخدم:"
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def reflink_new_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:reflink_new"
    await query.message.reply_text(
        "✍️ أرسل بيانات الرابط الجديد بالترتيب التالي مفصولة بمسافات:\n"
        "<عنوان_بدون_مسافات> <قيمة_المكافأة_بالليرة> <الحد_الأقصى_للاستخدام (0=بلا حد)>\n"
        "مثال: حملة_رمضان 5000 100"
    )


async def reflink_view_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    link_id = int(query.data.split(":")[1])
    links = db.list_admin_ref_links()
    match = next((l for l in links if l[0] == link_id), None)
    if not match:
        await query.message.reply_text("❌ الرابط غير موجود.")
        return
    _, code, title, reward, max_uses, uses, active = match
    me = await _app.bot.get_me()
    full_link = f"https://t.me/{me.username}?start=arl_{code}"
    text = (
        f"🔗 {title or code}\n"
        f"الرابط: {full_link}\n"
        f"القيمة: {fmt(reward)} ليرة لكل مستخدم جديد\n"
        f"الاستخدام: {uses}/{max_uses if max_uses else '∞'}\n"
        f"الحالة: {'مفعّل 🟢' if active else 'متوقف 🔴'}"
    )
    toggle_label = "⏸️ إيقاف الرابط" if active else "▶️ تفعيل الرابط"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data=f"adm_reflink_toggle:{link_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_reflinks")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def reflink_toggle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    link_id = int(query.data.split(":")[1])
    db.toggle_admin_ref_link(link_id)
    await query.message.reply_text("✔ تم تحديث حالة الرابط.")


# ==================== 👥 إدارة المستخدمين ====================

async def show_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    page = int(query.data.split(":")[1])
    total = db.count_users()
    offset = page * USERS_PAGE_SIZE
    rows = db.get_users_page(offset, USERS_PAGE_SIZE)
    if not rows:
        await query.message.reply_text("لا يوجد مستخدمين.")
        return
    keyboard = []
    for uid, uname, bal, banned in rows:
        mark = "🚫" if banned else "✅"
        keyboard.append([InlineKeyboardButton(f"{mark} {uname or uid} • {fmt(bal)}", callback_data=f"adm_usel:{uid}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_ubrowse:{page-1}"))
    if offset + USERS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_ubrowse:{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")])
    await query.message.edit_text(f"👥 المستخدمون (صفحة {page+1}):", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    _, uid_s, page_s = query.data.split(":")
    uid, page = int(uid_s), int(page_s)
    u = db.get_user(uid)
    if not u:
        await query.message.reply_text("المستخدم غير موجود.")
        return
    text = (
        f"👤 {u['username']}\nID: {u['user_id']}\n"
        f"💰 الرصيد: {fmt(u['balance'])} ليرة\n"
        f"👥 الإحالات: {u['referrals_count']}\n"
        f"الحالة: {'محظور 🚫' if u['banned'] else 'نشط ✅'}\n"
        f"🗓 الانضمام: {u['joined_at']}"
    )
    ban_label = "✅ إلغاء الحظر" if u["banned"] else "🚫 حظر"
    keyboard = [
        [InlineKeyboardButton("💬 رسالة له", callback_data=f"adm_dm_user:{uid}")],
        [InlineKeyboardButton("🎁 هدية نقاط له", callback_data=f"adm_gift_user_id:{uid}")],
        [InlineKeyboardButton("➖ حذف نقاط منه", callback_data=f"adm_deduct_user_id:{uid}")],
        [InlineKeyboardButton("✏️ تعديل عدد إحالاته", callback_data=f"adm_setref_user_id:{uid}")],
        [InlineKeyboardButton(ban_label, callback_data=f"adm_toggleban:{uid}:{page}")],
        [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data=f"adm_ubrowse:{page}")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def toggle_ban_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    _, uid_s, page_s = query.data.split(":")
    uid = int(uid_s)
    new_val = db.toggle_ban(uid)
    if new_val is None:
        await query.message.reply_text("المستخدم غير موجود.")
    else:
        await query.message.reply_text("🚫 تم الحظر" if new_val else "✅ تم إلغاء الحظر")


# ==================== 📨 رسائل ====================

async def broadcast_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:broadcast"
    await query.message.reply_text("📨 أرسل نص الرسالة الجماعية (سترسل لكل المستخدمين غير المحظورين):")


PICK_PAGE_SIZE = 8


async def show_user_pick_list(update: Update, context: ContextTypes.DEFAULT_TYPE, purpose, page):
    """قائمة مصغّرة بالمستخدمين (الأحدث أولاً) لاختيار مين تراسله أو تعطيه هدية، بدل كتابة الـ ID يدوياً."""
    total = db.count_users()
    offset = page * PICK_PAGE_SIZE
    rows = db.get_users_page(offset, PICK_PAGE_SIZE)
    if not rows:
        target = update.callback_query.message if update.callback_query else update.effective_message
        await target.reply_text("لا يوجد مستخدمين.")
        return
    keyboard = []
    for uid, uname, bal, banned in rows:
        mark = "🚫" if banned else "👤"
        keyboard.append([InlineKeyboardButton(f"{mark} {uname or uid} • {fmt(bal)}", callback_data=f"adm_pick:{purpose}:{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_pickpage:{purpose}:{page-1}"))
    if offset + PICK_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_pickpage:{purpose}:{page+1}"))
    if nav:
        keyboard.append(nav)
    back_targets = {
        "dm": "adm_home",
        "gift": "adm_gifts_menu",
        "deduct": "adm_gifts_menu",
        "setref": "adm_comp_menu",
    }
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_targets.get(purpose, "adm_home"))])
    labels = {
        "dm": "💬 اختر مين تراسله (الأحدث أولاً):",
        "gift": "🎁 اختر مين تعطيه هدية (الأحدث أولاً):",
        "deduct": "➖ اختر مين تحذف من رصيده (الأحدث أولاً):",
        "setref": "✏️ اختر مين تعدّل عدد إحالاته (الأحدث أولاً):",
    }
    label = labels.get(purpose, labels["gift"])
    if update.callback_query:
        await update.callback_query.message.edit_text(label, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.effective_message.reply_text(label, reply_markup=InlineKeyboardMarkup(keyboard))


async def pick_page_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    _, purpose, page_s = query.data.split(":")
    await show_user_pick_list(update, context, purpose, int(page_s))


async def pick_user_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    _, purpose, uid_s = query.data.split(":")
    uid = int(uid_s)
    if purpose == "dm":
        context.user_data["dm_target"] = uid
        context.user_data["awaiting"] = "admin:dm_text"
        await query.message.reply_text(f"💬 أرسل نص الرسالة للمستخدم {uid}:")
    elif purpose == "deduct":
        context.user_data["deduct_target"] = uid
        context.user_data["awaiting"] = "admin:deduct_amount"
        u = db.get_user(uid)
        bal = u["balance"] if u else 0
        await query.message.reply_text(f"➖ رصيد المستخدم {uid} الحالي: {fmt(bal)} ليرة.\nكم عدد النقاط التي تريد حذفها؟")
    elif purpose == "setref":
        context.user_data["setref_target"] = uid
        context.user_data["awaiting"] = "admin:setref_amount"
        u = db.get_user(uid)
        cur_count = u["referrals_count"] if u else 0
        await query.message.reply_text(f"✏️ عدد إحالات المستخدم {uid} الحالي: {fmt(cur_count)}.\nكم تريد أن يصبح عدد إحالاته؟")
    else:
        context.user_data["gift_target"] = uid
        context.user_data["awaiting"] = "admin:gift_amount"
        await query.message.reply_text(f"🎁 كم عدد النقاط التي تريد منحها للمستخدم {uid}؟")


async def dm_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    await show_user_pick_list(update, context, "dm", 0)


async def dm_user_direct_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    uid = int(query.data.split(":")[1])
    context.user_data["dm_target"] = uid
    context.user_data["awaiting"] = "admin:dm_text"
    await query.message.reply_text(f"💬 أرسل نص الرسالة للمستخدم {uid}:")


# ==================== 📢 نشر مهمة مجاناً (صلاحية أدمن) ====================

async def publish_start_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["draft_task"] = {"admin_free": True}
    keyboard = [
        [InlineKeyboardButton("🤖 بوت", callback_data="promo_cat:bot")],
        [InlineKeyboardButton("🔮 مجموعة", callback_data="promo_cat:group")],
        [InlineKeyboardButton("📢 قناة", callback_data="promo_cat:channel")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")],
    ]
    await query.message.reply_text(
        "📢 اختر نوع المهمة التي تريد نشرها — رح تنشر مباشرة من دون خصم أي رصيد منك (صلاحية أدمن):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==================== 🎁 أكواد الهدايا ====================

async def show_giftcodes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    codes = db.list_gift_codes()
    keyboard = []
    for code_id, code, points, max_uses, uses, active in codes[:15]:
        status = "🟢" if active else "🔴"
        used_txt = f"{uses}/{max_uses if max_uses else '∞'}"
        keyboard.append([InlineKeyboardButton(f"{status} {code} — {fmt(points)} ({used_txt})", callback_data=f"adm_giftcode_toggle:{code_id}")])
    keyboard.append([InlineKeyboardButton("➕ إنشاء كود جديد", callback_data="adm_giftcode_new")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_gifts_menu")])
    await query.message.reply_text("🎁 أكواد الهدايا (اضغط على أي كود لتفعيله/إيقافه):", reply_markup=InlineKeyboardMarkup(keyboard))


async def giftcode_new_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:giftcode_code"
    await send_step(update, context, "✍️ اكتب الكود الذي تريده (بدون مسافات، مثال: WELCOME2026):", key="giftcode_flow")


async def giftcode_toggle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    code_id = int(query.data.split(":")[1])
    db.toggle_gift_code(code_id)
    await query.message.reply_text("✔ تم تحديث حالة الكود.")


# ==================== 🎁 هدية نقاط ====================

async def gift_user_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    await show_user_pick_list(update, context, "gift", 0)


async def gift_user_direct_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    uid = int(query.data.split(":")[1])
    context.user_data["gift_target"] = uid
    context.user_data["awaiting"] = "admin:gift_amount"
    await query.message.reply_text(f"🎁 كم عدد النقاط التي تريد منحها للمستخدم {uid}؟")


async def gift_all_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:gift_all_amount"
    await query.message.reply_text("🎁 كم عدد النقاط التي تريد منحها لكل المستخدمين غير المحظورين؟")


# ==================== ➖ حذف نقاط ====================

async def deduct_user_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    await show_user_pick_list(update, context, "deduct", 0)


async def deduct_user_direct_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    uid = int(query.data.split(":")[1])
    context.user_data["deduct_target"] = uid
    context.user_data["awaiting"] = "admin:deduct_amount"
    u = db.get_user(uid)
    bal = u["balance"] if u else 0
    await query.message.reply_text(f"➖ رصيد المستخدم {uid} الحالي: {fmt(bal)} ليرة.\nكم عدد النقاط التي تريد حذفها؟")


async def deduct_all_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:deduct_all_amount"
    await query.message.reply_text(
        "➖ كم عدد النقاط التي تريد حذفها من كل المستخدمين غير المحظورين؟\n"
        "⚠️ أي مستخدم رصيده أقل من هذا المبلغ رح يتم تجاوزه (ما رح ينزل رصيده تحت الصفر)."
    )


async def setref_user_direct_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    uid = int(query.data.split(":")[1])
    context.user_data["setref_target"] = uid
    context.user_data["awaiting"] = "admin:setref_amount"
    u = db.get_user(uid)
    cur_count = u["referrals_count"] if u else 0
    await query.message.reply_text(f"✏️ عدد إحالات المستخدم {uid} الحالي: {fmt(cur_count)}.\nكم تريد أن يصبح عدد إحالاته؟")


# ==================== 🏆 المسابقات ====================

async def comp_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    comp = db.get_active_competition()
    if comp:
        status_line = f"📌 المسابقة الحالية: {comp['title']}"
    else:
        status_line = "📌 ما في مسابقة حالياً."
    keyboard = [
        [InlineKeyboardButton("➕ إنشاء مسابقة جديدة", callback_data="adm_comp_new")],
        [InlineKeyboardButton("✏️ تعديل عدد إحالات مستخدم", callback_data="adm_setref_user")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")],
    ]
    await query.message.edit_text(f"🏆 قائمة المسابقات\n{status_line}", reply_markup=InlineKeyboardMarkup(keyboard))


async def comp_new_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["comp_draft"] = {}
    context.user_data["awaiting"] = "admin:comp_title"
    await send_step(update, context, "✍️ اكتب عنوان المسابقة (مثال: مسابقة أكثر الإحالات):", key="comp_flow")


async def setref_user_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    await show_user_pick_list(update, context, "setref", 0)


# ==================== 📈 تضخيم الإحصائيات العامة ====================

BOOST_FIELDS = {
    "users": ("boost_users", "عدد المستخدمين"),
    "tasks": ("boost_tasks", "المهام المكتملة"),
    "referrals": ("boost_referrals", "عدد الإحالات"),
}


async def boost_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    keyboard = [
        [InlineKeyboardButton("➕ زيادة عدد المستخدمين", callback_data="adm_boost_add:users")],
        [InlineKeyboardButton("➕ زيادة المهام المكتملة", callback_data="adm_boost_add:tasks")],
        [InlineKeyboardButton("➕ زيادة عدد الإحالات", callback_data="adm_boost_add:referrals")],
        [InlineKeyboardButton("👁️ عرض الإضافات الحالية", callback_data="adm_boost_view")],
        [InlineKeyboardButton("🗑 تصفير الإضافات", callback_data="adm_boost_reset_menu")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")],
    ]
    await query.message.edit_text(
        "📈 تضخيم الإحصائيات المعروضة للمستخدمين\n"
        "(الأرقام هون بتنضاف فوق الأرقام الحقيقية بالعرض العام بس، وما بتأثر على إحصائياتك الداخلية الحقيقية)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def boost_add_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    code = query.data.split(":")[1]
    field, label = BOOST_FIELDS[code]
    context.user_data["boost_add_field"] = field
    context.user_data["boost_add_label"] = label
    context.user_data["awaiting"] = "admin:boost_add_amount"
    await query.message.reply_text(f"➕ كم تريد أن تزيد {label}؟ (اكتب رقم)")


async def boost_view_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    stats = db.full_stats()
    bu, bt, br = s.get("boost_users", 0) or 0, s.get("boost_tasks", 0) or 0, s.get("boost_referrals", 0) or 0
    text = (
        "👁️ الإضافات الوهمية الحالية (فوق الأرقام الحقيقية):\n\n"
        f"👥 عدد المستخدمين: أضفت {fmt(bu)}\n   الحقيقي: {fmt(stats['total_users'])} | المعروض: {fmt(stats['total_users'] + bu)}\n\n"
        f"✅ المهام المكتملة: أضفت {fmt(bt)}\n   الحقيقي: {fmt(stats['completed_tasks'])} | المعروض: {fmt(stats['completed_tasks'] + bt)}\n\n"
        f"🌟 عدد الإحالات: أضفت {fmt(br)}\n   الحقيقي: {fmt(stats['total_referrals'])} | المعروض: {fmt(stats['total_referrals'] + br)}"
    )
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="adm_boost_menu")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def boost_reset_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    keyboard = [
        [InlineKeyboardButton("🗑 تصفير عدد المستخدمين", callback_data="adm_boost_reset:users")],
        [InlineKeyboardButton("🗑 تصفير المهام المكتملة", callback_data="adm_boost_reset:tasks")],
        [InlineKeyboardButton("🗑 تصفير عدد الإحالات", callback_data="adm_boost_reset:referrals")],
        [InlineKeyboardButton("🗑 تصفير الكل", callback_data="adm_boost_reset:all")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_boost_menu")],
    ]
    await query.message.edit_text("🗑 اختر شو بدك تصفّر:", reply_markup=InlineKeyboardMarkup(keyboard))


async def boost_reset_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    code = query.data.split(":")[1]
    if code == "all":
        for field, _ in BOOST_FIELDS.values():
            db.reset_boost(field)
        await query.message.edit_text("✔ تم تصفير كل الإضافات.")
    else:
        field, label = BOOST_FIELDS[code]
        db.reset_boost(field)
        await query.message.edit_text(f"✔ تم تصفير إضافات {label}.")


# ==================== ⭐ إعدادات النجوم و🌐 إعدادات الإحالة و✏️ النصوص ====================

async def cheque_settings_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:cheque_commission"
    await send_step(update, context, "💳 اكتب نسبة العمولة % على إنشاء الشيكات:", key="cheque_settings_flow")


async def welcome_gift_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    context.user_data["awaiting"] = "admin:welcome_gift"
    await query.message.reply_text(
        f"🎁 القيمة الحالية لهدية الترحيب: {fmt(s['welcome_gift_points'])} ليرة (تُمنح تلقائياً لأي مستخدم جديد أول ما يضغط /start).\n"
        f"✍️ اكتب القيمة الجديدة (0 = تعطيل الهدية):"
    )


async def protection_settings_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    context.user_data["awaiting"] = "admin:protection_watchdays"
    await send_step(
        update, context,
        f"🚨 حماية إلغاء الاشتراك المبكر\nمدة المراقبة الحالية: {s['sub_watch_days']} يوم\n\n"
        f"إذا ألغى المستخدم اشتراكه بقناة/مجموعة كسب منها نقاط قبل ما تمر هالمدة، "
        f"بيتحظر من كل قسم «🗂️ الأرباح» لحد ما يدفع غرامة بقيمة الربح أو يرجع يشترك.\n\n"
        f"📅 اكتب عدد أيام المراقبة الجديد:",
        key="protection_flow"
    )

async def stars_settings_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:stars_rate"
    await send_step(update, context, "⭐ اكتب سعر صرف النجمة الواحدة بالليرة (كم ليرة تساوي كل نجمة):", key="stars_flow")


async def ref_settings_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    context.user_data["awaiting"] = "admin:ref_premium"
    await send_step(update, context, "🌐 اكتب مكافأة إحالة مستخدم لديه Premium (بالليرة):", key="ref_flow")


async def texts_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    templates = db.get_all_templates()
    keyboard = [[InlineKeyboardButton(label, callback_data=f"adm_text_edit:{key}")] for key, label, desc, content in templates]
    keyboard.append([InlineKeyboardButton("✏️ قناة الاشتراك الإلزامي", callback_data="adm_text_edit_mandatory")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")])
    await query.message.reply_text("✏️ اختر الرسالة التي تريد تعديلها — كل زر رح يوريك شرح مين بيشوفها وين:", reply_markup=InlineKeyboardMarkup(keyboard))


async def text_edit_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    key = query.data.split(":")[1]
    tpl = db.get_template(key)
    if not tpl:
        await query.message.reply_text("❌ هذا النص غير موجود.")
        return
    context.user_data["awaiting"] = f"admin:text:{key}"
    await query.message.reply_text(
        f"ℹ️ {tpl['description']}\n\n"
        f"📄 النص الحالي:\n{tpl['content']}\n\n"
        f"✍️ أرسل الآن النص الجديد ليحل مكانه:"
    )


async def text_edit_mandatory_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    context.user_data["awaiting"] = "admin:mandatory_channel"
    await query.message.reply_text(
        f"ℹ️ هذه القناة يتحقق منها البوت عند الضغط على «🔍 فحص الاشتراك».\n"
        f"القيمة الحالية: {s['mandatory_sub_channel'] or 'غير محددة'}\n\n"
        f"✍️ أرسل معرّف القناة (مثال: @my_channel)، أو أرسل - لإلغائها:"
    )


# ==================== ⏯️ تشغيل/إيقاف البوت ====================

async def toggle_bot_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    if s.get("bot_enabled", 1):
        db.update_setting("bot_enabled", 0)
        context.user_data["awaiting"] = "admin:maintenance_msg"
        tpl = db.get_template("maintenance_msg")
        await query.message.reply_text(
            f"🔴 تم إيقاف البوت عن كل المستخدمين (عدا الأدمن).\n\n"
            f"الرسالة الحالية التي ستظهر لهم: {tpl['content'] if tpl else ''}\n\n"
            f"✍️ اكتب رسالة جديدة الآن ليشوفوها، أو تجاهل هالرسالة وخلي القديمة زي ما هي."
        )
    else:
        db.update_setting("bot_enabled", 1)
        await query.message.reply_text("🟢 تم تشغيل البوت من جديد لكل المستخدمين.")


# ==================== 🚪 إعدادات الاشتراك الإجباري (بوابة /start) ====================

async def gate_settings_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    status = "🟢 مفعّل" if s.get("mandatory_gate_enabled") else "🔴 موقوف"
    toggle_label = "⏸️ إيقاف البوابة" if s.get("mandatory_gate_enabled") else "▶️ تفعيل البوابة"
    text = (
        f"🚪 بوابة الاشتراك الإجباري عند /start\n"
        f"الحالة: {status}\n"
        f"القناة/المجموعة الحالية: {s['mandatory_sub_channel'] or 'غير محددة'}\n\n"
        f"لما تكون مفعّلة، أي مستخدم جديد (غير أدمن) ما رح يقدر يستخدم البوت إلا بعد ما يشترك بالقناة/المجموعة المحددة."
    )
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="adm_gate_toggle")],
        [InlineKeyboardButton("✏️ تحديد القناة/المجموعة", callback_data="adm_text_edit_mandatory")],
        [InlineKeyboardButton("✏️ تعديل رسالة البوابة", callback_data="adm_text_edit:mandatory_gate_msg")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_home")],
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def gate_toggle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not config.is_admin(update.effective_user.id):
        return
    s = db.get_settings()
    if not s.get("mandatory_gate_enabled") and not s.get("mandatory_sub_channel"):
        await query.message.reply_text("⚠️ حدّد القناة/المجموعة أولاً قبل تفعيل البوابة.")
        return
    db.update_setting("mandatory_gate_enabled", 0 if s.get("mandatory_gate_enabled") else 1)
    await query.message.reply_text("✔ تم تحديث حالة البوابة.")


# ==================== موجّه النصوص الخاص بالأدمن ====================

async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: str):
    if not config.is_admin(update.effective_user.id):
        return
    text = update.message.text.strip()
    parts = awaiting.split(":")
    kind = parts[1]

    if kind == "price_min":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
            return
        draft = context.user_data.get("price_draft", {})
        draft["min"] = int(text)
        context.user_data["price_draft"] = draft
        context.user_data["awaiting"] = "admin:price_max"
        label = db.CATEGORY_LABELS.get(draft["key"], draft["key"]) if draft["kind"] == "cat" else db.BOT_TYPES.get(draft["key"], draft["key"])
        await send_step(update, context, f"✍️ فئة: {label}\nالحد الأدنى: {fmt(draft['min'])}\n\nاكتب الحد الأقصى (اكتب 0 إذا ما بدك حد أقصى):", key="price_flow")

    elif kind == "price_max":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
            return
        draft = context.user_data.get("price_draft", {})
        draft["max"] = int(text)
        context.user_data["price_draft"] = draft
        context.user_data["awaiting"] = "admin:price_sug"
        label = db.CATEGORY_LABELS.get(draft["key"], draft["key"]) if draft["kind"] == "cat" else db.BOT_TYPES.get(draft["key"], draft["key"])
        await send_step(
            update, context,
            f"✍️ فئة: {label}\nالحد الأدنى: {fmt(draft['min'])} | الحد الأقصى: {fmt(draft['max']) if draft['max'] else 'بلا حد'}\n\n"
            f"اكتب السعر المقترح لإظهاره للمستخدم (اكتب 0 حتى ما يظهر سعر مقترح إطلاقاً):",
            key="price_flow"
        )

    elif kind == "price_sug":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
            return
        draft = context.user_data.get("price_draft", {})
        sug = int(text)
        mn, mx = draft["min"], draft["max"]
        if draft["kind"] == "cat":
            db.set_category_price(draft["key"], mn, sug, mx)
            label = db.CATEGORY_LABELS.get(draft["key"], draft["key"])
        else:
            db.set_bot_type_price(draft["key"], mn, sug, mx)
            label = db.BOT_TYPES.get(draft["key"], draft["key"])
        context.user_data["awaiting"] = None
        context.user_data.pop("price_draft", None)
        clear_step(context, key="price_flow")
        sug_txt = f"مقترح {fmt(sug)} ليرة" if sug else "بدون سعر مقترح"
        await update.message.reply_text(
            f"✔ تم تحديث أسعار {label}: أدنى {fmt(mn)} / {sug_txt} / أقصى {fmt(mx) if mx else 'بلا حد'}"
        )

    elif kind == "commission":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        db.update_setting("commission_percent", int(text))
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✔ تم تحديث العمولة العامة إلى {text}%")

    elif kind == "reflink_new":
        vals = text.split()
        if len(vals) != 3 or not vals[1].isdigit() or not vals[2].isdigit():
            await update.message.reply_text("❌ صيغة غير صحيحة. مثال: حملة_رمضان 5000 100")
            return
        title, reward_s, max_uses_s = vals
        code = secrets.token_hex(4)
        db.create_admin_ref_link(code, title, int(reward_s), int(max_uses_s), update.effective_user.id)
        me = await _app.bot.get_me()
        context.user_data["awaiting"] = None
        await update.message.reply_text(
            f"✅ تم إنشاء الرابط:\nhttps://t.me/{me.username}?start=arl_{code}\n"
            f"القيمة: {fmt(int(reward_s))} ليرة لكل مستخدم جديد."
        )

    elif kind == "broadcast":
        user_ids = db.get_all_active_user_ids()
        sent = 0
        for uid in user_ids:
            try:
                await _app.bot.send_message(uid, text)
                sent += 1
            except Exception:
                pass
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✔ تم إرسال الرسالة إلى {sent} مستخدم.")

    elif kind == "dm_ask_id":
        if not text.isdigit():
            await update.message.reply_text("❌ الرجاء إرسال ID صحيح.")
            return
        context.user_data["dm_target"] = int(text)
        context.user_data["awaiting"] = "admin:dm_text"
        await update.message.reply_text("💬 أرسل نص الرسالة الآن:")

    elif kind == "dm_text":
        target = context.user_data.get("dm_target")
        try:
            await _app.bot.send_message(target, text)
            await update.message.reply_text("✔ تم الإرسال.")
        except Exception as e:
            await update.message.reply_text(f"خطأ أثناء الإرسال: {e}")
        context.user_data["awaiting"] = None
        context.user_data.pop("dm_target", None)

    elif kind == "gift_ask_id":
        if not text.isdigit():
            await update.message.reply_text("❌ الرجاء إرسال ID صحيح.")
            return
        context.user_data["gift_target"] = int(text)
        context.user_data["awaiting"] = "admin:gift_amount"
        await update.message.reply_text("🎁 كم عدد النقاط؟")

    elif kind == "gift_amount":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        target = context.user_data.get("gift_target")
        amount = int(text)
        db.add_balance(target, amount, kind="admin_gift", note="هدية من الأدمن")
        try:
            await _app.bot.send_message(target, f"🎁 وصلتك هدية نقاط من الإدارة: +{fmt(amount)} ليرة")
        except Exception:
            pass
        context.user_data["awaiting"] = None
        context.user_data.pop("gift_target", None)
        await update.message.reply_text(f"✔ تم منح {fmt(amount)} ليرة للمستخدم {target}.")

    elif kind == "gift_all_amount":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        amount = int(text)
        user_ids = db.get_all_active_user_ids()
        for uid in user_ids:
            db.add_balance(uid, amount, kind="admin_gift_all", note="هدية جماعية من الأدمن")
            try:
                await _app.bot.send_message(uid, f"🎁 هدية نقاط من الإدارة لجميع المستخدمين: +{fmt(amount)} ليرة")
            except Exception:
                pass
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✔ تم منح {fmt(amount)} ليرة لعدد {len(user_ids)} مستخدم.")

    elif kind == "deduct_amount":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        target = context.user_data.get("deduct_target")
        amount = int(text)
        u = db.get_user(target)
        bal = u["balance"] if u else 0
        if amount > bal:
            await update.message.reply_text(
                f"❌ رصيد المستخدم الحالي {fmt(bal)} ليرة فقط، وهو أقل من {fmt(amount)} ليرة.\n"
                f"أرسل رقم أصغر أو يساوي {fmt(bal)}."
            )
            return
        db.add_balance(target, -amount, kind="admin_deduct", note="حذف نقاط من الإدارة")
        try:
            await _app.bot.send_message(target, f"➖ تم خصم {fmt(amount)} ليرة من رصيدك من قبل الإدارة.")
        except Exception:
            pass
        context.user_data["awaiting"] = None
        context.user_data.pop("deduct_target", None)
        await update.message.reply_text(f"✔ تم حذف {fmt(amount)} ليرة من رصيد المستخدم {target}.")

    elif kind == "deduct_all_amount":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        amount = int(text)
        user_ids = db.get_all_active_user_ids()
        deducted, skipped = 0, 0
        for uid in user_ids:
            u = db.get_user(uid)
            bal = u["balance"] if u else 0
            if amount > bal:
                skipped += 1
                continue
            db.add_balance(uid, -amount, kind="admin_deduct_all", note="حذف جماعي من الإدارة")
            deducted += 1
            try:
                await _app.bot.send_message(uid, f"➖ تم خصم {fmt(amount)} ليرة من رصيدك من قبل الإدارة.")
            except Exception:
                pass
        context.user_data["awaiting"] = None
        await update.message.reply_text(
            f"✔ تم حذف {fmt(amount)} ليرة من {deducted} مستخدم.\n"
            f"⏭ تم تجاوز {skipped} مستخدم لأن رصيدهم أقل من المبلغ."
        )

    elif kind == "boost_add_amount":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        amount = int(text)
        field = context.user_data.get("boost_add_field")
        label = context.user_data.get("boost_add_label", "")
        db.add_boost(field, amount)
        context.user_data["awaiting"] = None
        context.user_data.pop("boost_add_field", None)
        context.user_data.pop("boost_add_label", None)
        s = db.get_settings()
        await update.message.reply_text(f"✔ تم زيادة {label} بمقدار {fmt(amount)}.\nإجمالي الإضافة الحالية: {fmt(s.get(field, 0))}")

    elif kind == "setref_amount":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        target = context.user_data.get("setref_target")
        count = int(text)
        db.set_referrals_count(target, count)
        try:
            await _app.bot.send_message(target, f"✏️ تم تحديث عدد إحالاتك من قبل الإدارة إلى {fmt(count)}.")
        except Exception:
            pass
        context.user_data["awaiting"] = None
        context.user_data.pop("setref_target", None)
        await update.message.reply_text(f"✔ تم تعديل عدد إحالات المستخدم {target} إلى {fmt(count)}.")

    elif kind == "comp_title":
        draft = {"title": text.strip()}
        context.user_data["comp_draft"] = draft
        context.user_data["awaiting"] = "admin:comp_desc"
        await send_step(
            update, context,
            f"العنوان: {draft['title']}\n\n📝 اكتب وصف/تفاصيل المسابقة (أو اكتب - لتجاوز):",
            key="comp_flow"
        )

    elif kind == "comp_desc":
        draft = context.user_data.get("comp_draft", {})
        draft["description"] = None if text.strip() in ("-", "تجاوز", "الغاء", "إلغاء") else text.strip()
        context.user_data["comp_draft"] = draft
        context.user_data["awaiting"] = "admin:comp_prize1"
        await send_step(update, context, "🥇 اكتب جائزة المركز الأول:", key="comp_flow")

    elif kind == "comp_prize1":
        draft = context.user_data.get("comp_draft", {})
        draft["prize_1"] = text.strip()
        context.user_data["comp_draft"] = draft
        context.user_data["awaiting"] = "admin:comp_prize2"
        await send_step(update, context, "🥈 اكتب جائزة المركز الثاني:", key="comp_flow")

    elif kind == "comp_prize2":
        draft = context.user_data.get("comp_draft", {})
        draft["prize_2"] = text.strip()
        context.user_data["comp_draft"] = draft
        context.user_data["awaiting"] = "admin:comp_prize3"
        await send_step(update, context, "🥉 اكتب جائزة المركز الثالث:", key="comp_flow")

    elif kind == "comp_prize3":
        draft = context.user_data.get("comp_draft", {})
        draft["prize_3"] = text.strip()
        db.create_competition(
            draft["title"], draft.get("description"),
            draft["prize_1"], draft["prize_2"], draft["prize_3"],
            update.effective_user.id
        )
        context.user_data["awaiting"] = None
        context.user_data.pop("comp_draft", None)
        clear_step(context, key="comp_flow")
        await update.message.reply_text(
            f"✅ تم إنشاء المسابقة:\n🏆 {draft['title']}\n"
            f"🥇 {draft['prize_1']}\n🥈 {draft['prize_2']}\n🥉 {draft['prize_3']}\n\n"
            f"بتظهر الآن للمستخدمين من قسم «🏆 المسابقات»."
        )

    elif kind == "stars_rate":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        context.user_data["stars_draft"] = {"rate": int(text)}
        context.user_data["awaiting"] = "admin:stars_min"
        await send_step(update, context, f"سعر الصرف: {text} ليرة/نجمة\n\n⭐ اكتب الحد الأدنى من النجوم لإلغاء العمولة:", key="stars_flow")

    elif kind == "stars_min":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("stars_draft", {})
        draft["min_nc"] = int(text)
        context.user_data["stars_draft"] = draft
        context.user_data["awaiting"] = "admin:stars_hours"
        await send_step(update, context, f"الحد الأدنى: {text} ⭐\n\n⏱ اكتب عدد الساعات التي تُلغى فيها العمولة لكل دفعة:", key="stars_flow")

    elif kind == "stars_hours":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("stars_draft", {})
        db.update_setting("stars_to_lira_rate", draft["rate"])
        db.update_setting("stars_min_no_commission", draft["min_nc"])
        db.update_setting("stars_no_commission_hours", int(text))
        context.user_data["awaiting"] = None
        context.user_data.pop("stars_draft", None)
        clear_step(context, key="stars_flow")
        await update.message.reply_text("✔ تم تحديث إعدادات النجوم.")

    elif kind == "ref_premium":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        context.user_data["ref_draft"] = {"premium": int(text)}
        context.user_data["awaiting"] = "admin:ref_regular"
        await send_step(update, context, f"Premium: {fmt(int(text))} ليرة\n\n🏃 اكتب مكافأة إحالة مستخدم عادي (بدون Premium):", key="ref_flow")

    elif kind == "ref_regular":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("ref_draft", {})
        draft["regular"] = int(text)
        context.user_data["ref_draft"] = draft
        context.user_data["awaiting"] = "admin:ref_mandatory"
        await send_step(update, context, f"عادي: {fmt(int(text))} ليرة\n\n👤 اكتب مكافأة الإحالة عبر الاشتراك الإجباري:", key="ref_flow")

    elif kind == "ref_mandatory":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("ref_draft", {})
        db.update_setting("ref_reward_premium", draft["premium"])
        db.update_setting("ref_reward_regular", draft["regular"])
        db.update_setting("ref_reward_mandatory", int(text))
        context.user_data["awaiting"] = None
        context.user_data.pop("ref_draft", None)
        clear_step(context, key="ref_flow")
        await update.message.reply_text("✔ تم تحديث إعدادات مكافآت الإحالة.")

    elif kind == "cheque_commission":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        context.user_data["cheque_settings_draft"] = {"commission": int(text)}
        context.user_data["awaiting"] = "admin:cheque_min"
        await send_step(update, context, f"العمولة: {text}%\n\n💳 اكتب الحد الأدنى لمبلغ الشيك الشخصي:", key="cheque_settings_flow")

    elif kind == "cheque_min":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("cheque_settings_draft", {})
        draft["min_amount"] = int(text)
        context.user_data["cheque_settings_draft"] = draft
        context.user_data["awaiting"] = "admin:cheque_personal_max"
        await send_step(update, context, f"الحد الأدنى (شخصي): {fmt(int(text))}\n\n💳 اكتب الحد الأقصى لمبلغ الشيك الشخصي:", key="cheque_settings_flow")

    elif kind == "cheque_personal_max":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("cheque_settings_draft", {})
        draft["personal_max"] = int(text)
        context.user_data["cheque_settings_draft"] = draft
        context.user_data["awaiting"] = "admin:cheque_multi_min"
        await send_step(update, context, f"الحد الأقصى (شخصي): {fmt(int(text))}\n\n👥 اكتب الحد الأدنى لمبلغ الشيك المتعدد (لكل مستخدم):", key="cheque_settings_flow")

    elif kind == "cheque_multi_min":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("cheque_settings_draft", {})
        draft["multi_min"] = int(text)
        context.user_data["cheque_settings_draft"] = draft
        context.user_data["awaiting"] = "admin:cheque_multi_max"
        await send_step(update, context, f"الحد الأدنى (متعدد): {fmt(int(text))}\n\n👥 اكتب الحد الأقصى لمبلغ الشيك المتعدد (لكل مستخدم):", key="cheque_settings_flow")

    elif kind == "cheque_multi_max":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("cheque_settings_draft", {})
        draft["multi_max"] = int(text)
        context.user_data["cheque_settings_draft"] = draft
        context.user_data["awaiting"] = "admin:cheque_passlen"
        await send_step(update, context, f"الحد الأقصى (متعدد): {fmt(int(text))}\n\n🔒 اكتب أقصى طول مسموح لكلمة مرور الشيك:", key="cheque_settings_flow")

    elif kind == "cheque_passlen":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("cheque_settings_draft", {})
        db.update_setting("cheque_commission_percent", draft["commission"])
        db.update_setting("cheque_min_amount", draft["min_amount"])
        db.update_setting("cheque_personal_max_amount", draft["personal_max"])
        db.update_setting("cheque_multi_min_amount", draft["multi_min"])
        db.update_setting("cheque_multi_max_amount", draft["multi_max"])
        db.update_setting("cheque_password_max_length", int(text))
        context.user_data["awaiting"] = None
        context.user_data.pop("cheque_settings_draft", None)
        clear_step(context, key="cheque_settings_flow")
        await update.message.reply_text("✔ تم تحديث إعدادات الشيكات.")

    elif kind == "welcome_gift":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        db.update_setting("welcome_gift_points", int(text))
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✔ تم تحديث هدية الترحيب إلى {fmt(int(text))} ليرة.")

    elif kind == "protection_watchdays":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        db.update_setting("sub_watch_days", int(text))
        context.user_data["awaiting"] = None
        clear_step(context, key="protection_flow")
        await update.message.reply_text(f"✔ تم تحديث مدة المراقبة إلى {text} يوم.")

    elif kind == "text":
        key = parts[2]
        db.set_template_content(key, text)
        context.user_data["awaiting"] = None
        if key == "maintenance_msg":
            await update.message.reply_text("✔ تم حفظ رسالة الصيانة. البوت لسا موقوف — رجّعه من «⏯️ حالة البوت» بلوحة الأدمن.")
        else:
            await update.message.reply_text("✔ تم حفظ النص الجديد.")

    elif kind == "maintenance_msg":
        db.set_template_content("maintenance_msg", text)
        context.user_data["awaiting"] = None
        await update.message.reply_text("✔ تم حفظ رسالة الصيانة. البوت موقوف حالياً عن غير الأدمن — رجّعه من «⏯️ حالة البوت» وقت ما بدك.")

    elif kind == "mandatory_channel":
        val = None if text in ("-", "الغاء", "إلغاء") else text
        db.update_setting("mandatory_sub_channel", val)
        context.user_data["awaiting"] = None
        await update.message.reply_text("✔ تم تحديث قناة الاشتراك الإلزامي." if val else "✔ تم إلغاء الاشتراك الإلزامي.")

    elif kind == "giftcode_code":
        code = text.strip().replace(" ", "_")
        if db.get_gift_code(code):
            await update.message.reply_text("❌ هذا الكود مستخدم من قبل، اختر كوداً آخر:")
            return
        draft = {"code": code}
        context.user_data["giftcode_draft"] = draft
        context.user_data["awaiting"] = "admin:giftcode_points"
        await send_step(update, context, f"الكود: {code}\n\n🎁 اكتب عدد النقاط التي يمنحها هذا الكود:", key="giftcode_flow")

    elif kind == "giftcode_points":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("giftcode_draft", {})
        draft["points"] = int(text)
        context.user_data["giftcode_draft"] = draft
        context.user_data["awaiting"] = "admin:giftcode_maxuses"
        await send_step(
            update, context,
            f"الكود: {draft['code']}\nالنقاط: {fmt(draft['points'])}\n\n"
            f"👥 كم عدد الأشخاص المسموح لهم استخدام هذا الكود؟ (0 = بلا حد):",
            key="giftcode_flow"
        )

    elif kind == "giftcode_maxuses":
        if not text.isdigit():
            await update.message.reply_text("❌ رقم غير صالح.")
            return
        draft = context.user_data.get("giftcode_draft", {})
        max_uses = int(text)
        db.create_gift_code(draft["code"], draft["points"], max_uses, update.effective_user.id)
        context.user_data["awaiting"] = None
        context.user_data.pop("giftcode_draft", None)
        clear_step(context, key="giftcode_flow")
        await update.message.reply_text(
            f"✅ تم إنشاء كود الهدية:\n`{draft['code']}`\n🎁 {fmt(draft['points'])} ليرة | الحد: {max_uses if max_uses else 'بلا حد'}\n\n"
            f"شارك الكود مع المستخدمين ليدخلوه من «📱 حسابي → 🎁 إدخال كود هدية»."
        )


# ==================== تسجيل الهاندلرز ====================

def register_handlers(telegram_app):
    global _app
    _app = telegram_app

    telegram_app.add_handler(CallbackQueryHandler(protection_settings_click, pattern="^adm_protection_settings$"))
    telegram_app.add_handler(CallbackQueryHandler(welcome_gift_click, pattern="^adm_welcome_gift$"))
    telegram_app.add_handler(CallbackQueryHandler(admin_home_callback, pattern="^adm_home$"))
    telegram_app.add_handler(CallbackQueryHandler(show_stats, pattern="^adm_stats$"))
    telegram_app.add_handler(CallbackQueryHandler(show_prices_menu, pattern="^adm_prices$"))
    telegram_app.add_handler(CallbackQueryHandler(price_edit_click, pattern="^adm_price_edit:"))
    telegram_app.add_handler(CallbackQueryHandler(commission_edit_click, pattern="^adm_commission_edit$"))

    telegram_app.add_handler(CallbackQueryHandler(show_reflinks_menu, pattern="^adm_reflinks$"))
    telegram_app.add_handler(CallbackQueryHandler(reflink_new_click, pattern="^adm_reflink_new$"))
    telegram_app.add_handler(CallbackQueryHandler(reflink_view_click, pattern="^adm_reflink_view:"))
    telegram_app.add_handler(CallbackQueryHandler(reflink_toggle_click, pattern="^adm_reflink_toggle:"))

    telegram_app.add_handler(CallbackQueryHandler(show_users_page, pattern="^adm_ubrowse:"))
    telegram_app.add_handler(CallbackQueryHandler(show_user_detail, pattern="^adm_usel:"))
    telegram_app.add_handler(CallbackQueryHandler(toggle_ban_click, pattern="^adm_toggleban:"))

    telegram_app.add_handler(CallbackQueryHandler(broadcast_click, pattern="^adm_broadcast$"))
    telegram_app.add_handler(CallbackQueryHandler(dm_click, pattern="^adm_dm$"))
    telegram_app.add_handler(CallbackQueryHandler(dm_user_direct_click, pattern="^adm_dm_user:"))
    telegram_app.add_handler(CallbackQueryHandler(pick_user_click, pattern="^adm_pick:"))
    telegram_app.add_handler(CallbackQueryHandler(pick_page_click, pattern="^adm_pickpage:"))

    telegram_app.add_handler(CallbackQueryHandler(gift_user_click, pattern="^adm_gift_user$"))
    telegram_app.add_handler(CallbackQueryHandler(gift_user_direct_click, pattern="^adm_gift_user_id:"))
    telegram_app.add_handler(CallbackQueryHandler(gift_all_click, pattern="^adm_gift_all$"))

    telegram_app.add_handler(CallbackQueryHandler(deduct_user_click, pattern="^adm_deduct_user$"))
    telegram_app.add_handler(CallbackQueryHandler(deduct_user_direct_click, pattern="^adm_deduct_user_id:"))
    telegram_app.add_handler(CallbackQueryHandler(deduct_all_click, pattern="^adm_deduct_all$"))

    telegram_app.add_handler(CallbackQueryHandler(gifts_menu_click, pattern="^adm_gifts_menu$"))

    telegram_app.add_handler(CallbackQueryHandler(comp_menu_click, pattern="^adm_comp_menu$"))
    telegram_app.add_handler(CallbackQueryHandler(comp_new_click, pattern="^adm_comp_new$"))
    telegram_app.add_handler(CallbackQueryHandler(setref_user_click, pattern="^adm_setref_user$"))
    telegram_app.add_handler(CallbackQueryHandler(setref_user_direct_click, pattern="^adm_setref_user_id:"))

    telegram_app.add_handler(CallbackQueryHandler(boost_menu_click, pattern="^adm_boost_menu$"))
    telegram_app.add_handler(CallbackQueryHandler(boost_add_click, pattern="^adm_boost_add:"))
    telegram_app.add_handler(CallbackQueryHandler(boost_view_click, pattern="^adm_boost_view$"))
    telegram_app.add_handler(CallbackQueryHandler(boost_reset_menu_click, pattern="^adm_boost_reset_menu$"))
    telegram_app.add_handler(CallbackQueryHandler(boost_reset_click, pattern="^adm_boost_reset:"))

    telegram_app.add_handler(CallbackQueryHandler(stars_settings_click, pattern="^adm_stars_settings$"))
    telegram_app.add_handler(CallbackQueryHandler(ref_settings_click, pattern="^adm_ref_settings$"))
    telegram_app.add_handler(CallbackQueryHandler(cheque_settings_click, pattern="^adm_cheque_settings$"))
    telegram_app.add_handler(CallbackQueryHandler(texts_menu_click, pattern="^adm_texts$"))
    telegram_app.add_handler(CallbackQueryHandler(text_edit_click, pattern="^adm_text_edit:"))
    telegram_app.add_handler(CallbackQueryHandler(text_edit_mandatory_click, pattern="^adm_text_edit_mandatory$"))

    telegram_app.add_handler(CallbackQueryHandler(toggle_bot_click, pattern="^adm_toggle_bot$"))
    telegram_app.add_handler(CallbackQueryHandler(gate_settings_click, pattern="^adm_gate_settings$"))
    telegram_app.add_handler(CallbackQueryHandler(gate_toggle_click, pattern="^adm_gate_toggle$"))

    telegram_app.add_handler(CallbackQueryHandler(publish_start_click, pattern="^adm_publish_start$"))

    telegram_app.add_handler(CallbackQueryHandler(show_giftcodes_menu, pattern="^adm_giftcodes$"))
    telegram_app.add_handler(CallbackQueryHandler(giftcode_new_click, pattern="^adm_giftcode_new$"))
    telegram_app.add_handler(CallbackQueryHandler(giftcode_toggle_click, pattern="^adm_giftcode_toggle:"))
