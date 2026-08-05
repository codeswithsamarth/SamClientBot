# handlers/products.py

import asyncio
import json
import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from html import escape as _esc
from datetime import datetime

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.exc import SQLAlchemyError

import config
from config import ADMIN_IDS
from utils.ui import show, update_card
from database import SessionLocal, transaction, retry_on_write_conflict
from models.product import Product
from models.user import User
from models.order import Order

logger = logging.getLogger(__name__)

router = Router()

# ╔══════════════════════════════════════════════════════════════╗
# ║                     CONFIGURATION                           ║
# ╚══════════════════════════════════════════════════════════════╝

PREORDER_MAX_QTY = 10
DEFAULT_LOW_STOCK_THRESHOLD = 3
MAX_SEARCH_RESULTS = 15

try:
    REFERRAL_COMMISSION_RATE = Decimal(
        str(getattr(config, "REFERRAL_COMMISSION_RATE", "0.05"))
    )
except (InvalidOperation, ValueError):
    REFERRAL_COMMISSION_RATE = Decimal("0.05")

REFERRAL_CREDIT_TO_BALANCE = getattr(config, "REFERRAL_CREDIT_TO_BALANCE", True)

MONEY_QUANT = Decimal("0.00000001")

# ---- Stock notification channel settings (config.py) -----------------
STOCK_GROUP_ID = getattr(config, "STOCK_GROUP_ID", None)
STOCK_NOTIFICATIONS = getattr(config, "STOCK_NOTIFICATIONS", True)
GROUP_ID = getattr(config, "GROUP_ID", None)
GROUP_NOTIFICATIONS = getattr(config, "GROUP_NOTIFICATIONS", False)

# ╔══════════════════════════════════════════════════════════════╗
# ║                   UI HELPERS                                ║
# ╚══════════════════════════════════════════════════════════════╝

def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _divider(char: str = "━", length: int = 30) -> str:
    return char * length


