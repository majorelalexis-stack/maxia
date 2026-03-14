"""MAXIA Solana TX V11 — Transactions reelles via RPC HTTP (serialisation corrigee)"""
import asyncio, time, struct, base64
import httpx
import base58
from nacl.signing import SigningKey
from config import get_rpc_url, MARKETING_WALLET_PRIVKEY, MARKETING_WALLET_ADDRESS

print("[SolanaTx] Mode RPC HTTP natif — transactions reelles activees")

MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"


def _keypair_from_b58(privkey_b58: str):
    secret = base58.b58decode(privkey_b58)
    return SigningKey(secret[:32])


def _encode_compact_u16(val: int) -> bytes:
    """Encode un entier en compact-u16 (format Solana wire)."""
    if val < 0x80:
        return bytes([val])
    elif val < 0x4000:
        return bytes([val & 0x7F | 0x80, val >> 7])
    else:
        return bytes([val & 0x7F | 0x80, (val >> 7) & 0x7F | 0x80, val >> 14])


def _build_message(from_pubkey: bytes, to_pubkey: bytes,
                    lamports: int, memo: str,
                    recent_blockhash: str) -> bytes:
    """Construit un message Solana v0 legacy correctement serialise."""
    system_prog = base58.b58decode(SYSTEM_PROGRAM_ID)
    memo_prog = base58.b58decode(MEMO_PROGRAM_ID)
    blockhash_bytes = base58.b58decode(recent_blockhash)

    # Comptes: [0]=from (signer+writable), [1]=to (writable), [2]=system, [3]=memo
    accounts = [from_pubkey, to_pubkey, system_prog, memo_prog]

    # Header
    num_required_sigs = 1
    num_readonly_signed = 0
    num_readonly_unsigned = 2  # system + memo programs
    header = bytes([num_required_sigs, num_readonly_signed, num_readonly_unsigned])

    # Compact array of account keys
    account_keys = _encode_compact_u16(len(accounts))
    for acc in accounts:
        account_keys += acc

    # Recent blockhash (32 bytes)
    bh = blockhash_bytes

    # Instructions
    instructions = bytearray()

    # Instruction 1: System Transfer (program index = 2)
    transfer_data = struct.pack("<I", 2) + struct.pack("<Q", lamports)
    instructions += bytes([2])  # program_id index
    instructions += _encode_compact_u16(2)  # num accounts
    instructions += bytes([0, 1])  # account indices [from, to]
    instructions += _encode_compact_u16(len(transfer_data))
    instructions += transfer_data

    # Instruction 2: Memo (program index = 3)
    memo_bytes = memo.encode("utf-8")[:400]  # Limiter la taille
    instructions += bytes([3])  # program_id index
    instructions += _encode_compact_u16(1)  # num accounts (signer)
    instructions += bytes([0])  # account index [from = signer]
    instructions += _encode_compact_u16(len(memo_bytes))
    instructions += memo_bytes

    # Compact array of instructions (2 instructions)
    num_ix = _encode_compact_u16(2)

    # Assemble message
    message = header + account_keys + bh + num_ix + bytes(instructions)

    return message


async def get_sol_balance(wallet_address: str) -> float:
    rpc = get_rpc_url()
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet_address]}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()
        return data.get("result", {}).get("value", 0) / 1e9
    except Exception as e:
        print(f"[SolanaTx] Balance error: {e}")
        return 0.0


async def get_recent_blockhash() -> str:
    rpc = get_rpc_url()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash", "params": []}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(rpc, json=payload)
        data = resp.json()
    return data["result"]["value"]["blockhash"]


