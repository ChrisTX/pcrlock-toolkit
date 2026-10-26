"""Type=secureboot-policy (only dbx may differ from the boot) and
Type=secureboot-authority (SbatLevel prediction from shim's .sbatlevel)."""

import json

import efi
import pytest
from conftest import digest_of

OWNER = "18b539e7-b53a-4f12-ab01-1437772323a3"


def setup_vars(sb, dbx=b""):
    sb.efivar("SecureBoot", efi.EFI_GLOBAL, b"\x01")
    sb.efivar("PK", efi.EFI_GLOBAL, efi.esl_sha256(OWNER, b"\x01" * 32))
    sb.efivar("KEK", efi.EFI_GLOBAL, efi.esl_sha256(OWNER, b"\x02" * 32))
    sb.efivar("db", efi.IMAGE_SECURITY_DB, efi.esl_sha256(OWNER, b"\x03" * 32))
    if dbx:
        sb.efivar("dbx", efi.IMAGE_SECURITY_DB, dbx)


def boot_log(sb, sbat_level=None):
    """PCR 7 as the firmware (and shim) measured the current efivars."""
    records = []
    for name, guid in efi.SECUREBOOT_POLICY_VARS:
        p = sb.efivars / f"{name}-{guid}"
        data = p.read_bytes()[4:] if p.exists() else b""
        records.append(efi.pcclient(7, "EV_EFI_VARIABLE_DRIVER_CONFIG",
                                    efi.var_event(guid, name, data)))
    records.append(efi.pcclient(7, "EV_SEPARATOR", b"\0\0\0\0"))
    records.append(efi.pcclient(7, "EV_EFI_VARIABLE_AUTHORITY",
                                efi.var_event(efi.IMAGE_SECURITY_DB, "db", b"cert")))
    if sbat_level is not None:
        records.append(efi.pcclient(7, "EV_EFI_VARIABLE_AUTHORITY",
                                    efi.var_event(efi.SHIM_LOCK, "SbatLevel", sbat_level.encode())))
    return records


# --- secureboot-policy -------------------------------------------------------------

def test_policy_matches_boot(sb):
    setup_vars(sb)
    sb.cel(boot_log(sb))
    sb.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "240-secureboot-policy.pcrlock.d/secureboot-policy.pcrlock")
    assert len(doc["records"]) == 5


def test_policy_accepts_dbx_update(sb):
    setup_vars(sb)
    sb.cel(boot_log(sb))
    sb.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    sb.ok("generate")
    # A runtime dbx update (missing before) only changes the dbx record; the
    # booted variant is kept.
    sb.efivar("dbx", efi.IMAGE_SECURITY_DB, efi.esl_sha256(OWNER, b"\x09" * 32))
    r = sb.ok("generate")
    assert "as secureboot-policy-previous.pcrlock" in r.stdout


@pytest.mark.parametrize("var, guid", [("db", efi.IMAGE_SECURITY_DB), ("KEK", efi.EFI_GLOBAL),
                                       ("PK", efi.EFI_GLOBAL)])
def test_policy_refuses_other_changes(sb, var, guid):
    setup_vars(sb)
    sb.cel(boot_log(sb))
    sb.efivar(var, guid, efi.esl_sha256(OWNER, b"\x0a" * 32))
    sb.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "Secure Boot variables other than dbx differ from the current boot" in r.stderr
    assert not (sb.pcrlock_d / "240-secureboot-policy.pcrlock.d").exists()


def test_policy_refuses_without_log(sb):
    setup_vars(sb)
    sb.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    assert sb.run("generate").returncode == 1


def test_policy_warns_about_varlib_duplicate(sb):
    setup_vars(sb)
    sb.cel(boot_log(sb))
    sb.varlib.mkdir(parents=True)
    (sb.varlib / "240-secureboot-policy.pcrlock").write_text("{}")
    sb.component("240-secureboot-policy.conf", "Type=secureboot-policy\n")
    r = sb.ok("generate")
    assert "240-secureboot-policy.pcrlock duplicates component 'secureboot-policy'" in r.stderr


# --- secureboot-authority ---------------------------------------------------------------

CURRENT = "sbat,1,2024010900\nshim,4\ngrub,3\n"
NEWER = "sbat,1,2025051000\nshim,4\ngrub,5\n"
OLDER = "sbat,1,2023012900\nshim,2\n"


