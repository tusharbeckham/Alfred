#!/usr/bin/env python3
"""Windows Job Object confinement for harness child processes.

WHY THIS EXISTS
---------------
The harness bounds two things about a child process: how long it may run
(``maxRuntimeSeconds``) and how much output it may produce (``maxOutputBytes``). It
bounds neither of the two resources a runaway child actually exhausts first — **memory**
and **CPU** — and it bounds nothing at all about a child's *descendants*. Kill the child
after a timeout and its grandchildren keep running, unparented and unlogged.

``subprocess`` cannot fix this. There is no portable memory limit; on Unix you would
reach for ``setrlimit``, and on Windows the equivalent is a **Job Object**: a kernel
object you attach processes to, carrying limits the kernel enforces on the whole tree.

So this module is the answer to the gap the previous threat model admitted:

    "The harness does not confine the child process. Output size and wall-clock time are
     bounded; memory and CPU are not."

WHAT IT ACTUALLY GUARANTEES
---------------------------
* A **memory ceiling** on the child and, separately, on the job as a whole — so a child
  that spawns ten copies of itself cannot evade a per-process limit by division.
* An **active-process cap**, so a fork bomb hits a wall instead of the scheduler.
* An optional **CPU-time ceiling** (user time across the job).
* **Kill on close**: when the harness drops the job handle — including if the harness
  itself is killed — the kernel terminates everything still inside it. This is the part
  ``subprocess`` genuinely cannot replicate, and it is why orphaned grandchildren stop
  being possible rather than merely unlikely.

THE RACE, AND WHY THERE ISN'T ONE
---------------------------------
The obvious implementation — spawn, then assign to the job — has a hole: between
``CreateProcess`` returning and ``AssignProcessToJobObject`` being called, the child is
already executing and can spawn a grandchild that is never assigned and therefore never
limited. The window is small, which is not the same as closed.

So the child is created **suspended**. It is assigned to the job before it has executed
a single instruction, and only then is its initial thread resumed. Python's
``subprocess`` will not hand back the thread handle, so the thread is located by
snapshotting system threads and matching the owner PID — which is why
:func:`_resume_process` exists rather than a one-line ``ResumeThread``.

If assignment fails for any reason the child is terminated while still suspended, so a
confinement failure means "nothing ran", not "something ran unconfined". Fail closed.

Everything here is ctypes against kernel32 — no pip install, consistent with the rest of
the harness. On a non-Windows platform every function degrades to a clearly-reported
no-op rather than pretending to confine anything.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------- constants

CREATE_SUSPENDED = 0x00000004

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JobObjectBasicAccountingInformation = 1
# Class 8 rather than reading IoInfo out of the extended limit struct, which is documented
# as reserved and returns zeros.
JobObjectBasicAndIoAccountingInformation = 8
JobObjectExtendedLimitInformation = 9

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
INVALID_HANDLE_VALUE = -1


class ConfinementError(RuntimeError):
    """Confinement could not be established. The caller must not run the child."""


@dataclass
class Limits:
    """What a confined child is allowed to consume.

    ``None`` means "do not limit this", which is different from zero — the Windows API
    treats a zero limit as "no limit", and conflating the two would silently disable a
    control someone thought they had configured.
    """

    memory_bytes: int | None = None          # per-process commit ceiling
    job_memory_bytes: int | None = None      # ceiling for the whole tree combined
    active_processes: int | None = None      # fork-bomb cap
    cpu_seconds: int | None = None           # user CPU time across the job
    # POSIX only. Windows Job Objects have no per-file size limit, so this is one place
    # the POSIX path is genuinely stronger rather than merely different.
    max_file_bytes: int | None = None

    def any_set(self) -> bool:
        return any(v for v in (self.memory_bytes, self.job_memory_bytes,
                               self.active_processes, self.cpu_seconds,
                               self.max_file_bytes))


@dataclass
class ConfinementResult:
    """What confinement was actually applied, and what it cost the child."""

    applied: bool
    reason: str = ""
    peak_process_bytes: int | None = None
    peak_job_bytes: int | None = None
    total_processes: int | None = None
    limits: dict[str, Any] = field(default_factory=dict)
    # True when the child died in a way consistent with hitting a job limit. Reported
    # rather than asserted: Windows does not tell you "this process was killed by the
    # job", so this is an inference and is named as one.
    likely_limit_kill: bool = False


# ------------------------------------------------------------------------ ctypes setup


def _win():
    """Return the ctypes bindings, or raise if unavailable.

    Built lazily so that importing this module on Linux (or in a test collection pass)
    costs nothing and cannot fail.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ULONG_PTR),
            ("MaximumWorkingSetSize", ULONG_PTR),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ULONG_PTR),
            ("JobMemoryLimit", ULONG_PTR),
            ("PeakProcessMemoryUsed", ULONG_PTR),
            ("PeakJobMemoryUsed", ULONG_PTR),
        ]

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION(ctypes.Structure):
        """Accounting plus IO counters.

        The ``IoInfo`` member of ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` looks like the
        obvious place to read IO totals from, and it is documented as **reserved** — it
        comes back as zeros. Reading it and reporting the result would have produced an
        audit field that always said "this capability touched no disk", which is worse than
        having no field at all. The real numbers live in info class 8.
        """

        _fields_ = [
            ("BasicInfo", JOBOBJECT_BASIC_ACCOUNTING_INFORMATION),
            ("IoInfo", IO_COUNTERS),
        ]

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]

    return {
        "ctypes": ctypes, "wintypes": wintypes, "k32": kernel32,
        "EXT": JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        "ACCT": JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
        "ACCT_IO": JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION,
        "THREADENTRY32": THREADENTRY32,
    }


