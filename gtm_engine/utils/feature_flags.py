import os
from gtm_engine.config import settings

def is_feature_enabled(flag_name: str) -> bool:
    """Check if a specific feature flag is active."""
    use_redis = os.environ.get("USE_REDIS", "false").lower() == "true" or bool(os.environ.get("REDIS_URL"))
    use_otel = os.environ.get("USE_OTEL", "false").lower() == "true" or bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    use_mcp = os.environ.get("USE_MCP", "false").lower() == "true"
    use_react = os.environ.get("USE_REACT", "false").lower() == "true"
    use_multi_tenant = os.environ.get("USE_MULTI_TENANT", "false").lower() == "true"

    flag_map = {
        "USE_REDIS": use_redis,
        "USE_OTEL": use_otel,
        "USE_MCP": use_mcp,
        "USE_REACT": use_react,
        "USE_MULTI_TENANT": use_multi_tenant,
    }
    return flag_map.get(flag_name, False)

def get_profile() -> str:
    """Return the current active configuration profile."""
    return os.environ.get("GTM_PROFILE", "development")
