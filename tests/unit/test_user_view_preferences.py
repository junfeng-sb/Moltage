"""Offline migration and atomic persistence of the accepted bond factor."""

import json

import pytest

from moltage.app.user_view_preferences import (
    PersistedOrbitalLighting,
    UserViewPreferencesError,
    UserViewPreferencesRepository,
)


@pytest.mark.parametrize("schema", [1, 2])
def test_legacy_view_preferences_load_default_factor_without_rewriting(tmp_path, schema):
    path = tmp_path / "view_preferences.json"
    raw = {
        "schema_version": schema,
        "orbital_lighting": {"ambient": 0.65, "light_intensity": 0.35},
    }
    if schema == 2:
        raw["theme_id"] = "event_horizon"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    repository = UserViewPreferencesRepository(path)
    assert repository.load_bond_threshold_factor() == 1.1
    assert repository.load().ambient == 0.65
    assert path.read_bytes() == before
    repository.save_bond_threshold_factor(1.25)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 3
    assert saved["bond_threshold_factor"] == 1.25
    assert repository.load().ambient == 0.65
    assert repository.load_theme_id() == ("event_horizon" if schema == 2 else None)


def test_independent_preference_saves_preserve_all_other_values(tmp_path):
    repository = UserViewPreferencesRepository(tmp_path / "view_preferences.json")
    assert repository.load_bond_threshold_factor() == 1.1
    lighting = PersistedOrbitalLighting(0.65, 0.35, 0.55, 48.0)
    repository.save(lighting)
    repository.save_theme_id("event_horizon")
    repository.save_bond_threshold_factor(1.25)
    assert repository.load() == lighting
    assert repository.load_theme_id() == "event_horizon"
    repository.save(PersistedOrbitalLighting())
    repository.save_theme_id("orbital")
    assert repository.load_bond_threshold_factor() == 1.25
    assert repository.load_theme_id() == "orbital"


@pytest.mark.parametrize("value", [True, "1.25", None, 0, 10.01, float("inf"), float("nan"), 10**400, -(10**400)])
def test_invalid_bond_factor_never_overwrites_existing_preferences(tmp_path, value):
    repository = UserViewPreferencesRepository(tmp_path / "view_preferences.json")
    repository.save_bond_threshold_factor(1.25)
    before = repository.path.read_bytes()
    with pytest.raises(ValueError, match="bond threshold factor"):
        repository.save_bond_threshold_factor(value)
    assert repository.path.read_bytes() == before


@pytest.mark.parametrize("value", [None, "1.2", -1, float("nan"), 10**400, -(10**400)])
def test_invalid_schema_3_factor_is_reported_not_silently_reset(tmp_path, value):
    repository = UserViewPreferencesRepository(tmp_path / "view_preferences.json")
    repository.save_bond_threshold_factor(1.25)
    raw = json.loads(repository.path.read_text(encoding="utf-8"))
    raw["bond_threshold_factor"] = value
    repository.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(UserViewPreferencesError, match="malformed"):
        repository.load_bond_threshold_factor()


def test_missing_schema_3_factor_is_reported(tmp_path):
    repository = UserViewPreferencesRepository(tmp_path / "view_preferences.json")
    repository.save(PersistedOrbitalLighting())
    raw = json.loads(repository.path.read_text(encoding="utf-8"))
    del raw["bond_threshold_factor"]
    repository.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(UserViewPreferencesError, match="malformed"):
        repository.load_bond_threshold_factor()


def test_failed_atomic_save_preserves_prior_preferences(tmp_path, monkeypatch):
    repository = UserViewPreferencesRepository(tmp_path / "view_preferences.json")
    repository.save_bond_threshold_factor(1.25)
    before = repository.path.read_bytes()

    def fail_replace(*_args):
        raise OSError("synthetic permission failure")

    monkeypatch.setattr("moltage.app.user_view_preferences.os.replace", fail_replace)
    with pytest.raises(UserViewPreferencesError, match="could not be persisted"):
        repository.save_bond_threshold_factor(1.5)
    assert repository.path.read_bytes() == before
    assert not repository.path.with_name(repository.path.name + ".tmp").exists()