# ------------------------------------------------------------------------- job objects


def create_job(limits: Limits) -> Any:
    """Create an anonymous Job Object carrying ``limits``. Returns its handle.

    ``KILL_ON_JOB_CLOSE`` is always set, regardless of what else is configured. That is
    the property that makes a job different from a bookkeeping exercise: it means the
    child tree cannot outlive the harness process, even if the harness is killed rather
    than exiting cleanly. Nothing in ``subprocess`` gives you that.
    """
    if not WINDOWS:
        raise ConfinementError("Job Objects are a Windows facility")
    win = _win()
    ctypes, k32 = win["ctypes"], win["k32"]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ConfinementError(f"CreateJobObjectW failed (error {ctypes.get_last_error()})")

    info = win["EXT"]()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION

    if limits.memory_bytes:
        info.ProcessMemoryLimit = int(limits.memory_bytes)
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
    if limits.job_memory_bytes:
        info.JobMemoryLimit = int(limits.job_memory_bytes)
        flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
    if limits.active_processes:
        info.BasicLimitInformation.ActiveProcessLimit = int(limits.active_processes)
        flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    if limits.cpu_seconds:
        # Windows counts in 100-nanosecond units.
        info.BasicLimitInformation.PerJobUserTimeLimit = int(limits.cpu_seconds) * 10_000_000
        flags |= JOB_OBJECT_LIMIT_JOB_TIME

    info.BasicLimitInformation.LimitFlags = flags

    ok = k32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise ConfinementError(f"SetInformationJobObject failed (error {err})")
    return job


