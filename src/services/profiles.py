from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_PROFILE_DIR = Path.home() / ".local" / "share" / "data_security" / "profiles"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "profile"


@dataclass
class CustomTerm:
    original: str
    entity_type: str
    replacement: str
    variants: list[str] = field(default_factory=list)
    notes: str = ""
    term_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def all_patterns(self) -> list[str]:
        patterns: list[str] = []
        for value in [self.original, *self.variants]:
            clean = value.strip()
            if clean and clean.casefold() not in {p.casefold() for p in patterns}:
                patterns.append(clean)
        return patterns


@dataclass
class RedactionProfile:
    profile_name: str
    profile_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    terms: list[CustomTerm] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RedactionProfile":
        terms = [CustomTerm(**term) for term in data.get("terms", [])]
        return cls(
            profile_name=data["profile_name"],
            profile_id=data.get("profile_id", uuid.uuid4().hex),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            terms=terms,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProfileStore:
    """Local-only JSON storage for custom redaction profiles."""

    def __init__(self, profile_dir: str | Path | None = None):
        self.profile_dir = Path(profile_dir) if profile_dir is not None else _DEFAULT_PROFILE_DIR
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[RedactionProfile]:
        profiles: list[RedactionProfile] = []
        for path in sorted(self.profile_dir.glob("*.json")):
            profiles.append(self._read_profile(path))
        return profiles

    def create_profile(self, profile_name: str) -> RedactionProfile:
        profile_name = profile_name.strip()
        if not profile_name:
            raise ValueError("Profile name is required")
        profile = RedactionProfile(profile_name=profile_name)
        self.save_profile(profile)
        return profile

    def get_profile(self, profile_id: str) -> RedactionProfile:
        path = self._path_for_id(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_id}")
        return self._read_profile(path)

    def save_profile(self, profile: RedactionProfile) -> RedactionProfile:
        profile.updated_at = _now_iso()
        path = self._path_for(profile)
        path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
        return profile

    def add_term(self, profile_id: str, term: CustomTerm) -> RedactionProfile:
        self._validate_term(term)
        profile = self.get_profile(profile_id)
        profile.terms.append(term)
        return self.save_profile(profile)

    def update_term(
        self,
        profile_id: str,
        term_id: str,
        *,
        original: str,
        entity_type: str,
        replacement: str,
        variants: list[str] | None = None,
        notes: str = "",
    ) -> RedactionProfile:
        profile = self.get_profile(profile_id)
        for term in profile.terms:
            if term.term_id == term_id:
                term.original = original
                term.entity_type = entity_type
                term.replacement = replacement
                term.variants = variants or []
                term.notes = notes
                self._validate_term(term)
                return self.save_profile(profile)
        raise KeyError(f"Term not found: {term_id}")

    def delete_term(self, profile_id: str, term_id: str) -> RedactionProfile:
        profile = self.get_profile(profile_id)
        profile.terms = [term for term in profile.terms if term.term_id != term_id]
        return self.save_profile(profile)

    def _path_for(self, profile: RedactionProfile) -> Path:
        return self.profile_dir / f"{_slugify(profile.profile_name)}-{profile.profile_id}.json"

    def _path_for_id(self, profile_id: str) -> Path:
        matches = list(self.profile_dir.glob(f"*-{profile_id}.json"))
        if matches:
            return matches[0]
        return self.profile_dir / f"profile-{profile_id}.json"

    def _read_profile(self, path: Path) -> RedactionProfile:
        return RedactionProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _validate_term(self, term: CustomTerm) -> None:
        if not term.original.strip():
            raise ValueError("Original term is required")
        if not term.entity_type.strip():
            raise ValueError("Entity type is required")
        if not term.replacement.strip():
            raise ValueError("Replacement label is required")