def _border_box(title: str, emoji: str = "📦") -> str:
    return (
        f"╔{_divider('═', 28)}╗\n"
        f"║  {emoji} {title:<23}║\n"
        f"╚{_divider('═', 28)}╝"
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║            CATEGORY -> BUTTON STYLE MAPPING                 ║
# ╚══════════════════════════════════════════════════════════════╝

CATEGORY_CONFIG = {
    "premium": {"icon": "👑", "label": "Premium", "style": "success", "color": "🟢"},
    "budget": {"icon": "💸", "label": "Budget", "style": "danger", "color": "🔴"},
    "vpn": {"icon": "🔒", "label": "VPN", "style": "primary", "color": "🔵"},
    "email": {"icon": "📧", "label": "Email", "style": "success", "color": "🟢"},
    "streaming": {"icon": "🎬", "label": "Streaming", "style": "primary", "color": "🔵"},
    "gaming": {"icon": "🎮", "label": "Gaming", "style": "success", "color": "🟢"},
    "software": {"icon": "💻", "label": "Software", "style": "primary", "color": "🔵"},
    "education": {"icon": "📚", "label": "Education", "style": "success", "color": "🟢"},
    "out of stock": {"icon": "🚫", "label": "Out of Stock", "style": "danger", "color": "⚫"},
}

DEFAULT_CATEGORY_CONFIG = {"icon": "📦", "label": "General", "style": "primary", "color": "🔵"}


def _get_category_config(category: str | None) -> dict:
    if not category:
        return DEFAULT_CATEGORY_CONFIG
    return CATEGORY_CONFIG.get(category.strip().lower(), DEFAULT_CATEGORY_CONFIG)


def _category_style(category: str | None) -> str:
    return _get_category_config(category)["style"]


# ╔══════════════════════════════════════════════════════════════╗
# ║              STOCK DISPLAY HELPERS                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _stock_indicator(stock: int) -> str:
    if stock <= 0:
        return "🔴 <b>Out of Stock</b>"
    elif stock <= 3:
        return f"🟡 <b>Low Stock</b> ({stock} left)"
    elif stock <= 10:
        return f"🟢 <b>In Stock</b> ({stock} available)"
    else:
        return f"🟢 <b>Well Stocked</b> ({stock} available)"


# ╔══════════════════════════════════════════════════════════════╗
# ║              BULK PRICING HELPERS                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_bulk_price(bulk_pricing, quantity: int):
    """
    Get the applicable bulk price for a given quantity.
    Returns None if no bulk pricing applies or no matching tier found.
    """
    if not bulk_pricing:
        return None

    try:
        tiers = json.loads(bulk_pricing) if isinstance(bulk_pricing, str) else bulk_pricing
    except (json.JSONDecodeError, TypeError):
        return None

    if not tiers:
        return None

    best_price = None
    best_min = 0

    for tier in tiers.values():
        min_qty = int(tier.get("min", 1))
        max_qty = tier.get("max")
        if max_qty is not None:
            max_qty = int(max_qty)
        price = float(tier.get("price", 0))

        if quantity >= min_qty:
            if max_qty is None or quantity <= max_qty:
                if min_qty > best_min:
                    best_price = price
                    best_min = min_qty

    return best_price


def _format_bulk_pricing_text(bulk_pricing) -> str:
    """Format bulk pricing tiers for display to users."""
    if not bulk_pricing:
        return "  ❌ <i>Not Available</i>"

    try:
        tiers = json.loads(bulk_pricing) if isinstance(bulk_pricing, str) else bulk_pricing
    except (json.JSONDecodeError, TypeError):
        return "  ❌ <i>Not Available</i>"

    if not tiers:
        return "  ❌ <i>Not Available</i>"

    lines = []
    sorted_tiers = sorted(tiers.values(), key=lambda x: int(x.get("min", 1)))

    for tier in sorted_tiers:
        min_q = tier.get("min", 1)
        max_q = tier.get("max")
        price = tier.get("price", 0)

        if max_q is not None:
            lines.append(f"  🏷 {min_q}-{max_q} units → <b>${float(price):.2f}</b>/each")
        else:
            lines.append(f"  🏷 {min_q}+ units → <b>${float(price):.2f}</b>/each")

    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════════╗
# ║                   FSM STATES                                ║
# ╚══════════════════════════════════════════════════════════════╝

class PurchaseStates(StatesGroup):
    waiting_qty = State()


class SearchStates(StatesGroup):
    waiting_query = State()


# ╔══════════════════════════════════════════════════════════════╗
# ║         IN-MEMORY STORES                                    ║
# ╚══════════════════════════════════════════════════════════════╝

_notify_subscribers: dict[int, list[int]] = {}
_favorites: dict[int, list[int]] = {}

_notify_lock = asyncio.Lock()
_favorites_lock = asyncio.Lock()


async def _add_notify_subscriber(product_id: int, user_id: int) -> bool:
    async with _notify_lock:
        if product_id not in _notify_subscribers:
            _notify_subscribers[product_id] = []
        if user_id not in _notify_subscribers[product_id]:
            _notify_subscribers[product_id].append(user_id)
            return True
        return False


async def _remove_notify_subscriber(product_id: int, user_id: int) -> bool:
    async with _notify_lock:
        if product_id in _notify_subscribers and user_id in _notify_subscribers[product_id]:
            _notify_subscribers[product_id].remove(user_id)
            return True
        return False


async def _toggle_favorite(user_id: int, product_id: int) -> bool:
    """Returns True if added, False if removed."""
    async with _favorites_lock:
        if user_id not in _favorites:
            _favorites[user_id] = []
        if product_id in _favorites[user_id]:
            _favorites[user_id].remove(product_id)
            return False
        else:
            _favorites[user_id].append(product_id)
            return True


async def _is_favorite(user_id: int, product_id: int) -> bool:
    async with _favorites_lock:
        return user_id in _favorites and product_id in _favorites[user_id]


async def _get_favorites(user_id: int) -> list[int]:
    async with _favorites_lock:
        return _favorites.get(user_id, [])[:]


async def _search_products(query: str) -> list:
    db = SessionLocal()
    try:
        search_term = f"%{query}%"
        return (
            db.query(Product)
            .filter(
                Product.is_active == True,
                (
                    Product.name.ilike(search_term) |
                    Product.category.ilike(search_term) |
                    Product.description.ilike(search_term)
                )
            )
            .order_by(Product.id.asc())
            .limit(MAX_SEARCH_RESULTS)
            .all()
        )
    finally:
        db.close()


# ╔══════════════════════════════════════════════════════════════╗
# ║              PER-USER PURCHASE LOCK                         ║
# ╚══════════════════════════════════════════════════════════════╝

_purchase_locks: dict[int, asyncio.Lock] = {}
_purchase_locks_guard = asyncio.Lock()


async def _get_purchase_lock(telegram_id: int) -> asyncio.Lock:
    async with _purchase_locks_guard:
        lock = _purchase_locks.get(telegram_id)
        if lock is None:
            lock = asyncio.Lock()
            _purchase_locks[telegram_id] = lock
        return lock


# ╔══════════════════════════════════════════════════════════════╗
# ║              CACHED PRODUCTS FETCH (30s TTL)               ║
# ╚══════════════════════════════════════════════════════════════╝

_products_cache: dict = {"data": None, "timestamp": 0}
_products_cache_lock = asyncio.Lock()


async def _fetch_active_products():
    """Cached product list — refreshes every 30 seconds. Excludes freebies (price=0)."""
    async with _products_cache_lock:
        now = asyncio.get_event_loop().time()
        if _products_cache["data"] is not None and (now - _products_cache["timestamp"]) < 30:
            return _products_cache["data"]

    def _query():
        db = SessionLocal()
        try:
            return (
                db.query(Product)
                .filter(Product.is_active == True, Product.price > 0)
                .order_by(Product.id.asc())
                .all()
            )
        finally:
            db.close()

    products = await asyncio.to_thread(_query)

    async with _products_cache_lock:
        _products_cache["data"] = products
        _products_cache["timestamp"] = asyncio.get_event_loop().time()

    return products


def _fetch_product(product_id: int):
    db = SessionLocal()
    try:
        return db.query(Product).filter(Product.id == product_id).first()
    finally:
        db.close()


def _fetch_products_by_ids(product_ids: list[int]) -> list:
    db = SessionLocal()
    try:
        return (
            db.query(Product)
            .filter(Product.id.in_(product_ids), Product.is_active == True)
            .all()
        )
    finally:
        db.close()


def _fetch_free_products() -> list:
    """Fetch all active products with price = 0 (freebies)."""
    db = SessionLocal()
    try:
        return (
            db.query(Product)
            .filter(Product.is_active == True, Product.price == 0)
            .order_by(Product.id.asc())
            .all()
        )
    finally:
        db.close()


def _accounts(product) -> list[str]:
    if not product.file_content:
        return []
    return [a.strip() for a in product.file_content.splitlines() if a.strip()]


def _real_stock(product) -> int:
    """Fast stock check — avoids _accounts() for manual delivery products."""
    delivery_type = (product.delivery_type or "automatic").lower()
    if delivery_type == "manual":
        return product.stock or 0
    accounts_available = len(_accounts(product)) if product.file_content else 0
    if delivery_type == "automatic":
        return accounts_available
    return max(accounts_available, product.stock or 0)


def _get_max_qty(product) -> int:
    cap = _real_stock(product)
    if cap <= 0 and product.preorder:
        return PREORDER_MAX_QTY
    return max(cap, 0)


# ╔══════════════════════════════════════════════════════════════╗
# ║      STOCK CHANGE TRACKING — RESTOCK / NEW PRODUCT /        ║
# ║      LIMITED STOCK BROADCASTS (→ STOCK_GROUP_ID, user-facing)║
# ╚══════════════════════════════════════════════════════════════╝

_stock_state: dict[int, dict] = {}
_stock_state_lock = asyncio.Lock()
_stock_scan_seeded = False


def _stockctl_block(
    action: str,
    steps: list[str],
    product,
    status_label: str,
    closing: str,
    extra_fields: list[tuple[str, str]] | None = None,
) -> str:
    """Renders a terminal/CLI-styled block (stockctl-style) as a Telegram <pre> block."""
    cat_config = _get_category_config(product.category)

    fields = [
        ("Name", product.name),
        ("Category", cat_config["label"]),
        ("Price", f"${_money(product.price):.2f}"),
    ]
    if extra_fields:
        fields.extend(extra_fields)
    fields.append(("Status", status_label))

    lines = [
        "┌──(root㉿Rain)-[/inventory]",
        f"└─# sudo stockctl {action}",
        "[sudo] password:",
        "************",
    ]
    lines.extend(steps)
    lines.append(_divider("━", 22))
    lines.append("PRODUCT")
    for label, value in fields:
        lines.append(f"{label:<12}{value}")
    lines.append(_divider("━", 22))
    lines.append(closing)
    lines.append("root@Rain:~#")

    return "<pre>" + _esc("\n".join(lines)) + "</pre>"


def _stock_event_text(kind: str, product, **kwargs) -> str:
    if kind == "new_product":
        stock = kwargs["stock"]
        status = "IN STOCK" if stock > 0 else "OUT OF STOCK"
        return _stockctl_block(
            action="add",
            steps=["[LOAD] Product Loaded", "[SYNC] Stock Database Updated", "[READY] Marketplace Refreshed"],
            product=product,
            status_label=status,
            closing="Stock committed.",
            extra_fields=[("Stock", str(stock))],
        )

    if kind == "restock":
        added = kwargs["added"]
        stock = kwargs["stock"]
        status = "IN STOCK" if stock > 0 else "OUT OF STOCK"
        return _stockctl_block(
            action="restock",
            steps=["[RESTOCK] Inventory Replenished", "[SYNC] Stock Database Updated", "[READY] Marketplace Refreshed"],
            product=product,
            status_label=status,
            closing="Stock committed.",
            extra_fields=[("Added", f"+{added}"), ("Stock", str(stock))],
        )

    if kind == "limited_stock":
        stock = kwargs["stock"]
        status = "LIMITED STOCK" if stock > 0 else "OUT OF STOCK"
        return _stockctl_block(
            action="update",
            steps=["[LOAD] Product Loaded", "[SYNC] Stock Database Updated", "[READY] Marketplace Refreshed"],
            product=product,
            status_label=status,
            closing="Stock committed.",
            extra_fields=[("Stock", str(stock))],
        )

    return ""


async def _send_stock_channel_message(bot, text: str):
    if not (STOCK_NOTIFICATIONS and STOCK_GROUP_ID and text):
        return
    try:
        await bot.send_message(STOCK_GROUP_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send stock channel notification")


async def _scan_stock_changes(bot, products: list):
    global _stock_scan_seeded

    if not products:
        return

    events = []

    async with _stock_state_lock:
        first_run = not _stock_scan_seeded

        for p in products:
            current = _real_stock(p)
            threshold = p.low_stock_threshold if p.low_stock_threshold is not None else DEFAULT_LOW_STOCK_THRESHOLD
            state = _stock_state.get(p.id)

            if state is None:
                _stock_state[p.id] = {
                    "last": current,
                    "low_stock_notified": current <= threshold,
                }
                if not first_run:
                    events.append(("new_product", p, {"stock": current}))
                continue

            last = state["last"]

            if current > last:
                added = current - last
                state["last"] = current
                state["low_stock_notified"] = current <= threshold
                events.append(("restock", p, {"added": added, "stock": current}))

            elif current < last:
                state["last"] = current
                if current <= threshold and not state["low_stock_notified"]:
                    state["low_stock_notified"] = True
                    events.append(("limited_stock", p, {"stock": current}))

        _stock_scan_seeded = True

    for kind, product, kwargs in events:
        await _send_stock_channel_message(bot, _stock_event_text(kind, product, **kwargs))


def _fire_stock_scan(bot, products: list):
    """Fire-and-forget wrapper so UI handlers never wait on notification sends."""
    try:
        asyncio.create_task(_scan_stock_changes(bot, products))
    except RuntimeError:
        logger.exception("Could not schedule stock scan task")


async def notify_new_product(bot, product):
    """
    Call this immediately after creating a new Product row in your admin
    panel, e.g. in admin.py:
        db.add(product)
        db.commit()
        db.refresh(product)
        await notify_new_product(callback.bot, product)
    """
    stock = _real_stock(product)
    threshold = product.low_stock_threshold if product.low_stock_threshold is not None else DEFAULT_LOW_STOCK_THRESHOLD

    async with _stock_state_lock:
        _stock_state[product.id] = {
            "last": stock,
            "low_stock_notified": stock <= threshold,
        }

    await _send_stock_channel_message(bot, _stock_event_text("new_product", product, stock=stock))


# ╔══════════════════════════════════════════════════════════════╗
# ║              FREEBIES MENU                                  ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "freebies_menu")
async def freebies_menu(callback: CallbackQuery):
    """Display all free products (price = 0) like the regular product catalog."""
    await callback.answer()

    products = await asyncio.to_thread(_fetch_free_products)

    if not products:
        await show(
            callback,
            (
                f"🎁 <b>FREEBIES</b>\n\n"
                f"📭 <b>No free products available right now.</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"💡 Check back later — free products\n"
                f"are added regularly!\n\n"
                f"<i>Stay tuned for giveaways 🎉</i>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍 Browse Paid Products", callback_data="products_menu", style="primary")],
                    [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
                ]
            ),
        )
        return

    _fire_stock_scan(callback.bot, products)

    text = (
        f"🎁 <b>FREEBIES</b>\n\n"
        f"<b>🎉 Free Products Available!</b>\n\n"
        f"{_divider('─', 28)}\n"
        f"<b>📊 Total Free Items:</b> {len(products)}\n\n"
        f"{_divider('─', 28)}\n\n"
        f"<b>👇 Grab your free product below:</b>\n"
        f"<i>Tap any item to claim it — no payment needed!</i>"
    )

    keyboard = []
    for p in products:
        cat_config = _get_category_config(p.category)
        stock = _real_stock(p)
        stock_badge = "🔴 OOS" if stock <= 0 else (f"🟡 {stock}" if stock <= 3 else f"🟢 {stock}")

        keyboard.append([
            InlineKeyboardButton(
                text=f"{p.icon or cat_config['icon']} {p.name} — FREE! 🎁 | {stock_badge}",
                callback_data=f"product_{p.id}",
                style=cat_config["style"],
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🛍 Browse Paid Products", callback_data="products_menu", style="primary"),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary"),
    ])

    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


