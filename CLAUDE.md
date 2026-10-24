# pcrlock-toolkit

Tooling around `systemd-pcrlock` for TPM2-unlocked Arch systems (shim →
systemd-boot → UKI, dracut initrd). User-facing docs are in `README.md`; this
file records how the code is organised and *why* it is the way it is. Many of
these decisions were reached by debugging real boots, so don't undo them
without a reason.

## Layout and commands

- `bin/pcrlock`: bash CLI (`generate`, `list`, `prune`, `uki add|remove`,
  `check`).
  Reads `/etc/pcrlock-toolkit/pcrlock.conf` and `components.d/*.conf`, writes
  to `/etc/pcrlock.d`.
- `bin/platsec-pcr-check`: POSIX sh, runs in the initrd. Checks memory
  encryption (TSME/TME), IOMMU and pre-boot DMA, measures one event, powers off
  if the baseline isn't met.
- `bin/mor-lock`: POSIX sh, runs in the initrd. Sets MOR ClearMemory, locks it
  without a key and measures the resulting state.
- `units/`: `platsec-pcr-check.service`, `mor-lock.service`,
  `initrd-pcr15-seal.service`, `pcrlock-prune.service`,
  `pcrlock-secureboot-policy.{path,service}`.
- `dracut/91platsec`, `dracut/91morlock`, `dracut/91pcr15seal`: initrd
  modules, opt-in via `add_dracutmodules`.
- `kernel-install/99-pcrlock.install`: thin wrapper around `pcrlock uki`.
- `hooks/25-pcrlock-pe.hook`: pacman hook. `examples/hooks/` are *not* installed
  as hooks.
- `examples/hardening/`: general hardening examples (cmdline, sysctl, …),
  unrelated to pcrlock and not installed. Keep them out of
  `examples/dracut.conf.d/`, whose `*.conf` users are told to copy wholesale.
  `examples/hardening/README.md` documents every setting; update it in the
  same commit when an example changes.
- `libexec/sign-file-tpm2` + `conf/openssl-tpm2.cnf`: DKMS signing with a
  TPM2 key. Unrelated to pcrlock; may move to its own package.
  The DKMS config is an example
  (`examples/dkms/framework.conf.d/mok-signing.conf`), not installed to
  `/etc/dkms`: DKMS reads every file there, so installing it would turn on
  signing for every DKMS build, the key paths are machine-specific, and it
  would conflict with a user's existing file of the same name.

