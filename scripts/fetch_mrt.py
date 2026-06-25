#!/usr/bin/env python3
"""
RIPE RIS MRT (TABLE_DUMP_V2) を解析して Three.js 銀河可視化用の JSON を生成

Usage:
    python scripts/fetch_mrt.py
    python scripts/fetch_mrt.py --skip-download       # ダウンロードをスキップ
    python scripts/fetch_mrt.py --force               # 強制的に再ダウンロード
    python scripts/fetch_mrt.py --max-age 3           # 3日より古ければ再ダウンロード
    python scripts/fetch_mrt.py --collector rrc01     # 使用する RRC を指定
    python scripts/fetch_mrt.py --max-rib 50000       # 処理エントリ数を制限
"""

import os
import sys
import json
import time
import argparse
from collections import defaultdict

try:
    import mrtparse
except ImportError:
    sys.exit("mrtparse が必要です:  pip install mrtparse")

# ---- パス ----------------------------------------------------------------

ROOT     = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
# graph.json はプロジェクトルートに出力（Python http.server で直接配信）
JSON_OUT = os.path.join(ROOT, "graph.json")

# ---- パラメータ -----------------------------------------------------------

DEFAULT_COLLECTOR = "rrc00"   # RIPE RIS コレクター (rrc00〜rrc27)
MAX_AGE_DAYS = 7              # N 日より古いファイルは再ダウンロード
MAX_RETRIES  = 3              # ダウンロード失敗時の最大リトライ回数

TOP_AS   = 2000    # 可視化するノード数上限
MAX_EDGE = 8000    # エッジ数上限
MAX_RIB  = 150_000 # 解析する RIB エントリ数上限（0 = 無制限）

# ---- AS 番号 → RIR 地域（大まかな推定）----------------------------------
# IANA の歴史的割り当てブロックに基づくヒューリスティック

_APNIC = [
    (4608,5120),(9216,10240),(17408,18944),(23552,24576),
    (27648,28672),(38912,40448),(45056,46080),(55296,56320),(58368,59392),
]
_LACNIC = [(22528,23552),(26624,27648),(52224,55296)]
_AFRINIC = [(36864,37888),(327680,328704)]
_RIPE = [
    (1877,2048),(2561,2774),(3001,3354),(5120,6656),(8192,9216),
    (12288,13312),(15360,16384),(20480,21504),(24576,25600),
    (28672,29696),(30720,31744),(33792,35840),(39936,40960),
    (44544,45056),(47104,51200),(56320,58368),(59392,61440),(62464,64512),
]

def _in(ranges, n):
    return any(s <= n < e for s, e in ranges)

def get_region(asn: int) -> str:
    if asn >= 64512:       return "PRIVATE"
    if _in(_APNIC, asn):   return "APNIC"
    if _in(_LACNIC, asn):  return "LACNIC"
    if _in(_AFRINIC, asn): return "AFRINIC"
    if _in(_RIPE, asn):    return "RIPE"
    return "ARIN"

# ---- ダウンロード --------------------------------------------------------

def _mrt_url(collector: str) -> str:
    return f"https://data.ris.ripe.net/{collector}/latest-bview.gz"

def _mrt_file(collector: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"{collector}-latest-bview.gz")

def _make_ssl_context():
    """
    SSL コンテキストを生成する。
    企業プロキシによる SSL インスペクション（自己署名証明書の挿入）に対応するため、
    証明書検証を無効化する。社内ネットワーク利用時に一般的な対処。
    """
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx

