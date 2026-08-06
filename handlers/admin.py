# handlers/admin.py — FIXED ADMIN PANEL WITH FULL DASHBOARD & BROADCAST
import asyncio

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from html import escape as html_escape

from sqlalchemy import func

from database import SessionLocal
from config import ADMIN_IDS

from models.user import User
from models.product import Product
from models.order import Order
from models.deposit import Deposit
from models.ticket import Ticket

from keyboards.admin_menu import get_admin_panel
from keyboards.menu import get_admin_main_menu

from states.broadcast import BroadcastState

router = Router()


def safe(text: str) -> str:
    return html_escape(str(text), quote=False)


# ╔══════════════════════════════════════════════════════════════╗
# ║  ADMIN PANEL DASHBOARD — TERMINAL STYLE                    ║
# ╚══════════════════════════════════════════════════════════════╝

def _build_admin_dashboard(db, admin_name: str, admin_id: int) -> str:
    """Build terminal-style admin control center."""
    users = db.query(User).count()
    products = db.query(Product).count()
    orders = db.query(Order).count()
    deposits = db.query(Deposit).count()
    tickets = db.query(Ticket).count()

    admin_user = db.query(User).filter(User.telegram_id == admin_id).first()
    admin_balance = float(getattr(admin_user, 'balance_display', float(admin_user.balance or 0))) if admin_user else 0
    admin_rewards = float(getattr(admin_user, 'referral_earnings_display', 0)) if admin_user else 0
    admin_deposited = float(getattr(admin_user, 'total_deposited', 0) or 0) if admin_user else 0

    total_revenue = db.query(func.coalesce(func.sum(Order.amount), 0)).scalar()

    return (
        "<code>┌──(root㉿rain)-[/control]</code>\n"
        "<code>└─# sudo rain-admin --dashboard</code>\n"
        "<code>[sudo] password:</code>\n"
        "<code>************</code>\n"
        "<code>[AUTH] Administrator Verified</code>\n"
        "<code>[CORE] Control Center Online</code>\n"
        "<code>[SYNC] Commerce Services Ready</code>\n"
        "<code>[MONITOR] Live System Active</code>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "<code>CONTROL CENTER</code>\n"
        f"<code>Admin       {admin_name}</code>\n"
        f"<code>UID         {admin_id}</code>\n"
        "<code>Privilege   Root</code>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "<code>LEDGER</code>\n"
        f"<code>Balance     ${admin_balance:.2f}</code>\n"
        f"<code>Rewards     ${admin_rewards:.2f}</code>\n"
        f"<code>Deposited   ${admin_deposited:.2f}</code>\n"
        f"<code>Revenue     ${float(total_revenue or 0):.2f}</code>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "<code>SYSTEM</code>\n"
        f"<code>Orders      {orders}</code>\n"
        "<code>Status      Operational</code>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "<code>Awaiting administrator command...</code>\n"
        "<code>root@rain:~#</code>\n\n"
        "👇 <b>Choose an action:</b>"
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  USER DASHBOARD — TERMINAL STYLE (for admin_back)          ║
# ╚══════════════════════════════════════════════════════════════╝

def _build_user_dashboard(user) -> str:
    """Build terminal-style user dashboard."""
    balance = float(getattr(user, 'balance_display', float(user.balance or 0)))
    total_orders = int(getattr(user, 'total_orders', 0) or 0)
    total_refs = int(getattr(user, 'total_referrals', 0) or 0)
    first_name = user.full_name.split()[0] if user.full_name else "user"

    return (
        "🛍 Rain Store\n"
        "Premium Digital Marketplace\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome back, {safe(first_name)}\n\n"
        "💎 Standard Plan\n\n"
        f"💰 Wallet: ${balance:.2f}\n"
        f"📦 Orders: {total_orders}\n"
        f"🎁 Rewards: {total_refs}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "<blockquote>"
        "✓ Verified Premium Products\n"
        "✓ Instant Delivery\n"
        "✓ Secure Payments\n"
        "✓ Dedicated Customer Support"
        "</blockquote>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "<blockquote>"
        "🛒 Shop \"Browse premium digital products\"\n"
        "💰 Deposit \"Top up your wallet instantly\"\n"
        "👤 Profile \"Manage your account & wallet\"\n"
        "📦 Orders \"View purchases & product keys\"\n"
        "📞 Support \"Get help from our support team\""
        "</blockquote>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 Stay Updated: @rainworlddd\n\n"
        "👇 Tap a button below to get started."
    )


# =====================================================
# /admin COMMAND
# =====================================================

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(
        "👑 Tap the crown button on your dashboard above to open the Admin Panel.\n\n"
        "(Send /start if you don't see your dashboard.)"
    )


# =====================================================
# ADMIN PANEL
# =====================================================

@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return

    db = SessionLocal()
    try:
        text = _build_admin_dashboard(db, callback.from_user.full_name, callback.from_user.id)

        await callback.message.edit_text(
            text,
            reply_markup=get_admin_panel(),
            parse_mode="HTML"
        )
    finally:
        db.close()

    await callback.answer()


# =====================================================
# USERS
# =====================================================

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return

    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.id.desc()).limit(50).all()
        keyboard = [
            [
                InlineKeyboardButton(
                    text=f"👤 {u.full_name} | {u.telegram_id}",
                    callback_data=f"view_user_{u.id}"
                )
            ]
            for u in users
        ]
        keyboard.append([
            InlineKeyboardButton(text="⬅ Back", callback_data="admin_panel")
        ])

        await callback.message.edit_text(
            f"👥 <b>Users ({len(users)})</b>\n\n<i>Showing latest 50</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="HTML"
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data.startswith("view_user_"))
async def view_user(callback: CallbackQuery):
    uid = int(callback.data.split("_")[2])
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            await callback.answer("User not found.", show_alert=True)
            return

        text = (
            "╔════════════════════════════╗\n"
            "║      👤 USER DETAILS       ║\n"
            "╚════════════════════════════╝\n\n"
            f"👤 <b>Name:</b> {safe(user.full_name)}\n"
            f"📝 <b>Username:</b> @{safe(user.username or 'None')}\n"
            f"🆔 <b>Telegram ID:</b> <code>{safe(str(user.telegram_id))}</code>\n\n"
            "────────────────────────────\n"
            f"💰 <b>Balance:</b> ${float(user.balance):.2f}\n"
            f"🎁 <b>Referral Earnings:</b> ${float(user.referral_earnings):.2f}\n"
            f"👥 <b>Referrals:</b> {user.total_referrals}\n\n"
            "────────────────────────────\n"
            f"🛒 <b>Orders:</b> {user.total_orders}\n"
            f"💸 <b>Total Spent:</b> ${float(user.total_spent):.2f}\n"
            f"📥 <b>Total Deposited:</b> ${float(user.total_deposited):.2f}\n\n"
            "────────────────────────────\n"
            f"🚫 <b>Banned:</b> {'<b>YES</b> 🚫' if user.is_banned else 'No 🟢'}"
        )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔙 Back to Users",
                            callback_data="admin_users"
                        )
                    ]
                ]
            )
        )
    finally:
        db.close()
    await callback.answer()


