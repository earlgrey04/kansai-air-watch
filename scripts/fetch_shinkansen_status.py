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
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.8,en;q=0.6',
}
# 取得失敗時に前回値を引き継ぐ最大時間
INHERIT_MAX_AGE = timedelta(hours=3)

SEVERITY = {'unknown': -1, 'normal': 0, 'info': 1, 'delay': 2, 'suspend': 3}
LABELS = {'normal': '平常運転', 'info': 'お知らせあり', 'delay': '遅れあり',
          'suspend': '運転見合わせ・運休あり', 'unknown': '情報取得失敗'}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def classify(text):
    """本文キーワードからステータスを推定"""
    if re.search(r'見合わせ|見合せ|運行取り止め|運転取り止め|運行取りやめ|運転取りやめ', text):
        return 'suspend'
    if re.search(r'遅れ|遅延', text):
        return 'delay'
    return 'info'


SEC_RE = re.compile(r'([一-龥ぁ-んァ-ヶーA-Za-z]{2,10})\s*[～〜~]\s*([一-龥ぁ-んァ-ヶーA-Za-z]{2,10})')


def extract_sections(text, status=None):
    """本文から「A～B」区間を抽出(駅名の妥当性はフロント側で路線の駅リストと照合)。

    区間ごとの状態は直後の文脈(行末まで)から判定する。
    例:「筑後船小屋～熊本：終日運行取り止め」→ suspend、
    　 「博多～筑後船小屋：本数を減らして運転」→ info
    """
    out = []
    seen = set()
    for m in SEC_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        a = re.sub(r'駅$', '', a)
        b = re.sub(r'駅間?$', '', b)
        if (a, b) in seen:
            continue
        seen.add((a, b))
        ctx = text[m.end():].split('\n', 1)[0][:60]
        if re.search(r'見合わせ|見合せ|取り止め|取りやめ|運休', ctx):
            sec_st = 'suspend'
        elif re.search(r'遅れ|遅延', ctx):
            sec_st = 'delay'
        elif re.search(r'減らして|減便|徐行|折り返し|折返し', ctx):
            sec_st = 'info'
        else:
            sec_st = status or classify(text)
        out.append({'from': a, 'to': b, 'status': sec_st})
    return out


def strip_tags(html):
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.S)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', html)
    return '\n'.join(l.strip() for l in text.split('\n') if l.strip())


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
        if st != 'normal':
            out[key]['sections'] = extract_sections(text)
            out[key]['detail'] = text
    if not out:
        raise ValueError('JR東: 路線ステータスをパースできず')
    # 運休情報セクション(計画運休はここに掲載される)。「ありません」以外なら別枠へ
    i = html.find('新幹線の運休情報')
    if i >= 0:
        block = strip_tags(html[i:i + 4000])
        # セクション見出し以降、次の定型文までを抜く
        m = re.search(r'新幹線の運休情報\n(.*?)(?:\n4時～翌2時|\nの間、|\n最新情報を|$)',
                      block, re.S)
        body = (m.group(1).strip() if m else '')
        body = re.sub(r'^新幹線の運休情報\n?', '', body)
        if body and '運休情報はありません' not in body:
            out['_tomorrow'] = [{'name': 'JR東日本の新幹線 運休情報', 'line': '',
                                 'status': 'suspend', 'text': body[:600],
                                 'source': 'JR東日本'}]
    return out




ODPT_SHINK_KEYS = {'東北新幹線': 'tohoku', '山形新幹線': 'yamagata', '秋田新幹線': 'akita',
                   '上越新幹線': 'joetsu', '北陸新幹線': 'hokuriku_east'}
ODPT_SHINK_JA = {"JR-Central.TokaidoShinkansen": "東海道新幹線", "JR-East.AkitaShinkansen": "秋田新幹線", "JR-East.HokurikuShinkansen": "北陸新幹線", "JR-East.JoetsuShinkansen": "上越新幹線", "JR-East.TohokuShinkansen": "東北新幹線", "JR-East.YamagataShinkansen": "山形新幹線"}