def query_job(job: Any) -> dict[str, int]:
    """Read back peak usage, process counts and IO totals, for the audit record.

    The IO counters are **accounting, not a bound**: a Job Object reports how many bytes
    the tree read and wrote, and can rate-limit IO on Windows 10+, but it cannot cap total
    bytes written. So a capability permitted to write can still fill a disk. Recording the
    figure at least makes that visible after the fact, and the honest framing belongs here
    rather than in a summary that implies disk is bounded. (On POSIX, ``RLIMIT_FSIZE`` *is*
    a real per-file bound — see :func:`_posix_preexec`.)
    """
    if job is None:
        return {}
    if not WINDOWS:
        return _query_posix(job) if isinstance(job, PosixJob) else {}
    win = _win()
    ctypes, k32 = win["ctypes"], win["k32"]
    out: dict[str, int] = {}

    ext = win["EXT"]()
    if k32.QueryInformationJobObject(job, JobObjectExtendedLimitInformation,
                                    ctypes.byref(ext), ctypes.sizeof(ext), None):
        out["peakProcessBytes"] = int(ext.PeakProcessMemoryUsed)
        out["peakJobBytes"] = int(ext.PeakJobMemoryUsed)

    acct = win["ACCT_IO"]()
    if k32.QueryInformationJobObject(job, JobObjectBasicAndIoAccountingInformation,
                                    ctypes.byref(acct), ctypes.sizeof(acct), None):
        basic = acct.BasicInfo
        out["totalProcesses"] = int(basic.TotalProcesses)
        out["activeProcesses"] = int(basic.ActiveProcesses)
        out["totalTerminated"] = int(basic.TotalTerminatedProcesses)
        # 100-nanosecond units -> seconds.
        out["cpuSeconds"] = round(
            (int(basic.TotalUserTime) + int(basic.TotalKernelTime)) / 10_000_000, 3)
        out["bytesRead"] = int(acct.IoInfo.ReadTransferCount)
        out["bytesWritten"] = int(acct.IoInfo.WriteTransferCount)
        out["ioOperations"] = int(acct.IoInfo.ReadOperationCount
                                 + acct.IoInfo.WriteOperationCount)
    return out


