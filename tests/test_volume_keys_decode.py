"""pcrlock volume-keys and the pcrlock decode pass-through."""

import json

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
