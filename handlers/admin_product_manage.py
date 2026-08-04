# admin_product_manage.py

import json
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database import SessionLocal
from models.product import Product
from states.product_states import AddAccounts, EditAccounts, EditBulkPricing
from handlers.products import _real_stock, _send_stock_channel_message

router = Router()
logger = logging.getLogger(__name__)

print("✅ admin_product_manage imported")

DELIVERY_TYPES = ["automatic", "manual", "hybrid"]
DELIVERY_LABELS = {
    "automatic": "⚡ Automatic",
    "manual": "🖐 Manual",
    "hybrid": "🔀 Hybrid",
}
DEFAULT_LOW_STOCK_THRESHOLD = 3


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _divider(char: str = "━", length: int = 30) -> str:
    return char * length


def _get_category_label(product) -> str:
    """Get formatted category label."""
    cats = {
        "premium": "Premium",
        "budget": "Budget",
        "vpn": "VPN",
        "email": "Email",
        "streaming": "Streaming",
        "gaming": "Gaming",
        "software": "Software",
        "education": "Education",
    }
    return cats.get((product.category or "").strip().lower(), "General")


def _build_stockctl_sync_text(product) -> str:
    """Build the stockctl sync notification text from scratch (no dependencies on products.py internals)."""
    from html import escape as _esc

    stock = _real_stock(product)

    status = "IN STOCK" if stock > 0 else "OUT OF STOCK"
    if 0 < stock <= 3:
        status = "LIMITED STOCK"

    lines = [
        "┌──(root㉿Rain)-[/inventory]",
        "└─# sudo stockctl sync",
        "Synchronizing inventory...",
        "[AUTH] Administrator Verified",
        "[SYNC] Stock Database Updated",
        "[INDEX] Repository Refreshed",
        "[LIVE] Marketplace Synchronized",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "STOCK UPDATED",
        f"Product     {product.name}",
        f"Category    {_get_category_label(product)}",
        f"Price       ${float(product.price):.2f}",
        f"Stock       {stock} Available",
        f"State       {status}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Inventory synchronized.",
        "root@Rain:~#",
    ]
    return "<pre>" + _esc("\n".join(lines)) + "</pre>"


async def _notify_stock_change(bot, pid: int):
    """
    Fetch product by ID, build a stockctl sync notification,
    and send it to STOCK_GROUP_ID.
    """
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
    finally:
        db.close()

    if not product:
        logger.warning("_notify_stock_change: Product %s not found", pid)
        return

    text = _build_stockctl_sync_text(product)
    await _send_stock_channel_message(bot, text)


def _parse_bulk_pricing(raw_text: str) -> dict | None:
    raw_text = raw_text.strip()

    if raw_text.lower() in ("skip", "none", "remove", "no", "n", ""):
        return None

    tiers = {}

    try:
        tiers = json.loads(raw_text)
        if isinstance(tiers, list):
            result = {}
            for t in tiers:
                key = str(t.get("min", 1))
                result[key] = {
                    "min": int(t.get("min", 1)),
                    "max": int(t["max"]) if t.get("max") else None,
                    "price": float(t.get("price", 0))
                }
            return result if result else None
        elif isinstance(tiers, dict):
            for k, v in tiers.items():
                if not isinstance(v, dict):
                    return None
                if "min" not in v or "price" not in v:
                    return None
            return tiers
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            return None
        range_part, price_part = line.split("=", 1)
        range_part = range_part.strip()
        price_part = price_part.strip()
        try:
            price = float(price_part)
        except ValueError:
            return None
        if price < 0:
            return None
        if "+" in range_part:
            min_qty = int(range_part.replace("+", "").strip())
            tiers[str(min_qty)] = {"min": min_qty, "max": None, "price": price}
        elif "-" in range_part:
            parts = range_part.split("-")
            if len(parts) != 2:
                return None
            min_qty = int(parts[0].strip())
            max_qty = int(parts[1].strip())
            if min_qty >= max_qty:
                return None
            tiers[str(min_qty)] = {"min": min_qty, "max": max_qty, "price": price}
        else:
            return None

    return tiers if tiers else None


