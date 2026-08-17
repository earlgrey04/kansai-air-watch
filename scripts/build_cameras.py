#!/usr/bin/env python3
"""公的機関の静止画ライブカメラ一覧(sources/*.json、調査エージェントの出力)を統合し、
3Dビューア用 web/cameras.json を生成する。
使い方: build_cameras.py <sources_dir> <out_json>
入力レコード: {name,lat,lon,img,page,source,kind,refresh_sec,tos,img_ok,yt,geocoded,cache_bust}
出力: {"gen":..., "items":[{n,la,lo,k,i,p,s,t,cb?}], "sources":[...]}
"""
import glob, json, os, sys, time, collections

# 規約上グレー/私的ホストで負荷懸念のある源は除外
EXCLUDE_SRC = ('JAXA', 'lcnet', '中標津空港ビル',
               '富山県道路情報',      # 「フレーム内に取り込む形でのリンク不可」
               'みち情報ネットふくい',  # 「無断転載を禁じます」
               # 以下は県公式の著作権方針が「無断複製・転用不可(・フレーム内表示は遠慮)」型で、
               # 画像の直接埋め込みが転用と解釈される余地があるため保留(許諾が取れれば外す)
               'サイポスレーダー', '静岡県 ', '愛知県 川の防災情報', '岐阜県 川の防災情報', '岐阜県 道の情報',
               '徳島県 河川整備課', '徳島県河川カメラマップ経由')  # 「画像の無断転載を禁じます」
EXCLUDE_NOTE = ('要許可', '右クリック禁止')
KIND_MAP = {'river':'river','dam':'dam','road':'road','volcano':'volcano','coast':'coast','port':'coast',
            'snow':'snow','other':'other','airport':'other'}

