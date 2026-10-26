# pcrlock-toolkit

Tooling around `systemd-pcrlock` for TPM2-unlocked Arch systems (shim →
systemd-boot → UKI, dracut initrd). User-facing docs are in `README.md`; this
file records how the code is organised and *why* it is the way it is. Many of
these decisions were reached by debugging real boots, so don't undo them
without a reason.

## Layout and commands

- `bin/pcrlock`: bash CLI (`generate`, `list`, `prune`, `uki add|remove`,
  `check`, `suspend-firmware`, `relock-firmware`, `decode`, `volume-keys`).
  Reads `/etc/pcrlock-toolkit/pcrlock.conf` and `components.d/*.conf`, writes
  to `/etc/pcrlock.d`.
- `libexec/cel-decode`: Python (stdlib only, `openssl` for certificate
  details), installed to `/usr/lib/pcrlock-toolkit/`. Decodes `event_data`
  of `systemd-pcrlock cel` output or `.pcrlock` files. `pcrlock decode`
  execs it before option parsing and the root check, since it has its own
  options and reads files.
- `bin/platsec-pcr-check`: POSIX sh, runs in the initrd. Checks memory
  encryption (TSME/TME), IOMMU and pre-boot DMA, measures one event, powers off
  if the baseline isn't met.
- `bin/mor-lock`: POSIX sh, runs in the initrd. Sets MOR ClearMemory, locks it
  without a key and measures the resulting state.