# ╔══════════════════════════════════════════════════════════════╗
# ║              PRODUCTS MENU (PAID ONLY)                      ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "products_menu")
async def products_menu(callback: CallbackQuery):
    await callback.answer()

    products = await _fetch_active_products()

    if not products:
        await show(
            callback,
            (
                f"📦 <b>PRODUCTS</b>\n\n"
                f"📭 <b>No paid products available right now.</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"💡 Check back later or contact support\n"
                f"for more information about upcoming products."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🎁 Check Freebies", callback_data="freebies_menu", style="success")],
                    [InlineKeyboardButton(text="🆘 Contact Support", callback_data="support_menu", style="danger")],
                    [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="main_menu", style="primary")]
                ]
            ),
        )
        return

    _fire_stock_scan(callback.bot, products)

    categories = {}
    for p in products:
        cat_config = _get_category_config(p.category)
        cat_key = cat_config["label"]
        if cat_key not in categories:
            categories[cat_key] = {"count": 0, "config": cat_config}
        categories[cat_key]["count"] += 1

    text = (
        f"🛍 <b>PRODUCT CATALOG</b>\n\n"
        f"<b>📊 Available Products:</b> {len(products)}\n"
        f"<i>🎁 Free products available in Freebies section</i>\n\n"
        f"{_divider('─', 28)}\n"
        f"<b>📂 Categories:</b>\n"
    )

    for cat_name, cat_data in sorted(categories.items()):
        cfg = cat_data["config"]
        text += f"  {cfg['color']} {cfg['icon']} <b>{cat_name}</b> — {cat_data['count']} items\n"

    text += f"\n{_divider('═', 28)}\n\n<b>👇 Select a product below:</b>"

    keyboard = []
    for p in products:
        cat_config = _get_category_config(p.category)
        stock = _real_stock(p)
        price = _money(p.price)
        stock_badge = "🔴 OOS" if stock <= 0 else (f"🟡 {stock}" if stock <= 3 else f"🟢 {stock}")
        bulk_badge = " 📦" if p.bulk_pricing else ""

        keyboard.append([
            InlineKeyboardButton(
                text=f"{p.icon or cat_config['icon']} {p.name} — ${price:.2f}{bulk_badge} | {stock_badge}",
                callback_data=f"product_{p.id}",
                style=cat_config["style"],
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🔍 Search", callback_data="search_start", style="primary"),
        InlineKeyboardButton(text="⭐ Favorites", callback_data="favorites_menu", style="success"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="🎁 Freebies", callback_data="freebies_menu", style="primary"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="📜 My Orders", callback_data="orders_menu", style="primary"),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary"),
    ])

    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


# ╔══════════════════════════════════════════════════════════════╗
# ║              FAVORITES SYSTEM                               ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.startswith("toggle_fav_"))
async def toggle_favorite(callback: CallbackQuery):
    product_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    added = await _toggle_favorite(user_id, product_id)
    await callback.answer(
        "⭐ Added to favorites!" if added else "💔 Removed from favorites",
        show_alert=True
    )


@router.callback_query(F.data == "favorites_menu")
async def favorites_menu(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    fav_ids = await _get_favorites(user_id)

    if not fav_ids:
        await show(
            callback,
            (
                f"⭐ <b>FAVORITES</b>\n\n"
                f"📭 <b>No favorites yet!</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"💡 <b>How to add:</b>\n"
                f"1. Browse products\n"
                f"2. Tap ⭐ on a product\n"
                f"3. It'll appear here!\n\n"
                f"<i>Your favorites are saved automatically.</i>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success")],
                    [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")],
                ]
            ),
        )
        return

    products = await asyncio.to_thread(_fetch_products_by_ids, fav_ids)

    if not products:
        await show(callback, "⚠️ Some favorites are no longer available.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🛍 Products", callback_data="products_menu", style="primary")]]
            ))
        return

    text = (
        f"⭐ <b>YOUR FAVORITES</b>\n\n"
        f"❤️ <b>{len(products)} favorite item(s):</b>\n\n"
        f"{_divider('─', 28)}\n\n"
    )

    for p in products:
        cat_config = _get_category_config(p.category)
        stock = _real_stock(p)
        stock_icon = "🟢" if stock > 0 else "🔴"
        is_free = float(p.price) == 0
        price_text = "🎁 FREE" if is_free else f"💰 ${_money(p.price):.2f}"
        text += (
            f"{p.icon or cat_config['icon']} <b>{_esc(p.name)}</b>\n"
            f"  {price_text} | {stock_icon} Stock: {stock}\n"
            f"  🏷 {cat_config['color']} {cat_config['label']}\n\n"
        )

    keyboard = []
    for p in products:
        cat_config = _get_category_config(p.category)
        keyboard.append([
            InlineKeyboardButton(
                text=f"{p.icon or cat_config['icon']} {p.name}",
                callback_data=f"product_{p.id}",
                style=cat_config["style"]
            ),
            InlineKeyboardButton(
                text="💔 Remove",
                callback_data=f"toggle_fav_{p.id}",
                style="danger"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success"),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary"),
    ])

    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


