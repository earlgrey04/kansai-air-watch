#!/usr/bin/env python3
"""公的静止画ライブカメラ(cameras.json)の鮮度チェック。
各画像URLに HEAD(不可なら Range GET)を投げ、HTTPステータスと Last-Modified を記録する。
出力: cameras_status.json  {"gen": ISO, "chk": epoch秒, "s": {"<md5(img)[:10]>": [last_modified_epoch|0, http_code]}}
- 固定URLのカメラのみ(時刻から組み立てる動的URL型は対象外)
- 同一ホストへの同時接続は2、全体8並列。7,000台で3〜5分程度
- Last-Modified が無いサーバーは 0 を記録(鮮度不明)
使い方: check_camera_freshness.py <cameras.json> <out.json>
"""
import sys, json, time, hashlib, threading, collections, urllib.request, urllib.parse, email.utils
import concurrent.futures as cf

UA = 'nihon-livemap-freshness/1.0 (+https://air.aiblockchainnexuslab.tech)'
REF = 'https://air.aiblockchainnexuslab.tech/3d.html'
host_sem = collections.defaultdict(lambda: threading.Semaphore(2))

def hid(u): return hashlib.md5(u.encode()).hexdigest()[:10]

def probe(url):
    host = urllib.parse.urlsplit(url).netloc
    with host_sem[host]:
        # HEAD → だめなら Range付きGET → Range非対応(416)なら GET(ヘッダだけ読んで即クローズ)
        attempts = (('HEAD', False), ('GET', True), ('GET', False))
        for i, (method, rng) in enumerate(attempts):
            try:
                h = {'User-Agent': UA, 'Referer': REF}
                if rng: h['Range'] = 'bytes=0-0'
                req = urllib.request.Request(url, method=method, headers=h)
                with urllib.request.urlopen(req, timeout=15) as r:
                    lm = r.headers.get('Last-Modified')
                    ts = int(email.utils.parsedate_to_datetime(lm).timestamp()) if lm else 0
                    return ts, (200 if r.status == 206 else r.status)
            except urllib.error.HTTPError as e:
                if i < len(attempts) - 1 and e.code in (405, 403, 501, 416):
                    continue
                return 0, e.code
            except Exception:
                if i < len(attempts) - 1:
                    continue
                return 0, 0
    return 0, 0

def main(src, out):
    items = json.load(open(src, encoding='utf-8'))['items']
    targets = [(hid(i['i']), i['i']) for i in items if i.get('i') and not i.get('d')]
    res = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(8) as ex:
        for h, (ts, code) in zip([t[0] for t in targets], ex.map(lambda t: probe(t[1]), targets)):
            res[h] = [ts, code]
    now = int(time.time())
    ok = sum(1 for v in res.values() if v[1] == 200)
    fresh = sum(1 for v in res.values() if v[0] and now - v[0] < 3600)
    tmp = out + '.tmp'
    json.dump({'gen': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'chk': now, 's': res}, open(tmp, 'w'), separators=(',', ':'))
    import os; os.replace(tmp, out)
    print(f'{len(res)} checked in {time.time()-t0:.0f}s: 200={ok}, updated<1h={fresh}, no-LM={sum(1 for v in res.values() if v[1]==200 and not v[0])}')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
