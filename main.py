import os
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import pydicom
from PIL import Image, ImageTk

CANVAS_SIZE = 420
LINE_COLOR = "#00ff00"
BG_COLOR = "#101018"

PLANES = ("Axial", "Coronal", "Sagittal")

class Volume:
  """HU 値のボリューム (nz, ny, nx) とボクセル間隔 (sz, sy, sx)"""

  def __init__(self, data, spacing, window=None):
    self.data = data
    self.sz, self.sy, self.sx = spacing
    self.nz, self.ny, self.nx = data.shape
    self.window = window  # (WL, WW) or None


def _first(value, default=None):
  """MultiValue 対策: 先頭要素を float で返す"""
  if value is None:
    return default
  try:
    if isinstance(value, (list, tuple, pydicom.multival.MultiValue)):
      value = value[0]
    return float(value)
  except Exception:
    return default


def _to_hu(ds, arr):
  slope = _first(getattr(ds, "RescaleSlope", None), 1.0)
  intercept = _first(getattr(ds, "RescaleIntercept", None), 0.0)
  return arr.astype(np.float32) * slope + intercept


def _slice_position(ds):
  """スライス法線方向の位置 (ソート用)"""
  ipp = getattr(ds, "ImagePositionPatient", None)
  iop = getattr(ds, "ImageOrientationPatient", None)
  if ipp is not None and iop is not None:
    r = np.array(iop[:3], dtype=float)
    c = np.array(iop[3:], dtype=float)
    n = np.cross(r, c)
    return float(np.dot(n, np.array(ipp, dtype=float)))
  return float(getattr(ds, "InstanceNumber", 0))


def load_dicom(paths):
  """ファイルパスのリストから Volume を作る"""
  items = []
  for p in paths:
    try:
      ds = pydicom.dcmread(p)
      if not hasattr(ds, "PixelData"):
        continue
      items.append(ds)
    except Exception:
      continue  # DICOM でないファイルは無視
  if not items:
    raise ValueError("読み込める DICOM 画像が見つかりませんでした。")

  # マルチフレーム 1 ファイル
  if len(items) == 1 and items[0].pixel_array.ndim == 3:
    ds = items[0]
    data = _to_hu(ds, ds.pixel_array)
    ps = getattr(ds, "PixelSpacing", [1.0, 1.0])
    sz = _first(getattr(ds, "SpacingBetweenSlices", None),
                _first(getattr(ds, "SliceThickness", None), 1.0))
    window = _window_from(ds)
    return Volume(data, (sz, float(ps[0]), float(ps[1])), window)

  # 同一シリーズ・同一サイズの最大グループを採用
  groups = {}
  for ds in items:
    key = (str(getattr(ds, "SeriesInstanceUID", "")), ds.Rows, ds.Columns)
    groups.setdefault(key, []).append(ds)
  series = max(groups.values(), key=len)
  series.sort(key=_slice_position)

  data = np.stack([_to_hu(ds, ds.pixel_array) for ds in series], axis=0)

  ds0 = series[0]
  ps = getattr(ds0, "PixelSpacing", [1.0, 1.0])
  sy, sx = float(ps[0]), float(ps[1])

  sz = None
  if len(series) > 1:
    pos = np.array([_slice_position(d) for d in series])
    d = np.abs(np.diff(pos))
    d = d[d > 1e-6]
    if len(d) > 0:
      sz = float(np.mean(d))
  if sz is None:
    sz = _first(getattr(ds0, "SliceThickness", None), 1.0)

  return Volume(data, (sz, sy, sx), _window_from(ds0))


def _window_from(ds):
  wl = _first(getattr(ds, "WindowCenter", None))
  ww = _first(getattr(ds, "WindowWidth", None))
  if wl is not None and ww is not None and ww > 0:
    return wl, ww
  return None


