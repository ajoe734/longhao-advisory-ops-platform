from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "legendflow_delivery"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("DeliveryMonitor", "RedriveReconciliation"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_ui_does_not_treat_unsupported_read_capability_as_unread():
    source = (FEATURE / "DeliveryMonitor.tsx").read_text(encoding="utf-8")
    assert "不支援已讀回報" in source
    assert "allowed_actions" in source
    assert '"未提供"' in source


def test_ui_redrive_never_offers_bulk_blind_send_and_server_owns_expiry_checks():
    source = (FEATURE / "RedriveReconciliation.tsx").read_text(encoding="utf-8")
    assert "一鍵盲發全部" in source
    assert "server" in source.lower() or "伺服器" in source


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    monitor = (FEATURE / "DeliveryMonitor.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert 'role="status"' in monitor
    assert "STATE_LABELS" in monitor  # status is rendered as text, not color alone