- `units/`: `platsec-pcr-check.service`, `mor-lock.service`,
  `initrd-pcr15-seal.service`, `pcrlock-prune.service`,
  `pcrlock-secureboot-policy.{path,service}`, `pcrlock-firmware-relock.service`,
  `pcrlock-firmware-suspend.{path,service}`.
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
make check                   # bash -n, sh -n, shellcheck, ruff; must pass (makepkg)
make test                    # pytest tests/; must pass (makepkg)
make install DESTDIR=/tmp/x  # staged install to inspect the layout
makepkg -si                  # pcrlock-toolkit-git from GitHub (pushed commits only)
```

**Tests** (`tests/`, pytest) pin the behaviour of `bin/pcrlock`, so that a
port (planned: Python) can be checked against the same suite:

- Each test gets a sandbox root (`tests/conftest.py`). The command under
  test is a copy of `bin/pcrlock` with every absolute system path rewritten
  into it. The harness asserts that each rewrite happened, so a code change
  can't silently escape to the real system. Only `Sandbox.run()` knows which
  implementation runs.
- Stubs in `tests/stubs/` log every call to `calls.log`:
  - `systemd-pcrlock`: `cel`, `lock-*` and `list-components`. The Secure
    Boot records are built from fake efivars with the real
    `UEFI_VARIABLE_DATA` encoding, so the dbx and SbatLevel rules are
    tested against real digests.
  - `systemctl`: `show` resolves the runtime drop-in like the real unit.
  - `bootctl`, `platsec-pcr-check`.
- `tests/efi.py` builds variables, signature lists, log records and a
  minimal PE with a long-named `.sbatlevel` section.
- `tests/test_real_log.py` runs only with a local dump
  (`sudo systemd-pcrlock cel > tests/local/cel.json`, gitignored: it holds
  the machine-id and volume UUIDs). It checks the harness's model against
  what the firmware and shim really logged, using this machine's efivarfs.
- When behaviour changes on purpose, change the test in the same commit.
  Deliberate regressions (keeping `-previous` unconditionally, an equal
  SbatLevel datestamp winning, no sum over stop points, a db change
  accepted, relock keeping its state file) each failed a test when the
  suite was written.

## Conventions

- shellcheck-clean. Disable a check only with an inline comment saying why
  (jq programs in single quotes; dracut-provided variables in module-setup.sh).
- Initrd code is POSIX sh (test with `dash -n`), or a native binary if
  needed. Host-side code may use bash, Python (stdlib only, so it needs no
  extra packages; Python is there anyway where ukify builds UKIs) or Rust.
  Binary parsing goes to Python, not bash. `make check` parses the Python
  files and runs `ruff check` if available.
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

Location window: `make-policy --strict=yes --location=791:940-`, set in a
drop-in for `systemd-pcrlock-make-policy.service`. `pcrlock` can't pass it
(it only restarts the unit), but `Location=` in `pcrlock.conf` (default
`791:940-`) is what the unit must use: `check_location` compares it with the
unit's `ExecStart=` and `apply_policy`/`relock-firmware` refuse on a mismatch
(warn with `--policy=none`). A missing `--location=` counts as pcrlock's
default `760-:940-`, which would accept states before platsec. Components
before 791 are mandatory; components in the window may or may not have
happened yet.

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

**Firmware updates: suspend, don't predict.** What new firmware measures into
PCR 0 (CRTM version, firmware volume blobs as mapped, AGESA/PSP events) can't
be derived from a capsule, and trusting the capsule's own claims would
defeat the point. `suspend-firmware` does what BitLocker and fwupd's plugin
do: one boot without the firmware PCRs. Details that matter:

- The reduced mask goes into a **runtime** drop-in
  (`/run/systemd/system/systemd-pcrlock-make-policy.service.d/zz-…`), so a
  reboot restores the real `ExecStart=`. The `zz-` prefix sorts it after the
  user's own drop-ins (drop-ins sort by file name across directories, and
  `override.conf` would otherwise reset `ExecStart=` again). `ExecStart=` is
  rebuilt from `systemctl show`'s `argv[]` with repeated `--pcr=N`.
- The persistent state file `/var/lib/pcrlock-toolkit/firmware-suspended` is
  what makes the relock unit run, and it's removed only after the full
  make-policy succeeded. Otherwise the NV index would keep the reduced policy
  indefinitely, since the boot-time make-policy run fails with stale
  firmware components.
- The relock unit is ordered **after** make-policy (restarting it from there
  is safe, unlike `pcrlock-prune.service`). `suspend-firmware` refuses unless
  the relock unit is enabled, and rolls back if make-policy fails.
- PCR lists are parsed with `read_pcrs`, never via `< <(...)`: a failing parse
  inside process substitution doesn't stop the caller, which once let a
  relock "succeed" with no PCRs and delete the state file.
- `pcrlock-firmware-suspend.path` automates the suspension for fwupd, whose
  own pcrlock plugin only drops systemd's `/var/lib` components (its
  `Lock(lock=false)` Varlink call) and so leaves `/etc/pcrlock.d` policies
  unchanged. fwupd plugins are built in, so a toolkit-side fwupd plugin
  isn't an option. Detection follows fwupd's uefi-capsule source:
  `fwupd-<class>-<hw_inst>` in GUID `0abba7dc-…` holds `EFI_UPDATE_INFO`
  (status u32 at struct offset 48, 1 = attempt update; efivarfs adds 4 bytes
  of attributes), and capsule on disk sets `OsIndications` bit 2.
  - The watch is `PathChanged=` on the efivars directory, since the variable
    name contains the ESRT class and the hardware instance. Events while the
    service runs don't start it again, hence the `sleep 2` before checking.
  - A stale pending state (firmware didn't apply the capsule) would otherwise
    suspend again after every relock. The fingerprint in `capsule-suspended`
    (pending variables + ESP capsule files with size and mtime) allows one
    suspension per staged update; staging again rewrites the file and
    changes the mtime.

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
- **PCR 1 boot variables** (aurora's AMI firmware, checked against efivarfs).
  The events are `EV_EFI_VARIABLE_BOOT`, but the digest is
  `sha256(UEFI_VARIABLE_DATA)` as for `BOOT2`, not of the data alone.
  `BootOrder` data is the whole variable. `Boot####` data is
  `var[6 : size-2]` with data length `size-8`: Attributes and
  FilePathListLength are left out and the end node is cut short, so
  `LOAD_OPTION_ACTIVE` isn't measured. The "BBS" entries (`Boot0002`–`0004`,
  "UEFI:CD/DVD Drive", "UEFI:Removable Device", "UEFI:Network Device") are
  firmware placeholders: a BBS device path node with device type
  `0x81`–`0x83`, not a standard BBS type. They stay in NVRAM while missing
  from `BootOrder`, so both variants can be rebuilt from efivarfs, but
  entries never seen can't be predicted.
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
- **shim internals** (read in rhboot/shim at 4dc4a1b, October 2026; source
  only, nothing booted):
  - SbatLevel's header row is parsed as a component named `sbat`, and every
    binary's `.sbat` starts with `sbat,1`. A level with `sbat,2` revokes
    everything, and shim refuses to apply a level that revokes itself.
  - Only component name and generation are compared; the version column
    (e.g. `262.1`) is ignored, so SBAT can't pin a build without a
    component whose generation changes per build.
  - The current SbatLevel is kept if its version field is higher, or if
    its datestamp is newer **or equal** (`strncmp` of the first 10
    characters). A replacement needs a strictly newer datestamp.
  - `revocations_sbat.efi` (needs both `.sbata` and `.sbatl`) and
    `shim_certificate*.efi` are read from shim's own directory, revocations
    first. Both are verified before the MOK state is imported, so only db
    or shim's built-in vendor cert can sign them.
  - The `.db` ESLs of `shim_certificate*.efi` are appended to the
    `MokListRT` mirror: shim trusts them, the kernel sees them as MOKs, and
    they are part of the MokList event logged to PCR 14. No MokManager is
    involved.
  - SbatLevel is measured into PCR 7 in `import_mok_state`, after the
    revocations file was applied. A SbatLevel overwritten by another OS is
    restored before it's measured.
  - `verify_eku` rejects a db or MokList entry carrying the module-signing
    EKU `1.3.6.1.4.1.2312.16.1.2` for images. It checks the list entry, not
    the leaf that signed the binary.
  - Certificate entries in dbx/MokListX are matched only as full X.509
    (`EFI_CERT_TYPE_X509_GUID`). shim doesn't support
    `EFI_CERT_X509_SHA256`, so every cert revoked through dbx costs a full
    certificate of space, and for the helper files shim is the only one
    checking.
  - A db (or MokListRT) **hash** match is measured like a cert match:
    `check_db_hash_in_ram` logs the matching `EFI_SIGNATURE_DATA` (owner
    GUID + hash) as `EV_EFI_VARIABLE_AUTHORITY` in PCR 7. EDK2-based
    firmware does the same as far as known. aurora's db holds the RTX 4090
    ROM as an `EFI_CERT_SHA256` entry (`dc4ab0fd…`, owner `18b539e7-…`);
    whether the early authority record before the separator is that hash
    is still to be checked with `pcrlock decode --text --pcr=7`.
  - shim deduplicates its own PCR 7 authority records
    (`tpm_data_measured`): each db or MokList entry it verifies with is
    measured once per boot, the first time it's used.
  - `shim_verify` (used for systemd-boot's loads) runs the SBAT check too.
  - The helper files (`revocations_sbat.efi`, `shim_certificate*.efi`) go
    through `verify_image` → `verify_buffer`, like any image shim loads:
    - **Denylist first.** A file is revoked by its Authenticode SHA-256 or
      SHA-1 in dbx, MokListX or the built-in vendor dbx, and every file
      signed under a cert that matches a dbx or MokListX cert entry is
      revoked. Only shim enforces this, since the firmware never loads
      these files.
    - **`.sbat` is mandatory.** A missing `.sbat` passes only for images
      verified through the shim protocol (`in_protocol`), and these files
      aren't.
    - **Two levels.** `revocations_sbat.efi` is checked against the level
      from before it's applied (first directory pass), and
      `shim_certificate*.efi` against the level after it (second pass).
    - **Rejected files are skipped silently.** `load_unbundled_trust` only
      logs "Failed to load addon certificates / sbat level", and the boot
      continues.
    - **PCR 4.** Each file is measured into PCR 4 (`tpm_log_pe`) after it
      verified; rejected files leave no record. The order is
      `revocations_sbat.efi`, then the `shim_certificate*` files in
      directory enumeration order, not by name. On FAT that's the order of
      the directory slots, so a deleted and recreated file can change it.
  - With a pending `Mok*` request (`MokNew`, `MokSB`, …) and no MokManager,
    shim started from the removable path (`\EFI\BOOT\BOOTX64.EFI`) resets
    after a 10 s countdown, i.e. loops; elsewhere it fails to boot.
- **Volume-key records.** The PCR 15 volume-key record is
  HMAC(volume key, `cryptsetup:<name>:<LUKS UUID>`), so it doesn't depend
  on the unlock method, and its sha256 digest is exactly the
  `fixate-volume-key=` value (enrolled that way on aurora). The NvPCR
  keyslot record (`tpm2-measure-keyslot-nvpcr`,
  `cryptsetup-keyslot:<name>:<uuid>:tpm2:1`) is the one that names the
  unlock method and keyslot, so it changes with them.
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
- `suspend-firmware`/`relock-firmware` through a real capsule update (only
  stubs so far): whether the firmware boots the OS once while applying the
  capsule, and that make-policy accepts the drop-in's `--pcr=` list.
- `pcrlock-secureboot-policy.path` and `pcrlock-firmware-suspend.path`
  firing on a real fwupd update (fwupd's dbx update on the RAID server is the
  planned test), and the capsule detection against a real `fwupdmgr update`
  (only checked against fwupd's source and fake efivars). efivarfs does emit
  inotify events (fsnotify is in the VFS): on aurora, creating a variable via
  efivarfs gave `CREATE`, `CLOSE_WRITE` and `DELETE` on the directory, even
  though the write failed (a volatile variable can't be created at runtime,
  `EINVAL`; efivarfs removes the file again). A `.path` unit with
  `PathChanged=` on a directory fires for such child events.

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
  - dbx updates for a local-KEK setup: part of the Secure Boot tool below.
  - systemd PR 43420 (`NotifySecureBootUpdate`/`PrepareSecureBootUpdate`,
    takes the update payload) may replace the prediction once merged.
- **Secure Boot tool (design, nothing written yet).** Manages the key
  hierarchy, MOKs and signing, so that only the current boot chain passes
  Secure Boot itself, not just the TPM policy. Reason: with dual boot,
  another OS that doesn't seal its disks to the TPM could be attacked with
  old binaries we signed. See the shim internals in the facts section.
  Suggested order: key management and `.auth` building → dbx/option ROMs →
  MOK-only signing → SBAT generations and revocations.
  - **Keys.** tpm2-openssl by default (opt-out for file or other keys).
    PK and KEK have a password. db and the boot MOK carry the code-signing
    EKU; the DKMS key carries the module-signing EKU, so shim refuses
    images signed with it while the kernel still accepts its modules. Each
    key is enrolled directly as a self-signed cert, not under a shared CA,
    because shim checks the EKU of the list entry, not of the leaf.
  - **Per-purpose db keys**, each rotated on its own schedule through KEK:
    - **shim key:** signs shim only, which changes about once a year.
    - **revocations key:** signs `revocations_sbat.efi`. Rotated often
      (e.g. monthly), so an old revocations file can't downgrade SbatLevel
      after an SbatLevel or NVRAM reset: it no longer verifies once its key
      is gone from db.
    - **certificates key:** signs `shim_certificate*.efi`. Rotated whenever
      a MOK changes, so an old certificate file with a possibly compromised
      MOK stops verifying.
  - **Chain.**
    - db keys sign only shim, `shim_certificate*.efi` and
      `revocations_sbat.efi`, each with its own key.
    - systemd-boot and the UKI are signed with the boot MOK, so the
      firmware won't start them without shim.
    - The MOKs come from `shim_certificate*.efi`. `mmx64.efi` is removed
      from the ESP and `mokutil` uninstalled, so MokList can't be changed
      at all.
    - Pending `Mok*` request variables are cleared at shutdown: another
      distribution's `mokutil` in a dual boot would otherwise stop the boot,
      or make it loop from the removable path.
  - **SBAT generations.** Upstream and Arch never raise `systemd-boot` /
    `systemd-stub` generations (Arch's are still 1 after
    GHSA-6m6p-rjcq-334c), and shim ignores the version column. So we add
    our own component per binary and raise its generation per build:
    - Name it after an app-specific ID (`systemd-id128 machine-id
      --app-specific=…`), not the raw machine-id.
    - The UKI gets the line via `ukify --sbat=`. systemd-boot (and
      optionally shim) gets its `.sbat` patched before signing, and PCR 4
      then locks the patched binary.
    - Generations go through `atoi` into a `UINT16`.
  - **SbatLevel.** Always the merge of upstream's latest level (shim's
    `.sbatlevel`) and our lines, taking the maximum generation per
    component.
    - Precedence comes from a far-future datestamp (about today + 100
      years), never `sbat,2`, which revokes everything.
    - Each bump must be strictly newer: store the last value and use
      `max(last + 1, today + 100 years)`.
    - Consequences: no built-in shim payload applies on this machine again,
      so the merge has to be kept up. Undoing it needs Secure Boot off and
      an `SbatPolicy` reset.
    - An overwrite by another OS is restored before the next measurement,
      so PCR 7 doesn't change.
  - **Revocations.**
    - `revocations_sbat.efi` (same payload in `.sbata` and `.sbatl`) is
      built and signed with the revocations key when the UKI or
      systemd-boot is built. It changes with every generation bump, so its
      old versions are never revoked by hash in dbx; rotating the
      revocations key cuts them off.
    - `pcrlock-prune.service` only moves it into place, after a boot has
      reached switch-root with the new binary, so the previous one stays as
      a fallback until then and no db key is used in early boot.
    - A signing log (like a CA's) records everything the db keys signed.
      Replaced shims go into dbx by hash (rare, small). Helper files are cut
      off by key rotation, not by dbx.
  - **Helper files** (see the shim internals in the facts section):
    - **No SBAT component of our own**, for either kind:
      - Revocations files are already ordered by the datestamp in their
        `sbat,1,<datestamp>` header.
      - SBAT can't protect against a reset SbatLevel either: the file is
        checked against the level that was just reset.
      - An SBAT component on certificate files falls to the same attack
        (reset SbatLevel, remove the current revocations file); key
        rotation doesn't.
      - A revocations file that raised its own component would also reject
        itself at every later boot.
    - Built on `addonx64.efi.stub`, they carry systemd's `.sbat` only
      because shim requires one (`systemd-addon` stays at 1 on Arch). shim
      only parses these files and never executes them, so the stub's own
      vulnerabilities don't matter.
    - A rejection fails closed, but late. A rejected certificate file means
      no MOK, so systemd-boot isn't trusted and shim falls back to the
      missing MokManager. A rejected revocations file means SbatLevel isn't
      raised and the PCR 7 prediction fails, so a recovery unlock follows.
      Before putting files in place, the tool checks they'd pass: not in
      dbx, and SBAT against the predicted level.
    - Rotation step 2 (old cert removed from db) cuts off every file signed
      with the old key; they must be re-signed in step 1.
    - The PCR 4 order follows FAT directory slots. Keep a single
      `shim_certificate` file, or rewrite files in place instead of
      deleting and recreating them. PCR 4 needs components for these
      measurements.
  - **`.auth` files.** Built by the tool, not `bootctl
    --secure-boot-auto-enroll` (one key for PK, KEK and db).
    - `PK.auth`: PK, self-signed.
    - `KEK.auth`: own KEK, plus optional Microsoft or manufacturer KEKs,
      signed by PK.
    - `db.auth`: own db certs (one per purpose), option ROM hashes, extra ESLs and the chosen
      Microsoft CAs, signed by KEK.
    - `dbx.auth`: a full snapshot, signed by KEK.
    - The `loader/keys/auto/` copies are always full snapshots of the
      current state.
  - **KEK/db/dbx sources.** `/etc/pcrlock-toolkit/secureboot/{KEK,db,dbx}.d/*.conf`,
    `Key=Value` and parsed, merged in name order. A CLI only adds and
    removes these files and builds/applies from them. Types:
    - `certificate`, `sha256`, `esl`
    - `firmware-default`: `KEKDefault`/`dbDefault`/`dbxDefault`, like
      sbctl's built-in keys; not every board has them
    - `microsoft`: named subsets, e.g. only Microsoft Option ROM UEFI CA
      2023, or the full set for dual boot
    - `signing-log`: our own revoked binaries, for dbx
    - `microsoft-dbx`: the staged, verified update

    Option ROM hashes come from the PCR 2 `EV_EFI_BOOT_SERVICES_DRIVER`
    digests. On aurora that equals the Authenticode hash of the RTX 4090's
    ROM. Own entries use a stable per-installation owner GUID; it's part of
    every PCR 7 authority record, so it must never change once enrolled.
  - **Writing the variables.** dbx additions as append writes; removals and
    db/KEK changes as full writes with a newer timestamp.
  - **PCR 7 prediction for the tool's writes** (on by default, opt-out).
    The `Type=secureboot-policy` rule becomes: automatic updates may only
    change dbx; writes made through the tool are user-initiated and
    predicted exactly.
    - Predict by reading back the variables after the write
      (`lock-secureboot-policy`), never by modelling the firmware's storage.
    - The tool leaves a short-lived "expected change" record (variable and
      exact bytes written). A PK/KEK/db difference is accepted only if the
      read-back equals it; anything else still fails, so changes by others
      stay unaccepted even while a tool write is pending.
    - A scheduled rotation could reuse this later.
  - **Key rotation in two steps**, one predicted boot each, so one bootable
    path always exists:
    1. Write db with the new cert next to the old one (full write), and
       re-sign every file the key signs. Predict the db config record from
       the read-back, and the authority record for that key with the old
       entry's `EFI_SIGNATURE_DATA` replaced by the new one, at the
       position the current log has it.
    2. After a successful boot, write db without the old cert. That alone
       makes everything the old key signed fail the allowlist.

    Old db certs are **removed, not put into dbx**, so frequent rotations
    don't grow dbx (which would cost a full certificate each, see the shim
    internals). A dbx entry would only help against a db rollback, which in
    user mode needs the KEK (time-based writes must be newer). The other
    route, an NVRAM reset into setup mode, also clears dbx and lets an
    attacker enroll their own keys anyway.

    Each db key has its own pending rotation state. MOK rotation works the
    same way through `shim_certificate*.efi`: PCR 14 MokList and the
    `MokListRT` authority records. KEK and PK are config records only, so
    their read-back covers them.
  - **Frequent rotation caveats** (firmware behaviour from memory, varies
    by vendor):
    - **Dual boot:** every OS's boot measures the full db into PCR 7, so
      each rotation breaks PCR 7 seals of other OSes (BitLocker by default,
      systemd-cryptenroll elsewhere) and causes a recovery prompt there.
      Linux can't suspend BitLocker. The rotation interval is configurable
      (monthly is fine for single boot); the tool warns when other OSes are
      found in the boot entries.
    - **`.auth` snapshot:** firmware updates often reset the keys, and
      systemd-boot then re-enrolls from `loader/keys/auto/`. A snapshot from
      before a rotation enrolls the old certs without the new one; the
      current certificate file then fails and nothing boots. The snapshot
      is rewritten in the same transaction as every db write, and failing to
      write it fails the rotation.
    - **Write ordering and NVRAM space:** many firmwares reclaim the
      variable store only at boot, so a runtime write can fail with
      `EFI_OUT_OF_RESOURCES`. Write db first and check the read-back; only
      then swap in the re-signed files. A failed write leaves everything as
      it was. Step 1 keeps both certs, so an interruption is harmless. Keep
      db small.
    - **Combine pending PCR 7 changes:** the location window doesn't help,
      since every PCR 7 component sits before 791, and the state before the
      window is the product of their variant counts (pcrlock counts "old db
      with new authority" too). SbatLevel and the authority records share
      620, so with one combined "next state" variant per component it's
      240 × 620 = 2 × 2 = 4, however many changes are pending. Separate
      variants per change would multiply past 8. A single whole-state PCR 7
      component (all PCR 7 records in log order, PCR 7 taken out of the
      separator component) would bring it to 2. `check_budget` runs before
      each write either way.
    - **Timestamps:** every full write needs a strictly newer timestamp;
      use `max(now, last + 1 s)` so a clock that went backwards doesn't get
      writes refused.
    - **Flash wear:** negligible. Two writes of a few KB per rotation,
      against `BootOrder`/`BootNext` writes and ~100k erase cycles per
      sector.
  - **Per-binary db entries (evaluated, not planned).** Idea: an ephemeral
    key per systemd-boot/UKI build, its cert written to db and the private
    key discarded, replacing the previous one each iteration. Revoking by
    removal from db survives an SbatLevel reset, unlike SBAT. But:
    - **No benefit over a hash.** db hash matches produce a PCR 7
      authority record too (see the shim internals), so a per-binary
      `sha256` entry gives the same measurement without keys.
    - **KEK per iteration.** Step 2 (removing the old entry after a good
      boot) can be pre-signed at build time with timestamp + 1, but the
      build still needs the KEK. A password would prompt in every
      kernel-install run, and the early-boot PCR 11 credential isn't usable
      at runtime. That works against protecting the KEK at all.
    - **Bypasses shim.** Anything in db can be started by the firmware
      directly: no `shim_certificate` MOKs (the kernel lacks the DKMS key),
      no SBAT check. Using an entry in the MOK list via a new
      `shim_certificate*.efi` per iteration avoids that, but shim loads
      every certificate file present, so an old one placed next to the
      current one is trusted again unless the certificates key rotates each
      time: a db write again.
    - **PCR 7 churn.** Every iteration changes the db config record and an
      authority record, competing with rotations and SbatLevel bumps for
      the 8-value budget.

    Conclusion: at most per-binary db hashes for systemd-boot on single
    boot (rare updates), accepting a KEK signature per update and that it
    can be started without shim. Not for UKIs; their revocation stays with
    SBAT generations and the rotated revocations key.
  - **Key and file layout.** A deliberate exception to the toolkit-owned
    `/etc/pcrlock-toolkit/` naming, since `/etc/secureboot` is an
    established shared layout:
    - `/etc/secureboot/keys/{PK,KEK,db,dbx}/`: only the signed lists built
      from the sources, the sbkeysync keystore layout, so users can enroll
      with sbkeysync. No private keys there, because sbkeysync parses
      whatever those directories contain. sbkeysync only adds missing
      entries; removals, rotations and full snapshots go through the tool,
      and the README must say so.
    - `/etc/secureboot/private/` (0700): private keys (TPM2 key blobs, or
      files when opting out) and their certs.
    - `/etc/pcrlock-toolkit/secureboot/owner-guid`: the owner GUID. It's
      configuration that must never change, so `/etc`, not `/var/lib`. It's
      created on first use and never regenerated while an entry with that
      owner is enrolled. There's no canonical location; sbctl keeps its own
      in a file named `GUID`.
    - An existing keystore (old sbctl used `/usr/share/secureboot`, newer
      releases `/var/lib/sbctl`) is detected, and the tool refuses to mix
      keys with it.
  - **Microsoft dbx, staged.**
    - At runtime: download it, strip the `EFI_VARIABLE_AUTHENTICATION_2`
      header, verify Microsoft's signature against their KEK, refuse if
      anything that boots is revoked (`lock-pe` Authenticode hashes), and
      stage it.
    - At the next boot, before the ready phase: sign the staged update
      with the KEK. Its password is a `systemd-creds` credential bound to
      an early-boot PCR 11 policy that `ready` invalidates.
    - Then predict PCR 7 and rewrite `dbx.auth`. fwupd's `uefi_dbx` can't
      work without Microsoft's KEK; disable it.
  - **PCR 7/14 shape.** Late authorities become roughly:

    ```text
    db (shim key)           firmware verifying shim
    db (revocations key)    shim, revocations_sbat.efi
    db (certificates key)   shim, shim_certificate*.efi
    SbatLevel               import_mok_state, after the helper files
    MokListRT (boot MOK)    systemd-boot, …
    ```

    shim measures each entry once per boot. Every generation bump adds a
    SbatLevel variant, and every rotation changes one authority record plus
    the db config record; both are predicted. `Type=secureboot-authority`
    and the PCR 14 component (MokList now includes the `shim_certificate`
    certs) need updating.
  - **Own shim build.** A `shim-src` package installs the current shim
    source (e.g. under `/usr/share`), and the tool builds shim locally, the
    way DKMS builds modules (gcc; the bundled gnu-efi and OpenSSL come with
    the source).
    - **Vendor key:** built in via `VENDOR_CERT_FILE` (or several entries
      via `VENDOR_DB_FILE`), managed and rotated by the tool. It
      only authorises other keys, like a CA: it signs `shim_certificate*.efi`
      and `revocations_sbat.efi`. It carries only the code-signing EKU and
      is held in the TPM. shim mirrors it into `MokListRT`, so the kernel
      sees it like a MOK. On aurora MOKs are trusted for modules, so it
      could probably sign modules too, unless the kernel restricts that
      keyring to CA certs.
    - **Only this shim accepts the helper files.** Files anchored to the
      vendor key are rejected by any other shim (another distribution in a
      dual boot), and the vendor cert isn't in db.
    - **Built-in SbatLevel:** shim's own `.sbatlevel` (automatic and latest)
      is the merged level with the far-future datestamp, so a new shim
      raises SbatLevel by itself. `revocations_sbat.efi`, signed by the
      vendor key, is only needed for bumps between shim builds.
    - **Built-in revocations:** `VENDOR_DBX_FILE` compiles a dbx in, which
      shim mirrors to `MokListXRT`. That replaces MokListX entries, which
      need MokManager otherwise.
    - **Enrolled by hash (single boot):** the shim stays unsigned and its
      Authenticode hash goes into db. Each rebuild (e.g. monthly with the
      vendor key rotation) is a KEK-signed db write, interactive, in two
      steps: add the new hash, remove the old one after a good boot. No
      dbx entry is needed, since removing the hash is the revocation. The
      tool builds the binary, so PCR 4 (Authenticode), the db entry and the
      authority record (PCR 7) are known before the reboot; bit-reproducible
      builds aren't needed.
    - **Build options** default on, opt-out in the config: `REQUIRE_TPM`,
      the loader path set to systemd-boot's default path (so systemd-boot
      updates work unchanged), no `fbx64.efi` next to a shim in `\EFI\BOOT`,
      later `ENABLE_CODESIGN_EKU`. There's no build option that disables
      MokManager; `check_mok_request` must be patched to ignore requests.
    - `ENABLE_CODESIGN_EKU` is broken upstream: since the OpenSSL 3.5.0
      migration it only compiles `CryptPkcs7VerifyEku.c`, and nothing calls
      `VerifyEKUsInPkcs7Signature` (PKCS#7 verification uses
      `X509_PURPOSE_ANY`). Reported as
      [rhboot/shim#834](https://github.com/rhboot/shim/issues/834); a
      maintainer is looking into it.
  - **Per-key trust anchors.** Each key in the config says how its trust is
    anchored, e.g. `Anchor=`:
    - `mokmanager`: enrolled by the user via MokManager; the tool only
      manages the key (TPM-held, signing).
    - `vendor`: delivered by a `shim_certificate*.efi` signed by the vendor
      key of our own shim build; this installation only.
    - `db`: a certificate file signed by a db key; every installed OS's
      shim accepts it.
    - `ephemeral-db`: a db key rotated constantly (see the per-binary
      evaluation for its cost).

    MOKs delivered by certificate files exist only for the boot that
    loaded them: our running system sees them in `MokListRT` and the kernel
    keyring, but another installed OS (e.g. Fedora for a game's anti-cheat)
    never sees them, unlike MokList in NVRAM, which every OS shares.
    Where a key is anchored also decides what it's trusted for: a
    third-party key (e.g. NVIDIA's) in db is trusted by the firmware for
    boot images, and in MokList or a certificate file shim trusts it for
    images too, unless it carries the module-signing EKU. The tool warns
    when such a key is anchored where shim trusts it for images.
  - **Per-key revocation lists.** The signing log keeps, per db key and
    MOK, what it signed and which of those are revoked. dbx and the
    built-in vendor dbx (MokListX) are assembled from the lists of every
    key that is still trusted, plus the Microsoft dbx source if configured.
    A rotated-out key's list drops out: what it signed already fails the
    allowlist once the key is gone. Shrinking dbx is a full write (KEK),
    with the `.auth` snapshot rewritten in the same step.
  - **Module MOK rotation.** Configurable; default one module MOK per kernel
    version, like the kernel's own throwaway module key. A signature never
    has to stay valid across kernel versions.
    - **Trigger:** a kernel-install plugin (runs for every kernel install,
      whatever the initrd generator) rather than a pacman hook; time-based
      rotation as an option.
    - **Ordering:** DKMS signs from its own pacman hook
      (`70-dkms-install.hook` on Arch), so the key must exist before. Most
      robust: the signing helper (`sign-file-tpm2`) creates the key on
      demand when the kernel version has none, which also covers
      `dkms autoinstall` run by hand.
    - **Same transaction:** the cert goes into the vendor-signed certificate
      file and takes effect at the next boot, together with the new kernel.
    - **Lifetime:** the private key is kept while its kernel is installed
      (DKMS rebuilds for the same kernel need it), then deleted and its
      cert dropped from the certificate file. Deleting it during an upgrade
      that replaces the running kernel is harmless: the running kernel only
      needs the cert, which is already in this boot's `MokListRT`.
    - **One MOK per module build** works too, but a new cert only takes
      effect at the next boot (an nvidia update without a kernel update
      can't load until a reboot), and every DKMS build changes PCR 14 and
      PCR 4.
- **SBAT tool (design, nothing written yet).** Arch never raises the
  generations of its SBAT entries (only shim's matter), so the toolkit
  could give SBAT a use of its own. Independent of the Secure Boot tool.
  - An `sbat` command manages entries, bumps generations, and assembles
    `.sbat` contents from several entries (e.g. for `ukify --sbat=`).
  - **Component names** use a prefix generated once and stored in the
    config, never the hostname: if names changed, binaries with the old
    names could never be revoked again. E.g. `<prefix>-kernel-linux-zen`,
    `<prefix>-systemd`, `<prefix>-uki-linux-zen`, `<prefix>-systemd-boot`.
  - **Generations follow install order**, not version order: each install
    event (pacman hook for kernels and systemd, each UKI signing) gets the
    next generation. The tool keeps the mapping generation ↔ package
    version; the `vendor_version` column can carry the package version for
    readers.
  - **`sbat require <component> <pkgver>`** raises the minimum to the
    generation that version got when it was first installed. A config can
    automate it (e.g. keep only the last N kernels).
    - A **downgrade** gets a new, higher generation (installing it is
      deliberate), so it stays bootable; the hook warns if it's older than
      the declared minimum. Reusing its old generation would lock it out
      until SbatLevel is reset (Secure Boot off).
    - **Lockout guard:** `require` and the automatic policy refuse to revoke
      the booted binaries or the kept fallback, and the level is raised
      only after a successful boot.
  - **Where the lines go:** the UKI gets kernel, systemd (stub) and uki
    lines via `ukify --sbat=`. systemd-boot needs its `.sbat` replaced
    before signing: rewrite in place when the new contents fit the
    section's file padding, otherwise append a new section and rename the
    old one (e.g. `.osbat`), since shim refuses two `.sbat` sections. In
    Python, not objcopy. PCR 4 then locks the patched binary.
  - SbatLevel changes don't touch other OSes' PCR 7 (only shim measures
    it). It still doesn't protect against an SbatLevel reset plus an old
    revocations file; that relies on rotating the key that signs them.
- **Packaging.** `PKGBUILD` is a VCS package (`pcrlock-toolkit-git`) that
  clones the default branch from GitHub, so it only builds pushed commits
  (the header comment shows how to build the working copy). Add a regular
  package with a tagged source once there's a release; `pkgver()` already
  switches to `<tag>.r<n>.<hash>` after the first tag.

## Licensing

Everything is 0BSD, declared for all files in `REUSE.toml` (no per-file
headers). Keep `reuse lint` passing; new files are covered by the `**` glob.

Upstream issue/PR text: the maintainer rewrites drafts in their own words, so
provide complete checklists of points rather than polished prose.
