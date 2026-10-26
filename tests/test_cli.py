"""Command line, pcrlock.conf and component file parsing."""

import pytest


def test_no_command_prints_usage(sb):
    r = sb.run()
    assert r.returncode == 2
    assert "Usage:" in r.stderr


def test_help_and_version(sb):
    assert "Commands:" in sb.ok("--help").stdout
    assert sb.ok("--version").stdout.startswith("pcrlock ")


@pytest.mark.parametrize("args, message", [
    (["--bogus", "list"], "unknown option: --bogus"),
    (["frobnicate"], "unknown command: frobnicate"),
    (["--policy=sometimes", "generate"], "--policy must be now, mark or none"),
    (["--if-capsule-pending", "generate"], "--if-capsule-pending only applies to suspend-firmware"),
    (["list", "extra"], "list takes no arguments"),
    (["prune", "extra"], "prune takes no arguments"),
    (["check", "extra"], "check takes no arguments"),
    (["relock-firmware", "extra"], "relock-firmware takes no arguments"),
    (["volume-keys", "extra"], "volume-keys takes no arguments"),
])
def test_cli_errors(sb, args, message):
    r = sb.run(*args)
    assert r.returncode == 1
    assert message in r.stderr


def test_double_dash_ends_options(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.run("generate", "--", "--not-an-option")
    assert r.returncode == 1
    assert "no such component: --not-an-option" in r.stderr


# --- pcrlock.conf ----------------------------------------------------------

@pytest.mark.parametrize("text, message", [
    ("Bogus=1\n", "pcrlock.conf:1: unknown key 'Bogus'"),
    ("# comment\n\nnot a pair\n", "pcrlock.conf:3: expected Key=Value"),
    ("Policy=later\n", "Policy= must be now, mark or none"),
    ("Directory=relative/dir\n", "Directory= must be an absolute path"),
    ("Location=791 940\n", "Location= must be a --location= value"),
])
def test_global_config_errors(sb, text, message):
    sb.conf(text)
    r = sb.run("list")
    assert r.returncode == 1
    assert message in r.stderr


def test_global_config_values(sb):
    other = sb.root / "elsewhere"
    sb.conf(f'  # indented comment\nDirectory = "{other}"\nPolicy=\'none\'\n')
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.ok("generate")
    assert (other / "799-seal.pcrlock").is_file()
    assert "run 'systemctl restart systemd-pcrlock-make-policy.service'" in r.stdout
    assert sb.calls("systemctl restart") == []


def test_hash_is_a_comment_only_at_line_start(sb):
    # Filter= values are jq programs and may contain '#'.
    sb.cel([{"pcr": 1, "digests": [{"hashAlg": "sha256", "digest": "aa"}],
             "content": {"event_type": "EV_X", "event_data": "#"}}])
    sb.component("550-x.conf",
                 'Type=log\nPCR=1\nFilter=.content.event_data == "#"\n')
    sb.ok("generate")
    assert len(sb.read_json(sb.pcrlock_d / "550-x.pcrlock")["records"]) == 1


# --- component files ---------------------------------------------------------

@pytest.mark.parametrize("filename, text, message", [
    ("seal.conf", "Type=raw\n", "file name must be <priority>-<name>.conf"),
    ("799-seal.conf", "PCR=15\n", "Type= missing"),
    ("799-seal.conf", "Type=magic\n", "unknown Type 'magic'"),
    ("799-seal.conf", "Type=raw\nColour=blue\n", "799-seal.conf:2: unknown key 'Colour'"),
    ("799-seal.conf", "Type=raw\nPCR=fifteen\n", "PCR= must be a number"),
    ("799-seal.conf", "Type=raw\nMinRecords=-1\n", "MinRecords= must be a number"),
    ("799-seal.conf", "Type=raw\nEnabled=perhaps\n", "invalid boolean 'perhaps'"),
    ("799-seal.conf", "Type=raw\nKeepPrevious=2\n", "invalid boolean '2'"),
])
def test_component_errors(sb, filename, text, message):
    sb.component(filename, text)
    r = sb.run("generate")
    assert r.returncode == 1
    assert message in r.stderr


def test_duplicate_component_name(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=a\n")
    sb.component("800-seal.conf", "Type=raw\nPCR=15\nText=b\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "duplicate component name 'seal'" in r.stderr


def test_unknown_component_name(sb):
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=a\n")
    r = sb.run("generate", "nope")
    assert r.returncode == 1
    assert "no such component: nope" in r.stderr


def test_must_be_root():
    # The harness replaces the root check, so check that it's still there.
    from conftest import REPO
    assert "((EUID == 0)) || die" in (REPO / "bin/pcrlock").read_text()
