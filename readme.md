# DICOM MPR ビューア

DICOM のCT画像を読み込み、Axial / Coronal / Sagittal の3断面を表示するビューアです。

## 動作環境

- Python **3.11**（3.10〜3.12 でも動作する想定。動作確認は 3.11）
- GUI は Python 標準の `tkinter` を使用（追加インストール不要。Linux のみ `sudo apt install python3-tk` が必要な場合あり）
- 使用パッケージは `requirements.txt` を参照（numpy / pydicom / Pillow / scipy / tifffile / matplotlib / vtk / pyvista、すべて版固定）

## インストール手順

```bash
# このフォルダで実行
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## 起動方法

```bash
python main.py
```

## 操作方法

1. メニュー **ファイル → DICOM フォルダを開く...**（Ctrl+O）で、DICOM ファイルが入ったフォルダを選択
   - サブフォルダも含めて探索し、最も枚数の多いシリーズを読み込みます
   - **ファイル → DICOM ファイルを開く...** でファイルを直接複数選択することもできます
2. 3面（Axial / Coronal / Sagittal）が表示されます。各画像上の**緑の線**は、他の2つの断面の位置を表します
3. 各画像の下のスライダー
   - **スライス位置**：その面のスライス位置を変更（他の2画像の緑の線も連動して動きます）
   - **WL / WW**：その画像だけのウィンドウレベル／ウィンドウ幅を変更
4. おまけ機能
   - 画像上をクリック（ドラッグ）すると、3断面の交点がその位置に移動します
   - 画像上でマウスホイールを回すとスライスを送れます

## 観点7：肺領域の3D表示（ボリュームレンダリング）

連続TIFFを使います。

**アプリから使う場合**
1. 先にCTのDICOMを読み込む（上記の手順）
2. メニュー **ツール → 肺領域の3D表示...** を選び、第5回の連続TIFFを選択
3. 「スライス順を反転しますか？」は通常「いいえ」。3D表示が上下逆の場合のみ「はい」
4. 3Dウィンドウが開きます。閉じるとビューアに戻ります

   | 操作 | キーボード | マウス |
   |---|---|---|
   | 回転 | 矢印キー（←→：左右、↑↓：上下） | 左ドラッグ |
   | 拡大 | `+`（`=`）/ PageUp | ホイール |
   | 縮小 | `-` / PageDown | ホイール |
   | 正面に戻す | Home | - |
   | ウィンドウを閉じる | `q` | - |

**コマンドラインから使う場合**
```bash
python volume_render.py --mask lung_mask.tif --dicom path/to/dicom_folder
# 主なオプション: --flip-z（順序反転）, --step 1（高画質・低速）, --mode mask（形状のみ）
```

- マスク内のCT値で描画するため、肺野に加えて血管や気管支も見えます（`--mode mask` で形状のみ）
- マスクのサイズ（枚数×縦×横）はCTと一致している必要があります
- マスクの白黒が逆（背景が白）の場合は自動で反転します

## 観点との対応

| 観点 | 内容 | 対応 |
|---|---|---|
| 1 | Viewer を自作 | tkinter Canvas + numpy + Pillow で自作（既存ビューアライブラリ不使用） |
| 2 | ファイルメニューでDICOM選択 | 「ファイル」メニュー |
| 3 | Axial/Coronal/Sagittal 表示 | ボクセル間隔を考慮した比率で表示 |
| 4 | 他の2面の位置を線で表示 | 緑の十字線 |
| 5 | WL/WW・スライス位置をスライダーで変更 | 各画像ごとにスライダー |
| 6 | 線がスライダーに連動 | スライダー操作で全ビューを再描画 |
| 7 | 肺領域のボリュームレンダリング | `volume_render.py`（pyvista/VTK）。メニュー「ツール」から起動 |

## 実装メモ

- スライスは `ImagePositionPatient` と `ImageOrientationPatient` から求めた法線方向の位置でソートしています
- CT値は `RescaleSlope` / `RescaleIntercept` で HU に変換して保持します
- 初期の WL/WW は DICOM の `WindowCenter` / `WindowWidth`（無い場合は 40 / 400）
- 圧縮 DICOM（JPEG 等）が読めない場合は `pip install pylibjpeg pylibjpeg-libjpeg` 等を追加してください