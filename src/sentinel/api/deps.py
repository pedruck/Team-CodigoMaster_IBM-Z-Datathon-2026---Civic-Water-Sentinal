from typing import Annotated, cast

from fastapi import Depends, Request

from sentinel.services.pipeline import Pipeline


def get_pipeline(request: Request) -> Pipeline:
    return cast(Pipeline, request.app.state.pipeline)


PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]
