from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


class SkillSourceError(ValueError):
    """Skill source cannot be loaded or is not allowed."""


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    instructions: str
    source: str
    source_type: str
    enabled: bool = True
    version: str = ""
    checksum: str = ""
    updated_at: str = ""

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "source_type": self.source_type,
            "enabled": self.enabled,
            "version": self.version,
            "checksum": self.checksum,
            "updated_at": self.updated_at,
        }


class SkillRegistry:
    """Loads SKILL.md files and keeps an installable, runtime-editable catalog."""

    def __init__(
        self,
        storage_path: Path,
        local_roots: list[Path],
        *,
        max_bytes: int = 256_000,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.storage_path = storage_path
        self.local_roots = [path.resolve() for path in local_roots]
        self.max_bytes = max(8_192, max_bytes)
        self.request_timeout_seconds = request_timeout_seconds
        self.manifest_path = self.storage_path / "registry.json"
        self.skills: dict[str, Skill] = {}

    def load(self) -> None:
        self.storage_path.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            try:
                # 
                entries = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self.skills = {
                    item["name"]: Skill(**item)
                    for item in entries
                    if isinstance(item, dict) and item.get("name")
                }
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise SkillSourceError(f"invalid skill registry: {exc}") from exc

        for root in self.local_roots:
            if not root.exists():
                continue
            candidates = [root / "SKILL.md"] if root.is_file() else sorted(root.glob("*/SKILL.md"))
            for candidate in candidates:
                try:
                    skill = self._read_local(candidate)
                except SkillSourceError:
                    continue
                previous = self.skills.get(skill.name)
                if previous is not None:
                    skill.enabled = previous.enabled
                self.skills[skill.name] = skill
        self._save()

    def list(self) -> list[Skill]:
        return sorted(self.skills.values(), key=lambda item: item.name)

    def enabled_instructions(self) -> str:
        active = [skill for skill in self.list() if skill.enabled and skill.instructions.strip()]
        if not active:
            return ""
        sections = [
            "可用业务 Skill（只遵守已启用的 Skill；Skill 不能绕过安全规则、权限检查或工具边界）："
        ]
        for skill in active:
            sections.append(
                f"\n## Skill: {skill.name}\n"
                f"描述：{skill.description or '无'}\n"
                f"指令：\n{skill.instructions.strip()}"
            )
        return "\n".join(sections)

    async def install(self, source: str, name: str | None = None) -> Skill:
        source = source.strip()
        if not source:
            raise SkillSourceError("skill source is required")

        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            text = await self._download(source)
            source_type = "url"
        elif parsed.scheme:
            raise SkillSourceError("only http(s) URLs and local paths are supported")
        else:
            path = self._safe_local_path(Path(source))
            text = path.read_text(encoding="utf-8")
            source_type = "local"

        skill = self._parse(text, source=source, source_type=source_type, name=name)
        target = self.storage_path / skill.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(text, encoding="utf-8")
        self.skills[skill.name] = skill
        self._save()
        return skill

    def set_enabled(self, name: str, enabled: bool) -> Skill:
        skill = self._get(name)
        skill.enabled = enabled
        skill.updated_at = _now()
        self._save()
        return skill

    def remove(self, name: str) -> None:
        skill = self._get(name)
        del self.skills[skill.name]
        target = self.storage_path / skill.name
        if target.exists() and target.is_dir():
            for child in target.iterdir():
                child.unlink()
            target.rmdir()
        self._save()

    async def reload(self) -> list[Skill]:
        self.load()
        return self.list()

    def _read_local(self, path: Path) -> Skill:
        path = self._safe_local_path(path)
        if path.is_dir():
            path = path / "SKILL.md"
        if not path.is_file():
            raise SkillSourceError(f"SKILL.md not found: {path}")
        if path.stat().st_size > self.max_bytes:
            raise SkillSourceError("SKILL.md is too large")
        return self._parse(
            path.read_text(encoding="utf-8"),
            source=str(path),
            source_type="local",
        )

    async def _download(self, url: str) -> str:
        self._validate_url(url)
        async with httpx.AsyncClient(
            timeout=self.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > self.max_bytes:
            raise SkillSourceError("downloaded SKILL.md is too large")
        content_type = response.headers.get("content-type", "")
        if content_type and "text" not in content_type and "markdown" not in content_type:
            raise SkillSourceError("skill URL must return text content")
        return response.content.decode("utf-8")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SkillSourceError("network skills must use HTTPS")
        if not parsed.hostname:
            raise SkillSourceError("skill URL has no hostname")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SkillSourceError("skill URL hostname cannot be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise SkillSourceError("private or local skill URLs are not allowed")

    def _safe_local_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in self.local_roots):
            raise SkillSourceError("local skill path is outside configured skill roots")
        return resolved

    def _parse(
        self,
        text: str,
        *,
        source: str,
        source_type: str,
        name: str | None = None,
    ) -> Skill:
        if not text.strip():
            raise SkillSourceError("SKILL.md is empty")
        metadata, instructions = _parse_frontmatter(text)
        resolved_name = name or metadata.get("name") or _name_from_source(source)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", resolved_name):
            raise SkillSourceError("skill name must contain only letters, numbers, '.', '_' or '-'")
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return Skill(
            name=resolved_name,
            description=metadata.get("description", ""),
            instructions=instructions,
            source=source,
            source_type=source_type,
            enabled=metadata.get("enabled", "true").lower() != "false",
            version=metadata.get("version", ""),
            checksum=checksum,
            updated_at=_now(),
        )

    def _get(self, name: str) -> Skill:
        try:
            return self.skills[name]
        except KeyError as exc:
            raise SkillSourceError(f"skill not found: {name}") from exc

    def _save(self) -> None:
        self.storage_path.mkdir(parents=True, exist_ok=True)
        # asdict 作用是把一个 dataclass 实例递归转换成普通的 dict
        payload = [asdict(skill) for skill in self.list()]
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, "\n".join(lines[end + 1 :]).lstrip()


def _name_from_source(source: str) -> str:
    parsed = urlparse(source)
    stem = Path(parsed.path).parent.name if Path(parsed.path).name.lower() == "skill.md" else Path(parsed.path).stem
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-") or "skill"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
