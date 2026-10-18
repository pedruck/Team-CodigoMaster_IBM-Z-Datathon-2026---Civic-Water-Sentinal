from typing import Annotated

from fastapi import APIRouter, Query

from sentinel.api.deps import AuditChainDep
from sentinel.schemas.api import AuditHead, AuditRecordPage, ErrorResponse

router = APIRouter(prefix="/v1", tags=["audit"], responses={404: {"model": ErrorResponse}})


@router.get("/audit", response_model=AuditRecordPage)
def list_audit_records(
    chain: AuditChainDep,
    after_seq: Annotated[int, Query(ge=-1)] = -1,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> AuditRecordPage:
    """Records with ``seq > after_seq``, oldest first, exactly as stored (Q-AUD-06). No
    "verified" flag: the client verifies the chain (contracts §4)."""
    items = chain.records_after(after_seq, limit)
    head = chain.head
    last = items[-1]["seq"] if items else None
    more = last is not None and head is not None and last < head.seq
    return AuditRecordPage(
        items=tuple(items),
        after_seq=after_seq,
        limit=limit,
        next_after_seq=last if more else None,
    )


@router.get("/audit/head", response_model=AuditHead)
def audit_head(chain: AuditChainDep) -> AuditHead:
    head = chain.head
    return AuditHead(seq=head.seq if head else None, hash=head.hash if head else None)
