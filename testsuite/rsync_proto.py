#!/usr/bin/env python3
"""Minimal pure-Python implementation of the rsync *client/sender* side of the
daemon protocol -- enough to push a (possibly malformed) file list to a real
rsync daemon receiver over a TCP socket.

Why this exists
---------------
Several rsync security fixes are for crashes the receiver hits while parsing a
file list that no cooperating sender would ever produce.  The historical way to
test them was build_patched_rsync(): copy the source, string-replace a line in
the sender, recompile.  That is slow (a full rsync rebuild per test) and brittle
(it breaks when the patched line is refactored).  This module replaces that with
a declarative wire-format speaker: encode a normal file entry, then flip one
field.

Design notes / scope
--------------------
* It is pinned to protocol 30 and advertises NO optional client capabilities
  (no 'i' => no inc_recurse, no 'v' => byte/short flist flags and no string
  negotiation), which keeps the encoding surface small.  Per send_file_entry()
  / setup_protocol() in the C source.  If the on-wire protocol changes, the
  constants and encoders here must be regenerated -- this is deliberately a
  second implementation kept tiny for that reason.
* The integer encoders are byte-for-byte ports of io.c (write_varint /
  write_varlong / read_varint / read_int), so the bytes match the C exactly.
* It is structured as a fuzzing substrate: FileEntry holds each field
  separately and every field (or the whole entry) can be overridden with raw
  bytes, so a future fuzzer can mutate one field at a time.

This is test-only code; it is not built or shipped.
"""

import socket
import struct

# ---------------------------------------------------------------------------
# Protocol constants (mirror rsync.h)
# ---------------------------------------------------------------------------
MPLEX_BASE = 7
MSG_DATA = 0           # FNONE-based data channel
MSG_ERROR_XFER = 1     # FERROR_XFER
MSG_INFO = 2           # FINFO
MSG_ERROR = 3          # FERROR
MSG_WARNING = 4        # FWARNING
MSG_DELETED = 101      # deleted a file on the receiving side
MSG_NO_SEND = 102      # sender failed to open a file we wanted

# XMIT_* file-entry flags (rsync.h)
XMIT_TOP_DIR            = 1 << 0
XMIT_SAME_MODE          = 1 << 1
XMIT_EXTENDED_FLAGS     = 1 << 2
XMIT_SAME_UID           = 1 << 3
XMIT_SAME_GID           = 1 << 4
XMIT_SAME_NAME          = 1 << 5
XMIT_LONG_NAME          = 1 << 6
XMIT_SAME_TIME          = 1 << 7
XMIT_SAME_RDEV_MAJOR    = 1 << 8
XMIT_NO_CONTENT_DIR     = 1 << 8
XMIT_HLINKED            = 1 << 9
XMIT_USER_NAME_FOLLOWS  = 1 << 10
XMIT_GROUP_NAME_FOLLOWS = 1 << 11
XMIT_HLINK_FIRST        = 1 << 12
XMIT_MOD_NSEC           = 1 << 13

# from sys/stat.h
S_IFREG = 0o100000
S_IFDIR = 0o040000
S_IFLNK = 0o120000
S_IFMT = 0o170000


def S_ISREG(m):
    return (m & S_IFMT) == S_IFREG


def S_ISDIR(m):
    return (m & S_IFMT) == S_IFDIR


def S_ISLNK(m):
    return (m & S_IFMT) == S_IFLNK


DEFAULT_PROTOCOL = 30

NDX_DONE = -1
NDX_FLIST_EOF = -2
NDX_FLIST_OFFSET = -101

# Transfer-phase iflags (rsync.h) and basis-type tags.
ITEM_BASIS_TYPE_FOLLOWS = 1 << 11
ITEM_XNAME_FOLLOWS      = 1 << 12
ITEM_IS_NEW             = 1 << 13
ITEM_TRANSFER           = 1 << 15
FNAMECMP_FNAME          = 0x80
FNAMECMP_FUZZY          = 0x83

CHUNK_SIZE = 32 * 1024

# ---------------------------------------------------------------------------
# Integer encoders -- exact ports of io.c
# ---------------------------------------------------------------------------

def w_byte(x):
    return bytes([x & 0xFF])


def w_shortint(x):
    return struct.pack('<H', x & 0xFFFF)


def w_int(x):
    return struct.pack('<i', _s32(x))


def _s32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def w_varint(x):
    """Port of io.c write_varint(): SIVAL the value into b[1..4] (little-endian),
    then pack the high bits / extra-byte count into the leading byte b[0]."""
    b = bytearray(5)
    v = x & 0xFFFFFFFF
    b[1] = v & 0xFF
    b[2] = (v >> 8) & 0xFF
    b[3] = (v >> 16) & 0xFF
    b[4] = (v >> 24) & 0xFF
    cnt = 4
    while cnt > 1 and b[cnt] == 0:
        cnt -= 1
    bit = 1 << (7 - cnt + 1)
    if b[cnt] >= bit:
        cnt += 1
        b[0] = (~(bit - 1)) & 0xFF
    elif cnt > 1:
        b[0] = (b[cnt] | ((~(bit * 2 - 1)) & 0xFF)) & 0xFF
    else:
        b[0] = b[1]
    return bytes(b[:cnt])


