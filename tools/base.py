from dataclasses import dataclass, field


@dataclass
class ToolResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""


class Tool:
    name: str = ""
    description: str = ""
    permissions: list = []

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
        }

    async def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError
