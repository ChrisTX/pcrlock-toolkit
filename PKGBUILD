# Maintainer: cpfeiffer <cpfeiffer@live.de>
#
# VCS package: builds the latest commit of the default branch on GitHub, so
# local commits are only included once they're pushed. To build unpushed
# local commits, point source=() at the working copy temporarily:
#   source=("$_pkgname::git+file://$startdir")
# shellcheck disable=SC2034,SC2154
# shellcheck shell=bash

_pkgname=pcrlock-toolkit
pkgname=$_pkgname-git
pkgver=r42.c5727d9
pkgrel=1
pkgdesc="Tools around systemd-pcrlock: declarative component generation, UKI locking, initrd platform security baseline and PCR 15 initrd seal"
arch=('any')
url="https://github.com/ChrisTX/pcrlock-toolkit"
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
makedepends=('git')
optdepends=(
  'dracut: initrd modules platsec and pcr15seal'
  'tpm2-openssl: TPM2-backed DKMS module signing via sign-file-tpm2'
  'openssl: certificate details in pcrlock decode'
)
checkdepends=('dash' 'python' 'python-pytest') # make check: dash -n, Python syntax; make test: pytest
provides=("$_pkgname")
conflicts=("$_pkgname")
backup=(
  'etc/pcrlock-toolkit/pcrlock.conf'
  'etc/pcrlock-toolkit/platsec.conf'
  'etc/pcrlock-toolkit/openssl-tpm2.cnf'
)
install=pcrlock-toolkit.install
source=("$_pkgname::git+$url.git")
sha256sums=('SKIP')

pkgver() {
  cd "$_pkgname" || return
  # r<commits>.<hash> until the first tag, then <tag>.r<commits since>.<hash>.
  (
    set -o pipefail
    git describe --long --abbrev=7 --tags 2>/dev/null |
      sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g' ||
      printf 'r%s.%s' "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
  )
}

check() {
  make -C "$_pkgname" check test
}

package() {
  make -C "$_pkgname" DESTDIR="$pkgdir" install
  install -Dm644 "$_pkgname/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
