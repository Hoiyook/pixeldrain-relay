#!/usr/bin/env python3
"""MEGA single-file public link anonymous downloader (full 32-byte key links)."""
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

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'}

def b64d(s):
    s = s.strip().replace('-', '+').replace('_', '/')
    return base64.b64decode(s + '=' * (-len(s) % 4))

def b64e(b):
    return base64.b64encode(b).decode().replace('+', '-').replace('/', '_').rstrip('=')

def xor16(a, b):
    return bytes(x ^ y for x, y in zip(a, b))

def mega_chunks(size):
    off, c = 0, 0x10000
    while off < size:
        if size - (off + c) < 0x10000 and c < size - off:
            c = size - off
        yield off, min(c, size - off)
        off += c
        c = min(c * 2, 0x100000)

def file_mac(aeskey, nonce, data):
    aes = AES.new(aeskey, AES.MODE_ECB)
    mac = bytes(16)
    size = len(data)
    for off, c in mega_chunks(size):
        chunk = data[off:off + c]
        for i in range(0, len(chunk), 16):
            mac = aes.encrypt(xor16(chunk[i:i + 16], mac))
        mac = aes.encrypt(xor16(mac, struct.pack('>II', nonce >> 32, nonce & 0xFFFFFFFF)
                                      + struct.pack('>II', off >> 32, off & 0xFFFFFFFF)))
    return mac

def main():
    link = sys.argv[1]
    out = sys.argv[sys.argv.index('-o') + 1] if '-o' in sys.argv else None
    import re
    m = re.search(r'/file/([a-zA-Z0-9_-]+)#([a-zA-Z0-9_-]+)', link)
    if not m:
        raise SystemExit('not a /file/ link: ' + link)
    handle, fk = m.group(1), b64d(m.group(2))
    if len(fk) == 32:
        aeskey = xor16(fk[:16], fk[16:])
        nonce = struct.unpack('>Q', fk[16:24])[0]
        mac_expect = struct.unpack('>Q', fk[24:32])[0]
    elif len(fk) == 16:
        aeskey, nonce, mac_expect = fk, 0, 0
    else:
        raise SystemExit(f'unexpected key length: {len(fk)}')
    sid = random.randint(0, 2 ** 31)
    api = f'https://g.api.mega.co.nz/cs?id={sid}'
    req = urllib.request.Request(api, data=json.dumps([{'a': 'g', 'p': handle}]).encode(),
                                 headers={'Content-Type': 'text/plain;charset=UTF-8', 'Referer': 'https://mega.nz/', 'Origin': 'https://mega.nz', **UA})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())[0]
    if not (isinstance(r, dict) and r.get('g')):
        raise SystemExit(f'g failed: {json.dumps(r)[:200]}')
    size = int(r['s'])
    name = ''
    try:
        at = b64d(r.get('at', ''))
        dec = AES.new(aeskey, AES.MODE_CBC, b'\0' * 16).decrypt(at)
        if dec.startswith(b'MEGA'):
            name = json.loads(dec[4:].rstrip(b'\x00').decode('utf-8', 'replace')).get('n', '')
    except Exception:
        pass
    print(f'NAME {json.dumps(name, ensure_ascii=True)}')
    print(f'SIZE {size}')
    gurl = r['g']
    tries = 0
    while tries < 6:
        tries += 1
        cur = os.path.getsize(out) if (out and os.path.exists(out)) else 0
        if cur >= size:
            break
        cur -= cur % 16
        try:
            req = urllib.request.Request(gurl, headers={**UA, 'Range': f'bytes={cur}-'})
            resp = urllib.request.urlopen(req, timeout=120)
            cipher = AES.new(aeskey, AES.MODE_CTR, nonce=b'', initial_value=(nonce << 64) + cur // 16)
            with open(out, 'ab' if cur else 'wb') as f:
                while True:
                    buf = resp.read(1 << 20)
                    if not buf:
                        break
                    f.write(cipher.encrypt(buf))
        except Exception as e:
            print(f'WARN retry {tries}: {e}', flush=True)
            time.sleep(3)
    if os.path.getsize(out) != size:
        raise SystemExit(f'size mismatch: {os.path.getsize(out)} != {size}')
    print(f'OUTPATH {out}')
    with open(out, 'rb') as f:
        head = f.read(16)
    print('MAGIC', head.hex())
    if size <= 512 * 1024 * 1024 and mac_expect:
        with open(out, 'rb') as f:
            data = f.read()
        calc = struct.unpack('>Q', file_mac(aeskey, nonce, data))[0]
        print(f'MAC {"ok" if calc == mac_expect else "MISMATCH calc=%016x expect=%016x" % (calc, mac_expect)}')
    else:
        print('MAC skipped')
    print('DOWNLOAD_OK')

if __name__ == '__main__':
    main()
