# Maintainer: cpfeiffer <cpfeiffer@live.de>
#
# Development PKGBUILD: builds straight from this working tree (run makepkg
# in the repository root). For publishing, switch source=() to a git tag or
# release tarball and replace "$startdir" with "$srcdir/$pkgname-$pkgver".
# shellcheck disable=SC2034,SC2154
# shellcheck shell=bash

pkgname=pcrlock-toolkit
pkgver=0.1.0
pkgrel=1
pkgdesc="Tools around systemd-pcrlock: declarative component generation, UKI locking, initrd platform security baseline and PCR 15 initrd seal"
arch=('any')
license=('0BSD')
depends=(
  'bash'
  'systemd' # systemd-pcrlock, systemd-pcrextend, varlinkctl
  'jq'
  'coreutils'
  'diffutils' # cmp
  'grep'
  'kmod' # modprobe in the initrd
  'tpm2-tss'
  'python' # pcrlock decode
)
optdepends=(
  'dracut: initrd modules platsec and pcr15seal'
  'tpm2-openssl: TPM2-backed DKMS module signing via sign-file-tpm2'
  'openssl: certificate details in pcrlock decode'
)
checkdepends=('dash' 'python' 'python-pytest') # make check: dash -n, Python syntax; make test: pytest
backup=(
  'etc/pcrlock-toolkit/pcrlock.conf'
  'etc/pcrlock-toolkit/platsec.conf'
  'etc/pcrlock-toolkit/openssl-tpm2.cnf'
)
install=pcrlock-toolkit.install

check() {
  make -C "$startdir" check test
}

package() {
  make -C "$startdir" DESTDIR="$pkgdir" install
  install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
