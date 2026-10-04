"""Inert dashboard recovery bootstrap; only anonymous inherited pipes release it.

The nonce correlates this launch, not an authority read from argv or disk. The
parent must publish the retained handle and win the registry fence before RUN.
Normal CLI input is /dev/null and output is the action log, never the channel.
"""
from contextlib import ExitStack
import os
import queue
import re
import runpy
import sys
import threading


class GateRefused(RuntimeError):
    """No update work may be started (or write delivery is uncertain)."""


def _frame(token: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise GateRefused("invalid bootstrap correlation token")
    return b"RUN " + token.encode("ascii") + b"\n"


def send_run(writer, token: str) -> None:
    # A single sub-PIPE_BUF frame into the fresh, empty, unbuffered stdin pipe.
    frame = _frame(token)
    if writer.write(frame) != len(frame):
        raise GateRefused("RUN delivery uncertain: short write")


def wait_and_run(reader, token: str, launch, *, timeout: float = 10.0):
    expected = _frame(token)
    result = queue.Queue(maxsize=1)

    def read_gate():
        try:
            result.put(reader.readline(len(expected) + 1))
        except Exception as exc:
            result.put(exc)

    # Anonymous Windows pipes are not selectable. One daemon reader, one finite
    # wait; the inert process exits on timeout without importing the CLI.
    threading.Thread(target=read_gate, name="update-run-gate", daemon=True).start()
    try:
        message = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise GateRefused("RUN deadline expired") from exc
    if message != expected:
        raise GateRefused("RUN absent, malformed, or cancelled")
    return launch()


def main() -> None:
    if len(sys.argv) < 3:
        raise GateRefused("missing bootstrap arguments")
    token, args = sys.argv[1], sys.argv[2:]
    # Register each acquisition immediately, before the next fallible operation.
    # A descriptor belongs either to its raw cleanup or its stream, never both.
    with ExitStack() as cleanup:
        raw = []
        def close_raw(slot):
            if slot[0] is not None:
                os.close(slot[0])

        for source in (0, 1):
            slot = [os.dup(source)]
            raw.append(slot)
            cleanup.callback(close_raw, slot)
        for slot in raw:
            os.set_inheritable(slot[0], False)
        streams = []
        for slot, mode in zip(raw, ("rb", "wb")):
            stream = os.fdopen(slot[0], mode, buffering=0)
            slot[0] = None
            cleanup.callback(stream.close)
            streams.append(stream)
        reader, writer = streams
        with open(os.devnull, "rb") as null_input:
            os.dup2(null_input.fileno(), 0)
        os.dup2(2, 1)

        from eidolon_cli.update_handoff_driver import Transport, bind_child
        # ExitStack remains sole stream owner; the transport borrows these
        # unbuffered streams until this scope exits. No gate reader survives.
        transport = Transport(reader, writer, owns_streams=False)
        cleanup.callback(transport.close)
        if transport.line() != _frame(token):
            raise GateRefused("RUN absent, malformed, or cancelled")
        context = bind_child(transport)
        context.require_full_lifecycle()


if __name__ == "__main__":
    main()
