"""Builders for the binary structures the tests need: UEFI variables as
efivarfs stores them, the UEFI_VARIABLE_DATA that variable events hash, event
log records, and a minimal PE file with a .sbatlevel section."""

import hashlib
import struct
import uuid

EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
IMAGE_SECURITY_DB = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"
SHIM_LOCK = "605dab50-e046-4300-abb6-3dd810dd8b23"
FWUPD = "0abba7dc-e516-4167-bbf5-4d9d1c739416"
CERT_SHA256 = "c1c41626-504c-4092-aca9-41f936934328"

# The variables lock-secureboot-policy measures, in order.
SECUREBOOT_POLICY_VARS = [
    ("SecureBoot", EFI_GLOBAL),
    ("PK", EFI_GLOBAL),
    ("KEK", EFI_GLOBAL),
    ("db", IMAGE_SECURITY_DB),
    ("dbx", IMAGE_SECURITY_DB),
]

NV_BS_RT = 0x7


def guid_bytes(g):
    return uuid.UUID(g).bytes_le


def var_event(guid, name, data):
    """UEFI_VARIABLE_DATA: GUID, u64 name length (characters), u64 data
    length, UTF-16LE name without NUL, data."""
    return (guid_bytes(guid) + struct.pack("<QQ", len(name), len(data))
            + name.encode("utf-16-le") + data)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def esl_sha256(owner, *hashes):
    """One EFI_SIGNATURE_LIST of SHA-256 entries."""
    entries = b"".join(guid_bytes(owner) + h for h in hashes)
    return (guid_bytes(CERT_SHA256)
            + struct.pack("<III", 28 + len(entries), 0, 16 + 32) + entries)


def pcclient(pcr, event_type, data):
    return {"pcr": pcr,
            "digests": [{"hashAlg": "sha256", "digest": sha256(data)}],
            "content_type": "pcclient_std",
            "content": {"event_type": event_type, "event_data": data.hex()}}


def systemd_record(pcr, event_type, string, digest=None):
    return {"pcr": pcr,
            "digests": [{"hashAlg": "sha256",
                         "digest": digest or sha256(string.encode())}],
            "content_type": "systemd",
            "content": {"string": string, "eventType": event_type}}


def pe_with_sbatlevel(automatic, latest):
    """A minimal PE32+ file whose only section is .sbatlevel. The name is
    longer than 8 bytes, so the section table holds "/4", an offset into the
    COFF string table after the (empty) symbol table, as in real shim builds.
    .sbatlevel: u32 format version 0, then u32 offsets (relative to the
    offset table at byte 4) of the NUL-terminated automatic and latest
    payloads."""
    a = automatic.encode() + b"\0"
    lat = latest.encode() + b"\0"
    section = struct.pack("<III", 0, 8, 8 + len(a)) + a + lat

    pe_off = 0x40
    coff_size = 20
    opt_size = 0
    sec_table = pe_off + 4 + coff_size + opt_size
    data_off = 0x200
    strtab_off = data_off + len(section)
    strtab = b".sbatlevel\0"
    strtab = struct.pack("<I", 4 + len(strtab)) + strtab

    out = bytearray(data_off)
    out[0:2] = b"MZ"
    struct.pack_into("<I", out, 0x3C, pe_off)
    out[pe_off:pe_off + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", out, pe_off + 4,
                     0x8664, 1, 0, strtab_off, 0, opt_size, 0x22)
    name = b"/4".ljust(8, b"\0")
    struct.pack_into("<8sIIIIIIHHI", out, sec_table, name, len(section),
                     0x1000, len(section), data_off, 0, 0, 0, 0, 0x40000040)
    return bytes(out) + section + strtab
