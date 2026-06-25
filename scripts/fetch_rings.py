#!/usr/bin/env python3
"""
graph.json から特定 AS を中心とした年輪グラフ (ring_graph.json) を生成

データソースの優先順位:
  1. graph.json に対象 AS が含まれている場合: BFS でホップ距離を算出
  2. 含まれていない場合: MRT ファイルを再解析して AS パスを収集
  3. それでも見つからない場合: RIPE Stat API でネイバー情報を再帰取得

Usage:
    python scripts/fetch_rings.py
    python scripts/fetch_rings.py --as 9363
    python scripts/fetch_rings.py --max-hops 4 --use-api   # RIPE Stat API 強制使用
    python scripts/fetch_rings.py --as 9363 --collector rrc21  # 日本 AS は東京が高精度
"""

import os
import sys
import json
import ssl
import urllib.request
import argparse
from collections import defaultdict, deque, Counter

ROOT     = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
JSON_IN  = os.path.join(ROOT, "graph.json")
JSON_OUT = os.path.join(ROOT, "ring_graph.json")

DEFAULT_AS         = 9363
DEFAULT_MAX_HOPS   = 6
DEFAULT_COLLECTOR  = "rrc00"

# ---- AS → RIR 地域（fetch_mrt.py と共通）--------------------------------

_APNIC   = [(4608,5120),(9216,10240),(17408,18944),(23552,24576),(27648,28672),(38912,40448),(45056,46080),(55296,56320),(58368,59392)]
_LACNIC  = [(22528,23552),(26624,27648),(52224,55296)]
_AFRINIC = [(36864,37888)]
_RIPE    = [(1877,2048),(2561,2774),(3001,3354),(5120,6656),(8192,9216),(12288,13312),(15360,16384),(20480,21504),(24576,25600),(28672,29696),(30720,31744),(33792,35840),(39936,40960),(44544,45056),(47104,51200),(56320,58368),(59392,61440),(62464,64512)]

def _in(ranges, n): return any(s <= n < e for s, e in ranges)

def get_region(asn: int) -> str:
    if asn >= 64512:       return "PRIVATE"
    if _in(_APNIC, asn):   return "APNIC"
    if _in(_LACNIC, asn):  return "LACNIC"
    if _in(_AFRINIC, asn): return "AFRINIC"
    if _in(_RIPE, asn):    return "RIPE"
    return "ARIN"

# ---- BFS -----------------------------------------------------------------

def bfs(adj: dict, start: int, max_hops: int) -> dict:
    """start から BFS し、{AS番号: ホップ距離} を返す"""
    dist = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        d = dist[cur]
        if d >= max_hops:
            continue
        for nb in adj.get(cur, set()):
            if nb not in dist:
                dist[nb] = d + 1
                q.append(nb)
    return dist

# ---- graph.json から試みる -----------------------------------------------

def try_from_graph_json(my_as: int, max_hops: int):
    """
    graph.json が存在し my_as が含まれていれば (hop_dist, node_info, edges) を返す。
    見つからなければ (None, None, None) を返す。
    """
    if not os.path.exists(JSON_IN):
        return None, None, None

    print(f"graph.json を読み込み中...")
    with open(JSON_IN) as f:
        graph = json.load(f)

    node_info = {n["id"]: n for n in graph["nodes"]}

    # 無向隣接リスト（graph.json は上位 AS のみ含む）
    adj = defaultdict(set)
    for e in graph["edges"]:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    if my_as not in adj:
        print(f"  AS{my_as} は graph.json の上位 AS に未収録 → MRT を再解析します")
        return None, None, None

    print(f"  AS{my_as} を発見。BFS でホップ距離を算出中...")
    hop_dist = bfs(adj, my_as, max_hops)
    return hop_dist, node_info, graph["edges"]

# ---- MRT を直接解析 -------------------------------------------------------

def _extract_as_path_ints(path_attributes: list):
    """AS_PATH 属性から int のリストを返す"""
    for attr in path_attributes:
        if 2 not in attr.get("type", {}):
            continue
        path = []
        for seg in attr.get("value", []):
            for asn in seg.get("value", []):
                try:
                    path.append(int(asn))
                except (TypeError, ValueError):
                    pass
        return path or None
    return None

