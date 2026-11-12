"""Tests for private master-resume path resolution and validation."""

import pytest

from applypilot import config


def test_resume_path_prefers_existing_legacy_txt(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_RESUME_PATH", raising=False)
    legacy = tmp_path / "resume.txt"
    markdown = tmp_path / "master_cv.md"
    legacy.write_text("legacy CV", encoding="utf-8")
    markdown.write_text("# master CV", encoding="utf-8")

    assert config.resolve_resume_path(tmp_path) == legacy


def test_resume_path_falls_back_to_master_markdown(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_RESUME_PATH", raising=False)
    markdown = tmp_path / "master_cv.md"
    markdown.write_text("# master CV", encoding="utf-8")

    assert config.resolve_resume_path(tmp_path) == markdown


def test_resume_path_returns_legacy_default_when_no_file_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_RESUME_PATH", raising=False)

    assert config.resolve_resume_path(tmp_path) == tmp_path / "resume.txt"


def test_resume_path_honours_explicit_environment_override(tmp_path, monkeypatch):
    override = tmp_path / "private" / "candidate.md"
    monkeypatch.setenv("APPLYPILOT_RESUME_PATH", str(override))

    assert config.resolve_resume_path(tmp_path) == override


def test_load_resume_text_reads_configured_path(tmp_path, monkeypatch):
    resume = tmp_path / "master_cv.md"
    resume.write_text("# Candidate\nSQL and Python", encoding="utf-8")
    monkeypatch.setattr(config, "RESUME_PATH", resume)

    assert config.load_resume_text() == "# Candidate\nSQL and Python"


def test_load_resume_text_rejects_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(config, "RESUME_PATH", missing)

    with pytest.raises(FileNotFoundError, match="Master resume not found"):
        config.load_resume_text()


def test_load_resume_text_rejects_empty_file(tmp_path, monkeypatch):
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    monkeypatch.setattr(config, "RESUME_PATH", empty)

    with pytest.raises(ValueError, match="is empty"):
        config.load_resume_text()


def test_load_resume_text_rejects_non_utf8_file(tmp_path, monkeypatch):
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe")
    monkeypatch.setattr(config, "RESUME_PATH", invalid)

    with pytest.raises(ValueError, match="not valid UTF-8"):
        config.load_resume_text()