```text
make check                   # bash -n, sh -n, shellcheck; must pass (makepkg)
make install DESTDIR=/tmp/x  # staged install to inspect the layout
makepkg -si                  # PKGBUILD builds from the working tree
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

Reference location window: `make-policy --strict=yes --location=791:940-`,
set in a drop-in for `systemd-pcrlock-make-policy.service`, never in this
tool. Components before 791 are mandatory; components in the window may or
may not have happened yet.

| Prio | Component | Measured by | When |
|---|---|---|---|
| 785 | mor-lock (`mor=on;morlock=locked`) | `mor-lock.service` | initrd, before platsec |
| 790 | platsec | `platsec-pcr-check` | initrd, before `cryptsetup-pre.target` |
| 795 | cryptsetup-root (volume-key) | systemd-cryptsetup | initrd, after root unlock |
| 799 | initrd-seal (`initrd-sealed`) | `initrd-pcr15-seal.service` | end of initrd, after `cryptsetup.target` |
| 820 | system-identity | pcrmachine + pcrfs, merged | after switch-root |
| 841+ | cryptsetup-<vol> | systemd-cryptsetup | after switch-root, serialized |

- Root unlocks against the state with only 785+790. Later disks unlock
  against 785+790+795+799+820(+previous disks), so their unlocks must be
  **serialized** and the component numbers must follow the unlock order.
- 785 and 790 sit before the window on purpose: no MOR lock or memory
  encryption off means no unlock at all, and they add no PCR 15 branches.
  `mor-lock.service` is ordered `Before=platsec-pcr-check.service`, so the two
  events always come in component order.
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
never the variant that is running. `uki remove` follows the same rule and
keeps a variant that `is_booted`: kernel-install runs `remove` for the old
kernel during an upgrade, which is usually the running one, and dropping it
would push the running system out of the policy for the rest of the boot.

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

**UKI variants are named `<kver>.pcrlock`.** The kernel-install plugin passes
`$2` (the kernel version), not the entry name: `/etc/pcrlock.d` belongs to one
machine, so the entry token adds nothing, and since v262
`KERNEL_INSTALL_ENTRY_NAME_FORMAT` can make the entry name arbitrary, which
would stop `prune` from finding the kernel version.

**`pcrlock-prune.service`** runs early (before sysinit and before
`systemd-pcrlock-make-policy.service`) with `--policy=none`. `now` would
deadlock: restarting a unit ordered after itself. It sets `TMPDIR` to its
`RuntimeDirectory` because `/tmp` may not be mounted yet.

**Variant budget.** make-policy builds one PolicyOR per PCR, so each PCR can
take at most 8 values (upstream discussion about cascading PolicyORs, not
implemented). The count is the product of per-component variant counts
(variants that differ on that PCR), summed over the stop points of the
`--location=` window: the state before the window start, then after each
window component that touches the PCR; components after the end are ignored.
`pcr_value_counts` reads the real component set from
`systemd-pcrlock list-components --json=short` (it resolves same-name
overrides across `/etc`, `/var/lib`, `/usr/lib`) and `--location=`/`--pcr=`
from the make-policy unit's `ExecStart=`, defaulting to `760-:940-`.
`apply_policy` refuses `now` and `mark` when over budget (the old NV policy
stays valid) and only warns with `none`. This is why PCR 7 support predicts
dbx and SbatLevel only: variants for arbitrary db changes multiply past 8.

**`Type=secureboot-policy`.** Wraps `lock-secureboot-policy`, which reads the
current EFI variables, so after a runtime dbx update it predicts the next
boot; `KeepPrevious=yes` keeps the booted variant. Every PCR 7
`EV_EFI_VARIABLE_DRIVER_CONFIG` record must match the log position by
position except dbx (identified by the image security database GUID and the
UTF-16 name "dbx" in the event data). So PK/KEK/db changes made by anyone are
never pre-accepted; they are locked only after a boot measured them. The
example keeps systemd's name `240-secureboot-policy`: fwupd's pcrlock plugin
matches component ids by the substring `secureboot-policy`. A same-named
`/var/lib` directory would add its `generated.pcrlock` as an extra variant,
so the type warns about it. `pcrlock-secureboot-policy.path` watches only
dbx, for the same reason.

**`Type=secureboot-authority` and SbatLevel.** Wraps
`lock-secureboot-authority` (the authorities after the separator). With
`Path=` (the shim binary), it predicts the SbatLevel the next shim boot
measures and replaces only that record, giving a whole alternative of 620
(same variant count as splitting 620, no record-position filters). The
current record is found by rebuilding it from `SbatLevelRT` and matching its
sha256; exactly one match is required, which also proves the encoding. The
prediction mirrors shim's `set_sbat_uefi_variable`/`preserve_sbat_uefi_variable`:
candidate is the automatic payload, or latest with `SbatPolicy` = 1 (2
automatic, 3 reset is ignored under Secure Boot; shim deletes SbatPolicy
after reading it); a valid current level is kept if its version field is
higher or its first 10 datestamp characters aren't older. `.sbatlevel` is a
u32 format version (0) followed by two u32 offsets (automatic, latest)
relative to the offset table, pointing at NUL-terminated strings. Its PE
section name is longer than 8 bytes, so the section table holds `/<n>`, an
offset into the COFF string table (after the symbol table). Strings are passed
via `printf -v`/`read -d ''`, since `$(...)` would strip SbatLevel's trailing
newline; `tail | head` dies of SIGPIPE under `pipefail`, hence `dd`. The
pacman hook runs `--type=pe,secureboot-authority` (`--type=` takes a list).

**No option ROM variants.** Option ROMs verified before the separator (e.g.
KVMs a server provider attaches) add authority records that could be
anything. Accepting unknown ones would defeat the point of PCR 7, so a new
option ROM means a recovery unlock and a regenerated policy, by design.

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

**mkosi-initrd drop-ins** (`examples/mkosi-initrd/`; verified by a test build
with mkosi 27.1, not yet booted). mkosi-initrd builds from repository
packages, so a locally built toolkit package can't simply be added to
`Packages=`; host files come in with `ExtraTrees=` instead (single files work,
as mkosi-initrd's own crypttab handling shows). Its preset is `disable *` and
`preset-all` runs after the extra trees are copied, so units are enabled by
`initrd.target.wants` symlinks under `/usr/lib` (in
`/usr/lib/pcrlock-toolkit/mkosi-initrd/<name>`), which presets don't remove.
`systemd-pcrextend.socket` is statically enabled by the systemd package. The
drop-ins are examples for `/etc/mkosi-initrd`, never installed to
`/usr/lib/mkosi-initrd`, which would apply them to every build. Every
`ExtraTrees=` source must exist, so host unit drop-ins are commented out.

Checking an image: `systemctl --root=<dir> is-enabled` reports these units
(and systemd's own `systemd-pcrextend.socket`) as `disabled`, because it
ignores `.wants/` symlinks under `/usr/lib`, and `list-dependencies` refuses
`--root=`. Use `SYSTEMD_UNIT_PATH=<dir>/etc/systemd/system:<dir>/usr/lib/systemd/system
/usr/lib/systemd/systemd --test --system --unit=initrd.target` and look for
`Wants:` under `initrd.target`. Build with `-t cpio`: mkosi 27.1's
`-t directory` crashes copying the result out. Running mkosi-initrd
unprivileged fails on repositories that need the host keyring, and local
`file://` repositories aren't visible in its sandbox.

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

