"""The one adapter contract: a subprocess, one JSON request, one JSON verdict.

Every external tool this pipeline drives — the voice env, the i2v env, and any
cloud adapter to come — is addressed the same way: an interpreter, a script, a
subcommand in argv[1], the request as JSON on stdin, and exactly one JSON line
on stdout as the verdict. Library import noise goes to stderr so stdout stays
a clean channel, and the caller gets a verdict, never a traceback.

Extracted from voice.bridge and visual.bridge (2026-08-26, stems Phase 6),
which had drifted: one caught TimeoutExpired, the other let it raise; one kept
12 lines of stderr, the other 15. Two implementations of a contract IS the
drift — this module is the contract, and the per-env modules keep only their
readiness checks and their timeouts.
"""
import json
import subprocess


def call(interpreter, script, command: str, request: dict,
         timeout: int, stderr_lines: int = 15) -> dict:
    """Run one bridge command. Always returns a dict with an `ok` key."""
    try:
        r = subprocess.run(
            [str(interpreter), str(script), command],
            input=json.dumps(request), capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{command} exceeded {timeout}s"}
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        tail = "\n".join((r.stderr or "").splitlines()[-stderr_lines:])
        return {"ok": False,
                "error": f"bridge produced no verdict (rc={r.returncode})\n{tail}"}