def apply_window(img, wl, ww):
  """HU 画像に WL/WW を適用して uint8 にする"""
  ww = max(float(ww), 1.0)
  lo = wl - ww / 2.0
  out = (img - lo) / ww
  return (np.clip(out, 0.0, 1.0) * 255.0).astype(np.uint8)


# ---------------------------------------------------------------------------
# 1 面分のビュー (Canvas + スライダー)
# ---------------------------------------------------------------------------
class PlaneView:
  def __init__(self, parent, app, name):
    self.app = app
    self.name = name
    self.photo = None
    self.geom = None  # (ox, oy, scale, sp_col, sp_row)

    self.frame = tk.Frame(parent, padx=4, pady=4)
    tk.Label(self.frame, text=name, font=("", 12, "bold")).pack()

    self.canvas = tk.Canvas(self.frame, width=CANVAS_SIZE, height=CANVAS_SIZE,
                            bg=BG_COLOR, highlightthickness=0)
    self.canvas.pack()
    self.canvas.bind("<Button-1>", self.on_click)
    self.canvas.bind("<B1-Motion>", self.on_click)
    self.canvas.bind("<MouseWheel>", self.on_wheel)       # Windows / macOS
    self.canvas.bind("<Button-4>", lambda e: self.step(1))   # Linux
    self.canvas.bind("<Button-5>", lambda e: self.step(-1))  # Linux

    self.pos_scale = self._make_scale("スライス位置", self.on_pos)
    self.wl_scale = self._make_scale("WL (ウィンドウレベル)", self.on_window)
    self.ww_scale = self._make_scale("WW (ウィンドウ幅)", self.on_window)

    self.show_message("ファイルメニューから\nDICOM を読み込んでください")

  def _make_scale(self, label, cmd):
    s = tk.Scale(self.frame, from_=0, to=1, orient=tk.HORIZONTAL,
                 length=CANVAS_SIZE, label=label, command=cmd,
                 resolution=1)
    s.pack(fill=tk.X)
    return s

  # -- 軸の対応 ---------------------------------------------------------
  @property
  def axis_key(self):
    # このビューのスライダーが動かす座標
    return {"Axial": "z", "Coronal": "y", "Sagittal": "x"}[self.name]

  def get_slice(self, vol, pos):
    """表示用 2D 配列 (HU) と、表示ピクセル間隔 (列方向, 行方向)"""
    z, y, x = pos["z"], pos["y"], pos["x"]
    if self.name == "Axial":
      return vol.data[z, :, :], (vol.sx, vol.sy)
    if self.name == "Coronal":
      return vol.data[::-1, y, :], (vol.sx, vol.sz)   # 上が頭側
    return vol.data[::-1, :, x], (vol.sy, vol.sz)

  def cross_index(self, vol, pos):
    """他2面の位置を示す線の (列, 行) インデックス"""
    z, y, x = pos["z"], pos["y"], pos["x"]
    if self.name == "Axial":
      return x, y
    if self.name == "Coronal":
      return x, vol.nz - 1 - z
    return y, vol.nz - 1 - z

  def physical_size(self, vol):
    if self.name == "Axial":
      return vol.nx * vol.sx, vol.ny * vol.sy
    if self.name == "Coronal":
      return vol.nx * vol.sx, vol.nz * vol.sz
    return vol.ny * vol.sy, vol.nz * vol.sz

  # -- コールバック -------------------------------------------------------
  def on_pos(self, _v):
    if self.app.busy:
      return
    self.app.pos[self.axis_key] = int(self.pos_scale.get())
    self.app.refresh_all()   # 他ビューの線も連動 (観点6)

  def on_window(self, _v):
    if self.app.busy:
      return
    self.render()

  def on_wheel(self, e):
    self.step(1 if e.delta > 0 else -1)

  def step(self, d):
    if self.app.vol is None:
      return
    self.pos_scale.set(self.pos_scale.get() + d)

  def on_click(self, e):
    vol = self.app.vol
    if vol is None or self.geom is None:
      return
    ox, oy, scale, sc, sr = self.geom
    col = int((e.x - ox) / (sc * scale))
    row = int((e.y - oy) / (sr * scale))
    pos = self.app.pos
    if self.name == "Axial":
      pos["x"] = int(np.clip(col, 0, vol.nx - 1))
      pos["y"] = int(np.clip(row, 0, vol.ny - 1))
    elif self.name == "Coronal":
      pos["x"] = int(np.clip(col, 0, vol.nx - 1))
      pos["z"] = int(np.clip(vol.nz - 1 - row, 0, vol.nz - 1))
    else:
      pos["y"] = int(np.clip(col, 0, vol.ny - 1))
      pos["z"] = int(np.clip(vol.nz - 1 - row, 0, vol.nz - 1))
    self.app.sync_sliders()
    self.app.refresh_all()

  # -- 描画 -------------------------------------------------------------
  def show_message(self, text):
    self.canvas.delete("all")
    self.canvas.create_text(CANVAS_SIZE // 2, CANVAS_SIZE // 2, text=text,
                            fill="#aaaaaa", justify=tk.CENTER)

  def render(self):
    vol = self.app.vol
    if vol is None:
      return
    pos = self.app.pos
    img, (sc, sr) = self.get_slice(vol, pos)
    u8 = apply_window(img, self.wl_scale.get(), self.ww_scale.get())

    scale = self.app.scale
    w = max(1, int(round(img.shape[1] * sc * scale)))
    h = max(1, int(round(img.shape[0] * sr * scale)))
    pil = Image.fromarray(u8, mode="L").resize((w, h), Image.BILINEAR)
    self.photo = ImageTk.PhotoImage(pil)

    ox = (CANVAS_SIZE - w) / 2.0
    oy = (CANVAS_SIZE - h) / 2.0
    self.geom = (ox, oy, scale, sc, sr)

    self.canvas.delete("all")
    self.canvas.create_image(ox, oy, image=self.photo, anchor="nw")

    # 他の2面の位置を示す線 (観点4)
    ci, ri = self.cross_index(vol, pos)
    cx = ox + (ci + 0.5) * sc * scale
    cy = oy + (ri + 0.5) * sr * scale
    self.canvas.create_line(cx, oy, cx, oy + h, fill=LINE_COLOR)
    self.canvas.create_line(ox, cy, ox + w, cy, fill=LINE_COLOR)


