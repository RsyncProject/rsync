import hashlib
import os
import socket
import struct

MPLEX_BASE = 7
MSG_DATA = 0
MSG_ERROR_XFER = 1
MSG_INFO = 2
MSG_ERROR = 3
MSG_WARNING = 4
MSG_DELETED = 101
MSG_NO_SEND = 102

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

ITEM_BASIS_TYPE_FOLLOWS = 1 << 11
ITEM_XNAME_FOLLOWS      = 1 << 12
ITEM_IS_NEW             = 1 << 13
ITEM_TRANSFER           = 1 << 15
FNAMECMP_FNAME          = 0x80
FNAMECMP_FUZZY          = 0x83

CHUNK_SIZE = 32 * 1024

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
    return w_varint(x) if protocol >= 30 else w_int(x)

def w_varlong30(x, min_bytes, protocol=DEFAULT_PROTOCOL):
    return w_varlong(x, min_bytes) if protocol >= 30 else w_int(x)

_INT_BYTE_EXTRA = ([0] * 32) + ([1] * 16) + ([2] * 8) + ([3] * 4) + ([4] * 2) + [5, 6]

def _read_varint(read):
    first = read(1)[0]
    extra = _INT_BYTE_EXTRA[first >> 2]
    value = bytearray(5)
    if extra:
        bit = 1 << (8 - extra)
        value[:extra] = read(extra)
        value[extra] = first & (bit - 1)
    else:
        value[0] = first
    return value[0] | (value[1] << 8) | (value[2] << 16) | (value[3] << 24)

def to_wire_mode(mode):
    return mode

def w_sum_head(count, blength, s2length, remainder):
    return w_int(count) + w_int(blength) + w_int(s2length) + w_int(remainder)

def get_checksum1(buf):
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
    return entry.name + (b'/' if entry.is_dir else b'')

def sort_entries(entries):
    return sorted(entries, key=sort_key)

def mode_to_perms(mode):
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
    out = bytearray(w_varint(0) + w_varint(len(items)))
    for name, datum_len, datum in items:
        if isinstance(name, str):
            name = name.encode()
        out += w_varint(len(name)) + w_varint(datum_len) + name + datum
    return bytes(out)

class FileEntry:
    def __init__(self, name, *, mode=S_IFREG | 0o644, length=0, modtime=1700000000,
                 csum=None, extra_flags=0, protocol=DEFAULT_PROTOCOL, raw=None,
                 hlink_ndx=None, uid=None, user_name=None):
        self.name = name.encode() if isinstance(name, str) else name
        self.mode = mode
        self.length = length
        self.modtime = modtime
        self.csum = csum
        self.extra_flags = extra_flags
        self.protocol = protocol
        self.raw = raw

        self.hlink_ndx = hlink_ndx

        self.uid = uid
        self.user_name = (user_name.encode() if isinstance(user_name, str)
                          else user_name)

    def encode(self):
        if self.raw is not None:
            return self.raw

        is_reg = (self.mode & 0o170000) == S_IFREG
        is_dir = (self.mode & 0o170000) == S_IFDIR

        xflags = XMIT_SAME_UID | XMIT_SAME_GID
        xflags |= self.extra_flags
        if self.hlink_ndx is not None:
            xflags |= XMIT_HLINKED
        if self.uid is not None:
            xflags &= ~XMIT_SAME_UID
            if self.user_name is not None:
                xflags |= XMIT_USER_NAME_FOLLOWS

        out = bytearray()

        if not xflags and not is_dir:
            xflags |= XMIT_TOP_DIR
        if (xflags & 0xFF00) or not xflags:
            xflags |= XMIT_EXTENDED_FLAGS
            out += w_shortint(xflags)
        else:
            out += w_byte(xflags)

        l2 = len(self.name)
        if xflags & XMIT_LONG_NAME:
            out += w_varint30(l2, self.protocol)
        else:
            out += w_byte(l2)
        out += self.name

        if self.hlink_ndx is not None:
            out += w_varint(self.hlink_ndx)

        out += w_varlong30(self.length, 3, self.protocol)
        if not (xflags & XMIT_SAME_TIME):
            out += w_varlong(self.modtime, 4) if self.protocol >= 30 else w_int(self.modtime)
        if not (xflags & XMIT_SAME_MODE):
            out += w_int(to_wire_mode(self.mode))

        if self.uid is not None:
            out += w_varint(self.uid)
            if self.user_name is not None:
                out += w_byte(len(self.user_name)) + self.user_name

        if self.csum is not None:
            out += self.csum

        return bytes(out)

