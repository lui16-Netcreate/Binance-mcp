"""
Crypto Auto-Trader — First-Time Setup

Walks you through entering all required API keys,
validates each one, and writes your .env file.

Usage:
    python setup.py
"""
import os
import sys
import requests
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"

# ── Helpers ───────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"

def header(text: str):
    width = 60
    print()
    print(CYAN + "━" * width + RESET)
    print(BOLD + f"  {text}" + RESET)
    print(CYAN + "━" * width + RESET)

def success(text: str):
    print(GREEN + f"  ✓ {text}" + RESET)

def warn(text: str):
    print(YELLOW + f"  ⚠ {text}" + RESET)

def error(text: str):
    print(RED + f"  ✗ {text}" + RESET)

def info(text: str):
    print(f"  {text}")

def prompt(label: str, secret: bool = False) -> str:
    value = ""
    while not value.strip():
        value = input(BOLD + f"\n  → {label}: " + RESET).strip()
        if not value:
            warn("This field is required.")
    return value

def confirm(label: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    ans = input(f"\n  → {label} [{default_str}]: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


# ── Validators ────────────────────────────────────────────────────────────────

def validate_telegram_bot(token: str, chat_id: str) -> bool:
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if not r.ok or not r.json().get("ok"):
            return False
        # Send a test message
        r2 = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Crypto Auto-Trader setup complete! Your bot is connected."},
            timeout=10,
        )
        return r2.ok
    except Exception:
        return False


def validate_binance(api_key: str, api_secret: str) -> tuple[bool, str]:
    try:
        from binance.client import Client
        from binance.exceptions import BinanceAPIException
        client = Client(api_key, api_secret, tld="us")
        client.ping()
        account = client.get_account()
        perms = account.get("permissions", [])
        if "SPOT" not in perms:
            return False, "Spot trading permission not enabled on this API key"
        balance = next((float(a["free"]) for a in account["balances"] if a["asset"] == "USDT"), 0)
        return True, f"USDT balance: ${balance:,.2f}"
    except Exception as e:
        return False, str(e)


def validate_anthropic(api_key: str) -> bool:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
        return True
    except Exception:
        return False


# ── Setup sections ────────────────────────────────────────────────────────────

def setup_telegram_bot() -> tuple[str, str]:
    header("Step 1 — Telegram Bot (for trade notifications)")
    info("Your bot sends you trade confirmations and alerts.")
    info("")
    info("How to create a Telegram bot:")
    info("  1. Open Telegram and search for @BotFather")
    info("  2. Send /newbot")
    info("  3. Choose a name and username for your bot")
    info("  4. BotFather will give you a token — copy it here")
    info("")
    info("How to get your Chat ID:")
    info("  1. Send any message to your new bot")
    info("  2. Open this URL in your browser:")
    info("     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates")
    info("  3. Find 'chat' → 'id' in the response")

    while True:
        token   = prompt("Telegram Bot Token (e.g. 123456:ABCdef...)")
        chat_id = prompt("Your Telegram Chat ID (e.g. 5563181813)")

        info("\n  Validating and sending a test message...")
        if validate_telegram_bot(token, chat_id):
            success("Bot connected! Check your Telegram — you should have a test message.")
            return token, chat_id
        else:
            error("Could not connect. Double-check the token and chat ID.")
            if not confirm("Try again?"):
                warn("Skipping validation — saving as entered.")
                return token, chat_id


def setup_telegram_api() -> tuple[str, str, str]:
    header("Step 2 — Telegram API (to read your signal group)")
    info("This lets the bot read messages from your private signal group.")
    info("")
    info("How to get your Telegram API credentials:")
    info("  1. Go to https://my.telegram.org")
    info("  2. Log in with your phone number")
    info("  3. Click 'API Development Tools'")
    info("  4. Create an app (name: anything, e.g. 'MyTrader')")
    info("     → App title must be at least 5 characters")
    info("     → Short name: letters/numbers only, e.g. 'mytrader'")
    info("     → Platform: Other")
    info("  5. Copy the App api_id and App api_hash below")

    api_id   = prompt("Telegram API ID (number, e.g. 12345678)")
    api_hash = prompt("Telegram API Hash (hex string)")
    phone    = prompt("Your phone number with country code (e.g. +12025550123)")

    success("Telegram API credentials saved.")
    info("Note: On first run of trader.py you'll receive a login code in Telegram.")
    return api_id, api_hash, phone


def setup_signal_group() -> str:
    header("Step 3 — Signal Group Name")
    info("Enter the exact name (or partial name) of your trading signal group.")
    info("The bot will listen to any group whose title contains this text.")
    info("")
    info("Example: 'Crypto Chiefs - Premium'")

    group = prompt("Signal group name")
    success(f"Will listen to groups matching: '{group}'")
    return group


def setup_binance() -> tuple[str, str]:
    header("Step 4 — Binance US API Keys")
    info("These allow the bot to place orders on your behalf.")
    info("")
    info("How to create Binance US API keys:")
    info("  1. Log in at https://binance.us")
    info("  2. Click your profile icon → API Management")
    info("  3. Click 'Create API' → System generated")
    info("  4. Label it anything (e.g. 'autotrader')")
    info("  5. Complete 2FA verification")
    info("  6. Enable 'Enable Spot & Margin Trading'")
    info("     ⚠ Do NOT enable withdrawals")
    info("  7. Whitelist your server IP or set to unrestricted")
    info("  8. Copy the API Key and Secret Key below")
    info("     ⚠ The secret is only shown once — save it immediately")

    while True:
        api_key    = prompt("Binance API Key")
        api_secret = prompt("Binance API Secret")

        info("\n  Validating Binance connection...")
        ok, detail = validate_binance(api_key, api_secret)
        if ok:
            success(f"Binance US connected. {detail}")
            return api_key, api_secret
        else:
            error(f"Binance validation failed: {detail}")
            if not confirm("Try again?"):
                warn("Skipping validation — saving as entered.")
                return api_key, api_secret


def setup_anthropic() -> str:
    header("Step 5 — Anthropic API Key (for weekly trade analysis)")
    info("Used by the weekly report to analyze your trading patterns with Claude.")
    info("")
    info("How to get your Anthropic API key:")
    info("  1. Go to https://console.anthropic.com")
    info("  2. Sign up or log in")
    info("  3. Go to API Keys → Create Key")
    info("  4. Copy the key below (starts with sk-ant-...)")

    while True:
        api_key = prompt("Anthropic API Key")

        info("\n  Validating Anthropic connection...")
        if validate_anthropic(api_key):
            success("Anthropic API connected.")
            return api_key
        else:
            error("Could not connect. Check the key and try again.")
            if not confirm("Try again?"):
                warn("Skipping validation — saving as entered.")
                return api_key


def setup_position_size() -> tuple[str, str]:
    header("Step 6 — Position Sizing")
    info("How much USDT to spend per entry order.")
    info("Each signal places up to 3 orders, so total per signal = amount × 3.")
    info("")
    info("Binance US minimum per order: $10")
    info("Example: $11/order × 3 = $33 max per signal")

    while True:
        amount = prompt("Fixed dollar amount per order (e.g. 11)")
        try:
            val = float(amount)
            if val < 10:
                warn("Binance US requires at least $10 per order. Enter $10 or more.")
                continue
            success(f"${val:.0f} per order — max ${val*3:.0f} per signal (3 entries).")
            break
        except ValueError:
            warn("Enter a number (e.g. 11)")

    info("")
    info("Stop-loss is placed automatically when an entry order fills.")
    info("It triggers when your loss reaches a set % of the order amount.")
    info(f"Example: 0.50 = stop out at 50% loss (${val*0.5:.2f} on a ${val:.0f} order)")

    while True:
        pct = prompt("Max loss per order as decimal (e.g. 0.50 for 50%)")
        try:
            pct_val = float(pct)
            if not 0.01 <= pct_val <= 1.0:
                warn("Enter a value between 0.01 and 1.00.")
                continue
            success(f"SL triggers at {pct_val*100:.0f}% loss — max ${val*pct_val:.2f} per order.")
            return str(val), str(pct_val)
        except ValueError:
            warn("Enter a decimal number (e.g. 0.50)")


def setup_dashboard() -> tuple[str, str]:
    header("Step 6 — Dashboard Password")
    info("Protects your trading dashboard from public access.")
    info("Choose a username and password — you'll use these to log in.")

    username = prompt("Dashboard username (e.g. admin)")
    password = prompt("Dashboard password")
    success("Dashboard credentials saved.")
    return username, password


# ── Write .env ────────────────────────────────────────────────────────────────

def write_env(values: dict):
    content = f"""# Crypto Auto-Trader — generated by setup.py

# Telegram Bot (notifications)
TELEGRAM_BOT_TOKEN={values['bot_token']}
TELEGRAM_CHAT_ID={values['chat_id']}

# Telegram API (signal group reader) — from my.telegram.org
TELEGRAM_API_ID={values['api_id']}
TELEGRAM_API_HASH={values['api_hash']}
TELEGRAM_PHONE={values['phone']}

# Signal group name (partial match)
SIGNAL_GROUP_NAME={values['group']}

# Binance US API keys
BINANCE_API_KEY={values['binance_key']}
BINANCE_API_SECRET={values['binance_secret']}

# Anthropic API key (weekly reports)
ANTHROPIC_API_KEY={values['anthropic_key']}

# Position sizing — fixed dollar amount per entry order
ORDER_SIZE_USD={values['order_size']}

# Stop-loss as fraction of order value lost (0.50 = stop out at 50% loss)
SL_LOSS_PCT={values['sl_loss_pct']}

# Dashboard credentials
DASHBOARD_USER={values['dash_user']}
DASHBOARD_PASS={values['dash_pass']}
"""
    ENV_PATH.write_text(content)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print(BOLD + CYAN + "  ╔══════════════════════════════════════════╗" + RESET)
    print(BOLD + CYAN + "  ║      CRYPTO AUTO-TRADER — SETUP          ║" + RESET)
    print(BOLD + CYAN + "  ╚══════════════════════════════════════════╝" + RESET)
    print()
    info("This wizard will collect your API keys and configure the system.")
    info("Your keys are stored ONLY in the local .env file — never transmitted.")

    if ENV_PATH.exists():
        print()
        warn(".env file already exists.")
        if not confirm("Overwrite it with new settings?", default=False):
            print()
            info("Setup cancelled. Your existing .env was not changed.")
            sys.exit(0)

    # Collect all values
    bot_token, chat_id     = setup_telegram_bot()
    api_id, api_hash, phone = setup_telegram_api()
    group                  = setup_signal_group()
    binance_key, binance_secret = setup_binance()
    anthropic_key          = setup_anthropic()
    order_size, sl_loss_pct = setup_position_size()
    dash_user, dash_pass   = setup_dashboard()

    # Write .env
    write_env({
        "bot_token":      bot_token,
        "chat_id":        chat_id,
        "api_id":         api_id,
        "api_hash":       api_hash,
        "phone":          phone,
        "group":          group,
        "binance_key":    binance_key,
        "binance_secret": binance_secret,
        "anthropic_key":  anthropic_key,
        "order_size":     order_size,
        "sl_loss_pct":    sl_loss_pct,
        "dash_user":      dash_user,
        "dash_pass":      dash_pass,
    })

    header("Setup Complete!")
    success(".env file created successfully.")
    info("")
    info("Next steps:")
    info("  1. Start the trader:       python trader.py")
    info("  2. Start the fill monitor: python fill_monitor.py")
    info("  3. Start the dashboard:    python dashboard.py")
    info("  4. Weekly reports:         python report.py")
    info("")
    info("  Tip: Use 'screen' to keep processes running 24/7:")
    info("    screen -S trader && python trader.py  (then Ctrl+A D to detach)")
    print()


if __name__ == "__main__":
    main()
