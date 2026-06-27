from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def render_metrics(app: FastAPI) -> Response:
    metrics = app.state.api_metrics
    return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)
