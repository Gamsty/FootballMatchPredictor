"""
Telemetry setup using Azure Monitor OpenTelemetry distro.

Enabled when APPLICATIONINSIGHTS_CONNECTION_STRING is set (i.e. in Azure Container Apps).
No-op otherwise — local development continues to use plain print/logging.
"""

import os
import logging

logger = logging.getLogger(__name__)


def setup_telemetry(flask_app) -> None:
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn_str:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        configure_azure_monitor(
            connection_string=conn_str,
            logger_name="fotballpred",
        )

        FlaskInstrumentor().instrument_app(flask_app)
        SQLAlchemyInstrumentor().instrument()
        RequestsInstrumentor().instrument()

        logger.info("Azure Monitor telemetry configured")
    except Exception as e:
        logger.warning(f"Telemetry setup failed (continuing without): {e}")
