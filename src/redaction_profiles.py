"""Local-only custom redaction profile models and JSON storage.

The service in this module is intentionally filesystem-backed and network-free so
custom client/matter terms can be reused by UI and detector code without leaving
this machine. Tests and checked-in examples must use synthetic terms only.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4


_PLACEHOLDER_ENTITY_RE = re.compile(r"[^A-Z0-9]+")


class RedactionProfileError(Exception):
    """Base exception for local redaction profile operations."""


class ProfileNotFoundError(RedactionProfileError):
    """Raised when a requested profile does not exist."""


class TermNotFoundError(RedactionProfileError):
    """Raised when a requested term does not exist in a profile."""


class DuplicateProfileNameError(RedactionProfileError):
    """Raised when creating or renaming to an existing profile name."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def normalize_entity_type(entity_type: str) -> str:
    """Return the canonical uppercase entity type used in placeholders."""
    entity_type = entity_type.strip().upper()
    entity_type = _PLACEHOLDER_ENTITY_RE.sub("_", entity_type).strip("_")
    return entity_type or "CUSTOM"


def next_placeholder(entity_type: str, existing_terms: Iterable["RedactionTerm"]) -> str:
    """Generate the next stable typed placeholder for a profile.

    Existing replacements for the same entity type are scanned so adding a new
    COMPANY term after [COMPANY_1] produces [COMPANY_2].
    """
    normalized = normalize_entity_type(entity_type)
    pattern = re.compile(rf"^\[{re.escape(normalized)}_(\d+)\]$")
    max_seen = 0
    for term in existing_terms:
        match = pattern.match(term.replacement)
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"[{normalized}_{max_seen + 1}]"


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@dataclass(slots=True)
class RedactionTerm:
    """A local custom redaction term and its variants."""

    term_id: str
    entity_type: str
    original: str
    replacement: str
    variants: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.entity_type = normalize_entity_type(self.entity_type)
        self.original = self.original.strip()
        self.replacement = self.replacement.strip()
        self.variants = _dedupe_preserving_order(self.variants)
        self.notes = self.notes.strip()
        if not self.original:
            raise ValueError("original term is required")
        if not self.replacement:
            raise ValueError("replacement label is required")

    def match_values(self) -> list[str]:
        """Return original plus variants for deterministic custom-term matching."""
        return _dedupe_preserving_order([self.original, *self.variants])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RedactionTerm":
        return cls(
            term_id=data["term_id"],
            entity_type=data["entity_type"],
            original=data["original"],
            replacement=data["replacement"],
            variants=list(data.get("variants", [])),
            notes=data.get("notes", ""),
            created_at=data.get("created_at") or _utc_now_iso(),
            updated_at=data.get("updated_at") or _utc_now_iso(),
        )


@dataclass(slots=True)
class RedactionProfile:
    """A local redaction profile for a synthetic/client/job/matter workflow."""

    profile_id: str
    profile_name: str
    terms: list[RedactionTerm] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.profile_name = self.profile_name.strip()
        if not self.profile_name:
            raise ValueError("profile_name is required")

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terms": [term.to_dict() for term in self.terms],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RedactionProfile":
        return cls(
            profile_id=data["profile_id"],
            profile_name=data["profile_name"],
            terms=[RedactionTerm.from_dict(item) for item in data.get("terms", [])],
            created_at=data.get("created_at") or _utc_now_iso(),
            updated_at=data.get("updated_at") or _utc_now_iso(),
        )


@dataclass(frozen=True, slots=True)
class CustomTermMatch:
    """A text-level match result usable by detector and UI orchestration code."""

    profile_id: str
    term_id: str
    entity_type: str
    text: str
    proposed_replacement: str
    start: int
    end: int
    variant_of: str


