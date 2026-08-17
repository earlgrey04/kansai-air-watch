#!/usr/bin/env python3
"""YouTubeライブカメラ一覧(自前収集)を更新し cameras_yt.json を出力する。

データの流れ:
  yt_seed.json (repo同梱・自前DB) … video_id/channel_id/タイトル/位置(独自ジオコーディング)/カテゴリ
      ↓ このスクリプト(6時間毎)
  cameras_yt.json (statusブランチ) … 3Dビューアが読む。配信中フラグと現在のvideo_idを反映

YouTube Data API v3 (環境変数 YOUTUBE_API_KEY) があれば:
  1. videos.list で既知動画の配信状態を確認 (1unit/50本)
  2. 配信が終わったチャンネルは uploads プレイリスト(playlistItems.list=1unit)+videos.list で
     新しいライブ配信を探す (チャンネルあたり約2unit)
  3. 検索(search.list, 100unit/回)で新規カメラ候補を少数発見し yt_pending.json に貯める(位置は未確定なので表示対象外)
API キーが無ければ oEmbed(公開・キー不要)で動画の存在確認のみ行い、配信中フラグは前回値/不明のまま。
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, '..', 'web', 'yt_seed.json')
if not os.path.exists(SEED):
    SEED = os.path.join(HERE, '..', 'yt_seed.json')  # リポジトリ直下(GitHub Pages側)
OUT = 'cameras_yt.json'
PENDING = 'yt_pending.json'
MIN_INTERVAL = 6 * 3600
API = 'https://www.googleapis.com/youtube/v3/'
KEY = os.environ.get('YOUTUBE_API_KEY', '').strip()
UA = {'User-Agent': 'nihon-livemap/2.0 (+https://air.aiblockchainnexuslab.tech)'}
DISCOVER_QUERIES = ['ライブカメラ', 'ライブ配信 カメラ 24時間', '定点カメラ ライブ', '河川 ライブカメラ', '駅前 ライブカメラ',
                    '空港 ライブ カメラ', '港 ライブカメラ', '富士山 ライブカメラ', '桜島 ライブ', '雪 ライブカメラ 道路']
DISCOVER_PER_RUN = 2  # 1回の実行で回す検索クエリ数(100unit×2)。ラウンドロビン

def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)

def api(method, **params):
    params['key'] = KEY
    return get(API + method + '?' + urllib.parse.urlencode(params, doseq=True))

def chunks(xs, n):
    for i in range(0, len(xs), n): yield xs[i:i + n]

def load_json(p, default):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default

def fresh(prev):
    try:
        g = datetime.strptime(prev.get('gen', ''), '%Y-%m-%dT%H:%M:%S%z')
        return (datetime.now(timezone.utc) - g).total_seconds() < MIN_INTERVAL and len(prev.get('items') or []) >= 100
    except Exception:
        return False

def video_status(vids):
    """video_id -> (live:bool|None, title, channel_id, ended:bool)"""
    st = {}
    for ch in chunks(vids, 50):
        try:
            d = api('videos', part='snippet,liveStreamingDetails', id=','.join(ch), maxResults=50)
        except Exception as e:
            print('videos.list failed:', e, file=sys.stderr); continue
        found = set()
        for it in d.get('items', []):
            sn = it.get('snippet', {}); ls = it.get('liveStreamingDetails', {})
            live = sn.get('liveBroadcastContent') == 'live' or (bool(ls.get('actualStartTime')) and not ls.get('actualEndTime'))
            ended = bool(ls.get('actualEndTime')) or (sn.get('liveBroadcastContent') == 'none' and not ls)
            st[it['id']] = (live, sn.get('title'), sn.get('channelId'), ended)
            found.add(it['id'])
        for v in ch:
            if v not in found: st[v] = (False, None, None, True)  # 削除・非公開
    return st

def find_channel_live(channel_id):
    """uploads プレイリストの新しい順10本から配信中の動画を探す(約2unit)。見つからなければ None"""
    try:
        pl = 'UU' + channel_id[2:]
        d = api('playlistItems', part='contentDetails', playlistId=pl, maxResults=10)
        vids = [i['contentDetails']['videoId'] for i in d.get('items', [])]
        if not vids: return None
        st = video_status(vids)
        for v in vids:
            if st.get(v, (False,))[0]: return v, st[v][1]
    except Exception as e:
        print('find_channel_live failed', channel_id, e, file=sys.stderr)
    return None

def oembed_ok(video_id):
    u = 'https://www.youtube.com/oembed?url=' + urllib.parse.quote('https://www.youtube.com/watch?v=' + video_id, safe='') + '&format=json'
    try:
        get(u, timeout=15); return True
    except Exception:
        return False

def main():
    prev = load_json(OUT, {})
    if fresh(prev):
        print('cameras_yt.json: fresh, skip'); return 0
    seed = load_json(SEED, None)
    if not seed:
        print('yt_seed.json not found:', SEED, file=sys.stderr); return 1
    seed_items = seed.get('items') if isinstance(seed, dict) else seed
    prev_by_id = {i.get('id'): i for i in (prev.get('items') or []) if i.get('id')}
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S%z')
    items = []
    if KEY:
        st = video_status([s['v'] for s in seed_items if s.get('v')])
        relookup = 0
        for s in seed_items:
            live, title, chid, ended = st.get(s.get('v'), (None, None, None, False))
            v = s.get('v'); n = s.get('n')
            p = prev_by_id.get(s['id'], {})
            if title: n = title
            if (not live) and s.get('ch') and relookup < 400:  # 配信が変わった/終わった → 同チャンネルの新しい配信を探す
                relookup += 1
                r = find_channel_live(s['ch'])
                if r: v, n = r[0], (r[1] or n); live = True
            last = now if live else (p.get('last') or s.get('last') or '')
            items.append({**s, 'n': n or s.get('n'), 'v': v, 'live': bool(live), 'last': last, 'gone': bool(ended and not live)})
        # 新規発見(少数)。位置未確定なので pending へ
        pend = load_json(PENDING, {'q_idx': 0, 'items': {}})
        known_ch = {s.get('ch') for s in seed_items}
        for _ in range(DISCOVER_PER_RUN):
            q = DISCOVER_QUERIES[pend['q_idx'] % len(DISCOVER_QUERIES)]; pend['q_idx'] += 1
            try:
                d = api('search', part='snippet', q=q, type='video', eventType='live', regionCode='JP', relevanceLanguage='ja', maxResults=50)
                for it in d.get('items', []):
                    ch = it['snippet']['channelId']
                    if ch in known_ch: continue
                    pend['items'][it['id']['videoId']] = {'ch': ch, 'n': it['snippet']['title'], 's': it['snippet']['channelTitle'], 'q': q, 'seen': now}
            except Exception as e:
                print('search failed:', e, file=sys.stderr); break
        json.dump(pend, open(PENDING, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        print(f'pending candidates: {len(pend["items"])}')
    else:
        print('YOUTUBE_API_KEY not set: oEmbed存在確認のみ', file=sys.stderr)
        for s in seed_items:
            p = prev_by_id.get(s['id'], {})
            ok = oembed_ok(s['v']) if s.get('v') else False
            live = p.get('live') if p else None  # 不明(None)のまま=表示側は通常色
            items.append({**s, 'live': (live if ok else False), 'last': p.get('last') or s.get('last') or '', 'gone': not ok})
    shown = [i for i in items if i.get('la') and i.get('lo')]
    out = {'gen': now, 'src': 'YouTube Data API v3 / 自前収集(位置は独自ジオコーディング)', 'items': shown}
    tmp = OUT + '.tmp'
    json.dump(out, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f'cameras_yt.json: {len(shown)} items, live={sum(1 for i in shown if i.get("live"))}, gone={sum(1 for i in shown if i.get("gone"))}')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('fetch_youtube_cams failed:', e, file=sys.stderr); sys.exit(1)
