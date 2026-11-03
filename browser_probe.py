"""Probe v4: CDP network capture incl. service workers, + real download click."""
import json, sys, time
from playwright.sync_api import sync_playwright

LINK = sys.argv[1]
events = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox'])
    ctx = b.new_context(accept_downloads=True, locale='en-US')
    page = ctx.new_page()

    cdp = ctx.new_cdp_session(page)
    cdp.send('Network.enable')
    def on_req(params):
        u = params.get('request', {}).get('url', '')
        if 'api.mega.co.nz' in u:
            events.append(('REQ', u[:110], params['request'].get('postData', '')[:400],
                           {k: v for k, v in params['request'].get('headers', {}).items()
                            if k.lower() in ('origin', 'referer', 'content-type')}))
    def on_resp(params):
        u = params.get('response', {}).get('url', '')
        if 'api.mega.co.nz' in u:
            events.append(('RESP', u[:110], params.get('response', {}).get('status', 0), ''))
    cdp.on('Network.requestWillBeSent', on_req)
    cdp.on('Network.responseReceived', on_resp)

    def dump(tag):
        print(f'=== {tag} ===')
        for i, e in enumerate(events):
            print(f'{tag}{i}', json.dumps(e, ensure_ascii=False)[:800])

    page.goto(LINK, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(15000)
    print('TITLE:', page.title())
    dump('LOAD')

    before = len(events)
    # 尝试点击下载按钮 (file 页有巨大 Download 按钮)
    for sel in ('button.download', '.download-button', '[id*=dlbut]', '.mega-button',
                'text=Download', '[class*=download]'):
        try:
            page.locator(sel).first.click(timeout=4000)
            print('clicked:', sel)
            break
        except Exception:
            continue
    page.wait_for_timeout(12000)
    dump('AFTERCLICK')

    # 等待可能的浏览器下载
    try:
        with page.expect_download(timeout=15000) as dl:
            pass
        d = dl.value
        print('DOWNLOAD EVENT:', d.suggested_filename, d.url[:150])
    except Exception as e:
        print('no download event:', str(e)[:120])
    dump('FINAL')
    b.close()
