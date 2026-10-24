# pcrlock-toolkit

Tooling around `systemd-pcrlock` for TPM2-unlocked Arch systems (shim →
systemd-boot → UKI, dracut initrd). User-facing docs are in `README.md`; this
file records how the code is organised and *why* it is the way it is. Many of
these decisions were reached by debugging real boots, so don't undo them
without a reason.

## Layout and commands

- `bin/pcrlock`: bash CLI (`generate`, `list`, `prune`, `uki add|remove`).
  Reads `/etc/pcrlock-toolkit/pcrlock.conf` and `components.d/*.conf`, writes
  to `/etc/pcrlock.d`.
- `bin/platsec-pcr-check`: POSIX sh, runs in the initrd. Checks memory
  encryption (TSME/TME), IOMMU and pre-boot DMA, measures one event, powers off
  if the baseline isn't met.
- `units/`: `platsec-pcr-check.service`, `initrd-pcr15-seal.service`,
  `pcrlock-prune.service`.
- `dracut/91platsec`, `dracut/91pcr15seal`: initrd modules, opt-in via
  `add_dracutmodules`.
- `kernel-install/99-pcrlock.install`: thin wrapper around `pcrlock uki`.
- `hooks/25-pcrlock-pe.hook`: pacman hook. `examples/hooks/` are *not* installed
  as hooks.
- `examples/hardening/`: general hardening examples (cmdline, sysctl, …),
  unrelated to pcrlock and not installed. Keep them out of
  `examples/dracut.conf.d/`, whose `*.conf` users are told to copy wholesale.
- `libexec/sign-file-tpm2` + `conf/openssl-tpm2.cnf`: DKMS signing with a
  TPM2 key. Unrelated to pcrlock; may move to its own package.
  `conf/mok-signing.conf` installs to `/etc/dkms/framework.conf.d/` (a
  `backup=` file), so DKMS signs with `sign-file-tpm2` as soon as the package
  is installed. This is a deliberate exception to "everything is opt-in";
  DKMS dictates the directory, so the toolkit-owned-name rule doesn't apply.

```text
make check                      # bash -n / sh -n, then shellcheck — must pass, makepkg runs it
make install DESTDIR=/tmp/x     # staged install to inspect the layout
makepkg -si                     # PKGBUILD builds from the working tree ($startdir)
```

There is no automated test suite yet. During development `bin/pcrlock` was run
against a stub `systemd-pcrlock` (emitting `{"records":[...]}` from file
hashes), a fake `cel` JSON event log, and a stub `systemctl` that logs calls,
by sed-replacing `readonly PCRLOCK=` and `/usr/lib/modules/`. Do the same (or
add a real `tests/`) before changing generation or prune logic.

## Conventions

- shellcheck-clean. Disable a check only with an inline comment saying why
  (jq programs in single quotes; dracut-provided variables in module-setup.sh).
- Initrd code is POSIX sh (test with `dash -n`). `bin/pcrlock` may use bash.
- Config files are `Key=Value`, **parsed, never sourced**. Unknown keys and
  malformed lines are hard errors with `file:line`.
- platsec precedence: CLI > `PLATSEC_*` env > `platsec.conf` > defaults.
- Everything is opt-in. The package doesn't enable dracut modules or units:
  enabling them without matching pcrlock components breaks unlocking, and
  platsec can power the machine off.
- User config lives under `/etc/pcrlock-toolkit/` with toolkit-owned names
  (e.g. `openssl-tpm2.cnf`, not `/etc/ssl/tpm2-openssl.conf`, which users may
  already have). Packaged files go to `/usr`; local hand-made files never do.
- `.pcrlock` files are `{"records":[{pcr, digests, content?}, ...]}`. Merging
  components means concatenating `records` in **log order**.
- When a measured string changes (unit `ExecStart=`, platsec event format),
  the matching component must change in the same commit.

## Measurement layout (PCR 15)