def w_varlong(x, min_bytes):
    """Port of io.c write_varlong()."""
    b = bytearray(9)
    v = x & ((1 << 64) - 1)
    for i in range(8):
        b[1 + i] = (v >> (8 * i)) & 0xFF
    cnt = 8
    while cnt > min_bytes and b[cnt] == 0:
        cnt -= 1
    bit = 1 << (7 - cnt + min_bytes)
    if b[cnt] >= bit:
        cnt += 1
        b[0] = (~(bit - 1)) & 0xFF
    elif cnt > min_bytes:
        b[0] = (b[cnt] | ((~(bit * 2 - 1)) & 0xFF)) & 0xFF
    else:
        b[0] = b[cnt]
    return bytes(b[:cnt])


def w_varint30(x, protocol=DEFAULT_PROTOCOL):
    # write_varint30(): varint at proto >= 30, plain int below.
    return w_varint(x) if protocol >= 30 else w_int(x)


def w_varlong30(x, min_bytes, protocol=DEFAULT_PROTOCOL):
    return w_varlong(x, min_bytes) if protocol >= 30 else w_int(x)


_INT_BYTE_EXTRA = ([0] * 32) + ([1] * 16) + ([2] * 8) + ([3] * 4) + ([4] * 2) + [5, 6]


def to_wire_mode(mode):
    # rsync.h to_wire_mode(): identity on Linux (S_ISLNK already 0120000 etc).
    return mode


def w_sum_head(count, blength, s2length, remainder):
    """io.c write_sum_head() at protocol >= 27: 4 ints."""
    return w_int(count) + w_int(blength) + w_int(s2length) + w_int(remainder)


def get_checksum1(buf):
    """Port of checksum.c get_checksum1() (CHAR_OFFSET == 0): rsync's rolling
    block checksum (sum1).  `buf` is bytes; the bytes are signed-char and the
    accumulators are uint32 (wrapping), matching the C exactly."""
    if isinstance(buf, str):
        buf = buf.encode()
    sb = [c - 256 if c >= 128 else c for c in buf]
    n = len(sb)
    s1 = s2 = 0
    i = 0
    while i < n - 4:
        s2 = (s2 + 4 * (s1 + sb[i]) + 3 * sb[i + 1] + 2 * sb[i + 2] + sb[i + 3]) & 0xFFFFFFFF
        s1 = (s1 + sb[i] + sb[i + 1] + sb[i + 2] + sb[i + 3]) & 0xFFFFFFFF
        i += 4
    while i < n:
        s1 = (s1 + sb[i]) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
        i += 1
    return ((s1 & 0xFFFF) + ((s2 & 0xFFFF) << 16)) & 0xFFFFFFFF


