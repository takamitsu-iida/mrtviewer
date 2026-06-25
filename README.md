# BGP Tree Rings — インターネット年輪可視化

**ライブデモ**: https://takamitsu-iida.github.io/mrtviewer/

RIPE RIS（Routing Information Service）が公開する MRT 形式の BGP ルーティングテーブルを取得・解析し、特定の AS を中心とした **重力井戸型の年輪** として 3D 可視化するツールです。

- **中心**：指定した AS（例: AS2497 IIJ）
- **リング半径**：ホップ距離（中心からの BGP AS パス長）
- **Y 軸の高さ**：ホップ² による放物面（重力井戸）＋ プレフィックス数による微細な起伏
- **扇形セクター**：RIR 地域別（APNIC / RIPE / AFRINIC / LACNIC / ARIN）
- **波紋アニメーション**：中心から広がる BGP 伝播イメージ

---

## アーキテクチャ

```
RIPE RIS
  └─ latest-bview.gz (MRT 形式, ~400MB)
         │
         ▼
  scripts/fetch_mrt.py  ──→  data/{collector}-latest-bview.gz
  (Python / mrtparse)          └──→  graph.json
         │
         ▼
  scripts/fetch_rings.py  ──→  ring_graph.json
  (BFS でホップ距離を計算)
         │
         ▼
  index.html + src/rings.js
  (Three.js / CDN importmap)
         │
         ▼
  ブラウザ 3D 可視化
```

---

## 技術スタック

| レイヤー | 技術 | 役割 |
|---|---|---|
| データ取得 | Python + `mrtparse` | MRT ファイルのダウンロード・解析 |
| グラフ構築 | Python BFS | 中心 AS からのホップ距離計算 |
| 3D 描画 | `Three.js r176`（CDN） | WebGL レンダリング |
| ビジュアル | GLSL カスタムシェーダー | AS ノードの発光エフェクト |
| ポストエフェクト | `UnrealBloomPass` | 光彩（グロー）|
| サーバー | Python `http.server` | npm/Node.js 不要 |

---

## セットアップ

### 前提条件

- Python 3.8 以上
- モダンブラウザ（Chrome / Firefox / Safari 最新版）

### インストール

```bash
git clone <repo-url>
cd mrtviewer

python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

---

## 使い方

### Step 1: MRT データを取得・解析

```bash
source .venv/bin/activate

# rrc00（フランクフルト）から最新 bview を取得して graph.json を生成
python scripts/fetch_mrt.py
```

> **ファイルサイズ**：bview は圧縮状態で約 400 MB あります。
> 動作確認だけなら `--max-rib 50000` で処理量を制限できます。

> **企業プロキシ（SSL インスペクション）環境**：
> スクリプトは自動的に証明書検証を無効化して接続します（`ssl.CERT_NONE`）。

**オプション一覧：**

| オプション | デフォルト | 説明 |
|---|---|---|
| `--collector rrc00` | `rrc00` | RIPE RIS コレクター（rrc00〜rrc27）|
| `--force` | — | 強制的に再ダウンロード |
| `--max-age 7` | `7` | N 日より古いファイルを再取得 |
| `--skip-download` | — | ダウンロードをスキップ |
| `--max-rib 150000` | `150000` | 解析エントリ数の上限 |

```bash
# 東京コレクター(rrc21)を使用（日本の AS はこちらが高精度）
python scripts/fetch_mrt.py --collector rrc21

# 既存 .gz を再利用して解析だけやり直す
python scripts/fetch_mrt.py --skip-download
```

### Step 2: 年輪グラフデータを生成

```bash
# graph.json から BFS でホップ距離を計算（デフォルト中心 AS: 9363）
python scripts/fetch_rings.py

# 中心 AS を指定する場合
python scripts/fetch_rings.py --as 2497

