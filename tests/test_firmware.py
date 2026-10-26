"""suspend-firmware, relock-firmware and the capsule trigger."""

import json
import os
import struct

import efi
import pytest

RELOCK = "pcrlock-firmware-relock.service"
EXEC = "/usr/lib/systemd/systemd-pcrlock make-policy --location=791:940- --strict=yes"


def dropin(sb):
    return sb.dropin_dir / "zz-pcrlock-toolkit-suspend-firmware.conf"


def state(sb):
    return sb.state / "firmware-suspended"


def suspended_execstart(sb):
    lines = dropin(sb).read_text().splitlines()
    assert "[Service]" in lines and "ExecStart=" in lines
    return [ln for ln in lines if ln.startswith("ExecStart=") and ln != "ExecStart="][-1]


# --- suspend --------------------------------------------------------------------------

def test_suspend_needs_relock_unit(sb):
    r = sb.run("suspend-firmware")
    assert r.returncode == 1
    assert f"{RELOCK} is not enabled" in r.stderr
    assert not dropin(sb).exists() and not state(sb).exists()


def test_suspend_default_pcrs(sb):
    sb.enable(RELOCK)
    r = sb.ok("suspend-firmware")
    assert suspended_execstart(sb) == (
        f"ExecStart={EXEC} --pcr=4 --pcr=5 --pcr=7 --pcr=11 --pcr=13 --pcr=14 --pcr=15")
    st = dict(ln.split("=", 1) for ln in state(sb).read_text().splitlines())
    assert st["PCRs"] == "0,1,2,3" and "Since" in st
    assert sb.calls("systemctl")[-2:] == [
        "systemctl daemon-reload", "systemctl restart systemd-pcrlock-make-policy.service"]
    assert "Stage the firmware update now" in r.stdout


def test_suspend_explicit_pcrs_and_existing_mask(sb):
    sb.enable(RELOCK)
    sb.execstart(f"{EXEC} --pcr=0 --pcr=4 --pcr=7")
    sb.ok("suspend-firmware", "0-3,7")
    assert suspended_execstart(sb) == f"ExecStart={EXEC} --pcr=4"
    assert "PCRs=0,7" in state(sb).read_text()


def test_suspend_pcrs_from_config(sb):
    sb.enable(RELOCK)
    sb.conf("SuspendPCRs=0+2\n")
    sb.ok("suspend-firmware")
    assert "PCRs=0,2" in state(sb).read_text()


def test_suspend_escapes_percent(sb):
    sb.enable(RELOCK)
    sb.execstart(f"{EXEC} --policy=/run/%i.json --pcr=0 --pcr=4")
    sb.ok("suspend-firmware")
    assert suspended_execstart(sb) == f"ExecStart={EXEC} --policy=/run/%%i.json --pcr=4"


def test_suspend_twice_starts_from_real_command_line(sb):
    sb.enable(RELOCK)
    sb.ok("suspend-firmware", "0")
    sb.ok("suspend-firmware", "1")
    words = suspended_execstart(sb).split()
    assert "--pcr=0" in words
    assert "--pcr=1" not in words


def test_suspend_nothing_to_suspend(sb):
    sb.enable(RELOCK)
    sb.execstart(f"{EXEC} --pcr=4")
    r = sb.run("suspend-firmware")
    assert r.returncode == 1
    assert "none of PCRs 0 1 2 3 is in the policy (PCRs 4)" in r.stderr


def test_suspend_rolls_back_when_make_policy_fails(sb):
    sb.enable(RELOCK)
    sb.fail("systemd-pcrlock-make-policy.service")
    r = sb.run("suspend-firmware")
    assert r.returncode == 1
    assert "failed; nothing was suspended" in r.stderr
    assert not dropin(sb).exists() and not state(sb).exists()


@pytest.mark.parametrize("args, message", [
    (["--dry-run", "suspend-firmware"], "suspend-firmware doesn't support --dry-run"),
    (["--dry-run", "relock-firmware"], "relock-firmware doesn't support --dry-run"),
    (["suspend-firmware", "zero"], "invalid PCR list"),
])
def test_suspend_relock_errors(sb, args, message):
    sb.enable(RELOCK)
    r = sb.run(*args)
    assert r.returncode == 1
    assert message in r.stderr


def test_list_and_check_warn_while_suspended(sb):
    sb.enable(RELOCK)
    sb.ok("suspend-firmware")
    assert "firmware PCRs are suspended (PCRs=0,1,2,3" in sb.run("list").stderr
    assert "run 'pcrlock relock-firmware'" in sb.run("check").stderr


# --- relock ---------------------------------------------------------------------------

def firmware_log():
    return [{"pcr": p, "digests": [{"hashAlg": "sha256", "digest": f"{p:02x}" * 32}],
             "content_type": "pcclient_std",
             "content": {"event_type": "EV_POST_CODE", "event_data": f"{p:02x}"}}
            for p in (0, 1, 2, 3)]


def test_relock_nothing_to_do(sb):
    r = sb.ok("relock-firmware")
    assert "firmware PCRs aren't suspended, nothing to do" in r.stdout
    assert sb.calls("systemctl restart") == []


