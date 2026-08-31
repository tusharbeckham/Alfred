"""Verify the argv-only wrappers Ultron will use, independently of what launches them.

Node is not installed in this WSL, so the Node code path cannot be executed here. But
`prlimit` and `unshare` are external programs: what they do to a child does not depend on
whether Python or Node spawned them. Verifying the wrappers here, and unit-testing the argv
Ultron builds, is the honest decomposition - and the gap (no end-to-end Node-on-Linux run) is
stated rather than glossed.
"""
import subprocess
import sys

FAILURES = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)


def run(argv, timeout=90):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


ALLOC = [sys.executable, "-c", "b = bytearray(400*1024*1024); print(len(b))"]
NET = [sys.executable, "-c",
       "import socket; s=socket.socket(); s.settimeout(4); print('CONNECT', s.connect_ex(('1.1.1.1',80)))"]

print("--- prlimit as an argv-only confinement wrapper ---")
rc, out, _ = run(ALLOC)
check("CONTROL: the allocation succeeds unwrapped", rc == 0 and "419430400" in out, f"rc={rc}")

rc, out, err = run(["prlimit", "--as=67108864", "--", *ALLOC])
check("prlimit --as refuses an over-limit allocation", rc != 0, f"rc={rc}")
check("the failure is a MemoryError", "MemoryError" in err, err.splitlines()[-1][:50] if err else "")

rc, out, err = run(["prlimit", "--cpu=1", "--", sys.executable, "-c", "x=0\nwhile True: x+=1"])
check("prlimit --cpu kills a spinning child", rc != 0, f"rc={rc}")

rc, out, err = run(["prlimit", "--fsize=1024", "--", sys.executable, "-c",
                    "open('/tmp/ultron-fsize-probe','wb').write(b'x'*10_000_000); print('WROTE')"])
check("prlimit --fsize refuses an over-limit write", rc != 0 and "WROTE" not in out, f"rc={rc}")

rc, out, err = run(["prlimit", "--nproc=1", "--", sys.executable, "-c",
                    "import subprocess,sys\n"
                    "try:\n"
                    "    subprocess.run([sys.executable,'-c','pass'],timeout=20); print('SPAWNED')\n"
                    "except Exception as e: print('BLOCKED', type(e).__name__)\n"])
check("prlimit --nproc bounds a fork bomb", "SPAWNED" not in out, out[:40])

rc, out, err = run(["prlimit", "--as=536870912", "--", sys.executable, "-c", "print('ok')"])
check("a confined child still runs normally", rc == 0 and "ok" in out, f"rc={rc}")

print("\n--- unshare as an argv-only egress wrapper ---")
rc, out, _ = run(NET)
control_has_net = "CONNECT 0" in out
check("CONTROL: the probe reaches the network unwrapped", control_has_net, out[:30])

rc, out, err = run(["unshare", "--user", "--map-current-user", "--net", "--", *NET])
check("unshare --net blocks egress", rc == 0 and "CONNECT 0" not in out, out[:30] or err[:40])

rc, out, err = run(["unshare", "--user", "--map-current-user", "--net", "--",
                    sys.executable, "-c", "import os; print('uid', os.getuid())"])
check("the child keeps its real uid", "uid 0" not in out, out[:24])

print("\n--- both wrappers composed, in the order Ultron builds them ---")
rc, out, err = run(["unshare", "--user", "--map-current-user", "--net", "--",
                    "prlimit", "--as=536870912", "--", sys.executable, "-c",
                    "import socket,os\n"
                    "s=socket.socket(); s.settimeout(3)\n"
                    "print('uid', os.getuid(), 'connect', s.connect_ex(('1.1.1.1',80)))\n"])
check("egress + rlimits compose", rc == 0 and "connect 0" not in out, out[:44] or err[:44])

print()
print("NOTE: Node is not installed in this WSL, so the Node code path was NOT executed here.")
print("      These checks verify the WRAPPERS; test/guards.test.mjs verifies the argv Ultron")
print("      builds. The end-to-end Node-on-Linux run remains unverified.")
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("ALL WRAPPER CHECKS PASSED")