def authority(sb, current, shim_auto, shim_latest=None, policy=None):
    setup_vars(sb)
    sb.efivar("SbatLevelRT", efi.SHIM_LOCK, current.encode())
    if policy is not None:
        sb.efivar("SbatPolicy", efi.SHIM_LOCK, bytes([policy]))
    sb.cel(boot_log(sb, sbat_level=current))
    shim = sb.root / "shimx64.efi"
    shim.write_bytes(efi.pe_with_sbatlevel(shim_auto, shim_latest or shim_auto))
    sb.component("620-secureboot-authority.conf", f"Type=secureboot-authority\nPath={shim}\n")
    return sb.pcrlock_d / "620-secureboot-authority.pcrlock.d/secureboot-authority.pcrlock"


def sbat_digest(level):
    return efi.sha256(efi.var_event(efi.SHIM_LOCK, "SbatLevel", level.encode()))


def test_authority_without_path_copies_log(sb):
    setup_vars(sb)
    sb.cel(boot_log(sb, sbat_level=CURRENT))
    sb.component("620-secureboot-authority.conf", "Type=secureboot-authority\n")
    sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "620-secureboot-authority.pcrlock.d/secureboot-authority.pcrlock")
    assert digest_of(doc)[-1] == sbat_digest(CURRENT)


def test_authority_predicts_newer_sbatlevel(sb):
    target = authority(sb, CURRENT, NEWER)
    r = sb.ok("generate")
    assert "will set SbatLevel to sbat,1,2025051000 (now sbat,1,2024010900)" in r.stdout
    doc = sb.read_json(target)
    assert digest_of(doc) == [efi.sha256(efi.var_event(efi.IMAGE_SECURITY_DB, "db", b"cert")),
                              sbat_digest(NEWER)]
    # The replaced record carries only digests, no stale content.
    assert set(doc["records"][-1]) == {"pcr", "digests"}


def test_authority_keeps_newer_current_level(sb):
    target = authority(sb, NEWER, OLDER)
    r = sb.ok("generate")
    assert "will set SbatLevel" not in r.stdout
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(NEWER)


def test_authority_equal_datestamp_keeps_current(sb):
    same_date = "sbat,1,2024010900\nshim,9\n"
    target = authority(sb, CURRENT, same_date)
    sb.ok("generate")
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(CURRENT)


def test_authority_higher_version_field_keeps_current(sb):
    current = "sbat,2,2020010100\nshim,1\n"
    target = authority(sb, current, NEWER)
    sb.ok("generate")
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(current)


def test_authority_sbatpolicy_latest(sb):
    target = authority(sb, CURRENT, OLDER, shim_latest=NEWER, policy=1)
    sb.ok("generate")
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(NEWER)


def test_authority_sbatpolicy_reset_is_ignored(sb):
    target = authority(sb, CURRENT, OLDER, shim_latest=NEWER, policy=3)
    sb.ok("generate")
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(CURRENT)


def test_authority_needs_exactly_one_sbatlevel_record(sb):
    authority(sb, CURRENT, NEWER)
    log = json.loads((sb.root / "cel.json").read_text())
    event = efi.var_event(efi.SHIM_LOCK, "SbatLevel", CURRENT.encode()).hex()
    sb.cel([r for r in log if r["content"]["event_data"] != event])
    r = sb.run("generate")
    assert r.returncode == 1
    assert "found 0 PCR 7 records for the current SbatLevel, expected 1" in r.stderr


def test_authority_needs_sbatlevelrt(sb):
    authority(sb, CURRENT, NEWER)
    (sb.efivars / f"SbatLevelRT-{efi.SHIM_LOCK}").unlink()
    r = sb.run("generate")
    assert r.returncode == 1
    assert "SbatLevelRT not found; was this boot started by shim?" in r.stderr


def test_authority_rejects_non_pe(sb):
    authority(sb, CURRENT, NEWER)
    (sb.root / "shimx64.efi").write_bytes(b"not a PE file at all" * 10)
    r = sb.run("generate")
    assert r.returncode == 1
    assert "not a PE binary" in r.stderr


def test_authority_trailing_newline_preserved(sb):
    # SbatLevel ends with a newline; losing it would change the digest and
    # no record would match.
    assert CURRENT.endswith("\n")
    target = authority(sb, CURRENT, OLDER)
    sb.ok("generate")
    assert digest_of(sb.read_json(target))[-1] == sbat_digest(CURRENT)