def end_of_flist(io_error=0, protocol=DEFAULT_PROTOCOL):
    return w_byte(0)

class ProtocolError(Exception):
    pass

class ParsedEntry:
    __slots__ = ('name', 'mode', 'length', 'mtime', 'link_target')

    def __init__(self, name, mode, length, mtime, link_target=None):
        self.name = name
        self.mode = mode
        self.length = length
        self.mtime = mtime
        self.link_target = link_target

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

class _SocketPeer:
    def __init__(self, sock):
        self.sock = sock
        self._rbuf = b''
        self._ndx_prev_positive = -1
        self._ndx_prev_negative = 1

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

    def _send_raw(self, data):
        self.sock.sendall(data)

    def _frame(self, code, payload):
        return struct.pack('<I', ((MPLEX_BASE + code) << 24) | len(payload)) + payload

    def send_data(self, payload):
        self._send_raw(self._frame(MSG_DATA, payload))

    def w_ndx(self, ndx):
        if ndx == NDX_DONE:
            return b'\x00'
        data = bytearray()
        if ndx >= 0:
            diff = ndx - self._ndx_prev_positive
            self._ndx_prev_positive = ndx
            absolute = ndx
        else:
            data.append(0xFF)
            absolute = -ndx
            diff = absolute - self._ndx_prev_negative
            self._ndx_prev_negative = absolute
        if 0 < diff < 0xFE:
            data.append(diff)
        elif diff < 0 or diff > 0x7FFF:
            data += bytes([0xFE, ((absolute >> 24) | 0x80) & 0xFF, absolute & 0xFF,
                           (absolute >> 8) & 0xFF, (absolute >> 16) & 0xFF])
        else:
            data += bytes([0xFE, (diff >> 8) & 0xFF, diff & 0xFF])
        return bytes(data)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