def test_relock_regenerates_and_rebuilds(sb):
    sb.enable(RELOCK)
    sb.cel(firmware_log())
    sb.component("260-crtm.conf", "Type=log\nPCR=0\n")
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    # systemd's own firmware-code component is in use.
    sb.varlib.mkdir(parents=True)
    (sb.varlib / "250-firmware-code-early.pcrlock").write_text(json.dumps({"records": []}))
    sb.ok("suspend-firmware")
    sb.reset_calls()
    r = sb.ok("relock-firmware")
    assert not dropin(sb).exists() and not state(sb).exists()
    assert "systemd-pcrlock lock-firmware-code" in sb.calls()
    assert not any(c.startswith("systemd-pcrlock lock-firmware-config") for c in sb.calls())
    assert (sb.pcrlock_d / "260-crtm.pcrlock").exists()
    # Only components on the suspended PCRs are regenerated.
    assert not (sb.pcrlock_d / "799-seal.pcrlock").exists()
    assert sb.calls("systemctl restart") == ["systemctl restart systemd-pcrlock-make-policy.service"]
    assert "PCRs 0 1 2 3 are locked again" in r.stdout


def test_relock_refuses_stale_hand_made_component(sb):
    sb.enable(RELOCK)
    sb.cel(firmware_log())
    sb.write_json(sb.pcrlock_d / "550-firmware-config-late.pcrlock.d/a.pcrlock",
                  {"records": [{"pcr": 1, "digests": [{"hashAlg": "sha256", "digest": "old"}]}]})
    sb.ok("suspend-firmware")
    r = sb.run("relock-firmware")
    assert r.returncode == 1
    assert "no variant of 550-firmware-config-late matches this boot" in r.stderr
    assert "PCRs 0 1 2 3 stay suspended" in r.stderr
    assert state(sb).exists()


def test_relock_keeps_state_when_make_policy_fails(sb):
    sb.enable(RELOCK)
    sb.ok("suspend-firmware")
    sb.fail("systemd-pcrlock-make-policy.service")
    r = sb.run("relock-firmware")
    assert r.returncode == 1
    assert "PCRs 0 1 2 3 stay suspended" in r.stderr
    assert state(sb).exists()


def test_relock_after_reboot_uses_state_file(sb):
    # After a reboot the runtime drop-in is gone, but the state file says
    # which PCRs to lock again.
    sb.enable(RELOCK)
    sb.ok("suspend-firmware", "0,2")
    dropin(sb).unlink()
    r = sb.ok("relock-firmware")
    assert "PCRs 0 2 are locked again" in r.stdout
    assert not state(sb).exists()


def test_relock_rejects_unknown_state_key(sb):
    sb.state.mkdir(parents=True)
    state(sb).write_text("PCRs=0\nColour=red\n")
    r = sb.run("relock-firmware")
    assert r.returncode == 1
    assert "firmware-suspended:2: unknown key 'Colour'" in r.stderr


# --- capsule trigger ----------------------------------------------------------------------

CLASS = "ddc0ee61-e7f0-4e7d-acc5-c070a398838e"


def update_info(status):
    """EFI_UPDATE_INFO: version, GUID, flags, hw_inst, EFI_TIME, status, path."""
    return (struct.pack("<I", 7) + efi.guid_bytes(CLASS) + struct.pack("<IQ", 0, 0)
            + bytes(16) + struct.pack("<I", status) + b"\x7f\xff\x04\x00")


def stage(sb, status=1):
    sb.efivar(f"fwupd-{CLASS}-0", efi.FWUPD, update_info(status))
    esp = sb.root / "esp/EFI/arch/fw"
    esp.mkdir(parents=True, exist_ok=True)
    (esp / f"fwupd-{CLASS}.cap").write_bytes(b"capsule")


def test_capsule_nothing_staged(sb):
    sb.enable(RELOCK)
    stage(sb, status=0)
    r = sb.ok("suspend-firmware", "--if-capsule-pending")
    assert "no UEFI capsule update is staged" in r.stdout
    assert not state(sb).exists()


def test_capsule_staged_suspends_once(sb):
    sb.enable(RELOCK)
    stage(sb)
    r = sb.ok("suspend-firmware", "--if-capsule-pending")
    assert f"UEFI capsule update staged: efivar fwupd-{CLASS}-0-{efi.FWUPD};file arch/fw/" in r.stdout
    assert "out of the policy for the staged update" in r.stdout
    assert state(sb).exists() and (sb.state / "capsule-suspended").exists()

    r = sb.ok("suspend-firmware", "--if-capsule-pending")
    assert "firmware PCRs are already suspended" in r.stdout

    # Relocked, but the firmware didn't apply the capsule: no second time.
    state(sb).unlink()
    r = sb.ok("suspend-firmware", "--if-capsule-pending")
    assert "already suspended for once and is still pending" in r.stderr
    assert not state(sb).exists()

    # Staging it again rewrites the capsule file.
    cap = sb.root / f"esp/EFI/arch/fw/fwupd-{CLASS}.cap"
    os.utime(cap, (cap.stat().st_atime, cap.stat().st_mtime + 60))
    sb.ok("suspend-firmware", "--if-capsule-pending")
    assert state(sb).exists()


def test_capsule_on_disk(sb):
    sb.enable(RELOCK)
    sb.efivar("OsIndications", efi.EFI_GLOBAL, struct.pack("<Q", 0x4))
    r = sb.ok("suspend-firmware", "--if-capsule-pending")
    assert "capsule-on-disk" in r.stdout
    assert "can't find the ESP" in r.stderr
    assert state(sb).exists()


def test_os_indications_without_capsule_bit(sb):
    sb.enable(RELOCK)
    sb.efivar("OsIndications", efi.EFI_GLOBAL, struct.pack("<Q", 0x1))
    assert "no UEFI capsule update is staged" in sb.ok("suspend-firmware", "--if-capsule-pending").stdout