# ╔══════════════════════════════════════════════════════════════╗
# ║              NOTIFY WHEN AVAILABLE                          ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.startswith("notify_available_"))
async def notify_when_available(callback: CallbackQuery):
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    product = await asyncio.to_thread(_fetch_product, product_id)
    user_id = callback.from_user.id

    if not product:
        return

    is_new = await _add_notify_subscriber(product_id, user_id)
    cat_config = _get_category_config(product.category)

    if is_new:
        text = (
            f"🔔 <b>NOTIFICATION SET</b>\n\n"
            f"🔔 <b>You'll be notified when this is back in stock!</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"📦 <b>Product:</b> {product.icon or cat_config['icon']} {product.name}\n"
            f"🏷 <b>Category:</b> {cat_config['color']} {cat_config['label']}\n"
            f"💰 <b>Price:</b> ${_money(product.price):.2f}\n\n"
            f"📬 <b>What happens next:</b>\n"
            f"• You'll receive a Telegram message\n"
            f"• As soon as stock is available\n"
            f"• You'll be first to know!\n\n"
            f"<i>Stay tuned! 🎯</i>"
        )
    else:
        text = (
            f"🔔 <b>ALREADY SUBSCRIBED</b>\n\n"
            f"✅ <b>You're already on the notification list!</b>\n\n"
            f"📦 {product.icon or cat_config['icon']} <b>{product.name}</b>\n\n"
            f"We'll message you as soon as it's available.\n\n"
            f"<i>No need to subscribe again.</i>"
        )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔕 Unsubscribe", callback_data=f"notify_remove_{product_id}", style="danger")],
            [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="primary")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")],
        ]
    )

    await show(callback, text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("notify_remove_"))
async def notify_remove(callback: CallbackQuery):
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    removed = await _remove_notify_subscriber(product_id, user_id)
    await callback.answer(
        "🔕 Unsubscribed from notifications" if removed else "You weren't subscribed to this product",
        show_alert=True
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║              SEARCH FUNCTIONALITY                           ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "search_start")
async def search_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SearchStates.waiting_query)

    text = (
        f"🔍 <b>SEARCH PRODUCTS</b>\n\n"
        f"<b>What are you looking for?</b>\n\n"
        f"{_divider('─', 28)}\n\n"
        f"💡 <b>Search tips:</b>\n"
        f"• Product name (e.g., Netflix)\n"
        f"• Category (e.g., VPN, Streaming)\n"
        f"• Keywords in description\n\n"
        f"<i>Type your search below 👇</i>\n"
        f"<i>Send 'cancel' to exit search</i>"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Browse All", callback_data="products_menu", style="primary"),
             InlineKeyboardButton(text="❌ Cancel", callback_data="search_cancel", style="danger")]
        ]
    )

    await show(callback, text, parse_mode="HTML", reply_markup=markup, state=state)


@router.callback_query(F.data == "search_cancel")
async def search_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show(callback,
               f"❌ <b>SEARCH CANCELLED</b>\n\n<b>Search cancelled.</b>\n\nBrowse all products or try again later.",        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success")],
                [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
            ]))


@router.message(SearchStates.waiting_query, F.text)
async def search_results(message: Message, state: FSMContext):
    query = message.text.strip()

    if query.lower() in ["cancel", "exit", "quit", "stop"]:
        await state.clear()
        data = await state.get_data()
        await update_card(message, None,
            "<b>Search cancelled.</b>\n\nBrowse all products or try again later.",
            chat_id=data.get("_card_chat_id"), message_id=data.get("_card_message_id"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success")]]
            ))
        return

    if len(query) < 2:
        await update_card(message, state,
            f"⚠️ <b>Search too short</b>\n\n{_divider('─', 24)}\n\nPlease enter at least 2 characters.\n\n<i>Try a product name or category.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍 Browse All", callback_data="products_menu", style="primary"),
                     InlineKeyboardButton(text="❌ Cancel", callback_data="search_cancel", style="danger")]
                ]))
        return

    await update_card(message, state,
        f"🔍 <b>Searching for:</b> <code>{_esc(query)}</code>\n\n<i>Looking through products...</i>",
        parse_mode="HTML")

    products = await asyncio.to_thread(_search_products, query)
    await state.clear()
    data = await state.get_data()
    card_chat_id = data.get("_card_chat_id")
    card_message_id = data.get("_card_message_id")

    if not products:
        text = (
            f"🔍 <b>NO RESULTS</b>\n\n"
            f"📭 <b>No products found for:</b>\n<code>{_esc(query)}</code>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"💡 <b>Suggestions:</b>\n• Try different keywords\n• Check spelling\n• Browse all categories\n\n"
            f"<i>Or browse all available products below.</i>"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Browse All Products", callback_data="products_menu", style="success")],
                [InlineKeyboardButton(text="🔄 New Search", callback_data="search_start", style="primary"),
                 InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
            ])
    else:
        plural = "s" if len(products) != 1 else ""
        text = (
            f"🔍 <b>SEARCH RESULTS</b>\n\n"
            f"✅ <b>Found {len(products)} product{plural}</b>\nfor: <code>{_esc(query)}</code>\n\n"
            f"{_divider('─', 28)}\n\n"
        )

        for p in products[:MAX_SEARCH_RESULTS]:
            cat_config = _get_category_config(p.category)
            stock = _real_stock(p)
            stock_icon = "🟢" if stock > 0 else "🔴"
            is_free = float(p.price) == 0
            price_text = "🎁 FREE" if is_free else f"💰 ${_money(p.price):.2f}"
            text += (
                f"{p.icon or cat_config['icon']} <b>{_esc(p.name)}</b>\n"
                f"  {price_text} | {stock_icon} Stock: {stock}\n"
                f"  🏷 {cat_config['color']} {cat_config['label']}\n\n"
            )

        if len(products) > 10:
            text += f"...and {len(products) - 10} more. Narrow your search or browse all.\n\n"

        text += f"{_divider('─', 28)}\n\n<i>Select a product from below to view details.</i>"

        keyboard = []
        for p in products[:10]:
            cat_config = _get_category_config(p.category)
            is_free = float(p.price) == 0
            price_label = "FREE!" if is_free else f"${_money(p.price):.2f}"
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{p.icon or cat_config['icon']} {p.name} — {price_label}",
                    callback_data=f"product_{p.id}",
                    style=cat_config["style"]
                )
            ])

        keyboard.append([
            InlineKeyboardButton(text="🔄 New Search", callback_data="search_start", style="primary"),
            InlineKeyboardButton(text="🛍 Show All", callback_data="products_menu", style="success")
        ])
        keyboard.append([
            InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await update_card(message, None, text, chat_id=card_chat_id, message_id=card_message_id, parse_mode="HTML", reply_markup=markup)


# ╔══════════════════════════════════════════════════════════════╗
# ║              PRODUCT DETAILS                                ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.startswith("product_"))
async def product_info(callback: CallbackQuery):
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    product = await asyncio.to_thread(_fetch_product, product_id)
    user_id = callback.from_user.id

    if not product:
        await show(callback,
                   f"❌ <b>NOT FOUND</b>\n\n<b>This product is no longer available.</b>",            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="primary")],
                    [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
                ]))
        return

    _fire_stock_scan(callback.bot, [product])

    max_qty = _get_max_qty(product)
    real_stock_available = _real_stock(product) > 0
    stock_count = _real_stock(product)
    cat_config = _get_category_config(product.category)
    is_fav = await _is_favorite(user_id, product_id)

    delivery_type = (product.delivery_type or "automatic").lower()
    delivery_config = {
        "automatic": {"icon": "🤖", "label": "Auto-Delivery", "desc": "Instant delivery after purchase"},
        "manual": {"icon": "👨‍💼", "label": "Manual Delivery", "desc": "Delivered by our team"},
        "hybrid": {"icon": "🔀", "label": "Hybrid", "desc": "Auto + manual delivery"},
    }
    delivery_info = delivery_config.get(delivery_type, {"icon": "📦", "label": "Standard", "desc": ""})

    description_block = product.description.strip() if product.description else "<i>No description available.</i>"
    fav_star = "⭐" if is_fav else "☆"

    # Check if product is free
    is_free = float(product.price) == 0

    # Check if delivery instruction exists 🆕
    has_instruction = bool(product.delivery_instruction)

    text = (
        f"{product.icon or '📦'} <b>PRODUCT DETAILS</b>\n\n"
        f"<b>{product.icon or cat_config['icon']} {_esc(product.name)}</b> {fav_star}\n"
        f"🏷 {cat_config['color']} <b>{cat_config['label']}</b>\n\n"
        f"{_divider('─', 28)}\n\n"
        f"<b>📝 Description:</b>\n"
        f"<blockquote>{description_block}</blockquote>\n\n"
        f"{_divider('─', 28)}\n\n"
        f"<b>📊 Product Info:</b>\n"
    )


    if is_free:
        text += f"  🎁 <b>Price:</b> <code>FREE!</code>\n"
    else:
        text += f"  💰 <b>Base Price:</b> <code>${_money(product.price):.2f}</code> each\n"

    text += (
        f"  📦 <b>Stock:</b> {_stock_indicator(stock_count)}\n"
        f"  🏷 <b>Category:</b> {cat_config['icon']} {cat_config['label']}\n"
        f"  {delivery_info['icon']} <b>Delivery:</b> {delivery_info['label']}\n"
    )

    if delivery_info['desc']:
        text += f"  └ <i>{delivery_info['desc']}</i>\n"

    if product.preorder:
        text += f"  📦 <b>Preorder:</b> ✅ Available\n"

    # 🆕 Show delivery instruction indicator
    if has_instruction:
        text += f"  📋 <b>Instructions:</b> ✅ Available (shown after purchase)\n"

    # Bulk pricing section - only for paid products
    if not is_free:
        text += f"\n{_divider('─', 28)}\n"
        text += f"📦 <b>Bulk Pricing:</b>\n"
        if product.bulk_pricing:
            text += _format_bulk_pricing_text(product.bulk_pricing) + "\n"
            text += f"\n  💡 <i>Buy more, save more!</i>"
        else:
            text += f"  ❌ <i>Not Available</i>\n"
            text += f"  └ All quantities at base price"

    text += f"\n{_divider('═', 28)}\n"

    if not real_stock_available and max_qty > 0:
        text += f"\n⚠️ <b>Out of Stock — Preorder Available</b>\n<i>Order now and receive when restocked.</i>\n"

    buttons = []

    if max_qty <= 0:
        text += f"\n❌ <b>Currently Unavailable</b>"
        buttons.append([
            InlineKeyboardButton(text="🔔 Notify When Available", callback_data=f"notify_available_{product_id}", style="primary")
        ])
    else:
        if is_free:
            button_text = "🎁 Claim Free Product! 🎁" if real_stock_available else f"📦 Preorder (FREE)"
        else:
            button_text = f"🛒 Buy Now — ${_money(product.price):.2f}" if real_stock_available else f"📦 Preorder — ${_money(product.price):.2f}"
        button_style = "success" if real_stock_available else "primary"
        buttons.append([
            InlineKeyboardButton(text=button_text, callback_data=f"select_qty_{product_id}", style=button_style)
        ])
        if real_stock_available and max_qty >= 1:
            text += f"\n💡 <b>Quick Buy:</b> You can buy up to <b>{max_qty}</b> units."

    fav_label = "⭐ Remove from Favorites" if is_fav else "☆ Add to Favorites"
    fav_style = "danger" if is_fav else "success"
    buttons.append([
        InlineKeyboardButton(text=fav_label, callback_data=f"toggle_fav_{product_id}", style=fav_style),
    ])

    buttons.append([
        InlineKeyboardButton(text="🛍 All Products", callback_data="products_menu", style=cat_config["style"]),
        InlineKeyboardButton(text="📜 My Orders", callback_data="orders_menu", style="primary"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔍 Search", callback_data="search_start", style="primary"),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary"),
    ])

    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ╔══════════════════════════════════════════════════════════════╗