class DaemonClient(_SocketPeer):
    def __init__(self, host, port, timeout=10):
        sock = socket.create_connection((host, port), timeout=timeout)
        self._initialise(sock, timeout)

    @classmethod
    def from_socket(cls, sock, timeout=10):
        client = cls.__new__(cls)
        client._initialise(sock, timeout)
        return client

    def _initialise(self, sock, timeout):
        sock.settimeout(timeout)
        super().__init__(sock)
        self.protocol = DEFAULT_PROTOCOL
        self.compat_flags = None
        self.seed = None
        self.xfer_sum_len = 16
        self._mux_in = b''
        self._r_ndx_prev_positive = -1
        self._r_ndx_prev_negative = 1
        self.messages = []

    def _r_int(self):
        return _s32(int.from_bytes(self._recv_exact(4), 'little'))

    def _r_varint(self):
        return _read_varint(self._recv_exact)

    def _read_data(self, n):
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
        return (self.r_int(), self.r_int(), self.r_int(), self.r_int())

    def r_varint(self):
        return _read_varint(self._read_data)

    def r_varlong(self, min_bytes):
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

    def handshake(self, module, server_args, greeting_version=30):
        greeting = self._readline()
        if not greeting.startswith('@RSYNCD:'):
            raise ProtocolError(f"bad greeting: {greeting!r}")

        self._send_raw(f"@RSYNCD: {greeting_version}.0\n".encode())
        self._send_raw(module.encode() + b"\n")
        resp = self._readline()
        if 'OK' not in resp:
            raise ProtocolError(f"daemon did not send OK (got {resp!r}); "
                                "module may require auth or be unknown")

        payload = b''.join(a.encode() + b"\0" for a in server_args) + b"\0"
        self._send_raw(payload)

        self.compat_flags = self._r_varint()
        self.seed = self._r_int()

    def send_message(self, code, payload):
        self._send_raw(self._frame(code, payload))

    def send_flat_flist(self, entries, io_error=0):
        buf = bytearray()
        for e in entries:
            buf += e.encode()
        buf += end_of_flist(io_error, self.protocol)
        self.send_data(bytes(buf))

    def send_transfer_ndx(self, ndx, iflags=0):
        self.send_data(self.w_ndx(ndx) + w_shortint(iflags))

    def send_ndx_done(self):
        self.send_data(self.w_ndx(NDX_DONE))

    def send_subflist_marker(self, dir_ndx):
        self.send_data(self.w_ndx(NDX_FLIST_OFFSET - dir_ndx))

    def run_forged_transfer(self, forged_type, xname, literal_tail=b'',
                            file_csum_len=16, max_phase=2):
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
                self.r_int()
                self.r_buf(s2length)
            out = bytearray()
            out += self.w_ndx(ndx)
            out += w_shortint(iflags | ITEM_BASIS_TYPE_FOLLOWS | ITEM_XNAME_FOLLOWS)
            out += w_byte(forged_type)
            out += w_vstring(xname)
            out += w_sum_head(count, blength, s2length, remainder)
            if count > 0:
                out += w_int(-1)
            if literal_tail:
                out += w_int(len(literal_tail)) + literal_tail
            out += w_int(0)
            out += b'\x00' * file_csum_len
            self.send_data(bytes(out))

    def recv_flist(self, preserve_links=True):
        self.send_data(w_int(0))
        entries = []
        prev_name = b''
        prev_mode = 0
        prev_mtime = 0
        while True:
            flags = self.r_byte()
            if flags & XMIT_EXTENDED_FLAGS:
                flags |= self.r_byte() << 8
            if flags == 0:
                break
            l1 = self.r_byte() if flags & XMIT_SAME_NAME else 0
            l2 = self.r_varint30() if flags & XMIT_LONG_NAME else self.r_byte()
            name = prev_name[:l1] + self.r_buf(l2)
            prev_name = name
            length = self.r_varlong30(3)
            if not (flags & XMIT_SAME_TIME):
                prev_mtime = self.r_varlong30(4)
            mtime = prev_mtime
            if flags & XMIT_MOD_NSEC:
                self.r_varint()
            if not (flags & XMIT_SAME_MODE):
                prev_mode = self.r_int()
            mode = prev_mode
            link_target = None
            if preserve_links and S_ISLNK(mode):
                link_target = self.r_buf(self.r_varint30())
            entries.append(ParsedEntry(name, mode, length, mtime, link_target))
        return entries

    def make_request(self, ndx):
        return (self.w_ndx(ndx) + w_shortint(ITEM_TRANSFER)
                + w_sum_head(0, 0, 0, 0))

    def recv_file_transfer(self, ndx):
        iflags = self.r_shortint()
        if iflags & ITEM_BASIS_TYPE_FOLLOWS:
            self.r_byte()
        if iflags & ITEM_XNAME_FOLLOWS:
            self.r_vstring()
        self.r_sum_head()
        data = bytearray()
        while True:
            tok = self.r_int()
            if tok == 0:
                break
            if tok > 0:
                data += self.r_buf(tok)
            else:
                raise ProtocolError(f"unexpected block match token {tok} "
                                    "(no basis was offered)")
        self.r_buf(self.xfer_sum_len)
        return bytes(data)

    def pull(self, dest_dir, verbose=False, preserve_times=True,
             preserve_perms=True):
        entries = sort_entries(self.recv_flist())
        reg = []
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

        for ndx, e, path in reg:
            self.send_data(self.make_request(ndx))
        self.send_data(self.w_ndx(NDX_DONE))

        got = {}
        phase = 1
        while True:
            ndx = self.r_ndx()
            if ndx == NDX_DONE:
                phase += 1
                if phase > 2:
                    break
                self.send_data(self.w_ndx(NDX_DONE))
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

    def push(self, files, modtime=1700000000):
        names = [n.encode() if isinstance(n, str) else n for n, _ in files]
        entries = [FileEntry(n, mode=S_IFREG | 0o644, length=len(c),
                             modtime=modtime, protocol=self.protocol)
                   for n, (_, c) in zip(names, files)]
        self.send_flat_flist(entries)

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
        out = bytearray()
        for off in range(0, len(content), CHUNK_SIZE):
            chunk = content[off:off + CHUNK_SIZE]
            out += w_int(len(chunk)) + chunk
        out += w_int(0)
        return bytes(out)

    def drain(self, timeout=3.0):
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

DaemonSender = DaemonClient

class DaemonReceiver(_SocketPeer):
    def __init__(self, sock, greeting_version=30, protocol=DEFAULT_PROTOCOL):
        super().__init__(sock)
        self.protocol = protocol
        self.greeting_version = greeting_version

    def handshake(self, compat_flags=0, seed=0):
        self._send_raw(f"@RSYNCD: {self.greeting_version}.0\n".encode())
        self._readline()
        self._readline()
        self._send_raw(b"@RSYNCD: OK\n")
        self._send_raw(w_varint(compat_flags) + w_int(seed))

    def send_sum_request(self, ndx, count, blength, s2length, remainder,
                         iflags=ITEM_TRANSFER):
        buf = (self.w_ndx(ndx) + w_shortint(iflags)
               + w_sum_head(count, blength, s2length, remainder))
        self.send_data(buf)

    def drain(self, timeout=5.0):
        self.sock.settimeout(timeout)
        try:
            while self.sock.recv(65536):
                pass
        except (OSError, ProtocolError):
            pass

