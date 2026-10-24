#!/bin/bash
# dracut module: check and measure the platform memory protection baseline
# (memory encryption, IOMMU, pre-boot DMA) into the TPM before disks are unlocked.
#
# The script ships in /usr/bin and the unit as a regular vendor unit in
# $systemdsystemunitdir. Host customisations (full overrides, drop-ins, masks)
# from $systemdsystemconfdir are copied into hostonly images.

# Variables such as $initdir, $dracutsysrootdir and $systemdsystemunitdir
# are provided by dracut when it sources this file.
# shellcheck disable=SC2154
platsec_unit=platsec-pcr-check.service

check() {
    require_binaries /usr/bin/platsec-pcr-check /usr/lib/systemd/systemd-pcrextend || return 1
    [[ -f "$dracutsysrootdir$systemdsystemunitdir/$platsec_unit" ]] || return 1
    # Only include when explicitly requested (add_dracutmodules+=" platsec ")
    return 255
}

depends() {
    echo systemd tpm2-tss
}

installkernel() {
    # ccp: AMD TSME status; msr: Intel IA32_TME_ACTIVATE
    instmods ccp msr
}

install() {
    inst_script /usr/bin/platsec-pcr-check
    inst_multiple \
        /usr/lib/systemd/systemd-pcrextend \
        cat sleep modprobe grep dd od tr \
        "$systemdsystemunitdir/$platsec_unit"

    $SYSTEMCTL -q --root "$initdir" add-wants initrd.target "$platsec_unit"

    # The config is the security policy the pcrlock component was generated
    # from, so it goes into every image, not only hostonly ones (and is not
    # tagged -H, so rd.hostonly=0 can't strip it).
    inst_multiple -o /etc/pcrlock-toolkit/platsec.conf

    # Install the host's local configuration for the unit if enabled.
    # A mask (symlink to /dev/null) in $systemdsystemconfdir is carried over too.
    if [[ $hostonly ]]; then
        inst_multiple -H -o \
            "$systemdsystemconfdir/$platsec_unit" \
            "$systemdsystemconfdir/$platsec_unit.d/*.conf"
    fi
}
