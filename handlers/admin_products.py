# admin_add_product.py — updated version

import json

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext

from database import SessionLocal
from config import ADMIN_IDS
from models.product import Product
from states.product_states import AddProduct

# 🆕 Import the notification function
from handlers.products import notify_new_product

router = Router()

print("✅ admin_add_product imported")


# ╔══════════════════════════════════════════════════════════════╗
# ║              UTILITY FUNCTIONS                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _parse_bulk_pricing(raw_text: str) -> dict | None:
    """
    Parse bulk pricing input.
    Format per line: min_qty-max_qty=price
    Example:
    1-10=5.00
    11-50=4.00
    51+=3.00

    Or send "skip" / "none" to skip.
    """
    raw_text = raw_text.strip()

    if raw_text.lower() in ("skip", "none", "no", "n", ""):
        return None

    tiers = {}

    # Try JSON format first
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
            # Validate structure
            for k, v in tiers.items():
                if not isinstance(v, dict):
                    return None
                if "min" not in v or "price" not in v:
                    return None
            return tiers
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try line-by-line format: 1-10=5.00
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
            # 51+ format
            min_qty = int(range_part.replace("+", "").strip())
            key = str(min_qty)
            tiers[key] = {"min": min_qty, "max": None, "price": price}
        elif "-" in range_part:
            # 1-10 format
            parts = range_part.split("-")
            if len(parts) != 2:
                return None
            min_qty = int(parts[0].strip())
            max_qty = int(parts[1].strip())
            if min_qty >= max_qty:
                return None
            key = str(min_qty)
            tiers[key] = {"min": min_qty, "max": max_qty, "price": price}
        else:
            return None

    return tiers if tiers else None


def _format_bulk_pricing_display(bulk_pricing: str | None) -> str:
    """Format bulk pricing for display in product panel."""
    if not bulk_pricing:
        return ""

    try:
        tiers = json.loads(bulk_pricing)
    except (json.JSONDecodeError, TypeError):
        return ""

    if not tiers:
        return ""

    lines = ["\n📦 <b>Bulk Pricing:</b>"]

    sorted_tiers = sorted(tiers.values(), key=lambda x: x.get("min", 0))

    for tier in sorted_tiers:
        min_qty = tier.get("min", 1)
        max_qty = tier.get("max")
        price = tier.get("price", 0)

        if max_qty:
            lines.append(f"  🏷 {min_qty}-{max_qty} units → <b>${price:.2f}</b>/each")
        else:
            lines.append(f"  🏷 {min_qty}+ units → <b>${price:.2f}</b>/each")

    return "\n".join(lines)


def _format_bulk_pricing_plain(bulk_pricing: str | None) -> str:
    """Format bulk pricing as plain text lines for editing."""
    if not bulk_pricing:
        return ""

    try:
        tiers = json.loads(bulk_pricing)
    except (json.JSONDecodeError, TypeError):
        return ""

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


def _divider(char: str = "━", length: int = 30) -> str:
    return char * length


# ╔══════════════════════════════════════════════════════════════╗
# ║              START — CREATE PRODUCT FLOW                    ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "create_product")
async def add_product(callback: CallbackQuery, state: FSMContext):
    """Step 1/9: Ask for product name."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Admin only.", show_alert=True)
        return

    await state.clear()
    await state.set_state(AddProduct.name)

    await callback.message.answer(
        "╔══════════════════════════════╗\n"
        "║  📦 CREATE NEW PRODUCT      ║\n"
        "╚══════════════════════════════╝\n\n"
        "✏️ <b>Step 1/9: Product Name</b>\n\n"
        f"{_divider('─')}\n\n"
        "Send the product name.\n\n"
        "<i>Example: Gemini Advanced 1 Month</i>"
    )
    await callback.answer()


@router.message(AddProduct.name)
async def product_name(message: Message, state: FSMContext):
    """Step 2/9: Ask for icon."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    name = message.text.strip()

    if len(name) < 2:
        await message.answer("❌ <b>Name too short!</b>\nPlease send at least 2 characters.")
        return

    await state.update_data(name=name)
    await state.set_state(AddProduct.icon)

    await message.answer(
        f"✅ <b>Name:</b> {name}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 2/9: Icon</b>\n\n"
        f"Send an emoji for this product.\n\n"
        f"<i>Example: 🎬 or 📧 or 🔑 or 🤖</i>"
    )


