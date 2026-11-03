"""Browser probe: capture real webclient API calls on a MEGA folder page."""
import json, re, sys
from playwright.sync_api import sync_playwright

LINK = sys.argv[1]
calls = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox'])
    ctx = b.new_context(locale='en-US')
    page = ctx.new_page()

    def on_response(r):
        u = r.url
        if 'api.mega.co.nz' not in u:
            return
        try:
            body = r.json()
        except Exception:
            try:
                body = r.text()[:300]
            except Exception:
                body = '?'
        calls.append({
            'url': u,
            'status': r.status,
            'post': (r.request.post_data or '')[:400],
            'resp': json.dumps(body)[:600] if not isinstance(body, str) else body,
            'hdrs': {k: v for k, v in r.request.headers.items()
                     if k.lower() in ('origin', 'referer', 'content-type', 'x-requested-with')},
        })

    page.on('response', on_response)

    ws_frames = []

    def on_ws(ws):
        print('WS OPEN:', ws.url[:100])
        ws.on('framereceived', lambda f: ws_frames.append(('recv', str(f)[:500])))
        ws.on('framesent', lambda f: ws_frames.append(('sent', str(f)[:500])))

    page.on('websocket', on_ws)

    page.goto(LINK, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(20000)
    print('TITLE:', page.title())
    print('=== WS frames (load) ===')
    for i, (d, f) in enumerate(ws_frames[:20]):
        print(f'WS{i} {d}:', f[:400])

    print('=== page markup probe ===')
    print(page.evaluate("""JSON.stringify({
        rows: [...document.querySelectorAll('[class*=file], [class*=grid], [class*=node]')]
            .filter(e => e.textContent && e.textContent.includes('.mp4') && e.children.length)
            .slice(0, 3).map(e => ({cls: e.className.slice(0, 80), txt: e.textContent.trim().slice(0, 60)}))
    })""")[:800])

    print('=== trigger download via UI ===')
    before_ws = len(ws_frames)
    before = len(calls)
    try:
        row = page.locator('text=Anby She Wolf (Sakura G).mp4').first
        row.click(button='right', timeout=8000)
        page.wait_for_timeout(3000)
        print('context menu items:', page.evaluate(
            "JSON.stringify([...document.querySelectorAll('[class*=menu] [class*=item], .dropdown-item, [role=menuitem]')]"
            ".map(e => e.textContent.trim().slice(0, 30)).filter(Boolean).slice(0, 12))"))
        dl = page.locator('text=Download').last
        dl.click(timeout=5000)
        page.wait_for_timeout(10000)
        print('clicked Download')
    except Exception as e:
        print('ui download failed:', str(e)[:200])

    print('=== WS frames after download ===')
    for i, (d, f) in enumerate(ws_frames[before_ws:before_ws + 15]):
        print(f'WSD{i} {d}:', f[:450])
    print('=== API calls after download ===')
    for i, c in enumerate(calls[before:before + 10]):
        print(f"DLCALL{i}", json.dumps(c, ensure_ascii=False)[:900])
    b.close()
