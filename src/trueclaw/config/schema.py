from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GatewayConfig:
    bind: str = "127.0.0.1"
    port: int = 18789
    path: str = "/ws"
    logLevel: str = "INFO"
    streamCoalesceMs: float = 300.0
    instanceLock: bool = True
    pushOutboundEvents: bool = True
    heartbeatIntervalSec: float = 20.0
    idleTimeoutSec: float = 70.0
    maxMessageBytes: int = 262144


@dataclass
class ProviderConfig:
    apiKey: str = ""
    apiBase: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    timeoutSec: int = 60


@dataclass
class AgentDefaults:
    provider: str = "mock"
    model: str = "gpt-4.1-mini"
    maxTurns: int = 8
    streamReplies: bool = True


@dataclass
class TelegramConfig:
    enabled: bool = False
    botToken: str = ""
    botUsername: str = ""
    allowFrom: list[str] = field(default_factory=list)
    groupPolicy: str = "mention_only"
    pollIntervalSec: float = 1.0


@dataclass
class SessionConfig:
    maxMessages: int = 40
    maxPromptTokensEst: int = 8000
    reservedCompletionTokensEst: int = 2000
    maxMessagesPerSession: int = 200
    maxToolResultChars: int = 2000


@dataclass
class ToolsConfig:
    enableFileRead: bool = True
    workspaceDir: str = "~/.trueclaw/workspace"
    maxToolCallsPerTurn: int = 4


@dataclass
class WakeTaskWakeConfig:
    delivery: str = "fixed_target"
    target_channel: str | None = None
    target_chat_id: str | None = None
    target_thread_id: str | None = None
    target_session_id: str | None = None
    tool_profile: str | None = "readonly"
    allow_user_visible_reply: bool = True
    prompt: str | None = None


@dataclass
class SchedulerTaskConfig:
    name: str
    enabled: bool = True
    cron: str | None = None
    intervalSec: float | None = None
    wake: WakeTaskWakeConfig = field(default_factory=WakeTaskWakeConfig)


@dataclass
class SchedulerConfig:
    mode: str = "off"
    leaderLock: bool = True
    leaderLockTtlSec: float = 20.0
    leaderBackend: str = "file"
    redisUrl: str = ""
    wakeIdBackend: str = "file"
    wakeIdTtlSec: float = 3600.0
    tasks: list[SchedulerTaskConfig] = field(default_factory=list)
    quiet_hours: dict = field(default_factory=dict)


@dataclass
class AppConfig:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=lambda: {"mock": ProviderConfig()})
    agents: dict[str, AgentDefaults] = field(default_factory=lambda: {"defaults": AgentDefaults()})
    channels: dict[str, object] = field(default_factory=lambda: {"telegram": TelegramConfig()})
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    plugins: dict = field(default_factory=lambda: {"enabled": [], "entries": []})
    mcp: dict = field(default_factory=lambda: {"servers": {}})


# 与第 3 章书中命名对齐
TrueClawConfig = AppConfig
