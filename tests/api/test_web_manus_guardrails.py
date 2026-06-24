import importlib.util
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.api.models import ToolProfile


REPO_ROOT = Path(__file__).parents[2]
MANUS_SYSTEM_PROMPT = "manus-system-prompt"
MANUS_NEXT_STEP_PROMPT = "manus-next-step-prompt"


class FakeTool:
    name: str

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeTerminate(FakeTool):
    name = "terminate"


class FakeBrowserUseTool(FakeTool):
    name = "browser_use"


class FakePythonExecute(FakeTool):
    name = "python_execute"


class FakeStrReplaceEditor(FakeTool):
    name = "str_replace_editor"


class FakeAskHuman(FakeTool):
    name = "ask_human"


class FakeBash(FakeTool):
    name = "bash"


class FakeComputerUseTool(FakeTool):
    name = "computer_use"


class FakeSandboxShellTool(FakeTool):
    name = "sandbox_shell"


class FakeSandboxBrowserTool(FakeTool):
    name = "sandbox_browser"


class FakeSandboxFilesTool(FakeTool):
    name = "sandbox_files"


class FakeSandboxVisionTool(FakeTool):
    name = "sandbox_vision"


class FakeMCPClientTool(FakeTool):
    name = "mcp_remote_tool"


class FakeToolCollection:
    def __init__(self, *tools):
        self.tools = tools
        self.tool_map = {tool.name: tool for tool in tools}

    def get_tool(self, name: str):
        return self.tool_map.get(name)


class FakeMemory:
    def __init__(self):
        self.messages = []


class FakeBrowserContextHelper:
    def __init__(self, agent):
        self.agent = agent

    async def format_next_step_prompt(self):
        self.agent.browser_prompt_calls += 1
        return "browser-context-prompt"


