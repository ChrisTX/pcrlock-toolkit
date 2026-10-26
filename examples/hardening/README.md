# Hardening examples

General system hardening for the same kind of machine pcrlock-toolkit targets:
Arch Linux, a signed UKI built by kernel-install, TPM2-unlocked LUKS root,
dracut initrd. None of this is needed for pcrlock and the package installs
none of it. Copy what fits and review each setting first: several of them
break hardware or software that some systems need.

| File | Install as | What it does |
|---|---|---|
| `kernel/cmdline` | `/etc/kernel/cmdline` | kernel command line embedded in the UKI |
| `sysctl.d/99-hardening.conf` | `/etc/sysctl.d/99-hardening.conf` | file system, sysrq, BPF and io_uring restrictions |
| `sysctl.d/99-network-security.conf` | `/etc/sysctl.d/99-network-security.conf` | strict reverse path filtering, no ICMP redirects |
| `sysusers.d/io_uring.conf` | `/etc/sysusers.d/io_uring.conf` | `io_uring` group for the io_uring sysctl |
| `modprobe.d/blacklist-rare.conf` | `/etc/modprobe.d/blacklist-rare.conf` | block rarely used protocols and file systems |
| `system/system.conf.d/syscall-arch.conf` | `/etc/systemd/system.conf.d/syscall-arch.conf` | no x32 system calls for services |
| `dracut.conf.d/pstore.conf` | `/etc/dracut.conf.d/pstore.conf` | `systemd-pstore` in the initrd |

Afterwards, run `kernel-install add-all` (or reinstall the kernel package) to
rebuild the UKI with the new command line and initrd, then `systemd-sysusers`
and `systemd-sysctl` (or reboot).

## Kernel command line (`kernel/cmdline`)

kernel-install reads `/etc/kernel/cmdline` and ukify embeds it in the UKI's
`.cmdline` section. Lines starting with `#` are ignored, so the file can carry
comments. Because the UKI is signed, the command line can't be edited at boot.
Rebuilding the UKI also re-locks it through `99-pcrlock.install`.

### Unlock and root device

- `rootflags=x-systemd.device-timeout=0`: wait for the root device forever
  instead of dropping to emergency mode.
- The passphrase/PIN prompt never times out without any option: crypttab's
  `timeout=` defaults to 0, which waits indefinitely. An earlier version set
  `rd.luks.options=timeout=0`, which had no effect: options without a UUID
  only apply to volumes that have neither a crypttab entry nor their own
  `rd.luks.options=`, and the auto-discovered root ignores them (see below).

#### Options for the auto-discovered root (crypttab override)

`systemd-gpt-auto-generator` sets up the discovered root with fixed options
and reads none from the command line: `tpm2-device=auto`, plus
`tpm2-measure-pcr=yes,tpm2-measure-keyslot-nvpcr=yes` when booted from a
measured UKI. It writes its `systemd-cryptsetup@root.service` to the *late*
generator directory, which has the lowest priority, so a crypttab entry
named `root` replaces it entirely. That's the way to add options such as
`fixate-volume-key=` while auto-discovery still picks the device (the root
partition on the disk the boot loader came from, checked against the image
policy):

```text
root  /dev/disk/by-designator/root-luks  -  tpm2-device=auto,tpm2-measure-pcr=yes,tpm2-measure-keyslot-nvpcr=yes,fixate-volume-key=<digest>,x-initrd.attach
```

- The device must be the one the discovery path uses: with
  `root=dissect`/`root=dissect-force` that's
  `/dev/disk/by-designator/root-luks`; with `root=gpt-auto` or no `root=`
  it's `/dev/gpt-auto-root-luks`.
- `x-initrd.attach` puts the entry into the initrd's crypttab.
- The name must stay `root`, or both units run.
- `<digest>` is the sha256 digest of the volume-key record in PCR 15,
  which `pcrlock volume-keys` prints.
- Whether the image policy is enforced where udev's dissection creates the
  `by-designator` links hasn't been confirmed; check it on the test boot.

