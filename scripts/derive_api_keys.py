#!/usr/bin/env python3
"""
Polymarket CLOB API Key Derivation Script
==========================================
Derives your API Key, Secret, and Passphrase from your wallet private key.
These credentials are required for live trading on Polymarket's CLOB.

Usage:
    python scripts/derive_api_keys.py
"""

import os
import sys

# Auto-load .env from project root
_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"' "))

def main():
    print("=" * 70)
    print("  POLYMARKET CLOB API KEY DERIVATION TOOL")
    print("=" * 70)

    # 1. Read private key
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip("\"' ")
    if not private_key:
        private_key = input("\n🔑 Enter your Polygon wallet PRIVATE KEY (0x...): ").strip()
    else:
        print(f"\n🔑 Using POLYMARKET_PRIVATE_KEY from .env (0x{private_key[2:6]}...)")

    if not private_key:
        print("❌ No private key provided. Exiting.")
        sys.exit(1)

    # 2. Read funder (deposit wallet)
    funder = os.getenv("POLYMARKET_FUNDER", "").strip("\"' ") or None
    if funder:
        print(f"📦 Deposit Wallet (funder) from .env: {funder[:10]}...{funder[-6:]}")
    else:
        funder_input = input("\n📦 Enter your Polymarket DEPOSIT WALLET address (0x...), or press Enter to skip (EOA mode): ").strip()
        funder = funder_input if funder_input else None

    # 3. Determine signature type
    if funder:
        sig_type = 3  # POLY_1271 (Deposit Wallet)
        print(f"\n🔐 Mode: Deposit Wallet (signature_type=3, POLY_1271)")
    else:
        sig_type = 0  # EOA (Direct Wallet)
        print(f"\n🔐 Mode: EOA Direct Wallet (signature_type=0)")

    # 4. Initialize CLOB client
    try:
        try:
            from py_clob_client_v2 import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds
            sdk_name = "py_clob_client_v2"
        except ImportError:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            sdk_name = "py_clob_client"

        print(f"📦 SDK: {sdk_name}")
    except ImportError:
        print("❌ py_clob_client SDK not installed. Run: pip install py-clob-client-v2")
        sys.exit(1)

    host = "https://clob.polymarket.com"
    print(f"\n⏳ Connecting to Polymarket CLOB API ({host})...")

    client = ClobClient(
        host=host,
        key=private_key,
        chain_id=137,
        signature_type=sig_type,
        funder=funder
    )

    # 5. Derive API credentials
    print("⏳ Deriving API credentials (this signs an L1 auth message with your private key)...\n")
    try:
        creds = client.create_or_derive_api_key()
    except Exception as e1:
        print(f"⚠  create_or_derive_api_key failed: {e1}")
        print("⏳ Trying derive_api_key fallback...")
        try:
            creds = client.derive_api_key()
        except Exception as e2:
            print(f"❌ derive_api_key also failed: {e2}")
            print("\nThis usually means your private key doesn't have a registered account on Polymarket.")
            print("Make sure you have logged into https://polymarket.com with this wallet first.")
            sys.exit(1)

    api_key = creds.api_key
    api_secret = creds.api_secret
    api_passphrase = creds.api_passphrase

    print("✅ API CREDENTIALS DERIVED SUCCESSFULLY!\n")

    # 6. Set credentials on client and verify
    client.set_api_creds(creds)
    print("⏳ Verifying credentials by querying CLOB server time...")
    try:
        import requests
        resp = requests.get(f"{host}/time", timeout=5)
        print(f"✅ CLOB Server Time: {resp.text.strip()}\n")
    except Exception:
        print("⚠  Could not verify server time (non-critical)\n")

    # 7. Try to get open orders to verify full auth works
    print("⏳ Verifying full L2 authentication (querying open orders)...")
    try:
        open_orders = client.get_open_orders()
        if isinstance(open_orders, list):
            print(f"✅ L2 Auth verified! You have {len(open_orders)} open order(s).\n")
        else:
            print(f"✅ L2 Auth response received: {str(open_orders)[:100]}\n")
    except Exception as e:
        print(f"⚠  L2 Auth verification: {e}")
        print("   (This may still work for order posting — credentials are derived correctly)\n")

    # 8. Output credentials
    print("=" * 70)
    print("  YOUR POLYMARKET API CREDENTIALS")
    print("  Copy these into your .env file")
    print("=" * 70)
    print(f"""
POLYMARKET_API_KEY="{api_key}"
POLYMARKET_SECRET="{api_secret}"
POLYMARKET_PASSPHRASE="{api_passphrase}"
""")

    if funder:
        print(f'POLYMARKET_FUNDER="{funder}"')
        print(f'# signature_type=3 (Deposit Wallet / POLY_1271)')
    else:
        print(f'# signature_type=0 (EOA Direct Wallet)')

    print("\n" + "=" * 70)

    # 9. Offer to auto-update .env
    update = input("\n📝 Auto-update your .env file with these credentials? (y/N): ").strip().lower()
    if update == "y":
        _update_env_file(api_key, api_secret, api_passphrase)


def _update_env_file(api_key: str, secret: str, passphrase: str):
    """Updates .env file with the derived API credentials."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        print(f"❌ .env file not found at {env_path}")
        return

    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("POLYMARKET_API_KEY="):
            new_lines.append(f'POLYMARKET_API_KEY="{api_key}"\n')
            updated_keys.add("POLYMARKET_API_KEY")
        elif stripped.startswith("POLYMARKET_SECRET="):
            new_lines.append(f'POLYMARKET_SECRET="{secret}"\n')
            updated_keys.add("POLYMARKET_SECRET")
        elif stripped.startswith("POLYMARKET_PASSPHRASE="):
            new_lines.append(f'POLYMARKET_PASSPHRASE="{passphrase}"\n')
            updated_keys.add("POLYMARKET_PASSPHRASE")
        else:
            new_lines.append(line)

    # Append any missing keys
    if "POLYMARKET_API_KEY" not in updated_keys:
        new_lines.append(f'POLYMARKET_API_KEY="{api_key}"\n')
    if "POLYMARKET_SECRET" not in updated_keys:
        new_lines.append(f'POLYMARKET_SECRET="{secret}"\n')
    if "POLYMARKET_PASSPHRASE" not in updated_keys:
        new_lines.append(f'POLYMARKET_PASSPHRASE="{passphrase}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"✅ .env file updated at: {env_path}")
    print("   You can now restart the bot with: python main.py")


if __name__ == "__main__":
    main()