# ║              QUANTITY INPUT + CONFIRM                       ║
# ╚══════════════════════════════════════════════════════════════╝

def _qty_text(product, qty: int, real_stock_available: bool) -> str:
    is_free = float(product.price) == 0

    if is_free:
        cat_config = _get_category_config(product.category)
        header = "🎁 FREE CLAIM" if real_stock_available else "📦 PREORDER (FREE)"
        return (
            f"{product.icon or '🎁'} <b>{header}</b>\n\n"
            f"<b>{product.icon or cat_config['icon']} {product.name}</b>\n"
            f"🏷 {cat_config['color']} {cat_config['label']}\n\n"
            f"{_divider('─', 28)}\n\n"
            f"<b>📊 Order Summary:</b>\n"
            f"  🎁 <b>Price:</b> <code>FREE!</code>\n"
            f"  📦 <b>Quantity:</b> {qty}x\n"
            f"  ━━━━━━━━━━━━━━━━━━\n"
            f"  💵 <b>Total:</b> <code>$0.00</code>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"🎉 <b>No payment needed!</b>\n\n"
            f"<i>Confirm below to claim your free product.</i>"
        )

    actual_price = _money(product.price)
    discount_note = ""

    if product.bulk_pricing:
        bulk_price = _get_bulk_price(product.bulk_pricing, qty)
        if bulk_price is not None:
            actual_price = _money(bulk_price)
            if actual_price < _money(product.price):
                savings_per_unit = _money(product.price) - actual_price
                total_savings = savings_per_unit * qty
                discount_note = (
                    f"  🎉 <b>Bulk Discount Applied!</b>\n"
                    f"  └ 💰 ${actual_price:.2f}/each (saved ${total_savings:.2f} total!)\n"
                )

    total = actual_price * qty
    cat_config = _get_category_config(product.category)
    header = "📦 PREORDER" if not real_stock_available else "🛒 PURCHASE"
    return (
        f"{product.icon or '📦'} <b>{header}</b>\n\n"
        f"<b>{product.icon or cat_config['icon']} {product.name}</b>\n"
        f"🏷 {cat_config['color']} {cat_config['label']}\n\n"
        f"{_divider('─', 28)}\n\n"
        f"<b>📊 Order Summary:</b>\n"
        f"  📦 <b>Quantity:</b> {qty}x\n"
        f"  💰 <b>Price per unit:</b> ${actual_price:.2f}\n"
        f"{discount_note}"
        f"  ━━━━━━━━━━━━━━━━━━\n"
        f"  💵 <b>Total:</b> <code>${total:.2f}</code>\n\n"
        f"{_divider('─', 28)}\n\n"
        f"<i>Please confirm your order below.</i>"
    )


def _confirm_keyboard(product_id: int, is_free: bool = False) -> InlineKeyboardMarkup:
    confirm_text = "🎁 Claim Now! 🎁" if is_free else "✅ Confirm Purchase"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=confirm_text, callback_data=f"confirm_buy_{product_id}", style="success")],
            [InlineKeyboardButton(text="🔄 Change Quantity", callback_data=f"select_qty_{product_id}", style="primary"),
             InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_buy_{product_id}", style="danger")]
        ]
    )


def _cancel_input_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_buy_{product_id}", style="danger"),
             InlineKeyboardButton(text="🔙 Back to Product", callback_data=f"product_{product_id}", style="primary")]
        ]
    )


@router.callback_query(F.data.startswith("select_qty_"))
async def select_qty(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    product = await asyncio.to_thread(_fetch_product, product_id)

    if not product or not product.is_active:
        await show(callback, f"⚠️ <b>Product Unavailable</b>", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="primary")]
            ]))
        return

    max_qty = _get_max_qty(product)
    if max_qty <= 0:
        await show(callback,
                   f"❌ <b>OUT OF STOCK</b>\n\n<b>This product is currently unavailable.</b>",            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Browse Other Products", callback_data="products_menu", style="success")],
                [InlineKeyboardButton(text="🔔 Notify When Available", callback_data=f"notify_available_{product_id}", style="primary")]
            ]))
        return

    cat_config = _get_category_config(product.category)
    range_text = "1" if max_qty == 1 else f"1 to {max_qty}"
    is_free = float(product.price) == 0

    if is_free:
        text = (
            f"🎁 <b>CLAIM FREE PRODUCT</b>\n\n"
            f"<b>{product.icon or cat_config['icon']} {product.name}</b>\n"
            f"🎁 <b>Price:</b> FREE! 🎉\n"
            f"📦 <b>Available:</b> {max_qty} units\n\n"
            f"{_divider('─', 28)}\n\n"
            f"🔢 <b>How many would you like?</b>\n\n"
            f"Reply with a number ({range_text})"
        )
    else:
        has_bulk = bool(product.bulk_pricing)
        bulk_info = ""
        if has_bulk:
            bulk_info = "\n📦 <b>💰 Bulk Discounts Available!</b>\n"
            bulk_tiers = _format_bulk_pricing_text(product.bulk_pricing)
            bulk_info += bulk_tiers
            bulk_info += "\n\n<i>The price adjusts automatically based on your quantity!</i>"

        text = (
            f"🔢 <b>SELECT QUANTITY</b>\n\n"
            f"<b>{product.icon or cat_config['icon']} {product.name}</b>\n"
            f"💰 <b>Base Price:</b> ${_money(product.price):.2f} each\n"
            f"📦 <b>Available:</b> {max_qty} units\n"
            f"{bulk_info}\n"
            f"{_divider('─', 28)}\n\n"
            f"🔢 <b>How many would you like?</b>\n\n"
            f"Reply with a number ({range_text})"
        )

    if max_qty > 1:
        text += f"\n\n<i>Example: Send 3 for three units</i>"

    await state.set_state(PurchaseStates.waiting_qty)
    await state.update_data(pending_product_id=product_id)
    await show(callback, text, parse_mode="HTML", reply_markup=_cancel_input_keyboard(product_id), state=state)


