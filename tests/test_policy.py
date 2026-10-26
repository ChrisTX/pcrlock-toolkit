"""The variant budget (pcrlock check, refusal to update an oversized policy)
and the --location= check."""

import json


def variants(sb, component, *digest_sets, pcr=4):
    """A <component>.pcrlock.d with one variant per digest set."""
    d = sb.pcrlock_d / f"{component}.pcrlock.d"
    d.mkdir(parents=True, exist_ok=True)
    for i, digests in enumerate(digest_sets):
        recs = [{"pcr": pcr, "digests": [{"hashAlg": "sha256", "digest": x}]} for x in digests]
        (d / f"v{i}.pcrlock").write_text(json.dumps({"records": recs}))


def check_table(stdout):
    rows = [ln.split(None, 2) for ln in stdout.splitlines()[1:] if ln.strip()]
    return {int(r[0]): (int(r[1]), r[2]) for r in rows}


def test_product_before_window(sb):
    variants(sb, "625-shim", ["a"], ["b"])
    variants(sb, "630-systemd-boot", ["c"], ["d"])
    variants(sb, "650-uki", ["e"], ["f"])
    r = sb.ok("check")
    assert r.stdout.splitlines()[0].split() == ["PCR", "VALUES", "STATE"]
    assert check_table(r.stdout) == {4: (8, "at the limit, no room for another variant")}


def test_over_the_limit(sb):
    variants(sb, "625-shim", ["a"], ["b"])
    variants(sb, "630-systemd-boot", ["c"], ["d"])
    variants(sb, "650-uki", ["e"], ["f"], ["g"])
    r = sb.run("check")
    assert r.returncode == 1
    assert check_table(r.stdout) == {4: (12, "over the limit (max 8)")}


def test_identical_variants_count_once(sb):
    variants(sb, "625-shim", ["a"], ["a"])
    assert check_table(sb.ok("check").stdout) == {4: (1, "ok")}


def test_window_stop_points(sb):
    # PCR 15 with --location=791:940-: 785 and 790 before the window are
    # mandatory, then the boot may stop after each window component that
    # touches the PCR: 1 + 2 + 2 + 4 = 9. 950 is after the window end.
    variants(sb, "785-mor-lock", ["m"], pcr=15)
    variants(sb, "790-platsec", ["p"], pcr=15)
    variants(sb, "795-cryptsetup-root", ["r1"], ["r2"], pcr=15)
    variants(sb, "799-initrd-seal", ["s"], pcr=15)
    variants(sb, "841-cryptsetup-secondary", ["x1"], ["x2"], pcr=15)
    variants(sb, "950-shutdown", ["z1"], ["z2"], ["z3"], pcr=15)
    r = sb.run("check")
    assert check_table(r.stdout) == {15: (9, "over the limit (max 8)")}


def test_components_not_touching_pcr_add_no_stop(sb):
    variants(sb, "795-cryptsetup-root", ["r1"], ["r2"], pcr=15)
    variants(sb, "800-other", ["o1"], ["o2"], ["o3"], pcr=11)
    assert check_table(sb.ok("check").stdout)[15] == (3, "ok")


def test_pcr_mask_from_execstart(sb):
    sb.execstart("/usr/lib/systemd/systemd-pcrlock make-policy --location=791:940- --pcr=4")
    variants(sb, "625-shim", ["a"], ["b"])
    variants(sb, "795-x", ["c"], pcr=15)
    assert set(check_table(sb.ok("check").stdout)) == {4}


def test_varlib_components_are_counted(sb):
    variants(sb, "625-shim", ["a"], ["b"])
    sb.varlib.mkdir(parents=True)
    (sb.varlib / "626-other.pcrlock").write_text(json.dumps(
        {"records": [{"pcr": 4, "digests": [{"hashAlg": "sha256", "digest": "q"}]}]}))
    # Same-named /var/lib components are overridden by /etc ones.
    (sb.varlib / "625-shim.pcrlock").write_text(json.dumps(
        {"records": [{"pcr": 4, "digests": [{"hashAlg": "sha256", "digest": "zz"}]}]}))
    assert check_table(sb.ok("check").stdout) == {4: (2, "ok")}


# --- location ---------------------------------------------------------------------

def test_location_mismatch(sb):
    sb.execstart("/usr/lib/systemd/systemd-pcrlock make-policy --strict=yes")
    r = sb.run("check")
    assert r.returncode == 1
    assert ("systemd-pcrlock-make-policy.service uses --location=760-:940-, "
            "but Location= is 791:940-") in r.stderr
    assert ("ExecStart=/usr/lib/systemd/systemd-pcrlock make-policy --location=791:940- --strict=yes"
            in r.stderr)


def test_location_from_config(sb):
    sb.conf("Location=760-:940-\n")
    sb.execstart("/usr/lib/systemd/systemd-pcrlock make-policy")
    sb.ok("check")


def test_generate_refuses_policy_update_on_location_mismatch(sb):
    sb.execstart("/usr/lib/systemd/systemd-pcrlock make-policy --location=760-:940-")
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "not updating the policy; the current one stays in place" in r.stderr
    assert (sb.pcrlock_d / "799-seal.pcrlock").exists()
    assert sb.calls("systemctl restart") == []


def test_generate_refuses_policy_update_over_budget(sb):
    variants(sb, "625-shim", ["a"], ["b"], ["c"])
    variants(sb, "630-systemd-boot", ["d"], ["e"], ["f"])
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.run("generate")
    assert r.returncode == 1
    assert "PCR 4 can take 9 values, more than the 8 a policy can hold" in r.stderr
    assert sb.calls("systemctl restart") == []


def test_policy_none_only_warns(sb):
    variants(sb, "625-shim", ["a"], ["b"], ["c"])
    variants(sb, "630-systemd-boot", ["d"], ["e"], ["f"])
    sb.component("799-seal.conf", "Type=raw\nPCR=15\nText=x\n")
    r = sb.ok("--policy=none", "generate")
    assert "PCR 4 can take 9 values" in r.stderr
