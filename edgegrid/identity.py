"""ECDSA secp256k1 node identity (Phase-1 Objective 1 / Module 1).

One keypair gives a node three things at once:
  * a stable node id,
  * an Ethereum address that is its settlement identity on chain,
  * the ability to sign every message it puts on the wire.

Keys are persisted to `~/.edgegrid/<name>.key` (0600) so a node keeps its
identity, and therefore its stake and reputation, across restarts.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from eth_keys import keys
from eth_keys.datatypes import PrivateKey, PublicKey, Signature

DEFAULT_KEY_DIR = Path(os.environ.get("EDGEGRID_KEY_DIR", Path.home() / ".edgegrid"))


class Identity:
    """A node's secp256k1 keypair and the addresses derived from it."""

    def __init__(self, private_key: PrivateKey):
        self._sk = private_key

    # -- construction ----------------------------------------------------

    @classmethod
    def generate(cls) -> "Identity":
        return cls(keys.PrivateKey(secrets.token_bytes(32)))

    @classmethod
    def from_hex(cls, private_hex: str) -> "Identity":
        return cls(keys.PrivateKey(bytes.fromhex(private_hex.removeprefix("0x"))))

    @classmethod
    def load_or_create(cls, name: str, key_dir: Optional[Path] = None) -> "Identity":
        """Load `<key_dir>/<name>.key`, creating it on first use."""
        d = Path(key_dir) if key_dir else DEFAULT_KEY_DIR
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = d / f"{name}.key"
        if path.exists():
            return cls.from_hex(path.read_text().strip())
        ident = cls.generate()
        path.write_text(ident.private_hex)
        path.chmod(0o600)
        return ident

    # -- material --------------------------------------------------------

    @property
    def private_hex(self) -> str:
        return self._sk.to_hex().removeprefix("0x")

    @property
    def public_key(self) -> PublicKey:
        return self._sk.public_key

    @property
    def pubkey_hex(self) -> str:
        return self.public_key.to_hex().removeprefix("0x")

    @property
    def address(self) -> str:
        """Checksummed Ethereum address - the node's settlement identity."""
        return self.public_key.to_checksum_address()

    @property
    def seed_bytes(self) -> bytes:
        """32 bytes suitable for deterministically seeding a libp2p host key,
        so a node's libp2p PeerID is also stable across restarts."""
        return self._sk.to_bytes()

    # -- signing ---------------------------------------------------------

    def sign(self, payload: bytes) -> str:
        return self._sk.sign_msg(payload).to_hex().removeprefix("0x")

    def sign_message(self, msg) -> None:
        """Sign a `_Base` schema object in place, over its canonical bytes."""
        msg.signature = self.sign(msg.canonical())

    def __repr__(self) -> str:
        return f"<Identity {self.address}>"


def recover_address(payload: bytes, signature_hex: str) -> str:
    """Recover the signer's address. Raises on a malformed signature."""
    sig = Signature(bytes.fromhex(signature_hex.removeprefix("0x")))
    return sig.recover_public_key_from_msg(payload).to_checksum_address()


def verify(payload: bytes, signature_hex: str, expected_address: str) -> bool:
    """True only if `signature_hex` is a valid signature over `payload` by
    `expected_address`. Any malformed input is a False, never an exception."""
    if not signature_hex or not expected_address:
        return False
    try:
        return recover_address(payload, signature_hex).lower() == expected_address.lower()
    except Exception:
        return False


def verify_message(msg, expected_address: str) -> bool:
    """Verify a `_Base` schema object that carries a `signature` field."""
    sig = getattr(msg, "signature", None)
    if not sig:
        return False
    return verify(msg.canonical(), sig, expected_address)
