import argparse
import os

import numpy as np


def load_mask(path):
  """連続TIFFを読み込み、(z, y, x) の bool 配列を返す"""
  import tifffile

  m = tifffile.imread(path)
  if m.ndim == 2:
    m = m[np.newaxis]
  elif m.ndim == 4:          # RGB(A) で保存されている場合
    m = m.max(axis=-1)
  elif m.ndim != 3:
    raise ValueError(f"想定外のTIFF次元数です: shape={m.shape}")

  mask = m > 0
  if mask.mean() > 0.5:      # 背景が白(255)・肺が黒(0)で保存されている場合
    print("マスクの白黒が逆と判断し、反転しました。")
    mask = ~mask
  return mask


def _bbox_slices(mask, margin=3):
  """マスクのバウンディングボックス (余白付き)"""
  slices = []
  for axis, n in zip(range(3), mask.shape):
    other = tuple(a for a in range(3) if a != axis)
    idx = np.where(mask.any(axis=other))[0]
    slices.append((max(idx[0] - margin, 0), min(idx[-1] + margin + 1, n)))
  return slices


def _setup_keys(pl, rot=10.0, zoom=1.1):
  """キーボード操作 (マウス無しでも回転・拡大縮小できる)"""
  cam = pl.camera

  def rotate(az=0.0, el=0.0):
    def f():
      if az:
        cam.Azimuth(az)
      if el:
        cam.Elevation(el)
        cam.OrthogonalizeViewUp()
      pl.render()
    return f

  def do_zoom(factor):
    def f():
      cam.Zoom(factor)
      pl.render()
    return f

  def reset():
    pl.view_xz()
    pl.render()

  pl.add_key_event("Left", rotate(az=rot))
  pl.add_key_event("Right", rotate(az=-rot))
  pl.add_key_event("Up", rotate(el=rot))
  pl.add_key_event("Down", rotate(el=-rot))
  for k in ("plus", "equal", "Prior"):       # + / = / PageUp : 拡大
    pl.add_key_event(k, do_zoom(zoom))
  for k in ("minus", "Next"):                # - / PageDown : 縮小
    pl.add_key_event(k, do_zoom(1.0 / zoom))
  pl.add_key_event("Home", reset)            # 正面に戻す


def show_lung(mask, vol=None, spacing=(1.0, 1.0, 1.0), flip_z=False,
              step=2, mode="ct", screenshot=None):
  """
  mask : (z, y, x) bool
  vol  : main.Volume (DICOM)。渡すと間隔・CT値を利用
  spacing : vol が無いときの (sz, sy, sx) [mm]
  step : 面内の間引き率 (大きいほど軽い)
  mode : "ct"   = 肺領域内のCT値でレンダリング (血管・気管支が見える)
         "mask" = マスク形状のみをレンダリング
  """
  import pyvista as pv
  from scipy import ndimage

  if flip_z:
    mask = mask[::-1]
  if not mask.any():
    raise ValueError("マスクが空です (肺領域が見つかりません)。")

  ct = None
  if vol is not None:
    if mask.shape != vol.data.shape:
      raise ValueError(
          f"マスクのサイズ {mask.shape} (z,y,x) が DICOM {vol.data.shape} と一致しません。\n"
          "同じCT系列から作成したマスクか確認してください。")
    ct = vol.data
    sz, sy, sx = vol.sz, vol.sy, vol.sx
  else:
    sz, sy, sx = spacing
    mode = "mask"

  # 肺の範囲だけ切り出し + 面内を間引いて高速化
  (z0, z1), (y0, y1), (x0, x1) = _bbox_slices(mask)
  sl = (slice(z0, z1), slice(y0, y1, step), slice(x0, x1, step))
  mask = mask[sl]
  sy, sx = sy * step, sx * step

  # 不透明度は「少数の制御点のリスト」で指定する (0〜255 を等間隔に補間)。
  # 256要素の配列で渡すと、この pyvista では描画されないことがあるため。
  if mode == "ct":
    c = np.clip(ct[sl], -1000, 200)
    s = 1 + (c + 1000) / 1200.0 * 254.0           # 1..255 (0 は領域外)
    s = np.where(mask, s, 0).astype(np.uint8)
    # 制御点: s=0, 64, 128, 191, 255 (CT値 約 -1000, -700, -400, -100, 200)
    opacity = [0.0, 0.15, 0.25, 0.60, 0.95]         # CT値が高いほど不透明
  else:
    f = ndimage.gaussian_filter(mask.astype(np.float32), sigma=1.0)
    s = (np.clip(f, 0, 1) * 255).astype(np.uint8)
    opacity = [0.0, 0.0, 0.3, 0.8, 0.8]            # 表面を滑らかに

  nz, ny, nx = s.shape
  grid = pv.ImageData(dimensions=(nx, ny, nz), spacing=(sx, sy, sz))
  # (z,y,x) -> x が最も速く変化する並びへ
  grid.point_data["v"] = s.transpose(2, 1, 0).flatten(order="F")

  pl = pv.Plotter(off_screen=screenshot is not None, window_size=(900, 900))
  pl.set_background("black")
  if mode == "ct":
    # 肺野(低CT値)が黒くならないよう、青灰色→白のカラーマップを使う
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list(
        "lung", ["#6f87a8", "#aebfd6", "#ffffff"])
  else:
    cmap = "bone"
  pl.add_volume(grid, scalars="v", clim=[0, 255], cmap=cmap,
                opacity=opacity, shade=True, show_scalar_bar=False)
  pl.add_text("Arrows: rotate / +,-: zoom / Home: reset / Q: close\n"
              "(Mouse: drag=rotate, wheel=zoom)",
              font_size=9, color="white")
  pl.view_xz()   # 正面(前方)から見た向き
  _setup_keys(pl)
  if screenshot:
    pl.show(screenshot=screenshot)
    print("保存しました:", screenshot)
  else:
    pl.show()


def main():
  from main import load_dicom

  ap = argparse.ArgumentParser(description="肺領域のボリュームレンダリング")
  ap.add_argument("--mask", required=True, help="第5回で作成した連続TIFF")
  ap.add_argument("--dicom", help="CTのDICOMフォルダ (指定するとCT値・間隔を利用)")
  ap.add_argument("--spacing", nargs=3, type=float, default=[1.0, 1.0, 1.0],
                  metavar=("SZ", "SY", "SX"), help="DICOM無し時のボクセル間隔[mm]")
  ap.add_argument("--flip-z", action="store_true",
                  help="マスクのスライス順が逆のとき指定")
  ap.add_argument("--step", type=int, default=2, help="面内の間引き率 (既定: 2)")
  ap.add_argument("--mode", choices=["ct", "mask"], default="ct")
  ap.add_argument("--screenshot", help="指定するとオフスクリーン描画してPNG保存")
  a = ap.parse_args()

  mask = load_mask(a.mask)
  vol = None
  if a.dicom:
    paths = [os.path.join(d, f) for d, _s, fs in os.walk(a.dicom) for f in fs]
    vol = load_dicom(paths)
  show_lung(mask, vol, tuple(a.spacing), a.flip_z, a.step, a.mode, a.screenshot)


if __name__ == "__main__":
  main()
