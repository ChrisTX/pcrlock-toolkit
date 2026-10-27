"""Test harness for bin/pcrlock.

Every test gets a Sandbox: a fake root with /etc/pcrlock-toolkit,
/etc/pcrlock.d, /var/lib/pcrlock.d, efivars, a state directory and an event
log, plus stubs for systemd-pcrlock, systemctl, bootctl and
platsec-pcr-check (tests/stubs, see their docstrings). The command under test
is a copy of bin/pcrlock whose absolute paths are rewritten into the
sandbox. These tests pin the bash implementation's behaviour so that a port
can be checked against them: only Sandbox.run() knows which implementation
runs.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUBS = REPO / "tests" / "stubs"
DEFAULT_EXECSTART = ("/usr/lib/systemd/systemd-pcrlock make-policy "
                     "--location=791:940- --strict=yes")

# Prefixes of real system paths that must end up inside the sandbox. A
# rewrite that doesn't happen anymore (because the code changed) fails
# loudly instead of touching the real system.
SANDBOXED = ["/var/lib/pcrlock", "/etc/pcrlock", "/etc/crypttab",
             "/sys/firmware/efi/efivars", "/run/systemd/system",
             "/usr/lib/modules"]


def patched_pcrlock(root):
    src = (REPO / "bin" / "pcrlock").read_text()
    exact = [
        ("readonly PCRLOCK=/usr/lib/systemd/systemd-pcrlock",
         f"readonly PCRLOCK={STUBS}/systemd-pcrlock"),
        ("readonly CEL_DECODE=/usr/lib/pcrlock-toolkit/cel-decode",
         f"readonly CEL_DECODE={REPO}/libexec/cel-decode"),
        ("((EUID == 0)) ||", "true ||"),
    ]
    for old, new in exact:
        assert old in src, f"bin/pcrlock no longer contains {old!r}; update the harness"
        src = src.replace(old, new)
    pattern = re.compile("(?<![\\w/.-])(" + "|".join(map(re.escape, SANDBOXED)) + ")")
    src, n = pattern.subn(lambda m: f"{root}{m.group(1)}", src)
    assert n > 0
    for prefix in SANDBOXED:
        leftover = re.search(f"(?<!{re.escape(str(root))}){re.escape(prefix)}", src)
        assert leftover is None, f"unsandboxed {prefix} in bin/pcrlock"
    return src


class Sandbox:
    def __init__(self, root):
        self.root = root
        self.config_dir = root / "etc/pcrlock-toolkit"
        self.components_dir = self.config_dir / "components.d"
        self.pcrlock_d = root / "etc/pcrlock.d"
        self.varlib = root / "var/lib/pcrlock.d"
        self.efivars = root / "sys/firmware/efi/efivars"
        self.state = root / "var/lib/pcrlock-toolkit"
        self.dropin_dir = root / "run/systemd/system/systemd-pcrlock-make-policy.service.d"
        self.modules = root / "usr/lib/modules"
        for d in (self.components_dir, self.pcrlock_d, self.efivars, root / "tmp",
                  root / "enabled", root / "fail", self.modules):
            d.mkdir(parents=True, exist_ok=True)
        self.bin = root / "bin/pcrlock"
        self.bin.parent.mkdir()
        self.bin.write_text(patched_pcrlock(root))
        self.bin.chmod(0o755)
        self.execstart(DEFAULT_EXECSTART)
        self.cel([])

    # --- setup -------------------------------------------------------------

    def conf(self, text):
        (self.config_dir / "pcrlock.conf").write_text(text)

    def component(self, filename, text):
        p = self.components_dir / filename
        p.write_text(text)
        return p

    def cel(self, records):
        (self.root / "cel.json").write_text(json.dumps(records))

    def efivar(self, name, guid, data, attrs=0x7):
        p = self.efivars / f"{name}-{guid}"
        p.write_bytes(attrs.to_bytes(4, "little") + data)
        return p

    def execstart(self, cmd):
        (self.root / "execstart").write_text(cmd + "\n")

    def enable(self, unit):
        (self.root / "enabled" / unit).touch()

    def fail(self, name):
        (self.root / "fail" / name).touch()

    def write_json(self, path, doc):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))
        return path

    # --- running -----------------------------------------------------------

    def env(self, **extra):
        return {
            "PATH": f"{STUBS}:/usr/bin:/bin",
            "STUB_ROOT": str(self.root),
            "STUB_COMPONENT_DIRS": f"{self.pcrlock_d}:{self.varlib}",
            "TMPDIR": str(self.root / "tmp"),
            "HOME": str(self.root),
            "LC_ALL": "C.UTF-8",
            **extra,
        }

    def run(self, *args, stdin=None):
        return subprocess.run([str(self.bin), *args], input=stdin, capture_output=True,
                              text=True, env=self.env(), timeout=60, check=False)

    def ok(self, *args, **kw):
        r = self.run(*args, **kw)
        assert r.returncode == 0, f"rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        return r

    def calls(self, prog=None):
        p = self.root / "calls.log"
        lines = p.read_text().splitlines() if p.exists() else []
        return [ln for ln in lines if prog is None or ln.startswith(prog + " ")]

    def reset_calls(self):
        (self.root / "calls.log").unlink(missing_ok=True)

    def read_json(self, path):
        return json.loads(Path(path).read_text())


@pytest.fixture
def sb(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return Sandbox(root)


def digest_of(doc):
    """sha256 digests of a .pcrlock document's records, in order."""
    return [d["digest"] for r in doc["records"] for d in r["digests"]
            if d["hashAlg"] == "sha256"]


@pytest.fixture
def no_tmp_leak(sb):
    yield
    assert os.listdir(sb.root / "tmp") == [], "pcrlock left files in TMPDIR"
