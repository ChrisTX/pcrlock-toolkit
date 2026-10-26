"""pcrlock generate: component types, -previous variants, failure handling
and how the policy is updated."""

import hashlib
import json

import pytest
from conftest import digest_of


def sha(b):
    return hashlib.sha256(b).hexdigest()


def pcr4(data):
    return {"pcr": 4, "digests": [{"hashAlg": "sha256", "digest": sha(data)}],
            "content_type": "pcclient_std",
            "content": {"event_type": "EV_EFI_BOOT_SERVICES_APPLICATION", "event_data": ""}}


# --- raw, copy, log, volume-key, identity, platsec ----------------------------

def test_raw(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=initrd-sealed\n")
    r = sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "799-seal.pcrlock")
    assert doc == {"records": [{"pcr": 15, "digests": [
        {"hashAlg": "sha256", "digest": sha(b"initrd-sealed")}]}]}
    assert "updated 799-seal.pcrlock" in r.stdout


@pytest.mark.parametrize("text, message", [
    ("Type=raw\nText=x\n", "PCR= missing"),
    ("Type=raw\nPCR=15\n", "Text= missing"),
    ("Type=log\n", "PCR= missing"),
    ("Type=volume-key\n", "Volume= missing"),
    ("Type=copy\n", "Source= missing"),
    ("Type=copy\nSource=/nonexistent\n", "/nonexistent not readable"),
    ("Type=pe\n", "Path= missing"),
    ("Type=pe\nPath=/nonexistent\n", "/nonexistent not readable"),
])
def test_missing_settings(sb, text, message):
    sb.component("700-x.conf", text)
    r = sb.run("generate")
    assert r.returncode == 1
    assert message in r.stderr
    assert "1 component(s) failed; their existing files were kept" in r.stderr


def test_copy(sb):
    src = sb.write_json(sb.root / "src.pcrlock",
                        {"records": [{"pcr": 7, "digests": [{"hashAlg": "sha256", "digest": "ab"}]}]})
    sb.component("500-sep.conf", f"Type=copy\nSource={src}\n")
    sb.ok("generate")
    assert sb.read_json(sb.pcrlock_d / "500-sep.pcrlock") == sb.read_json(src)


def test_copy_rejects_non_pcrlock(sb):
    src = sb.write_json(sb.root / "src.json", {"something": 1})
    sb.component("500-sep.conf", f"Type=copy\nSource={src}\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "is not a pcrlock file" in r.stderr


LOG = [
    {"pcr": 5, "digests": [{"hashAlg": "sha256", "digest": "01"}],
     "content_type": "pcclient_std", "content": {"event_type": "EV_EFI_GPT_EVENT", "event_data": "aa"}},
    {"pcr": 5, "digests": [{"hashAlg": "sha256", "digest": "02"}],
     "content_type": "pcclient_std", "content": {"event_type": "EV_EFI_ACTION", "event_data": "bb"}},
    {"pcr": 14, "digests": [{"hashAlg": "sha256", "digest": "03"}],
     "content_type": "pcclient_std", "content": {"event_type": "EV_IPL", "event_data": "cc"}},
    {"pcr": 14, "digests": [{"hashAlg": "sha256", "digest": "04"}],
     "content_type": "systemd", "content": {"string": "os-separator", "eventType": "os-separator"}},
]


def test_log_keeps_log_order_and_content(sb):
    sb.cel(LOG)
    sb.component("600-gpt.conf", "Type=log\nPCR=5\n")
    sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "600-gpt.pcrlock")
    assert digest_of(doc) == ["01", "02"]
    assert doc["records"][0]["content"] == LOG[0]["content"]
    assert set(doc["records"][0]) == {"pcr", "digests", "content"}


def test_log_event_types_and_filter(sb):
    sb.cel(LOG)
    sb.component("600-gpt.conf", "Type=log\nPCR=5\nEventTypes=EV_EFI_ACTION EV_NONE\n")
    # PCR 14 MOK: the runtime os-separator must be filtered out.
    sb.component("640-mok.conf", 'Type=log\nPCR=14\nFilter=.content_type != "systemd"\n')
    sb.ok("generate")
    assert digest_of(sb.read_json(sb.pcrlock_d / "600-gpt.pcrlock")) == ["02"]
    assert digest_of(sb.read_json(sb.pcrlock_d / "640-mok.pcrlock")) == ["03"]


