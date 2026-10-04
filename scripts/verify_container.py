from __future__ import annotations

import argparse
import base64
import http.client
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime

WORKER_SMOKE_FLAG = "--worker-smoke"
ADMIN_PROBE_FLAG = "--admin-probe"
WORKER_OK_MARKER = "VERIFY_WORKER_SMOKE_OK"
ADMIN_OK_MARKER = "VERIFY_ADMIN_PROBE_OK"
HOST_OK_MARKER = "VERIFY_CONTAINER_OK"
FAILURE_MARKER = "VERIFY_CONTAINER_FAILED"
TEARDOWN_MARKER = "VERIFY_CONTAINER_TEARDOWN_ERROR"
LOG_ERROR_PATTERN = re.compile(r"\b(?:CRITICAL|ERROR|FATAL)\b")
MISSING_RESOURCE_PATTERNS = (
    "no such container",
    "no such image",
    "no such object",
    "image not known",
    "not found: image",
    "no container with name or id",
    "unable to find image",
)
DOCKERFILE = "Dockerfile"
ADMIN_PORT = "5000"
TMPFS_TMP = "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777"
TMPFS_DATA = "/app/data:rw,nosuid,nodev,size=64m,mode=1777"
READINESS_SCRIPT = (
    "import socket,sys;"
    "s=socket.create_connection(('127.0.0.1',int(sys.argv[1])),2);s.close()"
)


class VerificationError(RuntimeError):
    pass


class NetworkAccessError(RuntimeError):
    pass


class RecordingDelivery:
    def __init__(self) -> None:
        self.deliveries: list[dict] = []

    def send_message_sync(self, chat_id, text, **kwargs):
        self.deliveries.append({"chat_id": chat_id, "text": text, **kwargs})
        return (True, None, 500 + len(self.deliveries))


class NoNetworkSession:
    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, name):
        def reject(*args, **kwargs):
            self.calls += 1
            raise NetworkAccessError(f"unexpected network call: {name}")

        return reject


def out(message):
    sys.stdout.write(message)


def err(message):
    sys.stderr.write(message)