def from_mrt(collector: str, my_as: int, max_hops: int, max_rib: int = 300_000):
    """
    MRT bview ファイルから my_as を含む AS パスを抽出し
    (hop_dist, node_info, raw_adj) を返す。
    """
    try:
        import mrtparse
    except ImportError:
        sys.exit("mrtparse が必要です:  pip install mrtparse")

    mrt_file = os.path.join(DATA_DIR, f"{collector}-latest-bview.gz")
    if not os.path.exists(mrt_file):
        sys.exit(
            f"MRT ファイルが見つかりません: {mrt_file}\n"
            f"先に  python scripts/fetch_mrt.py --collector {collector}  を実行してください"
        )

    print(f"MRT 解析中: {mrt_file}")
    print(f"  AS{my_as} を含む AS パスを検索中...")

    adj     = defaultdict(set)   # 無向隣接リスト
    pfx_cnt = defaultdict(int)   # origin AS → プレフィックス数

    total = found = 0
    for m in mrtparse.Reader(mrt_file):
        d = m.data
        if "rib_entries" not in d:
            continue
        total += 1
        if max_rib and total > max_rib:
            break

        for rib in d["rib_entries"][:1]:
            path = _extract_as_path_ints(rib.get("path_attributes", []))
            if not path or my_as not in path:
                continue

            found += 1
            idx     = path.index(my_as)
            subpath = path[idx:]   # my_as から origin 方向のサブパス

            if subpath:
                pfx_cnt[subpath[-1]] += 1

            for i in range(len(subpath) - 1):
                a, b = subpath[i], subpath[i + 1]
                if a != b:
                    adj[a].add(b)
                    adj[b].add(a)

        if total % 20_000 == 0:
            sys.stdout.write(
                f"\r  {total:,} エントリ解析済み / AS{my_as} 含む経路: {found:,}"
            )
            sys.stdout.flush()

    print(f"\n  解析完了: {total:,} エントリ / AS{my_as} 含む: {found:,}")

    if my_as not in adj:
        sys.exit(
            f"\nAS{my_as} が MRT データ内で見つかりませんでした。\n"
            f"ヒント: 日本の AS には東京コレクター (--collector rrc21) が高精度です。\n"
            f"        先に  python scripts/fetch_mrt.py --collector rrc21  でダウンロードしてください。"
        )

    hop_dist  = bfs(adj, my_as, max_hops)
    node_info = {
        asn: {"id": asn, "prefixes": cnt, "region": get_region(asn)}
        for asn, cnt in pfx_cnt.items()
    }
    for asn in hop_dist:
        if asn not in node_info:
            node_info[asn] = {"id": asn, "prefixes": 0, "region": get_region(asn)}

    return hop_dist, node_info, adj

# ---- RIPE Stat API で AS ネイバーを再帰取得 --------------------------------

def _make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def from_ripe_stat(my_as: int, max_hops: int, max_queries: int = 120):
    """
    RIPE Stat asn-neighbours API でネイバーを再帰取得し
    (hop_dist, node_info, raw_adj) を返す。
    AS が見つからない場合は (None, None, None) を返す。
    """
    ctx = _make_ssl_ctx()
    adj     = defaultdict(set)
    queried = set()
    calls   = [0]

    def fetch_nb(asn):
        if asn in queried or calls[0] >= max_queries:
            return
        queried.add(asn)
        calls[0] += 1
        url = (
            "https://stat.ripe.net/data/asn-neighbours/data.json"
            f"?resource=AS{asn}&data_overload_limit=0"
        )
        sys.stdout.write(
            f"\r  API [{calls[0]:>3}/{max_queries}] AS{asn:<8}  取得中..."
        )
        sys.stdout.flush()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mrtviewer/1.0 (BGP ring viz)"}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            sys.stdout.write(f"\r  AS{asn}: スキップ ({type(e).__name__})\n")
            return
        for nb in data.get("data", {}).get("neighbours", []):
            nb_asn = nb.get("asn")
            if nb_asn:
                try:
                    nb_asn = int(nb_asn)
                    adj[asn].add(nb_asn)
                    adj[nb_asn].add(asn)
                except (TypeError, ValueError):
                    pass

    # BFS: ホップ順にネイバーを取得
    level = [(my_as, 0)]
    while level and calls[0] < max_queries:
        next_level = []
        for asn, depth in level:
            fetch_nb(asn)
            if depth + 1 <= max_hops:
                for nb in list(adj.get(asn, set())):
                    if nb not in queried:
                        next_level.append((nb, depth + 1))
        level = next_level

    print(f"\n  完了: {calls[0]} API コール / {len(queried)} AS のネイバー取得")

    if my_as not in adj:
        return None, None, None

    hop_dist = bfs(adj, my_as, max_hops)

    # graph.json があればプレフィックス数を補完
    extra = {}
    if os.path.exists(JSON_IN):
        with open(JSON_IN) as f:
            g = json.load(f)
        extra = {n["id"]: n for n in g["nodes"]}

    node_info = {}
    for asn in hop_dist:
        info = extra.get(asn, {})
        node_info[asn] = {
            "id":       asn,
            "prefixes": info.get("prefixes", 0),
            "region":   info.get("region", get_region(asn)),
        }

    return hop_dist, node_info, adj

