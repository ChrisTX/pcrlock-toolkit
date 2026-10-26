"""pcrlock prune, uki add/remove and list."""

import hashlib
import json

import pytest
from conftest import digest_of


def sha(b):
    return hashlib.sha256(b).hexdigest()


def booted(sb, *blobs):
    sb.cel([{"pcr": 4, "digests": [{"hashAlg": "sha256", "digest": sha(b)}]} for b in blobs])


def variant(path, data, pcr=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": [
        {"pcr": pcr, "digests": [{"hashAlg": "sha256", "digest": sha(data)}]}]}))
    return path


# --- prune ---------------------------------------------------------------------------

def test_prune_previous(sb):
    booted(sb, b"running")
    keep = variant(sb.pcrlock_d / "625-shim.pcrlock.d/shim-previous.pcrlock", b"running")
    drop = variant(sb.pcrlock_d / "630-sd.pcrlock.d/sd-previous.pcrlock", b"old")
    current = variant(sb.pcrlock_d / "630-sd.pcrlock.d/sd.pcrlock", b"new")
    r = sb.ok("prune")
    assert keep.exists() and current.exists() and not drop.exists()
    assert "removed 630-sd.pcrlock.d/sd-previous.pcrlock (previous variant, not booted)" in r.stdout
    assert sb.calls("systemctl restart")


def test_prune_uki_of_removed_kernels(sb):
    booted(sb, b"uki-6.1")
    uki = sb.pcrlock_d / "650-uki.pcrlock.d"
    running = variant(uki / "6.1.pcrlock", b"uki-6.1")
    gone = variant(uki / "6.0.pcrlock", b"uki-6.0")
    installed = variant(uki / "6.2.pcrlock", b"uki-6.2")
    (sb.modules / "6.2").mkdir()
    r = sb.ok("prune")
    assert running.exists() and installed.exists() and not gone.exists()
    assert "removed 650-uki.pcrlock.d/6.0.pcrlock (kernel 6.0 no longer installed)" in r.stdout


def test_prune_uki_component_name_from_config(sb):
    sb.conf("UkiComponent=700-kernels\n")
    booted(sb)
    gone = variant(sb.pcrlock_d / "700-kernels.pcrlock.d/6.0.pcrlock", b"x")
    sb.ok("prune")
    assert not gone.exists()


def test_prune_dry_run(sb):
    booted(sb)
    drop = variant(sb.pcrlock_d / "630-sd.pcrlock.d/sd-previous.pcrlock", b"old")
    r = sb.ok("--dry-run", "prune")
    assert drop.exists()
    assert "would remove 630-sd.pcrlock.d/sd-previous.pcrlock" in r.stdout
    assert sb.calls("systemctl restart") == []


def test_prune_nothing_to_do(sb):
    booted(sb, b"x")
    variant(sb.pcrlock_d / "625-shim.pcrlock.d/shim-previous.pcrlock", b"x")
    sb.ok("prune")
    assert sb.calls("systemctl restart") == []


# --- uki -------------------------------------------------------------------------------

def test_uki_add_merges_kernel(sb):
    (sb.root / "uki.efi").write_bytes(b"uki")
    (sb.root / "vmlinuz").write_bytes(b"kernel")
    sb.ok("uki", "add", "6.1.0-arch1", str(sb.root / "uki.efi"), str(sb.root / "vmlinuz"))
    doc = sb.read_json(sb.pcrlock_d / "650-uki.pcrlock.d/6.1.0-arch1.pcrlock")
    assert digest_of(doc) == [sha(b"uki"), sha(b"kernel")]
    assert sb.calls("systemctl restart")


def test_uki_add_without_kernel(sb):
    (sb.root / "uki.efi").write_bytes(b"uki")
    sb.ok("uki", "add", "6.1", str(sb.root / "uki.efi"))
    assert digest_of(sb.read_json(sb.pcrlock_d / "650-uki.pcrlock.d/6.1.pcrlock")) == [sha(b"uki")]