# =====================================================
# PRODUCTS
# =====================================================

@router.callback_query(F.data == "admin_products")
async def admin_products(callback: CallbackQuery):
    db = SessionLocal()
    try:
        products = db.query(Product).order_by(Product.id.desc()).all()

        keyboard = [
            [
                InlineKeyboardButton(
                    text="➕ New Product",
                    callback_data="create_product"
                )
            ]
        ]

        for product in products:
            status = "🟢" if product.is_active else "🔴"
            keyboard.append([
                InlineKeyboardButton(
                    text=f"#{product.id} {status} {product.icon} {product.name} ({product.stock})",
                    callback_data=f"manage_{product.id}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(text="⬅ Back", callback_data="admin_panel")
        ])

        await callback.message.edit_text(
            "📦 <b>Product Management</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    finally:
        db.close()
    await callback.answer()


# =====================================================
# STATISTICS
# =====================================================

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    db = SessionLocal()
    try:
        users = db.query(User).count()
        products = db.query(Product).count()
        orders = db.query(Order).count()
        deposits = db.query(Deposit).count()
        tickets = db.query(Ticket).count()
        revenue = db.query(func.coalesce(func.sum(Order.amount), 0)).scalar()

        text = (
            "╔════════════════════════════╗\n"
            "║      📊 STATISTICS         ║\n"
            "╚════════════════════════════╝\n\n"
            f"👥 <b>Users:</b> {users}\n"
            f"📦 <b>Products:</b> {products}\n"
            f"🛒 <b>Orders:</b> {orders}\n"
            f"💰 <b>Deposits:</b> {deposits}\n"
            f"🎫 <b>Tickets:</b> {tickets}\n\n"
            "════════════════════════════\n"
            f"💵 <b>Revenue:</b> ${float(revenue or 0):.2f}"
        )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅ Back", callback_data="admin_panel")]
                ]
            )
        )
    finally:
        db.close()
    await callback.answer()


