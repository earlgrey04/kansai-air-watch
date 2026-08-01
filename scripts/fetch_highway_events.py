#!/usr/bin/env python3
"""JARTICの交通事象(規制・通行止め・事故・故障車)を集約してhighway_events.jsonを出力。

- r1/301: 規制・通行止め(原因c・規制種別rd・実座標つき)
- r3/901: 事故, r3/902: 故障車(発生時のみファイルが存在)
- エリア: 都道府県R01〜R47＋都市高速C01〜C05(ファイルなし=事象なし)
- 既存出力が12分以内なら再取得しない(呼び出し側は15〜20分毎)
出典: 道路交通情報はJARTIC(日本道路交通情報センター)提供情報を加工
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
OUT = 'highway_events.json'
BASE = 'https://www.jartic.or.jp/d/traffic_info'
UA = {'User-Agent': 'Mozilla/5.0 (compatible; kansai-air-watch)'}
AREAS = [f'R{i:02d}' for i in range(1, 48)] + [f'C{i:02d}' for i in range(1, 6)]
HW_RE = re.compile(r'自動車道|高速|有料道路|外環|アクアライン|首都圏中央連絡|西湘|新湘南|京葉道路|第三京浜|横浜新道|保土ヶ谷バイパス|名阪国道|バイパス')


def get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def main():
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
            if time.time() - prev.get('fetchedAtUnix', 0) < 12 * 60:
                print('skip (fresh)')
                return
        except Exception:
            pass

    t1 = (get(f'{BASE}/r1/target.json') or {}).get('target')
    t3 = (get(f'{BASE}/r3/target.json') or {}).get('target')
    events = []

    def collect(rN, tgt, code, kind):
        if not tgt:
            return
        for area in AREAS:
            d = get(f'{BASE}/{rN}/{tgt}/d/{code}/{area}.json', timeout=12)
            if not d:
                continue
            for f in d.get('features', []):
                p = f.get('properties', {})
                g = f.get('geometry', {})
                road = ' '.join(p.get('pd') or []) or (p.get('r') or '')
                hw = bool(area.startswith('C') or HW_RE.search(road))
                cs = p.get('cs') or ''
                k2 = kind
                if kind == 'reg':
                    if cs == '401':
                        k2 = 'jam'      # 渋滞
                    elif cs == '402':
                        k2 = 'slow'     # 混雑
                    elif not (p.get('c') or p.get('rd')):
                        continue        # ラベルなしの雑データは除外
                ev = {'k': k2,
                      'c': p.get('c') or '',      # 原因(工事・事故など)
                      'rd': p.get('rd') or '',    # 規制種別(通行止など)
                      'r': p.get('r') or '',      # 路線・区間説明
                      'i': p.get('i') or '',      # 区間名
                      'd': p.get('d') or '',      # 方向
                      'road': road, 'hw': 1 if hw else 0, 'a': area}
                if g.get('type') == 'Point':
                    ev['pt'] = [round(g['coordinates'][0], 5), round(g['coordinates'][1], 5)]
                elif g.get('type') == 'LineString' and g.get('coordinates'):
                    ev['ln'] = [[round(x, 5), round(y, 5)] for x, y in g['coordinates']]
                    ev['pt'] = ev['ln'][len(ev['ln']) // 2]
                elif p.get('p'):
                    ev['pt'] = [round(p['p'][0][0], 5), round(p['p'][0][1], 5)]
                else:
                    continue
                events.append(ev)
            time.sleep(0.05)

    collect('r1', t1, '301', 'reg')      # 規制・通行止め
    collect('r3', t3, '901', 'acc')      # 事故
    collect('r3', t3, '902', 'brk')      # 故障車

    out = {'fetchedAt': datetime.now(JST).isoformat(timespec='seconds'),
           'fetchedAtUnix': int(time.time()),
           'target': t1, 'events': events}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    n_hw = sum(1 for e in events if e['hw'])
    print(f'ok events={len(events)} (高速系 {n_hw}) bytes={os.path.getsize(OUT)}')


if __name__ == '__main__':
    main()
