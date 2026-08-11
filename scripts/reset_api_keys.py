#!/usr/bin/env python3
"""
Polymarket CLOB API Key Reset & Derivation Tool (V2)
===============================================
1. Creates/Derives API keys with Level 1 Auth.
2. Tests signature_type = 1 (POLY_PROXY) and signature_type = 2 (POLY_GNOSIS_SAFE).
   (Both set signer = EOA (matches API Key) and maker = funder (Deposit Wallet)).
3. Auto-updates .env file upon SUCCESS.
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
print("  POLYMARKET CLOB API KEY RESET & DERIVATION TOOL (V2)")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit Wallet (funder): {funder}")
print(f"🔑 Existing API Key: {api_key[:8]}..." if api_key else "❌ Existing API Key missing!")

if not private_key or not funder:
    print("❌ Both POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER are required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Step 1: Initialize Level 1 client
print("\n⏳ Initializing Level 1 Auth Client (signature_type=1, POLY_PROXY)...")
l1_client = ClobClient(
    host=host,
    key=private_key,
    chain_id=137,
    signature_type=1,
    funder=funder
)

# Step 2: Derive/Create API key
print("⏳ Deriving API key bound to your wallet...")
try:
    fresh_creds = l1_client.create_or_derive_api_key()
    print(f"🎉 API KEY RESOLVED SUCCESSFULLY!")
    print(f"   API Key: {fresh_creds.api_key[:12]}...")
except Exception as e:
    print(f"❌ API key resolution failed: {e}")
    sys.exit(1)

# Step 3: Test live order placement with signature_type=1 and signature_type=2
sig_candidates = [1, 2, 0, 3]
successful_sig = None

dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"

for st in sig_candidates:
    print(f"\n⏳ Testing live order placement with signature_type={st}...")
    try:
        test_client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            creds=fresh_creds,
            signature_type=st,
            funder=funder if st in [1, 2, 3] else None
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

if not successful_sig:
    print("\n❌ Could not find a working signature type for order placement.")
    sys.exit(1)

# Step 4: Auto-update .env file
print("\n" + "=" * 70)
print("  UPDATING .ENV FILE WITH WORKING CREDENTIALS & SIGNATURE TYPE")
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
        elif stripped.startswith("POLYMARKET_SIGNATURE_TYPE="):
            new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{successful_sig}"\n')
            updated_keys.add("POLYMARKET_SIGNATURE_TYPE")
        else:
            new_lines.append(line)

    if "POLYMARKET_API_KEY" not in updated_keys:
        new_lines.append(f'POLYMARKET_API_KEY="{fresh_creds.api_key}"\n')
    if "POLYMARKET_SECRET" not in updated_keys:
        new_lines.append(f'POLYMARKET_SECRET="{fresh_creds.api_secret}"\n')
    if "POLYMARKET_PASSPHRASE" not in updated_keys:
        new_lines.append(f'POLYMARKET_PASSPHRASE="{fresh_creds.api_passphrase}"\n')
    if "POLYMARKET_SIGNATURE_TYPE" not in updated_keys:
        new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{successful_sig}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"✅ .env file successfully updated with working signature_type={successful_sig}!")
    print(f"   Saved at: {env_path}")
    print("=" * 70)
