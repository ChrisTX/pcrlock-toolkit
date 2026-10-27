"""kernel-install/99-pcrlock.install: which pcrlock calls it makes, and the
whole path through the sandboxed pcrlock."""

import hashlib
import subprocess

import pytest
from conftest import REPO
from test_variants import booted

PLUGIN = REPO / "kernel-install/99-pcrlock.install"


def plugin(sb, pcrlock):
    """The plugin with /usr/bin/pcrlock replaced by PCRLOCK."""
    src = PLUGIN.read_text()
    assert src.count("/usr/bin/pcrlock") == 2
    p = sb.root / "99-pcrlock.install"
    p.write_text(src.replace("/usr/bin/pcrlock", str(pcrlock)))
    p.chmod(0o755)
    return p


def recorder(sb):
    p = sb.root / "record-pcrlock"
    log = sb.root / "pcrlock-args"
    p.write_text(f'#!/bin/sh\nprintf "%s|" "$@" >>"{log}"\necho >>"{log}"\n')
    p.chmod(0o755)
    return p, log


def run(sb, script, *args, layout="uki", verbose="0"):
    staging = sb.root / "staging"
    staging.mkdir(exist_ok=True)
    env = sb.env(KERNEL_INSTALL_LAYOUT=layout, KERNEL_INSTALL_STAGING_AREA=str(staging),
                 KERNEL_INSTALL_VERBOSE=verbose)
    return subprocess.run([str(script), *args], capture_output=True, text=True, env=env,
                          timeout=60, check=False)


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_add_and_remove(sb):
    rec, log = recorder(sb)
    script = plugin(sb, rec)
    (sb.root / "staging/").mkdir()
    (sb.root / "staging/uki.efi").write_bytes(b"uki")
    assert run(sb, script, "add", "6.1.0", "/boot/x", "/usr/lib/modules/6.1.0/vmlinuz").returncode == 0
    assert run(sb, script, "remove", "6.1.0").returncode == 0
    staging = sb.root / "staging"
    assert calls(log) == [
        f"uki|add|6.1.0|{staging}/uki.efi|/usr/lib/modules/6.1.0/vmlinuz|",
        "uki|remove|6.1.0|",
    ]


def test_verbose_is_passed_on(sb):
    rec, log = recorder(sb)
    script = plugin(sb, rec)
    run(sb, script, "remove", "6.1.0", verbose="1")
    assert calls(log) == ["--verbose|uki|remove|6.1.0|"]


@pytest.mark.parametrize("layout", ["bls", "other"])
def test_other_layouts_are_ignored(sb, layout):
    rec, log = recorder(sb)
    script = plugin(sb, rec)
    assert run(sb, script, "add", "6.1.0", "/boot/x", "/vmlinuz", layout=layout).returncode == 0
    assert calls(log) == []


def test_prebuilt_uki_is_not_locked(sb):
    rec, log = recorder(sb)
    script = plugin(sb, rec)
    r = run(sb, script, "add", "6.1.0", "/boot/x", "/vmlinuz")
    assert r.returncode == 0
    assert "no UKI built in the staging area (prebuilt UKI?); not locking 6.1.0" in r.stderr
    assert calls(log) == []


def test_missing_arguments_fail(sb):
    rec, _ = recorder(sb)
    assert run(sb, plugin(sb, rec), "add").returncode != 0


def test_end_to_end_with_pcrlock(sb):
    script = plugin(sb, sb.bin)
    staging = sb.root / "staging"
    staging.mkdir()
    (staging / "uki.efi").write_bytes(b"uki-6.2")
    kernel = sb.root / "vmlinuz"
    kernel.write_bytes(b"kernel-6.2")
    r = run(sb, script, "add", "6.2.0", "/boot/x", str(kernel))
    assert r.returncode == 0, r.stderr
    variant = sb.pcrlock_d / "650-uki.pcrlock.d/6.2.0.pcrlock"
    doc = sb.read_json(variant)
    assert [d["digest"] for rec in doc["records"] for d in rec["digests"]] == [
        hashlib.sha256(b"uki-6.2").hexdigest(), hashlib.sha256(b"kernel-6.2").hexdigest()]
    # Removing a kernel that isn't the running one drops its variant.
    booted(sb, b"something else")
    assert run(sb, script, "remove", "6.2.0").returncode == 0
    assert not variant.exists()