@router.message(AddProduct.icon)
async def product_icon(message: Message, state: FSMContext):
    """Step 3/9: Ask for category."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    icon = message.text.strip()

    if len(icon) > 15:
        await message.answer("❌ <b>Icon too long!</b>\nUse 1-15 characters. An emoji is best: 🎬")
        return

    await state.update_data(icon=icon)
    await state.set_state(AddProduct.category)

    await message.answer(
        f"✅ <b>Icon:</b> {icon}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 3/9: Category</b>\n\n"
        f"Send a category name.\n\n"
        f"<b>Available categories:</b>\n"
        f"• premium\n• budget\n• vpn\n• email\n"
        f"• streaming\n• gaming\n• software\n• education\n\n"
        f"<i>Example: streaming</i>"
    )


@router.message(AddProduct.category)
async def product_category(message: Message, state: FSMContext):
    """Step 4/9: Ask for price."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    category = message.text.strip().lower()
    await state.update_data(category=category)
    await state.set_state(AddProduct.price)

    await message.answer(
        f"✅ <b>Category:</b> {category}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 4/9: Price</b>\n\n"
        f"Send the base price per unit (USD).\n\n"
        f"<i>Example: 9.99</i>\n\n"
        f"💡 <i>You'll be able to add bulk/tiered\n"
        f"pricing in a later step!</i>"
    )


@router.message(AddProduct.price)
async def product_price(message: Message, state: FSMContext):
    """Step 5/9: Ask for description."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    try:
        price = float(message.text.strip())
    except ValueError:
        await message.answer("❌ <b>Invalid price!</b>\nPlease send a number like 9.99")
        return

    if price < 0:
        await message.answer("❌ <b>Price can't be negative!</b>")
        return

    if price == 0:
        data = await state.get_data()
        if not data.get("_zero_confirmed"):
            await state.update_data(_zero_confirmed=True)
            await message.answer(
                "⚠️ <b>Price is $0.00 — FREE product!</b>\n\n"
                "Send <b>0</b> again to confirm."
            )
            return

    await state.update_data(price=price)
    await state.set_state(AddProduct.description)

    await message.answer(
        f"✅ <b>Price:</b> ${price:.2f}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 5/9: Description</b>\n\n"
        f"Send a description for this product.\n\n"
        f"<i>Example: Premium Gemini Advanced account\n"
        f"with 1-month validity. Includes all features.</i>\n\n"
        f"💡 <i>Send 'skip' to leave empty</i>"
    )


@router.message(AddProduct.description)
async def product_description(message: Message, state: FSMContext):
    """Step 6/9: Ask for delivery type."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    desc = message.text.strip()

    if desc.lower() == "skip":
        desc = ""

    await state.update_data(description=desc)
    await state.set_state(AddProduct.delivery_type)

    await message.answer(
        f"✅ <b>Description:</b> {desc if desc else '(empty)'}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 6/9: Delivery Type</b>\n\n"
        f"Choose delivery type:\n"
        f"• 🟢 <b>automatic</b> — Instant auto-delivery\n"
        f"• 🟡 <b>manual</b> — Manual by admin team\n"
        f"• 🔵 <b>hybrid</b> — Auto + manual\n\n"
        f"<i>Send: automatic, manual, or hybrid</i>"
    )