# =====================================================
# BROADCAST — FULLY FIXED
# =====================================================

@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    """Start broadcast mode — sets FSM state and waits for message."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return

    # Set state to wait for broadcast message
    await state.set_state(BroadcastState.waiting_message)

    await callback.message.edit_text(
        "📢 <b>Broadcast Mode Activated</b>\n\n"
        "✏️ Send me the message you want to broadcast to <b>ALL users</b>.\n\n"
        "📝 <i>You can send text, photos, videos, documents — anything!</i>\n\n"
        "⚠️ <i>This will be forwarded to every user in the database.</i>\n\n"
        "👇 Click Cancel to abort.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Cancel Broadcast", callback_data="cancel_broadcast")]
            ]
        )
    )

    await callback.answer("📢 Broadcast mode activated — send your message now.")


@router.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Cancel the broadcast and return to admin panel."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return

    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()

    db = SessionLocal()
    try:
        text = _build_admin_dashboard(db, callback.from_user.full_name, callback.from_user.id)
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_panel(),
            parse_mode="HTML"
        )
    finally:
        db.close()

    await callback.answer("❌ Broadcast cancelled.")


# HANDLERS/ADMIN.PY — REPLACE THE send_broadcast FUNCTION

@router.message(BroadcastState.waiting_message)
async def send_broadcast(message: Message, state: FSMContext):
    """Handle the broadcast message and send to all users."""
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("⛔ Access denied. You are not an admin.")
        return

    # Send initial status message
    status_msg = await message.answer(
        "📢 <b>Broadcasting...</b>\n\n⏳ Fetching users from database...",
        parse_mode="HTML"
    )

    db = SessionLocal()
    try:
        users = db.query(User.telegram_id).all()
        user_ids = [x[0] for x in users]
    finally:
        db.close()

    if not user_ids:
        await status_msg.edit_text("⚠️ <b>No users found in database!</b>", parse_mode="HTML")
        await state.clear()
        return

    total = len(user_ids)
    sent = 0
    failed = 0
    blocked = 0
    deactivated = 0
    not_started = 0
    unknown_error = 0
    error_details = []

    # Update status message
    await status_msg.edit_text(
        f"📢 <b>Broadcasting...</b>\n\n"
        f"📊 Total users: {total}\n"
        f"✅ Sent: 0\n"
        f"❌ Failed: 0\n\n"
        f"⏳ Starting broadcast...",
        parse_mode="HTML"
    )

    # Send to each user with detailed error tracking
    for i, user_id in enumerate(user_ids, 1):
        try:
            await message.copy_to(chat_id=user_id)
            sent += 1

            # Small delay to avoid hitting rate limits
            if i % 5 == 0:
                await asyncio.sleep(0.1)

        except Exception as e:
            error_str = str(e).lower()

            if "bot was blocked by the user" in error_str or "blocked" in error_str:
                blocked += 1
            elif "user is deactivated" in error_str or "deactivated" in error_str:
                deactivated += 1
            elif "chat not found" in error_str:
                not_started += 1
            elif "bot can't initiate conversation" in error_str:
                not_started += 1
            else:
                failed += 1
                unknown_error += 1
                # Store first few errors for debugging
                if len(error_details) < 5:
                    error_details.append(f"UID {user_id}: {str(e)[:100]}")

        # Update progress every 5 users or on last user
        if i % 5 == 0 or i == total:
            try:
                progress_text = (
                    f"📢 <b>Broadcasting...</b>\n\n"
                    f"📊 Progress: {i}/{total}\n"
                    f"✅ Sent: {sent}\n"
                    f"❌ Failed: {failed}\n"
                    f"🚫 Blocked: {blocked}\n\n"
                    f"⏳ Still sending..."
                )
                await status_msg.edit_text(progress_text, parse_mode="HTML")
            except:
                pass

    # Calculate stats
    total_failed = blocked + deactivated + not_started + unknown_error
    success_rate = (sent / total * 100) if total > 0 else 0

    # Build detailed result message
    result_text = (
        f"📢 <b>✅ Broadcast Complete!</b>\n\n"
        f"<code>═══════════════════════</code>\n"
        f"📊 <b>Total Users in DB:</b> {total}\n"
        f"<code>═══════════════════════</code>\n"
        f"✅ <b>Successfully Sent:</b> {sent}\n"
        f"📈 <b>Success Rate:</b> {success_rate:.1f}%\n"
        f"<code>═══════════════════════</code>\n"
        f"❌ <b>Failed to Deliver:</b> {total_failed}\n"
        f"  ├ 🚫 <b>Blocked Bot:</b> {blocked}\n"
        f"  ├ 💀 <b>Deactivated Account:</b> {deactivated}\n"
        f"  ├ 🔇 <b>Never Started Bot:</b> {not_started}\n"
        f"  └ ⚠️ <b>Unknown Error:</b> {unknown_error}\n"
        f"<code>═══════════════════════</code>\n\n"
    )

    # Add explanation
    if blocked > 0 or deactivated > 0 or not_started > 0:
        result_text += (
            "<b>ℹ️ Why so many failed?</b>\n"
        )
        if not_started > 0:
            result_text += "• <b>Never started:</b> Users who haven't sent /start to the bot cannot receive messages (Telegram API restriction)\n"
        if blocked > 0:
            result_text += "• <b>Blocked:</b> Users who blocked the bot\n"
        if deactivated > 0:
            result_text += "• <b>Deactivated:</b> Users who deleted their Telegram account\n"
        result_text += "\n"

    # Add error details if any unknown errors
    if error_details:
        result_text += (
            f"<b>⚠️ Unknown Errors (first {len(error_details)}):</b>\n"
            f"<code>{chr(10).join(error_details)}</code>\n\n"
        )

    result_text += "<i>💡 Only users who have started the bot and not blocked it can receive broadcasts.</i>"

    await status_msg.edit_text(
        result_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Broadcast Again", callback_data="admin_broadcast"),
                    InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")
                ]
            ]
        )
    )

    # Clear the FSM state
    await state.clear()


# =====================================================
# BACK TO USER PANEL — Terminal style, NO popup
# =====================================================

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    """Back to user dashboard — terminal style, edits current message."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == callback.from_user.id).first()

        if not user:
            await callback.answer("User not found.", show_alert=True)
            return

        text = _build_user_dashboard(user)

        await callback.message.edit_text(
            text,
            reply_markup=get_admin_main_menu(),
            parse_mode="HTML"
        )
    finally:
        db.close()
    await callback.answer()