def main(src_dir, out):
    items, seen_img, seen_key = [], set(), set()
    stats = collections.Counter(); srcs = collections.Counter()
    files = sorted(glob.glob(os.path.join(src_dir, '*.json')), key=lambda p: (os.path.basename(p) != 'national.json', p))
    for f in files:
        try:
            rows = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            print('skip', f, e, file=sys.stderr); continue
        if isinstance(rows, dict): rows = rows.get('items') or rows.get('cameras') or []
        for r in rows:
            try:
                la = float(r.get('lat')); lo = float(r.get('lon') if r.get('lon') is not None else r.get('lng'))
            except Exception:
                stats['no_coord'] += 1; continue
            if not (20 <= la <= 50 and 120 <= lo <= 155):
                stats['out_of_jp'] += 1; continue
            img = (r.get('img') or '').strip()
            dyn = r.get('img_dynamic') or None
            d = None
            # 道路情報提供システム(road-info-prvs)は規約で「各画像への直接リンクはご遠慮ください」→ 画像表示は行わない
            if 'road-info-prvs.mlit.go.jp' in (img or '') or 'road-info-prvs.mlit.go.jp' in json.dumps(r.get('img_template') or dyn or {}):
                stats['excluded_prvs'] += 1; continue
            if r.get('img_template') is True and '{' in (img or '') and not dyn:
                # 画像URL自体がテンプレート({YYYYMMDD}/{HHMM}/{yyyyMMddHHmm}/{mm10} 等)
                d = {'t': 'slot', 'tpl': img.replace('{YYYYMMDDHHmm}', '{YYYYMMDDHHMM}').replace('{yyyyMMddHHmm}', '{YYYYMMDDHHMM}'),
                     'step': int(r.get('img_time_step_min') or r.get('img_slot_min') or 10),
                     'lag': int(r.get('img_time_lag_min') or 10)}
                img = ''
            elif isinstance(r.get('img_template'), str) and not dyn:
                d = {'t': 'slot', 'tpl': r['img_template'].replace('{YYYYMMDDHHmm}', '{YYYYMMDDHHMM}'),
                     'step': int(r.get('img_time_step_min') or r.get('img_slot_min') or 5),
                     'lag': int(r.get('img_time_lag_min') or 4)}
            elif dyn:
                if dyn.get('template_full') or dyn.get('template_thumb'):
                    d = {'t': 'slot', 'tpl': dyn.get('template_full') or dyn.get('template_thumb')}
                elif dyn.get('list_url') and dyn.get('regex') and r.get('cors') is not False:
                    d = {'t': 're', 'u': dyn['list_url'], 're': dyn['regex'], 'b': dyn.get('base') or ''}
                else:
                    stats['dyn_unusable'] += 1; continue  # ブラウザから最新URLを組み立てられない
            if not d and (not img or not img.startswith('http')):
                stats['no_img'] += 1; continue
            # https サイトから http 画像は混在コンテンツで表示不可 → 除外
            if (d and d.get('tpl', '').startswith('http://')) or (not d and img.startswith('http://')):
                stats['http_only'] += 1; continue
            # 対応していないプレースホルダ({YYYYMMDDHHMMSS} 等)を含むテンプレは組み立て不能
            if d and d.get('t') == 'slot':
                import re as _re
                if _re.search(r'\{(?!YYYYMMDDHHMM\}|YYYYMMDD\}|HHMM\}|mm10\})[^}]*\}', d['tpl']):
                    stats['tpl_unsupported'] += 1; continue
            # 気象庁火山カメラ・海上保安庁カメラは national.json で全国分を収録済み(地域ファイル側は重複)
            if os.path.basename(f) != 'national.json' and any(x in (r.get('source') or '') for x in ('気象庁', '海上保安庁')):
                stats['dup_national'] += 1; continue
            if r.get('img_ok') is False and not (d and d['t'] == 'slot'):
                stats['img_ng'] += 1; continue
            src = r.get('source') or ''
            if any(x in src for x in EXCLUDE_SRC) or any(x in (r.get('note') or '') for x in EXCLUDE_NOTE):
                stats['excluded_tos'] += 1; continue
            imgkey = d['tpl'] if (d and d.get('t') == 'slot') else img
            if imgkey and imgkey in seen_img:
                stats['dup_img'] += 1; continue
            key = (round(la, 4), round(lo, 4), (r.get('name') or '').strip())
            if key in seen_key:
                stats['dup_key'] += 1; continue
            if imgkey: seen_img.add(imgkey)
            seen_key.add(key)
            k = KIND_MAP.get((r.get('kind') or 'other').lower(), 'other')
            it = {'n': (r.get('name') or '').strip()[:60], 'la': round(la, 5), 'lo': round(lo, 5), 'k': k, 'i': img,
                  'p': r.get('page') or '', 's': src, 't': int(r.get('refresh_sec') or 120)}
            if d: it['d'] = d
            if r.get('cache_bust') is False: it['cb'] = False
            if r.get('stale'): it['stale'] = True
            if r.get('geocoded') and (r.get('geo_precision') in ('municipality', 'approx') or r.get('geo_precision') is None):
                it['g'] = 1  # 位置は地名からの推定(誤差数km)
            items.append(it); stats['ok'] += 1; srcs[it['s']] += 1
    # 近接デデュープ(約100m以内・同種別): 道路情報提供システム(全国横断)より各地整の固定URLを優先
    def cell(it): return (it['k'], round(it['la'], 3), round(it['lo'], 3))
    prio = lambda it: 1 if '道路情報提供システム' in it['s'] else 0
    items.sort(key=prio)
    occupied = set(); kept = []
    for it in items:
        c = cell(it)
        near = any((c[0], c[1] + dy / 1000, c[2] + dx / 1000) in occupied for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        if near and prio(it) == 1:
            stats['dup_near_prvs'] += 1; continue
        occupied.add(c); kept.append(it)
    items = kept
    items.sort(key=lambda x: (x['k'], x['la'], x['lo']))
    json.dump({'gen': time.strftime('%Y-%m-%d'), 'items': items,
               'sources': [s for s, _ in srcs.most_common()]},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(dict(stats)); print(collections.Counter(i['k'] for i in items)); print(len(srcs), 'sources')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
