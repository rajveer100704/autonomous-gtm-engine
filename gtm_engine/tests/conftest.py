import os
import pytest

# Force isolated SQLite environment and disable external integrations for all tests
os.environ["USE_REDIS"] = "false"
os.environ["REDIS_URL"] = ""
os.environ["USE_OTEL"] = "false"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["GTM_MOCK_MODE"] = "true"