@router.message(AddProduct.delivery_type)
async def product_delivery(message: Message, state: FSMContext):
    """Step 7/9: Ask if preorder is allowed."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    dt = message.text.strip().lower()

    if dt not in ("automatic", "manual", "hybrid"):
        await message.answer(
            "❌ <b>Invalid!</b>\n\n"
            "Please send one of:\n"
            "• <b>automatic</b>\n"
            "• <b>manual</b>\n"
            "• <b>hybrid</b>"
        )
        return

    await state.update_data(delivery_type=dt)
    await state.set_state(AddProduct.preorder)

    delivery_labels = {
        "automatic": "🤖 Auto-Delivery",
        "manual": "👨‍💼 Manual Delivery",
        "hybrid": "🔀 Hybrid",
    }

    await message.answer(
        f"✅ <b>Delivery:</b> {delivery_labels.get(dt, dt)}\n\n"
        f"{_divider('─')}\n\n"
        f"✏️ <b>Step 7/9: Preorder</b>\n\n"
        f"Allow preorders when out of stock?\n\n"
        f"📦 <b>What are preorders?</b>\n"
        f"Users can buy even when stock is 0.\n"
        f"They'll receive the product when restocked.\n\n"
        f"Send: <b>yes</b> or <b>no</b>"
    )


@router.message(AddProduct.preorder)
async def product_preorder(message: Message, state: FSMContext):
    """Step 8/9: Ask for bulk pricing (optional)."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    raw = message.text.strip().lower()
    preorder = raw in ("yes", "y", "true", "1", "enable", "on")

    await state.update_data(preorder=preorder)
    await state.set_state(AddProduct.bulk_pricing)

    text = (
        f"✅ <b>Preorder:</b> {'🟢 Yes' if preorder else '🔴 No'}\n\n"
        f"{_divider('═')}\n\n"
        f"✏️ <b>Step 8/9: Bulk Pricing</b> <i>(Optional)</i>\n\n"
        f"{_divider('─')}\n\n"
        f"📦 <b>Want to add tiered/bulk pricing?</b>\n\n"
        f"Buyers automatically get discounts\n"
        f"when they purchase more units!\n\n"
        f"{_divider('─')}\n\n"
        f"📝 <b>How to format (one per line):</b>\n\n"
        f"<code>1-10=5.00</code>\n"
        f"  └ 1-10 units → $5.00 each\n\n"
        f"<code>11-50=4.00</code>\n"
        f"  └ 11-50 units → $4.00 each\n\n"
        f"<code>51+=3.00</code>\n"
        f"  └ 51+ units → $3.00 each\n\n"
        f"{_divider('─')}\n\n"
        f"📤 <b>Send your tiers now</b>\n"
        f"OR send <b>skip</b> for flat pricing only\n\n"
        f"<i>Example message:</i>\n"
        f"<code>1-10=5.00\n11-50=4.00\n51+=3.00</code>"
    )

    await message.answer(text)


