"""
debug_deposit.py

Paste your tx hash and find EXACTLY why it's not being verified.
Shows ALL deposits from Binance and which one matches (if any).
"""

from binance.client import Client
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════
# CONFIGURE
# ═══════════════════════════════════════════════
API_KEY = "3Tk9ccXYHZAyck5AjjsEYIo1P5znIpvGNzTpjYFS2CGCJ1ZxlwQzOd6BJcsXWgoW"
API_SECRET = "gzLELlA8gc3FX1acLkeGTW3K1WDFg4fksmo5TV7AjbOdsYiB01XwxhKNXiNzp28a"

# Your deposit addresses
YOUR_BEP20 = "0xYOUR_BEP20_ADDRESS".lower()
YOUR_POLYGON = "0xYOUR_POLYGON_ADDRESS".lower()

# The tx hash that's failing
TX_HASH = "0x86ee1194a03b5df94c384895d6565b591981e35a036f194a83ea5ffbf407aff7"

# ═══════════════════════════════════════════════

def debug_deposit():
    client = Client(API_KEY, API_SECRET)

    print("╔══════════════════════════════════════════╗")
    print("║   DEPOSIT DEBUGGER                      ║")
    print("╚══════════════════════════════════════════╝")
    print(f"\n🔍 Looking for: {TX_HASH}")

    # Check BOTH networks
    networks = {
        "BSC (BEP20)": "BSC",
        "Polygon (MATIC)": "MATIC",
    }

    found = False

    for name, network in networks.items():
        print(f"\n{'='*50}")
        print(f"📡 Checking {name}...")
        print(f"{'='*50}")

        try:
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = end_time - 7 * 24 * 60 * 60 * 1000

            history = client.get_deposit_history(
                coin="USDT",
                network=network,
                startTime=start_time,
                endTime=end_time,
            )

            if not history:
                print(f"   ❌ No USDT deposits on {name} in last 7 days!")
                print(f"   → Check if deposit was sent to correct network")
                print(f"   → Your address: {YOUR_BEP20 if 'BEP20' in name else YOUR_POLYGON}")
                continue

            print(f"   📊 Found {len(history)} recent deposits")

            # Show ALL recent deposits (helpful for debugging)
            print(f"\n   📋 Last 10 deposits on {name}:")
            print(f"   {'─'*45}")

            for i, dep in enumerate(history[:10]):
                tx = dep.get("txId", "N/A")
                amount = dep.get("amount", "N/A")
                status = dep.get("status", "N/A")
                address = dep.get("address", "N/A")
                confirmations = dep.get("confirmTimes", "N/A")
                insert_time = datetime.fromtimestamp(
                    int(dep.get("insertTime", 0)) / 1000
                ).strftime("%Y-%m-%d %H:%M")

                match = "✅ MATCH!" if tx.lower() == TX_HASH.lower() else ""

                print(f"   {i+1}. {tx[:16]}... | {amount} USDT | {insert_time} | {match}")
                if match:
                    print(f"      → Address: {address}")
                    print(f"      → Status: {status} (1=confirmed)")
                    print(f"      → Confirmations: {confirmations}")
                    print(f"      → Full tx: {tx}")
                    found = True

            # Exact search
            print(f"\n   🔍 Exact search for your tx hash...")
            exact = None
            for dep in history:
                if (dep.get("txId") or "").lower() == TX_HASH.lower():
                    exact = dep
                    break

            if exact:
                print(f"   ✅ FOUND!")
                print(f"   ├─ Amount: {exact.get('amount')} USDT")
                print(f"   ├─ Status: {exact.get('status')}")
                print(f"   ├─ Address: {exact.get('address')}")
                print(f"   ├─ Confirmations: {exact.get('confirmTimes')}")
                print(f"   ├─ Contract: {exact.get('contractAddress', 'N/A')}")
                print(f"   └─ Time: {datetime.fromtimestamp(int(exact.get('insertTime',0))/1000)}")

                # Address check
                expected = YOUR_BEP20 if "BEP20" in name else YOUR_POLYGON
                received = (exact.get("address") or "").lower()

                if expected and received != expected:
                    print(f"\n   ❌ ADDRESS MISMATCH!")
                    print(f"   Expected: {expected}")
                    print(f"   Got:      {received}")
                    print(f"   → Deposit went to WRONG ADDRESS!")
                else:
                    print(f"\n   ✅ Address matches!")

                # Status check
                if exact.get("status") != 1:
                    print(f"\n   ⚠️ Status is NOT confirmed!")
                    print(f"   → Wait for Binance to confirm")
                    print(f"   → Currently: {exact.get('status')}")

                found = True
            else:
                print(f"   ❌ NOT FOUND on {name}")
                print(f"\n   Possible reasons:")
                print(f"   1. Wrong network — sent on different chain")
                print(f"   2. Tx hash typo — check every character")
                print(f"   3. Deposit very recent (< 2 minutes)")
                print(f"   4. Network congestion delaying Binance indexing")
                print(f"   5. Sent to wrong deposit address")

                # Check if ANY tx went to your address
                print(f"\n   🔍 Checking if ANY deposit went to your address...")
                your_addr = YOUR_BEP20 if "BEP20" in name else YOUR_POLYGON
                your_deps = [d for d in history if (d.get("address") or "").lower() == your_addr]

                if your_deps:
                    print(f"   ✅ Found {len(your_deps)} deposits to your address!")
                    for d in your_deps[-3:]:
                        print(f"      → {d.get('txId','')[:20]}... | {d.get('amount')} USDT")
                    print(f"   → Your tx hash might be WRONG — compare with above")
                else:
                    print(f"   ❌ No deposits found to your address on {name}")
                    print(f"   → Check your deposit address in the bot")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    if not found:
        print(f"\n{'='*50}")
        print(f"❌ TX HASH NOT FOUND ON ANY NETWORK")
        print(f"{'='*50}")
        print(f"\nChecklist:")
        print(f"☐ Did you copy the FULL tx hash? (66 chars: 0x + 64 hex)")
        print(f"☐ Are you checking the right network? (BEP20 vs Polygon)")
        print(f"☐ Did you wait 2+ minutes after sending?")
        print(f"☐ Did you send to the correct deposit address?")
        print(f"☐ Check the tx on explorer directly:")
        print(f"   BSC: https://bscscan.com/tx/{TX_HASH}")
        print(f"   Polygon: https://polygonscan.com/tx/{TX_HASH}")


if __name__ == "__main__":
    debug_deposit()