"""Reserve contracts — an asset (typically the battery or hospital backup)
committing to hold a slice of capacity for emergency use only, as opposed
to an active trade. Built to satisfy the contract's stated ownership
(Person 2 produces ReserveContract) but not yet exercised by anything else
in the pipeline — nobody currently calls this. If the demo scenario ends
up needing "hospital reserves its own battery, refuses to trade it away,"
this is the object that represents that commitment.
"""
import uuid
from datetime import datetime

from shared.contracts import ReserveContract


def create_reserve_contract(asset_id: str, reserved_kw: float, valid_until: datetime,
                             purpose: str) -> ReserveContract:
    return ReserveContract(
        contract_id=uuid.uuid4().hex[:8],
        asset_id=asset_id,
        reserved_kw=reserved_kw,
        valid_until=valid_until,
        purpose=purpose,
    )