@router.message(PurchaseStates.waiting_qty, F.text)
async def receive_qty(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("pending_product_id")
    card_chat_id = data.get("_card_chat_id")
    card_message_id = data.get("_card_message_id")

    if product_id is None:
        await state.clear()
        await update_card(message, None, f"⚠️ <b>Session Expired</b>\n\nPlease start over from the product page.",
            chat_id=card_chat_id, message_id=card_message_id, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Products", callback_data="products_menu", style="primary")]
            ]))
        return

    product = await asyncio.to_thread(_fetch_product, product_id)
    if not product or not product.is_active:
        await state.clear()
        await update_card(message, None, f"⚠️ <b>Product Unavailable</b>",
            chat_id=card_chat_id, message_id=card_message_id, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Products", callback_data="products_menu", style="primary")]
            ]))
        return

    max_qty = _get_max_qty(product)
    if max_qty <= 0:
        await state.clear()
        await update_card(message, None, f"❌ <b>Out of Stock</b>",
            chat_id=card_chat_id, message_id=card_message_id, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="primary")]
            ]))
        return

    raw = message.text.strip()
    if not raw.isdigit():
        await update_card(message, state,
            f"⚠️ <b>Invalid Input</b>\n\n{_divider('─', 24)}\n\n❌ Please send a whole number.\n\n"
            f"<b>Valid range:</b> 1 to {max_qty}\n\n<i>Example: 1 or 3 or {max_qty}</i>",
            parse_mode="HTML", reply_markup=_cancel_input_keyboard(product_id))
        return

    qty = int(raw)
    if qty < 1 or qty > max_qty:
        await update_card(message, state,
            f"⚠️ <b>Out of Range</b>\n\n{_divider('─', 24)}\n\n❌ Quantity must be between <b>1</b> and <b>{max_qty}</b>.\n\n"
            f"You entered: <b>{qty}</b>\n\n<i>Please try again.</i>",
            parse_mode="HTML", reply_markup=_cancel_input_keyboard(product_id))
        return

    await state.update_data(**{f"qty_{product_id}": qty})
    real_stock_available = _real_stock(product) > 0
    is_free = float(product.price) == 0
    await update_card(message, state, _qty_text(product, qty, real_stock_available),
        parse_mode="HTML", reply_markup=_confirm_keyboard(product_id, is_free))


@router.callback_query(F.data.startswith("cancel_buy_"))
async def cancel_buy(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    await state.update_data(**{f"qty_{product_id}": 1})
    await state.clear()
    await show(callback,
               f"❌ <b>CANCELLED</b>\n\n<b>Purchase cancelled.</b>\n\nYour balance has not been charged.",        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success"),
             InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
        ]))


# ╔══════════════════════════════════════════════════════════════╗
# ║              PURCHASE TRANSACTION                           ║
# ╚══════════════════════════════════════════════════════════════╝

@retry_on_write_conflict(max_attempts=3)
def _do_purchase(telegram_id: int, product_id: int, quantity: int) -> dict:
    with transaction() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).with_for_update().first()
        if not user:
            return {"error": "User not found."}
        if getattr(user, "is_banned", False):
            return {"error": "Your account is banned from making purchases."}

        product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
        if not product:
            return {"error": "Product not found."}
        if not product.is_active:
            return {"error": "Product unavailable."}
        if quantity < 1:
            return {"error": "Quantity must be at least 1."}

        # 🎁 Freebie limit: one claim per user per free product
        if float(product.price) == 0:
            already_claimed = (
                db.query(Order)
                .filter(
                    Order.telegram_id == telegram_id,
                    Order.product_id == product.id,
                    Order.status.in_(["completed", "pending_manual", "preorder"]),
                    Order.refunded == False
                )
                .count()
            ) > 0
            if already_claimed:
                return {
                    "error": (
                        "🎁 <b>Already Claimed!</b>\n\n"
                        "You've already claimed this free product.\n"
                        "Each freebie can only be claimed once per user.\n\n"
                        "💡 Check out our other free products or browse paid items!"
                    )
                }

        # Apply bulk pricing if applicable
        price = _money(product.price)
        if product.bulk_pricing:
            bulk_price = _get_bulk_price(product.bulk_pricing, quantity)
            if bulk_price is not None:
                price = _money(bulk_price)

        total_amount = _money(price * quantity)
        user_balance = _money(user.balance)

        if user_balance < total_amount:
            return {
                "error": "insufficient_balance",
                "total_price": float(total_amount),
                "balance": float(user_balance)
            }

        delivery_type = (product.delivery_type or "automatic").lower()
        threshold = product.low_stock_threshold if product.low_stock_threshold is not None else DEFAULT_LOW_STOCK_THRESHOLD

        accounts = _accounts(product)
        available = len(accounts)
        stock_before = product.stock or 0

        delivered_accounts: list[str] = []
        is_preorder_order = False
        new_stock = stock_before

        can_auto_deliver = delivery_type in ("automatic", "hybrid") and available >= quantity
        can_manual_fulfill = delivery_type in ("manual", "hybrid") and stock_before >= quantity

        if can_auto_deliver:
            delivered_accounts = accounts[:quantity]
            product.file_content = "\n".join(accounts[quantity:])
            new_stock = len(accounts) - quantity
            product.stock = new_stock
        elif can_manual_fulfill:
            new_stock = stock_before - quantity
            product.stock = new_stock
        elif product.preorder:
            is_preorder_order = True
        else:
            shortfall = available if delivery_type == "automatic" else stock_before
            return {"error": f"Only {shortfall} left in stock."}

        if new_stock < 0:
            return {"error": "Stock changed while processing your order. Please try again."}

        status = "completed" if delivered_accounts else ("preorder" if is_preorder_order else "pending_manual")

        user.balance = user_balance - total_amount
        user.total_orders += 1
        user.total_spent = _money(user.total_spent) + total_amount

        order = Order(
            telegram_id=user.telegram_id, product_id=product.id, product_name=product.name,
            delivered_account="\n".join(delivered_accounts) if delivered_accounts else None,
            amount=total_amount, quantity=quantity, delivery_type=delivery_type,
            is_preorder=is_preorder_order, status=status, refunded=False
        )
        db.add(order)
        db.flush()

        referral_commission_paid = None
        if not is_preorder_order and user.referred_by:
            referrer = db.query(User).filter(User.telegram_id == user.referred_by).with_for_update().first()
            if referrer is not None:
                commission = _money(total_amount * REFERRAL_COMMISSION_RATE)
                if commission > 0:
                    referrer.referral_earnings = _money(referrer.referral_earnings) + commission
                    if REFERRAL_CREDIT_TO_BALANCE:
                        referrer.balance = _money(referrer.balance) + commission
                    referral_commission_paid = {"referrer_telegram_id": referrer.telegram_id, "amount": commission}

        low_stock_alert = None
        if not is_preorder_order and stock_before > threshold >= new_stock:
            low_stock_alert = {"product_id": product.id, "product_name": product.name, "stock": new_stock, "threshold": threshold}

        # 🆕 Include delivery_instruction in result
        result = {
            "order_id": order.id, "icon": product.icon, "name": product.name,
            "delivered_accounts": delivered_accounts, "balance": user.balance,
            "stock": new_stock, "status": status, "is_preorder": is_preorder_order,
            "quantity": quantity, "total_price": total_amount, "price_per_unit": price,
            "low_stock_alert": low_stock_alert, "referral_commission_paid": referral_commission_paid,
            "delivery_instruction": product.delivery_instruction,  # 🆕
        }
    return result


