from __future__ import annotations

from dataclasses import asdict

from trueclaw.config.schema import AppConfig


class ConfigError(ValueError):
    pass


def validate_config(cfg: AppConfig) -> None:
    if cfg.gateway.port <= 0 or cfg.gateway.port > 65535:
        raise ConfigError("gateway.port must be in range 1..65535")
    if cfg.gateway.logLevel.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("gateway.logLevel must be one of DEBUG/INFO/WARNING/ERROR")
    if cfg.gateway.idleTimeoutSec <= cfg.gateway.heartbeatIntervalSec:
        raise ConfigError("gateway.idleTimeoutSec must be > gateway.heartbeatIntervalSec")
    if cfg.gateway.maxMessageBytes <= 0:
        raise ConfigError("gateway.maxMessageBytes must be > 0")
    if "defaults" not in cfg.agents:
        raise ConfigError("agents.defaults is required")
    default_provider = cfg.agents["defaults"].provider
    if default_provider not in cfg.providers:
        raise ConfigError(f"agents.defaults.provider not found: {default_provider}")
    _validate_scheduler(cfg)
    _validate_llm_provider(cfg)
    _validate_session(cfg)


def _validate_session(cfg: AppConfig) -> None:
    s = cfg.session
    if s.maxMessages <= 0:
        raise ConfigError("session.maxMessages must be > 0")
    if s.maxMessagesPerSession < s.maxMessages:
        raise ConfigError("session.maxMessagesPerSession must be >= session.maxMessages")
    if s.maxPromptTokensEst <= s.reservedCompletionTokensEst:
        raise ConfigError("session.maxPromptTokensEst must be > session.reservedCompletionTokensEst")


def _validate_llm_provider(cfg: AppConfig) -> None:
    name = cfg.agents["defaults"].provider
    if name == "mock":
        return
    pcfg = cfg.providers.get(name)
    if pcfg is None:
        raise ConfigError(f"agents.defaults.provider not found in providers: {name}")
    if not pcfg.apiKey:
        raise ConfigError(
            f"providers.{name}.apiKey is required when agents.defaults.provider={name}"
        )
    if not pcfg.apiBase:
        raise ConfigError(f"providers.{name}.apiBase is required")


def _validate_scheduler(cfg: AppConfig) -> None:
    if cfg.scheduler.mode not in {"off", "inprocess"}:
        raise ConfigError("scheduler.mode must be one of off/inprocess")
    if cfg.scheduler.mode != "inprocess":
        return
    names: set[str] = set()
    for task in cfg.scheduler.tasks:
        if task.name in names:
            raise ConfigError(f"duplicate scheduler task name: {task.name}")
        names.add(task.name)
        if not task.enabled:
            continue
        if task.intervalSec and task.cron:
            raise ConfigError(f"scheduler task {task.name}: use intervalSec or cron, not both")
        if not task.intervalSec and not task.cron:
            raise ConfigError(f"scheduler task {task.name}: intervalSec or cron required")
        if task.intervalSec is not None and task.intervalSec < 1:
            raise ConfigError(f"scheduler task {task.name}: intervalSec must be >= 1")
        if task.cron:
            parts = str(task.cron).split()
            if len(parts) != 5:
                raise ConfigError(f"scheduler task {task.name}: cron must have 5 fields")
        if task.wake.delivery == "fixed_target":
            if not task.wake.target_channel or not task.wake.target_chat_id:
                raise ConfigError(
                    f"scheduler task {task.name}: fixed_target requires target_channel and target_chat_id"
                )


def config_summary(cfg: AppConfig) -> dict:
    raw = asdict(cfg)
    for provider in raw.get("providers", {}).values():
        if provider.get("apiKey"):
            provider["apiKey"] = "***"
    tg = raw.get("channels", {}).get("telegram", {})
    if tg.get("botToken"):
        tg["botToken"] = "***"
    return raw