Reference location window: `make-policy --strict=yes --location=791:940-`, set in a drop-in
for `systemd-pcrlock-make-policy.service`, never in this tool. Components
before 791 are mandatory; components in the window may or may not have
happened yet.

| Prio | Component | Measured by | When |
|---|---|---|---|
| 790 | platsec | `platsec-pcr-check` | initrd, before `cryptsetup-pre.target` |
| 795 | cryptsetup-root (volume-key) | systemd-cryptsetup | initrd, after root unlock |
| 799 | initrd-seal (`initrd-sealed`) | `initrd-pcr15-seal.service` | end of initrd, after `cryptsetup.target` |
| 820 | system-identity | pcrmachine + pcrfs, merged | after switch-root |
| 841+ | cryptsetup-<vol> | systemd-cryptsetup | after switch-root, serialized |

- Root unlocks against the state with only 790. Later disks unlock against
  790+795+799+820(+previous disks), so their unlocks must be **serialized** and
  the component numbers must follow the unlock order.
- 790 sits before the window on purpose: memory encryption off means no
  unlock at all, and it adds no PCR 15 branches.
- The 820 merge exists to cut PCR 15 states: nothing unlocks between
  machine-id and file-system measurements. Users must disable
  `systemd-pcrlock-machine-id.service` and `systemd-pcrlock-file-system.service`,
  or the originals come back in `/var/lib/pcrlock.d` (the identity type warns).
- PCR 15 was kept over PCR 7 for platsec: PCR 7 breaks raw PCR 7 bindings
  (cryptenroll default) and is firmware-owned by convention. PCRs above 15 are
  useless for sealing: 16 and 23 are resettable from locality 0, 17–22 are
  DRTM-only.
- NvPCRs would be the right home for platsec, but `systemd-pcrlock` parses
  `nv_index` records and then skips them everywhere (`EVENT_LOG_RECORD_IS_PCR`
  checks in replay, matching and prediction), and nothing can seal to NvPCR
  values yet. Upstream RFE pending (see Open items).

## Design decisions

**Previous variants.** `KeepPrevious=yes` (the default for `Type=pe` and for
UKIs) writes `<prio>-<name>.pcrlock.d/<name>.pcrlock`. On change, the old file
is kept as `<name>-previous.pcrlock` **only if it matches the current boot**
(`is_booted`: PCR 4 digests if the variant has any, else every record must be
in the event log). `prune` deletes previous variants that don't match the
current boot, and UKI variants whose `/usr/lib/modules/<kver>` is gone, but
never the variant that is running.

**Loaders stay separate components.** shim (625) and systemd-boot (630) are
not merged. Independent variants accept every old/new combination. That
matters when the ESP copies are updated at different times (shim copied in the
transaction, systemd-boot at next boot via `systemd-boot-update.service` on
non-Arch, or by a custom hook). Merging is pointless anyway: both sit before
the location window, so they add no branches.

