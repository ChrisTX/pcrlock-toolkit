"""Checks against a real event log, skipped unless one was saved locally:

    sudo /usr/lib/systemd/systemd-pcrlock cel > tests/local/cel.json

The dump contains the machine-id, volume UUIDs and boot IDs, so tests/local/
is ignored by git. The Secure Boot variables are read from this machine's
efivarfs, which needs no root. These tests check the harness's model of the
firmware against reality: if the stub's UEFI_VARIABLE_DATA encoding or the
SbatLevel record differed from what the firmware and shim logged, they fail.
"""

import json
import subprocess
from pathlib import Path

import efi
import pytest
from conftest import REPO

LOCAL_CEL = REPO / "tests/local/cel.json"
REAL_EFIVARS = Path("/sys/firmware/efi/efivars")

pytestmark = pytest.mark.skipif(not LOCAL_CEL.exists(),
                                reason="no tests/local/cel.json (see module docstring)")


@pytest.fixture
def real(sb):
    sb.cel(json.loads(LOCAL_CEL.read_text()))
    names = [n for n, _ in efi.SECUREBOOT_POLICY_VARS] + ["SbatLevelRT"]
    for p in REAL_EFIVARS.iterdir():
        if p.name.rsplit("-", 5)[0] in names:
            (sb.efivars / p.name).write_bytes(p.read_bytes())
    return sb


def test_decoder_handles_every_record():
    out = subprocess.run([REPO / "libexec/cel-decode", str(LOCAL_CEL)], capture_output=True,
                         check=True, text=True).stdout
    errors = [r for r in json.loads(out) if "error" in r.get("decoded", {})]
    assert errors == []


def test_secureboot_policy_matches_the_firmware(real):
    real.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    real.ok("generate")


def test_sbatlevel_record_found_once(real):
    if not (real.efivars / f"SbatLevelRT-{efi.SHIM_LOCK}").exists():
        pytest.skip("not booted through shim")
    # The current shim's .sbatlevel is irrelevant here; a payload that can't
    # win keeps the current level, so the record must be found and kept.
    shim = real.root / "shim.efi"
    shim.write_bytes(efi.pe_with_sbatlevel("sbat,1,2000010100\n", "sbat,1,2000010100\n"))
    real.component("620-secureboot-authority.conf", f"Type=secureboot-authority\nPath={shim}\n")
    real.ok("generate")


def test_log_components_find_records(real):
    real.component("600-gpt-and-loader.conf", "Type=log\nPCR=5\n")
    real.component("640-mok.conf", 'Type=log\nPCR=14\nFilter=.content_type != "systemd"\n')
    real.ok("generate")


def test_volume_keys_from_real_records(real):
    log = json.loads(LOCAL_CEL.read_text())
    keys = {r["content"]["string"].split(":")[1]: r["digests"]
            for r in log if r.get("content", {}).get("eventType") == "volume-key"}
    slots = {r["content"]["string"].split(":")[1] for r in log
             if r.get("content", {}).get("eventType") == "keyslot"}
    if not keys:
        pytest.skip("no volume keys measured in this log")
    out = real.ok("volume-keys").stdout
    rows = {ln.split()[0]: ln.split() for ln in out.splitlines()[1:] if ln.strip()}
    for name, digests in keys.items():
        sha = next(d["digest"] for d in digests if d["hashAlg"] == "sha256")
        assert rows[name][-1] == sha
        # Unlock method and slot from the NvPCR keyslot record, if measured.
        assert (rows[name][1] != "-") == (name in slots)
