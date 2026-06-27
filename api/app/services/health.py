from fastapi import FastAPI

from app_shared.health import run_dependency_health_checks


async def run_health_checks(app: FastAPI) -> dict:
    settings = app.state.settings
    metrics = app.state.api_metrics
    dependency_report = await run_dependency_health_checks(settings)
    checks = {"api": {"status": "ok"}, **dependency_report["checks"]}

    for dependency_name, result in dependency_report["checks"].items():
        metrics.healthcheck_total.labels(dependency=dependency_name, status=result["status"]).inc()

    status = "ok" if all(check["status"] == "ok" for check in checks.values()) else "degraded"
    return {
        "status": status,
        "service": "api",
        "checks": checks,
    }