def _format_bulk_pricing_display(bulk_pricing: str | None) -> str:
    if not bulk_pricing:
        return ""

    try:
        tiers = json.loads(bulk_pricing)
    except (json.JSONDecodeError, TypeError):
        return ""

    if not tiers:
        return ""

    lines = []
    sorted_tiers = sorted(tiers.values(), key=lambda x: x.get("min", 0))

    for tier in sorted_tiers:
        min_qty = tier.get("min", 1)
        max_qty = tier.get("max")
        price = tier.get("price", 0)
        if max_qty:
            lines.append(f"  🏷 {min_qty}-{max_qty} units → ${price:.2f}/each")
        else:
            lines.append(f"  🏷 {min_qty}+ units → ${price:.2f}/each")

    return "\n".join(lines)


def _format_bulk_pricing_plain(bulk_pricing: str | None) -> str:
    if not bulk_pricing:
        return "(not set)"

    try:
        tiers = json.loads(bulk_pricing)
    except (json.JSONDecodeError, TypeError):
        return "(invalid data)"

    lines = []
    sorted_tiers = sorted(tiers.values(), key=lambda x: x.get("min", 0))

    for tier in sorted_tiers:
        min_qty = tier.get("min", 1)
        max_qty = tier.get("max")
        price = tier.get("price", 0)
        if max_qty:
            lines.append(f"{min_qty}-{max_qty}={price:.2f}")
        else:
            lines.append(f"{min_qty}+={price:.2f}")

    return "\n".join(lines)


# ==================================================
# HELPER – refresh the manage panel
# ==================================================

async def _refresh_manage_panel(callback: CallbackQuery, pid: int):
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
    finally:
        db.close()

    if not product:
        await callback.answer("❌ Product not found.")
        return

    text, markup = _build_product_panel(product)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


def _build_product_panel(product: Product):
    delivery_type = (product.delivery_type or "automatic").lower()
    threshold = (
        product.low_stock_threshold
        if product.low_stock_threshold is not None
        else DEFAULT_LOW_STOCK_THRESHOLD
    )

    text = f"""
🆔 <b>ID:</b> {product.id}

📦 <b>Product:</b>
{product.icon or '📦'} {product.name}

💰 <b>Base Price:</b>
${float(product.price):.2f}

📊 <b>Stock:</b>
{product.stock}

⚠️ <b>Low Stock Alert At:</b>
{threshold}

🚚 <b>Delivery Type:</b>
{DELIVERY_LABELS.get(delivery_type, delivery_type)}

📦 <b>Preorder:</b>
{"🟢 Enabled" if product.preorder else "🔴 Disabled"}

🏷 <b>Category:</b>
{product.category}

<b>Status:</b>
{"🟢 Enabled" if product.is_active else "🔴 Disabled"}
"""

    text += f"\n{_divider('─')}\n"
    text += "📦 <b>Bulk Pricing:</b>\n"

    if product.bulk_pricing:
        display = _format_bulk_pricing_display(product.bulk_pricing)
        if display:
            text += display + "\n"
        else:
            text += "  ❌ <i>Not Available</i>\n"
    else:
        text += "  ❌ <i>Not Available</i>\n"
        text += "  └ All quantities at base price\n"

    text += f"""
{_divider('─')}

📝 <b>Description:</b>

{product.description or "No description"}
"""

    has_accounts = bool(product.file_content and product.file_content.strip())

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Add Accounts",
                    callback_data=f"add_accounts_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Edit Accounts" if has_accounts else "✏️ Edit Accounts (empty)",
                    callback_data=f"edit_accounts_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔴 Disable" if product.is_active else "🟢 Enable",
                    callback_data=f"toggle_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Edit Price",
                    callback_data=f"edit_price_{product.id}"
                ),
                InlineKeyboardButton(
                    text="📦 Edit Stock",
                    callback_data=f"edit_stock_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🚚 Delivery: {DELIVERY_LABELS.get(delivery_type, delivery_type)}",
                    callback_data=f"cycle_delivery_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Preorder: 🟢 ON" if product.preorder else "📦 Preorder: 🔴 OFF",
                    callback_data=f"toggle_preorder_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Edit Bulk Pricing" if product.bulk_pricing else "📦 Add Bulk Pricing",
                    callback_data=f"edit_bulk_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Edit Low Stock Alert",
                    callback_data=f"edit_threshold_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Description",
                    callback_data=f"desc_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Delete Product",
                    callback_data=f"delete_product_{product.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="admin_products"
                )
            ]
        ]
    )

    return text, markup


