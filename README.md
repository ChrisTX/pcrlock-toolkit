# pcrlock-toolkit

Tools around [systemd-pcrlock](https://www.freedesktop.org/software/systemd/man/latest/systemd-pcrlock.html)
for a TPM2-unlocked system:

- **`pcrlock`**: generates and maintains `.pcrlock` components from a
  declarative configuration, locks UKIs from kernel-install, and prunes stale
  variants.
- **`platsec-pcr-check`** + dracut module `platsec`: checks the platform
  security baseline (memory encryption, IOMMU, pre-boot DMA protection) in the
  initrd, measures it, and powers off if it isn't met.
- **`mor-lock`** + dracut module `morlock`: in the initrd, asks the firmware
  to wipe memory on the next reset (MOR) and locks that request (MOR Lock, no
  key), then measures the result into PCR 15. The firmware then wipes memory
  on every reboot, which can make reboots noticeably slower with a lot of RAM.
  `mor-lock --status` shows the current state.
- **`initrd-pcr15-seal.service`** + dracut module `pcr15seal`: extends a
  separator into PCR 15 at the end of the initrd.
- **`sign-file-tpm2`**: DKMS module signing with a TPM2-held key. To use it,
  copy `/usr/share/pcrlock-toolkit/examples/dkms/framework.conf.d/mok-signing.conf`
  to `/etc/dkms/framework.conf.d/` and adjust the key paths.

## Layout

| Path | Purpose |
|---|---|
| `/usr/bin/pcrlock` | component generator CLI |
| `/usr/bin/platsec-pcr-check` | baseline check (initrd and `--status` on the host) |
| `/usr/bin/mor-lock` | MOR set + lock (initrd and `--status` on the host) |
| `/etc/pcrlock-toolkit/pcrlock.conf` | global settings for `pcrlock` |
| `/etc/pcrlock-toolkit/components.d/*.conf` | one file per component |
| `/etc/pcrlock-toolkit/platsec.conf` | platsec settings (copied into the initrd) |
| `/etc/pcrlock-toolkit/openssl-tpm2.cnf` | OpenSSL config used by `sign-file-tpm2` |
| `/usr/lib/kernel/install.d/99-pcrlock.install` | locks UKIs on kernel install/removal |
| `/usr/share/libalpm/hooks/25-pcrlock-pe.hook` | re-locks boot loaders on package updates |
| `pcrlock-prune.service` | runs `pcrlock prune` early at boot, before make-policy (opt-in) |
| `pcrlock-secureboot-policy.path` | regenerates the Secure Boot policy component when dbx changes (opt-in) |
| `/usr/lib/dracut/modules.d/91platsec`, `91morlock`, `91pcr15seal` | initrd modules (opt-in) |
| `/usr/lib/pcrlock-toolkit/mkosi-initrd/` | `initrd.target.wants` trees for the mkosi-initrd drop-ins |
| `/usr/share/pcrlock-toolkit/examples/` | example components, dracut and mkosi-initrd configs, pacman hooks, DKMS signing config |

### mkosi-initrd

With `initrd_generator=mkosi-initrd` in `/etc/kernel/install.conf`, use the
drop-ins instead of the dracut modules: copy what applies from
`/usr/share/pcrlock-toolkit/examples/mkosi-initrd/mkosi.conf.d/` to
`/etc/mkosi-initrd/mkosi.conf.d/`, then rebuild the UKI (`kernel-install add-all`).
They copy the script, the units and `platsec.conf` in with `ExtraTrees=` and
add the `initrd.target.wants` symlinks, since mkosi-initrd presets every unit
to disabled. Host drop-ins for the units aren't picked up automatically;
uncomment the matching `ExtraTrees=` line. `rd.shell=` and `rd.emergency=`
are dracut options and have no effect in a mkosi-initrd image.

`examples/hardening/` in the source tree holds unrelated system hardening
examples (kernel command line, sysctl, modprobe, sysusers, systemd and dracut
drop-ins). They are not installed by the package; see
[`examples/hardening/README.md`](examples/hardening/README.md) for where each
file goes and what every setting does.

## `pcrlock`

```text
pcrlock generate [NAME...]     generate all enabled (or the named) components
pcrlock list                   show components and whether they match this boot
pcrlock prune                  drop -previous variants and UKIs of removed kernels
pcrlock uki add KVER UKI [KERNEL]
pcrlock uki remove KVER
pcrlock check                  show how many values each PCR can take (max 8)
```

Options: `--type=TYPE[,TYPE...]`, `--policy=now|mark|none`, `--dry-run`, `-v`.
After any change the policy is updated by restarting
`systemd-pcrlock-make-policy.service` (`now`), marking it for pacman's
restart-marked hook (`mark`), or not at all (`none`). Put make-policy options
such as `--location=` into a drop-in for that service.

### Policy size

make-policy builds one TPM2 PolicyOR per PCR, and a PolicyOR takes at most 8
branches. The number of values a PCR can take is the product of the variant
counts of all components that measure into it, and inside the `--location=`
window every point where the boot may stop adds its own value. A pending UKI,
shim and systemd-boot update (each with a `-previous` variant) already uses
all 8 for PCR 4.

Before every policy update `pcrlock` counts this per PCR, using `--location=`
and `--pcr=` from the make-policy unit, and refuses to update the policy if a
PCR is over the limit. The current policy stays in place; `pcrlock prune`
after the next reboot usually brings the count down again. `pcrlock check`
shows the counts. They are upper bounds: variants that happen to produce the
same value are counted twice.

### Boot loader updates and pruning

`25-pcrlock-pe.hook` runs
`pcrlock generate --type=pe,secureboot-authority --policy=mark` when the
shim or systemd-boot binaries in their packages change, and
`30-systemd-restart-marked.hook` then updates the policy once for the whole
transaction. `Type=pe` components lock the unsigned binaries: PCR 4 records
the Authenticode hash, which excludes the signature, so it is the same for the
signed copy that boots (check once with `systemd-pcrlock lock-pe` on both).
That makes the hook independent of signing hooks such as sbctl's.
Examples for a signing hook and for installing shim and systemd-boot behind
it are in `examples/hooks/`. `bootctl update` doesn't manage systemd-boot when
it is installed as shim's second stage (`\EFI\BOOT\grubx64.efi`), so that
copy needs its own hook.

The variant that matched the running system before an update is kept as
`-previous`. Likewise, `pcrlock uki remove` (run by kernel-install when a
kernel is removed, which on Arch happens on every kernel upgrade) keeps the
variant of the running kernel. `pcrlock-prune.service` removes both on the
next boot once they no longer match, before
`systemd-pcrlock-make-policy.service` rebuilds the policy:

```text
systemctl enable systemd-pcrlock-make-policy.service pcrlock-prune.service
```

### Components

A component is `/etc/pcrlock-toolkit/components.d/<priority>-<name>.conf` and
produces `/etc/pcrlock.d/<priority>-<name>.pcrlock`. With `KeepPrevious=yes`
it produces `<priority>-<name>.pcrlock.d/<name>.pcrlock` instead. When that
file changes and the old one matches the current boot, the old one is kept as
`<name>-previous.pcrlock`, so the running system stays covered until it
reboots. `pcrlock prune` removes it once it no longer matches.

| Type | Produces | Keys |
|---|---|---|
| `pe` | `lock-pe` of an EFI binary (KeepPrevious defaults to yes) | `Path=` |
| `log` | records from the current event log | `PCR=`, `EventTypes=`, `Filter=` (jq boolean) |
| `volume-key` | cryptsetup volume key measurement from the event log | `Volume=`, `PCR=` (15) |
| `identity` | machine-id and file system identities, merged | `MountPoints=` (`/`) |
| `raw` | a fixed string, like `systemd-pcrextend` | `PCR=`, `Text=` |
| `platsec` | the current `platsec-pcr-check --status` (refuses if not met) | `PCR=` (from platsec.conf) |
| `copy` | a copy of an existing `.pcrlock` file | `Source=` |
| `secureboot-policy` | `lock-secureboot-policy` (PCR 7); only dbx may differ from the current boot (KeepPrevious defaults to yes) | |
| `secureboot-authority` | `lock-secureboot-authority` (PCR 7), with the SbatLevel the shim at `Path=` will set (KeepPrevious defaults to yes) | `Path=` (optional) |

Common keys: `Enabled=`, `Output=` (path relative to `/etc/pcrlock.d`),
`KeepPrevious=`, `MinRecords=` (default 1).

A failing component (for example a volume not unlocked via TPM this boot)
keeps its existing file; the others still run.

### Secure Boot policy and dbx updates

PCR 7 starts with one record each for the `SecureBoot`, `PK`, `KEK`, `db` and
`dbx` variables. A dbx update changes the variable right away, but the
firmware measures it only on the next boot. `Type=secureboot-policy` locks the
current variables, so after an update it predicts the next boot, and the
variant of the running boot is kept as `-previous` until `pcrlock prune`.
Only dbx is predicted: if `SecureBoot`, `PK`, `KEK` or `db` differ from what
this boot measured, the component fails and keeps its file. Such changes
need a recovery unlock on the next boot and a `pcrlock generate` after it.

`examples/components.d/240-secureboot-policy.conf` uses the same name as
systemd's own generated component. Remove that one and disable the unit that
regenerates it:

```text
rm -r /var/lib/pcrlock.d/240-secureboot-policy.pcrlock.d
systemctl disable systemd-pcrlock-secureboot-policy.service
systemctl enable pcrlock-secureboot-policy.path
```

`pcrlock-secureboot-policy.path` runs `pcrlock generate --type=secureboot-policy`
whenever the dbx variable changes, e.g. when fwupd applies a dbx update.
fwupd's own `systemd-pcrlock` plugin (2.1.7+) instead asks systemd-pcrlock
to drop its generated Secure Boot components before an update and relies on
systemd's units to lock them again; it doesn't touch the files in
`/etc/pcrlock.d`.

Updates applied outside the running system (Windows, a firmware update that
resets the keys) can't be predicted and need a recovery unlock too. So does
a new option ROM, e.g. a different KVM attached to a server: it adds an
authority record to PCR 7 (and changes PCR 2).

**SbatLevel.** shim measures its `SbatLevel` variable into PCR 7, after it
may have raised it on startup. `Type=secureboot-authority` with `Path=` set
to the shim binary predicts that: it reads the shim's `.sbatlevel` section,
picks the payload shim will use (the automatic one, or the latest if
`mokutil --set-sbat-policy latest` is pending) and keeps the current level if
that's newer, as shim does. If the level changes, the SbatLevel record is
swapped in a copy of the current boot's authorities, and the current variant
is kept as `-previous`. The pacman hook runs this when the shim package
changes. Remove `/var/lib/pcrlock.d/620-secureboot-authority.pcrlock.d` and
disable `systemd-pcrlock-secureboot-authority.service` first.

**sbctl and fwupd.** sbctl can't enroll a dbx
([Foxboron/sbctl#23](https://github.com/Foxboron/sbctl/issues/23)), so after
`sbctl enroll-keys` the dbx variable doesn't exist at all (firmware measures
it as empty). fwupd before 2.1.9 then showed the UEFI dbx device without a
version and offered no dbx update
([fwupd/fwupd#11057](https://github.com/fwupd/fwupd/issues/11057), fixed in
[997c7e1](https://github.com/fwupd/fwupd/commit/997c7e132b25c23e37b557ff6e65ec17dee65d31)).
With 2.1.9 the update shows up. The first one creates the variable.

**Firmware resets.** systemd-boot can re-enroll your keys from `.auth` files
in `loader/keys/auto/` on the ESP (`secure-boot-enroll` in `loader.conf`).
It only does so in Setup Mode: a reset to the vendor's factory keys needs
the keys cleared in the firmware setup first. If the enrolled variables match
the old ones byte for byte, PCR 7 comes out the same. A `dbx.auth` there is a
snapshot, though, so keep it in sync with every dbx update, or a re-enrollment
rolls dbx back and changes PCR 7.
