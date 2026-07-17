import logging
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("gtm.mcp.tool_registry")


class ToolDefinition(BaseModel):
    tool: str = Field(..., description="Unique tool identifier")
    version: str = Field("1.0", description="Semver version of the tool")
    description: str = Field(..., description="Human-readable description of what the tool does")
    permissions: List[str] = Field(default_factory=list, description="Access permissions required to invoke this tool")
    cost: float = Field(0.0, description="Estimated monetary cost per invocation (USD)")
    timeout_seconds: int = Field(30, description="Max execution time limit before timeout error is raised")
    estimated_latency_ms: int = Field(500, description="Expected duration in milliseconds")
    retry: bool = Field(True, description="Whether the runner should retry on failure")
    owner: str = Field("system", description="Owner team or service namespace")
    tags: List[str] = Field(default_factory=list, description="Categorization tags for discovery")
    category: str = Field("utility", description="High-level category (e.g. communication, research)")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for validation of inputs")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for validation of outputs")


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._executors: Dict[str, Callable] = {}

    def register(self, definition: ToolDefinition, executor: Callable) -> None:
        """Register a tool and its executable function callback."""
        self._tools[definition.tool] = definition
        self._executors[definition.tool] = executor
        log.info("ToolRegistry: registered tool %r (version %s)", definition.tool, definition.version)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Fetch the tool definition schema by identifier."""
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        """List all discoverable tool definitions in the registry."""
        return list(self._tools.values())

    def invoke(self, name: str, **kwargs) -> Any:
        """Invoke a registered tool function validating execution policies."""
        tool_def = self.get_tool(name)
        if not tool_def:
            raise ValueError(f"Tool {name!r} not found in the registry")
            
        executor = self._executors.get(name)
        if not executor:
            raise ValueError(f"Executor for tool {name!r} not registered")

        # In production, we'd validate inputs against input_schema here.
        log.debug("ToolRegistry: invoking %s with args %s", name, kwargs)
        return executor(**kwargs)


# Global Tool Registry singleton
tool_registry = ToolRegistry()


# ── Register Core Platform Tools ──────────────────────────────────────────

def _init_core_tools():
    # 1. Gmail Email Sending Tool
    gmail_def = ToolDefinition(
        tool="gmail.send",
        version="1.0",
        description="Send personalized outreach email to target prospect",
        permissions=["gmail.send"],
        cost=0.0003,
        timeout_seconds=30,
        estimated_latency_ms=1200,
        retry=True,
        owner="gmail",
        tags=["communication", "email", "outreach"],
        category="communication",
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "lead_id": {"type": "integer"}
            },
            "required": ["to", "subject", "body"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "message_id": {"type": "string"}
            }
        }
    )
    from gtm_engine.executors.gmail_executor import send_email
    tool_registry.register(gmail_def, send_email)

    # 2. LinkedIn Connect Tool
    linkedin_def = ToolDefinition(
        tool="linkedin.connect",
        version="1.0",
        description="Send LinkedIn connection request with a personalized note",
        permissions=["linkedin.connect"],
        cost=0.0005,
        timeout_seconds=45,
        estimated_latency_ms=3000,
        retry=True,
        owner="linkedin",
        tags=["communication", "linkedin", "outreach"],
        category="communication",
        input_schema={
            "type": "object",
            "properties": {
                "profile_url": {"type": "string"},
                "message": {"type": "string"}
            },
            "required": ["profile_url", "message"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "duration_ms": {"type": "integer"}
            }
        }
    )
    # Wrap async linkedin connector
    def _sync_linkedin_connect(profile_url: str, message: str) -> dict:
        import asyncio
        from gtm_engine.executors.linkedin_executor import LinkedInExecutor
        executor = LinkedInExecutor()
        return asyncio.run(executor.send_connection_request(profile_url=profile_url, message=message))
        
    tool_registry.register(linkedin_def, _sync_linkedin_connect)

    # 3. Tavily Web Search Tool
    tavily_def = ToolDefinition(
        tool="tavily.search",
        version="1.0",
        description="Search Google/Tavily for company intelligence and news signals",
        permissions=["tavily.search"],
        cost=0.015,
        timeout_seconds=20,
        estimated_latency_ms=800,
        retry=True,
        owner="tavily",
        tags=["research", "search", "intelligence"],
        category="research",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array"}
            }
        }
    )
    from gtm_engine.utils.search import web_search
    tool_registry.register(tavily_def, web_search)


# Initialize core tools automatically
try:
    _init_core_tools()
except Exception as e:
    log.warning("ToolRegistry: failed to auto-register core tools: %s", e)