def close_job(job: Any) -> None:
    """Release the job, terminating anything still inside it.

    On Windows this is a handle close and the kernel does the reaping — which is why the
    tree cannot outlive the harness even if the harness is killed. On POSIX there is no
    such object, so the process group is signalled explicitly: SIGTERM, then SIGKILL. That
    is genuinely weaker, because a harness that is itself SIGKILLed never gets to send
    anything.
    """
    if job is None:
        return
    if not WINDOWS:
        if isinstance(job, PosixJob):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(job.pgid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    return          # already gone, or never a group leader
                time.sleep(0.05)
        return
    try:
        _win()["k32"].CloseHandle(job)
    except Exception:  # noqa: BLE001 - cleanup must never raise over the real result
        pass


# ------------------------------------------------------------------------------ posix


@dataclass
class PosixJob:
    """The POSIX analogue of a job handle: a process group plus the limits applied.

    Windows hands you one kernel object that owns the whole tree. POSIX does not, so this
    is a process *group* id and the limits are recorded separately for the audit record.
    The differences are real and are stated in :func:`_posix_preexec` rather than smoothed
    over — a confinement story that claims parity it does not have is worse than one that
    admits the gap.
    """

    pgid: int
    limits: Limits
    rusage_before: Any = None


def _posix_preexec(limits: Limits):
    """Build the function that applies ``limits`` in the child, after fork, before exec.

    What each limit buys, and what it does not:

    * ``RLIMIT_AS`` — address space. This is the closest POSIX analogue to a Job Object's
      ``ProcessMemoryLimit``, but it caps *virtual* address space rather than resident
      memory, so a process that maps a large file without touching it counts against it.
      It is the limit that actually stops a runaway allocation, which is the case we care
      about.
    * ``RLIMIT_CPU`` — CPU seconds. The kernel sends SIGXCPU at the soft limit and SIGKILL
      at the hard one, so a spinning child dies rather than being merely noted.
    * ``RLIMIT_NPROC`` — **weaker than the Windows equivalent, deliberately noted.** It caps
      processes per real UID, not per process tree. So it bounds a fork bomb, but the number
      it counts against includes every other process the same user is already running. A
      Job Object's ``ActiveProcessLimit`` counts only the job. Same intent, coarser
      instrument.
    * ``RLIMIT_FSIZE`` — maximum size of any single file the child may write. Windows Job
      Objects have **no** equivalent, so this is one place the POSIX path is *stronger*: it
      is a genuine disk bound, not merely accounting.

    ``preexec_fn`` is documented as unsafe in a multi-threaded parent, because it runs
    between fork and exec where only async-signal-safe operations are legal. The harness is
    single-threaded at this point (it runs one capability, synchronously), so this is
    acceptable — and it is the only way to set rlimits on a child from Python's stdlib.
    """
    import resource

    settings: list[tuple[int, int]] = []
    if limits.memory_bytes:
        settings.append((resource.RLIMIT_AS, int(limits.memory_bytes)))
    if limits.cpu_seconds:
        settings.append((resource.RLIMIT_CPU, int(limits.cpu_seconds)))
    if limits.active_processes:
        settings.append((resource.RLIMIT_NPROC, int(limits.active_processes)))
    if limits.max_file_bytes:
        settings.append((resource.RLIMIT_FSIZE, int(limits.max_file_bytes)))

    def apply() -> None:  # pragma: no cover - runs only in the forked child
        for which, value in settings:
            # Set soft AND hard to the same value so the child cannot raise its own soft
            # limit back up. A child that can call setrlimit to undo its confinement is
            # not confined.
            resource.setrlimit(which, (value, value))

    return apply


def _spawn_confined_posix(argv: list[str], limits: Limits, *, cwd, env,
                          stdout, stderr,
                          isolate_network: bool = False) -> tuple[subprocess.Popen, Any, str]:
    notes: list[str] = []
    launch = list(argv)
    if isolate_network:
        if network_isolation_available():
            # Prefixing argv, not building a shell string: `unshare` is still an argv array
            # and the capability's own argv follows it after `--`, so nothing about the
            # no-shell guarantee changes.
            launch = [*NETNS_PREFIX, *launch]
            notes.append("netns")
        else:
            notes.append("netns-unavailable")

    if not limits.any_set():
        proc = subprocess.Popen(launch, cwd=cwd, env=env, shell=False,  # noqa: S603
                                stdout=stdout, stderr=stderr, start_new_session=True)
        return proc, PosixJob(pgid=proc.pid, limits=limits), \
            "+".join(["no limits configured", *notes])

    try:
        import resource
    except ImportError:
        proc = subprocess.Popen(launch, cwd=cwd, env=env, shell=False,  # noqa: S603
                                stdout=stdout, stderr=stderr)
        return proc, None, "+".join(["no resource module: rlimit confinement unavailable", *notes])

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.Popen(  # noqa: S603 - argv array, shell=False, validated upstream
        launch, cwd=cwd, env=env, shell=False,
        stdout=stdout, stderr=stderr,
        preexec_fn=_posix_preexec(limits),  # noqa: PLW1509 - single-threaded parent; see docstring
        # A new session means a new process group, so the whole tree can be signalled at
        # once. This is the closest POSIX gets to KILL_ON_JOB_CLOSE — and it is genuinely
        # weaker: if the harness is SIGKILLed it cannot signal anything, whereas a Windows
        # job is reaped by the kernel when its last handle closes.
        start_new_session=True,
    )
    return proc, PosixJob(pgid=proc.pid, limits=limits, rusage_before=before), \
        "+".join(["confined", *notes])


def _query_posix(job: PosixJob) -> dict[str, int]:
    """Best-effort accounting for a POSIX child.

    ``getrusage(RUSAGE_CHILDREN)`` reports the maximum RSS across all *reaped* children, so
    the delta since spawn is a fair reading only because the harness runs one capability at
    a time. It is labelled ``peakProcessBytes`` for symmetry with the Windows path, and it
    is an approximation rather than the per-job figure a Job Object gives.
    """
    try:
        import resource
    except ImportError:
        return {}
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is kilobytes on Linux, bytes on macOS. Normalize to bytes.
    scale = 1 if sys.platform == "darwin" else 1024
    peak = int(after.ru_maxrss) * scale
    out = {"peakProcessBytes": peak, "peakJobBytes": peak,
           "cpuSeconds": round(after.ru_utime + after.ru_stime, 3)}
    if job.rusage_before is not None:
        out["cpuSecondsThisRun"] = round(
            (after.ru_utime + after.ru_stime)
            - (job.rusage_before.ru_utime + job.rusage_before.ru_stime), 3)
    return out


# ------------------------------------------------------------------- network isolation

# `unshare --user --map-current-user --net` puts the child in a fresh network namespace with
# no configured interfaces, so every outbound connection fails with ENETUNREACH. Three parts,
# each chosen for a reason:
#
#   --user              creates a USER namespace first, which is what makes the rest work
#                       without root. Creating a bare network namespace needs CAP_SYS_ADMIN;
#                       verified: `unshare --net` alone returns "Operation not permitted".
#   --map-current-user  keeps the child's uid. The more common `--map-root-user` makes the
#                       child believe it is uid 0 — harmless on the host, since that maps back
#                       to the real user, but a script that branches on `geteuid() == 0` would
#                       take a privileged path it has no business taking.
#   --net               the actual isolation.
#
# This blocks loopback too, because a fresh namespace's `lo` exists but is DOWN. That is not a
# bug to work around: a capability that talks to LM Studio on 127.0.0.1 genuinely needs the
# network, and the policy has to say so rather than have the harness quietly punch a hole.
NETNS_PREFIX = ("unshare", "--user", "--map-current-user", "--net", "--")

_netns_available: bool | None = None


def network_isolation_available() -> bool:
    """Can this machine isolate a child's network? Probed once, then cached.

    Probed rather than assumed: `unshare` exists on essentially every Linux, but whether an
    *unprivileged* user may create namespaces depends on kernel configuration and on the
    container the harness might be running inside. A control whose availability is assumed is
    a control that silently is not there.
    """
    global _netns_available
    if _netns_available is not None:
        return _netns_available
    if WINDOWS:
        _netns_available = False
        return False
    import shutil

    if not shutil.which("unshare"):
        _netns_available = False
        return False
    try:
        probe = subprocess.run(  # noqa: S603
            [*NETNS_PREFIX, "true"], capture_output=True, timeout=15,
        )
        _netns_available = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _netns_available = False
    return _netns_available


def _resume_process(pid: int) -> int:
    """Resume every thread of ``pid``. Returns how many threads were resumed.

    A process created with ``CREATE_SUSPENDED`` has exactly one thread, but this resumes
    all of them rather than assuming that, because "exactly one" is an assumption about
    someone else's loader.

    ``subprocess`` does not expose the thread handle ``CreateProcess`` returned, so the
    thread is found by snapshotting system threads and matching the owner PID. This is
    the price of not reimplementing ``CreateProcess`` via ctypes, and it is paid once per
    confined call.
    """
    win = _win()
    ctypes, k32 = win["ctypes"], win["k32"]

    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == INVALID_HANDLE_VALUE or not snapshot:
        raise ConfinementError(f"CreateToolhelp32Snapshot failed (error {ctypes.get_last_error()})")

    resumed = 0
    try:
        entry = win["THREADENTRY32"]()
        entry.dwSize = ctypes.sizeof(entry)
        if not k32.Thread32First(snapshot, ctypes.byref(entry)):
            raise ConfinementError("Thread32First found no threads at all")
        while True:
            if entry.th32OwnerProcessID == pid:
                thread = k32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    try:
                        if k32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        k32.CloseHandle(thread)
            if not k32.Thread32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snapshot)

    if resumed == 0:
        raise ConfinementError(f"could not resume any thread of pid {pid}")
    return resumed


