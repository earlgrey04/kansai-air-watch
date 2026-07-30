#!/usr/bin/env python3
"""全国の新幹線運行情報をJR5社の公開データから取得し、正規化JSONを出力する。

出力: shinkansen_status.json
  status: normal(平常) / info(お知らせ・運転変更) / delay(遅れ) /
          suspend(運転見合わせ) / unknown(取得失敗)

標準ライブラリのみ使用(GitHub Actionsでそのまま動く)。
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
UA = 'Mozilla/5.0 (compatible; shinkansen-status-map; +https://earlgrey04.github.io/kansai-air-watch/)'

SEVERITY = {'unknown': -1, 'normal': 0, 'info': 1, 'delay': 2, 'suspend': 3}
LABELS = {'normal': '平常運転', 'info': 'お知らせあり', 'delay': '遅れあり',
          'suspend': '運転見合わせ', 'unknown': '情報取得失敗'}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def classify(text):
    """本文キーワードからステータスを推定"""
    if re.search(r'見合わせ|見合せ', text):
        return 'suspend'
    if re.search(r'遅れ|遅延', text):
        return 'delay'
    return 'info'


def jr_east():
    """東北・山形・秋田・上越・北陸(JR東区間)"""
    html = fetch('https://traininfo.jreast.co.jp/train_info/shinkansen.aspx').decode('utf-8', 'replace')
    items = re.findall(
        r'traininfo-routes__name">([^<]+)</span>.*?traininfo-routes__status\s*([\w-]*)"?>\s*(?:<[^>]+>\s*)*<span>([^<]+)</span>',
        html, re.S)
    name2key = {'東北新幹線': 'tohoku', '山形新幹線': 'yamagata', '秋田新幹線': 'akita',
                '上越新幹線': 'joetsu', '北陸新幹線': 'hokuriku_east'}
    out = {}
    for name, cls, text in items:
        key = name2key.get(name.strip())
        if not key:
            continue
        text = text.strip()
        if '平常' in text:
            st = 'normal'
        elif 'normal' in cls:
            st = 'normal'
        else:
            st = classify(text)
        out[key] = {'status': st, 'text': text}
    if not out:
        raise ValueError('JR東: 路線ステータスをパースできず')
    return out


def jr_central():
    """東海道新幹線"""
    d = json.loads(fetch('https://traininfo.jr-central.co.jp/shinkansen/var/train_info/service_status.json'))
    info = d.get('serviceStatusInfo', {})
    data = info.get('data') or []
    if not info.get('serviceStatusIsEnabled') and not data:
        return {'tokaido': {'status': 'normal', 'text': '平常運転'}}
    texts = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            texts.append(o)
    walk(data)
    blob = ' '.join(texts) or '運転情報あり'
    return {'tokaido': {'status': classify(blob), 'text': blob[:120]}}


def jr_west():
    """山陽新幹線・北陸新幹線(JR西区間)"""
    d = json.loads(fetch('https://trafficinfo.westjr.co.jp/api/v1/trafficinfo.json'))
    today = datetime.now(JST).strftime('%Y-%m-%d')
    out = {}
    for area_id, key in ((4, 'sanyo'), (5, 'hokuriku_west')):
        area = next((a for a in d.get('areaTrafficInfos', []) if a.get('id') == area_id), None)
        if area is None:
            out[key] = {'status': 'normal', 'text': '平常運転'}
            continue
        conds = []
        for dd in area.get('dailyData') or []:
            if dd.get('date') and dd['date'] != today:
                continue
            for pti in dd.get('placeTrafficInfos') or []:
                for sti in pti.get('shinkansenTrafficInfos') or []:
                    for det in sti.get('shinkansenTrafficInfoDetails') or []:
                        cond = det.get('conditionName') or ''
                        cause = det.get('cause') or ''
                        conds.append((cond, cause))
        if not conds:
            out[key] = {'status': 'normal', 'text': '平常運転'}
        else:
            worst = max((classify(c) for c, _ in conds), key=lambda s: SEVERITY[s])
            txt = ' / '.join(dict.fromkeys(
                f'{c}({z})' if z else c for c, z in conds))[:120]
            out[key] = {'status': worst, 'text': txt}
    return out


def jr_kyushu():
    """九州新幹線・西九州新幹線"""
    xml = fetch('https://www.jrkyushu.co.jp/trains/info/data/IDS2Web.xml')
    root = ET.fromstring(xml)
    out = {}
    name2key = {'Kyushu-Shinkansen': 'kyushu', 'Nishi-Kyushu-Shinkansen': 'nishikyushu'}
    for aif in root.find('info').findall('aif'):
        key = name2key.get(aif.findtext('nm', ''))
        if not key:
            continue
        sts = (aif.findtext('sts') or '0').strip()
        if sts == '0':
            out[key] = {'status': 'normal', 'text': '平常運転'}
        else:
            txts = [e.findtext('txt') or '' for e in aif.findall('eif')]
            blob = ' '.join(txts)
            out[key] = {'status': classify(blob),
                        'text': (txts[0].split('\n')[0] if txts else '運行情報あり')[:120]}
    for key in name2key.values():
        out.setdefault(key, {'status': 'unknown', 'text': LABELS['unknown']})
    return out


def jr_hokkaido():
    """北海道新幹線"""
    d = json.loads(fetch('https://www3.jrhokkaido.co.jp/webunkou/json/senku/senku_24.json').decode('utf-8-sig'))
    today = d.get('today', {})
    shin = today.get('areaStatus', {}).get('shin', 0)
    unkyu = today.get('unkyuTrains') or []
    chien = today.get('chienTrains') or []
    gaikyo = ' '.join(g.get('honbun', '') for g in today.get('gaikyo') or [])
    if shin == 0 and not unkyu and not chien:
        return {'hokkaido': {'status': 'normal', 'text': '平常運転'}}
    parts = []
    if unkyu:
        parts.append(f'運休 {len(unkyu)}本')
    if chien:
        parts.append(f'遅延 {len(chien)}本')
    blob = gaikyo + ' ' + ' '.join(parts)
    st = 'suspend' if re.search(r'見合わせ|見合せ', gaikyo) else \
         ('delay' if chien or re.search(r'遅れ|遅延', gaikyo) else 'info')
    return {'hokkaido': {'status': st, 'text': (' '.join(parts) or gaikyo.strip())[:120]}}


def main():
    sources = [
        ('JR東日本', jr_east),
        ('JR東海', jr_central),
        ('JR西日本', jr_west),
        ('JR九州', jr_kyushu),
        ('JR北海道', jr_hokkaido),
    ]
    raw = {}
    errors = []
    for label, fn in sources:
        try:
            for k, v in fn().items():
                v['source'] = label
                raw[k] = v
        except Exception as e:
            errors.append(f'{label}: {type(e).__name__}: {e}')

    # 北陸新幹線はJR東・JR西の悪い方を採用
    east = raw.pop('hokuriku_east', None)
    west = raw.pop('hokuriku_west', None)
    if east or west:
        cands = [x for x in (east, west) if x]
        worst = max(cands, key=lambda x: SEVERITY[x['status']])
        detail = ' / '.join(
            f"{lbl}区間: {x['text']}" for lbl, x in
            (('JR東日本', east), ('JR西日本', west)) if x)
        raw['hokuriku'] = {'status': worst['status'], 'text': detail[:160],
                           'source': 'JR東日本・JR西日本'}

    all_keys = ['hokkaido', 'tohoku', 'yamagata', 'akita', 'joetsu',
                'hokuriku', 'tokaido', 'sanyo', 'kyushu', 'nishikyushu']
    lines = {}
    for k in all_keys:
        v = raw.get(k, {'status': 'unknown', 'text': LABELS['unknown'], 'source': ''})
        v['label'] = LABELS[v['status']]
        lines[k] = v

    out = {'updated': datetime.now(JST).isoformat(timespec='seconds'),
           'lines': lines, 'errors': errors}
    with open('shinkansen_status.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if errors:
        print('WARN:', errors, file=sys.stderr)


if __name__ == '__main__':
    main()