Naming the volume by `UUID=` instead isn't weaker once `fixate-volume-key=`
is set. The pinned digest is HMAC(volume key,
`cryptsetup:root:<LUKS UUID>`), so a substituted partition with the same
UUID but another volume key is refused, and nobody can create a LUKS volume
with the volume key without knowing it. The binding then only decides which
disk is used when there are duplicates, a question of availability. Neither
binding prevents rolling back to an older copy of the same volume; that
takes something like dm-verity or IMA, which protect the contents rather
than the key.

### systemd lockdown

- `root=dissect-force`: discover root from the GPT with full dissection, so
  the image policy below applies to root too. Without `root=` (or with
  `root=gpt-auto`), root is found through the ESP the boot loader reported,
  and the policy only covers the other partition types. `-force` activates
  root without waiting for a factory reset phase (there is none with
  `systemd.factory_reset=0`). `rootflags=` still applies.
  **Test-boot this with a fallback boot entry first:** if the policy doesn't
  match the root partition, root is never found, and with
  `x-systemd.device-timeout=0` the boot waits forever instead of failing.
  With an explicit `root` entry in `/etc/crypttab` next to auto-discovery,
  also check afterwards that the crypttab options are still the ones in
  effect (`systemctl cat systemd-cryptsetup@root.service`) and that the
  volume-key record still shows up in PCR 15 (`pcrlock volume-keys`).
- `mount.usr=off`: no separate `/usr` file system, neither discovered from
  the GPT nor taken from the root's fstab. Discovery is off by default; this
  makes it explicit.
- `systemd.image_policy=root=encrypted+read-only-off:esp=unprotected+vfat:=absent+unused`:
  restricts what `systemd-gpt-auto-generator` accepts from the GPT. Root must
  be encrypted, the ESP must be unprotected vfat, and every other partition
  type must be absent or unused. Partitions such as `/home`, `/srv` or swap
  are then never auto-mounted from the partition table; put them in
  `/etc/fstab` or `/etc/crypttab`.
- `systemd.factory_reset=0`: never boot into factory reset mode
  (`systemd-factory-reset-generator`, `systemd-repart`).
- `systemd.tpm2_allow_clear=0`: `systemd-tpm2-clear.service` never asks the
  firmware to clear the TPM. Factory reset is already off; this guards the
  TPM itself, whose clearing would also destroy every key held in it.
- `systemd.firstboot=off` with
  `systemd.mask=systemd-firstboot.service systemd.mask=systemd-homed-firstboot.service`:
  no first-boot prompts on the console, which could set a root password or
  create a homed user. `systemd.condition_first_boot=on` would otherwise
  start them on an installed system. `systemd.firstboot=off` alone could be
  overridden by a later `systemd.firstboot=on` (last occurrence wins), the
  masks can't. `first-boot-complete.target` is left alone; it only orders
  first-boot units.

### Boot inputs from outside the UKI

- `rd.systemd.sysext=0 rd.systemd.confext=0`: no system or configuration
  extensions are merged in the initrd. systemd-stub picks them up from the
  ESP, which isn't encrypted, and a confext could change the initrd's `/etc`
  before the root unlock. PCR 13 would then no longer match, but a user
  could still type the recovery key into the modified initrd. Only for
  setups that don't use initrd extensions.
- `systemd.credentials_boot_policy=strict` (systemd 262): never accept
  credentials encrypted with the "null" key, which offer neither
  confidentiality nor authenticity. The default, `relaxed`, accepts them
  when Secure Boot is off or no TPM is found, e.g. after the fTPM was
  disabled in firmware setup. Credentials bound to the host key or the TPM,
  such as the pcrlock policy on the ESP, aren't affected; check one boot
  afterwards anyway.
- Not set: `systemd.import_credentials=0`. It also stops credentials passed
  in by systemd-stub, including the pcrlock policy from
  `/loader/credentials/` that the initrd needs to unlock root.

### LSMs, lockdown and module signatures

- `lsm=landlock,lockdown,yama,integrity,apparmor,bpf`: the full LSM list,
  with AppArmor enabled. This **replaces** the kernel's default list; any LSM
  not named is off.
- `apparmor=1`: AppArmor on, matching the list above.
- `lockdown=integrity`: blocks userspace from modifying the running kernel
  (unsigned modules, `/dev/mem`, unsigned kexec, …). `confidentiality` blocks
  any facility to read kernel memory on top. It will disable Kprobes,
  and XFRM secrets cannot be read back to userspace, which breaks MOBIKE with
  IPsec IKEv2.
