#!/bin/bash
# dracut module: seal the initrd phase by extending a separator into PCR 15
# via the io.systemd.PCRExtend Varlink interface.
#
# The unit itself ships as a regular vendor unit in $systemdsystemunitdir.
# Host customisations (full overrides, drop-ins, masks) from
# $systemdsystemconfdir are copied into hostonly images.

# Variables such as $initdir, $dracutsysrootdir and $systemdsystemunitdir
# are provided by dracut when it sources this file.
# shellcheck disable=SC2154
pcr15seal_unit=initrd-pcr15-seal.service

check() {
    require_binaries varlinkctl || return 1
    [[ -f "$dracutsysrootdir$systemdsystemunitdir/$pcr15seal_unit" ]] || return 1
    # Only include when explicitly requested (add_dracutmodules+=" pcr15seal ")
    return 255
}

depends() {
    # systemd-pcrextend provides systemd-pcrextend.socket and the
    # systemd-pcrextend@.service template the Varlink call activates.
    echo systemd systemd-pcrextend tpm2-tss
}

install() {
    inst_multiple \
        varlinkctl \
        "$systemdsystemunitdir/$pcr15seal_unit"

    $SYSTEMCTL -q --root "$initdir" add-wants initrd.target "$pcr15seal_unit"

    # Install the host's local configuration for the unit if enabled.
    # A mask (symlink to /dev/null) in $systemdsystemconfdir is carried over too.
    if [[ $hostonly ]]; then
        inst_multiple -H -o \
            "$systemdsystemconfdir/$pcr15seal_unit" \
            "$systemdsystemconfdir/$pcr15seal_unit.d/*.conf"
    fi
}
