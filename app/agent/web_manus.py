from pydantic import Field

from app.agent.manus import Manus
from app.agent.toolcall import ToolCallAgent
from app.api.models import ToolProfile
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.python_execute import PythonExecute
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


def build_web_tools(
    profile: ToolProfile, allow_dev_local: bool = False
) -> ToolCollection:
    profile = ToolProfile(profile)

    if profile == ToolProfile.CHAT:
        return ToolCollection(Terminate())
    if profile == ToolProfile.BROWSER:
        return ToolCollection(BrowserUseTool(), Terminate())
    if profile == ToolProfile.DEV_LOCAL:
        if not allow_dev_local:
            raise PermissionError("Tool profile dev_local requires allow_dev_local=True")
        return ToolCollection(
            PythonExecute(),
            StrReplaceEditor(),
            BrowserUseTool(),
            Terminate(),
        )

    raise ValueError(f"Unsupported web tool profile: {profile}")


class WebManus(Manus):
    name: str = "WebManus"
    description: str = "A web-safe Manus agent with explicit tool profiles"

    available_tools: ToolCollection = Field(
        default_factory=lambda: build_web_tools(ToolProfile.CHAT)
    )

    @classmethod
    def create_for_web(
        cls,
        profile: ToolProfile,
        max_steps: int,
        allow_dev_local: bool = False,
    ) -> "WebManus":
        return cls(
            available_tools=build_web_tools(profile, allow_dev_local=allow_dev_local),
            max_steps=max_steps,
        )

    async def initialize_mcp_servers(self) -> None:
        return None

    async def think(self) -> bool:
        original_prompt = self.next_step_prompt
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []
        browser_in_use = any(
            tc.function.name == BrowserUseTool().name
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )

        result = await ToolCallAgent.think(self)
        self.next_step_prompt = original_prompt
        return result


__all__ = ["WebManus", "build_web_tools"]