**`Type=pe` locks the unsigned package binaries.** PCR 4 records the
Authenticode hash, which excludes the certificate table, the security
directory entry and the checksum. So it's identical for the `*.signed` copy
that boots (verified on the reference machine). Caveat: if the unsigned file
isn't 8-byte aligned, signing pads it and the hashes differ. This makes the
pacman hook independent of signing hooks (own, sbctl's `zz-sbctl.hook`).

**Pacman hook ordering.** `25-pcrlock-pe.hook` uses `--policy=mark`, so
`30-systemd-restart-marked.hook` rebuilds the policy once per transaction. A
`zzz-` name would need `--policy=now`, since the mark would be set after the
restart hook ran, and it causes a double make-policy run. Triggers must be
package files (`usr/share/shim/*.efi`, `usr/lib/systemd/boot/efi/systemd-boot*.efi`),
never `*.signed`, which no package ships.

**kernel-install uses `--policy=now`.** kernel-install also runs outside
pacman, and under pacman it's driven by a hook that sorts after the `30-`
restart hook. A transaction updating both a loader and the kernel therefore
still runs make-policy twice; that's accepted.

**`uki add` merges `lock-uki` + `lock-pe` of the kernel image.** systemd-stub
loads the embedded `.linux` via LoadImage(), which the firmware measures into
PCR 4 as a separate event. This replaced a separate inner-kernel component.

**`pcrlock-prune.service`** runs early (before sysinit and before
`systemd-pcrlock-make-policy.service`) with `--policy=none`. `now` would
deadlock: restarting a unit ordered after itself. It sets `TMPDIR` to its
`RuntimeDirectory` because `/tmp` may not be mounted yet.

**Failure handling in `generate`.** Each component runs in a subshell. A
failing one (e.g. a volume not TPM-unlocked this boot) keeps its existing file,
and the others still run. `MinRecords=` (default 1) prevents writing empty
components. The policy is only touched if something changed. `Type=platsec`
refuses to lock a state where `--status` isn't met.

**Fail closed in the initrd.**

- platsec: `FailureAction=poweroff-force`. Poweroff, not reboot, because the
  cause is a firmware setting and a reboot would loop. It always measures
  first, then fails, so a PIN or recovery key is never typed into a machine
  with unencrypted memory. `ENFORCE=0` only stops the baseline from failing;
  a broken check still triggers `FailureAction=` unless overridden.
- seal: `FailureAction=reboot-force` (PID 1 runs it directly; nothing can
  cancel it). Not `emergency.target`: an emergency shell sits in front of the
  decrypted root, and continuing from it would skip the seal while the policy
  still accepts the pre-seal state.

**Seal via Varlink.** `initrd-pcr15-seal.service` calls
`io.systemd.PCRExtend.Extend` with `varlinkctl`, the same path
systemd-cryptsetup uses since v262. That makes it a canary for the
measurement path cryptsetup relies on. This needs `systemd-pcrextend.socket`
in the initrd (dracut's `systemd-pcrextend` module; dracut ≤112 lacked the
socket, fixed for 113).

**dracut modules.** Vendor units come from `$systemdsystemunitdir`. Host
overrides and drop-ins from `$systemdsystemconfdir` are copied with `-H -o`
inside `if [[ $hostonly ]]`, the same pattern as dracut's `11systemd-pcrextend`.
`platsec.conf` is copied into **every** image without `-H`, because it's the
policy the component was generated from, and `rd.hostonly=0` must not strip
it. `check()` returns 255 (only when requested). Don't use `readonly` at
module scope, because dracut may source module-setup.sh twice.

**platsec checks.**

- **Memory encryption.** AMD: `ccp` `tsme_status`, waiting up to 5s for the
  driver to bind. Intel: MSR `IA32_TME_ACTIVATE` (0x982). It must be enabled
  and locked, with bypass off; bypass on means KeyID 0 is unencrypted.
- **IOMMU.** An IOMMU must be present, and every IOMMU group must be `DMA`,
  `DMA-FQ` or `blocked`. `iommu=panic`/`force` on the cmdline do **not** make
  boot fail without an IOMMU: `panic` is `panic_on_overflow`, `force` is for
  GART.
- **Pre-boot DMA.** The ACPI declaration: IVRS IVinfo bit 1 (as in fwupd's
  `acpi-ivrs` plugin), or DMAR flags bit 2 `DMA_CTRL_PLATFORM_OPT_IN`. This
  catches config resets, not an attacker who already runs code pre-boot.
- **Event format.** `key=state` joined by `;` in fixed order, e.g.
  `tsme=on;iommu=on;preboot_dma=on`. Something is always measured.
- **Running on the host.** Refuses to extend outside the initrd without
  `--force`.

**Separator error variant.** pcrlock ignores `/dev/null` symlinks for variant
files, so the `0xFFFFFFFF` separator variant is overridden with a same-named
copy of the success variant (`Type=copy`, `500-separator-no-error`).

## pcrlock / TPM facts worth remembering

- **Component order vs log order.** Components are predicted in filename
  order, and extends are order-dependent. If firmware logs an event out of
  the expected order, the component must be split around the separator (e.g.
  an early `EV_EFI_VARIABLE_AUTHORITY` for a driver verified before the PCR 7
  separator → separate `390-…early` and `620-…late` components).
- **PCR 14 MOK.** Extraction from `cel` must filter `content_type != "systemd"`,
  or it captures the runtime os-separator record and double-extends it.
- **PCR 5.** May log the GPT event twice with loader.conf in between. One
  combined `log` component in log order (`600-gpt-and-loader`) handles it.
- **PCR 9 and 12 can't be locked with a pcrlock ESP credential.** systemd-stub
  prepends the global credentials cpio (containing `pcrlock.<machine-id>.cred`)
  to the initrd. The kernel measures the combined buffer, so the measurement
  depends on the policy itself.
- **fwupd false positive.** "TPM PCR0 reconstruction: Invalid" appears on any
  system running `systemd-pcrosseparator`: fwupd only replays
  `binary_bios_measurements` and ignores the runtime log.
- **Volume-key records.** cryptsetup's PCR 15 volume-key record differs by
  unlock method in practice. The NvPCR keyslot record
  (`tpm2-measure-keyslot-nvpcr`) carries the keyslot index.
- **v262 changes.** Since v262, cryptsetup measures via the pcrextend Varlink
  service, and `tpm2-measure-bank=` is ignored.
- **Stale `pcrlock.json`.** Deleting `/var/lib/systemd/pcrlock.json` fixed
  "Failed to allocate policy session" (TPM 0x9a2). PolicyAuthorizeNV 0x84
  means the submitted policy differs from the one stored in the NV index.

## Not yet verified on real hardware

- **platsec in a real initrd**, the Intel TME path, and the Intel DMAR offset
  (only AMD/IVRS was exercised against fake sysfs).
- **The seal's measurement.** Whether `varlinkctl … {"text":"initrd-sealed"}`
  hashes the same bytes as `printf %s initrd-sealed | lock-raw`. Check
  `systemd-pcrlock log --pcr=15` for a ✓ on 799.
- **`lock-file-system <path> --pcrlock=`** with one call per mount point
  (used by `Type=identity`).
- **`bin/pcrlock` against the real `systemd-pcrlock`.** It has only run
  against stubs.

## Open items

- **NvPCR RFE.** Upstream systemd RFE: pcrlock support for NvPCRs in
  prediction and policy (`PolicyNV`). Questions to address: uninitialised
  NvPCRs, read auth, policy size, opt-in `--nvpcr=`.
- **bootctl and shim.** systemd-boot installed as shim's
  `\EFI\BOOT\grubx64.efi` shows in `bootctl status` but not `is-installed`,
  and `bootctl update` ignores it. Proposal: treat the file named by
  `LoaderImageIdentifier` as an installed copy and update it from
  `systemd-boot*.efi.signed`. Until then, an ESP copy hook is needed
  (`examples/hooks/22-update-systemd-boot.hook`).
- **Not covered by the toolkit yet.** PCR 1 (SMBIOS/firmware config,
  BootOrder/BBS variants), the PCR 7 secure-boot authority split, dbx update
  variants, and an optional drop of the empty `700-action-efi-exit-boot-services`
  variant.
- **Packaging.** `PKGBUILD` builds from the working tree; switch to a tagged
  source before publishing.

## Licensing

Everything is 0BSD, declared for all files in `REUSE.toml` (no per-file
headers). Keep `reuse lint` passing; new files are covered by the `**` glob.

Upstream issue/PR text: the maintainer rewrites drafts in their own words, so
provide complete checklists of points rather than polished prose.