def test_log_bad_filter(sb):
    sb.cel(LOG)
    sb.component("640-mok.conf", "Type=log\nPCR=14\nFilter=this is not jq(\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "filter failed" in r.stderr


def test_volume_key(sb):
    sb.cel([
        {"pcr": 15, "digests": [{"hashAlg": "sha256", "digest": "aa"}], "content_type": "systemd",
         "content": {"string": "cryptsetup:root:1234", "eventType": "volume-key"}},
        {"pcr": 15, "digests": [{"hashAlg": "sha256", "digest": "bb"}], "content_type": "systemd",
         "content": {"string": "cryptsetup:secondary:5678", "eventType": "volume-key"}},
    ])
    sb.component("795-cryptsetup-root.conf", "Type=volume-key\nVolume=root\n")
    sb.component("841-cryptsetup-secondary.conf", "Type=volume-key\nVolume=secondary\n")
    sb.ok("generate")
    assert digest_of(sb.read_json(sb.pcrlock_d / "795-cryptsetup-root.pcrlock")) == ["aa"]
    assert digest_of(sb.read_json(sb.pcrlock_d / "841-cryptsetup-secondary.pcrlock")) == ["bb"]


def test_volume_key_not_unlocked_keeps_old_file_and_continues(sb):
    old = sb.write_json(sb.pcrlock_d / "842-cryptsetup-extra.pcrlock", {"records": [{"pcr": 15}]})
    before = old.read_text()
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    sb.component("842-cryptsetup-extra.conf", "Type=volume-key\nVolume=extra\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "expected 1 volume-key record for 'extra', found 0 (was it unlocked via TPM this boot?)" in r.stderr
    assert old.read_text() == before
    # The other component still ran, and the policy was still updated.
    assert (sb.pcrlock_d / "799-seal.pcrlock").exists()
    assert sb.calls("systemctl restart")


def test_identity_merges_in_order_and_warns_about_systemd_files(sb):
    sb.varlib.mkdir(parents=True)
    (sb.varlib / "820-machine-id.pcrlock").write_text("{}")
    sb.component("820-system-identity.conf", "Type=identity\nMountPoints=/ /var\n")
    r = sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "820-system-identity.pcrlock")
    assert digest_of(doc) == [sha(b"machine-id:stub"), sha(b"file-system:/"), sha(b"file-system:/var")]
    assert "820-machine-id.pcrlock duplicates component 'system-identity'" in r.stderr


def test_platsec(sb):
    (sb.root / "platsec").mkdir()
    (sb.root / "platsec/status").write_text("tsme=on;iommu=on;preboot_dma=on")
    sb.component("790-platsec.conf", "Type=platsec\n")
    sb.ok("generate")
    doc = sb.read_json(sb.pcrlock_d / "790-platsec.pcrlock")
    assert doc["records"][0]["pcr"] == 15
    assert digest_of(doc) == [sha(b"tsme=on;iommu=on;preboot_dma=on")]


def test_platsec_pcr_override(sb):
    (sb.root / "platsec").mkdir()
    (sb.root / "platsec/status").write_text("tsme=on")
    sb.component("790-platsec.conf", "Type=platsec\nPCR=12\n")
    sb.ok("generate")
    assert sb.read_json(sb.pcrlock_d / "790-platsec.pcrlock")["records"][0]["pcr"] == 12


def test_platsec_refuses_failing_baseline(sb):
    (sb.root / "platsec").mkdir()
    (sb.root / "platsec/status").write_text("tsme=off;iommu=on;preboot_dma=on")
    (sb.root / "platsec/rc").write_text("1")
    sb.component("790-platsec.conf", "Type=platsec\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "platform baseline not met (tsme=off;iommu=on;preboot_dma=on)" in r.stderr
    assert not (sb.pcrlock_d / "790-platsec.pcrlock").exists()


# --- MinRecords, Enabled, Output, filters ----------------------------------------

def test_min_records(sb):
    sb.cel(LOG)
    sb.component("650-empty.conf", "Type=log\nPCR=9\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "produced 0 record(s), expected at least 1" in r.stderr
    sb.component("650-empty.conf", "Type=log\nPCR=9\nMinRecords=0\n")
    sb.ok("generate")
    assert sb.read_json(sb.pcrlock_d / "650-empty.pcrlock") == {"records": []}


def test_disabled_type_filter_and_names(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    sb.component("798-off.conf", "Type=raw\nPCR=15\nText=y\nEnabled=no\n")
    src = sb.write_json(sb.root / "s.pcrlock", {"records": []})
    sb.component("500-copy.conf", f"Type=copy\nSource={src}\nMinRecords=0\n")
    sb.ok("generate", "--type=copy,pe")
    assert sorted(p.name for p in sb.pcrlock_d.iterdir()) == ["500-copy.pcrlock"]
    sb.ok("generate", "seal", "off")
    assert sorted(p.name for p in sb.pcrlock_d.iterdir()) == ["500-copy.pcrlock", "799-seal.pcrlock"]


def test_output(sb):
    absolute = sb.root / "abs.pcrlock"
    sb.component("799-a.conf", "Type=raw\nPCR=15\nText=a\nOutput=sub/a.pcrlock\n")
    sb.component("799-b.conf", f"Type=raw\nPCR=15\nText=b\nOutput={absolute}\n")
    sb.ok("generate")
    assert (sb.pcrlock_d / "sub/a.pcrlock").is_file()
    assert absolute.is_file()


def test_keep_previous_needs_pcrlock_d(sb):
    sb.component("799-a.conf", "Type=raw\nPCR=15\nText=a\nKeepPrevious=yes\nOutput=a.pcrlock\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "KeepPrevious=yes needs the output inside a <name>.pcrlock.d directory" in r.stderr


# --- -previous variants -------------------------------------------------------------

def test_pe_keeps_booted_variant_as_previous(sb):
    old, new = b"shim 16.0", b"shim 16.1"
    binary = sb.root / "shimx64.efi"
    binary.write_bytes(old)
    sb.component("625-shim.conf", f"Type=pe\nPath={binary}\n")
    sb.cel([pcr4(old)])
    sb.ok("generate")
    target = sb.pcrlock_d / "625-shim.pcrlock.d/shim.pcrlock"
    assert digest_of(sb.read_json(target)) == [sha(old)]

    binary.write_bytes(new)
    r = sb.ok("generate")
    assert "kept 625-shim.pcrlock.d/shim.pcrlock as shim-previous.pcrlock (matches the current boot)" in r.stdout
    assert digest_of(sb.read_json(target)) == [sha(new)]
    assert digest_of(sb.read_json(target.with_name("shim-previous.pcrlock"))) == [sha(old)]


def test_pe_drops_variant_that_is_not_booted(sb):
    binary = sb.root / "shimx64.efi"
    binary.write_bytes(b"a")
    sb.component("625-shim.conf", f"Type=pe\nPath={binary}\n")
    sb.cel([pcr4(b"something else")])
    sb.ok("generate")
    binary.write_bytes(b"b")
    r = sb.ok("generate")
    assert "kept" not in r.stdout
    assert not (sb.pcrlock_d / "625-shim.pcrlock.d/shim-previous.pcrlock").exists()


def test_is_booted_uses_only_pcr4_when_present(sb):
    # A variant with PCR 4 records counts as booted if its PCR 4 digests are
    # in the log, even if other records aren't.
    binary = sb.root / "x.efi"
    binary.write_bytes(b"a")
    sb.component("630-x.conf", f"Type=pe\nPath={binary}\n")
    sb.cel([pcr4(b"a")])
    sb.ok("generate")
    target = sb.pcrlock_d / "630-x.pcrlock.d/x.pcrlock"
    doc = sb.read_json(target)
    doc["records"].append({"pcr": 7, "digests": [{"hashAlg": "sha256", "digest": "ff"}]})
    target.write_text(json.dumps(doc))
    binary.write_bytes(b"b")
    sb.ok("generate")
    assert (target.with_name("x-previous.pcrlock")).exists()


def test_unchanged_output_leaves_policy_alone(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    sb.ok("generate")
    sb.reset_calls()
    r = sb.ok("generate")
    assert "updated" not in r.stdout
    assert sb.calls("systemctl restart") == []
    assert sb.calls("systemctl set-property") == []


# --- policy modes, dry run --------------------------------------------------------------

def test_policy_mark(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.ok("--policy=mark", "generate")
    assert sb.calls("systemctl set-property") == [
        "systemctl set-property --runtime systemd-pcrlock-make-policy.service Markers=needs-start"]
    assert sb.calls("systemctl restart") == []
    assert "marked systemd-pcrlock-make-policy.service for restart" in r.stdout


def test_policy_from_config_and_cli_precedence(sb):
    sb.conf("Policy=mark\n")
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    sb.ok("--policy=now", "generate")
    assert sb.calls("systemctl restart")
    assert sb.calls("systemctl set-property") == []


def test_dry_run_writes_nothing(sb):
    binary = sb.root / "shimx64.efi"
    binary.write_bytes(b"old")
    sb.component("625-shim.conf", f"Type=pe\nPath={binary}\n")
    sb.cel([pcr4(b"old")])
    sb.ok("generate")
    binary.write_bytes(b"new")
    sb.reset_calls()
    r = sb.ok("--dry-run", "generate")
    assert "would keep 625-shim.pcrlock.d/shim.pcrlock as shim-previous.pcrlock" in r.stdout
    assert "would update 625-shim.pcrlock.d/shim.pcrlock" in r.stdout
    assert "would update the policy (now)" in r.stdout
    assert digest_of(sb.read_json(sb.pcrlock_d / "625-shim.pcrlock.d/shim.pcrlock")) == [sha(b"old")]
    assert sb.calls("systemctl restart") == []


def test_tmpdir_cleaned_up(sb, no_tmp_leak):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    sb.ok("generate")
