#!/usr/bin/env python3
"""Minimal MEGA public-folder client: anonymous ls + get via MEGA API."""
import json, sys, os, base64, struct, random, time
import urllib.request

def _ensure_crypto():
    try:
        from Crypto.Cipher import AES  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'pycryptodome'])

_ensure_crypto()
from Crypto.Cipher import AES  # noqa: E402

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'}

def b64d(s):
    s = s.strip().replace('-', '+').replace('_', '/')
    return base64.b64decode(s + '=' * (-len(s) % 4))

def xor16(a, b):
    return bytes(x ^ y for x, y in zip(a, b))

def parse_link(link):
    if '#F!' in link:
        parts = link.split('#F!', 1)[1].split('!')
        return parts[0], b64d(parts[1])
    if '/folder/' in link:
        p = link.split('/folder/', 1)[1]
        if '#' not in p:
            raise SystemExit('link missing key fragment')
        h, k = p.split('#', 1)
        return h.strip(), b64d(k.split('/', 1)[0])
    raise SystemExit('unsupported link format: ' + link)

def mega_chunks(size):
    """MEGA chunk layout: 128K doubling to 1M; final <128K remainder merges into last."""
    off, c = 0, 0x10000
    while off < size:
        if size - (off + c) < 0x10000 and c < size - off:
            c = size - off
        yield off, min(c, size - off)
        off += c
        c = min(c * 2, 0x100000)

