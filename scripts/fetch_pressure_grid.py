#!/usr/bin/env python3
"""Open-Meteo(GFS)の海面更正気圧グリッドを取得してpressure_grid.jsonを出力。

- 既存のpressure_grid.jsonが170分以内ならスキップ(3時間毎相当)。Open-Meteo無料枠(1日1万コール、地点数で加算)を超えないよう
  2026-08-18 に 1.25°/毎時/hourly24値 → 1.5°/3時間毎/current のみ に軽量化(1218地点×24/時 → 875地点×1/3時間)
- ブラウザから直接Open-Meteoを叩かずに済むよう、statusブランチ経由で配信する
"""
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
OUT = 'pressure_grid.json'
LAT0, LAT1, LON0, LON1, STEP = 16.0, 52.0, 112.0, 164.0, 1.5


def main():
    # 鮮度チェック(50分以内ならスキップ)
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
            age = time.time() - prev.get('fetchedAtUnix', 0)
            if age < 170 * 60:
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
               + '&current=pressure_msl&timeformat=unixtime')
        arr = None
        for tryi in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'kansai-air-watch/1.0'})
                with urllib.request.urlopen(req, timeout=35) as r:
                    d = json.load(r)
                arr = d if isinstance(d, list) else [d]
                if arr and arr[0].get('current'):
                    break
                arr = None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise SystemExit('Open-Meteo 429(1日の上限超過)。前回ファイルを維持')
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
        c = results[k].get('current') if results[k] else None
        if not c:
            continue
        t_used = c.get('time')
        v = c.get('pressure_msl')
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
