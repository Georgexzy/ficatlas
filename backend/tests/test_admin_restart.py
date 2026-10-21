import time
import stat
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api import admin
from api import settings


def test_restart_request_round_trips_atomically(tmp_path):
    request = {
        "id": "request-1",
        "restart_at": 1_900_000_000,
        "announcement": "FicAtlas will be back soon.",
    }
    admin._write_restart(tmp_path, request)
    assert admin._read_restart(tmp_path) == request
    assert stat.S_IMODE((tmp_path / "restart.json").stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["restart.json"]


def test_invalid_restart_request_is_ignored(tmp_path):
    (tmp_path / "restart.json").write_text("[]")
    assert admin._read_restart(tmp_path) is None


@pytest.fixture
def memory_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(settings, "_table_ready", False)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_restart_announces_then_cancel_restores_previous_notice(memory_db, tmp_path, monkeypatch):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    (tmp_path / "bridge.ready").write_text(admin._RESTART_BRIDGE_MARKER)
    monkeypatch.setattr(admin, "_wait_for_bridge_ack", lambda *_args: True)
    settings.put_setting(memory_db, "site_notice", "Archive maintenance today.")
    settings.put_setting(memory_db, "site_notice_until", "")

    scheduled = admin.schedule_restart(admin.RestartRequest(confirm=True), memory_db, None)
    assert scheduled["pending"]["restart_at"] >= time.time() + 299
    assert "restart in five minutes" in settings.get_setting(memory_db, "site_notice")

    cancelled = admin.cancel_restart(memory_db, None)
    assert cancelled["ok"]
    assert cancelled["site_notice"] == "Archive maintenance today."
    assert not (tmp_path / "restart.json").exists()


def test_scheduled_restart_publishes_notice_five_minutes_before(monkeypatch, memory_db, tmp_path):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    (tmp_path / "bridge.ready").write_text(admin._RESTART_BRIDGE_MARKER)
    monkeypatch.setattr(admin, "_wait_for_bridge_ack", lambda *_args: True)
    target = time.time() + 3600
    result = admin.schedule_restart(
        admin.RestartRequest(confirm=True, restart_at=target), memory_db, None)
    pending = result["pending"]
    assert abs(pending["restart_at"] - target) < 1
    start = datetime.fromisoformat(pending["announcement_start_at"]).timestamp()
    assert abs(start - (pending["restart_at"] - 300)) < 1
    assert admin.restart_status(None, memory_db)["notice_status"] == "scheduled"
    settings.put_setting(memory_db, "site_notice", "")
    assert admin.restart_status(None, memory_db)["notice_status"] == "missing"


def test_restart_must_be_at_least_five_minutes_away(memory_db, tmp_path, monkeypatch):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    (tmp_path / "bridge.ready").write_text(admin._RESTART_BRIDGE_MARKER)
    with pytest.raises(HTTPException) as exc:
        admin.schedule_restart(
            admin.RestartRequest(confirm=True, restart_at=time.time() + 60), memory_db, None)
    assert exc.value.status_code == 400


def test_restart_refuses_when_host_bridge_is_missing(memory_db, tmp_path, monkeypatch):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        admin.schedule_restart(admin.RestartRequest(confirm=True), memory_db, None)
    assert exc.value.status_code == 503


def test_restart_refuses_old_bridge_without_erasing_notice(memory_db, tmp_path, monkeypatch):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    (tmp_path / "bridge.ready").write_text("ready\n")
    with pytest.raises(HTTPException) as exc:
        admin.schedule_restart(admin.RestartRequest(confirm=True), memory_db, None)
    assert exc.value.status_code == 503
    assert not (tmp_path / "restart.json").exists()
    assert settings.get_setting(memory_db, "site_notice") == ""


def test_restart_without_bridge_ack_rolls_back_notice(memory_db, tmp_path, monkeypatch):
    monkeypatch.setenv("FICATLAS_RESTART_DIR", str(tmp_path))
    (tmp_path / "bridge.ready").write_text(admin._RESTART_BRIDGE_MARKER)
    monkeypatch.setattr(admin, "_wait_for_bridge_ack", lambda *_args: False)
    settings.put_setting(memory_db, "site_notice", "Previous notice")
    with pytest.raises(HTTPException) as exc:
        admin.schedule_restart(admin.RestartRequest(confirm=True), memory_db, None)
    assert exc.value.status_code == 503
    assert not (tmp_path / "restart.json").exists()
    assert settings.get_setting(memory_db, "site_notice") == "Previous notice"
