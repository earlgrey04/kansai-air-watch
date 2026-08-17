#!/usr/bin/env python3
"""YouTubeライブカメラ一覧(とまり木 tomarigi.me の公開API /api/spots)を取得し、
3Dビューアのライブカメラモード用 cameras_yt.json に正規化する。
- 6時間以内に更新済みならスキップ(相手サーバーへの負荷配慮)
- 失敗時は前回ファイルをそのまま残す
出力: カレントディレクトリの cameras_yt.json
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

OUT = 'cameras_yt.json'
SRC = 'https://tomarigi.me/api/spots'
MIN_INTERVAL = 6 * 3600

def main():
    # 前回ファイルの gen(生成時刻)で鮮度判定(Actionsではcurlで落とすためmtimeは使えない)
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
        g = datetime.strptime(prev.get('gen', ''), '%Y-%m-%dT%H:%M:%S%z')
        if (datetime.now(timezone.utc) - g).total_seconds() < MIN_INTERVAL and len(prev.get('items') or []) >= 100:
            print('cameras_yt.json: fresh, skip'); return 0
    except Exception:
        pass
    req = urllib.request.Request(SRC, headers={'User-Agent': 'nihon-livemap/1.0 (+https://air.aiblockchainnexuslab.tech)'})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)
    spots = d.get('spots') or []
    items = []
    for s in spots:
        try:
            la = float(s['lat']); lo = float(s['lng'])
        except Exception:
            continue
        if not (20 <= la <= 50 and 120 <= lo <= 155):
            continue
        items.append({
            'n': s.get('name') or '', 'la': round(la, 5), 'lo': round(lo, 5), 'k': 'yt',
            'c': s.get('category') or 'other', 'ch': s.get('channel_id') or '', 'v': s.get('video_id') or '',
            'live': bool(s.get('is_live')), 'last': (s.get('last_live_at') or '')[:19],
            's': s.get('channel_title') or '',
        })
    if len(items) < 100:
        print('too few items, keep previous', file=sys.stderr); return 1
    out = {'gen': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'src': 'https://tomarigi.me/', 'items': items}
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f'cameras_yt.json: {len(items)} items, live={sum(i["live"] for i in items)}')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('fetch_youtube_cams failed:', e, file=sys.stderr)
        sys.exit(1)