async def _notify_admins_low_stock(bot, alert: dict):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,
                f"⚠️ <b>LOW STOCK ALERT</b>\n\n{_divider('─', 24)}\n\n"
                f"📦 <b>Product:</b> {alert['product_name']}\n🆔 <b>ID:</b> #{alert['product_id']}\n"
                f"📊 <b>Remaining:</b> {alert['stock']}\n🔔 <b>Threshold:</b> {alert['threshold']}\n\n"
                f"⚡ <i>Stock has dropped below the alert threshold.</i>", parse_mode="HTML")
        except Exception:
            logger.exception("Failed to notify admin %s of low stock", admin_id)


async def _notify_admins_pending_order(bot, buyer_id: int, result: dict):
    kind = "Preorder" if result["is_preorder"] else "Manual Order"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,
                f"🆕 <b>NEW {kind.upper()}</b>\n\n{_divider('─', 24)}\n\n"
                f"🆔 <b>Order:</b> #{result['order_id']}\n👤 <b>Buyer ID:</b> <code>{buyer_id}</code>\n"
                f"📦 <b>Product:</b> {result['name']}\n🔢 <b>Quantity:</b> {result['quantity']}x\n"
                f"💰 <b>Total:</b> ${result['total_price']:.2f}\n\n"
                f"📋 <b>Action Required:</b>\nAdmin → Orders → #{result['order_id']} → Deliver", parse_mode="HTML")
        except Exception:
            logger.exception("Failed to notify admin %s of pending order", admin_id)