def run(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise VerificationError(f"runtime executable not found: {command[0]}") from exc


def require_success(result, action):
    if result.returncode == 0:
        return
    lines = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
    detail = lines[-1] if lines else "no output"
    raise VerificationError(f"{action} failed with exit code {result.returncode}: {detail}")


def wait_exit_code(name, result):
    output = (result.stdout or "").strip()
    code = output[1:] if output.startswith("+") else output
    if not (code.isascii() and code.isdigit()):
        raise VerificationError(f"container {name} exit code missing or malformed: {output!r}")
    exit_code = int(code)
    if exit_code != 0:
        raise VerificationError(f"container {name} exited with code {exit_code}")


def resource_missing(text):
    lowered = text.lower()
    return any(pattern in lowered for pattern in MISSING_RESOURCE_PATTERNS)


def sigterm_interrupt(signum, frame):
    raise VerificationError("received SIGTERM")


def install_sigterm_handler():
    try:
        previous = signal.getsignal(signal.SIGTERM)
    except (ValueError, OSError):
        return None
    try:
        signal.signal(signal.SIGTERM, sigterm_interrupt)
    except (ValueError, OSError):
        return None
    return previous if previous is not None else signal.SIG_DFL


def restore_sigterm_handler(previous):
    if previous is None:
        return
    try:
        signal.signal(signal.SIGTERM, previous)
    except (ValueError, OSError):
        pass


def positive_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid timeout: {value}") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


def fake_environment():
    suffix = uuid.uuid4().hex
    return {
        "ADMIN_PASSWORD": f"verify-password-{suffix}",
        "ADMIN_PORT": ADMIN_PORT,
        "ADMIN_USERNAME": "verify-admin",
        "BOT_LANGUAGE": "Italian",
        "DEBUG": "false",
        "EXA_API_KEY": f"verify-fake-exa-{suffix}",
        "FLASK_SECRET_KEY": uuid.uuid4().hex,
        "FOOTBALL_API_TOKEN": "",
        "PARTITA_SKIP_DOTENV": "true",
        "TELEGRAM_BOT_TOKEN": f"verify-fake-telegram-{suffix}",
        "TIMEZONE": "Europe/Rome",
    }


def start(runtime, name, image, environment, timeout):
    command = [
        runtime, "run", "-d", "--name", name, "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "512m", "--pids-limit", "256", "--cpus", "1",
        "--tmpfs", TMPFS_TMP, "--tmpfs", TMPFS_DATA,
    ]
    for key, value in environment.items():
        command += ["-e", f"{key}={value}"]
    command.append(image)
    require_success(run(command, timeout), f"container start {name}")


def read_logs(runtime, name, timeout):
    result = run([runtime, "logs", name], timeout)
    require_success(result, f"container logs {name}")
    output = (result.stdout or "") + (result.stderr or "")
    return output if not output or output.endswith("\n") else output + "\n"


def check_logs(name, output):
    if "Traceback (most recent call last)" in output:
        raise VerificationError(f"{name} logs contain a traceback")
    match = LOG_ERROR_PATTERN.search(output)
    if match:
        raise VerificationError(f"{name} logs contain {match.group(0)} output")


def capture_logs(runtime, name, timeout):
    output = read_logs(runtime, name, timeout)
    check_logs(name, output)
    return output


def wait_for_admin(runtime, name, timeout, deadline):
    while True:
        command = [runtime, "exec", name, "python", "-c", READINESS_SCRIPT, ADMIN_PORT]
        if run(command, timeout).returncode == 0:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError(f"container {name} did not become ready before timeout")
        time.sleep(min(1.0, remaining))


def teardown(runtime, image, attempted, created):
    errors = []
    order = [label for label in attempted if label != image]
    if image in attempted:
        order.append(image)
    for label in order:
        if label == image:
            command = [runtime, "rmi", "-f", image]
            timeout = 300.0
        else:
            command = [runtime, "rm", "-f", label]
            timeout = 120.0
        try:
            result = run(command, timeout)
        except (VerificationError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            continue
        if result.returncode == 0:
            continue
        text = (result.stderr or "") + (result.stdout or "")
        if resource_missing(text):
            if label in created:
                errors.append(f"{label} was missing during teardown")
            continue
        lines = text.strip().splitlines()
        detail = lines[-1] if lines else "no output"
        errors.append(f"failed to remove {label}: {detail}")
    return errors


def run_host(args):
    suffix = uuid.uuid4().hex[:12]
    image = f"partita-bot-verify:{suffix}"
    bot = f"partita-bot-verify-bot-{suffix}"
    admin = f"partita-bot-verify-admin-{suffix}"
    env = fake_environment()
    bot_env = dict(
        env, SERVICE_TYPE="bot",
        BOT_COMMAND="python -m scripts.verify_container --worker-smoke",
    )
    attempted: list[str] = []
    created: set[str] = set()
    containers: list[str] = []
    captured: dict[str, str] = {}
    errors: list[str] = []
    failure = None
    previous_sigterm = None
    try:
        previous_sigterm = install_sigterm_handler()
        out(f"[verify] building image {image} with {args.runtime}\n")
        attempted.append(image)
        require_success(
            run([args.runtime, "build", "-t", image, "-f", DOCKERFILE, "."], args.timeout),
            "container build",
        )
        created.add(image)
        out(f"[verify] starting worker container {bot}\n")
        attempted.append(bot)
        containers.append(bot)
        start(args.runtime, bot, image, bot_env, args.timeout)
        created.add(bot)
        wait = run([args.runtime, "wait", bot], args.timeout)
        require_success(wait, f"container {bot}")
        wait_exit_code(bot, wait)
        captured[bot] = capture_logs(args.runtime, bot, args.timeout)
        if WORKER_OK_MARKER not in captured[bot]:
            raise VerificationError(f"worker smoke did not print {WORKER_OK_MARKER}")
        out(f"[verify] starting admin container {admin}\n")
        attempted.append(admin)
        containers.append(admin)
        start(args.runtime, admin, image, dict(env, SERVICE_TYPE="admin"), args.timeout)
        created.add(admin)
        wait_for_admin(
            args.runtime, admin, args.timeout, time.monotonic() + args.ready_timeout
        )
        probe = run(
            [
                args.runtime, "exec", admin,
                "python", "-m", "scripts.verify_container", ADMIN_PROBE_FLAG,
            ],
            args.timeout,
        )
        probe_output = (probe.stdout or "") + (probe.stderr or "")
        if probe_output:
            out("----- admin probe -----\n")
            out(probe_output if probe_output.endswith("\n") else probe_output + "\n")
        if probe.returncode != 0 or ADMIN_OK_MARKER not in probe_output:
            raise VerificationError(f"admin probe failed with exit code {probe.returncode}")
        stop = run([args.runtime, "stop", "-t", "5", admin], args.timeout)
        require_success(stop, f"container stop {admin}")
        captured[admin] = capture_logs(args.runtime, admin, args.timeout)
    except KeyboardInterrupt:
        failure = "interrupted"
    except VerificationError as exc:
        failure = str(exc)
    except subprocess.TimeoutExpired as exc:
        failure = f"command timed out after {exc.timeout}s: {' '.join(exc.cmd)}"
    finally:
        try:
            for name in containers:
                if name not in captured:
                    try:
                        captured[name] = read_logs(args.runtime, name, args.timeout)
                    except (VerificationError, subprocess.TimeoutExpired):
                        captured[name] = ""
                if captured[name]:
                    out(f"----- logs {name} -----\n")
                    out(captured[name])
                    if not captured[name].endswith("\n"):
                        out("\n")
            errors = teardown(args.runtime, image, attempted, created)
            for error in errors:
                err(f"[verify] {TEARDOWN_MARKER}: {error}\n")
        finally:
            restore_sigterm_handler(previous_sigterm)
    if failure is not None:
        err(f"[verify] {FAILURE_MARKER}: {failure}\n")
        return 1
    if errors:
        err(f"[verify] {FAILURE_MARKER}: teardown did not complete cleanly\n")
        return 1
    out(f"[verify] {HOST_OK_MARKER} image={image} worker=ok admin=ok cleanup=ok\n")
    return 0


def worker_smoke():
    import partita_bot.config as config
    import run_bot
    from partita_bot.event_fetcher import QUERY_TYPE_FOOTBALL, QUERY_TYPE_GENERAL, EventFetcher
    from partita_bot.notifications import process_notifications
    from partita_bot.storage import Database

    config.FOOTBALL_API_TOKEN = ""
    today = datetime.now(tz=config.TIMEZONE_INFO).date()
    iso_date = today.isoformat()
    urls = {
        "roma": "https://example.invalid/verify/roma-event",
        "milano": "https://example.invalid/verify/milano-event",
    }

    db = Database(database_url="sqlite:///:memory:")
    try:
        db.add_user(1001, "verify-user", "Roma")
        db.set_user_cities(1001, ["roma", "milano"])
        for city, url in urls.items():
            event = {
                "title": f"{city.title()} di verifica",
                "time": "21:00",
                "location": f"{city.title()}, Centro",
                "type": "Verifica",
                "details": "Evento sintetico di verifica",
                "event_date": iso_date,
                "source_url": url,
            }
            db.save_event_cache(city, today, "yes", [event], QUERY_TYPE_GENERAL)
            db.save_event_cache(city, today, "no", [], QUERY_TYPE_FOOTBALL)

        network = NoNetworkSession()
        fetcher = EventFetcher(db, http_client=network)
        local_time = datetime.now(tz=config.TIMEZONE_INFO)

        def dispatch():
            return process_notifications(
                users=db.get_all_users(), db=db, fetcher=fetcher,
                queue_message=db.queue_rich_message, local_time=local_time,
            )

        first = dispatch()
        assert first == {
            "notifications_sent": 2, "no_events": 0, "already_notified": 0, "fetch_errors": 0,
        }, first

        queued = db.get_pending_messages()
        assert len(queued) == 2, len(queued)
        rows = {city: next(r for r in queued if city in r.message.lower()) for city in urls}
        for city, url in urls.items():
            assert url in (rows[city].entities_json or "")
        assert rows["roma"].link_preview_options_json
        assert "vai alla fonte" in rows["roma"].message.lower()

        delivery = RecordingDelivery()
        run_bot.process_message_batch(delivery, db, queued, sleep_fn=lambda _seconds: None)
        assert len(delivery.deliveries) == 2, delivery.deliveries
        texts = [item["text"] for item in delivery.deliveries]
        entities = [entity for item in delivery.deliveries for entity in item["entities"]]
        assert set(urls.values()) <= {entity.url for entity in entities}
        assert any("Roma" in text for text in texts) and any("Milano" in text for text in texts)
        sent = db.get_sent_messages_for_user_within_hours(1001, hours=1)
        assert len(sent) == 2 and all(row.sent_message_id is not None for row in sent)

        second = dispatch()
        assert second["notifications_sent"] == 0 and second["already_notified"] == 1, second
        assert db.count_pending_messages() == 0 and network.calls == 0
        out(f"{WORKER_OK_MARKER} {json.dumps({'cities': 2, 'deliveries': 2})}\n")
        return 0
    finally:
        db.close()


def admin_probe():
    port = int(os.environ.get("ADMIN_PORT", ADMIN_PORT))
    username = os.environ.get("ADMIN_USERNAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    assert username and password

    def request(user, secret):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {}
        if user is not None:
            encoded = base64.b64encode(f"{user}:{secret}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        connection.request("GET", "/", headers=headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, body

    status, _ = request(None, None)
    assert status == 401, status
    status, _ = request(username, "not-the-password")
    assert status == 401, status
    status, body = request(username, password)
    assert status == 200, status
    assert "Bot Admin Panel" in body
    out(f"{ADMIN_OK_MARKER} status=200\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify_container")
    parser.add_argument("--runtime", choices=("podman", "docker"), default="podman")
    parser.add_argument("--timeout", type=positive_seconds, default=600.0)
    parser.add_argument("--ready-timeout", type=positive_seconds, default=120.0)
    parser.add_argument(WORKER_SMOKE_FLAG, action="store_true")
    parser.add_argument(ADMIN_PROBE_FLAG, action="store_true")
    args = parser.parse_args(argv)
    if args.worker_smoke and args.admin_probe:
        parser.error("internal modes are mutually exclusive")
    if args.worker_smoke:
        return worker_smoke()
    if args.admin_probe:
        return admin_probe()
    return run_host(args)


if __name__ == "__main__":
    sys.exit(main())
