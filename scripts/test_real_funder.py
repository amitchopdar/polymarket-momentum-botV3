#!/usr/bin/env python3
"""
Polymarket Live Token Verification Tool (signature_type=3 POLY_1271)
===================================================================
Fetches an ACTIVE live Polymarket token ID from Gamma API and tests live order placement
with your REAL Deposit Wallet (0x3F6605D1909139a6482136Cb61f191EB887aD1A2) under signature_type=3.
"""

import os
import sys
import json
import requests

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
real_funder = "0x3F6605D1909139a6482136Cb61f191EB887aD1A2"

print("=" * 70)
print("  POLYMARKET LIVE TOKEN VERIFICATION TOOL (signature_type=3)")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Real Deposit Wallet (funder): {real_funder}")

if not private_key:
    print("❌ POLYMARKET_PRIVATE_KEY is required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Step 1: Fetch active live token ID from Polymarket Gamma API
print("\n⏳ Fetching active live market token ID from Polymarket Gamma API...")
active_token_id = None
try:
    resp = requests.get("https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=10", timeout=10)
    data = resp.json()
    for m in data:
        tokens_raw = m.get("clobTokenIds")
        if tokens_raw:
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            if tokens and len(tokens) > 0:
                active_token_id = str(tokens[0]).strip("\"' ")
                print(f"✅ Found active live market token ID: {active_token_id[:25]}...")
                break
except Exception as e:
    print(f"⚠ Could not fetch live token from Gamma API: {e}")

if not active_token_id:
    active_token_id = "32338220190071351435772801779725302244575775216413325951443816017994629993401"

# Step 2: Initialize Level 1 client with REAL funder and signature_type=3 (POLY_1271)
print("\n⏳ Initializing Level 1 Auth Client (signature_type=3, POLY_1271)...")
l1_client = ClobClient(
    host=host,
    key=private_key,
    chain_id=137,
    signature_type=3,
    funder=real_funder
)

# Step 3: Derive L2 credentials
print("⏳ Deriving L2 API credentials from Polymarket server...")
creds = None
try:
    creds = l1_client.create_or_derive_api_key()
    print(f"🎉 API CREDENTIALS DERIVED SUCCESSFULLY!")
    print(f"   API Key    : {creds.api_key}")
except Exception as e:
    print(f"❌ Could not derive API credentials: {e}")
    sys.exit(1)

# Step 4: Test live order placement with signature_type=3 (POLY_1271)
print(f"\n⏳ Testing live order placement with signature_type=3 (POLY_1271) on token {active_token_id[:20]}...")
try:
    test_client = ClobClient(
        host=host,
        key=private_key,
        chain_id=137,
        creds=creds,
        signature_type=3,
        funder=real_funder
    )
    # Price $0.01 (extremely low, will not fill), size 5 shares ($0.05 spend limit)
    order_args = OrderArgsV2(price=0.01, size=5.0, side="BUY", token_id=active_token_id)
    signed_order = test_client.create_order(order_args)
    resp = test_client.post_order(signed_order, OrderType.GTC)
    print(f"\n" + "=" * 70)
    print(f"🎉🎉🎉 SUCCESS! LIVE ORDER POSTED 200 OK! 🎉🎉🎉")
    print(f"Order Response: {resp}")
    print("=" * 70)
    
    if isinstance(resp, dict) and "orderID" in resp:
        cancel_resp = test_client.cancel_order(resp["orderID"])
        print(f"✅ Successfully cancelled test order: {cancel_resp}")
        
    # Auto-update .env file with verified working configuration
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("POLYMARKET_FUNDER="):
                new_lines.append(f'POLYMARKET_FUNDER="{real_funder}"\n')
                updated_keys.add("POLYMARKET_FUNDER")
            elif stripped.startswith("POLYMARKET_SIGNATURE_TYPE="):
                new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="3"\n')
                updated_keys.add("POLYMARKET_SIGNATURE_TYPE")
            elif stripped.startswith("POLYMARKET_API_KEY="):
                new_lines.append(f'POLYMARKET_API_KEY="{creds.api_key}"\n')
                updated_keys.add("POLYMARKET_API_KEY")
            elif stripped.startswith("POLYMARKET_SECRET="):
                new_lines.append(f'POLYMARKET_SECRET="{creds.api_secret}"\n')
                updated_keys.add("POLYMARKET_SECRET")
            elif stripped.startswith("POLYMARKET_PASSPHRASE="):
                new_lines.append(f'POLYMARKET_PASSPHRASE="{creds.api_passphrase}"\n')
                updated_keys.add("POLYMARKET_PASSPHRASE")
            else:
                new_lines.append(line)

        if "POLYMARKET_FUNDER" not in updated_keys:
            new_lines.append(f'POLYMARKET_FUNDER="{real_funder}"\n')
        if "POLYMARKET_SIGNATURE_TYPE" not in updated_keys:
            new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="3"\n')
        if "POLYMARKET_API_KEY" not in updated_keys:
            new_lines.append(f'POLYMARKET_API_KEY="{creds.api_key}"\n')
        if "POLYMARKET_SECRET" not in updated_keys:
            new_lines.append(f'POLYMARKET_SECRET="{creds.api_secret}"\n')
        if "POLYMARKET_PASSPHRASE" not in updated_keys:
            new_lines.append(f'POLYMARKET_PASSPHRASE="{creds.api_passphrase}"\n')

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        print(f"✅ .env file successfully updated with signature_type=3 and real funder!")
        print(f"   Saved at: {env_path}")
        print("=" * 70)
except Exception as e:
    print(f"\n❌ Live order post failed: {e}")
    sys.exit(1)