# ---- 出力グラフ構築 -------------------------------------------------------

def build_output(my_as: int, hop_dist: dict, node_info: dict,
                 graph_edges=None, raw_adj=None, max_hops: int = DEFAULT_MAX_HOPS):

    valid = set(hop_dist.keys())

    nodes = []
    for asn, hop in hop_dist.items():
        info = node_info.get(asn, {})
        nodes.append({
            "id":       asn,
            "hop":      hop,
            "prefixes": info.get("prefixes", 0),
            "region":   info.get("region", get_region(asn)),
            "label":    f"AS{asn}",
        })

    # エッジ（valid ノード間のみ）
    edges = []
    if graph_edges is not None:
        edges = [
            {"source": e["source"], "target": e["target"]}
            for e in graph_edges
            if e["source"] in valid and e["target"] in valid
        ]
    elif raw_adj is not None:
        seen = set()
        for a, neighbors in raw_adj.items():
            if a not in valid:
                continue
            for b in neighbors:
                if b in valid:
                    key = (min(a, b), max(a, b))
                    if key not in seen:
                        seen.add(key)
                        edges.append({"source": a, "target": b})

    # ホップ別ノード数を表示
    hop_counts = Counter(hop_dist.values())
    print(f"\nホップ別ノード数:")
    for h in sorted(hop_counts):
        label = f"AS{my_as}（中心）" if h == 0 else f"  ホップ {h}"
        print(f"  {label}: {hop_counts[h]:,} AS")

    return {
        "center":   my_as,
        "max_hops": max_hops,
        "nodes":    nodes,
        "edges":    edges,
    }

# ---- エントリポイント -----------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="年輪グラフ (ring_graph.json) を生成",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--as",        type=int, default=DEFAULT_AS,        dest="my_as",
                    help="中心 AS 番号")
    ap.add_argument("--max-hops",  type=int, default=DEFAULT_MAX_HOPS,
                    help="表示するホップ距離の上限")
    ap.add_argument("--collector", default=DEFAULT_COLLECTOR, metavar="RRC",
                    help="MRT 再解析時に使用するコレクター (例: rrc21=東京)")
    ap.add_argument("--use-api",   action="store_true",
                    help="RIPE Stat API を強制使用（MRT ダウンロード不要）")
    args = ap.parse_args()

    # Step 1: graph.json から試みる（--use-api の場合はスキップ）
    hop_dist = node_info = graph_edges = raw_adj = None

    if not args.use_api:
        hop_dist, node_info, graph_edges = try_from_graph_json(args.my_as, args.max_hops)

    # Step 2: MRT から解析（--use-api の場合はスキップ）
    if hop_dist is None and not args.use_api:
        hop_dist, node_info, raw_adj = from_mrt(
            args.collector, args.my_as, args.max_hops
        )
        graph_edges = None

    # Step 3: RIPE Stat API（フォールバック or --use-api）
    if hop_dist is None:
        print(f"RIPE Stat API で AS{args.my_as} のネイバー情報を取得します...")
        print(f"  （最大ホップ {args.max_hops}、最大 120 APIコール）\n")
        hop_dist, node_info, raw_adj = from_ripe_stat(args.my_as, args.max_hops)
        graph_edges = None
        if hop_dist is None:
            sys.exit(
                f"\nAS{args.my_as} が RIPE Stat API でも見つかりませんでした。\n"
                f"AS 番号をご確認ください。"
            )

    out = build_output(
        my_as       = args.my_as,
        hop_dist    = hop_dist,
        node_info   = node_info,
        graph_edges = graph_edges,
        raw_adj     = raw_adj,
        max_hops    = args.max_hops,
    )

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"\nJSON 保存: {JSON_OUT}")
    print(f"  ノード: {len(out['nodes']):,}  エッジ: {len(out['edges']):,}")
    print("次のステップ:  python3 -m http.server 8080  →  http://localhost:8080/rings.html")

if __name__ == "__main__":
    main()
