#!/usr/bin/env python3
"""
Polymarket CLOB Clean Key Generator (Cloudflare Protected)
==========================================================
Uses standard headers and rate-limiting delays to pass Cloudflare checks,
deriving clean credentials for signature_type = 1 and signature_type = 2.
"""

import os
import sys
import time

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
print("  POLYMARKET CLOB CLEAN KEY GENERATOR (V4)")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit Wallet (funder): {funder}")

if not private_key or not funder:
    print("❌ Both POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER are required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Candidate signature types for Deposit Wallets
sig_candidates = [1, 2, 3]
dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"
successful_config = None

for st in sig_candidates:
    print(f"\n----------------------------------------------------------------------")
    print(f"🧪 Testing signature_type={st} with 1.5s rate-limit delay...")
    time.sleep(1.5)
    
    try:
        client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            signature_type=st,
            funder=funder
        )
        
        creds = None
        # Try nonces 0, 1, 2
        for n in range(3):
            time.sleep(1.0)
            try:
                c_creds = client.create_api_key(nonce=n)
                print(f"  🎉 Created API Key (nonce={n}): {c_creds.api_key[:12]}...")
                creds = c_creds
                break
            except Exception:
                try:
                    d_creds = client.derive_api_key(nonce=n)
                    print(f"  ℹ Derived API Key (nonce={n}): {d_creds.api_key[:12]}...")
                    creds = d_creds
                    break
                except Exception:
                    pass

        if not creds:
            print(f"  ❌ Could not resolve API keys for signature_type={st}")
            continue

        time.sleep(1.0)
        print(f"  ⏳ Testing live order with API Key {creds.api_key[:8]}... (sig_type={st})")
        test_client = ClobClient(
            host=host,
            key=private_key,
            chain_id=137,
            creds=creds,
            signature_type=st,
            funder=funder
        )
        order_args = OrderArgsV2(price=0.01, size=5.0, side="BUY", token_id=dummy_token)
        signed_order = test_client.create_order(order_args)
        resp = test_client.post_order(signed_order, OrderType.GTC)
        print(f"  🎉 LIVE ORDER POST SUCCESSFUL! Order response: {resp}")
        if isinstance(resp, dict) and "orderID" in resp:
            cancel_resp = test_client.cancel_order(resp["orderID"])
            print(f"     Cancelled test order: {cancel_resp}")
        
        successful_config = (st, creds)
        break
    except Exception as order_err:
        print(f"     ❌ Order post failed: {order_err}")

if not successful_config:
    print("\n" + "=" * 70)
    print("❌ Rate limited by Cloudflare or signature type mismatch.")
    print("======================================================================")
    sys.exit(1)

st, working_creds = successful_config

print("\n" + "=" * 70)
print(f"🎉 VERIFIED WORKING CONFIGURATION FOUND!")
print(f"   Signature Type : {st}")
print(f"   API Key        : {working_creds.api_key}")
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
        if stripped.startswith("POLYMARKET_API_KEY="):
            new_lines.append(f'POLYMARKET_API_KEY="{working_creds.api_key}"\n')
            updated_keys.add("POLYMARKET_API_KEY")
        elif stripped.startswith("POLYMARKET_SECRET="):
            new_lines.append(f'POLYMARKET_SECRET="{working_creds.api_secret}"\n')
            updated_keys.add("POLYMARKET_SECRET")
        elif stripped.startswith("POLYMARKET_PASSPHRASE="):
            new_lines.append(f'POLYMARKET_PASSPHRASE="{working_creds.api_passphrase}"\n')
            updated_keys.add("POLYMARKET_PASSPHRASE")
        elif stripped.startswith("POLYMARKET_SIGNATURE_TYPE="):
            new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{st}"\n')
            updated_keys.add("POLYMARKET_SIGNATURE_TYPE")
        else:
            new_lines.append(line)

    if "POLYMARKET_API_KEY" not in updated_keys:
        new_lines.append(f'POLYMARKET_API_KEY="{working_creds.api_key}"\n')
    if "POLYMARKET_SECRET" not in updated_keys:
        new_lines.append(f'POLYMARKET_SECRET="{working_creds.api_secret}"\n')
    if "POLYMARKET_PASSPHRASE" not in updated_keys:
        new_lines.append(f'POLYMARKET_PASSPHRASE="{working_creds.api_passphrase}"\n')
    if "POLYMARKET_SIGNATURE_TYPE" not in updated_keys:
        new_lines.append(f'POLYMARKET_SIGNATURE_TYPE="{st}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"✅ .env file successfully updated with verified working credentials!")
    print(f"   Saved at: {env_path}")
    print("=" * 70)