@router.message(AddProduct.bulk_pricing)
async def product_bulk_pricing(message: Message, state: FSMContext):
    """Step 9/9: Ask for accounts."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    raw = message.text.strip()

    # Check if skipped
    if raw.lower() in ("skip", "none", "no", "n", ""):
        await state.update_data(bulk_pricing=None)
        await state.set_state(AddProduct.accounts)

        await message.answer(
            f"✅ <b>Bulk Pricing:</b> Skipped\n"
            f"   └ Using flat pricing: base price applies to all quantities\n\n"
            f"{_divider('═')}\n\n"
            f"✏️ <b>Step 9/9: Accounts</b>\n\n"
            f"Send the accounts for this product.\n\n"
            f"<b>Format:</b> One account per line\n"
            f"<code>email1@gmail.com:password1</code>\n"
            f"<code>email2@gmail.com:password2</code>\n\n"
            f"📊 Stock will be set automatically from\n"
            f"the number of accounts you provide.\n\n"
            f"💡 <i>Send 'skip' if no accounts yet</i>"
        )
        return

    # Parse bulk pricing
    bulk_data = _parse_bulk_pricing(raw)

    if bulk_data is None:
        await message.answer(
            "❌ <b>Invalid format!</b>\n\n"
            f"{_divider('─')}\n\n"
            "Please use the correct format:\n\n"
            "<code>1-10=5.00</code>\n"
            "<code>11-50=4.00</code>\n"
            "<code>51+=3.00</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 Rules:\n"
            "• One tier per line\n"
            "• Format: MIN-MAX=PRICE or MIN+=PRICE\n"
            "• Prices must be numbers\n"
            "• Ranges cannot overlap\n\n"
            "OR send <b>skip</b> for flat pricing."
        )
        return

    # Validate tiers make sense
    sorted_tiers = sorted(bulk_data.values(), key=lambda x: x.get("min", 0))
    for i in range(len(sorted_tiers) - 1):
        current = sorted_tiers[i]
        next_tier = sorted_tiers[i + 1]
        if current.get("max") and current["max"] >= next_tier.get("min", 0):
            await message.answer(
                "❌ <b>Overlapping tiers!</b>\n\n"
                f"Tier {current.get('min')}-{current.get('max')} overlaps with "
                f"tier starting at {next_tier.get('min')}.\n\n"
                "Please fix and send again."
            )
            return

    # Format confirmation
    confirm_lines = ["✅ <b>Bulk Pricing Set:</b>\n"]
    for tier in sorted_tiers:
        min_q = tier.get("min", 1)
        max_q = tier.get("max")
        price = tier.get("price", 0)
        if max_q:
            confirm_lines.append(f"  🏷 {min_q}-{max_q} units → <b>${price:.2f}</b>/each")
        else:
            confirm_lines.append(f"  🏷 {min_q}+ units → <b>${price:.2f}</b>/each")

    bulk_json = json.dumps(bulk_data)
    await state.update_data(bulk_pricing=bulk_json)
    await state.set_state(AddProduct.accounts)

    confirm_lines.append(f"\n{_divider('═')}")
    confirm_lines.append(f"\n✏️ <b>Step 9/9: Accounts</b>\n")
    confirm_lines.append("Send the accounts for this product.\n")
    confirm_lines.append("<b>Format:</b> One account per line\n")
    confirm_lines.append("<code>email1@gmail.com:password1</code>\n")
    confirm_lines.append("<code>email2@gmail.com:password2</code>\n")
    confirm_lines.append("\n💡 <i>Send 'skip' if no accounts yet</i>")

    await message.answer("\n".join(confirm_lines))


@router.message(AddProduct.accounts)
async def save_product(message: Message, state: FSMContext):
    """Save the product to database."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text:
        return

    data = await state.get_data()
    raw_text = message.text.strip()

    # Handle accounts
    if raw_text.lower() in ("skip", "none", "no", ""):
        accounts = []
        file_content = ""
    else:
        accounts = [x.strip() for x in raw_text.splitlines() if x.strip()]
        file_content = "\n".join(accounts)

    # Save to database
    db = SessionLocal()
    try:
        product = Product(
            name=data["name"],
            icon=data.get("icon", "📦"),
            category=data.get("category", "general"),
            description=data.get("description", ""),
            price=data["price"],
            stock=len(accounts),
            file_content=file_content if file_content else None,
            is_active=True,
            delivery_type=data.get("delivery_type", "automatic"),
            preorder=data.get("preorder", False),
            bulk_pricing=data.get("bulk_pricing", None),
            low_stock_threshold=3,
        )

        db.add(product)
        db.commit()
        db.refresh(product)
        pid = product.id
    finally:
        db.close()

    await state.clear()

    # 🆕 NOTIFY STOCK CHANNEL — New product launched
    try:
        if hasattr(message, "bot"):
            await notify_new_product(message.bot, product)
        elif hasattr(message, "_bot"):
            await notify_new_product(message._bot(), product)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to send stock notification for new product %s", pid)

    # Build success message
    text_parts = [
        "╔══════════════════════════════╗",
        "║  ✅ PRODUCT CREATED ✨       ║",
        "╚══════════════════════════════╝",
        "",
        f"🆔 <b>ID:</b> {pid}",
        f"📦 <b>Name:</b> {product.icon} {product.name}",
        f"🏷 <b>Category:</b> {product.category}",
        f"💰 <b>Base Price:</b> ${float(product.price):.2f}",
        f"📊 <b>Stock:</b> {product.stock}",
        f"🚚 <b>Delivery:</b> {product.delivery_type}",
        f"📦 <b>Preorder:</b> {'🟢 Yes' if product.preorder else '🔴 No'}",
    ]

    # Bulk pricing section
    text_parts.append(f"\n{_divider('─')}")

    if product.bulk_pricing:
        text_parts.append(_format_bulk_pricing_display(product.bulk_pricing))
    else:
        text_parts.append("\n📦 <b>Bulk Pricing:</b> ❌ Not set")
        text_parts.append("   └ All quantities at base price")

    text_parts.append(f"\n{_divider('─')}")

    if product.description:
        text_parts.append(f"\n📝 <b>Description:</b> {product.description[:300]}")
        if len(product.description) > 300:
            text_parts.append("   ...(truncated)")

    if accounts:
        text_parts.append(f"\n🔑 <b>Accounts loaded:</b> {len(accounts)}")

    text_parts.append(f"\n{_divider('═')}")
    text_parts.append("\n✅ <b>Product is now live!</b>")

    text = "\n".join(text_parts)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Product Manager",
                    callback_data="admin_products",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Create Another",
                    callback_data="create_product",
                    style="success"
                ),
                InlineKeyboardButton(
                    text="📋 Manage This Product",
                    callback_data=f"manage_{pid}",
                    style="primary"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=keyboard)