# ==================================================
# TEST
# ==================================================

@router.message(Command("testadmin"))
async def test_admin(message: Message):
    await message.answer("✅ Admin router working")


# ==================================================
# PRODUCT PANEL
# ==================================================

@router.callback_query(F.data.startswith("manage_"))
async def manage_product(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    product_id = int(callback.data.split("_")[1])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
    finally:
        db.close()

    if not product:
        await callback.answer("❌ Product not found.")
        return

    text, markup = _build_product_panel(product)

    if callback.message.text is not None:
        await callback.message.edit_text(text, reply_markup=markup)
    else:
        await callback.message.answer(text, reply_markup=markup)

    await callback.answer()


# ==================================================
# ADD ACCOUNTS
# ==================================================

@router.callback_query(F.data.startswith("add_accounts_"))
async def add_accounts(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])
    await state.update_data(product_id=pid)
    await state.set_state(AddAccounts.accounts)

    await callback.message.answer(
        "Send accounts.\n\n"
        "One account per line.\n\n"
        "Example:\n"
        "email1@gmail.com:pass1\n"
        "email2@gmail.com:pass2\n\n"
        "Stock is recalculated from the total number of lines after each sale."
    )
    await callback.answer()


@router.message(AddAccounts.accounts)
async def save_accounts(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if not message.text:
        return

    data = await state.get_data()
    pid = data["product_id"]
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await message.answer("❌ Product not found.")
            return

        old_accounts = product.file_content or ""
        new_accounts = (message.text or "").strip()

        if old_accounts:
            product.file_content = old_accounts + "\n" + new_accounts
        else:
            product.file_content = new_accounts

        product.stock = len(
            [line for line in product.file_content.splitlines() if line.strip()]
        )
        db.commit()
        db.refresh(product)
    finally:
        db.close()

    await state.clear()

    # 🆕 NOTIFY STOCK CHANNEL
    try:
        await _notify_stock_change(message.bot, pid)
    except Exception:
        logger.exception("Failed to send stock notification after adding accounts")

    await message.answer(
        "✅ Accounts added.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 Product Manager",
                        callback_data="admin_products"
                    )
                ]
            ]
        )
    )


# ==================================================
# EDIT ACCOUNTS
# ==================================================

@router.callback_query(F.data.startswith("edit_accounts_"))
async def edit_accounts(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
    finally:
        db.close()

    if not product:
        await callback.answer("❌ Product not found.")
        return

    accounts = product.file_content or ""
    line_count = len([l for l in accounts.splitlines() if l.strip()])

    preview = accounts[:500] + ("..." if len(accounts) > 500 else "")

    text = f"📋 **Accounts for** {product.name}\n\n"
    text += f"📊 Total accounts: **{line_count}**\n"
    text += f"📦 Current stock: **{product.stock}**\n\n"
    text += f"```\n{preview}\n```"

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Replace All (overwrite)",
                    callback_data=f"replace_accounts_{pid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Clear All Accounts",
                    callback_data=f"clear_accounts_{pid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Add More",
                    callback_data=f"add_accounts_{pid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back to Product",
                    callback_data=f"manage_{pid}"
                )
            ]
        ]
    )

    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("replace_accounts_"))
