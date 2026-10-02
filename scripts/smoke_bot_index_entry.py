# -*- coding: utf-8 -*-
"""
=============================================
Bot index entry transport-independent online E2E smoke
=============================================

Runs a single-target analysis online end to end through the real ``CommandDispatcher.dispatch_async -> AnalyzeCommand ->
TaskService -> StockAnalysisPipeline`` chain, covering the
SH_ALIAS / REGISTERED_NAME / CSI_ALIAS matrix scenarios, without going through any webhook
transport (no transport changes, no mocking of online dependencies, no dry-run).

How to run (single target argument, --timeout defaults to 900 seconds)::

    .venv\\Scripts\\python.exe scripts/smoke_bot_index_entry.py "SH.000016"
    .venv\\Scripts\\python.exe scripts/smoke_bot_index_entry.py "上证50"
    .venv\\Scripts\\python.exe scripts/smoke_bot_index_entry.py "930955.CSI"
    .venv\\Scripts\\python.exe scripts/smoke_bot_index_entry.py "SH.000016" --timeout 1800

Process roles:
- Parent process: only handles the deadline, process-tree cleanup and exit code; the worker inherits the console.
- worker: separate child process (launched explicitly by the parent with the internal ``--worker`` flag, independent of
  any environment variable); submits via the real dispatcher and polls
  ``TaskService`` in the same process, emitting single-line ``E2E_EVENT {json}`` events.

Event contract (single-line ``E2E_EVENT {json}``, phase=submitted|completed|failed|timeout,
includes target, plus task_id / stock_code / result / error depending on phase):
- submitted : task submitted, includes ``task_id`` / ``stock_code``.
- completed : task completed and structural assertions passed, includes ``result``.
- failed    : task failed, or completed with an incomplete result, includes ``error``.
- timeout   : worker exceeded the deadline and was cleaned up (emitted by the parent).

Exit codes: 0 = success; 1 = failure (structural assertion or failed event); 124 = timeout.

Timeout cleanup: Windows uses ``taskkill /T /F`` to end the whole process tree; POSIX sends SIGKILL
to the process group. Timeout only cleans up the process tree; it does not roll back existing side effects such as DB / reports / notifications.

Ctrl-C abort: when the parent receives Ctrl-C during the polling window it also cleans up the process tree first; on cleanup success
the interrupt is propagated, on cleanup failure a ``failed`` event with the cleanup error is emitted and it exits 1, never silently
swallowing cleanup failures.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_env import _reconfigure_output_stream  # noqa: E402

EVENT_PREFIX = "E2E_EVENT "

# Authoritative mapping: matrix input -> (canonical code, registered display name).
# The worker's expected code/name come only from here; never trust identity self-reported by the response or task result,
# so a dispatcher routing error cannot pass falsely.
EXPECTED_OUTCOMES = {
    "SH.000016": ("sh000016", "上证50"),
    "上证50": ("sh000016", "上证50"),
    "930955.CSI": ("csi930955", "红利低波100"),
}


def _resolve_expected(target: str):
    """Return ``(expected_code, expected_name)`` for a matrix target.

    Raises ``ValueError`` for targets outside the required matrix.
    """
    try:
        return EXPECTED_OUTCOMES[target]
    except KeyError:
        raise ValueError(
            f"unsupported smoke target: {target!r} (must be one of "
            f"{sorted(EXPECTED_OUTCOMES)!r})"
        )


def _emit_event(phase: str, target: str, **fields) -> None:
    """Print one single-line ``E2E_EVENT {json}`` event to stdout."""
    payload = {"phase": phase, "target": target}
    payload.update(fields)
    print(f"{EVENT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def _spawn_kwargs(platform_name: str) -> dict:
    """Worker spawn kwargs: inherit the console (never ``PIPE``) and detach
    the child into its own session / new process group so the parent can
    kill the whole tree on timeout."""
    if platform_name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": creationflags} if creationflags else {}
    return {"start_new_session": True}


def _terminate_process_tree(proc, platform_name: str) -> Optional[str]:
    """Kill the whole worker process tree.

    Returns ``None`` only when tree cleanup is confirmed: a successful
    ``taskkill /T /F`` (Windows) or process-group kill (POSIX), or a
    ``ProcessLookupError`` for the tree/group (already gone). A failed tree
    cleanup returns an error string even when the direct ``proc.kill()``
    best-effort fallback succeeds — direct kill only proves the worker
    process itself was killed, not its descendants, so it never erases the
    original tree-cleanup error.
    """
    if platform_name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                timeout=30,
            )
            if result.returncode == 0:
                return None
            taskkill_error = f"taskkill exited {result.returncode}"
        except Exception as exc:
            taskkill_error = f"taskkill failed: {exc}"
        # Best-effort damage control: kill the worker process itself, but
        # never report a confirmed tree cleanup.
        try:
            proc.kill()
            return (
                f"{taskkill_error}; direct worker kill succeeded but "
                f"tree cleanup is unconfirmed"
            )
        except ProcessLookupError:
            return (
                f"{taskkill_error}; worker process already gone but "
                f"tree cleanup is unconfirmed"
            )
        except Exception as exc:
            return f"{taskkill_error}; direct worker kill also failed: {exc}"
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    killpg = getattr(os, "killpg", None)
    try:
        if killpg is not None:
            killpg(proc.pid, kill_signal)
        else:
            # No process-group primitive: a direct kill only proves the worker itself was killed, not that
            # descendants were cleaned up, so we must not return None claiming tree cleanup was confirmed.
            proc.kill()
            return (
                "process-group kill unavailable; direct worker kill "
                "succeeded but tree cleanup is unconfirmed"
            )
        return None
    except ProcessLookupError:
        # The process group is already gone — confirmed success.
        return None
    except OSError as exc:
        try:
            proc.kill()
            return (
                f"process-group kill failed: {exc}; "
                f"direct worker kill succeeded but tree cleanup is unconfirmed"
            )
        except ProcessLookupError:
            return (
                f"process-group kill failed: {exc}; "
                f"worker process already gone but tree cleanup is unconfirmed"
            )
        except Exception as kill_exc:
            return (
                f"process-group kill failed: {exc}; "
                f"direct worker kill also failed: {kill_exc}"
            )


def _cleanup_process_tree(proc, platform_name: str) -> Optional[str]:
    """Terminate the worker tree and wait for the worker to exit.

    Returns ``None`` only when tree cleanup is confirmed; any terminate /
    post-cleanup wait / kill failure is merged into the returned error
    string so callers never silently claim a confirmed cleanup.
    """
    cleanup_error = _terminate_process_tree(proc, platform_name)
    # Post-cleanup handling must never escape as an uncaught exception:
    # merge any failure into ``cleanup_error``.
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception as exc:
            post_error = f"post-cleanup kill failed: {exc}"
            cleanup_error = (
                f"{cleanup_error}; {post_error}"
                if cleanup_error is not None
                else post_error
            )
    except Exception as exc:
        post_error = f"post-cleanup wait failed: {exc}"
        cleanup_error = (
            f"{cleanup_error}; {post_error}"
            if cleanup_error is not None
            else post_error
        )
    return cleanup_error


def _assert_complete_result(result: dict, expected_code: str, expected_name: str):
    """Validate a completed task result.

    Returns ``None`` on success, or a human-readable failure reason string.
    A completed task is only a success when the canonical code equals the
    authoritative expected code, the name equals the registered display
    name, and the three non-empty result fields (analysis_summary /
    operation_advice / trend_prediction) are present.
    """
    if not isinstance(result, dict):
        return "completed result is not a dict"
    if result.get("code") != expected_code:
        return (
            f"result code mismatch: expected {expected_code!r}, "
            f"got {result.get('code')!r}"
        )
    if result.get("name") != expected_name:
        return (
            f"result name mismatch: expected {expected_name!r}, "
            f"got {result.get('name')!r}"
        )
    missing = [
        key
        for key in ("analysis_summary", "operation_advice", "trend_prediction")
        if not (result.get(key) or "").strip()
    ]
    if missing:
        return f"completed result missing non-empty fields: {missing}"
    return None


def _poll_once(service, task_id: str, target: str, stock_code: str):
    """One poll of the in-process ``TaskService``.

    Empty initial state (``None`` / ``running``) returns ``None`` so the
    caller keeps waiting. Terminal states return exactly one event dict:
    ``completed`` (structure assertions passed against the authoritative
    matrix expectation) or ``failed`` (task failed or completed but
    incomplete).
    """
    expected_code, expected_name = _resolve_expected(target)
    status = service.get_task_status(task_id)
    if status is None or status.get("status") in ("running", "pending"):
        return None
    if status.get("status") == "completed":
        result = status.get("result")
        reason = _assert_complete_result(result, expected_code, expected_name)
        if reason is not None:
            return {
                "phase": "failed",
                "target": target,
                "task_id": task_id,
                "stock_code": stock_code,
                "error": reason,
            }
        return {
            "phase": "completed",
            "target": target,
            "task_id": task_id,
            "stock_code": stock_code,
            "result": result,
        }
    return {
        "phase": "failed",
        "target": target,
        "task_id": task_id,
        "stock_code": stock_code,
        "error": status.get("error") or f"task status is {status.get('status')!r}",
    }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def _run_worker(target: str) -> int:
    """Worker entrypoint: submit via the real dispatcher in this process and
    poll the same process's TaskService until a terminal state.

    Only exits 0 when a completed event with passing structure assertions
    was produced. Authoritative target validation runs BEFORE any dispatcher
    construction or submission, so an unsupported target can never submit.
    Unexpected exceptions from dispatch / poll / event serialization are
    converted into a ``failed`` E2E event with the exception evidence on
    stderr and exit 1 — never an uncaught traceback. ``KeyboardInterrupt``
    is not treated as an ordinary failure.
    """
    expected_code = _resolve_expected(target)[0]

    try:
        return _run_worker_inner(target, expected_code)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _emit_event("failed", target, error=f"worker exception: {exc}")
        print(f"ERROR: worker exception: {exc}", file=sys.stderr)
        return 1


def _run_worker_inner(target: str, expected_code: str) -> int:
    """Dispatch + poll loop of the worker (see :func:`_run_worker`)."""
    from datetime import datetime

    from bot.commands.analyze import AnalyzeCommand
    from bot.dispatcher import CommandDispatcher
    from bot.models import BotMessage, ChatType
    from src.services.task_service import get_task_service

    dispatcher = CommandDispatcher()
    dispatcher.register(AnalyzeCommand())

    message = BotMessage(
        platform="smoke",
        message_id="smoke-e2e",
        user_id="smoke",
        user_name="smoke",
        chat_id="smoke",
        chat_type=ChatType.PRIVATE,
        content=f"/analyze {target}",
        raw_content=f"/analyze {target}",
        mentioned=False,
        timestamp=datetime.now(),
    )

    import asyncio

    response = asyncio.run(dispatcher.dispatch_async(message))

    # Authoritative expectations come only from the matrix mapping; never trust ``extra.stock_code`` or identity self-reported
    # by the task result: a dispatcher routing error (wrong code/name submitted) still fails.
    response_code = response.extra.get("stock_code")
    response_task_id = response.extra.get("task_id")
    if response_task_id and response_code == expected_code:
        task_id = response_task_id
        _emit_event(
            "submitted",
            target,
            task_id=task_id,
            stock_code=expected_code,
        )
    elif response_task_id:
        # Task identity exists but the code differs from the matrix expectation: the wrong target was submitted; emit
        # an explicit mismatch error (with expected and actual values) and the structured actual code,
        # rather than the success response text.
        _emit_event(
            "failed",
            target,
            task_id=response_task_id,
            stock_code=response_code,
            error=(
                f"submitted code mismatch: expected {expected_code!r}, "
                f"got {response_code!r}"
            ),
        )
        return 1
    else:
        _emit_event("failed", target, error=response.text)
        return 1

    service = get_task_service()
    while True:
        event = _poll_once(service, task_id, target, expected_code)
        if event is not None:
            _emit_event(event["phase"], target, **{
                k: v
                for k, v in event.items()
                if k not in ("phase", "target")
            })
            return 0 if event["phase"] == "completed" else 1
        time.sleep(5)


# ---------------------------------------------------------------------------
# Parent
# ---------------------------------------------------------------------------
def _run_parent(target: str, timeout: int) -> int:
    """Parent entrypoint: spawn the worker (inheriting the console), enforce
    the deadline, clean up the process tree on timeout and on Ctrl-C, and
    map to exit codes 0 / 1 / 124. Authoritative target validation runs
    before the subprocess spawn, so an unsupported target never reaches
    ``Popen``; a non-positive timeout is rejected the same way. On Ctrl-C
    the parent cleans up the worker tree first: a confirmed cleanup
    propagates the interrupt, a failed cleanup emits a structured ``failed``
    event and returns 1."""
    _resolve_expected(target)  # raises ValueError for unsupported targets
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")

    # ``proc.wait`` without ``timeout`` would block SIGINT handling on
    # Windows, so poll with the deadline instead.
    child_args = [sys.executable, str(Path(__file__).resolve()), "--worker", target]
    if os.name == "nt":
        proc = subprocess.Popen(
            child_args,
            cwd=str(REPO_ROOT),
            **_spawn_kwargs("nt"),
        )
    else:
        proc = subprocess.Popen(
            child_args,
            cwd=str(REPO_ROOT),
            **_spawn_kwargs("posix"),
        )
    deadline = time.monotonic() + timeout
    while True:
        try:
            exit_code = proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            exit_code = None
        except KeyboardInterrupt:
            # User abort: clean up the worker process tree. Cleanup failures must not be silently swallowed:
            # like the timeout branch, emit a structured failed event and return 1, so the
            # user does not wrongly assume the tree was cleaned up while worker descendants still run real side effects.
            if proc.poll() is not None:
                # Worker already exited on its own: no cleanup needed, propagate the interrupt directly.
                raise
            cleanup_error = _cleanup_process_tree(
                proc, "nt" if os.name == "nt" else "posix"
            )
            if cleanup_error is not None:
                _emit_event(
                    "failed",
                    target,
                    error=f"interrupt cleanup failed: {cleanup_error}",
                )
                return 1
            raise
        if exit_code is not None:
            # The documented runtime contract exposes only 0 / 1 / 124: any other worker exit code
            # is normalized to 1 to avoid leaking arbitrary crash codes.
            if exit_code in (0, 1, 124):
                return exit_code
            return 1
        if time.monotonic() >= deadline:
            cleanup_error = _cleanup_process_tree(
                proc, "nt" if os.name == "nt" else "posix"
            )
            if cleanup_error is not None:
                # Cleanup failure must not silently claim success: emit a structured failed event and return 1.
                _emit_event(
                    "failed",
                    target,
                    error=f"timeout cleanup failed: {cleanup_error}",
                )
                return 1
            _emit_event("timeout", target)
            return 124


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bot 指数入口 transport-independent 在线 E2E smoke："
            "从真实 dispatcher 贯穿 Pipeline，单标的在线分析。"
        )
    )
    parser.add_argument("target", help="矩阵输入：SH.000016 / 上证50 / 930955.CSI")
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="worker 超时秒数（默认 900 秒；超时清理进程树，不回滚副作用）",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    target = (args.target or "").strip()
    if not target:
        print("ERROR: target 不能为空", file=sys.stderr)
        return 2

    # Authoritative target validation must precede any role branch: an unsupported target must not build
    # a dispatcher, submit, or spawn a child process (argument/input error, non-zero return).
    try:
        _resolve_expected(target)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # ``--timeout`` must be positive: a non-positive timeout is rejected before spawn/submit (input error).
    if args.timeout <= 0:
        print(f"ERROR: --timeout 必须为正整数，got {args.timeout}", file=sys.stderr)
        return 2

    _reconfigure_output_stream(sys.stdout)
    _reconfigure_output_stream(sys.stderr)

    # Role selection is decided only by the explicit ``--worker`` flag (passed explicitly when the parent launches the child),
    # independent of any environment variable, so a preset external env var cannot bypass the parent's hard timeout.
    if args.worker:
        return _run_worker(target)
    return _run_parent(target, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