def spawn_confined(argv: list[str], limits: Limits, *, cwd: str | None = None,
                   env: dict[str, str] | None = None,
                   stdout: Any = subprocess.PIPE,
                   stderr: Any = subprocess.PIPE,
                   isolate_network: bool = False) -> tuple[subprocess.Popen, Any, str]:
    """Spawn ``argv`` with ``limits`` enforced by the kernel.

    Returns ``(process, handle, note)``. ``handle`` is ``None`` when confinement was not
    applied, and ``note`` says why — the caller decides whether an unconfined run is
    acceptable, because that is a policy question rather than a mechanism question.

    On **Windows** the mechanism is a Job Object, and the ordering is the whole point:

    1. create the job with its limits
    2. create the process **suspended** — it has not executed anything yet
    3. assign it to the job
    4. only now resume it

    A child therefore cannot spawn an unconfined grandchild in the gap between steps 2
    and 3, because in that gap it is not running. If step 3 or 4 fails the process is
    terminated while still suspended, so a confinement failure means nothing ran.

    On **POSIX** the mechanism is ``setrlimit`` applied in the child between fork and exec,
    plus a new session so the whole process group can be signalled. See
    :func:`_posix_preexec` for what that does and does not guarantee.

    ``isolate_network`` requests a fresh network namespace. It is honoured on POSIX where
    the kernel permits it and is **impossible on Windows** without AppContainer, so the
    returned ``note`` distinguishes "isolated" from "requested but unavailable" rather than
    letting the caller assume egress was blocked.
    """
    if not WINDOWS:
        return _spawn_confined_posix(argv, limits, cwd=cwd, env=env,
                                    stdout=stdout, stderr=stderr,
                                    isolate_network=isolate_network)

    if not limits.any_set():
        proc = subprocess.Popen(argv, cwd=cwd, env=env, shell=False,  # noqa: S603
                                stdout=stdout, stderr=stderr)
        return proc, None, "no limits configured"

    try:
        job = create_job(limits)
    except ConfinementError as exc:
        return (subprocess.Popen(argv, cwd=cwd, env=env, shell=False,  # noqa: S603
                                 stdout=stdout, stderr=stderr),
                None, f"job creation failed: {exc}")

    proc = subprocess.Popen(  # noqa: S603 - argv array, shell=False, validated upstream
        argv, cwd=cwd, env=env, shell=False,
        stdout=stdout, stderr=stderr,
        creationflags=CREATE_SUSPENDED,
    )

    win = _win()
    ctypes, k32 = win["ctypes"], win["k32"]
    try:
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):  # noqa: SLF001
            raise ConfinementError(
                f"AssignProcessToJobObject failed (error {ctypes.get_last_error()})"
            )
        _resume_process(proc.pid)
    except ConfinementError:
        # The child is still suspended and has run nothing. Kill it rather than resuming
        # it unconfined: silently downgrading confinement is how a control becomes
        # decorative.
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        close_job(job)
        raise

    return proc, job, "confined"


