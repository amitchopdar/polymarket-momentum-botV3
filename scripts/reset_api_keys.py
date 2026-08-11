#!/usr/bin/env python3
"""
Polymarket CLOB API Key Reset & Derivation Tool
===============================================
1. Deletes old EOA-bound API key on Polymarket servers.
2. Creates a brand new API key bound directly to your Deposit Wallet (signature_type=3).
3. Auto-updates .env file with the fresh credentials.
"""

import os
import sys

# Auto-load .env
_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"' "))

from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType

private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip("\"' ")
funder = os.getenv("POLYMARKET_FUNDER", "").strip("\"' ") or None
api_key = os.getenv("POLYMARKET_API_KEY", "").strip("\"' ")
secret = os.getenv("POLYMARKET_SECRET", "").strip("\"' ")
passphrase = os.getenv("POLYMARKET_PASSPHRASE", "").strip("\"' ")

print("=" * 70)
print("  POLYMARKET CLOB API KEY RESET & DERIVATION TOOL")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit Wallet (funder): {funder}")
print(f"🔑 Existing API Key: {api_key[:8]}..." if api_key else "❌ Existing API Key missing!")

if not private_key or not funder:
    print("❌ Both POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER are required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Step 1: Initialize client with existing credentials to delete old API key if possible
if api_key and secret and passphrase:
    print("\n⏳ Attempting to revoke/delete old EOA-bound API key from Polymarket server...")
    try:
        old_creds = ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)
        del_client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            creds=old_creds,
            signature_type=3,
            funder=funder
        )
        del_resp = del_client.delete_api_key()
        print(f"✅ Successfully deleted old API key! Response: {del_resp}")
    except Exception as e:
        print(f"⚠ Delete API key notice (can be ignored): {e}")

# Step 2: Initialize Level 1 client for signature_type=3 + Deposit Wallet
print("\n⏳ Initializing Level 1 Auth Client (signature_type=3, POLY_1271)...")
l1_client = ClobClient(
    host=host,
    key=private_key,
    chain_id=137,
    signature_type=3,
    funder=funder
)

# Step 3: Create brand new API key
print("⏳ Creating brand new API key registered directly to Deposit Wallet...")
try:
    fresh_creds = l1_client.create_api_key()
    print(f"🎉 BRAND NEW API KEY CREATED SUCCESSFULLY!")
    print(f"   API Key: {fresh_creds.api_key[:12]}...")
except Exception as e1:
    print(f"⚠ create_api_key notice: {e1}")
    print("⏳ Fallback to derive_api_key...")
    try:
        fresh_creds = l1_client.derive_api_key()
        print(f"   Derived API Key: {fresh_creds.api_key[:12]}...")
    except Exception as e2:
        print(f"❌ Could not create or derive API key: {e2}")
        sys.exit(1)

# Step 4: Test live order placement with fresh credentials
print("\n⏳ Testing live order placement with fresh API key (signature_type=3)...")
try:
    test_client = ClobClient(
        host=host,
        key=private_key,
        chain_id=137,
        creds=fresh_creds,
        signature_type=3,
        funder=funder
    )
    dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"
    order_args = OrderArgsV2(price=0.01, size=5.0, side="BUY", token_id=dummy_token)
    signed_order = test_client.create_order(order_args)
    resp = test_client.post_order(signed_order, OrderType.GTC)
    print(f"🎉 LIVE ORDER POST SUCCESSFUL! Order response: {resp}")
    if isinstance(resp, dict) and "orderID" in resp:
        cancel_resp = test_client.cancel_order(resp["orderID"])
        print(f"   Cancelled test order: {cancel_resp}")
except Exception as e:
    print(f"❌ Test order failed: {e}")
    sys.exit(1)

# Step 5: Auto-update .env file
print("\n" + "=" * 70)
print("  UPDATING .ENV FILE WITH FRESH DEPOSIT-WALLET-BOUND CREDENTIALS")
print("=" * 70)

env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("POLYMARKET_API_KEY="):
            new_lines.append(f'POLYMARKET_API_KEY="{fresh_creds.api_key}"\n')
            updated_keys.add("POLYMARKET_API_KEY")
        elif stripped.startswith("POLYMARKET_SECRET="):
            new_lines.append(f'POLYMARKET_SECRET="{fresh_creds.api_secret}"\n')
            updated_keys.add("POLYMARKET_SECRET")
        elif stripped.startswith("POLYMARKET_PASSPHRASE="):
            new_lines.append(f'POLYMARKET_PASSPHRASE="{fresh_creds.api_passphrase}"\n')
            updated_keys.add("POLYMARKET_PASSPHRASE")
        else:
            new_lines.append(line)

    if "POLYMARKET_API_KEY" not in updated_keys:
        new_lines.append(f'POLYMARKET_API_KEY="{fresh_creds.api_key}"\n')
    if "POLYMARKET_SECRET" not in updated_keys:
        new_lines.append(f'POLYMARKET_SECRET="{fresh_creds.api_secret}"\n')
    if "POLYMARKET_PASSPHRASE" not in updated_keys:
        new_lines.append(f'POLYMARKET_PASSPHRASE="{fresh_creds.api_passphrase}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"✅ .env file successfully updated with fresh Deposit Wallet API keys!")
    print(f"   Saved at: {env_path}")
    print("=" * 70)