def test_uki_add_rebuilt_running_kernel_keeps_previous(sb):
    (sb.root / "uki.efi").write_bytes(b"uki-a")
    booted(sb, b"uki-a")
    sb.ok("uki", "add", "6.1", str(sb.root / "uki.efi"))
    (sb.root / "uki.efi").write_bytes(b"uki-b")
    sb.ok("uki", "add", "6.1", str(sb.root / "uki.efi"))
    d = sb.pcrlock_d / "650-uki.pcrlock.d"
    assert digest_of(sb.read_json(d / "6.1-previous.pcrlock")) == [sha(b"uki-a")]
    assert digest_of(sb.read_json(d / "6.1.pcrlock")) == [sha(b"uki-b")]


@pytest.mark.parametrize("kver, message", [
    ("../x", "invalid kernel version '../x'"),
    (".hidden", "invalid kernel version '.hidden'"),
    ("6.1-previous", "clashes with the -previous variant name"),
])
def test_uki_invalid_kver(sb, kver, message):
    (sb.root / "uki.efi").write_bytes(b"uki")
    r = sb.run("uki", "add", kver, str(sb.root / "uki.efi"))
    assert r.returncode == 1
    assert message in r.stderr


@pytest.mark.parametrize("args, message", [
    (["uki"], "usage: pcrlock uki add|remove"),
    (["uki", "add", "6.1"], "usage: pcrlock uki add KVER UKI [KERNEL]"),
    (["uki", "remove"], "usage: pcrlock uki remove KVER"),
    (["uki", "add", "6.1", "/nonexistent"], "/nonexistent not readable"),
])
def test_uki_usage(sb, args, message):
    r = sb.run(*args)
    assert r.returncode == 1
    assert message in r.stderr


def test_uki_remove(sb):
    booted(sb, b"running")
    d = sb.pcrlock_d / "650-uki.pcrlock.d"
    running = variant(d / "6.1.pcrlock", b"running")
    other = variant(d / "6.0.pcrlock", b"other")
    r = sb.ok("uki", "remove", "6.1")
    assert running.exists()
    assert "kernel 6.1 is the current boot; 'pcrlock prune' removes it after the next boot" in r.stdout
    assert sb.calls("systemctl restart") == []
    r = sb.ok("uki", "remove", "6.0")
    assert not other.exists()
    assert "removed 650-uki.pcrlock.d/6.0.pcrlock (kernel 6.0 removed)" in r.stdout
    assert sb.calls("systemctl restart")
    sb.reset_calls()
    sb.ok("uki", "remove", "5.0")
    assert sb.calls("systemctl restart") == []


# --- list --------------------------------------------------------------------------------

def test_list_states(sb):
    booted(sb, b"running")
    sb.component("625-shim.conf", f"Type=pe\nPath={sb.root}/shim.efi\n")
    sb.component("630-sd.conf", f"Type=pe\nPath={sb.root}/sd.efi\n")
    sb.component("640-mok.conf", "Type=log\nPCR=14\n")
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\nEnabled=no\n")
    variant(sb.pcrlock_d / "625-shim.pcrlock.d/shim.pcrlock", b"running")
    variant(sb.pcrlock_d / "630-sd.pcrlock.d/sd.pcrlock", b"new")
    rows = [ln.split() for ln in sb.ok("list").stdout.splitlines()]
    assert rows[0] == ["PRIO", "NAME", "TYPE", "STATE", "OUTPUT"]
    assert rows[1:] == [
        ["625", "shim", "pe", "booted", "625-shim.pcrlock.d/shim.pcrlock"],
        ["630", "sd", "pe", "present", "630-sd.pcrlock.d/sd.pcrlock"],
        ["640", "mok", "log", "missing", "640-mok.pcrlock"],
        ["799", "seal", "raw", "disabled", "799-seal.pcrlock"],
    ]
