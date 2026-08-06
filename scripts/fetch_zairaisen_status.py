#!/usr/bin/env python3
"""JR6社(＋可能なら私鉄)の在来線運行情報を集約してzairaisen_status.jsonを出力。

出力:
{updated, ok: [取得成功した会社コード], lines: {"jre:山手線": {st,text}, "pvt:京王線": {...}},
 errors: [...]}
st: info/delay/suspend のみ記録(平常の路線は載せない。会社がokなら未記載=平常)
"""
import json
import os
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
    t = text or ''
    if re.search(r'見合わせ|見合せ|取り止め|取りやめ', t):
        # 「見合わせていましたが運転を再開」等の過去形は遅れ扱い
        if re.search(r'運転を再開', t) and not re.search(r'再開(?:予定|見込)', t):
            return 'delay'
        return 'suspend'
    if re.search(r'遅れ|遅延|運休', t):
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


PVT_SOURCES = [
    # key, 表示名, URL, タイプ, 平常判定regex, エンコーディング
    dict(key='hankyu', name='阪急電鉄',
         url='https://www.hankyu.co.jp/railinfo/include/page_railinfo.html',
         typ='html', normal=r'平常(?:通り|どおり)|遅れはございません|情報はありません'),
    dict(key='hanshin', name='阪神電気鉄道', url='https://rail.hanshin.co.jp/unkou/',
         typ='html', normal=r'遅れはございません'),
    dict(key='kintetsu', name='近畿日本鉄道', url='https://www.kintetsu.jp/unkou/unkou.html',
         typ='html', enc='cp932', normal=r'遅れはございません'),
    dict(key='nankai', name='南海電気鉄道',
         url='https://www.nankai.co.jp/api/v1/nocache/emergency_news', typ='nankai'),
    dict(key='osakametro', name='大阪メトロ',
         url='https://subway.osakametro.co.jp/guide/subway_information.php', typ='osakametro'),
    dict(key='keio', name='京王電鉄', url='https://www.keio.co.jp/unkou/unkou_pc.html',
         typ='html', normal=r'平常通り運転'),
    dict(key='keikyu', name='京浜急行電鉄', url='https://unkou.keikyu.co.jp/',
         typ='html', normal=r'平常通り運転'),
    dict(key='keisei', name='京成電鉄', url='https://www.keisei.co.jp/traininfo/index.php',
         typ='html', normal=r'平常(?:運行|どおり)'),
    dict(key='meitetsu', name='名古屋鉄道', url='https://top.meitetsu.co.jp/em/',
         typ='html', normal=r'遅れはございません'),
]
OSAKA_METRO_LINES = ['御堂筋線', '谷町線', '四つ橋線', '中央線', '千日前線', '堺筋線',
                     '長堀鶴見緑地線', '今里筋線', '南港ポートタウン線']


def _strip_html(t):
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', t, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'[\s\u3000]+', ' ', t)


def private_official(lines):
    """大手私鉄9社の公式運行情報を取得。会社単位＋可能なら路線単位で反映"""
    got = 0
    for src in PVT_SOURCES:
        try:
            raw = fetch(src['url'], timeout=15)
            if src['typ'] == 'nankai':
                items = json.loads(raw)
                if not items:
                    put(lines, f"pvtco:{src['key']}", 'normal', f"{src['name']}: 平常運転")
                else:
                    blob = ' '.join(str(i.get('title', '') or '') + str(i.get('body', '') or '')
                                    for i in items)[:300]
                    put(lines, f"pvtco:{src['key']}", classify(blob), f"{src['name']}: {blob[:160]}")
                got += 1
                continue
            enc = src.get('enc', 'utf-8')
            body = _strip_html(raw.decode(enc, errors='replace'))
            if src['typ'] == 'osakametro':
                worst = 'normal'
                texts = []
                for ln in OSAKA_METRO_LINES:
                    m = re.search(re.escape(ln) + r'.{0,120}?([◯○△×])', body)
                    if not m:
                        continue
                    st = {'◯': 'normal', '○': 'normal', '△': 'delay', '×': 'suspend'}[m.group(1)]
                    if st != 'normal':
                        put(lines, f"pvt:{src['key']}:{norm(ln)}", st,
                            f"大阪メトロ{ln}: " + ('遅延など' if st == 'delay' else '運転見合わせ'))
                        texts.append(f'{ln}={m.group(1)}')
                        if SEVERITY[st] > SEVERITY[worst]:
                            worst = st
                put(lines, f"pvtco:{src['key']}", worst,
                    f"{src['name']}: " + ('全線通常運行' if worst == 'normal' else ' '.join(texts)))
                got += 1
                continue
            # 汎用HTML: 平常マーカー or 異常本文
            if re.search(src['normal'], body):
                put(lines, f"pvtco:{src['key']}", 'normal', f"{src['name']}: 平常運転")
            else:
                m = re.search(r'.{0,60}(?:見合わせ|遅れ|遅延|運休|直通運転を中止).{0,120}', body)
                blob = (m.group(0).strip() if m else '運行情報あり(公式サイト参照)')[:180]
                st = classify(blob)
                put(lines, f"pvtco:{src['key']}", st, f"{src['name']}: {blob}")
                for lnm in set(re.findall(r'([一-龥ぁ-んァ-ヶー]{1,7}線)', blob)):
                    put(lines, f"pvt:{src['key']}:{norm(lnm)}", st, f"{src['name']}{lnm}: {blob[:120]}")
            got += 1
        except Exception:
            pass
    if not got:
        raise RuntimeError('全社取得失敗')
    return got