def jr_east_odpt(lines_out):
    """JR東HTMLが403の環境向け: ODPTチャレンジAPIから新幹線運行情報"""
    import os
    token = os.environ.get('ODPT_CHALLENGE_TOKEN', '')
    if not token:
        raise RuntimeError('no ODPT_CHALLENGE_TOKEN')
    raw = fetch('https://api-challenge.odpt.org/api/v4/odpt:TrainInformation'
                f'?acl:consumerKey={token}', timeout=20)
    found = {}
    for e in json.loads(raw):
        rid = (e.get('odpt:railway') or '').replace('odpt.Railway:', '')
        key = ODPT_SHINK_KEYS.get(ODPT_SHINK_JA.get(rid, ''))
        if not key:
            continue
        text = ((e.get('odpt:trainInformationText') or {}).get('ja') or '').strip()
        if not text or re.search(r'平常|ありません|通常どおり|通常運転', text):
            found[key] = {'status': 'normal', 'text': '平常運転'}
        else:
            st = classify(text)
            found[key] = {'status': st, 'text': text[:160],
                          'sections': extract_sections(text, st)}
    if not found:
        raise RuntimeError('ODPTに新幹線情報なし')
    lines_out.update(found)
    return found


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
    st = classify(blob)
    return {'tokaido': {'status': st, 'text': blob[:120],
                        'sections': extract_sections(blob, st),
                        'detail': blob[:600]}}


