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

SCRIPTS = bin/pcrlock bin/platsec-pcr-check libexec/sign-file-tpm2 \
	kernel-install/99-pcrlock.install \
	dracut/91platsec/module-setup.sh dracut/91pcr15seal/module-setup.sh

.PHONY: all check install

all:

check:
	bash -n bin/pcrlock
	sh -n bin/platsec-pcr-check
	sh -n libexec/sign-file-tpm2
	sh -n kernel-install/99-pcrlock.install
	bash -n dracut/91platsec/module-setup.sh
	bash -n dracut/91pcr15seal/module-setup.sh
	if command -v shellcheck >/dev/null; then shellcheck $(SCRIPTS); fi

install:
	install -Dm755 bin/pcrlock                         $(DESTDIR)$(BINDIR)/pcrlock
	install -Dm755 bin/platsec-pcr-check               $(DESTDIR)$(BINDIR)/platsec-pcr-check
	install -Dm755 libexec/sign-file-tpm2              $(DESTDIR)$(LIBDIR)/sign-file-tpm2
	install -Dm755 kernel-install/99-pcrlock.install   $(DESTDIR)$(KINSTALLDIR)/99-pcrlock.install
	install -Dm644 hooks/25-pcrlock-pe.hook            $(DESTDIR)$(ALPMHOOKDIR)/25-pcrlock-pe.hook
	install -Dm644 units/platsec-pcr-check.service     $(DESTDIR)$(UNITDIR)/platsec-pcr-check.service
	install -Dm644 units/initrd-pcr15-seal.service     $(DESTDIR)$(UNITDIR)/initrd-pcr15-seal.service
	install -Dm644 units/pcrlock-prune.service         $(DESTDIR)$(UNITDIR)/pcrlock-prune.service
	install -Dm755 dracut/91platsec/module-setup.sh    $(DESTDIR)$(DRACUTDIR)/91platsec/module-setup.sh
	install -Dm755 dracut/91pcr15seal/module-setup.sh  $(DESTDIR)$(DRACUTDIR)/91pcr15seal/module-setup.sh
	install -Dm644 conf/pcrlock.conf                   $(DESTDIR)$(CONFDIR)/pcrlock.conf
	install -Dm644 conf/platsec.conf                   $(DESTDIR)$(CONFDIR)/platsec.conf
	install -Dm644 conf/openssl-tpm2.cnf               $(DESTDIR)$(CONFDIR)/openssl-tpm2.cnf
	install -dm755                                     $(DESTDIR)$(CONFDIR)/components.d
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/components.d examples/components.d/*.conf
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/dracut.conf.d examples/dracut.conf.d/*.conf
	install -Dm644 -t $(DESTDIR)$(DATADIR)/examples/hooks examples/hooks/*.hook
	install -Dm644 README.md                           $(DESTDIR)$(DOCDIR)/README.md
