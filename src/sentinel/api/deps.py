from typing import Annotated, cast

from fastapi import Depends, Request

from sentinel.audit.chain import HashChainAuditSink
from sentinel.narrative.runtime import ExplainingAuditSink
from sentinel.narrative.store import ExplanationService
from sentinel.services.errors import NotFoundError
from sentinel.services.pipeline import Pipeline


def get_pipeline(request: Request) -> Pipeline:
    return cast(Pipeline, request.app.state.pipeline)


PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]


def get_audit_chain(pipeline: PipelineDep) -> HashChainAuditSink:
    """The readable hash chain, or 404 when the configured sink has none (null, memory)."""
    sink = pipeline.audit_sink
    chain = sink.inner if isinstance(sink, ExplainingAuditSink) else sink
    if not isinstance(chain, HashChainAuditSink):
        raise NotFoundError(
            f"the audit log is not readable with audit sink {sink.name!r} "
            "(needs SENTINEL_AUDIT_SINK=chain)"
        )
    return chain


def get_explanations(pipeline: PipelineDep) -> ExplanationService:
    sink = pipeline.audit_sink
    if not isinstance(sink, ExplainingAuditSink):
        raise NotFoundError(
            f"explanations are disabled with audit sink {sink.name!r} "
            "(they need SENTINEL_AUDIT_SINK=chain)"
        )
    return sink.service


AuditChainDep = Annotated[HashChainAuditSink, Depends(get_audit_chain)]
ExplanationsDep = Annotated[ExplanationService, Depends(get_explanations)]
