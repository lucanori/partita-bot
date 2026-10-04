import ast
import signal
import subprocess
from pathlib import Path

import pytest

import scripts.verify_container as verify

STDLIB_TOP_LEVEL_IMPORTS = {
    "__future__",
    "argparse",
    "base64",
    "datetime",
    "http",
    "json",
    "math",
    "os",
    "re",
    "signal",
    "subprocess",
    "sys",
    "time",
    "uuid",
}

DOCKERIGNORE_PATH = Path(verify.__file__).resolve().parents[1] / ".dockerignore"
REQUIRED_DOCKERIGNORE_PATTERNS = {
    ".git",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "!.env.example",
    "data",
    "**/*.sqlite*",
    ".coverage",
    "**/.coverage",
    "htmlcov",
    "**/htmlcov",
    "**/status",
    ".venv",
    "__pycache__",
    "**/__pycache__",
    ".pytest_cache",
    "**/.pytest_cache",
    ".ruff_cache",
    "**/.ruff_cache",
    "tmp",
}


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.build_code = 0
        self.run_code = 0
        self.wait_code = 0
        self.wait_output = "0\n"
        self.wait_timeout = False
        self.worker_logs_code = 0
        self.admin_logs_code = 0
        self.probe_code = 0
        self.probe_output = verify.ADMIN_OK_MARKER + " status=200\n"
        self.readiness_failures = 0
        self.worker_logs = verify.WORKER_OK_MARKER + " {}\n"
        self.admin_logs = "Starting Admin interface...\n[INFO] Booting worker with pid: 7\n"
        self.stop_code = 0
        self.stop_error = "stop failed\n"
        self.rm_code = 0
        self.rmi_code = 0
        self.rm_error = "no such container"
        self.rmi_error = "no such image"
        self.missing = False
        self.interrupt_on = None
        self.sigterm_on = None

    def __call__(self, command, capture_output=True, text=True, timeout=None):
        if self.missing:
            raise FileNotFoundError(command[0])
        self.calls.append(list(command))
        action = command[1]
        if action == self.interrupt_on:
            raise KeyboardInterrupt
        if action == self.sigterm_on:
            signal.raise_signal(signal.SIGTERM)
        if action == "build":
            return self.completed(command, self.build_code, "", "build failed\n")
        if action == "run":
            return self.completed(command, self.run_code, "container-id\n", "run failed\n")
        if action == "wait":
            if self.wait_timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            return self.completed(command, self.wait_code, self.wait_output, "")
        if action == "logs":
            worker = "verify-bot-" in command[2]
            code = self.worker_logs_code if worker else self.admin_logs_code
            output = self.worker_logs if worker else self.admin_logs
            return self.completed(command, code, output, "logs failed\n" if code else "")
        if action == "exec":
            if verify.ADMIN_PROBE_FLAG in command:
                return self.completed(command, self.probe_code, self.probe_output, "")
            if self.readiness_failures > 0:
                self.readiness_failures -= 1
                return self.completed(command, 1, "", "")
            return self.completed(command, 0, "", "")
        if action == "stop":
            return self.completed(command, self.stop_code, "", self.stop_error)
        if action == "rm":
            return self.completed(command, self.rm_code, "", self.rm_error)
        if action == "rmi":
            return self.completed(command, self.rmi_code, "", self.rmi_error)
        raise AssertionError(command)

    @staticmethod
    def completed(command, code, stdout, stderr):
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def install(monkeypatch, fake):
    monkeypatch.setattr(verify.subprocess, "run", fake)


def actions(fake):
    return [call[1] for call in fake.calls]


def tmpfs_mounts(call):
    mounts = {}
    for index, token in enumerate(call):
        if token == "--tmpfs":
            path, _, options = call[index + 1].partition(":")
            mounts[path] = set(options.split(","))
    return mounts


def test_invalid_runtime_is_rejected():
    with pytest.raises(SystemExit) as excinfo:
        verify.main(["--runtime", "invalid"])
    assert excinfo.value.code == 2