def _stream_download(url: str, dest: str, attempt: int, ssl_ctx) -> None:
    """URL からファイルをチャンク単位でストリーミングダウンロードする。"""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "mrtviewer/1.0"})
    with urllib.request.urlopen(req, context=ssl_ctx) as resp:
        total      = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 65_536  # 64 KB

        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    mb  = downloaded / 1_048_576
                    pct = min(downloaded * 100 // total, 100)
                    sys.stdout.write(
                        f"\r  [{attempt}/{MAX_RETRIES}] {pct:3d}%  {mb:.1f} MB"
                    )
                    sys.stdout.flush()

def download(collector: str, force: bool = False, max_age_days: int = MAX_AGE_DAYS) -> str:
    """
    RIPE RIS bview ファイルをダウンロードして保存パスを返す。

    - 既存ファイルが max_age_days 日以内なら再利用
    - force=True なら無条件に再ダウンロード
    - 失敗時は指数バックオフで MAX_RETRIES 回リトライ
    - .tmp への書き込み後にアトミックリネームして破損を防ぐ
    - SSL 証明書検証を無効化（企業プロキシの SSL インスペクション対応）
    """
    url      = _mrt_url(collector)
    filepath = _mrt_file(collector)
    ssl_ctx  = _make_ssl_context()

    # ---- 鮮度チェック ----
    if not force and os.path.exists(filepath):
        age_days = (time.time() - os.path.getmtime(filepath)) / 86_400
        if age_days < max_age_days:
            print(f"既存ファイルを使用 (取得 {age_days:.1f} 日前): {filepath}")
            return filepath
        print(f"ファイルが {age_days:.1f} 日前のため再ダウンロードします")

    print(f"ダウンロード中: {url}")
    print("  (SSL 証明書検証を無効化: 企業プロキシ対応)")
    print("  (ファイルサイズ目安: 20〜30 MB)")

    tmp = filepath + ".tmp"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _stream_download(url, tmp, attempt, ssl_ctx)
            os.replace(tmp, filepath)   # アトミックリネーム（破損防止）
            size_mb = os.path.getsize(filepath) / 1_048_576
            print(f"\n保存完了: {filepath}  ({size_mb:.1f} MB)")
            return filepath

        except Exception as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  エラー: {exc}")
                print(f"  {wait} 秒後にリトライします... ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                sys.exit(f"\nダウンロード失敗 ({MAX_RETRIES} 回試行): {exc}")

# ---- MRT 解析 -----------------------------------------------------------

def _extract_as_path(path_attributes):
    """path_attributes リストから AS パス（int リスト）を返す。見つからなければ None。
    mrtparse は AS 番号を str で返す場合があるため int に正規化する。
    """
    for attr in path_attributes:
        # attr['type'] は {int: str} の dict（例: {2: 'AS_PATH'}）
        attr_type = attr.get("type", {})
        if 2 not in attr_type:   # 2 = AS_PATH
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

def parse(filepath: str, max_rib: int):
    prefix_count = defaultdict(int)   # origin AS → プレフィックス数
    as_adj       = defaultdict(int)   # (min_as, max_as) → 隣接回数

    total = routed = 0
    print(f"MRT 解析中: {filepath}")

    for m in mrtparse.Reader(filepath):
        d = m.data
        if "rib_entries" not in d:
            continue

        total += 1
        if max_rib and total > max_rib:
            break

        # 最初の RIB エントリ（代表経路）の AS パスを取得
        for rib in d["rib_entries"][:1]:
            path = _extract_as_path(rib.get("path_attributes", []))
            if not path:
                continue

            origin = path[-1]
            prefix_count[origin] += 1
            routed += 1

            for i in range(len(path) - 1):
                a, b = path[i], path[i + 1]
                if a != b:
                    as_adj[(min(a, b), max(a, b))] += 1

        if total % 10_000 == 0:
            sys.stdout.write(
                f"\r  {total:,} エントリ処理済み ({routed:,} ルート、{len(prefix_count):,} AS)"
            )
            sys.stdout.flush()

    print(
        f"\n完了: {total:,} エントリ / {routed:,} ルート / "
        f"{len(prefix_count):,} AS / {len(as_adj):,} 隣接ペア"
    )
    return prefix_count, as_adj

# ---- グラフ構築 ----------------------------------------------------------

def build_graph(prefix_count, as_adj):
    # 上位 TOP_AS を選択
    top = sorted(prefix_count.items(), key=lambda x: x[1], reverse=True)[:TOP_AS]
    top_set = {asn for asn, _ in top}

    nodes = [
        {
            "id":       asn,
            "prefixes": cnt,
            "region":   get_region(asn),
            "label":    f"AS{asn}",
        }
        for asn, cnt in top
    ]

    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in sorted(as_adj.items(), key=lambda x: x[1], reverse=True)
        if a in top_set and b in top_set
    ][:MAX_EDGE]

    print(f"グラフ: ノード {len(nodes):,} / エッジ {len(edges):,}")
    return {"nodes": nodes, "edges": edges}

# ---- エントリポイント ---------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="RIPE RIS MRT → graph.json",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--collector", default=DEFAULT_COLLECTOR,
        metavar="RRC",
        help="使用するコレクター (rrc00〜rrc27)",
    )
    ap.add_argument(
        "--skip-download", action="store_true",
        help="ダウンロードをスキップして既存ファイルを使用",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="ファイルが新しくても強制的に再ダウンロード",
    )
    ap.add_argument(
        "--max-age", type=int, default=MAX_AGE_DAYS, metavar="DAYS",
        help="この日数より古いファイルは再ダウンロード",
    )
    ap.add_argument(
        "--max-rib", type=int, default=MAX_RIB,
        help="解析するエントリ数上限 (0=無制限)",
    )
    args = ap.parse_args()

    if args.skip_download:
        # --skip-download 時はデフォルトのファイルパスを使用
        mrt_file = _mrt_file(args.collector)
        if not os.path.exists(mrt_file):
            sys.exit(f"ファイルが見つかりません: {mrt_file}\n--skip-download を外して実行してください")
    else:
        mrt_file = download(
            collector  = args.collector,
            force      = args.force,
            max_age_days = args.max_age,
        )

    prefix_count, as_adj = parse(mrt_file, args.max_rib)
    graph = build_graph(prefix_count, as_adj)

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(graph, f, separators=(",", ":"))

    print(f"JSON 保存: {JSON_OUT}")
    print("次のステップ:  python3 -m http.server 8080  →  http://localhost:8080")

if __name__ == "__main__":
    main()