- `module.sig_enforce=1`: only load signed modules, whatever the lockdown
  state. DKMS modules must be signed with a key the kernel trusts, e.g. a MOK
  via `sign-file-tpm2` and `/etc/dkms/framework.conf.d/mok-signing.conf`.

### IOMMU

- `iommu.passthrough=0`: translate DMA (no identity default domain).
- `iommu.strict=1`: invalidate the IOTLB synchronously on unmap. Groups become
  `DMA` instead of `DMA-FQ`, at some I/O cost.
- `amd_iommu=force_isolation,pgtbl_v2`: keep every device isolated even where
  the driver would relax it, and use the v2 page table format.
- `intel_iommu=on`: the same intent on Intel.

### Kernel self-protection (see KSPP, kernel-hardening-checker)

- `bdev_allow_write_mounted=0`: no writes to block devices that are mounted.
- `cfi=kcfi`: kernel control-flow integrity mode. Only takes effect on kernels
  built with CFI support. Currently this needs a Clang build kernel,
  but the feature is expected to land in GCC 17. Does not harm enabling.
- `debugfs=off`: no debugfs.
- `efi=disable_early_pci_dma`: the EFI stub clears bus mastering on PCI
  bridges, closing a DMA window before the IOMMU is up.
- `hardened_usercopy=1`, `slab_nomerge`, `page_alloc.shuffle=1`,
  `randomize_kstack_offset=1`: harder heap and stack exploitation.
- `init_on_alloc=1`: zero memory on allocation. Usually less than 1%
  performance impact.
- `hash_pointers=always`: always hash `%p` pointers in kernel messages.
- `proc_mem.force_override=never`: `/proc/<pid>/mem` can't write through
  page protections.
- `vsyscall=none`: no legacy vsyscall page. Breaks only very old static
  binaries.

## Choices with performance impact

- `init_on_free=1`: zero memory on free. Performance impact of 3-5% in usual workloads.
- `slub_debug=ZF`: `Z` changes the SLUB allocator to perform redzoning
and `F` adds sanity checks. Severe performance impact in usual workloads.

### Early sysctls

`sysctl.<key>=<value>` is applied by the kernel before PID 1 starts, so it
covers the initrd as well, and it's part of the signed and measured UKI. Only
keys that systemd's own `sysctl.d` files don't set belong here, since
`systemd-sysctl` would overwrite them later. The rest are in `sysctl.d/`.

- `kernel.kexec_load_disabled=1`, `kernel.unprivileged_bpf_disabled=1`:
  one-way; they can't be undone until reboot.
- `vm.mmap_rnd_bits=32`, `vm.mmap_rnd_compat_bits=16`: maximum ASLR entropy
  on x86-64, including the initrd and PID 1.
- `kernel.kptr_restrict=2`, `kernel.dmesg_restrict=1`,
  `kernel.perf_event_paranoid=2`: no kernel pointers, dmesg or kernel
  profiling for unprivileged users.
- `kernel.yama.ptrace_scope=2`: only `CAP_SYS_PTRACE` may ptrace. Debuggers
  need root.
- `dev.tty.ldisc_autoload=0`, `dev.tty.legacy_tiocsti=0`: no line-discipline
  autoloading, no `TIOCSTI` input injection.
- `kernel.oops_limit=1`, `kernel.warn_limit=100`: panic after the first oops
  or the 100th warning, so a half-broken kernel doesn't keep running.

### Crash handling and audit

- `efi_pstore.pstore_disable=0`: use EFI variables as the pstore backend, so
  the oops behind a panic survives the reboot. `systemd-pstore.service` moves
  it to `/var/lib/systemd/pstore/` on the next boot (see `dracut.conf.d/pstore.conf`).
- `panic=10`: reboot 10 seconds after a panic instead of hanging.
- `systemd.crash_action=reboot`: the same when PID 1 crashes (default:
  freeze), so decrypted memory doesn't stay in RAM indefinitely. With
  `mor-lock`'s MOR ClearMemory set, the firmware wipes memory on the way
  back up.
- `audit=1 audit_backlog_limit=8192`: audit from early boot, with a backlog
  large enough not to lose boot events.

### Initrd