class FakeManus(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "Manus"
    description: str = "fake Manus"
    system_prompt: str = MANUS_SYSTEM_PROMPT
    next_step_prompt: str = MANUS_NEXT_STEP_PROMPT
    max_steps: int = 20
    available_tools: FakeToolCollection = Field(
        default_factory=lambda: FakeToolCollection(FakeTerminate())
    )
    memory: FakeMemory = Field(default_factory=FakeMemory)
    browser_context_helper: FakeBrowserContextHelper | None = None
    mcp_initialize_calls: int = 0
    browser_prompt_calls: int = 0
    toolcall_think_calls: int = 0

    def __init__(self, **data):
        super().__init__(**data)
        if self.browser_context_helper is None:
            self.browser_context_helper = FakeBrowserContextHelper(self)

    async def initialize_mcp_servers(self):
        self.mcp_initialize_calls += 1
        raise AssertionError("MCP initialization must not run in web tests")


class FakeToolCallAgent:
    async def think(self):
        self.toolcall_think_calls += 1
        return True


def make_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def web_manus_module():
    module_names = [
        "app.agent",
        "app.agent.manus",
        "app.agent.toolcall",
        "app.agent.web_manus",
        "app.tool",
        "app.tool.browser_use_tool",
        "app.tool.python_execute",
        "app.tool.str_replace_editor",
        "app.tool.terminate",
        "app.tool.tool_collection",
        "app.tool.ask_human",
        "app.tool.bash",
        "app.tool.computer_use_tool",
        "app.tool.mcp",
        "app.tool.sandbox",
        "app.tool.sandbox.sb_browser_tool",
        "app.tool.sandbox.sb_files_tool",
        "app.tool.sandbox.sb_shell_tool",
        "app.tool.sandbox.sb_vision_tool",
    ]
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    agent_package = make_module("app.agent")
    agent_package.__path__ = []
    tool_package = make_module(
        "app.tool",
        BrowserUseTool=FakeBrowserUseTool,
        PythonExecute=FakePythonExecute,
        StrReplaceEditor=FakeStrReplaceEditor,
        Terminate=FakeTerminate,
        ToolCollection=FakeToolCollection,
    )
    tool_package.__path__ = []
    sandbox_package = make_module("app.tool.sandbox")
    sandbox_package.__path__ = []

    sys.modules.update(
        {
            "app.agent": agent_package,
            "app.agent.manus": make_module("app.agent.manus", Manus=FakeManus),
            "app.agent.toolcall": make_module(
                "app.agent.toolcall", ToolCallAgent=FakeToolCallAgent
            ),
            "app.tool": tool_package,
            "app.tool.browser_use_tool": make_module(
                "app.tool.browser_use_tool", BrowserUseTool=FakeBrowserUseTool
            ),
            "app.tool.python_execute": make_module(
                "app.tool.python_execute", PythonExecute=FakePythonExecute
            ),
            "app.tool.str_replace_editor": make_module(
                "app.tool.str_replace_editor", StrReplaceEditor=FakeStrReplaceEditor
            ),
            "app.tool.terminate": make_module(
                "app.tool.terminate", Terminate=FakeTerminate
            ),
            "app.tool.tool_collection": make_module(
                "app.tool.tool_collection", ToolCollection=FakeToolCollection
            ),
            "app.tool.ask_human": make_module(
                "app.tool.ask_human", AskHuman=FakeAskHuman
            ),
            "app.tool.bash": make_module("app.tool.bash", Bash=FakeBash),
            "app.tool.computer_use_tool": make_module(
                "app.tool.computer_use_tool", ComputerUseTool=FakeComputerUseTool
            ),
            "app.tool.mcp": make_module(
                "app.tool.mcp", MCPClientTool=FakeMCPClientTool
            ),
            "app.tool.sandbox": sandbox_package,
            "app.tool.sandbox.sb_browser_tool": make_module(
                "app.tool.sandbox.sb_browser_tool",
                SandboxBrowserTool=FakeSandboxBrowserTool,
            ),
            "app.tool.sandbox.sb_files_tool": make_module(
                "app.tool.sandbox.sb_files_tool", SandboxFilesTool=FakeSandboxFilesTool
            ),
            "app.tool.sandbox.sb_shell_tool": make_module(
                "app.tool.sandbox.sb_shell_tool", SandboxShellTool=FakeSandboxShellTool
            ),
            "app.tool.sandbox.sb_vision_tool": make_module(
                "app.tool.sandbox.sb_vision_tool", SandboxVisionTool=FakeSandboxVisionTool
            ),
        }
    )

    try:
        module_path = REPO_ROOT / "app" / "agent" / "web_manus.py"
        spec = importlib.util.spec_from_file_location("app.agent.web_manus", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous_module in previous_modules.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module


def tool_names(collection):
    return [tool.name for tool in collection.tools]


def test_chat_profile_exact_allowlist(web_manus_module):
    tools = web_manus_module.build_web_tools(ToolProfile.CHAT)

    assert set(tool_names(tools)) == {web_manus_module.Terminate().name}
    assert tool_names(tools) == [web_manus_module.Terminate().name]


def test_browser_profile_exact_allowlist(web_manus_module):
    tools = web_manus_module.build_web_tools(ToolProfile.BROWSER)

    assert tool_names(tools) == [
        web_manus_module.BrowserUseTool().name,
        web_manus_module.Terminate().name,
    ]


def test_dev_local_requires_flag(web_manus_module):
    with pytest.raises(PermissionError, match="allow_dev_local=True"):
        web_manus_module.build_web_tools(ToolProfile.DEV_LOCAL)

    tools = web_manus_module.build_web_tools(
        ToolProfile.DEV_LOCAL, allow_dev_local=True
    )

    assert tool_names(tools) == [
        web_manus_module.PythonExecute().name,
        web_manus_module.StrReplaceEditor().name,
        web_manus_module.BrowserUseTool().name,
        web_manus_module.Terminate().name,
    ]


def test_unsafe_denied_tool_names_absent_from_allowed_profiles(web_manus_module):
    allowed_profiles = [
        web_manus_module.build_web_tools(ToolProfile.CHAT),
        web_manus_module.build_web_tools(ToolProfile.BROWSER),
        web_manus_module.build_web_tools(
            ToolProfile.DEV_LOCAL, allow_dev_local=True
        ),
    ]
    unsafe_names = {
        FakeAskHuman().name,
        FakeBash().name,
        FakeComputerUseTool().name,
        FakeSandboxShellTool().name,
        FakeSandboxBrowserTool().name,
        FakeSandboxFilesTool().name,
        FakeSandboxVisionTool().name,
        FakeMCPClientTool().name,
    }

    for tools in allowed_profiles:
        assert unsafe_names.isdisjoint(tool_names(tools))
        assert not any(isinstance(tool, FakeMCPClientTool) for tool in tools.tools)


def test_create_for_web_sets_max_steps_and_safe_tools_without_mcp_initialization(
    web_manus_module,
):
    agent = web_manus_module.WebManus.create_for_web(ToolProfile.BROWSER, max_steps=7)

    assert agent.max_steps == 7
    assert tool_names(agent.available_tools) == [
        web_manus_module.BrowserUseTool().name,
        web_manus_module.Terminate().name,
    ]
    assert agent.system_prompt == MANUS_SYSTEM_PROMPT
    assert agent.next_step_prompt == MANUS_NEXT_STEP_PROMPT
    assert agent.mcp_initialize_calls == 0


@pytest.mark.asyncio
async def test_think_does_not_lazy_initialize_mcp(web_manus_module):
    agent = web_manus_module.WebManus.create_for_web(ToolProfile.BROWSER, max_steps=3)
    tool_call = types.SimpleNamespace(
        function=types.SimpleNamespace(name=web_manus_module.BrowserUseTool().name)
    )
    agent.memory.messages.append(types.SimpleNamespace(tool_calls=[tool_call]))

    result = await agent.think()

    assert result is True
    assert agent.mcp_initialize_calls == 0
    assert agent.toolcall_think_calls == 1
    assert agent.browser_prompt_calls == 1
    assert agent.next_step_prompt == MANUS_NEXT_STEP_PROMPT
