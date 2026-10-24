# pcrlock-toolkit

Tools around [systemd-pcrlock](https://www.freedesktop.org/software/systemd/man/latest/systemd-pcrlock.html)
for a TPM2-unlocked system:

- **`pcrlock`**: generates and maintains `.pcrlock` components from a
  declarative configuration, locks UKIs from kernel-install, and prunes stale
  variants.
- **`platsec-pcr-check`** + dracut module `platsec`: checks the platform
  security baseline (memory encryption, IOMMU, pre-boot DMA protection) in the
  initrd, measures it, and powers off if it isn't met.
- **`initrd-pcr15-seal.service`** + dracut module `pcr15seal`: extends a
  separator into PCR 15 at the end of the initrd.
- **`sign-file-tpm2`**: DKMS module signing with a TPM2-held key, enabled by
  `/etc/dkms/framework.conf.d/mok-signing.conf`. Adjust the key paths there.

## Layout

| Path | Purpose |
|---|---|
| `/usr/bin/pcrlock` | component generator CLI |
| `/usr/bin/platsec-pcr-check` | baseline check (initrd and `--status` on the host) |
| `/etc/pcrlock-toolkit/pcrlock.conf` | global settings for `pcrlock` |
| `/etc/pcrlock-toolkit/components.d/*.conf` | one file per component |
| `/etc/pcrlock-toolkit/platsec.conf` | platsec settings (copied into the initrd) |
| `/etc/pcrlock-toolkit/openssl-tpm2.cnf` | OpenSSL config used by `sign-file-tpm2` |
| `/usr/lib/kernel/install.d/99-pcrlock.install` | locks UKIs on kernel install/removal |
| `/usr/share/libalpm/hooks/25-pcrlock-pe.hook` | re-locks boot loaders on package updates |
| `pcrlock-prune.service` | runs `pcrlock prune` early at boot, before make-policy (opt-in) |
| `/usr/lib/dracut/modules.d/91platsec`, `91pcr15seal` | initrd modules (opt-in) |
| `/etc/dkms/framework.conf.d/mok-signing.conf` | makes DKMS sign modules with `sign-file-tpm2` |
| `/usr/share/pcrlock-toolkit/examples/` | example components, dracut configs and pacman hooks |

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
pcrlock uki add ENTRY UKI [KERNEL]
pcrlock uki remove ENTRY
```

Options: `--type=TYPE`, `--policy=now|mark|none`, `--dry-run`, `-v`.
After any change the policy is updated by restarting
`systemd-pcrlock-make-policy.service` (`now`), marking it for pacman's
restart-marked hook (`mark`), or not at all (`none`). Put make-policy options
such as `--location=` into a drop-in for that service.

### Boot loader updates and pruning

`25-pcrlock-pe.hook` runs `pcrlock generate --type=pe --policy=mark` when the
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
`-previous`. `pcrlock-prune.service` removes it on the next boot once it no
longer matches, before `systemd-pcrlock-make-policy.service` rebuilds the
policy:

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

Common keys: `Enabled=`, `Output=` (path relative to `/etc/pcrlock.d`),
`KeepPrevious=`, `MinRecords=` (default 1).

A failing component (for example a volume not unlocked via TPM this boot)
keeps its existing file; the others still run.
