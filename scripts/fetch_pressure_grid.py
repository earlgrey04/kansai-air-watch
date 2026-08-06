#!/usr/bin/env python3
"""Open-Meteo(GFS)の海面更正気圧グリッドを取得してpressure_grid.jsonを出力。

- 既存のpressure_grid.jsonが50分以内ならスキップ(15分毎のcronから呼ばれても1時間毎相当)
- ブラウザから直接Open-Meteoを叩かずに済むよう、statusブランチ経由で配信する
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
OUT = 'pressure_grid.json'
LAT0, LAT1, LON0, LON1, STEP = 16.0, 52.0, 112.0, 164.0, 1.25


def main():
    # 鮮度チェック(50分以内ならスキップ)
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
            age = time.time() - prev.get('fetchedAtUnix', 0)
            if age < 50 * 60:
                print(f'skip (age {int(age/60)}min)')
                return
        except Exception:
            pass

    lats, lons = [], []
    la = LAT0
    while la <= LAT1 + 1e-9:
        lo = LON0
        while lo <= LON1 + 1e-9:
            lats.append(la)
            lons.append(lo)
            lo += STEP
        la += STEP
    n = len(lats)
    n_lo = 0
    lo = LON0
    while lo <= LON1 + 1e-9:
        n_lo += 1
        lo += STEP
    n_la = n // n_lo

    results = [None] * n
    half = (n + 1) // 2
    for s, e in ((0, half), (half, n)):
        url = ('https://api.open-meteo.com/v1/gfs?latitude='
               + ','.join(str(x) for x in lats[s:e])
               + '&longitude=' + ','.join(str(x) for x in lons[s:e])
               + '&hourly=pressure_msl&forecast_days=1&timeformat=unixtime')
        arr = None
        for tryi in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'kansai-air-watch/1.0'})
                with urllib.request.urlopen(req, timeout=35) as r:
                    d = json.load(r)
                arr = d if isinstance(d, list) else [d]
                if arr and arr[0].get('hourly'):
                    break
                arr = None
            except Exception:
                arr = None
            time.sleep(8 * (tryi + 1))
        if not arr:
            raise SystemExit('Open-Meteo取得失敗(レート制限の可能性)')
        for k, v in enumerate(arr):
            results[s + k] = v

    now = time.time()
    t_used = None
    P = [[None] * n_lo for _ in range(n_la)]
    for k in range(n):
        h = results[k].get('hourly') if results[k] else None
        if not h:
            continue
        ti = 0
        for t in range(len(h['time'])):
            if h['time'][t] <= now:
                ti = t
        t_used = h['time'][ti]
        v = h['pressure_msl'][ti]
        P[k // n_lo][k % n_lo] = round(v, 1) if v is not None else None

    out = {'fetchedAt': datetime.now(JST).isoformat(timespec='seconds'),
           'fetchedAtUnix': int(now), 'tUsed': t_used,
           'lat0': LAT0, 'lon0': LON0, 'step': STEP,
           'nLa': n_la, 'nLo': n_lo, 'P': P}
    with open(OUT, 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    print(f'ok {n_la}x{n_lo} tUsed={t_used} bytes={os.path.getsize(OUT)}')


if __name__ == '__main__':
    main()
