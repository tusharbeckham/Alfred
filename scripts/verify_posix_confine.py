#!/usr/bin/env python3
"""Verify the POSIX confinement path for real, under a real Linux kernel.

Run this under WSL (or any Linux) rather than trusting that the setrlimit branch works:

    wsl -e python3 /mnt/c/Alfred/scripts/verify_posix_confine.py

Every check spends a real resource and expects the kernel to refuse. A check that
"passes" because the operation failed for an unrelated reason is worthless, so the
memory test is paired with a control that proves the same allocation succeeds when the
limit is absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_confine as confine  # noqa: E402

MIB = 1024 * 1024
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)


def run(limits: confine.Limits, code: str, timeout: int = 60):
    proc, job, note = confine.spawn_confined([sys.executable, "-c", code], limits)
    out, err = proc.communicate(timeout=timeout)
    usage = confine.query_job(job)
    confine.close_job(job)
    return proc.returncode, out, err, usage, note


def main() -> int:
    print(f"platform={sys.platform} os.name={os.name} WINDOWS={confine.WINDOWS}")
    if confine.WINDOWS:
        print("This script must run on POSIX. Use: wsl -e python3 <path>")
        return 1

    # 1. Memory ceiling actually refuses an over-limit allocation.
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=64 * MIB),
        "b = bytearray(400 * 1024 * 1024); print(len(b))",
    )
    check("memory limit refuses a 400MB alloc under 64MB", code != 0, f"exit={code}")
    check("failure is a MemoryError, not something unrelated", b"MemoryError" in err,
          err.decode("utf-8", "replace").strip().splitlines()[-1][:70] if err else "no stderr")
    check("confinement was reported as applied", note == "confined", note)

    # 2. The control: the same allocation must SUCCEED with no limit. Without this the
    #    check above could be passing for a reason that has nothing to do with the limit.
    code, out, err, usage, note = run(
        confine.Limits(), "b = bytearray(400 * 1024 * 1024); print(len(b))")
    check("CONTROL: same alloc succeeds with no limit", code == 0 and b"419430400" in out,
          f"exit={code}")

    # 3. A confined child still runs normally (the limits must not break the happy path).
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=256 * MIB, cpu_seconds=30),
        "print('hello from a confined child')")
    check("a confined child runs normally", code == 0 and b"hello" in out, f"exit={code}")

    # 4. CPU ceiling kills a spinning child. SIGKILL shows up as -9.
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=256 * MIB, cpu_seconds=1),
        "x = 0\nwhile True: x += 1", timeout=90)
    check("cpu limit kills a spinning child", code != 0, f"exit={code}")

    # 5. RLIMIT_FSIZE is a genuine disk bound - the thing Windows Job Objects cannot do.
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=256 * MIB, max_file_bytes=1024),
        "open('/tmp/confine-probe.bin','wb').write(b'x' * 10_000_000); print('WROTE')")
    check("file-size limit refuses an over-limit write", code != 0 and b"WROTE" not in out,
          f"exit={code}")
    try:
        size = os.path.getsize("/tmp/confine-probe.bin")
        check("the file never exceeded the limit", size <= 1024, f"{size} bytes")
        os.unlink("/tmp/confine-probe.bin")
    except OSError:
        check("the file never exceeded the limit", True, "file absent")

    # 6. A child cannot raise its own soft limit back up. If it could, it is not confined.
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=64 * MIB),
        "import resource\n"
        "try:\n"
        "    resource.setrlimit(resource.RLIMIT_AS, (400*1024*1024, 400*1024*1024))\n"
        "    print('RAISED')\n"
        "except Exception as e:\n"
        "    print('BLOCKED', type(e).__name__)\n")
    check("a child cannot raise its own limit", b"RAISED" not in out,
          out.decode("utf-8", "replace").strip()[:60])

    # 7. Accounting is reported, so a limit is auditable rather than merely claimed.
    code, out, err, usage, note = run(
        confine.Limits(memory_bytes=512 * MIB),
        "b = bytearray(100 * 1024 * 1024); print(len(b))")
    check("peak memory is reported", usage.get("peakProcessBytes", 0) > 0,
          f"peak={usage.get('peakProcessBytes')}")
    check("cpu time is reported", "cpuSeconds" in usage, f"cpu={usage.get('cpuSeconds')}")

    # 8. The process group exists so the tree can be signalled as a unit.
    proc, job, note = confine.spawn_confined(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        confine.Limits(memory_bytes=128 * MIB))
    check("a POSIX job records its process group", getattr(job, "pgid", None) == proc.pid,
          f"pgid={getattr(job, 'pgid', None)} pid={proc.pid}")
    confine.close_job(job)
    try:
        proc.wait(timeout=15)
        check("closing the job kills the group", proc.returncode is not None,
              f"exit={proc.returncode}")
    except Exception as exc:  # noqa: BLE001
        check("closing the job kills the group", False, str(exc)[:60])

    # 9. Network isolation: a capability declared local-only must not reach the network.
    probe = (
        "import socket\n"
        "s = socket.socket(); s.settimeout(4)\n"
        "print('CONNECT', s.connect_ex(('1.1.1.1', 80)))\n"
    )
    if not confine.network_isolation_available():
        check("network isolation is available", False,
              "unshare missing or unprivileged namespaces disabled")
    else:
        check("network isolation is available", True)

        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", probe], confine.Limits(memory_bytes=256 * MIB),
            isolate_network=True)
        out, err = proc.communicate(timeout=60)
        confine.close_job(job)
        text = out.decode("utf-8", "replace").strip()
        check("an isolated child cannot reach the network", "CONNECT 0" not in text,
              text[:40] or err.decode("utf-8", "replace")[:40])
        check("the note reports isolation was applied", "netns" in note, note)

        # 10. The control: without isolation the SAME probe must succeed, or the check above
        #     could be passing because this machine has no network at all.
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", probe], confine.Limits(memory_bytes=256 * MIB),
            isolate_network=False)
        out, _ = proc.communicate(timeout=60)
        confine.close_job(job)
        text = out.decode("utf-8", "replace").strip()
        if "CONNECT 0" in text:
            check("CONTROL: the same probe reaches the network unisolated", True, text[:30])
        else:
            print(f"  SKIP  CONTROL: this machine has no outbound network ({text[:30]}) - "
                  "the isolation check above is therefore not conclusive")

        # 11. Isolation must not stop a child doing local work.
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", "print('local work ok')"],
            confine.Limits(memory_bytes=256 * MIB), isolate_network=True)
        out, err = proc.communicate(timeout=60)
        confine.close_job(job)
        check("an isolated child still runs local work", b"local work ok" in out,
              err.decode("utf-8", "replace")[:50])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL POSIX CONFINEMENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