# graph.json に対象 AS がない場合は RIPE Stat API を使用
python scripts/fetch_rings.py --as 9363 --use-api
```

完了すると `ring_graph.json` がプロジェクトルートに生成されます。

**オプション一覧：**

| オプション | デフォルト | 説明 |
|---|---|---|
| `--as 2497` | `9363` | 中心 AS 番号 |
| `--max-hops 5` | `6` | 表示するホップ数の上限 |
| `--collector rrc00` | `rrc00` | MRT 再解析時のコレクター |
| `--use-api` | — | RIPE Stat API を使用（MRT 不要）|

### Step 3: ブラウザで可視化

```bash
python3 -m http.server 8080
```

**http://localhost:8080** をブラウザで開きます。

`ring_graph.json` が見つからない場合は、ローディング画面にエラーと生成コマンドが表示されます。
中心 AS を変更するには `ring_graph.json` を再生成してページをリロードしてください。

> CDN（cdn.jsdelivr.net）へのアクセスが遮断される場合は、ブラウザのプロキシ除外設定に
> `cdn.jsdelivr.net` と `esm.sh` を追加してください。

---

## 可視化の見方

### カラーコード（RIR 地域 / セクター）

| 色 | 地域 |
|---|---|
| 緑（`#44ffcc`） | APNIC（アジア太平洋）|
| 青（`#4499ff`） | RIPE NCC（欧州・中東）|
| 黄（`#ffdd33`） | AFRINIC（アフリカ）|
| 紫（`#cc44ff`） | LACNIC（中南米）|
| 橙（`#ff6633`） | ARIN（北米）|

### レイアウト

- **リング半径** = ホップ距離 × 130 units
- **Y 軸の高さ** = hop^2 × 22（放物面・重力井戸）+ log10(prefixes+2) × 2.5
- **セクター** = 72°（360°÷5 地域）で均等分割

### 操作方法

| 操作 | 動作 |
|---|---|
| ドラッグ | 視点を回転 |
| スクロール | ズームイン・アウト |
| `R` キー | カメラをリセット |
| `B` キー | ブルームエフェクトの切替 |
| 上部入力欄 | 中心 AS を変更 |

---

## ファイル構成

```
mrtviewer/
├── data/                        # MRT バイナリ（git 管理外）
│   └── rrc00-latest-bview.gz
├── scripts/
│   ├── fetch_mrt.py             # MRT ダウンロード・解析 → graph.json
│   ├── fetch_rings.py           # BFS ホップ計算 → ring_graph.json
│   └── requirements.txt         # Python 依存: mrtparse
├── src/
│   ├── rings.js                 # Three.js 年輪可視化
│   └── style.css                # ローディング画面・HUD・凡例
├── index.html                   # CDN importmap + UI
├── graph.json                   # AS グラフデータ（git 管理外）
└── ring_graph.json              # 年輪グラフデータ（git 管理外）
```

---

## データフォーマット

### graph.json

```json
{
  "nodes": [{"id": 2497, "prefixes": 5000, "region": "APNIC", "label": "AS2497"}],
  "edges": [{"source": 2497, "target": 3356, "weight": 120}]
}
```

### ring_graph.json

```json
{
  "center": 2497,
  "max_hops": 5,
  "nodes": [{"id": 2497, "hop": 0, "prefixes": 5000, "region": "APNIC", "label": "AS2497"}],
  "edges": [{"source": 2497, "target": 7500}]
}
```

---

## データソース

- **RIPE RIS（Routing Information Service）**
  https://www.ripe.net/analyse/internet-measurements/routing-information-service-ris
- **MRT ファイル URL パターン**
  `https://data.ris.ripe.net/{collector}/latest-bview.gz`
- **利用可能なコレクター**
  rrc00（フランクフルト）、rrc21（東京）、rrc23（シンガポール）ほか全 27 拠点

---

## ライセンス

本ツールは MIT ライセンスです。
使用データは [RIPE NCC 利用規約](https://www.ripe.net/about-us/legal/ripe-ncc-website-terms-and-conditions) に従います。