class Mega:
    def __init__(self, link):
        self.handle, self.mkey = parse_link(link)
        self.sid = random.randint(0, 2 ** 31)
        self._nodes = None

    def api(self, seq):
        url = f'https://g.api.mega.co.nz/cs?id={self.sid}&n={self.handle}'
        req = urllib.request.Request(url, data=json.dumps(seq).encode(),
                                     headers={'Content-Type': 'application/json', **UA})
        last = None
        for attempt in range(3):
            try:
                return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())[0]
            except Exception as e:
                last = e
                time.sleep(2)
        raise SystemExit(f'api error: {last}')

    def nodes(self):
        if self._nodes is not None:
            return self._nodes
        r = self.api([{'a': 'f', 'c': 1, 'r': 1}])
        if isinstance(r, int):
            raise SystemExit(f'list failed: errno {-r}')
        if not (isinstance(r, dict) and 'f' in r):
            raise SystemExit('unexpected list response: ' + json.dumps(r)[:200])
        out = {}
        for n in r['f']:
            h, p, t = n.get('h'), n.get('p'), n.get('t', 1)
            fullkey, name = None, ''
            size = int(n.get('s', 0) or 0)
            ablob = b64d(n.get('a', '')) if n.get('a') else b''
            for seg in n.get('k', '').split('/'):
                part = seg.split(':')[-1]
                try:
                    fk = AES.new(self.mkey, AES.MODE_ECB).decrypt(b64d(part))
                except Exception:
                    continue
                if len(fk) not in (16, 32):
                    continue
                ak = xor16(fk[:16], fk[16:]) if t == 0 and len(fk) == 32 else fk[:16]
                if ablob:
                    try:
                        dec = AES.new(ak, AES.MODE_CBC, b'\0' * 16).decrypt(ablob)
                        if not dec.startswith(b'MEGA'):
                            continue
                        name = json.loads(dec[4:].rstrip(b'\x00').decode('utf-8', 'replace')).get('n', '')
                    except Exception:
                        continue
                fullkey = fk
                break
            out[h] = {'p': p, 't': t, 'name': name, 'size': size, 'key': fullkey}
        self._nodes = out
        return out

    def ls(self):
        nm = self.nodes()
        def path_of(h):
            parts, c = [], h
            for _ in range(16):
                if c not in nm:
                    break
                p = nm[c]['p']
                if p not in nm or p == c:
                    break
                c = p
                if nm[c]['t'] == 1 and nm[c]['name']:
                    parts.append(nm[c]['name'])
            return '/'.join(reversed(parts))
        files = [dict(h=h, path=path_of(h), name=n['name'] or h, size=n['size'], key=n['key'])
                 for h, n in nm.items() if n['t'] == 0]
        files.sort(key=lambda x: (x['path'], x['name']))
        return files

    def by_handle(self, h):
        nm = self.nodes()
        if h not in nm:
            raise SystemExit('handle not found: ' + h)
        n = nm[h]
        if n['t'] != 0 or not n['key']:
            raise SystemExit('not a downloadable file / no key: ' + h)
        return n

    def file_url(self, h):
        last = 'no url'
        # 现代 API: 先拿节点公开句柄(a:l), 再 g(p=ph) 取直链
        r = self.api([{'a': 'l', 'n': h}])
        ph = r if isinstance(r, str) else None
        if ph:
            for seq in ([{'a': 'g', 'p': ph, 'v': 1}], [{'a': 'g', 'p': ph}]):
                r = self.api(seq)
                if isinstance(r, dict) and r.get('g'):
                    return r['g'], int(r['s'])
                last = r
        # 旧式回退: 直接 g(n=h)
        for seq in ([{'a': 'g', 'n': h, 'v': 1}], [{'a': 'g', 'n': h}]):
            r = self.api(seq)
            if isinstance(r, dict) and r.get('g'):
                return r['g'], int(r['s'])
            last = r
        raise SystemExit(f'g failed: {json.dumps(last)[:200]}')

    def file_mac(self, fk, data, size):
        aeskey = xor16(fk[:16], fk[16:])
        nonce = struct.unpack('>Q', fk[16:24])[0]
        aes = AES.new(aeskey, AES.MODE_ECB)
        mac = bytes(16)
        off, cs = 0, 0x10000
        while off < size:
            c = min(cs, size - off)
            if size - (off + c) < 0x10000 and c < size - off:
                c = size - off
            chunk = data[off:off + c]
            for i in range(0, len(chunk), 16):
                mac = aes.encrypt(xor16(chunk[i:i + 16], mac))
            mac = aes.encrypt(xor16(mac, struct.pack('>II', nonce >> 32, nonce & 0xFFFFFFFF)
                                          + struct.pack('>II', off >> 32, off & 0xFFFFFFFF)))
            off += c
            cs = min(cs * 2, 0x100000)
        return mac

    def download(self, h, out_path, want_mac=True):
        url, size = self.file_url(h)
        fk = self.by_handle(h)['key']
        aeskey = xor16(fk[:16], fk[16:])
        nonce = struct.unpack('>Q', fk[16:24])[0]
        expect = struct.unpack('>Q', fk[24:32])[0]
        tries = 0
        while tries < 5:
            tries += 1
            cur = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            if cur >= size:
                break
            cur -= cur % 16
            try:
                req = urllib.request.Request(url, headers={**UA, 'Range': f'bytes={cur}-'})
                resp = urllib.request.urlopen(req, timeout=120)
                cipher = AES.new(aeskey, AES.MODE_CTR, nonce=b'', initial_value=(nonce << 64) + cur // 16)
                with open(out_path, 'ab' if cur else 'wb') as f:
                    while True:
                        buf = resp.read(1 << 20)
                        if not buf:
                            break
                        f.write(cipher.encrypt(buf))
            except Exception as e:
                print(f'WARN retry {tries}: {e}', flush=True)
                time.sleep(3)
        if os.path.getsize(out_path) != size:
            raise SystemExit(f'size mismatch: {os.path.getsize(out_path)} != {size}')
        print(f'SIZE {size}')
        with open(out_path, 'rb') as f:
            head = f.read(16)
        print('MAGIC', head.hex())
        if want_mac and size <= 512 * 1024 * 1024 and cur == 0:
            with open(out_path, 'rb') as f:
                data = f.read()
            calc = struct.unpack('>Q', self.file_mac(fk, data, size))[0]
            print(f'MAC {"ok" if calc == expect else "MISMATCH calc=%016x expect=%016x" % (calc, expect)}')
        else:
            print('MAC skipped')
        print('DOWNLOAD_OK')

def main():
    mode, link = sys.argv[1], sys.argv[2]
    m = Mega(link)
    if mode == 'debug':
        r = m.api([{'a': 'f', 'c': 1, 'r': 1}])
        if isinstance(r, int):
            raise SystemExit(f'list errno {-r}')
        files = [n for n in r['f'] if n.get('t') == 0][:3]
        print('FOLDERKEY', m.mkey.hex(), 'HANDLE', m.handle)
        for n in files:
            print('NODE', json.dumps({k: (v[:80] if isinstance(v, str) else v) for k, v in n.items()})[:500])
            kf = n.get('k', '')
            if kf:
                for seg in kf.split('/'):
                    part = seg.split(':')[-1]
                    try:
                        fk = AES.new(m.mkey, AES.MODE_ECB).decrypt(b64d(part))
                        print('  SEG', seg[:60], '-> fullkey', fk.hex())
                        if n.get('t') == 0 and len(fk) == 32:
                            ak = xor16(fk[:16], fk[16:])
                            blob = b64d(n.get('a', ''))
                            try:
                                dec = AES.new(ak, AES.MODE_CBC, b'\0' * 16).decrypt(blob)
                                print('  ATTR dec repr:', dec[:80])
                            except Exception as e:
                                print('  ATTR fail:', e)
                    except Exception as e:
                        print('  SEG fail:', seg[:60], e)
    elif mode == 'ls':
        for f in m.ls():
            print(json.dumps({k: f[k] for k in ('h', 'path', 'name', 'size')}, ensure_ascii=True))
    elif mode == 'gdebug':
        h = sys.argv[3]
        r = m.api([{'a': 'l', 'n': h}])
        print('a:l ->', repr(r)[:300])
        ph = None
        if isinstance(r, str):
            ph = r
        elif isinstance(r, list) and len(r) == 2 and isinstance(r[1], str):
            ph = r[1]
        elif isinstance(r, list) and len(r) == 2 and isinstance(r[0], int) and r[0] == 0 and isinstance(r[1], str):
            ph = r[1]
        print('ph =', repr(ph))
        for seq in ([{'a': 'g', 'p': ph, 'v': 1}] if ph else [],
                    [{'a': 'g', 'n': h, 'v': 1}],
                    [{'a': 'g', 'n': h, 'ssl': 2}]):
            if not seq:
                continue
            r = m.api(seq)
            print(seq[0].get('p', seq[0].get('n')), seq[0].get('a'), '->', repr(r)[:300])
    elif mode == 'get':
        h = sys.argv[3]
        out = sys.argv[sys.argv.index('-o') + 1] if '-o' in sys.argv else h
        n = m.by_handle(h)
        print('NAME', json.dumps(n['name'], ensure_ascii=True))
        m.download(h, out)
        print('OUTPATH', out)
        print('DOWNLOAD_OK')

if __name__ == '__main__':
    main()