# ===== ODPT(公共交通オープンデータセンター) 運行情報 =====
# 無料トークンで取得可: 東京メトロ/都営/横浜市営/りんかい線/TX/多摩モノレール。
# 追加事業者(東急・東武等)はODPT側で利用申請が通れば自動的に取り込まれる。
ODPT_OP_KEY = {
    'TokyoMetro': ('tokyometro', '東京メトロ'), 'Toei': ('toei', '都営交通'),
    'YokohamaMunicipal': ('yokohama', '横浜市営地下鉄'), 'TWR': ('twr', 'りんかい線'),
    'MIR': ('mir', 'つくばエクスプレス'), 'TamaMonorail': ('tamamono', '多摩モノレール'),
    'Tokyu': ('tokyu', '東急電鉄'), 'Tobu': ('tobu', '東武鉄道'), 'Seibu': ('seibu', '西武鉄道'),
    'Odakyu': ('odakyu', '小田急電鉄'), 'Sotetsu': ('sotetsu', '相模鉄道'),
    'Keio': ('keio', '京王電鉄'), 'Keikyu': ('keikyu', '京浜急行電鉄'),
}
ODPT_RAILWAY_JA = {"Sotetsu.Izumino": "いずみ野線", "Sotetsu.Main": "相鉄本線", "Sotetsu.SotetsuShinYokohama": "相鉄新横浜線", "Yurikamome.Yurikamome": "ゆりかもめ", "TWR.Rinkai": "りんかい線", "Toei.Arakawa": "東京さくらトラム", "Toei.Asakusa": "浅草線", "Keikyu.Main": "京急本線", "Keikyu.Zushi": "逗子線", "TokyoMetro.Marunouchi": "丸ノ内線", "Odakyu.Enoshima": "江ノ島線", "Odakyu.Odawara": "小田原線", "Toei.Mita": "三田線", "Toei.NipporiToneri": "日暮里・舎人ライナー", "Toei.Oedo": "大江戸線", "Toei.Shinjuku": "新宿線", "MIR.TsukubaExpress": "つくばエクスプレス", "Odakyu.Tama": "多摩線", "TokyoMetro.Chiyoda": "千代田線", "Keikyu.Airport": "空港線", "Keikyu.Kurihama": "久里浜線", "Tobu.Isesaki": "伊勢崎線", "TokyoMetro.Namboku": "南北線", "Seibu.Haijima": "拝島線", "Seibu.Ikebukuro": "池袋線", "Seibu.Sayama": "狭山線", "Seibu.SeibuChichibu": "西武秩父線", "Seibu.SeibuYurakucho": "西武有楽町線", "TokyoMetro.Fukutoshin": "副都心線", "TokyoMetro.Ginza": "銀座線", "Seibu.Shinjuku": "新宿線", "Seibu.Toshima": "豊島線", "Tobu.Nikko": "日光線", "Tobu.TobuSkytreeBranch": "東武スカイツリーライン(押上-曳舟)", "Tobu.TobuSkytree": "東武スカイツリーライン", "Tobu.TobuUrbanPark": "東武アーバンパークライン", "Tobu.Tojo": "東上線", "Tokyu.DenEnToshi": "田園都市線", "Tokyu.Ikegami": "池上線", "Keio.Dobutsuen": "動物園線", "TokyoMetro.Hanzomon": "半蔵門線", "TokyoMetro.Hibiya": "日比谷線", "Tokyu.Meguro": "目黒線", "Tokyu.Oimachi": "大井町線", "Tokyu.Toyoko": "東横線", "TokyoMetro.Tozai": "東西線", "TamaMonorail.TamaMonorail": "多摩モノレール", "Tokyu.TokyuShinYokohama": "東急新横浜線", "YokohamaMunicipal.Blue": "ブルーライン", "YokohamaMunicipal.Green": "グリーンライン", "TokyoMetro.Yurakucho": "有楽町線", "Keio.Keibajo": "競馬場線", "JR-Central.TokaidoShinkansen": "東海道新幹線", "TokyoMetro.MarunouchiBranch": "丸ノ内線支線", "Hokuso.Hokuso": "北総線", "JR-East.AkitaShinkansen": "秋田新幹線", "JR-East.HokurikuShinkansen": "北陸新幹線", "JR-East.JoetsuShinkansen": "上越新幹線", "JR-East.TohokuShinkansen": "東北新幹線", "JR-East.YamagataShinkansen": "山形新幹線", "Keio.Inokashira": "井の頭線", "Keio.KeioNew": "京王新線", "Keio.Keio": "京王線", "Keio.Sagamihara": "相模原線", "Keisei.Main": "京成本線", "Keisei.NaritaSkyAccess": "成田スカイアクセス線", "Keisei.Oshiage": "押上線", "Minatomirai.Minatomirai": "みなとみらい線", "OdakyuHakone.HakoneTozan": "箱根登山線", "SaitamaRailway.SaitamaRailway": "埼玉高速鉄道線", "Shibayama.Shibayama": "芝山鉄道線", "TokyoMonorail.HanedaAirport": "羽田空港線", "JR-East.Utsunomiya": "宇都宮線", "KantoRailway.Joso": "常総線", "Keio.Takao": "高尾線", "ToyoRapid.ToyoRapid": "東葉高速線", "JR-East.ChuoRapid": "中央線快速", "JR-East.ChuoSobuLocal": "中央・総武各駅停車", "JR-East.JobanLocal": "常磐線各駅停車", "JR-East.JobanRapid": "常磐線快速", "JR-East.KeihinTohokuNegishi": "京浜東北線・根岸線", "JR-East.Keiyo": "京葉線", "JR-East.Musashino": "武蔵野線", "JR-East.Nambu": "南武線", "JR-East.Ome": "青梅線", "JR-East.SaikyoKawagoe": "埼京線・川越線", "JR-East.ShonanShinjuku": "湘南新宿ライン", "JR-East.SobuRapid": "総武快速線", "JR-East.SotetsuDirect": "相鉄直通線", "JR-East.Takasaki": "高崎線", "JR-East.Tokaido": "東海道線", "JR-East.Yamanote": "山手線", "JR-East.Yokohama": "横浜線", "JR-East.Yokosuka": "横須賀線", "ChibaMonorail.Line2": "２号線", "JR-Central.Chuo": "中央本線", "Tokyu.Kodomonokuni": "こどもの国線", "Tokyu.Setagaya": "世田谷線", "Tobu.Kinugawa": "鬼怒川線", "Tokyu.TokyuTamagawa": "東急多摩川線", "Tobu.Daishi": "大師線", "Tobu.Kameido": "亀戸線", "Tobu.Kiryu": "桐生線", "Tobu.KoizumiBranch": "小泉線(東小泉-太田)", "Tobu.Koizumi": "小泉線", "Tobu.Ogose": "越生線", "Tobu.Sano": "佐野線", "Isumi.Isumi": "いすみ線", "JR-East.BanetsuEast": "磐越東線", "Tobu.Utsunomiya": "宇都宮線", "IzuHakone.Daiyuzan": "大雄山線", "IzuHakone.Sunzu": "駿豆線", "Aizu.Aizu": "会津線", "ChibaMonorail.Line1": "１号線", "Seibu.Kokubunji": "国分寺線", "Izukyu.Izukyu": "伊豆急行線", "JR-East.BanetsuWest": "磐越西線", "Seibu.Seibuen": "西武園線", "Seibu.Tamagawa": "多摩川線", "Seibu.Tamako": "多摩湖線", "Seibu.Yamaguchi": "山口線", "Chichibu.Chichibu": "秩父本線", "Choshi.Choshi": "銚子電鉄線", "Enoden.Enoden": "江ノ島電鉄線", "Fujikyu.Fujikyu": "富士急行線", "Hitachinaka.Minato": "湊線", "Hokuetsu.Hokuhoku": "ほくほく線", "JR-Central.Gotemba": "御殿場線", "JR-Central.Minobu": "身延線", "JR-Central.Tokaido": "東海道線", "JR-East.Aterazawa": "左沢線", "JR-East.ChuoTatsunoBranch": "中央本線辰野支線", "JR-East.Echigo": "越後線", "JR-East.Gono": "五能線", "JR-East.Hachinohe": "八戸線", "JR-East.Hakushin": "白新線", "JR-East.Hanawa": "花輪線", "JR-East.Iiyama": "飯山線", "JR-East.Ishinomaki": "石巻線", "JR-East.Kamaishi": "釜石線", "JR-East.Karasuyama": "烏山線", "JR-East.Kesennuma": "気仙沼線", "JR-East.Kitakami": "北上線", "JR-East.Koumi": "小海線", "JR-East.Mito": "水戸線", "JR-East.Nikko": "日光線", "JR-East.Ofunato": "大船渡線", "JR-East.Oga": "男鹿線", "JR-East.Oito": "大糸線", "JR-East.Ominato": "大湊線", "JR-East.OuYamagata": "山形線", "JR-East.Ou": "奥羽本線", "JR-East.RikuEast": "陸羽東線", "JR-East.RikuWest": "陸羽西線", "JR-East.Ryomo": "両毛線", "JR-East.SensekiTohoku": "仙石東北ライン", "JR-East.Senseki": "仙石線", "JR-East.Senzan": "仙山線", "JR-East.Shinetsu": "信越本線", "JR-East.Shinonoi": "篠ノ井線", "JR-East.SuigunBranch": "水郡線支線", "JR-East.Suigun": "水郡線", "JR-East.Tadami": "只見線", "JR-East.Tazawako": "田沢湖線", "JR-East.Tohoku": "東北本線", "JR-East.Tsugaru": "津軽線", "JR-East.Uetsu": "羽越本線", "JR-East.Yahiko": "弥彦線", "JR-East.Yamada": "山田線", "JR-East.Yonesaka": "米坂線", "JR-Shikoku.SetoOhashi": "瀬戸大橋線", "JR-West.Sanin": "山陰本線", "Jomo.Jomo": "上毛線", "Joshin.Joshin": "上信線", "KantoRailway.Ryugasaki": "竜ヶ崎線", "KashimaRinkai.OaraiKashima": "大洗鹿島線", "Keisei.Chiba": "千葉線", "Keisei.HigashiNarita": "東成田線", "Keisei.Kanamachi": "金町線", "Keisei.Matsudo": "松戸線", "Kominato.Kominato": "小湊鉄道線", "MaihamaResort.DisneyResort": "ディズニーリゾートライン", "Ryutetsu.Nagareyama": "流山線", "SaitamaTransit.NewShuttle": "ニューシャトル", "SendaiAirportTransit.SendaiAirport": "仙台空港線", "SendaiMunicipal.Namboku": "南北線", "SendaiMunicipal.Tozai": "東西線", "ShonanMonorail.ShonanMonorail": "湘南モノレール線", "UtsunomiyaLightRail.UtsunomiyaLightRail": "宇都宮ライトレール", "WataraseKeikoku.WataraseKeikoku": "わたらせ渓谷線", "Yagan.AizuKinugawa": "会津鬼怒川線", "YokohamaSeaside.KanazawaSeaside": "金沢シーサイドライン", "Keikyu.Daishi": "大師線", "JR-East.Agatsuma": "吾妻線", "JR-East.Chuo": "中央本線", "JR-East.Hachiko": "八高線", "JR-East.Ito": "伊東線", "JR-East.Itsukaichi": "五日市線", "JR-East.Joban": "常磐線", "JR-East.Joetsu": "上越線", "JR-East.Kashima": "鹿島線", "JR-East.Kawagoe": "川越線(川越-高麗川間)", "JR-East.Kururi": "久留里線", "JR-East.NambuBranch": "南武線浜川崎支線", "JR-East.NaritaAbikoBranch": "成田線我孫子支線", "JR-East.TsurumiOkawaBranch": "鶴見線大川支線", "JR-East.NaritaAirportBranch": "成田線空港支線", "JR-East.Narita": "成田線", "JR-East.Sagami": "相模線", "JR-East.Sobu": "総武本線", "JR-East.Sotobo": "外房線", "JR-East.Togane": "東金線", "JR-East.TsurumiUmiShibauraBranch": "鶴見線海芝浦支線", "JR-East.Tsurumi": "鶴見線", "JR-East.Uchibo": "内房線"}


