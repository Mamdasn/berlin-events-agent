from dataclasses import dataclass, field


@dataclass
class AgentState:
    thread_id: str
    messages: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
    tool_budget: int = 5

    def used_tool(self, name):
        self.tools_used.append(name)
        self.tool_budget -= 1

    @property
    def budget_left(self):
        return self.tool_budget > 0