# ---------------------------------------------------------------------------
# アプリ本体
# ---------------------------------------------------------------------------
class App:
  def __init__(self, root):
    self.root = root
    root.title("DICOM MPR Viewer")
    self.vol = None
    self.pos = {"z": 0, "y": 0, "x": 0}
    self.scale = 1.0
    self.busy = False

    self._build_menu()

    body = tk.Frame(root)
    body.pack(padx=6, pady=6)
    self.views = []
    for i, name in enumerate(PLANES):
      v = PlaneView(body, self, name)
      v.frame.grid(row=0, column=i)
      self.views.append(v)

    self.status = tk.Label(root, text="未読み込み", anchor="w")
    self.status.pack(fill=tk.X, padx=6, pady=(0, 4))

  # -- メニュー (観点2) ---------------------------------------------------
  def _build_menu(self):
    menubar = tk.Menu(self.root)
    fm = tk.Menu(menubar, tearoff=0)
    fm.add_command(label="DICOM フォルダを開く...", command=self.open_folder,
                   accelerator="Ctrl+O")
    fm.add_command(label="DICOM ファイルを開く...", command=self.open_files)
    fm.add_separator()
    fm.add_command(label="終了", command=self.root.quit)
    menubar.add_cascade(label="ファイル", menu=fm)
    tm = tk.Menu(menubar, tearoff=0)
    tm.add_command(label="肺領域の3D表示 (ボリュームレンダリング)...",
                   command=self.open_volume_render)
    menubar.add_cascade(label="ツール", menu=tm)
    self.root.config(menu=menubar)
    self.root.bind("<Control-o>", lambda e: self.open_folder())

  def open_folder(self):
    d = filedialog.askdirectory(title="DICOM フォルダを選択")
    if not d:
      return
    paths = []
    for dirpath, _dirs, files in os.walk(d):
      for f in files:
        paths.append(os.path.join(dirpath, f))
    self.load(paths)

  def open_files(self):
    paths = filedialog.askopenfilenames(
        title="DICOM ファイルを選択 (複数可)",
        filetypes=[("DICOM", "*.dcm *.DCM *.dicom"), ("すべて", "*")])
    if paths:
      self.load(list(paths))

  def load(self, paths):
    self.root.config(cursor="watch")
    self.root.update_idletasks()
    try:
      vol = load_dicom(paths)
    except Exception as e:
      messagebox.showerror("読み込みエラー", str(e))
      return
    finally:
      self.root.config(cursor="")
    self.set_volume(vol)

  # -- 観点7: 肺領域の3D表示 ----------------------------------------------
  def open_volume_render(self):
    if self.vol is None:
      messagebox.showinfo("案内", "先にCTのDICOMを読み込んでください。")
      return
    path = filedialog.askopenfilename(
        title="第5回の肺領域マスク(連続TIFF)を選択",
        filetypes=[("TIFF", "*.tif *.tiff"), ("すべて", "*")])
    if not path:
      return
    flip = messagebox.askyesno(
        "スライス順", "マスクのスライス順を反転しますか？\n"
        "(通常は「いいえ」。3D表示が上下逆のときだけ「はい」)")
    self.root.config(cursor="watch")
    self.root.update_idletasks()
    try:
      import volume_render
      mask = volume_render.load_mask(path)
      self.root.config(cursor="")
      volume_render.show_lung(mask, self.vol, flip_z=flip)  # 閉じるまで待機
    except Exception as e:
      messagebox.showerror("3D表示エラー", str(e))
    finally:
      self.root.config(cursor="")

  # -- ボリューム設定 -----------------------------------------------------
  def set_volume(self, vol):
    self.busy = True
    self.vol = vol
    self.pos = {"z": vol.nz // 2, "y": vol.ny // 2, "x": vol.nx // 2}

    # 全ビュー共通の表示倍率 (物理サイズ比を保つ)
    sizes = [v.physical_size(vol) for v in self.views]
    self.scale = min(min((CANVAS_SIZE - 4) / w, (CANVAS_SIZE - 4) / h)
                     for w, h in sizes)

    dmin, dmax = float(vol.data.min()), float(vol.data.max())
    wl0, ww0 = vol.window if vol.window else (40.0, 400.0)
    maxes = {"z": vol.nz, "y": vol.ny, "x": vol.nx}

    for v in self.views:
      n = maxes[v.axis_key]
      v.pos_scale.config(from_=0, to=max(n - 1, 1))
      v.wl_scale.config(from_=int(dmin), to=int(dmax))
      v.ww_scale.config(from_=1, to=max(int(dmax - dmin), 2))
      v.wl_scale.set(wl0)
      v.ww_scale.set(ww0)
    self.sync_sliders()

    self.busy = False
    self.refresh_all()
    self.status.config(
        text=f"サイズ {vol.nx}x{vol.ny}x{vol.nz}  "
             f"間隔(mm) x={vol.sx:.3f} y={vol.sy:.3f} z={vol.sz:.3f}  "
             f"HU {dmin:.0f}〜{dmax:.0f}")

  def sync_sliders(self):
    """内部座標をスライダーに反映 (コールバックは抑止)"""
    was = self.busy
    self.busy = True
    for v in self.views:
      v.pos_scale.set(self.pos[v.axis_key])
    self.busy = was

  def refresh_all(self):
    if self.vol is None:
      return
    for v in self.views:
      v.render()


def main():
  root = tk.Tk()
  App(root)
  root.mainloop()


if __name__ == "__main__":
  main()