def limits_from_policy(settings: dict[str, Any], trust: str | None = None) -> Limits:
    """Build :class:`Limits` from policy settings, tightened for low-trust callers.

    Two layers on purpose. ``confinement`` in settings is the default for everything;
    ``confinementByTrust`` narrows it per trust level, because the caller we trust least
    is the one whose runaway loop we most want bounded — and the Owner at a terminal
    should not have his own backup killed for using memory.
    """
    base = dict(settings.get("confinement") or {})
    if trust:
        override = (settings.get("confinementByTrust") or {}).get(trust)
        if isinstance(override, dict):
            base.update(override)
    return Limits(
        memory_bytes=base.get("maxMemoryBytes"),
        job_memory_bytes=base.get("maxJobMemoryBytes"),
        active_processes=base.get("maxActiveProcesses"),
        cpu_seconds=base.get("maxCpuSeconds"),
        max_file_bytes=base.get("maxFileBytes"),
    )


def _selftest() -> int:
    """Prove confinement is real: allocate past the limit and expect to fail.

    Run directly (``python scripts/harness_confine.py``) as a manual check on a machine
    whose behaviour you doubt. The automated version lives in test_harness_guards.py.
    """
    if not WINDOWS:
        print("not Windows; nothing to test")
        return 0
    limits = Limits(memory_bytes=64 * 1024 * 1024, active_processes=4)
    code = "b = bytearray(400 * 1024 * 1024); print(len(b))"
    proc, job, note = spawn_confined([sys.executable, "-c", code], limits)
    out, err = proc.communicate(timeout=60)
    usage = query_job(job)
    close_job(job)
    print(f"note={note} exit={proc.returncode} usage={usage}")
    print(f"stdout={out[:200]!r}")
    print(f"stderr={err[:300]!r}")
    print("CONFINED OK" if proc.returncode != 0 else "NOT CONFINED — allocation succeeded")
    return 0 if proc.returncode != 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
