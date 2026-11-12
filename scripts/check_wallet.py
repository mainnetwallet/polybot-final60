"""Read-only wallet diagnostic for live trading.

Run from the repo folder (the bot may stay stopped):

    .venv\\Scripts\\python.exe scripts\\check_wallet.py

It reads your .env, derives the PUBLIC signer address from POLYMARKET_PRIVATE_KEY,
and asks the Polymarket CLOB what pUSD balance it sees for each signature type
(0 = EOA, 1 = email/magic proxy, 2 = browser/MetaMask proxy, 3 = smart-contract
wallet). The type that shows your balance is the one to put in POLYMARKET_SIGNATURE_TYPE.

It never places orders and never prints the private key.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config.settings import Settings  # noqa: E402
from py_clob_client_v2 import AssetType, BalanceAllowanceParams, ClobClient  # noqa: E402

TYPE_NAMES = {
    0: "EOA (plain wallet)",
    1: "email / Magic proxy",
    2: "browser wallet (MetaMask) proxy",
    3: "smart-contract / deposit wallet",
}


def to_units(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text) if "." in text else int(text) / 1_000_000.0
    except ValueError:
        return None


def main() -> int:
    settings = Settings()
    key = settings.polymarket_private_key.strip()
    if not key:
        print("POLYMARKET_PRIVATE_KEY is empty in .env")
        return 1
    funder = settings.polymarket_funder_address.strip() or None

    probe = ClobClient(host=settings.clob_base_url, chain_id=settings.polymarket_chain_id, key=key)
    print(f"Signer address (from your private key): {probe.signer.address()}")
    print(f"Funder address in .env:                  {funder or '(empty)'}")
    print(f"Signature type in .env:                  {settings.polymarket_signature_type}")
    print()

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    best: tuple[int, float] | None = None
    for sig_type in (0, 1, 2, 3):
        label = f"type {sig_type} - {TYPE_NAMES[sig_type]}"
        if sig_type != 0 and not funder:
            print(f"{label}: skipped (set POLYMARKET_FUNDER_ADDRESS first)")
            continue
        try:
            client = ClobClient(
                host=settings.clob_base_url,
                chain_id=settings.polymarket_chain_id,
                key=key,
                signature_type=sig_type,
                funder=funder if sig_type != 0 else None,
            )
            client.set_api_creds(client.create_or_derive_api_key())
            try:
                client.update_balance_allowance(params)
            except Exception:
                pass
            response = client.get_balance_allowance(params)
            balance = to_units(response.get("balance")) if isinstance(response, dict) else None
        except Exception as exc:  # never print exception text (could echo request data)
            print(f"{label}: failed ({type(exc).__name__})")
            continue
        print(f"{label}: pUSD balance = {balance if balance is not None else 'unknown'}")
        if balance and (best is None or balance > best[1]):
            best = (sig_type, balance)

    print()
    if best:
        marker = "" if best[0] == settings.polymarket_signature_type else "  <-- differs from your .env!"
        print(f"Use POLYMARKET_SIGNATURE_TYPE={best[0]} (balance {best[1]}){marker}")
        return 0
    print("No signature type shows a pUSD balance.")
    print("- Check that this private key belongs to the account you log in to Polymarket with.")
    print("- Check POLYMARKET_FUNDER_ADDRESS matches the address on your Polymarket profile.")
    print("- If your cash is USDC.e, wrap it into pUSD via the Collateral Onramp first.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
