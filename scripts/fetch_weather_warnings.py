#!/usr/bin/env python3
"""気象庁の全国警報・注意報(bosai r8 map.json)を集約してweather_warnings.jsonを出力。

- 官署(≒都道府県)ごとの最新報のみ採用
- munis: 市町村等コード -> {lv: 最大レベル, k: 有効な警報コード一覧}
- offices: 官署コード -> {lv: 管内最大レベル}
レベル: 20=注意報 30=警報 40=危険警報 50=特別警報 (令和8年制度)
"""
import json
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
URL = 'https://www.jma.go.jp/bosai/warning/data/r8/map.json'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

# 警報コード -> レベル (気象庁 warningページのコード表より)
CODE_LEVEL = {
    '33': 50, '43': 40, '03': 30, '10': 20,          # 大雨
    '39': 50, '49': 40, '09': 30, '29': 20,          # 土砂災害
    '38': 50, '48': 40, '08': 30, '19': 20,          # 高潮
    '35': 50, '05': 30, '15': 20,                    # 暴風/強風
    '32': 50, '02': 30, '13': 20,                    # 暴風雪/風雪
    '36': 50, '06': 30, '12': 20,                    # 大雪
    '37': 50, '07': 30, '16': 20,                    # 波浪
    '53': 50, '51': 50, '41': 40, '40': 40, '31': 30, '30': 30,  # 洪水
    '14': 20, '17': 20, '20': 20, '21': 20, '22': 20,
    '23': 20, '24': 20, '25': 20, '26': 20, '18': 20, '04': 30,
}


def main():
    req = urllib.request.Request(URL, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        reports = json.load(r)

    latest = {}
    for rep in reports:
        off = rep.get('publishingOffice')
        if off and (off not in latest
                    or rep.get('reportDatetime', '') > latest[off].get('reportDatetime', '')):
            latest[off] = rep

    munis = {}
    newest = ''
    for rep in latest.values():
        newest = max(newest, rep.get('reportDatetime', ''))
        for item in rep.get('warning', {}).get('class20Items', []):
            code = item.get('areaCode')
            if not code:
                continue
            ks = [k.get('code') for k in item.get('kinds', [])
                  if k.get('status') in ('発表', '継続') and k.get('code')]
            lv = max((CODE_LEVEL.get(k, 20) for k in ks), default=0)
            if lv:
                munis[code] = {'lv': lv, 'k': ks}

    out = {'updated': datetime.now(JST).isoformat(timespec='seconds'),
           'reportDatetime': newest,
           'munis': munis}
    with open('weather_warnings.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'munis with warnings: {len(munis)} / newest report: {newest}')


if __name__ == '__main__':
    main()
