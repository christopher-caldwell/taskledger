from __future__ import annotations

import copy
import hashlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from taskledger.core import canonical

from .model import SessionRole


class ConfigurationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedProfile:
    logical_name: str
    role_name: str
    description: str | None
    nickname_candidates: tuple[str, ...] | None
    source_kind: str
    source_path: Path
    source_display: str
    profile_hash: str
    native_config: dict[str, Any]
    effective_config: dict[str, Any]
    effective_config_hash: str
    model: str
    effort: str
    developer_instructions: str | None


FORBIDDEN_TOP_LEVEL = {
    "approval_policy",
    "approvals_reviewer",
    "auto_review",
    "default_permissions",
    "permissions",
    "sandbox_mode",
    "sandbox_workspace_write",
    "shell_environment_policy",
    "allow_login_shell",
    "mcp_servers",
    "mcp_oauth_credentials_store",
    "mcp_oauth_callback_port",
    "mcp_oauth_callback_url",
    "mcp_optional_startup_grace_ms",
    "apps",
    "plugins",
    "marketplaces",
    "browser_use",
    "computer_use",
    "web_search",
    "tools",
    "tool_suggest",
    "orchestrator",
    "goals",
    "hooks",
    "notify",
    "history",
    "sqlite_home",
    "log_dir",
    "experimental_thread_store",
    "experimental_thread_store_endpoint",
    "projects",
    "include_collaboration_mode_instructions",
}


class ProfileResolver:
    """Resolve Codex-native agent role files without cloning ConfigToml in Python."""

    def __init__(self, repository_root: str | Path, *, codex_home: str | Path | None = None):
        self.repository_root = Path(repository_root).resolve()
        configured_home = codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        self.codex_home = Path(configured_home).resolve()
        self.bundled_root = Path(__file__).parents[3] / "skills" / "taskledger" / "assets"

    def resolve(self, logical_name: str, role: SessionRole) -> ResolvedProfile:
        filename = self._filename(logical_name, role)
        candidates = (
            ("PROJECT", self.repository_root / ".codex" / "agents" / filename),
            ("USER/GLOBAL", self.codex_home / "agents" / filename),
            ("BUNDLED", self.bundled_root / filename),
            ("BUNDLED", self.codex_home / "skills" / "taskledger" / "assets" / filename),
        )
        for source_kind, path in candidates:
            if path.is_file():
                return self._parse(logical_name, path, source_kind)
        raise RuntimeError(f"Codex profile configuration is missing for {logical_name}")

    def _parse(self, logical_name: str, path: Path, source_kind: str) -> ResolvedProfile:
        raw = path.read_bytes()
        try:
            value = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise RuntimeError(f"invalid Codex agent role file {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid Codex agent role file {path}: root must be a table")
        role_name = value.pop("name", None) or logical_name
        description = value.pop("description", None)
        nicknames = value.pop("nickname_candidates", None)
        if not isinstance(role_name, str) or not role_name.strip():
            raise RuntimeError(f"invalid Codex agent role file {path}: name must be non-empty")
        if description is not None and (not isinstance(description, str) or not description.strip()):
            raise RuntimeError(f"invalid Codex agent role file {path}: description must be non-empty")
        if nicknames is not None and (
            not isinstance(nicknames, list)
            or not nicknames
            or any(not isinstance(item, str) or not item.strip() for item in nicknames)
            or len(set(nicknames)) != len(nicknames)
        ):
            raise RuntimeError(f"invalid Codex agent role file {path}: nickname_candidates are invalid")
        if nicknames and any(not all(char.isascii() and (char.isalnum() or char in " -_") for char in item.strip()) for item in nicknames):
            raise RuntimeError(f"invalid Codex agent role file {path}: nickname_candidates contain unsupported characters")
        self._validate_authority(value, path)
        model = value.get("model")
        effort = value.get("model_reasoning_effort")
        if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
            raise RuntimeError(f"invalid Codex agent role file {path}: model and model_reasoning_effort are required")
        developer = value.get("developer_instructions")
        if developer is not None and (not isinstance(developer, str) or not developer.strip()):
            raise RuntimeError(f"invalid Codex agent role file {path}: developer_instructions must be non-empty")
        effective = self._merge_controller_config(value)
        display = self._display(path, source_kind)
        return ResolvedProfile(
            logical_name=logical_name,
            role_name=role_name.strip(),
            description=description.strip() if isinstance(description, str) else None,
            nickname_candidates=tuple(item.strip() for item in nicknames) if nicknames else None,
            source_kind=source_kind,
            source_path=path.resolve(),
            source_display=display,
            profile_hash=hashlib.sha256(raw).hexdigest(),
            native_config=value,
            effective_config=effective,
            effective_config_hash=hashlib.sha256(canonical(effective).encode()).hexdigest(),
            model=model,
            effort=effort,
            developer_instructions=developer,
        )

    @staticmethod
    def _validate_authority(config: dict[str, Any], path: Path) -> None:
        conflict = sorted(FORBIDDEN_TOP_LEVEL.intersection(config))
        agents = config.get("agents")
        if agents is not None and (not isinstance(agents, dict) or agents.get("enabled") is not False):
            conflict.append("agents.enabled")
        features = config.get("features")
        if isinstance(features, dict):
            if "multi_agent_v2" in features:
                multi = features["multi_agent_v2"]
                disabled = multi is False or (isinstance(multi, dict) and multi.get("enabled") is False)
                if not disabled:
                    conflict.append("features.multi_agent_v2")
            if features.get("collab") not in {None, False}:
                conflict.append("features.collab")
        if conflict:
            raise ConfigurationConflict(
                "CONFIGURATION_CONFLICT: profile requests controller-owned capability "
                + ", ".join(sorted(set(conflict)))
                + f" in {path}"
            )

    @staticmethod
    def _merge_controller_config(native: dict[str, Any]) -> dict[str, Any]:
        effective = copy.deepcopy(native)
        agents = effective.setdefault("agents", {})
        if not isinstance(agents, dict):
            raise ConfigurationConflict("CONFIGURATION_CONFLICT: agents must be a table")
        agents["enabled"] = False
        features = effective.setdefault("features", {})
        if not isinstance(features, dict):
            raise ConfigurationConflict("CONFIGURATION_CONFLICT: features must be a table")
        features["multi_agent_v2"] = False
        features["collab"] = False
        effective["include_collaboration_mode_instructions"] = False
        effective["sandbox_workspace_write"] = {"network_access": False}
        return effective

    def _display(self, path: Path, source_kind: str) -> str:
        if source_kind == "PROJECT":
            return str(path.resolve().relative_to(self.repository_root))
        if source_kind == "USER/GLOBAL":
            return str(path.resolve().relative_to(self.codex_home).as_posix())
        return f"bundled:{path.name}"

    @staticmethod
    def _filename(logical_name: str, role: SessionRole) -> str:
        if role in {SessionRole.REVIEWER, SessionRole.REQUIREMENT_REVIEWER}:
            return "taskledger-reviewer.toml"
        if role == SessionRole.TASK_CREATOR:
            return "taskledger-task-creator.toml"
        return f"taskledger-worker-{logical_name}.toml"