def odpt_private(lines):
    """ODPTのTrainInformation(センター＋チャレンジの2ソース)を路線単位で反映"""
    sources = []
    if os.environ.get('ODPT_TOKEN'):
        sources.append(('https://api.odpt.org/api/v4/odpt:TrainInformation'
                        f"?acl:consumerKey={os.environ['ODPT_TOKEN']}"))
    if os.environ.get('ODPT_CHALLENGE_TOKEN'):
        sources.append(('https://api-challenge.odpt.org/api/v4/odpt:TrainInformation'
                        f"?acl:consumerKey={os.environ['ODPT_CHALLENGE_TOKEN']}"))
    if not sources:
        return 0
    data = []
    for url in sources:
        try:
            data.extend(json.loads(fetch(url, timeout=20)))
        except Exception:
            pass
    per_co = {}
    for e in data:
        op = (e.get('odpt:operator') or '').split(':')[-1]
        rid0 = (e.get('odpt:railway') or '').replace('odpt.Railway:', '')
        if op in ('jre-is', 'JR-East'):
            if 'Shinkansen' in rid0:
                continue
            ja0 = ODPT_RAILWAY_JA.get(rid0, rid0.split('.')[-1])
            text0 = ((e.get('odpt:trainInformationText') or {}).get('ja') or '').strip()
            if text0 and not re.search(r'平常|ありません|通常どおり|通常運転', text0):
                st0 = classify(text0)
                put(lines, 'jre:' + norm(ja0), st0, f'{ja0}: {text0[:180]}',
                    extract_sections(text0, st0))
            continue
        km = ODPT_OP_KEY.get(op)
        if not km:
            continue
        key, disp = km
        rid = (e.get('odpt:railway') or '').replace('odpt.Railway:', '')
        ja = ODPT_RAILWAY_JA.get(rid, rid.split('.')[-1])
        text = ((e.get('odpt:trainInformationText') or {}).get('ja') or '').strip()
        st = 'normal' if (not text or re.search(r'平常|ありません|通常どおり|通常運転', text)) else classify(text)
        cur = per_co.setdefault(key, {'disp': disp, 'worst': 'normal', 'texts': []})
        if st != 'normal':
            put(lines, f'pvt:{key}:{norm(ja)}', st, f'{disp}{ja}: {text[:150]}')
            cur['texts'].append(f'{ja}: {text[:80]}')
            if SEVERITY[st] > SEVERITY[cur['worst']]:
                cur['worst'] = st
    for key, v in per_co.items():
        put(lines, f'pvtco:{key}', v['worst'],
            f"{v['disp']}: " + (' / '.join(v['texts'])[:180] if v['texts'] else '平常運転'))
    return len(per_co)


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
        n = private_official(lines)
        ok.append('pvt')
        ok.append(f'pvt{n}')
    except Exception as e:
        errors.append(f'pvt: {type(e).__name__}: {e}')
    try:
        n2 = odpt_private(lines)
        if n2:
            ok.append(f'odpt{n2}')
    except Exception as e:
        errors.append(f'odpt: {type(e).__name__}: {e}')

    out = {'updated': datetime.now(JST).isoformat(timespec='seconds'),
           'ok': ok, 'lines': lines, 'errors': errors}
    with open('zairaisen_status.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'掲載: {len(lines)}路線 / ok: {ok} / errors: {errors}', file=sys.stderr)


if __name__ == '__main__':
    main()
