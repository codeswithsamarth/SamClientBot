import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# TELEGRAM
# ==========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ADMIN_IDS = [7943742895, 1316577060]

CHANNEL_LINK = "https://t.me/RainOrdersGroup"
GROUP_LINK = "https://t.me/RainStockGroup"
TOS_LINK = "https://your-site.com/tos"

GROUP_ID = -1004310831686
GROUP_NOTIFICATIONS = True

# ==========================================================
# STOCK ALERTS
# ==========================================================
STOCK_GROUP_ID = -1004396081675
STOCK_NOTIFICATIONS = True
STOCK_ALERT_THRESHOLDS = [40, 70, 90]

# ==========================================================
# MySQL / TiDB Cloud
# ==========================================================
MYSQL_HOST = os.getenv("MYSQL_HOST", "")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "4000"))
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "telegram_shop")
MYSQL_SSL_CA = os.getenv("MYSQL_SSL_CA", "ca.pem")

# ==========================================================
# DEPOSIT NETWORKS
# ==========================================================
BEP20_ADDRESS = "0x573ff2eec518828332efce4267e585932c21f867"
POLYGON_ADDRESS = "0x573ff2eec518828332efce4267e585932c21f867"
BINANCE_DEPOSIT_COINS = ["USDT", "BUSD", "USDC"]

USDT_CONTRACT_BEP20 = "0x55d398326f99059ff775485246999027b3197955"
BUSD_CONTRACT_BEP20 = "0xe9e7cea3dedca5984780bafc599bd69add087d56"
USDC_CONTRACT_BEP20 = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
USDT_CONTRACT_POLYGON = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
USDC_CONTRACT_POLYGON = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
REQUIRE_CONTRACT_MATCH = True

BINANCE_CONFIRMATION_MAP = {"BEP20": 5, "POLYGON": 5}
MIN_BLOCK_CONFIRMATIONS = 5
FINALITY_CONFIRMATIONS = {"BEP20": 15, "POLYGON": 60}
BINANCE_NETWORK_MAP = {"BEP20": "BSC", "POLYGON": "MATIC"}

# ==========================================================
# BINANCE API
# ==========================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

BINANCE_PAY_ID = "45829163"
BINANCE_PAY_LOOKBACK_DAYS = 7

# ==========================================================
# DEPOSIT — UPI
# ==========================================================
UPI_ID = "luquman@fam"
IMAP_HOST = "imap.gmail.com"
IMAP_EMAIL = os.getenv("IMAP_EMAIL", "")
IMAP_APP_PASSWORD = os.getenv("IMAP_APP_PASSWORD", "")
FAMAPP_SENDER_EMAIL = "no-reply@famapp.in"
IMAP_LOOKBACK_DAYS = 1

# ==========================================================
# DEPOSIT — AMOUNT VERIFICATION
# ==========================================================
DEPOSIT_AMOUNT_TOLERANCE = "0.01"
DEPOSIT_ALLOW_OVERPAY = True
DEPOSIT_MAX_CHECK_ATTEMPTS = 60
DEPOSIT_DELETE_FAILED = False
BINANCE_DEPOSIT_LOOKBACK_DAYS = 7
DEPOSIT_BEFORE_TX_GRACE_DAYS = 5
MIN_DEPOSIT_USD = 0.01

# ==========================================================
# EXPLORER API KEYS
# ==========================================================
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
ETHERSCAN_V2_API_KEY = os.getenv("ETHERSCAN_V2_API_KEY", "")

# ==========================================================
# SECURITY LAYER TOGGLES
# ==========================================================
ENABLE_CONTRACT_CHECK = True
ENABLE_BLOCKLIST_CHECK = True
ENABLE_SENDER_BLOCKLIST = True
ENABLE_CONFIRMATION_CHECK = True
ENABLE_FINALITY_CHECK = False
ENABLE_SWAP_LIQUIDITY = False
ENABLE_EXPLORER_CHECK = True
ENABLE_TIMESTAMP_CHECK = True
ENABLE_VALUE_CHECK = True
ENABLE_DUST_CHECK = True

KNOWN_FAKE_CONTRACTS = []
KNOWN_SCAM_SENDERS = []
UPI_USDT_INR_RATE = 95.0