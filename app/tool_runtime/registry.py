from app.core.errors import UnknownToolError
from app.models.enums import PrincipalRole
from app.tool_runtime.models import ToolDefinition
from app.tool_runtime.policy import permissions_for_role


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def get_tools_for_role(self, role: PrincipalRole) -> tuple[ToolDefinition, ...]:
        permissions = permissions_for_role(role)
        return tuple(
            tool for tool in self._tools.values() if tool.required_permission in permissions
        )

    def get_tools_for_intent(self, intent: str) -> tuple[ToolDefinition, ...]:
        return tuple(tool for tool in self._tools.values() if intent in tool.intents)

    def select_tools(self, *, intent: str, role: PrincipalRole) -> tuple[ToolDefinition, ...]:
        permissions = permissions_for_role(role)
        return tuple(
            tool
            for tool in self._tools.values()
            if intent in tool.intents and tool.required_permission in permissions
        )