def test_internal_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as excinfo:
        verify.main(["--worker-smoke", "--admin-probe"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("flag", ["--timeout", "--ready-timeout"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_timeouts_are_rejected(flag, value):
    with pytest.raises(SystemExit) as excinfo:
        verify.main(["--runtime", "podman", flag, value])
    assert excinfo.value.code == 2


def test_module_top_level_imports_are_stdlib_only():
    tree = ast.parse(Path(verify.__file__).read_text())
    modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert modules <= STDLIB_TOP_LEVEL_IMPORTS


def test_dockerignore_excludes_credentials_data_and_caches():
    lines = {
        line.strip()
        for line in DOCKERIGNORE_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert REQUIRED_DOCKERIGNORE_PATTERNS <= lines


@pytest.mark.parametrize("runtime", ["podman", "docker"])
def test_success_lifecycle_flags_environment_and_cleanup(monkeypatch, capsys, runtime):
    fake = FakeRuntime()
    install(monkeypatch, fake)
    code = verify.main(["--runtime", runtime, "--timeout", "1", "--ready-timeout", "1"])
    assert code == 0
    assert actions(fake).count("build") == 1
    assert actions(fake).count("run") == 2
    assert actions(fake).count("wait") == 1
    assert actions(fake).count("logs") == 2
    assert actions(fake).count("stop") == 1
    assert actions(fake).count("rm") == 2
    assert actions(fake)[-1] == "rmi"
    captured_output = capsys.readouterr().out
    assert verify.HOST_OK_MARKER in captured_output

    run_calls = [call for call in fake.calls if call[1] == "run"]
    for call in run_calls:
        assert call[0] == runtime
        joined = " ".join(call)
        for flag in (
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--memory 512m",
            "--pids-limit 256",
            "--cpus 1",
        ):
            assert flag in joined
        mounts = tmpfs_mounts(call)
        assert mounts["/tmp"] == {"rw", "nosuid", "nodev", "noexec", "size=64m", "mode=1777"}
        assert mounts["/app/data"] == {"rw", "nosuid", "nodev", "size=64m", "mode=1777"}
        assert "uid=" not in joined
        assert "gid=" not in joined
        assert "--publish" not in call
        assert "--volume" not in call
        assert call[-1].startswith("partita-bot-verify:")

    env = {}
    for call in fake.calls:
        for index, token in enumerate(call):
            if token == "-e":
                key, _, value = call[index + 1].partition("=")
                assert "=" in call[index + 1], call
                env[key] = value
    assert env["PARTITA_SKIP_DOTENV"] == "true"
    assert env["FOOTBALL_API_TOKEN"] == ""
    assert env["TELEGRAM_BOT_TOKEN"].startswith("verify-fake-telegram-")
    assert env["EXA_API_KEY"].startswith("verify-fake-exa-")
    assert env["ADMIN_USERNAME"] == "verify-admin"
    assert env["ADMIN_PASSWORD"].startswith("verify-password-")
    assert env["FLASK_SECRET_KEY"]
    assert env["SERVICE_TYPE"] == "admin"

    bot_call = next(call for call in run_calls if "SERVICE_TYPE=bot" in call)
    admin_call = next(call for call in run_calls if "SERVICE_TYPE=admin" in call)
    assert "BOT_COMMAND=python -m scripts.verify_container --worker-smoke" in bot_call
    assert not any(token.startswith("BOT_COMMAND=") for token in admin_call)

    exec_calls = [call for call in fake.calls if call[1] == "exec"]
    assert any(verify.ADMIN_PROBE_FLAG in call for call in exec_calls)
    assert any("-c" in call for call in exec_calls)


def test_build_failure_skips_containers_and_cleans_image(monkeypatch):
    fake = FakeRuntime()
    fake.build_code = 1
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "docker"]) == 1
    assert fake.calls[0][0] == "docker"
    assert "run" not in actions(fake)
    assert actions(fake)[-1] == "rmi"


def test_build_failure_tolerates_missing_image(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.build_code = 1
    fake.rmi_code = 1
    fake.rmi_error = "no such image"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "docker"]) == 1
    assert actions(fake)[-1] == "rmi"
    assert verify.TEARDOWN_MARKER not in capsys.readouterr().err


def test_container_start_failure_cleans_up(monkeypatch):
    fake = FakeRuntime()
    fake.run_code = 1
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"


def test_container_start_failure_tolerates_missing_container(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.run_code = 1
    fake.rm_code = 1
    fake.rm_error = "no such container"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"
    assert verify.TEARDOWN_MARKER not in capsys.readouterr().err


def test_missing_created_container_during_teardown_fails(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.rm_code = 1
    fake.rm_error = "no such container"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1
    captured = capsys.readouterr()
    assert verify.TEARDOWN_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


def test_worker_failure_reports_traceback_and_skips_admin(monkeypatch):
    fake = FakeRuntime()
    fake.wait_code = 1
    fake.worker_logs = "Traceback (most recent call last):\nAssertionError: boom\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("run") == 1
    assert actions(fake)[-1] == "rmi"


def test_worker_wait_container_exit_nonzero_fails(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.wait_code = 0
    fake.wait_output = "1\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("run") == 1
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"
    captured = capsys.readouterr()
    assert verify.FAILURE_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


@pytest.mark.parametrize("output", ["", "\n", "unknown\n", "0.0\n", "  \n"])
def test_worker_wait_malformed_output_fails(monkeypatch, output):
    fake = FakeRuntime()
    fake.wait_output = output
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("run") == 1
    assert actions(fake)[-1] == "rmi"


def test_worker_error_log_fails(monkeypatch):
    fake = FakeRuntime()
    fake.worker_logs = verify.WORKER_OK_MARKER + "\n2026-01-01 - root - ERROR - boom\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1


def test_worker_missing_marker_fails(monkeypatch):
    fake = FakeRuntime()
    fake.worker_logs = "worker finished without marker\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1


def test_worker_logs_command_failure_fails_and_cleans_up(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.worker_logs_code = 1
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake).count("run") == 1
    assert actions(fake).count("stop") == 0
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"
    captured = capsys.readouterr()
    assert verify.FAILURE_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


def test_worker_wait_timeout_fails_and_cleans_up(monkeypatch):
    fake = FakeRuntime()
    fake.wait_timeout = True
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--timeout", "0.1"]) == 1
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"


def test_admin_readiness_timeout_fails_before_probe(monkeypatch):
    fake = FakeRuntime()
    fake.readiness_failures = 10**9
    install(monkeypatch, fake)
    code = verify.main(["--runtime", "podman", "--ready-timeout", "0.05", "--timeout", "1"])
    assert code == 1
    assert not any(verify.ADMIN_PROBE_FLAG in call for call in fake.calls)
    assert actions(fake)[-1] == "rmi"


def test_admin_probe_failure_cleans_up(monkeypatch):
    fake = FakeRuntime()
    fake.probe_code = 1
    fake.probe_output = "AssertionError: 500\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1
    assert actions(fake).count("stop") == 0
    assert actions(fake)[-1] == "rmi"


def test_admin_probe_missing_marker_fails(monkeypatch):
    fake = FakeRuntime()
    fake.probe_output = "probe completed without marker\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1


def test_admin_logs_command_failure_fails_and_cleans_up(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.admin_logs_code = 1
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1
    assert actions(fake).count("stop") == 1
    assert actions(fake).count("rm") == 2
    assert actions(fake)[-1] == "rmi"
    captured = capsys.readouterr()
    assert verify.FAILURE_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


def test_admin_stop_failure_fails_and_still_collects_logs(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.stop_code = 1
    fake.stop_error = "Error: stop failed\n"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1
    captured = capsys.readouterr()
    assert "Booting worker" in captured.out
    assert verify.FAILURE_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out
    assert actions(fake).count("rm") == 2
    assert actions(fake)[-1] == "rmi"


def test_teardown_failure_propagates(monkeypatch):
    fake = FakeRuntime()
    fake.rmi_code = 1
    fake.rmi_error = "permission denied"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1


def test_missing_image_after_successful_build_fails(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.rmi_code = 1
    fake.rmi_error = "no such image"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman", "--ready-timeout", "1", "--timeout", "1"]) == 1
    captured = capsys.readouterr()
    assert verify.TEARDOWN_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


def test_missing_runtime_fails(monkeypatch):
    fake = FakeRuntime()
    fake.missing = True
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "docker"]) == 1


def test_keyboard_interrupt_cleans_up(monkeypatch):
    fake = FakeRuntime()
    fake.interrupt_on = "run"
    install(monkeypatch, fake)
    assert verify.main(["--runtime", "podman"]) == 1
    assert actions(fake)[-1] == "rmi"


def test_sigterm_cleans_up_and_restores_handler(monkeypatch, capsys):
    fake = FakeRuntime()
    fake.sigterm_on = "run"
    install(monkeypatch, fake)
    previous = signal.getsignal(signal.SIGTERM)
    assert verify.main(["--runtime", "podman"]) == 1
    assert signal.getsignal(signal.SIGTERM) == previous
    assert actions(fake).count("run") == 1
    assert actions(fake).count("rm") == 1
    assert actions(fake)[-1] == "rmi"
    captured = capsys.readouterr()
    assert verify.FAILURE_MARKER in captured.err
    assert verify.HOST_OK_MARKER not in captured.out


def test_worker_smoke_runs_offline(capsys):
    assert verify.worker_smoke() == 0
    captured = capsys.readouterr()
    assert verify.WORKER_OK_MARKER in captured.out


def test_worker_smoke_rejects_unexpected_network(monkeypatch, capsys):
    from partita_bot.event_fetcher import EventFetcher

    def network_leak(self, city, target_date=None):
        self.session.post("https://example.invalid/verify", json={})

    monkeypatch.setattr(EventFetcher, "fetch_event_message", network_leak)
    with pytest.raises(verify.NetworkAccessError):
        verify.worker_smoke()
    assert verify.WORKER_OK_MARKER not in capsys.readouterr().out


def test_worker_smoke_propagates_persistence_failure(monkeypatch, capsys):
    from partita_bot.storage import Database

    def broken_queue(self, telegram_id, rich_msg):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Database, "queue_rich_message", broken_queue)
    with pytest.raises(RuntimeError, match="database unavailable"):
        verify.worker_smoke()
    assert verify.WORKER_OK_MARKER not in capsys.readouterr().out
