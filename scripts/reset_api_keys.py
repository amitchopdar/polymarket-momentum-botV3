#!/usr/bin/env python3
"""
Polymarket CLOB API Key Generator & Verification Tool (V3)
==========================================================
Iterates nonces (0..5) to create a BRAND NEW fresh API key on Polymarket's server,
tests live order placement for all signature types (1, 2, 3, 0), and auto-updates .env.
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
print("  POLYMARKET CLOB API KEY GENERATOR & VERIFICATION TOOL (V3)")
print("=" * 70)
print(f"🔑 Private Key: 0x{private_key[2:6]}..." if private_key else "❌ Private Key missing!")
print(f"📦 Deposit Wallet (funder): {funder}")

if not private_key or not funder:
    print("❌ Both POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER are required.")
    sys.exit(1)

host = "https://clob.polymarket.com"

# Step 1: Try creating/deriving fresh API key across nonces 0..5 and signature candidates 1, 2, 3, 0
sig_candidates = [1, 2, 3, 0]
dummy_token = "60071130405041607714679803984580413572787897674829718027387574381836360117448"

successful_config = None

for st in sig_candidates:
    print(f"\n----------------------------------------------------------------------")
    print(f"🧪 Testing signature_type={st}...")
    
    # Try nonces 0..5 to find or generate a valid API key
    creds_list = []
    l1_client = ClobClient(
        host=host,
        key=private_key,
        chain_id=137,
        signature_type=st,
        funder=funder if st in [1, 2, 3] else None
    )
    
    for n in range(6):
        try:
            c_creds = l1_client.create_api_key(nonce=n)
            print(f"  🎉 Created fresh API Key (nonce={n}): {c_creds.api_key[:12]}...")
            creds_list.append(c_creds)
        except Exception:
            try:
                d_creds = l1_client.derive_api_key(nonce=n)
                print(f"  ℹ Derived existing API Key (nonce={n}): {d_creds.api_key[:12]}...")
                creds_list.append(d_creds)
            except Exception:
                pass

    if not creds_list:
        print(f"  ❌ Could not create or derive any API keys for signature_type={st}")
        continue

    # Test live order placement for each resolved creds set
    for creds in creds_list:
        try:
            print(f"  ⏳ Testing live order with API Key {creds.api_key[:8]}... (sig_type={st})")
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
            print(f"  🎉 LIVE ORDER POST SUCCESSFUL! Order response: {resp}")
            if isinstance(resp, dict) and "orderID" in resp:
                cancel_resp = test_client.cancel_order(resp["orderID"])
                print(f"     Cancelled test order: {cancel_resp}")
            
            successful_config = (st, creds)
            break
        except Exception as order_err:
            print(f"     ❌ Order post failed: {order_err}")
            
    if successful_config:
        break

if not successful_config:
    print("\n" + "=" * 70)
    print("❌ Could not find a working signature_type and API key combination.")
    print("======================================================================")
    sys.exit(1)

st, working_creds = successful_config

print("\n" + "=" * 70)
print(f"🎉 VERIFIED WORKING CONFIGURATION FOUND!")
print(f"   Signature Type : {st}")
print(f"   API Key        : {working_creds.api_key}")
print("=" * 70)

# Step 2: Auto-update .env file
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