- `rd.shell=0 rd.emergency=reboot`: no initrd shell; reboot instead of
  entering emergency mode. An emergency shell would sit in front of the
  unlocked root, which is why `initrd-pcr15-seal.service` also refuses to
  fall back to it.
- `rd.systemd.mask=debug-shell.service systemd.mask=debug-shell.service`: no
  debug shell in the initrd or on the host, whether requested with
  `rd.systemd.debug_shell`/`systemd.debug_shell` or enabled persistently.
  Most options take their last occurrence, so a parameter appended later
  (e.g. by a command line addon on the ESP, measured into PCR 12) would win;
  a mask can't be undone that way. `emergency.target` isn't masked: failures
  would then hang instead of following `rd.emergency=reboot`, and on the host
  `emergency.service` runs sulogin, which refuses without a usable root
  password.
- `quiet splash`: plain boot screen.

## sysctl (`sysctl.d/`)

The files are named `99-*` so they sort after systemd's `50-default.conf` and
`50-coredump.conf`, which would otherwise reset some of the values.

`99-hardening.conf`:

- `fs.protected_fifos=2`, `fs.protected_regular=2`, `fs.protected_hardlinks=1`,
  `fs.protected_symlinks=1`: stricter than systemd's defaults for FIFOs and
  regular files in sticky world-writable directories.
- `kernel.sysrq=0`: no magic SysRq keys (systemd sets 16, sync only).
- `net.core.bpf_jit_harden=2`: constant blinding for all BPF JIT code.
- `kernel.io_uring_disabled=1`, `kernel.io_uring_group=900`: only
  `CAP_SYS_ADMIN` and members of GID 900 may create io_uring instances. Most
  programs fall back to other I/O; those that require io_uring need their user
  in the group. Keep the GID in sync with `sysusers.d/io_uring.conf`.
- `fs.suid_dumpable` is left at systemd's 2: privileged crashes go to
  `systemd-coredump` and are stored root-only.

`99-network-security.conf`:

- `net.ipv4.conf.*.rp_filter=1`: strict reverse path filtering. The glob is
  needed because systemd sets `rp_filter=2` per interface and the kernel uses
  the maximum of `all` and the interface value. Strict filtering can drop
  packets on multi-homed or roaming setups (test IKEv2/MOBIKE); use 2 if it
  does.
- No accepting, no "secure" accepting, and no sending of ICMP redirects, for
  IPv4 and IPv6 (`all` and `default`).
- `net.ipv4.tcp_rfc1337=1`: drop RST packets for sockets in TIME-WAIT.

## io_uring group (`sysusers.d/io_uring.conf`)

Creates the `io_uring` group with the fixed GID 900 the sysctl refers to,
since `kernel.io_uring_group` takes a number. Pick another GID if 900 is
taken, and replace `<user>` with the users that need io_uring (one `m` line
each). Group membership applies from the next login.

## Module blocklist (`modprobe.d/blacklist-rare.conf`)

`install <module> /bin/false` makes every `modprobe` of the module fail. A plain
`blacklist` line only stops autoloading by alias, so an unprivileged socket
call could still pull the module in. Blocked:

- Network protocols with a history of bugs and few users: `sctp`, `rds`,
  `tipc`, `atm`, `can`, `ieee802154`, `6lowpan`, `af_rxrpc`.
- `n_hdlc` (TTY line discipline).
- Legacy or exotic file systems: `cramfs`, `jffs2`, `hfs`, `hfsplus`, `affs`,
  `befs`, `omfs`, `minix`, `orangefs`, `coda`.

Remove `can` for CAN bus hardware and `hfsplus` to read Mac-formatted drives.

## System call architectures (`system/system.conf.d/syscall-arch.conf`)

`SystemCallArchitectures=x86-64 x86` becomes the default for every service:
native and 32-bit x86 system calls are allowed, the x32 ABI is not. Use
`native` instead to also block 32-bit system calls, if no service needs them.
This applies to system services only, not to user sessions.

## pstore in the initrd (`dracut.conf.d/pstore.conf`)

Adds dracut's `systemd-pstore` module. Together with
`efi_pstore.pstore_disable=0`, `panic=10` and `kernel.oops_limit=1` on the
command line, a crash leaves its log in EFI variables, the machine reboots,
and `systemd-pstore` archives the log on the next boot.
