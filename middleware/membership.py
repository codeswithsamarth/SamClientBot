# middleware/membership.py

import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import CHANNEL_LINK, GROUP_LINK, ADMIN_IDS

logger = logging.getLogger(__name__)


def _extract_username(link: str) -> str:
    """Extract @username from t.me link. Skips private invite links."""
    if not link:
        return None

    link = link.rstrip('/')
    parts = link.split('/')

    if not parts:
        return None

    username = parts[-1]

    # Skip private invite links (they start with +)
    if username.startswith('+'):
        logger.warning(f"Skipping private link: {link}. Use a public @username instead.")
        return None

    if username.startswith('@'):
        username = username[1:]

    return f"@{username}"


CHANNEL_USERNAME = _extract_username(CHANNEL_LINK)
GROUP_USERNAME = _extract_username(GROUP_LINK)

# Print for debugging
print(f"CHANNEL_USERNAME: {CHANNEL_USERNAME}")
print(f"GROUP_USERNAME: {GROUP_USERNAME}")


async def check_user_membership(bot, user_id: int) -> bool:
    """Returns True if user is a member of all required channels/groups."""
    to_check = []
    if CHANNEL_USERNAME:
        to_check.append(CHANNEL_USERNAME)
    if GROUP_USERNAME:
        to_check.append(GROUP_USERNAME)

    if not to_check:
        logger.warning("No valid public channels to check — allowing access")
        return True

    for chat_username in to_check:
        try:
            member = await bot.get_chat_member(chat_id=chat_username, user_id=user_id)
            logger.info(f"User {user_id} in {chat_username}: {member.status}")

            if member.status in ["left", "kicked"]:
                logger.warning(f"User {user_id} NOT in {chat_username}: {member.status}")
                return False
        except Exception as e:
            logger.error(f"Failed to check {chat_username}: {e}")
            # If bot can't check, DON'T block the user
            continue

    return True


def get_join_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with join links and retry button."""
    buttons = []
    if CHANNEL_LINK and not CHANNEL_LINK.rstrip('/').split('/')[-1].startswith('+'):
        buttons.append([InlineKeyboardButton(text="📢 Join Channel", url=CHANNEL_LINK)])
    if GROUP_LINK and not GROUP_LINK.rstrip('/').split('/')[-1].startswith('+'):
        buttons.append([InlineKeyboardButton(text="👥 Join Group", url=GROUP_LINK)])
    buttons.append([InlineKeyboardButton(text="🔄 Try Again", callback_data="check_membership_retry")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)