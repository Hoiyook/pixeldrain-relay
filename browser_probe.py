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
    page.goto(LINK, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(20000)
    print('TITLE:', page.title())
    try:
        print('SERVICE WORKERS:', [w.url for w in ctx.service_workers])
    except Exception as e:
        print('sw check failed:', str(e)[:100])
    print('=== API calls on load ===')
    for i, c in enumerate(calls):
        print(f"CALL{i}", json.dumps(c, ensure_ascii=False)[:900])

    print('=== global objects ===')
    print(page.evaluate("JSON.stringify({api_req: typeof api_req, api: typeof api, M: typeof M, "
                        "MegaApi: typeof MegaApi, megajs: typeof megajs, uapi: typeof uapi})"))
    for expr in (
        "api_req({a:'g', n:'hGghhQaZ'})",
        "api.req({a:'g', n:'hGghhQaZ'})",
        "M.api.req({a:'g', n:'hGghhQaZ'})",
    ):
        try:
            res = page.evaluate(f"{expr}.then(r => JSON.stringify(r).slice(0, 400)).catch(e => 'ERR ' + String(e).slice(0, 150))")
            print(f"EVAL {expr} -> {res}")
            if res and 'ERR' not in str(res[:6]):
                break
        except Exception as e:
            print(f"EVAL {expr} EXC {str(e)[:120]}")

    print('=== API calls after eval ===')
    for i, c in enumerate(calls):
        print(f"POSTCALL{i}", json.dumps(c, ensure_ascii=False)[:900])
    b.close()