# ╔══════════════════════════════════════════════════════════════╗
# ║         DELIVERY INSTRUCTION BUTTON HANDLER 🆕              ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.startswith("delivery_instruction_"))
async def show_delivery_instruction(callback: CallbackQuery):
    """Show delivery instruction when user clicks the button after purchase."""
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    product = await asyncio.to_thread(_fetch_product, product_id)

    if not product or not product.delivery_instruction:
        await callback.answer("📋 No delivery instructions available.", show_alert=True)
        return

    cat_config = _get_category_config(product.category)

    text = (
        f"╔{'═' * 30}╗\n"
        f"║  📋 DELIVERY INSTRUCTIONS      ║\n"
        f"╚{'═' * 30}╝\n\n"
        f"<b>{product.icon or cat_config['icon']} {_esc(product.name)}</b>\n\n"
        f"{'─' * 30}\n\n"
        f"<b>⚠️ IMPORTANT — READ CAREFULLY:</b>\n\n"
        f"<blockquote>{_esc(product.delivery_instruction)}</blockquote>\n\n"
        f"{'═' * 30}\n\n"
        f"<i>💡 Please follow these instructions carefully\n"
        f"to ensure a smooth experience.</i>\n\n"
        f"<i>If you have any issues, contact support!</i>"
    )

    await callback.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📜 View Orders", callback_data="orders_menu", style="success"),
                 InlineKeyboardButton(text="🛍 Buy More", callback_data="products_menu", style="primary")],
                [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
            ]
        )
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║              CONFIRM PURCHASE                               ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.startswith("confirm_buy_"))
async def confirm_buy(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    telegram_id = callback.from_user.id
    product_id = int(callback.data.split("_")[2])

    lock = await _get_purchase_lock(telegram_id)
    if lock.locked():
        await callback.answer("⏳ Your previous order is still being processed…", show_alert=True)
        return

    async with lock:
        data = await state.get_data()
        quantity = data.get(f"qty_{product_id}", 1)

        try:
            result = await asyncio.to_thread(_do_purchase, telegram_id, product_id, quantity)
        except SQLAlchemyError:
            logger.exception("Database error during purchase")
            await show(callback, f"❌ <b>Database Error</b>\n\nSomething went wrong. Please try again.", parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Try Again", callback_data=f"product_{product_id}", style="primary"),
                     InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
                ]))
            return
        except Exception:
            logger.exception("Unexpected error during purchase")
            await show(callback, f"❌ <b>Unexpected Error</b>\n\nPlease try again or contact support.", parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🆘 Support", callback_data="support_menu", style="danger"),
                     InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
                ]))
            return

        await state.update_data(**{f"qty_{product_id}": 1})
        await state.clear()

        if "error" in result:
            error_msg = result["error"]
            is_insufficient = error_msg == "insufficient_balance"

            if is_insufficient:
                total_price = result.get("total_price", 0)
                balance = result.get("balance", 0)
                text = (
                    f"╔{'═' * 30}╗\n"
                    f"║  💸 INSUFFICIENT BALANCE        ║\n"
                    f"╚{'═' * 30}╝\n\n"
                    f"😔 <b>Oops! You don't have enough funds.</b>\n\n"
                    f"{'─' * 30}\n\n"
                    f"🛒 <b>Order Summary:</b>\n"
                    f"   💰 <b>Cost:</b> <code>${total_price:.2f}</code>\n"
                    f"   💳 <b>Your Balance:</b> <code>${balance:.2f}</code>\n\n"
                    f"{'─' * 30}\n\n"
                    f"💡 <b>What would you like to do?</b>\n\n"
                    f"  🏦 <b>Deposit Funds</b> — Add money to\n"
                    f"     your wallet and try again.\n\n"
                    f"  🛍 <b>Browse Products</b> — Find\n"
                    f"     something within your budget.\n\n"
                    f"  🏠 <b>Main Menu</b> — Go back to\n"
                    f"     the dashboard.\n\n"
                    f"{'─' * 30}\n\n"
                    f"⚡ <i>Quick Tip: Top up your balance\n"
                    f"with crypto or fiat in seconds!</i>"
                )
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🏦 💰 Deposit Funds Now",
                                callback_data="deposit_start",
                                style="success"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🛍 Browse Other Products",
                                callback_data="products_menu",
                                style="primary"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🏠 Back to Main Menu",
                                callback_data="main_menu",
                                style="primary"
                            )
                        ]
                    ]
                )
            else:
                text = (
                    f"╔{'═' * 30}╗\n"
                    f"║  ❌ PURCHASE FAILED             ║\n"
                    f"╚{'═' * 30}╝\n\n"
                    f"⚠️ <b>{error_msg}</b>\n\n"
                    f"{'─' * 30}\n\n"
                    f"💡 <i>If you need help, contact\n"
                    f"our support team anytime!</i>"
                )
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💳 Deposit Funds",
                                callback_data="deposit_start",
                                style="success"
                            ),
                            InlineKeyboardButton(
                                text="🛍 Browse Products",
                                callback_data="products_menu",
                                style="primary"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🏠 Main Menu",
                                callback_data="main_menu",
                                style="primary"
                            )
                        ]
                    ]
                )

            await show(callback, text, parse_mode="HTML", reply_markup=reply_markup)
            return

        # Group notification (purchase / deposit channel — config.GROUP_ID)
        if GROUP_NOTIFICATIONS and GROUP_ID:
            try:
                now = datetime.now().strftime("%d-%b-%Y %I:%M %p IST")
                uid = str(telegram_id)
                masked_uid = f"{uid[:4]}***{uid[-3:]}" if len(uid) >= 7 else uid

                group_msg = (
                    "<code>$ journalctl --wallet</code>\n"
                    "<code>New wallet event detected.</code>\n"
                    "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
                    f"<code>ACTION      PURCHASE</code>\n"
                    f"<code>USER        {masked_uid}</code>\n"
                    f"<code>PRODUCT     {result['name']}</code>\n"
                    f"<code>AMOUNT      ${result['total_price']:.2f}</code>\n"
                    f"<code>ORDER       #{result['order_id']}</code>\n"
                    f"<code>TIME        {now}</code>\n"
                    "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
                    "<code>Wallet synchronized.</code>"
                )
                await callback.bot.send_message(
                    chat_id=GROUP_ID,
                    text=group_msg,
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send group purchase notification")

        is_free = float(result.get("total_price", 0)) == 0
        delivery_instruction = result.get("delivery_instruction")

        # Helper: Build all reply_markup buttons with optional delivery instruction button
        def _build_success_keyboard(product_id: int, has_instruction: bool) -> InlineKeyboardMarkup:
            buttons = []
            if has_instruction:
                buttons.append([
                    InlineKeyboardButton(
                        text="📋 📖 Delivery Instructions",
                        callback_data=f"delivery_instruction_{product_id}",
                        style="primary"
                    )
                ])
            buttons.append([
                InlineKeyboardButton(text="📜 View Orders", callback_data="orders_menu", style="success"),
                InlineKeyboardButton(text="🛍 Buy More", callback_data="products_menu", style="primary")
            ])
            buttons.append([
                InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")
            ])
            return InlineKeyboardMarkup(inline_keyboard=buttons)

        if result["status"] == "completed":
            joined_accounts = "\n".join(f"  {i + 1}. <code>{acc}</code>" for i, acc in enumerate(result["delivered_accounts"]))
            has_instr = bool(delivery_instruction)

            if is_free:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  🎁 FREEBIE CLAIMED!           ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"🎉 <b>Your free product has been delivered!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"🎁 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> $0.00 🎉\n\n"
                    f"{'─' * 34}\n\n"
                    f"🔑 <b>Your Accounts:</b>\n\n{joined_accounts}\n\n"
                    f"{'═' * 34}\n\n"
                    f"💳 <b>Balance:</b> <code>${result['balance']:.2f}</code>\n"
                    f"📦 <b>Stock Left:</b> {result['stock']} units\n\n"
                    f"<i>Enjoy your free product! 🎉</i>"
                )
                if has_instr:
                    text += f"\n\n📋 <b>⚠️ Important:</b> Tap <b>Delivery Instructions</b> below!"
            else:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  ✅ PURCHASE SUCCESSFUL        ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"🎉 <b>Your order has been delivered!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"📦 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> ${result['total_price']:.2f}\n\n"
                    f"{'─' * 34}\n\n"
                    f"🔑 <b>Your Accounts:</b>\n\n{joined_accounts}\n\n"
                    f"{'═' * 34}\n\n"
                    f"💳 <b>Remaining Balance:</b> <code>${result['balance']:.2f}</code>\n"
                    f"📦 <b>Stock Left:</b> {result['stock']} units\n\n"
                    f"<i>Thank you for your purchase! 🙏</i>"
                )
                if has_instr:
                    text += f"\n\n📋 <b>⚠️ Important:</b> Tap <b>Delivery Instructions</b> below!"

            reply_markup = _build_success_keyboard(product_id, has_instr)

        elif result["status"] == "preorder":
            if is_free:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  📦 PREORDER PLACED            ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"📦 <b>Your preorder has been confirmed!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"🎁 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> $0.00 🎉\n\n"
                    f"{'─' * 34}\n\n"
                    f"⏳ <b>Status:</b> Awaiting Restock\n"
                    f"📦 <b>Delivery:</b> You'll receive a message\n"
                    f"as soon as stock is available.\n\n"
                    f"💳 <b>Balance:</b> <code>${result['balance']:.2f}</code>\n\n"
                    f"<i>We'll notify you when it's ready! 🔔</i>"
                )
            else:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  📦 PREORDER PLACED            ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"📦 <b>Your preorder has been confirmed!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"📦 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> ${result['total_price']:.2f}\n\n"
                    f"{'─' * 34}\n\n"
                    f"⏳ <b>Status:</b> Awaiting Restock\n"
                    f"📦 <b>Delivery:</b> You'll receive a message\n"
                    f"as soon as stock is available.\n\n"
                    f"💳 <b>Remaining Balance:</b> <code>${result['balance']:.2f}</code>\n\n"
                    f"<i>We'll notify you when it's ready! 🔔</i>"
                )
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📜 Track Order", callback_data="orders_menu", style="primary"),
                 InlineKeyboardButton(text="🛍 Browse Products", callback_data="products_menu", style="success")],
                [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")]
            ])
        else:
            # pending_manual status
            has_instr = bool(delivery_instruction)

            if is_free:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  ⏳ ORDER RECEIVED             ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"⏳ <b>Your free order is being processed!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"🎁 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> $0.00 🎉\n\n"
                    f"{'─' * 34}\n\n"
                    f"👨‍💼 <b>Delivery:</b> Manual by our team\n"
                    f"⏱ <b>ETA:</b> Usually within 24 hours\n"
                    f"🔔 <b>Notification:</b> You'll receive\n"
                    f"a message when it's delivered.\n\n"
                    f"💳 <b>Balance:</b> <code>${result['balance']:.2f}</code>\n\n"
                    f"<i>Our team is on it! 🚀</i>"
                )
            else:
                text = (
                    f"╔{'═' * 34}╗\n"
                    f"║  ⏳ ORDER RECEIVED             ║\n"
                    f"╚{'═' * 34}╝\n\n"
                    f"⏳ <b>Your order is being processed!</b>\n\n"
                    f"{'─' * 34}\n\n"
                    f"🆔 <b>Order:</b> #{result['order_id']}\n"
                    f"📦 <b>Product:</b> {result['icon']} {result['name']}\n"
                    f"🔢 <b>Quantity:</b> {result['quantity']}x\n"
                    f"💰 <b>Charged:</b> ${result['total_price']:.2f}\n\n"
                    f"{'─' * 34}\n\n"
                    f"👨‍💼 <b>Delivery:</b> Manual by our team\n"
                    f"⏱ <b>ETA:</b> Usually within 24 hours\n"
                    f"🔔 <b>Notification:</b> You'll receive\n"
                    f"a message when it's delivered.\n\n"
                    f"💳 <b>Remaining Balance:</b> <code>${result['balance']:.2f}</code>\n\n"
                    f"<i>Our team is on it! 🚀</i>"
                )

            # Build keyboard with optional delivery instruction button
            pending_buttons = [
                [InlineKeyboardButton(text="📜 Track Order", callback_data="orders_menu", style="primary"),
                 InlineKeyboardButton(text="🆘 Support", callback_data="support_menu", style="danger")],
            ]
            if has_instr:
                pending_buttons.insert(0, [
                    InlineKeyboardButton(text="📋 📖 Delivery Instructions", callback_data=f"delivery_instruction_{product_id}", style="primary")
                ])
            pending_buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu", style="primary")])
            reply_markup = InlineKeyboardMarkup(inline_keyboard=pending_buttons)

        await show(callback, text, parse_mode="HTML", reply_markup=reply_markup)

        if result.get("low_stock_alert"):
            await _notify_admins_low_stock(callback.bot, result["low_stock_alert"])
        if result["status"] in ("pending_manual", "preorder"):
            await _notify_admins_pending_order(callback.bot, telegram_id, result)

        fresh_product = await asyncio.to_thread(_fetch_product, product_id)
        if fresh_product:
            _fire_stock_scan(callback.bot, [fresh_product])

        commission = result.get("referral_commission_paid")
        if commission:
            try:
                await callback.bot.send_message(commission["referrer_telegram_id"],
                    f"🎉 <b>Referral Commission Earned!</b>\n\n{_divider('─', 22)}\n\n"
                    f"💵 <b>Amount:</b> ${commission['amount']:.2f}\n👤 <b>From:</b> A user you referred\n\n"
                    f"<i>Thanks for sharing your link! 🙏</i>", parse_mode="HTML")
            except Exception:
                logger.exception("Failed to notify referrer %s of commission", commission["referrer_telegram_id"])