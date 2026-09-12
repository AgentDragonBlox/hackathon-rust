"""
Simulated append-only ledger — NOT a real blockchain, deliberately.
Per the project scope, real chain infra (Hardhat/Ganache/etc.) is a
DO NOT BUILD for a 24-hour hackathon. This gives the dashboard
something real to render (sequential blocks, hash chaining, confirm
status) without an external dependency that can fail or eat hours.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from shared.contracts import BlockchainTransaction


class SimulatedLedger:
    def __init__(self) -> None:
        self._chain: list[BlockchainTransaction] = []

    def _hash(self, prev_hash: str, payload_ref: str, tx_type: str) -> str:
        raw = f"{prev_hash}:{tx_type}:{payload_ref}:{datetime.utcnow().isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def append(self, tx_type: str, payload_ref: str) -> BlockchainTransaction:
        prev_hash = self._chain[-1].hash if self._chain else "genesis"
        block_number = len(self._chain) + 1
        tx = BlockchainTransaction(
            tx_id=str(uuid.uuid4()),
            tx_type=tx_type,  # type: ignore[arg-type]
            payload_ref=payload_ref,
            timestamp=datetime.utcnow(),
            block_number=block_number,
            prev_hash=prev_hash,
            hash=self._hash(prev_hash, payload_ref, tx_type),
            status="confirmed",
        )
        self._chain.append(tx)
        return tx

    def all_transactions(self) -> list[BlockchainTransaction]:
        return list(self._chain)

    def reset(self) -> None:
        self._chain = []