async def send_memo_transfer(to_address: str, amount_sol: float, memo_text: str) -> dict:
    """Envoie un micro-transfert SOL avec memo. VRAIE TRANSACTION."""
    if not MARKETING_WALLET_PRIVKEY:
        return {"success": False, "error": "MARKETING_WALLET_PRIVKEY non configure"}
    if amount_sol > 0.01:
        return {"success": False, "error": "Securite: max 0.01 SOL par tx"}

    balance = await get_sol_balance(MARKETING_WALLET_ADDRESS)
    if balance < amount_sol + 0.005:
        return {"success": False, "error": f"Solde insuffisant: {balance:.4f} SOL"}

    try:
        rpc = get_rpc_url()
        signing_key = _keypair_from_b58(MARKETING_WALLET_PRIVKEY)
        from_pubkey = bytes(signing_key.verify_key)
        to_pubkey = base58.b58decode(to_address)

        blockhash = await get_recent_blockhash()

        lamports = int(amount_sol * 1e9)
        message = _build_message(from_pubkey, to_pubkey, lamports, memo_text[:400], blockhash)

        # Signer le message
        signature = signing_key.sign(message).signature

        # Transaction = compact_array(signatures) + message
        signed_tx = _encode_compact_u16(1) + signature + message

        tx_base64 = base64.b64encode(signed_tx).decode("ascii")

        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_base64, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()

        if "result" in data:
            sig = data["result"]
            print(f"[SolanaTx] SENT: {sig[:20]}... -> {to_address[:8]}...")
            return {
                "success": True, "status": "sent", "signature": sig,
                "from": MARKETING_WALLET_ADDRESS, "to": to_address,
                "amount_sol": amount_sol, "memo": memo_text[:400],
                "explorer": f"https://solscan.io/tx/{sig}",
            }
        else:
            error = data.get("error", {})
            err_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            # Log concis, pas flood
            if "Blockhash" in err_msg:
                print(f"[SolanaTx] Blockhash expire — retry au prochain cycle")
            else:
                print(f"[SolanaTx] RPC error: {err_msg[:120]}")
            return {"success": False, "error": err_msg}

    except Exception as e:
        print(f"[SolanaTx] TX error: {e}")
        return {"success": False, "error": str(e)}


