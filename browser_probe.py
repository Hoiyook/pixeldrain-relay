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
            'url': u[:120],
            'status': r.status,
            'post': (r.request.post_data or '')[:300],
            'resp': json.dumps(body)[:500] if not isinstance(body, str) else body,
            'origin': r.request.headers.get('origin', ''),
        })

    page.on('response', on_response)
    page.goto(LINK, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(20000)
    print('TITLE:', page.title())
    print('=== API calls on load ===')
    for i, c in enumerate(calls):
        print(f"CALL{i}", json.dumps(c, ensure_ascii=False)[:700])

    before = len(calls)
    try:
        page.locator('text=Anby').first.click(timeout=10000)
        page.wait_for_timeout(8000)
        print('clicked file; url now:', page.url[:100])
    except Exception as e:
        print('click file failed:', str(e)[:150])

    for sel in ('button.download', '.download-button', '[class*=download]', 'text=Download'):
        try:
            page.locator(sel).first.click(timeout=4000)
            page.wait_for_timeout(8000)
            print(f'clicked {sel}')
            break
        except Exception:
            continue

    print('=== API calls after interactions ===')
    for i, c in enumerate(calls[before:]):
        print(f"CALL+{i}", json.dumps(c, ensure_ascii=False)[:700])
    b.close()
