import ao3_budget
import ao3_import


def test_interactive_wait_declines_a_far_future_shared_slot(monkeypatch):
    budget = ao3_budget._Budget(host="archiveofourown.org")
    monkeypatch.setattr(ao3_budget, "_claim_shared_slot", lambda *_: 45.0)

    assert budget.wait(max_wait_s=2.0) is False


def test_normal_wait_still_grants_a_nearby_slot(monkeypatch):
    budget = ao3_budget._Budget(host="archiveofourown.org")
    monkeypatch.setattr(ao3_budget, "_claim_shared_slot", lambda *_: 0.0)

    assert budget.wait(max_wait_s=2.0) is True


def test_direct_reader_fetch_skips_when_shared_budget_is_busy(monkeypatch):
    monkeypatch.setattr(ao3_import.ao3_budget, "wait", lambda **_: False)

    assert ao3_import.fetch_ao3_full_work(
        "https://archiveofourown.org/works/123456") is None
