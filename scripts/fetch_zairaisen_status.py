#!/usr/bin/env python3
"""JR6社(＋可能なら私鉄)の在来線運行情報を集約してzairaisen_status.jsonを出力。

出力:
{updated, ok: [取得成功した会社コード], lines: {"jre:山手線": {st,text}, "pvt:京王線": {...}},
 errors: [...]}
st: info/delay/suspend のみ記録(平常の路線は載せない。会社がokなら未記載=平常)
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
SEVERITY = {'normal': 0, 'info': 1, 'delay': 2, 'suspend': 3}

# 愛称・通称 → N02の正式路線名(近似)
ALIAS = {
    '琵琶湖線': '東海道線', 'JR京都線': '東海道線', 'JR神戸線': '山陽線',
    'JR宝塚線': '福知山線', '学研都市線': '片町線', '大和路線': '関西線',
    'JRゆめ咲線': '桜島線', '嵯峨野線': '山陰線', 'きのくに線': '紀勢線',
    '万葉まほろば線': '桜井線', '瀬戸大橋線': '本四備讃線', '宇野みなと線': '宇野線',
    'サンライナー': '山陽線', 'ゆめ咲線': '桜島線',
    '京浜東北線': '東北線', '宇都宮線': '東北線', '埼京線': '東北線',
    '湘南新宿ライン': '東北線', '京葉線': '京葉線', '中央総武線各駅停車': '総武線',
    '中央・総武各駅停車': '総武線', '成田エクスプレス': '成田線',
    '学園都市線': '札沼線', '函館・千歳線': '函館線',
}


def norm(name):
    n = re.sub(r'[\s　]', '', name or '')
    n = re.sub(r'（[^）]*）|\([^)]*\)', '', n)
    n = re.sub(r'^(ＪＲ|JR)', 'JR', n)
    n = ALIAS.get(n, n)
    n = re.sub(r'本線$', '線', n)
    return n


def classify(text):
    if re.search(r'見合わせ|見合せ|取り止め|取りやめ', text or ''):
        return 'suspend'
    if re.search(r'遅れ|遅延|運休', text or ''):
        return 'delay'
    return 'info'


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


SEC_RE = re.compile(r'([一-龥ぁ-んァ-ヶーA-Za-z]{2,10})\s*[～〜~]\s*([一-龥ぁ-んァ-ヶーA-Za-z]{2,10})')


def extract_sections(text, default_st=None):
    """本文から「A～B(駅間)」を抽出。区間直後の文脈で個別に状態判定"""
    out = []
    seen = set()
    for m in SEC_RE.finditer(text or ''):
        a = re.split(r'駅', m.group(1))[-1] if '駅' in m.group(1) else m.group(1)
        b = re.split(r'駅', m.group(2))[0]
        b = re.sub(r'間$', '', b)
        if (a, b) in seen:
            continue
        seen.add((a, b))
        ctx = (text[m.end():].split('。', 1)[0])[:80]
        if re.search(r'見合わせ|見合せ|取り止め|取りやめ|運休', ctx):
            st = 'suspend'
        elif re.search(r'遅れ|遅延|徐行', ctx):
            st = 'delay'
        else:
            st = default_st or classify(text)
        out.append({'f': a, 't': b, 'st': st})
    return out


def put(lines, key, st, text, sec=None):
    cur = lines.get(key)
    if cur is None:
        lines[key] = {'st': st, 'text': (text or '')[:200]}
        if sec:
            lines[key]['sec'] = sec
        return
    # 区間は常にマージ(同一路線の複数発表: 例 北上線の遅延行+見合わせ行)
    if sec:
        old = cur.get('sec') or []
        seen = {(s['f'], s['t']) for s in old}
        cur['sec'] = old + [s for s in sec if (s['f'], s['t']) not in seen]
    if SEVERITY[st] > SEVERITY[cur['st']]:
        cur['st'] = st
        cur['text'] = (text or '')[:200]
    # 路線の状態は区間の最悪値まで引き上げる(パネルの色と整合)
    for sc in cur.get('sec') or []:
        if SEVERITY.get(sc['st'], 0) > SEVERITY[cur['st']]:
            cur['st'] = sc['st']


def jr_east(lines):
    for page in ('kanto', 'tohoku', 'shinetsu'):
        html = fetch(f'https://traininfo.jreast.co.jp/train_info/{page}.aspx').decode('utf-8', 'replace')
        for block in html.split('traininfo-routes__table__item')[1:]:
            m = re.search(r'traininfo-routes__name">([^<]+)</span>', block)
            s = re.search(r'traininfo-routes__status\s*([\w-]*)"?>\s*(?:<[^>]+>\s*)*<span>([^<]+)</span>', block)
            if not m or not s:
                continue
            name, cls, text = m.group(1).strip(), s.group(1), s.group(2).strip()
            if '平常' in text or 'normal' in cls:
                continue
            note = re.search(r'traininfo-routes__note">([^<]+)<', block)
            note_t = note.group(1).strip() if note else ''
            st = classify(f'{text} {note_t}')
            put(lines, 'jre:' + norm(name), st,
                f'{name}: {note_t or text}', extract_sections(note_t, st))


def jr_west(lines):
    d = json.loads(fetch('https://trafficinfo.westjr.co.jp/api/v1/trafficinfo.json'))
    today = datetime.now(JST).strftime('%Y-%m-%d')
    for area in d.get('areaTrafficInfos', []):
        for dd in area.get('dailyData') or []:
            if dd.get('date') and dd['date'] != today:
                continue
            for pti in dd.get('placeTrafficInfos') or []:
                for li in pti.get('conventionalLineTrafficInfos') or []:
                    ln = li.get('lineName') or ''
                    conds = []
                    secs = []
                    for det in li.get('conventionalLineTrafficInfoDetails') or []:
                        c = det.get('conditionName') or ''
                        z = det.get('cause') or ''
                        conds.append(f'{c}({z})' if z else c)
                        for sec in det.get('sections') or []:
                            a, b = sec.get('startStation'), sec.get('endStation')
                            if a and b:
                                secs.append({'f': a, 't': b,
                                             'st': classify(sec.get('conditionName') or c)})
                    if conds:
                        blob = ' / '.join(dict.fromkeys(conds))
                        put(lines, 'jrw:' + norm(ln), classify(blob), f'{ln}: {blob}', secs)


def jr_central(lines):
    d = json.loads(fetch('https://traininfo.jr-central.co.jp/zairaisen/data/trainInfo/json/unkou.json').decode('utf-8-sig'))
    for mi in d.get('message_info') or []:
        name = next((t.get('name') for t in mi.get('trainline') or [] if t.get('lang') == 'ja'), '')
        msg = next((t.get('message') for t in mi.get('delivery_msg') or [] if t.get('lang') == 'ja'), '')
        if name and name.endswith('線'):
            st = classify(msg)
            put(lines, 'jrc:' + norm(name), st, f'{name}: {msg}', extract_sections(msg, st))


def jr_kyushu(lines):
    xml = fetch('https://www.jrkyushu.co.jp/trains/info/data/IDS2Web.xml')
    root = ET.fromstring(xml)
    for aif in root.find('info').findall('aif'):
        if '新幹線' in (aif.findtext('nm') or ''.join(
                e.find('lin').findtext('nm') or '' for e in aif.findall('eif'))):
            pass  # 新幹線エリアの中身も lin 名で判定するので特別扱い不要
        for e in aif.findall('eif'):
            ln = e.find('lin').findtext('nm') if e.find('lin') is not None else ''
            if not ln or '新幹線' in ln:
                continue
            txt = (e.findtext('txt') or '').strip()
            st = classify(txt)
            put(lines, 'jrq:' + norm(ln), st,
                f'{ln}: {txt.splitlines()[0][:80] if txt else "運行情報あり"}',
                extract_sections(txt, st))


JRH_SENKU_LINES = {
    '03': ['函館線', '千歳線'], '04': ['札沼線'], '07': ['函館線', '千歳線'],
    '08': ['札沼線'], '09': ['室蘭線'], '10': ['日高線'], '13': ['函館線'],
    '15': ['宗谷線'], '16': ['石北線'], '17': ['富良野線'], '20': ['石勝線'],
    '21': ['根室線'], '22': ['根室線'], '23': ['釧網線'],
}


def jr_hokkaido(lines):
    for senku, lnames in JRH_SENKU_LINES.items():
        try:
            d = json.loads(fetch(
                f'https://www3.jrhokkaido.co.jp/webunkou/json/senku/senku_{senku}.json',
                timeout=15).decode('utf-8-sig'))
        except Exception:
            continue
        today = d.get('today', {})
        unkyu = today.get('unkyuTrains') or []
        chien = today.get('chienTrains') or []
        gaikyo = ' '.join(g.get('honbun', '') for g in today.get('gaikyo') or [])
        if re.search(r'情報はありません', gaikyo):
            gaikyo = ''
        st = None
        if re.search(r'見合わせ|見合せ', gaikyo):
            st = 'suspend'
        elif unkyu or chien or re.search(r'遅れ|遅延|運休', gaikyo):
            st = 'delay'
        if st:
            parts = []
            if unkyu:
                parts.append(f'運休{len(unkyu)}本')
            if chien:
                parts.append(f'遅延{len(chien)}本')
            for ln in lnames:
                put(lines, 'jrh:' + norm(ln), st,
                    f'{ln}: {" ".join(parts) or gaikyo.strip()[:80]}')


def jr_shikoku(lines):
    html = fetch('https://www.jr-shikoku.co.jp/info/').decode('utf-8', 'replace')
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.S)
    t = re.sub(r'<[^>]+>', '\n', t)
    body = '\n'.join(l.strip() for l in t.split('\n') if l.strip())
    if '遅れ等の情報はありません' in body:
        return
    # 運行情報の本文から路線名を拾う
    for ln in set(re.findall(r'([一-龥ぁ-んァ-ヶ]{1,6}線)', body)):
        if ln in ('新幹線',):
            continue
        put(lines, 'jrs:' + norm(ln), classify(body), f'{ln}: 運行情報あり(JR四国サイト参照)')


def private_rti(lines):
    """鉄道遅延情報のjson(取得できる環境でのみ)。掲載=遅延あり"""
    d = json.loads(fetch('https://tetsudo.rti-giken.jp/free/delay.json', timeout=10))
    for e in d:
        name = e.get('name') or ''
        comp = e.get('company') or ''
        if not name:
            continue
        if re.search(r'^JR|旅客鉄道', comp):
            continue  # JRは各社ソースで把握済み
        put(lines, 'pvt:' + norm(name), 'delay', f'{comp}{name}: 遅延情報あり')
    return True


def main():
    lines = {}
    ok = []
    errors = []
    for co, fn in (('jre', jr_east), ('jrw', jr_west), ('jrc', jr_central),
                   ('jrq', jr_kyushu), ('jrh', jr_hokkaido), ('jrs', jr_shikoku)):
        try:
            fn(lines)
            ok.append(co)
        except Exception as e:
            errors.append(f'{co}: {type(e).__name__}: {e}')
    try:
        private_rti(lines)
        ok.append('pvt')
    except Exception as e:
        errors.append(f'pvt: {type(e).__name__}: {e}')

    out = {'updated': datetime.now(JST).isoformat(timespec='seconds'),
           'ok': ok, 'lines': lines, 'errors': errors}
    with open('zairaisen_status.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'掲載: {len(lines)}路線 / ok: {ok} / errors: {errors}', file=sys.stderr)


if __name__ == '__main__':
    main()