class RedactionProfileStore:
    """CRUD service for local JSON-backed custom redaction profiles."""

    def __init__(self, storage_dir: str | Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[RedactionProfile]:
        profiles = []
        for path in sorted(self.storage_dir.glob("*.json")):
            profiles.append(self._load_path(path))
        return sorted(profiles, key=lambda profile: profile.profile_id)

    def get_profile(self, profile_id: str) -> RedactionProfile | None:
        path = self._profile_path(profile_id)
        if not path.exists():
            return None
        return self._load_path(path)

    def create_profile(self, profile_name: str) -> RedactionProfile:
        if self._profile_name_exists(profile_name):
            raise DuplicateProfileNameError(f"profile name already exists: {profile_name}")
        now = _utc_now_iso()
        profile = RedactionProfile(
            profile_id=_new_id("profile"),
            profile_name=profile_name,
            created_at=now,
            updated_at=now,
        )
        self._save(profile)
        return profile

    def update_profile(self, profile_id: str, *, profile_name: str | None = None) -> RedactionProfile:
        profile = self._require_profile(profile_id)
        if profile_name is not None:
            if self._profile_name_exists(profile_name, excluding_profile_id=profile_id):
                raise DuplicateProfileNameError(f"profile name already exists: {profile_name}")
            profile.profile_name = profile_name.strip()
            if not profile.profile_name:
                raise ValueError("profile_name is required")
        profile.updated_at = _utc_now_iso()
        self._save(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        path = self._profile_path(profile_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def add_term(
        self,
        profile_id: str,
        *,
        original: str,
        entity_type: str,
        replacement: str | None = None,
        variants: Iterable[str] | None = None,
        notes: str = "",
    ) -> RedactionTerm:
        profile = self._require_profile(profile_id)
        normalized_entity = normalize_entity_type(entity_type)
        term = RedactionTerm(
            term_id=_new_id("term"),
            entity_type=normalized_entity,
            original=original,
            replacement=replacement or next_placeholder(normalized_entity, profile.terms),
            variants=list(variants or []),
            notes=notes,
        )
        profile.terms.append(term)
        profile.updated_at = _utc_now_iso()
        self._save(profile)
        return term

    def update_term(
        self,
        profile_id: str,
        term_id: str,
        *,
        original: str | None = None,
        entity_type: str | None = None,
        replacement: str | None = None,
        variants: Iterable[str] | None = None,
        notes: str | None = None,
    ) -> RedactionTerm:
        profile = self._require_profile(profile_id)
        term = self._require_term(profile, term_id)
        if original is not None:
            term.original = original.strip()
        if entity_type is not None:
            term.entity_type = normalize_entity_type(entity_type)
        if replacement is not None:
            term.replacement = replacement.strip()
        if variants is not None:
            term.variants = _dedupe_preserving_order(variants)
        if notes is not None:
            term.notes = notes.strip()
        term.__post_init__()
        term.updated_at = _utc_now_iso()
        profile.updated_at = _utc_now_iso()
        self._save(profile)
        return term

    def delete_term(self, profile_id: str, term_id: str) -> bool:
        profile = self._require_profile(profile_id)
        original_len = len(profile.terms)
        profile.terms = [term for term in profile.terms if term.term_id != term_id]
        if len(profile.terms) == original_len:
            return False
        profile.updated_at = _utc_now_iso()
        self._save(profile)
        return True

    def find_text_matches(self, profile_id: str, text: str, *, case_sensitive: bool = False) -> list[CustomTermMatch]:
        """Find original/variant text matches for detector orchestration.

        This intentionally operates on plain text only; PDF page coordinates are
        the responsibility of detector code that can map text back to pages.
        """
        profile = self._require_profile(profile_id)
        flags = 0 if case_sensitive else re.IGNORECASE
        candidates: list[CustomTermMatch] = []
        for term in profile.terms:
            for value in term.match_values():
                pattern = re.compile(re.escape(value), flags)
                for match in pattern.finditer(text):
                    candidates.append(
                        CustomTermMatch(
                            profile_id=profile.profile_id,
                            term_id=term.term_id,
                            entity_type=term.entity_type,
                            text=match.group(0),
                            proposed_replacement=term.replacement,
                            start=match.start(),
                            end=match.end(),
                            variant_of=term.original,
                        )
                    )

        matches: list[CustomTermMatch] = []
        for candidate in sorted(candidates, key=lambda item: (item.start, -(item.end - item.start), item.term_id)):
            if any(candidate.start < existing.end and existing.start < candidate.end for existing in matches):
                continue
            matches.append(candidate)
        return sorted(matches, key=lambda item: (item.start, item.end, item.term_id))

    def _profile_path(self, profile_id: str) -> Path:
        safe_id = Path(profile_id).name
        return self.storage_dir / f"{safe_id}.json"

    def _load_path(self, path: Path) -> RedactionProfile:
        with path.open("r", encoding="utf-8") as handle:
            return RedactionProfile.from_dict(json.load(handle))

    def _save(self, profile: RedactionProfile) -> None:
        path = self._profile_path(profile.profile_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(profile.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")

    def _require_profile(self, profile_id: str) -> RedactionProfile:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ProfileNotFoundError(f"profile not found: {profile_id}")
        return profile

    def _require_term(self, profile: RedactionProfile, term_id: str) -> RedactionTerm:
        for term in profile.terms:
            if term.term_id == term_id:
                return term
        raise TermNotFoundError(f"term not found: {term_id}")

    def _profile_name_exists(self, profile_name: str, *, excluding_profile_id: str | None = None) -> bool:
        wanted = profile_name.strip().casefold()
        return any(
            profile.profile_id != excluding_profile_id and profile.profile_name.casefold() == wanted
            for profile in self.list_profiles()
        )
