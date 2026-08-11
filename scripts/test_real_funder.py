#!/usr/bin/env python3
"""
Polymarket Real Deposit Wallet Verification Tool
================================================
Tests live order placement using your REAL Polymarket Deposit Wallet Address:
0x3F6605D1909139a6482136Cb61f191EB887aD1A2
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
real_funder = "0x3F6605D1909139a6482136Cb61f191EB887aD1A2"

print("=" * 70)
print("  POLYMARKET REAL DEPOSIT WALLET VERIFICATION TOOL")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Real Deposit Wallet (funder): {real_funder}")

if not private_key:
    print("❌ POLYMARKET_PRIVATE_KEY is required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Step 1: Initialize Level 1 client with REAL funder
print("\n⏳ Initializing Level 1 Auth Client for REAL Deposit Wallet (signature_type=1, POLY_PROXY)...")
l1_client = ClobClient(
    host=host,
    key=private_key,
    chain_id=137,
    signature_type=1,
    funder=real_funder
)

# Step 2: Auto-derive matching L2 API credentials directly from Polymarket server
print("⏳ Auto-deriving matching CLOB HMAC API Credentials from Polymarket server...")
creds = None
try:
    creds = l1_client.create_or_derive_api_key()
    print(f"🎉 API CREDENTIALS DERIVED SUCCESSFULLY!")
    print(f"   API Key    : {creds.api_key}")
    print(f"   API Secret : {creds.api_secret[:12]}...")
    print(f"   Passphrase : {creds.api_passphrase[:12]}...")
except Exception as e:
    print(f"❌ Could not derive API credentials: {e}")
    sys.exit(1)

# Step 3: Test live order placement across signature candidates (1, 2, 3, 0)
dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"
sig_candidates = [1, 2, 3, 0]
successful_sig = None

for st in sig_candidates:
    print(f"\n⏳ Testing live order placement with signature_type={st}...")
    try:
        test_client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            creds=creds,
            signature_type=st,
            funder=real_funder if st in [1, 2, 3] else None
        )
        order_args = OrderArgsV2(price=0.01, size=5.0, side="BUY", token_id=dummy_token)
        signed_order = test_client.create_order(order_args)
        resp = test_client.post_order(signed_order, OrderType.GTC)
        print(f"🎉 LIVE ORDER POST SUCCESSFUL FOR signature_type={st}! Response: {resp}")
        if isinstance(resp, dict) and "orderID" in resp:
            cancel_resp = test_client.cancel_order(resp["orderID"])
            print(f"   Cancelled test order: {cancel_resp}")
        successful_sig = st
        break
    except Exception as e:
        print(f"❌ Order post failed for signature_type={st}: {e}")

if successful_sig is not None:
    print("\n" + "=" * 70)
    print(f"🎉 EUREKA! VERIFIED WORKING CONFIGURATION FOUND!")
    print(f"   Signature Type : {successful_sig}")
    print(f"   API Key        : {creds.api_key}")
    print(f"   Deposit Wallet : {real_funder}")
    print("=" * 70)

    # Auto-update .env file
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
            elif stripped.startswith("POLYMARKET_API_KEY="):
                new_lines.append(f'POLYMARKET_API_KEY="{creds.api_key}"\n')
                updated_keys.add("POLYMARKET_API_KEY")
            elif stripped.startswith("POLYMARKET_SECRET="):
                new_lines.append(f'POLYMARKET_SECRET="{creds.api_secret}"\n')
                updated_keys.add("POLYMARKET_SECRET")
            elif stripped.startswith("POLYMARKET_PASSPHRASE="):
                new_lines.append(f'POLYMARKET_PASSPHRASE="{creds.api_passphrase}"\n')
                updated_keys.add("POLYMARKET_PASSPHRASE")
            elif stripped.startswith("POLYMARKET_SIGNATURE_TYPE="):
                new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{successful_sig}"\n')
                updated_keys.add("POLYMARKET_SIGNATURE_TYPE")
            else:
                new_lines.append(line)

        if "POLYMARKET_FUNDER" not in updated_keys:
            new_lines.append(f'POLYMARKET_FUNDER="{real_funder}"\n')
        if "POLYMARKET_API_KEY" not in updated_keys:
            new_lines.append(f'POLYMARKET_API_KEY="{creds.api_key}"\n')
        if "POLYMARKET_SECRET" not in updated_keys:
            new_lines.append(f'POLYMARKET_SECRET="{creds.api_secret}"\n')
        if "POLYMARKET_PASSPHRASE" not in updated_keys:
            new_lines.append(f'POLYMARKET_PASSPHRASE="{creds.api_passphrase}"\n')
        if "POLYMARKET_SIGNATURE_TYPE" not in updated_keys:
            new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{successful_sig}"\n')

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        print(f"✅ .env file successfully updated with REAL Deposit Wallet credentials!")
        print(f"   Saved at: {env_path}")
        print("=" * 70)