def w_vstring(s):
    """io.c write_vstring(): a 1- or 2-byte length prefix then the bytes."""
    if isinstance(s, str):
        s = s.encode()
    n = len(s)
    if n > 0x7F:
        return bytes([n // 0x100 + 0x80, n & 0xFF]) + s
    return bytes([n]) + s


_PERM_BITS = [
    (S_IFDIR, 'd'), (S_IFLNK, 'l'), (0o020000, 'c'), (0o060000, 'b'),
    (0o010000, 'p'), (0o140000, 's'),
]


def sort_key(entry):
    """Approximate rsync's f_name_cmp ordering: bytewise on the name, with a
    directory keyed as if it had a trailing '/' so it sorts immediately before
    its contents (and a file foo.txt before a dir foo).  Good for flat lists and
    simple trees; not the full path-state machine."""
    return entry.name + (b'/' if entry.is_dir else b'')


def sort_entries(entries):
    """rsync sorts the received file list before indexing it, so the transfer
    ndx is the sorted position, not the wire position.  Return entries in that
    order."""
    return sorted(entries, key=sort_key)


def mode_to_perms(mode):
    """An ls-style permission string, e.g. '-rw-r--r--' / 'drwxr-xr-x'."""
    typ = '-'
    for bits, ch in _PERM_BITS:
        if (mode & S_IFMT) == bits:
            typ = ch
            break
    out = [typ]
    for who in (6, 3, 0):
        out.append('r' if mode & (4 << who) else '-')
        out.append('w' if mode & (2 << who) else '-')
        out.append('x' if mode & (1 << who) else '-')
    return ''.join(out)


def xattr_list_wire(items):
    """xattrs.c send_xattr() wire bytes for a NEW xattr list -- the form a
    sender appends to a file-list entry under -X.  The leading 0 (ndx+1 with
    ndx=-1) means 'literal data follows'.  `items` is a list of (name,
    datum_len, datum): `name` includes any namespace prefix and a trailing NUL,
    `datum_len` is the declared value length (need not match `datum`), and
    `datum` is the literal trailing bytes.  A peer exercising the receiver's
    datum_len cap can pass an empty `datum` since receive_xattr() validates
    datum_len before it reads the value."""
    out = bytearray(w_varint(0) + w_varint(len(items)))
    for name, datum_len, datum in items:
        if isinstance(name, str):
            name = name.encode()
        out += w_varint(len(name)) + w_varint(datum_len) + name + datum
    return bytes(out)


# ---------------------------------------------------------------------------
# File-list entry -- declarative, every field overridable for fuzzing
# ---------------------------------------------------------------------------

class FileEntry:
    """One flat (non-inc-recurse) file-list entry.

    Set the fields you care about; encode() emits the exact wire bytes for
    protocol 30 with byte/short flags.  For malformed-input testing, set
    extra_flags to OR bits into the computed xflags, or set raw= to emit a
    fully hand-built byte string instead.
    """

    def __init__(self, name, *, mode=S_IFREG | 0o644, length=0, modtime=1700000000,
                 csum=None, extra_flags=0, protocol=DEFAULT_PROTOCOL, raw=None,
                 hlink_ndx=None, uid=None, user_name=None):
        self.name = name.encode() if isinstance(name, str) else name
        self.mode = mode
        self.length = length
        self.modtime = modtime
        self.csum = csum            # bytes; appended when the daemon has -c
        self.extra_flags = extra_flags
        self.protocol = protocol
        self.raw = raw
        # A non-first hard-link entry: sets XMIT_HLINKED (without HLINK_FIRST)
        # and carries this gnum (first_hlink_ndx) as a varint after the name.
        self.hlink_ndx = hlink_ndx
        # Owner: when uid is set the entry drops XMIT_SAME_UID and carries the
        # uid varint (the receiver reads it under preserve_uid). When user_name
        # is also set it adds XMIT_USER_NAME_FOLLOWS + a byte-counted name, which
        # the daemon feeds to its name converter (recv_user_name).
        self.uid = uid
        self.user_name = (user_name.encode() if isinstance(user_name, str)
                          else user_name)

    def encode(self):
        if self.raw is not None:
            return self.raw

        is_reg = (self.mode & 0o170000) == S_IFREG
        is_dir = (self.mode & 0o170000) == S_IFDIR

        # We never preserve uid/gid and always emit a full (non-abbreviated)
        # entry, so the only "same as previous" flags are UID/GID.
        xflags = XMIT_SAME_UID | XMIT_SAME_GID
        xflags |= self.extra_flags
        if self.hlink_ndx is not None:
            xflags |= XMIT_HLINKED
        if self.uid is not None:
            xflags &= ~XMIT_SAME_UID
            if self.user_name is not None:
                xflags |= XMIT_USER_NAME_FOLLOWS

        out = bytearray()

        # --- flags byte/short (proto >= 28, non-varint path) ---
        if not xflags and not is_dir:
            xflags |= XMIT_TOP_DIR
        if (xflags & 0xFF00) or not xflags:
            xflags |= XMIT_EXTENDED_FLAGS
            out += w_shortint(xflags)
        else:
            out += w_byte(xflags)

        # --- name (no prefix compression: l1 == 0) ---
        l2 = len(self.name)
        if xflags & XMIT_LONG_NAME:
            out += w_varint30(l2, self.protocol)
        else:
            out += w_byte(l2)
        out += self.name

        # A non-first hard-link entry (XMIT_HLINKED set, HLINK_FIRST unset)
        # carries its gnum here, per send_file_entry(). A HLINK_FIRST entry
        # carries no gnum (BITS_SETnUNSET is false in recv_file_entry), which is
        # why 0004 uses extra_flags rather than hlink_ndx. We only use small
        # gnums (< this flist's ndx_start), so the full length/mode fields
        # follow; a gnum >= ndx_start would abbreviate the entry (goto the_end).
        if self.hlink_ndx is not None:
            out += w_varint(self.hlink_ndx)

        out += w_varlong30(self.length, 3, self.protocol)
        if not (xflags & XMIT_SAME_TIME):
            out += w_varlong(self.modtime, 4) if self.protocol >= 30 else w_int(self.modtime)
        if not (xflags & XMIT_SAME_MODE):
            out += w_int(to_wire_mode(self.mode))

        # Owner (after mode, per recv_file_entry): uid varint, then -- under
        # XMIT_USER_NAME_FOLLOWS -- a byte-counted user name for the converter.
        if self.uid is not None:
            out += w_varint(self.uid)
            if self.user_name is not None:
                out += w_byte(len(self.user_name)) + self.user_name

        # checksum trailer (only when the receiver runs with -c / always_checksum)
        if self.csum is not None:
            out += self.csum

        return bytes(out)


def end_of_flist(io_error=0, protocol=DEFAULT_PROTOCOL):
    """Trailing marker after the last entry. In byte-flags mode (no
    CF_VARINT_FLIST_FLAGS, i.e. no 'v' advertised) recv_file_list() ends the
    list on a single 0 flag byte and reads NO io_error after it -- the io_error
    varint exists only in the varint-flags path. Sending an extra byte here
    leaves a stray 0x00 that the receiver then reads as NDX_DONE (freeing the
    flist), which broke inc_recurse sub-flist sequencing."""
    return w_byte(0)


# NOTE: a plain push to a daemon module (no --delete, no --prune-empty-dirs)
# exchanges NO filter list -- recv_filter_list() only reads from the wire when
# `am_sender || receiver_wants_list`, both false for the receiver here. So the
# file list starts immediately after the checksum seed; do not send a filter
# list or its first byte is consumed as a 0 (end-of-list) flag.


# ---------------------------------------------------------------------------
# Daemon client / sender
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    pass


class ParsedEntry:
    """A file-list entry decoded from the wire (the read side of FileEntry)."""

    __slots__ = ('name', 'mode', 'length', 'mtime', 'link_target')

    def __init__(self, name, mode, length, mtime, link_target=None):
        self.name = name                  # bytes, module-relative
        self.mode = mode
        self.length = length
        self.mtime = mtime
        self.link_target = link_target    # bytes for a symlink, else None

    @property
    def is_reg(self):
        return S_ISREG(self.mode)

    @property
    def is_dir(self):
        return S_ISDIR(self.mode)

    @property
    def is_link(self):
        return S_ISLNK(self.mode)

    def __repr__(self):
        return f"ParsedEntry({self.name!r}, mode={self.mode:o}, length={self.length})"


class DaemonClient:
    """A client that connects to an rsync daemon: runs the @RSYNCD handshake +
    protocol setup, then drives either role depending on the server args -- push
    (send a file list + deltas, as a sender) or pull/list (receive + parse the
    file list, request and receive files, as a receiver).  The protocol steps
    are small overridable methods so a test can swap one behaviour (e.g. the sum
    header or an flist entry) while reusing the rest -- see xrsync.py."""

    def __init__(self, host, port, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.protocol = DEFAULT_PROTOCOL
        self.compat_flags = None
        self.seed = None
        self.xfer_sum_len = 16        # md5 at proto 30 (no string negotiation)
        self._rbuf = b''
        self._ndx_prev_positive = -1  # write_ndx delta state (proto >= 30)
        self._ndx_prev_negative = 1
        self._mux_in = b''            # de-multiplexed sender input (data channel)
        self._r_ndx_prev_positive = -1  # read_ndx delta state
        self._r_ndx_prev_negative = 1
        self.messages = []            # (tag, payload) of non-data frames seen

    # -- low-level socket I/O --------------------------------------------
    def _recv_exact(self, n):
        data = b''
        while len(data) < n:
            if self._rbuf:
                take = self._rbuf[:n - len(data)]
                self._rbuf = self._rbuf[len(take):]
                data += take
                continue
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ProtocolError(f"EOF after {len(data)}/{n} bytes")
            data += chunk
        return data

    def _readline(self):
        line = b''
        while not line.endswith(b'\n'):
            if self._rbuf:
                c, self._rbuf = self._rbuf[:1], self._rbuf[1:]
            else:
                c = self.sock.recv(1)
                if not c:
                    raise ProtocolError("EOF reading a line")
            line += c
        return line.decode('latin-1').rstrip('\n')

    def _r_int(self):
        return _s32(int.from_bytes(self._recv_exact(4), 'little'))

    def _r_varint(self):
        ch = self._recv_exact(1)[0]
        extra = _INT_BYTE_EXTRA[ch >> 2]
        b = bytearray(5)
        if extra:
            bit = 1 << (8 - extra)
            b[0:extra] = self._recv_exact(extra)
            b[extra] = ch & (bit - 1)
        else:
            b[0] = ch
        return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)

    # -- de-multiplexed input (the generator's stream) -------------------
    def _read_data(self, n):
        """Read n bytes from the de-multiplexed sender input, dispatching any
        non-MSG_DATA frames (MSG_INFO/MSG_ERROR/keepalive/redo/...) into
        self.messages and discarding them."""
        while len(self._mux_in) < n:
            val = int.from_bytes(self._recv_exact(4), 'little')
            tag = (val >> 24) - MPLEX_BASE
            ln = val & 0xFFFFFF
            payload = self._recv_exact(ln) if ln else b''
            if tag == MSG_DATA:
                self._mux_in += payload
            else:
                self.messages.append((tag, payload))
        out, self._mux_in = self._mux_in[:n], self._mux_in[n:]
        return out

    def r_int(self):
        return _s32(int.from_bytes(self._read_data(4), 'little'))

    def r_byte(self):
        return self._read_data(1)[0]

    def r_shortint(self):
        return int.from_bytes(self._read_data(2), 'little')

    def r_buf(self, n):
        return self._read_data(n)

    def r_vstring(self):
        n = self._read_data(1)[0]
        if n & 0x80:
            n = (n & 0x7F) * 0x100 + self._read_data(1)[0]
        return self._read_data(n) if n else b''

    def r_ndx(self):
        """Port of io.c read_ndx() at protocol >= 30."""
        b0 = self._read_data(1)[0]
        if b0 == 0xFF:
            b0 = self._read_data(1)[0]
            neg = True
        elif b0 == 0:
            return NDX_DONE
        else:
            neg = False
        prev = self._r_ndx_prev_negative if neg else self._r_ndx_prev_positive
        if b0 == 0xFE:
            b = self._read_data(2)
            if b[0] & 0x80:
                rest = self._read_data(2)
                num = ((b[0] & 0x7F) << 24) | b[1] | (rest[0] << 8) | (rest[1] << 16)
            else:
                num = (b[0] << 8) + b[1] + prev
        else:
            num = b0 + prev
        if neg:
            self._r_ndx_prev_negative = num
            return -num
        self._r_ndx_prev_positive = num
        return num

    def r_sum_head(self):
        """io.c read_sum_head(): count, blength, s2length, remainder."""
        return (self.r_int(), self.r_int(), self.r_int(), self.r_int())

    def r_varint(self):
        """io.c read_varint() on the de-multiplexed stream."""
        ch = self._read_data(1)[0]
        extra = _INT_BYTE_EXTRA[ch >> 2]
        b = bytearray(5)
        if extra:
            bit = 1 << (8 - extra)
            b[0:extra] = self._read_data(extra)
            b[extra] = ch & (bit - 1)
        else:
            b[0] = ch
        return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)

    def r_varlong(self, min_bytes):
        """io.c read_varlong() on the de-multiplexed stream."""
        b2 = self._read_data(min_bytes)
        u = bytearray(9)
        u[0:min_bytes - 1] = b2[1:min_bytes]
        extra = _INT_BYTE_EXTRA[b2[0] >> 2]
        if extra:
            bit = 1 << (8 - extra)
            u[min_bytes - 1:min_bytes - 1 + extra] = self._read_data(extra)
            u[min_bytes + extra - 1] = b2[0] & (bit - 1)
        else:
            u[min_bytes - 1] = b2[0]
        x = 0
        for i in range(8):
            x |= u[i] << (8 * i)
        return x

    def r_varint30(self):
        return self.r_varint() if self.protocol >= 30 else self.r_int()

    def r_varlong30(self, min_bytes):
        return self.r_varlong(min_bytes) if self.protocol >= 30 else self.r_int()

    # -- handshake + setup ------------------------------------------------
    def handshake(self, module, server_args, greeting_version=30):
        """Run the @RSYNCD handshake for `module` and send `server_args`
        (the daemon-side argv).  Returns once protocol setup is done and the
        stream is multiplexed in both directions."""
        greeting = self._readline()
        if not greeting.startswith('@RSYNCD:'):
            raise ProtocolError(f"bad greeting: {greeting!r}")
        # Our greeting: claim greeting_version so the daemon negotiates down to
        # it; <=31 means we needn't send a digest list.
        self._send_raw(f"@RSYNCD: {greeting_version}.0\n".encode())
        self._send_raw(module.encode() + b"\n")
        resp = self._readline()
        if 'OK' not in resp:
            raise ProtocolError(f"daemon did not send OK (got {resp!r}); "
                                "module may require auth or be unknown")
        # Server-side argv, NUL-terminated, with a trailing empty arg.
        payload = b''.join(a.encode() + b"\0" for a in server_args) + b"\0"
        self._send_raw(payload)
        # setup_protocol (proto >= 30, daemon side): it skips the binary version
        # exchange (remote_protocol is already set from the greeting), writes
        # compat_flags (varint) and -- since we didn't advertise 'v' -- skips
        # string negotiation, then writes the checksum seed.  All still raw.
        self.compat_flags = self._r_varint()
        self.seed = self._r_int()
        # Multiplexing is now active in both directions.

    # -- multiplexed output ----------------------------------------------
    def _send_raw(self, data):
        self.sock.sendall(data)

    def _frame(self, code, payload):
        hdr = struct.pack('<I', ((MPLEX_BASE + code) << 24) | len(payload))
        return hdr + payload

    def send_data(self, payload):
        """Send `payload` as one MSG_DATA frame (the normal sender stream:
        filter list, file list, file data)."""
        self._send_raw(self._frame(MSG_DATA, payload))

    def send_message(self, code, payload):
        """Send a non-data message frame (e.g. MSG_INFO) to the receiver."""
        self._send_raw(self._frame(code, payload))

    # -- convenience ------------------------------------------------------
    def send_flat_flist(self, entries, io_error=0):
        """Send `entries` followed by the end marker, in one MSG_DATA frame.
        No filter list is sent (see the note above empty-filter handling)."""
        buf = bytearray()
        for e in entries:
            buf += e.encode()
        buf += end_of_flist(io_error, self.protocol)
        self.send_data(bytes(buf))

    def w_ndx(self, ndx):
        """Full port of io.c write_ndx() at protocol >= 30: NDX_DONE -> a single
        0 byte; negatives -> a leading 0xFF then a delta against prev_negative;
        non-negatives -> a delta against prev_positive."""
        if ndx == NDX_DONE:
            return b'\x00'
        b = bytearray()
        if ndx >= 0:
            diff = ndx - self._ndx_prev_positive
            self._ndx_prev_positive = ndx
            absndx = ndx
        else:
            b.append(0xFF)
            absndx = -ndx
            diff = absndx - self._ndx_prev_negative
            self._ndx_prev_negative = absndx
        if 0 < diff < 0xFE:
            b.append(diff)
        elif diff < 0 or diff > 0x7FFF:
            b += bytes([0xFE, ((absndx >> 24) | 0x80) & 0xFF, absndx & 0xFF,
                        (absndx >> 8) & 0xFF, (absndx >> 16) & 0xFF])
        else:
            b += bytes([0xFE, (diff >> 8) & 0xFF, diff & 0xFF])
        return bytes(b)

    def send_transfer_ndx(self, ndx, iflags=0):
        """Transfer-phase token: a write_ndx() index followed by the shortint
        iflags read by read_ndx_and_attrs() (protocol >= 29)."""
        self.send_data(self.w_ndx(ndx) + w_shortint(iflags))

    def send_ndx_done(self):
        """Send NDX_DONE (a single 0 byte in the ndx stream)."""
        self.send_data(self.w_ndx(NDX_DONE))

    def send_subflist_marker(self, dir_ndx):
        """Announce an inc_recurse sub-flist for dir_ndx: write_ndx of
        NDX_FLIST_OFFSET - dir_ndx (a negative index)."""
        self.send_data(self.w_ndx(NDX_FLIST_OFFSET - dir_ndx))

    def run_forged_transfer(self, forged_type, xname, literal_tail=b'',
                            file_csum_len=16, max_phase=2):
        """Drive the sender side of the transfer phase, FORGING fnamecmp_type on
        every file the generator requests (the chroot-basis attack): for each
        request, read its iflags / basis-type / xname / sum header + block sums,
        then send back the ndx with our forged basis type + xname, the echoed sum
        header, a delta that MATCHES block 0 of the (forged) basis plus the given
        literal tail, and a deliberately wrong whole-file checksum (so the
        receiver -- if it opened the forged basis -- reconstructs the wrong bytes
        and logs 'failed verification'). Loops through both transfer phases (the
        checksum-mismatch redo) until NDX_DONE has advanced past max_phase.

        Stops early (returns) if the receiver drops the connection mid-transfer
        -- e.g. a confined receiver that refuses the forged basis and exits with
        'got a block match with no basis file'."""
        try:
            self._forged_transfer_loop(forged_type, xname, literal_tail,
                                       file_csum_len, max_phase)
        except (ProtocolError, socket.timeout, OSError):
            pass

    def _forged_transfer_loop(self, forged_type, xname, literal_tail,
                              file_csum_len, max_phase):
        phase = 0
        while True:
            ndx = self.r_ndx()
            if ndx == NDX_DONE:
                self.send_data(self.w_ndx(NDX_DONE))
                phase += 1
                if phase > max_phase:
                    break
                continue
            iflags = self.r_shortint()
            if iflags & ITEM_BASIS_TYPE_FOLLOWS:
                self.r_byte()
            if iflags & ITEM_XNAME_FOLLOWS:
                self.r_vstring()
            count, blength, s2length, remainder = self.r_sum_head()
            for _ in range(count):
                self.r_int()             # block weak checksum
                self.r_buf(s2length)     # block strong checksum
            out = bytearray()
            out += self.w_ndx(ndx)
            out += w_shortint(iflags | ITEM_BASIS_TYPE_FOLLOWS | ITEM_XNAME_FOLLOWS)
            out += w_byte(forged_type)
            out += w_vstring(xname)
            out += w_sum_head(count, blength, s2length, remainder)
            if count > 0:
                out += w_int(-1)         # match block 0 of the (forged) basis
            if literal_tail:
                out += w_int(len(literal_tail)) + literal_tail
            out += w_int(0)              # end-of-file token
            out += b'\x00' * file_csum_len  # wrong whole-file checksum
            self.send_data(bytes(out))

    # -- receive side (pull / list) --------------------------------------
    def recv_flist(self, preserve_links=True):
        """Send the (empty) filter list the sender expects, then read + parse
        the daemon's file list.  Returns a list of ParsedEntry.  Decodes the
        -lt subset (no uid/gid/devices); override for more.  This is a natural
        hook point: a test can subclass and tamper with what it returns."""
        self.send_data(w_int(0))                 # empty filter list (terminator)
        entries = []
        prev_name = b''
        prev_mode = 0
        prev_mtime = 0
        while True:
            flags = self.r_byte()
            if flags & XMIT_EXTENDED_FLAGS:
                flags |= self.r_byte() << 8
            if flags == 0:
                break                            # end of list (no io_error at p30)
            l1 = self.r_byte() if flags & XMIT_SAME_NAME else 0
            l2 = self.r_varint30() if flags & XMIT_LONG_NAME else self.r_byte()
            name = prev_name[:l1] + self.r_buf(l2)
            prev_name = name
            length = self.r_varlong30(3)
            if not (flags & XMIT_SAME_TIME):
                prev_mtime = self.r_varlong30(4)
            mtime = prev_mtime
            if flags & XMIT_MOD_NSEC:
                self.r_varint()                  # nsec, ignored
            if not (flags & XMIT_SAME_MODE):
                prev_mode = self.r_int()
            mode = prev_mode
            link_target = None
            if preserve_links and S_ISLNK(mode):
                link_target = self.r_buf(self.r_varint30())
            entries.append(ParsedEntry(name, mode, length, mtime, link_target))
        return entries

    def make_request(self, ndx):
        """Generator request for a whole-file transfer of `ndx`: the ndx, the
        item flags, and a count=0 sum header (no local basis).  Overridable
        hook -- a test can return a malformed request here."""
        return (self.w_ndx(ndx) + w_shortint(ITEM_TRANSFER)
                + w_sum_head(0, 0, 0, 0))

    def recv_file_transfer(self, ndx):
        """Read one file the sender sends in reply to make_request(): item
        flags, optional basis type / xname, the echoed sum header, the literal
        token stream, and the whole-file checksum.  Returns the file bytes."""
        iflags = self.r_shortint()
        if iflags & ITEM_BASIS_TYPE_FOLLOWS:
            self.r_byte()
        if iflags & ITEM_XNAME_FOLLOWS:
            self.r_vstring()
        self.r_sum_head()                        # count=0 for a whole-file pull
        data = bytearray()
        while True:
            tok = self.r_int()
            if tok == 0:
                break                            # end of file
            if tok > 0:
                data += self.r_buf(tok)          # literal chunk
            else:
                raise ProtocolError(f"unexpected block match token {tok} "
                                    "(no basis was offered)")
        self.r_buf(self.xfer_sum_len)            # whole-file checksum (unverified)
        return bytes(data)

    def pull(self, dest_dir, verbose=False, preserve_times=True,
             preserve_perms=True):
        """Receive the file list and materialise it under dest_dir: make
        directories, create symlinks, and download regular files (whole-file
        requests).  Returns the parsed file list."""
        import os
        # rsync indexes the file list by SORTED order, so the transfer ndx is
        # the sorted position (both peers sort identically).
        entries = sort_entries(self.recv_flist())
        reg = []                                 # (ndx, entry, path) to fetch
        for ndx, e in enumerate(entries):
            rel = e.name.decode('utf-8', 'surrogateescape')
            if rel in ('.', ''):
                continue
            path = os.path.join(dest_dir, rel)
            if e.is_dir:
                os.makedirs(path, exist_ok=True)
            elif e.is_link and e.link_target is not None:
                tgt = e.link_target.decode('utf-8', 'surrogateescape')
                if os.path.lexists(path):
                    os.unlink(path)
                os.symlink(tgt, path)
            elif e.is_reg:
                reg.append((ndx, e, path))
            if verbose:
                print(rel)
        # Phase 1: send all whole-file requests, then NDX_DONE.
        for ndx, e, path in reg:
            self.send_data(self.make_request(ndx))
        self.send_data(self.w_ndx(NDX_DONE))
        # Read replies, mirroring the sender's per-phase NDX_DONE handshake.
        got = {}
        phase = 1
        while True:
            ndx = self.r_ndx()
            if ndx == NDX_DONE:
                phase += 1
                if phase > 2:
                    break
                self.send_data(self.w_ndx(NDX_DONE))   # phase 2: no redos
                continue
            got[ndx] = self.recv_file_transfer(ndx)
        for ndx, e, path in reg:
            if ndx not in got:
                continue
            with open(path, 'wb') as fh:
                fh.write(got[ndx])
            if preserve_perms:
                os.chmod(path, e.mode & 0o7777)
            if preserve_times:
                os.utime(path, (e.mtime, e.mtime))
        return entries

    def finish_no_transfer(self):
        """Walk the per-phase NDX_DONE handshake with no files requested -- used
        after --list-only so the daemon shuts down cleanly."""
        self.send_data(self.w_ndx(NDX_DONE))
        phase = 1
        try:
            while True:
                if self.r_ndx() == NDX_DONE:
                    phase += 1
                    if phase > 2:
                        break
                    self.send_data(self.w_ndx(NDX_DONE))
        except (ProtocolError, socket.timeout, OSError):
            pass

    # -- send side (basic push) ------------------------------------------
    def push(self, files, modtime=1700000000):
        """Basic upload: `files` is a list of (name, content) regular files.
        Send the file list, then satisfy the receiver's whole-file requests with
        the literal content + its md5 (the proto-30 whole-file digest is plain
        md5, no seed).  Overridable pieces: make_file_token_stream()."""
        import hashlib
        names = [n.encode() if isinstance(n, str) else n for n, _ in files]
        entries = [FileEntry(n, mode=S_IFREG | 0o644, length=len(c),
                             modtime=modtime, protocol=self.protocol)
                   for n, (_, c) in zip(names, files)]
        self.send_flat_flist(entries)
        # The receiver requests by sorted ndx; map it back to our content.
        order = sorted(range(len(files)), key=lambda i: names[i])
        content_by_ndx = {ndx: files[i][1] for ndx, i in enumerate(order)}
        phase = 0
        while True:
            ndx = self.r_ndx()
            if ndx == NDX_DONE:
                self.send_data(self.w_ndx(NDX_DONE))
                phase += 1
                if phase > 2:
                    break
                continue
            iflags = self.r_shortint()
            if iflags & ITEM_BASIS_TYPE_FOLLOWS:
                self.r_byte()
            if iflags & ITEM_XNAME_FOLLOWS:
                self.r_vstring()
            count, blength, s2length, remainder = self.r_sum_head()
            for _ in range(count):
                self.r_int()
                self.r_buf(s2length)
            content = content_by_ndx.get(ndx, b'')
            out = bytearray()
            out += self.w_ndx(ndx)
            out += w_shortint(ITEM_TRANSFER)
            out += w_sum_head(0, 0, 0, 0)
            out += self.make_file_token_stream(content)
            out += hashlib.md5(content).digest()
            self.send_data(bytes(out))

    def make_file_token_stream(self, content):
        """Whole-file literal token stream (token.c simple_send_token, no -z):
        CHUNK_SIZE-sized literal runs then a 0 end token.  Overridable hook."""
        out = bytearray()
        for off in range(0, len(content), CHUNK_SIZE):
            chunk = content[off:off + CHUNK_SIZE]
            out += w_int(len(chunk)) + chunk
        out += w_int(0)
        return bytes(out)

    def drain(self, timeout=3.0):
        """Read whatever the daemon sends back until EOF/timeout.  Returns the
        raw bytes (mux frames undecoded); used only to detect a dropped
        connection (crash) vs an orderly close."""
        self.sock.settimeout(timeout)
        out = b''
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                out += chunk
        except socket.timeout:
            pass
        return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# Back-compat alias: the existing security tests speak of a "DaemonSender".
DaemonSender = DaemonClient


class DaemonReceiver:
    """The SERVER side of the daemon protocol -- the inverse of DaemonSender.
    Accepts a real rsync client and runs the @RSYNCD handshake + protocol-30
    setup as the daemon.  The @RSYNCD handshake is direction-agnostic, so after
    setup this can drive either role: when the client PUSHES (client = sender)
    we act as the receiver/generator and send transfer requests (e.g. malformed
    sum headers); when the client PULLS (client = receiver) we act as the sender
    and send a file list (e.g. one carrying an oversized xattr datum).  Both are
    things rsync_proto's client/sender role cannot do.

    The two stream directions are independent, so we never need to parse the
    client's file list: we just send a request for an index the client's flist
    is known to contain (index 0 for a single pushed file) and let the client's
    send_files() read it.  After sending we drain the client's stream so its
    flist writes don't block while it reaches the request and errors out."""

    def __init__(self, sock, greeting_version=30, protocol=DEFAULT_PROTOCOL):
        self.sock = sock
        self.protocol = protocol
        self.greeting_version = greeting_version
        self._rbuf = b''
        self._ndx_prev_positive = -1
        self._ndx_prev_negative = 1

    # -- low-level I/O (mirrors DaemonSender) -----------------------------
    def _send_raw(self, data):
        self.sock.sendall(data)

    def _recv_exact(self, n):
        data = b''
        while len(data) < n:
            if self._rbuf:
                take = self._rbuf[:n - len(data)]
                self._rbuf = self._rbuf[len(take):]
                data += take
                continue
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ProtocolError(f"EOF after {len(data)}/{n} bytes")
            data += chunk
        return data

    def _readline(self):
        line = b''
        while not line.endswith(b'\n'):
            if self._rbuf:
                c, self._rbuf = self._rbuf[:1], self._rbuf[1:]
            else:
                c = self.sock.recv(1)
                if not c:
                    raise ProtocolError("EOF reading a line")
            line += c
        return line.decode('latin-1').rstrip('\n')

    def _frame(self, code, payload):
        hdr = struct.pack('<I', ((MPLEX_BASE + code) << 24) | len(payload))
        return hdr + payload

    def send_data(self, payload):
        self._send_raw(self._frame(MSG_DATA, payload))

    def w_ndx(self, ndx):
        # Identical to DaemonSender.w_ndx (io.c write_ndx, protocol >= 30).
        if ndx == NDX_DONE:
            return b'\x00'
        b = bytearray()
        if ndx >= 0:
            diff = ndx - self._ndx_prev_positive
            self._ndx_prev_positive = ndx
            absndx = ndx
        else:
            b.append(0xFF)
            absndx = -ndx
            diff = absndx - self._ndx_prev_negative
            self._ndx_prev_negative = absndx
        if 0 < diff < 0xFE:
            b.append(diff)
        elif diff < 0 or diff > 0x7FFF:
            b += bytes([0xFE, ((absndx >> 24) | 0x80) & 0xFF, absndx & 0xFF,
                        (absndx >> 8) & 0xFF, (absndx >> 16) & 0xFF])
        else:
            b += bytes([0xFE, (diff >> 8) & 0xFF, diff & 0xFF])
        return bytes(b)

    # -- handshake (server side) -----------------------------------------
    def handshake(self, compat_flags=0, seed=0):
        """Send the daemon greeting, read the client's greeting + module line,
        send '@RSYNCD: OK', then write the protocol-30 setup (compat flags +
        checksum seed).  We don't read the client's NUL-separated args: it sends
        them after reading our OK and reads our compat/seed afterwards, and the
        directions are independent."""
        self._send_raw(f"@RSYNCD: {self.greeting_version}.0\n".encode())
        self._readline()                 # client greeting (version[, digests])
        self._readline()                 # module name
        self._send_raw(b"@RSYNCD: OK\n")
        self._send_raw(w_varint(compat_flags) + w_int(seed))
        # Multiplexing is now active in both directions.

    # -- generator requests ----------------------------------------------
    def send_sum_request(self, ndx, count, blength, s2length, remainder,
                         iflags=ITEM_TRANSFER):
        """As the generator, request a transfer of file `ndx` with the given
        (possibly malformed) sum header -- ndx + iflags + write_sum_head(), the
        bytes the client's send_files()/receive_sums() read."""
        buf = (self.w_ndx(ndx) + w_shortint(iflags)
               + w_sum_head(count, blength, s2length, remainder))
        self.send_data(buf)

    def drain(self, timeout=5.0):
        """Read and discard the client's stream until it closes (it pushes its
        file list, reaches our request, errors, and disconnects)."""
        self.sock.settimeout(timeout)
        try:
            while self.sock.recv(65536):
                pass
        except (OSError, ProtocolError):
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
