#!/usr/bin/env python3
"""ビットコイン/イーサリアムのノード分布を取得して crypto_nodes.json を出力。

- BTC: btcnodes.io (旧bitnodes) `/api/nodes?limit=20000` + `/api/global-nodes?window=1`
       ※CORSヘッダが無いのでブラウザから直接は叩けない → statusブランチ経由で配信
- ETH: nodewatch.chainsafe.io GraphQL (ビーコンチェーンのコンセンサスノード)
- 既存ファイルが350分以内ならスキップ(6時間毎相当)。失敗時は前回ファイルを維持
出力: crypto_nodes.json (座標は小数2桁=約1kmに丸めて同一セルを件数集約)
"""
import json, os, sys, time, urllib.request, collections
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
OUT = 'crypto_nodes.json'
MIN_INTERVAL = 350 * 60
UA = {'User-Agent': 'Mozilla/5.0 (compatible; nihon-livemap/1.0; +https://air.aiblockchainnexuslab.tech)'}
BTC_NODES = 'https://btcnodes.io/api/nodes?limit=20000'
BTC_GLOBAL = 'https://btcnodes.io/api/global-nodes?window=1'
ETH_GQL = 'https://nodewatch.chainsafe.io/query'


def get(url, timeout=90):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def gql(query, timeout=90):
    req = urllib.request.Request(ETH_GQL, method='POST', data=json.dumps({'query': query}).encode(),
                                 headers={**UA, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    if d.get('errors'):
        raise RuntimeError(str(d['errors'])[:200])
    return d['data']


def cells(rows):
    """[(lat, lon, key)] -> [[lat, lon, count, key], ...] 小数2桁で集約"""
    c = collections.Counter((round(la, 2), round(lo, 2), k) for la, lo, k in rows)
    return [[la, lo, n, k] for (la, lo, k), n in c.most_common()]


def fetch_btc():
    g = get(BTC_GLOBAL)
    nodes = get(BTC_NODES)['nodes']
    geo = [n for n in nodes if n.get('lat') not in (None, 0) and (n.get('country_name') or 'n/a') != 'n/a']
    rows = [(float(n['lat']), float(n['lon']), 0) for n in geo]
    cc = collections.Counter((n.get('country_name') or '?', n.get('country') or '?') for n in geo)
    org = collections.Counter(n.get('organization') or '?' for n in geo)
    city = collections.Counter(f"{n.get('city')}, {n.get('country_name')}" for n in geo if n.get('city'))
    return {
        'total': g.get('total_nodes'), 'geo': len(geo),
        'networks': [[x['name'], x['nodes'], x['percent']] for x in g.get('networks', [])],
        'countries': [[n, c, v] for (n, c), v in cc.most_common(20)],
        'cities': [[k, v] for k, v in city.most_common(12)],
        'orgs': [[k, v] for k, v in org.most_common(12)],
        'pts': [[p[0], p[1], p[2]] for p in cells(rows)],
        'height': None,
    }


def fetch_eth():
    d = gql('{ getHeatmapData { clientType syncStatus networkType latitude longitude } '
            'getNodeStats { totalNodes nodeSyncedPercentage nodeUnsyncedPercentage } '
            'aggregateByCountry { name count } aggregateByAgentName { name count } '
            'aggregateByNetwork { name count } }')
    hm = d['getHeatmapData']
    clients = [c for c, _ in collections.Counter(x['clientType'] for x in hm).most_common()]
    ci = {c: i for i, c in enumerate(clients)}
    rows = [(float(x['latitude']), float(x['longitude']), ci[x['clientType']]) for x in hm
            if x.get('latitude') is not None]
    st = d['getNodeStats']
    return {
        'total': st.get('totalNodes'), 'geo': len(rows),
        'synced_pct': round(st.get('nodeSyncedPercentage') or 0, 1),
        'clients': clients,
        'client_counts': [[x['name'], x['count']] for x in d.get('aggregateByAgentName', [])][:10],
        'networks': [[x['name'] or '不明', x['count']] for x in d.get('aggregateByNetwork', [])][:8],
        'countries': [[x['name'], None, x['count']] for x in
                      sorted(d.get('aggregateByCountry', []), key=lambda x: -x['count'])[:20]],
        'pts': cells(rows),  # [lat, lon, count, clientIdx]
    }


def main():
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
        g = datetime.strptime(prev.get('gen_iso', ''), '%Y-%m-%dT%H:%M:%S%z')
        if (datetime.now(timezone.utc) - g).total_seconds() < MIN_INTERVAL:
            print('crypto_nodes.json: fresh, skip'); return 0
    except Exception:
        prev = None

    out = {'gen': datetime.now(JST).strftime('%Y-%m-%d %H:%M'),
           'gen_iso': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S%z')}
    ok = 0
    for key, fn, src in (('btc', fetch_btc, 'btcnodes.io'), ('eth', fetch_eth, 'nodewatch.chainsafe.io')):
        try:
            out[key] = fn(); out[key]['src'] = src; ok += 1
        except Exception as e:
            print(f'{key} failed: {e}', file=sys.stderr)
            if prev and prev.get(key):
                out[key] = prev[key]  # 前回値を維持
    if not ok:
        print('both failed, keep previous', file=sys.stderr); return 1
    tmp = OUT + '.tmp'
    json.dump(out, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f"crypto_nodes.json: btc={out.get('btc',{}).get('geo')}/{out.get('btc',{}).get('total')} "
          f"eth={out.get('eth',{}).get('geo')}/{out.get('eth',{}).get('total')} bytes={os.path.getsize(OUT)}")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('fetch_crypto_nodes failed:', e, file=sys.stderr); sys.exit(1)
