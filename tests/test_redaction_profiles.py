from pathlib import Path

import pytest

from src.redaction_profiles import (
    DuplicateProfileNameError,
    RedactionProfile,
    RedactionProfileStore,
    RedactionTerm,
)


def test_creates_profile_with_generated_typed_placeholder_and_persists(tmp_path: Path):
    store = RedactionProfileStore(tmp_path)

    profile = store.create_profile("Synthetic Alpha Matter")
    term = store.add_term(
        profile.profile_id,
        original="Synthetic Alpha Pty Ltd",
        entity_type="company",
        variants=["Synthetic Alpha", "SYNTHETIC ALPHA PTY LTD"],
    )

    assert term.replacement == "[COMPANY_1]"
    assert term.entity_type == "COMPANY"
    assert term.variants == ["Synthetic Alpha", "SYNTHETIC ALPHA PTY LTD"]

    reloaded = RedactionProfileStore(tmp_path).get_profile(profile.profile_id)
    assert reloaded.profile_name == "Synthetic Alpha Matter"
    assert reloaded.terms[0].original == "Synthetic Alpha Pty Ltd"
    assert reloaded.terms[0].replacement == "[COMPANY_1]"


def test_updates_profile_and_term_without_losing_variants(tmp_path: Path):
    store = RedactionProfileStore(tmp_path)
    profile = store.create_profile("Synthetic Alpha Matter")
    term = store.add_term(
        profile.profile_id,
        original="Synthetic Adviser One",
        entity_type="PERSON",
        variants=["S Adviser"],
        replacement="[ADVISER_1]",
    )

    updated_profile = store.update_profile(profile.profile_id, profile_name="Synthetic Beta Matter")
    updated_term = store.update_term(
        profile.profile_id,
        term.term_id,
        original="Synthetic Adviser Two",
        entity_type="person",
        variants=["Synthetic Adviser", "Adviser Two"],
        replacement="[PERSON_9]",
        notes="synthetic-only note",
    )

    assert updated_profile.profile_name == "Synthetic Beta Matter"
    assert updated_term.original == "Synthetic Adviser Two"
    assert updated_term.entity_type == "PERSON"
    assert updated_term.replacement == "[PERSON_9]"
    assert updated_term.variants == ["Synthetic Adviser", "Adviser Two"]
    assert updated_term.notes == "synthetic-only note"

    reloaded = RedactionProfileStore(tmp_path).get_profile(profile.profile_id)
    assert reloaded.terms[0].variants == ["Synthetic Adviser", "Adviser Two"]


def test_deletes_terms_and_profiles_from_local_json_storage(tmp_path: Path):
    store = RedactionProfileStore(tmp_path)
    profile = store.create_profile("Synthetic Delete Matter")
    term = store.add_term(profile.profile_id, original="Synthetic Trust", entity_type="TRUST")

    assert store.delete_term(profile.profile_id, term.term_id) is True
    assert store.get_profile(profile.profile_id).terms == []
    assert store.delete_term(profile.profile_id, term.term_id) is False

    profile_file = tmp_path / f"{profile.profile_id}.json"
    assert profile_file.exists()
    assert store.delete_profile(profile.profile_id) is True
    assert store.get_profile(profile.profile_id) is None
    assert not profile_file.exists()


def test_lists_profiles_and_rejects_duplicate_profile_names(tmp_path: Path):
    store = RedactionProfileStore(tmp_path)
    first = store.create_profile("Synthetic Shared Matter")

    with pytest.raises(DuplicateProfileNameError):
        store.create_profile("Synthetic Shared Matter")

    second = store.create_profile("Synthetic Other Matter")
    listed = store.list_profiles()

    assert [p.profile_id for p in listed] == sorted([first.profile_id, second.profile_id])


def test_redaction_term_matching_values_include_original_and_variants_without_duplicates():
    term = RedactionTerm(
        term_id="term-synthetic",
        entity_type="COMPANY",
        original="Synthetic Alpha Pty Ltd",
        replacement="[COMPANY_1]",
        variants=["Synthetic Alpha", "Synthetic Alpha Pty Ltd", "SYNTHETIC ALPHA"],
    )

    assert term.match_values() == [
        "Synthetic Alpha Pty Ltd",
        "Synthetic Alpha",
        "SYNTHETIC ALPHA",
    ]


def test_text_match_api_returns_detector_ready_matches_for_variants(tmp_path: Path):
    store = RedactionProfileStore(tmp_path)
    profile = store.create_profile("Synthetic Detector Matter")
    term = store.add_term(
        profile.profile_id,
        original="Synthetic Alpha Pty Ltd",
        entity_type="COMPANY",
        variants=["Synthetic Alpha"],
    )

    matches = store.find_text_matches(
        profile.profile_id,
        "Synthetic Alpha received a distribution from Synthetic Alpha Pty Ltd.",
    )

    assert [(m.term_id, m.text, m.entity_type, m.proposed_replacement, m.variant_of) for m in matches] == [
        (term.term_id, "Synthetic Alpha", "COMPANY", "[COMPANY_1]", "Synthetic Alpha Pty Ltd"),
        (term.term_id, "Synthetic Alpha Pty Ltd", "COMPANY", "[COMPANY_1]", "Synthetic Alpha Pty Ltd"),
    ]


def test_synthetic_fixture_profile_uses_profile_schema_only():
    fixture_path = Path("fixtures/redaction_profiles/synthetic_mvp_sample.json")
    profile = RedactionProfile.from_dict(__import__("json").loads(fixture_path.read_text()))

    assert profile.profile_id == "profile_synthetic_mvp_sample"
    assert [term.replacement for term in profile.terms] == ["[COMPANY_1]", "[TRUST_1]", "[CLIENT_ID_1]"]
    assert all("Synthetic" in term.original or term.original.startswith("SYN-") for term in profile.terms)