async def replace_accounts_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])
    await state.update_data(product_id=pid, replace_mode=True)
    await state.set_state(EditAccounts.replace_accounts)

    await callback.message.answer(
        "📝 Send the **new** account list.\n\n"
        "This will **replace** all existing accounts.\n"
        "One account per line.\n\n"
        "Example:\n"
        "email1@gmail.com:pass1\n"
        "email2@gmail.com:pass2"
    )
    await callback.answer()


@router.message(EditAccounts.replace_accounts)
async def replace_accounts_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if not message.text:
        return

    data = await state.get_data()
    pid = data["product_id"]
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await message.answer("❌ Product not found.")
            return

        product.file_content = (message.text or "").strip()
        product.stock = len(
            [line for line in product.file_content.splitlines() if line.strip()]
        )
        db.commit()
        db.refresh(product)
    finally:
        db.close()

    await state.clear()

    # 🆕 NOTIFY STOCK CHANNEL
    try:
        await _notify_stock_change(message.bot, pid)
    except Exception:
        logger.exception("Failed to send stock notification after replacing accounts")

    await message.answer(
        "✅ Accounts replaced successfully.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 Back to Product",
                        callback_data=f"manage_{pid}"
                    )
                ]
            ]
        )
    )


@router.callback_query(F.data.startswith("clear_accounts_"))
async def clear_accounts(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await callback.answer("❌ Product not found.")
            return

        product.file_content = None
        product.stock = 0
        db.commit()
        db.refresh(product)
    finally:
        db.close()

    # 🆕 NOTIFY STOCK CHANNEL
    try:
        await _notify_stock_change(callback.bot, pid)
    except Exception:
        logger.exception("Failed to send stock notification after clearing accounts")

    await callback.answer("🗑 Accounts cleared.")
    await _refresh_manage_panel(callback, pid)


# ==================================================
# ENABLE / DISABLE
# ==================================================

@router.callback_query(
    F.data.startswith("toggle_")
    & ~F.data.startswith("toggle_preorder_")
)
async def toggle_product(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[1])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if product:
            product.is_active = not product.is_active
            db.commit()
    finally:
        db.close()

    await _refresh_manage_panel(callback, pid)


# ==================================================
# DELIVERY TYPE
# ==================================================

@router.callback_query(F.data.startswith("cycle_delivery_"))
async def cycle_delivery_type(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await callback.answer("❌ Product not found.")
            return

        current = (product.delivery_type or "automatic").lower()
        try:
            next_index = (DELIVERY_TYPES.index(current) + 1) % len(DELIVERY_TYPES)
        except ValueError:
            next_index = 0
        product.delivery_type = DELIVERY_TYPES[next_index]
        db.commit()

        new_label = DELIVERY_LABELS[product.delivery_type]
    finally:
        db.close()

    await callback.answer(f"🚚 Delivery set to {new_label}")
    await _refresh_manage_panel(callback, pid)


# ==================================================
# PREORDER TOGGLE
# ==================================================

@router.callback_query(F.data.startswith("toggle_preorder_"))
async def toggle_preorder(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await callback.answer("❌ Product not found.")
            return

        product.preorder = not product.preorder
        db.commit()
        new_state = product.preorder
    finally:
        db.close()

    await callback.answer(
        "📦 Preorder enabled" if new_state else "📦 Preorder disabled"
    )
    await _refresh_manage_panel(callback, pid)


# ==================================================
# BULK PRICING — EDIT
# ==================================================

@router.callback_query(F.data.startswith("edit_bulk_"))
async def edit_bulk_pricing(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    pid = int(callback.data.split("_")[2])

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
    finally:
        db.close()

    if not product:
        await callback.answer("❌ Product not found.")
        return

    current = _format_bulk_pricing_plain(product.bulk_pricing)

    await state.update_data(edit_bulk_pid=pid)
    await state.set_state(EditBulkPricing.waiting_input)

    text = (
        "╔══════════════════════════════╗\n"
        "║  📦 EDIT BULK PRICING       ║\n"
        "╚══════════════════════════════╝\n\n"
        f"📦 <b>Product:</b> #{pid} — {product.name}\n\n"
        f"{_divider('─')}\n\n"
        f"<b>Current Bulk Pricing:</b>\n"
        f"<code>{current}</code>\n\n"
        f"{_divider('═')}\n\n"
        f"📝 <b>Send new tiers</b> (one per line):\n\n"
        f"<code>1-10=5.00</code>\n"
        f"  └ 1-10 units → $5.00 each\n\n"
        f"<code>11-50=4.00</code>\n"
        f"  └ 11-50 units → $4.00 each\n\n"
        f"<code>51+=3.00</code>\n"
        f"  └ 51+ units → $3.00 each\n\n"
        f"{_divider('─')}\n\n"
        f"💡 Send <b>skip</b> or <b>remove</b> to\n"
        f"delete bulk pricing (flat pricing)"
    )

    await callback.message.answer(text)
    await callback.answer()


@router.message(EditBulkPricing.waiting_input)
async def save_bulk_pricing(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        return

    data = await state.get_data()
    pid = data.get("edit_bulk_pid")
    raw = message.text.strip()

    if raw.lower() in ("skip", "none", "remove", "no", "n"):
        bulk_json = None
        confirm = (
            "✅ <b>Bulk Pricing Removed!</b>\n\n"
            "📦 Now using flat pricing.\n"
            "All quantities will be charged at the base price."
        )
        await state.clear()
        await message.answer(confirm, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Back to Product",
                        callback_data=f"manage_{pid}",
                        style="primary"
                    )
                ]
            ]
        ))
    else:
        bulk_data = _parse_bulk_pricing(raw)

        if bulk_data is None:
            await message.answer(
                "❌ <b>Invalid format!</b>\n\n"
                f"{_divider('─')}\n\n"
                "Please use:\n\n"
                "<code>1-10=5.00</code>\n"
                "<code>11-50=4.00</code>\n"
                "<code>51+=3.00</code>\n\n"
                "OR send <b>skip</b> to remove."
            )
            return

        bulk_json = json.dumps(bulk_data)
        display = _format_bulk_pricing_display(bulk_json)

        await state.clear()

        confirm = (
            "✅ <b>Bulk Pricing Updated!</b>\n\n"
            f"{_divider('─')}\n"
            f"{display}\n"
            f"{_divider('─')}\n\n"
            "📦 Buyers will automatically get\n"
            "the best price for their quantity!"
        )

        await message.answer(confirm, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Back to Product",
                        callback_data=f"manage_{pid}",
                        style="primary"
                    )
                ]
            ]
        ))

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if product:
            product.bulk_pricing = bulk_json
            db.commit()
    finally:
        db.close()


