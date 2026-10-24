#!/bin/bash
# dracut module: set and lock MOR (memory overwrite request) in the initrd and
# measure its state into PCR 15, before disks are unlocked.
#
# The script ships in /usr/bin and the unit as a regular vendor unit in
# $systemdsystemunitdir. Host customisations (full overrides, drop-ins, masks)
# from $systemdsystemconfdir are copied into hostonly images.

# Variables such as $initdir, $dracutsysrootdir and $systemdsystemunitdir
# are provided by dracut when it sources this file.
# shellcheck disable=SC2154
morlock_unit=mor-lock.service

check() {
    require_binaries /usr/bin/mor-lock /usr/lib/systemd/systemd-pcrextend chattr || return 1
    [[ -f "$dracutsysrootdir$systemdsystemunitdir/$morlock_unit" ]] || return 1
    # Only include when explicitly requested (add_dracutmodules+=" morlock ")
    return 255
}

depends() {
    echo systemd tpm2-tss
}

install() {
    inst_script /usr/bin/mor-lock
    inst_multiple \
        /usr/lib/systemd/systemd-pcrextend \
        chattr od \
        "$systemdsystemunitdir/$morlock_unit"

    $SYSTEMCTL -q --root "$initdir" add-wants initrd.target "$morlock_unit"

    # Install the host's local configuration for the unit if enabled.
    # A mask (symlink to /dev/null) in $systemdsystemconfdir is carried over too.
    if [[ $hostonly ]]; then
        inst_multiple -H -o \
            "$systemdsystemconfdir/$morlock_unit" \
            "$systemdsystemconfdir/$morlock_unit.d/*.conf"
    fi
}
