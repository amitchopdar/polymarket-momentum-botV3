#!/usr/bin/env python3
"""
Polymarket UI API Key Test Script
=================================
Tests live order placement using the UI-generated API key and its associated Address.
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

# Also allow passing API key & funder from prompt
api_key = os.getenv("POLYMARKET_API_KEY", "").strip("\"' ")
secret = os.getenv("POLYMARKET_SECRET", "").strip("\"' ")
passphrase = os.getenv("POLYMARKET_PASSPHRASE", "").strip("\"' ")

print("=" * 70)
print("  POLYMARKET UI API KEY VERIFICATION TEST")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit/Relayer Address (funder): {funder}")
print(f"🔑 UI API Key: {api_key}")

if not private_key or not funder:
    print("❌ Both POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER are required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Derive secret/passphrase deterministically if missing
l1_client = ClobClient(
    host=host,
    key=private_key,
    chain_id=137,
    signature_type=1,
    funder=funder
)

if not secret or not passphrase:
    print("⏳ Deriving HMAC Secret & Passphrase deterministically from wallet...")
    try:
        derived = l1_client.create_or_derive_api_key()
        secret = derived.api_secret
        passphrase = derived.api_passphrase
        print(f"✅ Resolved Secret: {secret[:8]}...")
    except Exception as e:
        print(f"⚠ Derivation notice: {e}")

creds = ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)

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

if successful_sig is not None:
    print("\n" + "=" * 70)
    print(f"🎉 SUCCESS! YOUR WORKING SIGNATURE TYPE IS: {successful_sig}")
    print("=" * 70)
