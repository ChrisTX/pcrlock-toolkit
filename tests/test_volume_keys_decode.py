"""pcrlock volume-keys and pcrlock decode (pass-through and --efivar)."""

import glob
import json

import efi
import pytest

ROOT_UUID = "e67f24c8-0e7b-4cf4-9a0d-ee6715f001b6"


def vk(name, uuid, digest, pcr=15):
    return {"pcr": pcr, "digests": [{"hashAlg": "sha1", "digest": "00"},
                                     {"hashAlg": "sha256", "digest": digest}],
            "content_type": "systemd",
            "content": {"string": f"cryptsetup:{name}:{uuid}", "eventType": "volume-key"}}


def setup(sb):
    sb.cel([
        vk("root", ROOT_UUID, "a" * 64),
        {"nv_index": 0x1D10201, "digests": [], "content_type": "systemd",
         "content": {"string": f"cryptsetup-keyslot:root:{ROOT_UUID}:tpm2:1", "eventType": "keyslot"}},
        vk("secondary", "1111", "b" * 64),
        vk("extra", "3333", "c" * 64),
        vk("swap", "5555", "d" * 64),
    ])
    (sb.root / "etc/crypttab").write_text(
        "# comment\n"
        f"root UUID={ROOT_UUID} - tpm2-device=auto,fixate-volume-key={'A' * 64},x-initrd.attach\n"
        "secondary UUID=1111 /etc/key tpm2-measure-pcr=yes\n"
        "\n"
        f"extra UUID=3333 - fixate-volume-key={'e' * 64}\n"
        "nooptions UUID=9999\n")


def rows(stdout):
    return [ln.split() for ln in stdout.splitlines()]


def test_volume_keys(sb):
    setup(sb)
    r = sb.run("volume-keys")
    assert r.returncode == 1
    out = rows(r.stdout)
    assert out[0] == ["VOLUME", "UNLOCKED", "CRYPTTAB", "DIGEST"]
    assert out[1:5] == [
        ["root", "tpm2:1", "fixed", "a" * 64],
        ["secondary", "-", "not", "fixed", "b" * 64],
        ["extra", "-", "MISMATCH", "c" * 64],
        ["swap", "-", "no", "entry", "d" * 64],
    ]
    assert f"secondary: fixate-volume-key={'b' * 64}" in r.stdout
    assert "doesn't match the measured volume key" in r.stderr


def test_volume_keys_all_fixed(sb):
    sb.cel([vk("root", ROOT_UUID, "a" * 64)])
    (sb.root / "etc/crypttab").write_text(f"root UUID=x - fixate-volume-key={'a' * 64}\n")
    r = sb.ok("volume-keys")
    assert "To pin them" not in r.stdout


def test_volume_keys_none_measured(sb):
    sb.cel([])
    assert "no volume keys measured this boot" in sb.ok("volume-keys").stdout


def test_volume_keys_without_crypttab(sb):
    sb.cel([vk("root", ROOT_UUID, "a" * 64)])
    assert rows(sb.ok("volume-keys").stdout)[1] == ["root", "-", "no", "entry", "a" * 64]


def test_decode_is_passed_through_before_option_parsing(sb):
    log = sb.root / "log.json"
    log.write_text(json.dumps([{"pcr": 4, "digests": [], "content_type": "pcclient_std",
                                "content": {"event_type": "EV_SEPARATOR", "event_data": "00000000"}}]))
    r = sb.ok("decode", "--text", str(log))
    assert "EV_SEPARATOR" in r.stdout and "success" in r.stdout
    doc = json.loads(sb.ok("decode", str(log)).stdout)
    assert doc[0]["decoded"] == {"value": "success"}


# --- decode --efivar ------------------------------------------------------------------

def test_decode_efivar_files(sb):
    kek = sb.efivar("KEKDefault", efi.EFI_GLOBAL,
                    efi.esl_sha256("77fa9abd-0359-4d32-bd60-28f4e78f784b", b"\x11" * 32), attrs=0x6)
    sbat = sb.efivar("SbatLevelRT", efi.SHIM_LOCK, b"sbat,1,2024040900\nshim,4\n", attrs=0x6)
    doc = json.loads(sb.ok("decode", "--efivar", str(kek), "--efivar", str(sbat)).stdout)
    assert doc[0]["name"] == "KEKDefault"
    assert doc[0]["guid"].startswith(efi.EFI_GLOBAL)
    assert doc[0]["attributes"] == ["BS", "RT"]
    entry = doc[0]["lists"][0]["entries"][0]
    assert entry == {"owner": "77fa9abd-0359-4d32-bd60-28f4e78f784b (Microsoft)", "hash": "11" * 32}
    assert doc[1]["text"] == "sbat,1,2024040900\nshim,4\n"
    text = sb.ok("decode", "--text", "--efivar", str(kek)).stdout
    assert text.startswith(f"KEKDefault-{efi.EFI_GLOBAL}  [BS,RT]  (1 entry)")
    assert "11" * 32 in text


def test_decode_efivar_errors(sb):
    r = sb.run("decode", "--efivar", str(sb.root / "missing"))
    assert r.returncode == 1 and "No such file" in r.stderr
    r = sb.run("decode", "--efivar", "x/y", "--pcr=7")
    assert r.returncode == 1 and "--efivar takes no FILE or --pcr" in r.stderr
    short = sb.root / "short"
    short.write_bytes(b"\x07")
    r = sb.run("decode", "--efivar", str(short))
    assert r.returncode == 1 and "shorter than the attribute header" in r.stderr


def test_decode_efivar_by_name_on_this_machine(sb):
    # Name lookup reads the real efivarfs; SecureBoot exists on any UEFI system.
    if not glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        pytest.skip("no efivarfs")
    doc = json.loads(sb.ok("decode", "--efivar", "SecureBoot").stdout)
    assert doc[0]["name"] == "SecureBoot" and doc[0]["value"] in (0, 1)


def test_decode_missing_log_file(sb):
    r = sb.run("decode", str(sb.root / "missing.json"))
    assert r.returncode == 1 and "No such file" in r.stderr and "Traceback" not in r.stderr
