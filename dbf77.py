"""Minimal DBF reader for 1C 7.7 (dBase III/FoxPro), read-only."""
import struct, datetime
def read_dbf(path, encoding=None):
    with open(path, 'rb') as f:
        hdr = f.read(32)
        nrec, hlen, rlen = struct.unpack('<IHH', hdr[4:12])
        cp = hdr[29]
        enc = encoding or {0x65: 'cp866', 0xC9: 'cp1251', 0x26: 'cp866'}.get(cp, 'cp1251')
        fields = []
        while True:
            d = f.read(32)
            if not d or d[0] == 0x0D: break
            name = d[:11].split(b'\0')[0].decode('ascii')
            fields.append((name, chr(d[11]), d[16], d[17]))
        f.seek(hlen)
        rows = []
        for _ in range(nrec):
            r = f.read(rlen)
            if len(r) < rlen: break
            if r[0:1] == b'*': continue
            pos, row = 1, {}
            for name, t, ln, dec in fields:
                raw = r[pos:pos+ln]; pos += ln
                s = raw.decode(enc, 'replace')
                if t == 'N':
                    s = s.strip(); row[name] = float(s) if s else 0.0
                elif t == 'D':
                    s = s.strip(); row[name] = datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8])) if s.strip() and s != '00000000' else None
                else:
                    row[name] = s.rstrip()
            rows.append(row)
        return fields, rows, enc
