# pcrlock-toolkit
PREFIX     ?= /usr
SYSCONFDIR ?= /etc
DESTDIR    ?=

BINDIR      = $(PREFIX)/bin
LIBDIR      = $(PREFIX)/lib/pcrlock-toolkit
UNITDIR     = $(PREFIX)/lib/systemd/system
DRACUTDIR   = $(PREFIX)/lib/dracut/modules.d
KINSTALLDIR = $(PREFIX)/lib/kernel/install.d
ALPMHOOKDIR = $(PREFIX)/share/libalpm/hooks
DATADIR     = $(PREFIX)/share/pcrlock-toolkit
DOCDIR      = $(PREFIX)/share/doc/pcrlock-toolkit
CONFDIR     = $(SYSCONFDIR)/pcrlock-toolkit

SCRIPTS = bin/pcrlock bin/platsec-pcr-check bin/mor-lock libexec/sign-file-tpm2 \
	kernel-install/99-pcrlock.install \
	dracut/91platsec/module-setup.sh dracut/91pcr15seal/module-setup.sh \
	dracut/91morlock/module-setup.sh
PYTHON = libexec/cel-decode tests/stubs/systemd-pcrlock tests/stubs/systemctl tests/*.py

.PHONY: all check test install

all:

check:
	bash -n bin/pcrlock
	dash -n bin/platsec-pcr-check
	dash -n bin/mor-lock
	dash -n libexec/sign-file-tpm2
	dash -n kernel-install/99-pcrlock.install
	bash -n dracut/91platsec/module-setup.sh
	bash -n dracut/91pcr15seal/module-setup.sh
	bash -n dracut/91morlock/module-setup.sh
	if command -v shellcheck >/dev/null; then shellcheck $(SCRIPTS) tests/stubs/bootctl tests/stubs/platsec-pcr-check; fi
	python3 -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' $(PYTHON)
	if command -v ruff >/dev/null; then ruff check $(PYTHON); fi

# Pins the behaviour of bin/pcrlock against stubs (see tests/conftest.py).
test:
	python3 -m pytest -q tests

install:
	install -Dm755 bin/pcrlock                         $(DESTDIR)$(BINDIR)/pcrlock
	install -Dm755 bin/platsec-pcr-check               $(DESTDIR)$(BINDIR)/platsec-pcr-check
	install -Dm755 bin/mor-lock                        $(DESTDIR)$(BINDIR)/mor-lock
	install -Dm755 libexec/sign-file-tpm2              $(DESTDIR)$(LIBDIR)/sign-file-tpm2
	install -Dm755 libexec/cel-decode                  $(DESTDIR)$(LIBDIR)/cel-decode
	install -Dm755 kernel-install/99-pcrlock.install   $(DESTDIR)$(KINSTALLDIR)/99-pcrlock.install
	install -Dm644 hooks/25-pcrlock-pe.hook            $(DESTDIR)$(ALPMHOOKDIR)/25-pcrlock-pe.hook
	install -Dm644 units/platsec-pcr-check.service     $(DESTDIR)$(UNITDIR)/platsec-pcr-check.service
	install -Dm644 units/initrd-pcr15-seal.service     $(DESTDIR)$(UNITDIR)/initrd-pcr15-seal.service
	install -Dm644 units/pcrlock-prune.service         $(DESTDIR)$(UNITDIR)/pcrlock-prune.service
	install -Dm644 units/mor-lock.service              $(DESTDIR)$(UNITDIR)/mor-lock.service
	install -Dm644 units/pcrlock-secureboot-policy.path  $(DESTDIR)$(UNITDIR)/pcrlock-secureboot-policy.path
	install -Dm644 units/pcrlock-secureboot-policy.service $(DESTDIR)$(UNITDIR)/pcrlock-secureboot-policy.service
	install -Dm644 units/pcrlock-firmware-relock.service $(DESTDIR)$(UNITDIR)/pcrlock-firmware-relock.service
	install -Dm644 units/pcrlock-firmware-suspend.path $(DESTDIR)$(UNITDIR)/pcrlock-firmware-suspend.path
	install -Dm644 units/pcrlock-firmware-suspend.service $(DESTDIR)$(UNITDIR)/pcrlock-firmware-suspend.service
	install -Dm755 dracut/91platsec/module-setup.sh    $(DESTDIR)$(DRACUTDIR)/91platsec/module-setup.sh
	install -Dm755 dracut/91pcr15seal/module-setup.sh  $(DESTDIR)$(DRACUTDIR)/91pcr15seal/module-setup.sh
	install -Dm755 dracut/91morlock/module-setup.sh    $(DESTDIR)$(DRACUTDIR)/91morlock/module-setup.sh
	install -dm755 $(DESTDIR)$(LIBDIR)/mkosi-initrd/platsec/usr/lib/systemd/system/initrd.target.wants
	ln -sf ../platsec-pcr-check.service $(DESTDIR)$(LIBDIR)/mkosi-initrd/platsec/usr/lib/systemd/system/initrd.target.wants/platsec-pcr-check.service
	install -dm755 $(DESTDIR)$(LIBDIR)/mkosi-initrd/pcr15seal/usr/lib/systemd/system/initrd.target.wants
	ln -sf ../initrd-pcr15-seal.service $(DESTDIR)$(LIBDIR)/mkosi-initrd/pcr15seal/usr/lib/systemd/system/initrd.target.wants/initrd-pcr15-seal.service
	install -dm755 $(DESTDIR)$(LIBDIR)/mkosi-initrd/morlock/usr/lib/systemd/system/initrd.target.wants
	ln -sf ../mor-lock.service $(DESTDIR)$(LIBDIR)/mkosi-initrd/morlock/usr/lib/systemd/system/initrd.target.wants/mor-lock.service
	install -Dm644 conf/pcrlock.conf                   $(DESTDIR)$(CONFDIR)/pcrlock.conf
	install -Dm644 conf/platsec.conf                   $(DESTDIR)$(CONFDIR)/platsec.conf
	install -Dm644 conf/openssl-tpm2.cnf               $(DESTDIR)$(CONFDIR)/openssl-tpm2.cnf
	install -dm755                                     $(DESTDIR)$(CONFDIR)/components.d
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/components.d examples/components.d/*.conf
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/dracut.conf.d examples/dracut.conf.d/*.conf
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/hooks examples/hooks/*.hook
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/dkms/framework.conf.d examples/dkms/framework.conf.d/*.conf
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/mkosi-initrd/mkosi.conf.d examples/mkosi-initrd/mkosi.conf.d/*.conf
	install -Dm644 README.md                           $(DESTDIR)$(DOCDIR)/README.md