# ==================================================
# HELP COMMANDS
# ==================================================

@router.callback_query(F.data.startswith("edit_price_"))
async def edit_price(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    pid = callback.data.split("_")[2]
    await callback.message.answer(f"Send:\n/setprice {pid} 9.99")
    await callback.answer()


@router.callback_query(F.data.startswith("edit_stock_"))
async def edit_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    pid = callback.data.split("_")[2]
    await callback.message.answer(f"Send:\n/setstock {pid} 100")
    await callback.answer()


@router.callback_query(F.data.startswith("edit_threshold_"))
async def edit_threshold(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    pid = callback.data.split("_")[2]
    await callback.message.answer(
        f"Send:\n/setthreshold {pid} 3\n\n"
        "You'll get a Telegram alert whenever stock drops to or below this number."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("desc_"))
async def edit_desc(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    pid = callback.data.split("_")[1]
    await callback.message.answer(f"Send:\n/setdesc {pid} New Description")
    await callback.answer()


# ==================================================
# SET PRICE
# ==================================================

@router.message(Command("setprice"))
async def set_price(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Usage: /setprice <product_id> <price>\nExample: /setprice 5 9.99")
        return
    _, pid_raw, price_raw = parts
    if not pid_raw.isdigit():
        await message.answer("❌ Product ID must be a number.")
        return
    try:
        price = float(price_raw)
    except ValueError:
        await message.answer("❌ Price must be a number, e.g. 9.99")
        return
    if price < 0:
        await message.answer("❌ Price can't be negative.")
        return
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == int(pid_raw)).first()
        if not product:
            await message.answer("❌ Product not found.")
            return
        product.price = price
        db.commit()
        await message.answer(f"✅ Price updated to ${price:.2f}.")
    finally:
        db.close()


# ==================================================
# SET STOCK
# ==================================================

@router.message(Command("setstock"))
async def set_stock(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Usage: /setstock <product_id> <stock>\nExample: /setstock 5 100")
        return
    _, pid_raw, stock_raw = parts
    if not pid_raw.isdigit():
        await message.answer("❌ Product ID must be a number.")
        return
    pid = int(pid_raw)
    try:
        stock = int(stock_raw)
    except ValueError:
        await message.answer("❌ Stock must be a whole number.")
        return
    if stock < 0:
        await message.answer("❌ Stock can't be negative.")
        return
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            await message.answer("❌ Product not found.")
            return
        delivery_type = (product.delivery_type or "automatic").lower()
        if delivery_type == "automatic":
            await message.answer(
                "ℹ️ This product delivers automatically from the account list, "
                "so stock is derived from how many accounts are loaded "
                "(use ➕ Add Accounts to change it). Setting it manually here "
                "won't survive the next sale or account upload."
            )
        product.stock = stock
        db.commit()
        db.refresh(product)
    finally:
        db.close()

    # 🆕 NOTIFY STOCK CHANNEL
    try:
        await _notify_stock_change(message.bot, pid)
    except Exception:
        logger.exception("Failed to send stock notification after /setstock")

    await message.answer(f"✅ Stock updated to {stock}.")


# ==================================================
# SET LOW STOCK THRESHOLD
# ==================================================

@router.message(Command("setthreshold"))
async def set_threshold(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Usage: /setthreshold <product_id> <threshold>\nExample: /setthreshold 5 3")
        return
    _, pid_raw, threshold_raw = parts
    if not pid_raw.isdigit():
        await message.answer("❌ Product ID must be a number.")
        return
    try:
        threshold = int(threshold_raw)
    except ValueError:
        await message.answer("❌ Threshold must be a whole number.")
        return
    if threshold < 0:
        await message.answer("❌ Threshold can't be negative.")
        return
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == int(pid_raw)).first()
        if not product:
            await message.answer("❌ Product not found.")
            return
        product.low_stock_threshold = threshold
        db.commit()
        await message.answer(f"✅ Low stock alert threshold set to {threshold}.")
    finally:
        db.close()


# ==================================================
# SET DESCRIPTION
# ==================================================

@router.message(Command("setdesc"))
async def set_desc(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("❌ Usage: /setdesc <product_id> <description>")
        return
    _, pid_raw, desc = parts
    if not pid_raw.isdigit():
        await message.answer("❌ Product ID must be a number.")
        return
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == int(pid_raw)).first()
        if not product:
            await message.answer("❌ Product not found.")
            return
        product.description = desc
        db.commit()
        await message.answer("✅ Description updated.")
    finally:
        db.close()


# ==================================================
# DELETE PRODUCT
# ==================================================

@router.callback_query(F.data.startswith("delete_product_"))
async def delete_product(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    pid = int(callback.data.split("_")[2])
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == pid).first()
        if product:
            db.delete(product)
            db.commit()
    finally:
        db.close()
    await callback.message.edit_text(
        "✅ Product deleted.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 Product Manager",
                        callback_data="admin_products"
                    )
                ]
            ]
        )
    )
    await callback.answer()