async def verify_usdc_payment(tx_signature: str, expected_amount_usdc: float = 0,
                                expected_to: str = "") -> dict:
    rpc = get_rpc_url()
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTransaction",
            "params": [tx_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()
        result = data.get("result")
        if not result:
            return {"valid": False, "error": "Transaction introuvable"}
        meta = result.get("meta", {})
        if meta.get("err"):
            return {"valid": False, "error": f"Transaction echouee: {meta['err']}"}
        return {"valid": True, "signature": tx_signature}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def check_wallet_activity(wallet_address: str, min_sol: float = 0.1) -> dict:
    balance = await get_sol_balance(wallet_address)
    return {
        "wallet": wallet_address, "balance_sol": balance,
        "is_active": balance >= min_sol, "meets_minimum": balance >= min_sol,
    }


async def send_usdc_transfer(to_address: str, amount_usdc: float,
                              from_privkey: str, from_address: str) -> dict:
    """Vrai transfert USDC — delegue a send_usdc_transfer_real."""
    return await send_usdc_transfer_real(to_address, amount_usdc, from_privkey, from_address)


# ── USDC SPL Token Transfer reel ──
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


async def find_token_account(wallet: str, mint: str = USDC_MINT) -> str:
    """Trouve le token account USDC d'un wallet."""
    rpc = get_rpc_url()
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [wallet, {"mint": mint}, {"encoding": "jsonParsed"}],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()
        accounts = data.get("result", {}).get("value", [])
        if accounts:
            return accounts[0].get("pubkey", "")
    except Exception as e:
        print(f"[SolanaTx] Token account error: {e}")
    return ""


async def get_usdc_balance(wallet: str) -> float:
    """Recupere le solde USDC d'un wallet."""
    rpc = get_rpc_url()
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [wallet, {"mint": USDC_MINT}, {"encoding": "jsonParsed"}],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()
        accounts = data.get("result", {}).get("value", [])
        if accounts:
            info = accounts[0].get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amount = info.get("tokenAmount", {}).get("uiAmount", 0)
            return float(amount) if amount else 0
    except Exception:
        pass
    return 0


async def send_usdc_transfer_real(to_address: str, amount_usdc: float,
                                    from_privkey: str, from_address: str) -> dict:
    """Vrai transfert USDC SPL via RPC."""
    if not from_privkey:
        return {"success": False, "error": "Cle privee non configuree"}
    if amount_usdc <= 0:
        return {"success": False, "error": "Montant invalide"}

    # Verifier le solde SOL pour les frais
    sol_balance = await get_sol_balance(from_address)
    if sol_balance < 0.005:
        return {"success": False, "error": f"SOL insuffisant pour frais: {sol_balance:.4f}"}

    # Verifier le solde USDC
    usdc_balance = await get_usdc_balance(from_address)
    if usdc_balance < amount_usdc:
        return {"success": False, "error": f"USDC insuffisant: {usdc_balance:.2f} (besoin: {amount_usdc:.2f})"}

    try:
        rpc = get_rpc_url()
        signing_key = _keypair_from_b58(from_privkey)
        from_pubkey = bytes(signing_key.verify_key)

        # Trouver les token accounts
        from_token_account = await find_token_account(from_address)
        to_token_account = await find_token_account(to_address)

        if not from_token_account:
            return {"success": False, "error": "Token account USDC source introuvable"}
        if not to_token_account:
            return {"success": False, "error": "Token account USDC destination introuvable (le destinataire doit avoir un compte USDC)"}

        from_ta = base58.b58decode(from_token_account)
        to_ta = base58.b58decode(to_token_account)
        token_prog = base58.b58decode(TOKEN_PROGRAM)

        blockhash = await get_recent_blockhash()
        blockhash_bytes = base58.b58decode(blockhash)

        # Amount en raw (USDC = 6 decimales)
        amount_raw = int(amount_usdc * 1_000_000)

        # Construire le message
        # Accounts: [0]=from_owner (signer), [1]=from_token_account, [2]=to_token_account, [3]=token_program
        accounts = [from_pubkey, from_ta, to_ta, token_prog]
        header = bytes([1, 0, 1])  # 1 signer, 0 readonly signed, 1 readonly unsigned (token prog)
        account_keys = _encode_compact_u16(len(accounts))
        for acc in accounts:
            account_keys += acc

        # SPL Token Transfer instruction (index 3 in SPL Token program)
        # Data: 1 byte instruction (3) + 8 bytes amount (u64 LE)
        transfer_data = bytes([3]) + struct.pack("<Q", amount_raw)
        ix = bytes([3])  # program index (token program)
        ix += _encode_compact_u16(3)  # 3 accounts
        ix += bytes([1, 2, 0])  # [from_token, to_token, owner/signer]
        ix += _encode_compact_u16(len(transfer_data))
        ix += transfer_data

        message = header + account_keys + blockhash_bytes + _encode_compact_u16(1) + ix

        # Signer
        signature = signing_key.sign(message).signature
        signed_tx = _encode_compact_u16(1) + signature + message

        tx_base64 = base64.b64encode(signed_tx).decode("ascii")

        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_base64, {"encoding": "base64", "skipPreflight": False}],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()

        if "result" in data:
            sig = data["result"]
            print(f"[SolanaTx] USDC SENT: {amount_usdc} USDC -> {to_address[:8]}... TX: {sig[:16]}...")
            return {
                "success": True, "signature": sig,
                "amount_usdc": amount_usdc,
                "from": from_address, "to": to_address,
                "explorer": f"https://solscan.io/tx/{sig}",
            }
        else:
            error = data.get("error", {})
            err_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            print(f"[SolanaTx] USDC error: {err_msg[:100]}")
            return {"success": False, "error": err_msg}

    except Exception as e:
        print(f"[SolanaTx] USDC TX error: {e}")
        return {"success": False, "error": str(e)}