**MOR lock.** `mor-lock` sets `MemoryOverwriteRequestControl` bit 0
(ClearMemory) and then `MemoryOverwriteRequestControlLock` to 1 (locked, no
key), as Windows does with BitLocker. Locking with ClearMemory at 0 would
protect nothing; the kernel's `CONFIG_RESET_ATTACK_MITIGATION` (off on the
reference kernel) would set it in the EFI stub, but userspace would then have
to clear it on clean shutdown, which the lock forbids. The cost is a firmware
memory wipe on every reboot. That made no noticeable difference on the
reference machine, but it can slow reboots down a lot on other firmware.
Details that matter:

- efivarfs data is 4 bytes of attributes plus the value, written in one
  `write()`, and the kernel marks these variables immutable, so each write is
  wrapped in `chattr -i`/`+i` (`chattr` from e2fsprogs is needed in the initrd).
- Variables that don't exist are never created: on firmware without MOR that
  would just add an ordinary NV variable and fake the protection.
- "locked" is only reported after a probe write that clears ClearMemory is
  refused. If the write goes through, the old value is restored and the state
  is `unenforced`. `--status` doesn't probe, since it must not write.
- No `FailureAction=`, unlike platsec: every state is measured, and anything
  but `mor=on;morlock=locked` fails the PCR 15 policy, so the disks stay
  locked without powering off. The unit still fails for the journal.

**Separator error variant.** pcrlock ignores `/dev/null` symlinks for variant
files, so the `0xFFFFFFFF` separator variant is overridden with a same-named
copy of the success variant (`Type=copy`, `500-separator-no-error`). The
same trick replaces the empty `600-absent` variant of
`700-action-efi-exit-boot-services` with a copy of `300-present`
(`700-exit-boot-services-present`): firmware that doesn't log the
ExitBootServices events is buggy, not a configuration to accept.

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
- **PCR 7 encoding** (checked against aurora and the RAID server). Every
  record's digest is `sha256(event_data)`; variable events are
  `UEFI_VARIABLE_DATA` (GUID, u64 name length, u64 data length, UTF-16LE name,
  data). A missing dbx is measured with length 0. Authority records are
  variable `db` whose data is the matching db `EFI_SIGNATURE_DATA` (owner GUID
  and cert), so they can be rebuilt offline from the db variable; that
  reproduced the logged digest on both machines.
