#!/usr/bin/env python3
"""
Polymarket CLOB Signature Type Diagnostic Script (V2)
=====================================================
Tests fresh API key derivation for signature_type = 3 (POLY_1271) to bind API key
directly to Deposit Wallet address (0xD7cac568...), resolving signer == api_key match!
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

print("=" * 70)
print("  POLYMARKET CLOB SIGNATURE TYPE DIAGNOSTIC TEST (V2)")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit Wallet (funder): {funder}")

if not private_key:
    sys.exit(1)

host = "https://clob.polymarket.com"

# Dummy order details ($0.01 price, won't fill, immediately cancelled)
dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"
dummy_price = 0.01
dummy_size = 5.0

sig_types = [3, 1, 2, 0]

for st in sig_types:
    print(f"\n----------------------------------------------------------------------")
    print(f"🧪 Testing signature_type={st} with FRESH derived API keys...")
    try:
        # Step 1: Create client for L1 Auth
        client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            signature_type=st,
            funder=funder if st in [1, 2, 3] else None
        )
        print(f"  Deriving fresh API key for signature_type={st} and funder={funder}...")
        fresh_creds = client.create_or_derive_api_key()
        print(f"  Derived API Key: {fresh_creds.api_key[:8]}...")
        client.set_api_creds(fresh_creds)

        # Step 2: Post test order
        order_args = OrderArgsV2(price=dummy_price, size=dummy_size, side="BUY", token_id=dummy_token)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        print(f"🎉 SUCCESS for signature_type={st}! Order response: {resp}")
        
        if isinstance(resp, dict) and "orderID" in resp:
            cancel_resp = client.cancel_order(resp["orderID"])
            print(f"  Cancelled test order: {cancel_resp}")

        print(f"\n✅ YOUR WALLET REQUIRES signature_type={st}!")
        print(f"   Derived API Key to save to .env:")
        print(f'   POLYMARKET_API_KEY="{fresh_creds.api_key}"')
        print(f'   POLYMARKET_SECRET="{fresh_creds.api_secret}"')
        print(f'   POLYMARKET_PASSPHRASE="{fresh_creds.api_passphrase}"')
        break
    except Exception as e:
        print(f"❌ Failed for signature_type={st}: {e}")

print("=" * 70)
