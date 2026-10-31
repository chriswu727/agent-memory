from membrain import diagnostics, host


def test_doctor_does_not_create_an_installation(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")
    directory = tmp_path / "not-installed"
    result = diagnostics.diagnose(directory)
    assert not result["ok"] and not directory.exists()
    service = next(check for check in result["checks"] if check["name"] == "service")
    assert service["state"] == "error" and "plugin start" in service["fix"]


def test_doctor_reports_model_and_queue_recovery_without_printing_content(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "installation_status", lambda directory: {
        "ready": False, "state": "stopped", "semantic_recall": "not_downloaded", "pending_records": 3})
    result = diagnostics.diagnose(tmp_path)
    checks = {check["name"]: check for check in result["checks"]}
    assert not result["ok"]
    assert checks["queue"]["state"] == "warning" and "3 records" in checks["queue"]["detail"]
    assert "fetch-model" in checks["semantic_recall"]["fix"]