- **Late PCR 7 authorities with shim** are `[db, SbatLevel, db]`: firmware
  verifying shim, shim's `SbatLevel-605dab50-…` as `EV_EFI_VARIABLE_AUTHORITY`
  with the variable's text as data, then shim verifying systemd-boot against
  db (shim doesn't deduplicate against the firmware's record). Without shim
  it's a single db record.
- **Volume-key records.** cryptsetup's PCR 15 volume-key record differs by
  unlock method in practice. The NvPCR keyslot record
  (`tpm2-measure-keyslot-nvpcr`) carries the keyslot index.
- **v262 changes.** Since v262, cryptsetup measures via the pcrextend Varlink
  service, and `tpm2-measure-bank=` is ignored.
- **Stale `pcrlock.json`.** Deleting `/var/lib/systemd/pcrlock.json` fixed
  "Failed to allocate policy session" (TPM 0x9a2). PolicyAuthorizeNV 0x84
  means the submitted policy differs from the one stored in the NV index.

## Verified on the reference machine

As of October 2026 (AMD, systemd 262, dracut with the pcrextend socket fix),
one full boot with the generated policy:

- `bin/pcrlock` against the real `systemd-pcrlock`, including the
  kernel-install plugin; every component generated as expected.
- platsec in a real initrd on the AMD path (TSME, IVRS):
  `tsme=on;iommu=on;preboot_dma=on`.
- mor-lock on real firmware: efivarfs accepts the writes and the probe write
  is refused once locked (`mor=on;morlock=locked`). The memory wipe added no
  noticeable reboot time there; other firmware can be much slower.
- The seal's Varlink measurement matches the `lock-raw` component (799).
- `lock-file-system` with one call per mount point (`/` and `/var` both match
  820).
- The serialized unlock order: root against 785+790, then `secondary` (841)
  and `extra` (842) against the full chain.

Reading `systemd-pcrlock log`: the component column is what matters, since it
means the record's digest matched that component. The ✓/✗ column only checks
the logged content against the digest. ✗ is expected for volume-key records
(the content isn't the hashed data), and the seal shows `-` there.

## Not yet verified on real hardware

- The Intel paths of platsec: TME via MSR 0x982 and the DMAR flags offset
  (only exercised against fake sysfs).
- A booted mkosi-initrd image (only test-built).
- `Type=secureboot-authority` against the real `lock-secureboot-authority`
  (stubbed with aurora's logged authorities; the SbatLevel record and the
  prediction were checked against aurora's real `SbatLevelRT` and the
  installed shim 16.1). Not yet through a real shim update that raises
  SbatLevel.
- `Type=secureboot-policy` and the variant budget against the real
  `systemd-pcrlock` (only stubs fed with aurora's real PCR 7 log). In
  particular: that `lock-secureboot-policy` records come in log order, and
  that the budget formula matches how make-policy counts the location window.
- `pcrlock-secureboot-policy.path` firing on an efivarfs write (fwupd's dbx
  update on the RAID server is the planned test).

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
  BootOrder/BBS variants). Remaining PCR 7 work:
  - A dbx tool for a local-KEK setup: verify Microsoft's signed update,
    diff against the current dbx, refuse if anything that boots is revoked
    (`lock-pe` Authenticode hashes), sign the additions as an append write
    with the local KEK, predict, and rewrite `loader/keys/auto/dbx.auth`.
  - systemd PR 43420 (`NotifySecureBootUpdate`/`PrepareSecureBootUpdate`,
    takes the update payload) may replace the prediction once merged.
- **Packaging.** `PKGBUILD` builds from the working tree; switch to a tagged
  source before publishing.

## Licensing

Everything is 0BSD, declared for all files in `REUSE.toml` (no per-file
headers). Keep `reuse lint` passing; new files are covered by the `**` glob.

Upstream issue/PR text: the maintainer rewrites drafts in their own words, so
provide complete checklists of points rather than polished prose.