def jr_west():
    """山陽新幹線・北陸新幹線(JR西区間)。翌日以降の計画情報はdailyDataの日付枠から拾う"""
    d = json.loads(fetch('https://trafficinfo.westjr.co.jp/api/v1/trafficinfo.json'))
    today = datetime.now(JST).strftime('%Y-%m-%d')
    out = {}
    tmr_items = []
    for area_id, key, lname in ((4, 'sanyo', '山陽新幹線'), (5, 'hokuriku_west', '北陸新幹線')):
        area = next((a for a in d.get('areaTrafficInfos', []) if a.get('id') == area_id), None)
        if area is None:
            continue
        for dd in area.get('dailyData') or []:
            date = dd.get('date') or ''
            if not date or date <= today:
                continue
            conds, sec_strs = [], []
            for pti in dd.get('placeTrafficInfos') or []:
                for sti in pti.get('shinkansenTrafficInfos') or []:
                    for det in sti.get('shinkansenTrafficInfoDetails') or []:
                        cond = det.get('conditionName') or ''
                        cause = det.get('cause') or ''
                        conds.append((cond, cause))
                        for sec in det.get('sections') or []:
                            a, b = sec.get('startStation'), sec.get('endStation')
                            if a and b:
                                sec_strs.append(f'{a}〜{b}')
            if conds:
                worst = max((classify(c) for c, _ in conds), key=lambda s: SEVERITY[s])
                txt = ' / '.join(dict.fromkeys(
                    f'{c}({z})' if z else c for c, z in conds))
                if sec_strs:
                    txt += ' 区間: ' + '、'.join(dict.fromkeys(sec_strs))
                md = date[5:].replace('-', '/')
                tmr_items.append({'name': f'{lname}（{md}）', 'line': key,
                                  'status': worst, 'text': txt[:300],
                                  'source': 'JR西日本'})
    if tmr_items:
        out['_tomorrow'] = tmr_items
    for area_id, key in ((4, 'sanyo'), (5, 'hokuriku_west')):
        area = next((a for a in d.get('areaTrafficInfos', []) if a.get('id') == area_id), None)
        if area is None:
            out[key] = {'status': 'normal', 'text': '平常運転'}
            continue
        conds = []
        secs = []
        details = []
        for dd in area.get('dailyData') or []:
            if dd.get('date') and dd['date'] != today:
                continue
            for pti in dd.get('placeTrafficInfos') or []:
                for sti in pti.get('shinkansenTrafficInfos') or []:
                    for det in sti.get('shinkansenTrafficInfoDetails') or []:
                        cond = det.get('conditionName') or ''
                        cause = det.get('cause') or ''
                        conds.append((cond, cause))
                        sec_strs = []
                        for sec in det.get('sections') or []:
                            a = sec.get('startStation')
                            b = sec.get('endStation')
                            if a and b:
                                secs.append({'from': a, 'to': b,
                                             'status': classify(sec.get('conditionName') or cond)})
                                ud = sec.get('upAndDown') or ''
                                sec_strs.append(f'{a}〜{b}{"("+ud+")" if ud else ""}')
                        # 発表タイトル(versionDetailの先頭)＋補足を詳細文に
                        vtitle = ''
                        for vd in det.get('versionDetail') or []:
                            if vd.get('title'):
                                vtitle = vd['title']
                                break
                        parts = [p for p in (
                            f'【{cond}】' if cond else '',
                            vtitle or (det.get('supplementary') or ''),
                            f'原因: {cause}' if cause else '',
                            f'区間: {"、".join(sec_strs)}' if sec_strs else '') if p]
                        if parts:
                            details.append(' '.join(parts))
        if not conds:
            out[key] = {'status': 'normal', 'text': '平常運転'}
        else:
            worst = max((classify(c) for c, _ in conds), key=lambda s: SEVERITY[s])
            txt = ' / '.join(dict.fromkeys(
                f'{c}({z})' if z else c for c, z in conds))[:120]
            out[key] = {'status': worst, 'text': txt, 'sections': secs,
                        'detail': '\n'.join(dict.fromkeys(details))[:600]}
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
            st = classify(blob)
            out[key] = {'status': st,
                        'text': (txts[0].split('\n')[0] if txts else '運行情報あり')[:120],
                        'sections': extract_sections(blob, st),
                        'detail': '\n\n'.join(t.strip() for t in txts if t.strip())[:800]}
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
    out = {'hokkaido': {'status': st, 'text': (' '.join(parts) or gaikyo.strip())[:120],
                        'sections': extract_sections(gaikyo, st),
                        'detail': (gaikyo.strip() + ('\n' + ' '.join(parts) if parts else ''))[:600]}}
    # 翌日ブロック(計画運休の予告はここに載る)
    tm = d.get('tomorrow') or {}
    tshin = tm.get('areaStatus', {}).get('shin', 0)
    tunkyu = tm.get('unkyuTrains') or []
    tgaikyo = ' '.join(g.get('honbun', '') for g in tm.get('gaikyo') or []).strip()
    if tshin != 0 or tunkyu or (tgaikyo and '情報はありません' not in tgaikyo):
        txt = tgaikyo or (f'運休予定 {len(tunkyu)}本' if tunkyu else '運行情報あり')
        out['_tomorrow'] = [{'name': f'北海道新幹線（{tm.get("dateText", "翌日")}）',
                             'line': 'hokkaido', 'status': classify(txt),
                             'text': txt[:300], 'source': 'JR北海道'}]
    return out


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
    tomorrow_items = []
    for label, fn in sources:
        try:
            res = fn()
            tomorrow_items.extend(res.pop('_tomorrow', []))
            for k, v in res.items():
                v['source'] = label
                raw[k] = v
        except Exception as e:
            errors.append(f'{label}: {type(e).__name__}: {e}')
            if label == 'JR東日本':
                try:
                    fb = {}
                    jr_east_odpt(fb)
                    for k, v in fb.items():
                        v['source'] = 'JR東日本(ODPT)'
                        raw[k] = v
                    errors[-1] += ' → ODPTで代替取得'
                except Exception as e2:
                    errors.append(f'jre-odpt: {type(e2).__name__}: {e2}')

    # 北陸新幹線はJR東・JR西の悪い方を採用
    east = raw.pop('hokuriku_east', None)
    west = raw.pop('hokuriku_west', None)
    if east or west:
        cands = [x for x in (east, west) if x]
        worst = max(cands, key=lambda x: SEVERITY[x['status']])
        detail = ' / '.join(
            f"{lbl}区間: {x['text']}" for lbl, x in
            (('JR東日本', east), ('JR西日本', west)) if x)
        secs = [s for x in cands for s in x.get('sections') or []]
        raw['hokuriku'] = {'status': worst['status'], 'text': detail[:160],
                           'source': 'JR東日本・JR西日本'}
        if secs:
            raw['hokuriku']['sections'] = secs
        dets = [f"《{lbl}区間》{x['detail']}" for lbl, x in
                (('JR東日本', east), ('JR西日本', west)) if x and x.get('detail')]
        if dets:
            raw['hokuriku']['detail'] = '\n'.join(dets)[:800]

    now = datetime.now(JST)

    # 前回値(あれば)を読み、今回unknownの路線は一定時間まで引き継ぐ
    prev_lines = {}
    try:
        with open('shinkansen_status.json', encoding='utf-8') as f:
            prev_lines = json.load(f).get('lines', {})
    except Exception:
        pass

    all_keys = ['hokkaido', 'tohoku', 'yamagata', 'akita', 'joetsu',
                'hokuriku', 'tokaido', 'sanyo', 'kyushu', 'nishikyushu']
    lines = {}
    for k in all_keys:
        v = raw.get(k)
        if v:
            v['asOf'] = now.isoformat(timespec='seconds')
        else:
            p = prev_lines.get(k)
            ok = False
            if p and p.get('status') not in (None, 'unknown') and p.get('asOf'):
                try:
                    age = now - datetime.fromisoformat(p['asOf'])
                    ok = age <= INHERIT_MAX_AGE
                except ValueError:
                    ok = False
            if ok:
                v = dict(p)
            else:
                v = {'status': 'unknown', 'text': LABELS['unknown'],
                     'source': '', 'asOf': now.isoformat(timespec='seconds')}
        v['label'] = LABELS[v['status']]
        lines[k] = v

    out = {'updated': now.isoformat(timespec='seconds'),
           'lines': lines,
           'tomorrow': {'items': tomorrow_items},
           'errors': errors}
    with open('shinkansen_status.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if errors:
        print('WARN:', errors, file=sys.stderr)


if __name__ == '__main__':
    main()
