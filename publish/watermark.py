"""像素图暗码：发帖图里藏一个编号，肉眼看不出，被搬运后拿原图对一下就能读出来（11-21 本人定，从造型演变一帖起用）。

做法：像素图放大后每个像素格是一整块 K×K 同色方块。从「整块同色、亮度不贴边」的方块里，按固定种子挑 48 块，
每块整体加或减 D 个色阶（加＝1，减＝0），写 16 位编号重复 3 遍。平台压缩、缩放会把方块磨成平均色，但平均色
的升降还在，所以抗 JPEG 和缩放；读的时候必须有未加码的原图（clean）做对照。

用法：
  加码：python3 publish/watermark.py embed <clean.png> <out.png> <编号> [--k 8]
  读码：python3 publish/watermark.py read  <clean.png> <可疑图> [--k 8]
  自测：python3 publish/watermark.py selftest <clean.png> [--k 8]   （缩到 1080 宽＋JPEG 75 再读）
编号约定：帖子编号×10＋平台（1 小红书、2 抖音、3 Instagram），例如鹿57 小红书＝571。
"""
import argparse
import hashlib
import io
import random
import sys

import numpy as np
from PIL import Image

BITS, REPEAT, D = 16, 3, 4


def _blocks(clean, k):
    """可用方块：整块同色、亮度 24–230、不在最外一圈；按固定种子打乱后取前 BITS×REPEAT 块。"""
    a = np.asarray(clean.convert('RGB'), dtype=np.int16)
    h, w = a.shape[0] // k, a.shape[1] // k
    cand = []
    for by in range(1, h - 1):
        for bx in range(1, w - 1):
            blk = a[by * k:(by + 1) * k, bx * k:(bx + 1) * k]
            if (blk == blk[0, 0]).all() and 24 <= blk[0, 0].mean() <= 230:
                cand.append((by, bx))
    n = BITS * REPEAT
    if len(cand) < n:
        raise SystemExit(f'可用方块只有 {len(cand)} 块，不够 {n} 块')
    seed = int(hashlib.sha256(f'{a.shape}'.encode()).hexdigest()[:8], 16)
    random.Random(seed).shuffle(cand)
    return a, cand[:n]


def embed(clean_path, out_path, code, k):
    clean = Image.open(clean_path)
    a, blocks = _blocks(clean, k)
    bits = [(code >> i) & 1 for i in range(BITS)] * REPEAT
    b = a.copy()
    for (by, bx), bit in zip(blocks, bits):
        b[by * k:(by + 1) * k, bx * k:(bx + 1) * k] += D if bit else -D
    Image.fromarray(np.clip(b, 0, 255).astype(np.uint8)).save(out_path)
    return out_path


def read(clean_path, suspect, k):
    clean = Image.open(clean_path)
    a, blocks = _blocks(clean, k)
    s = suspect if isinstance(suspect, Image.Image) else Image.open(suspect)
    s = np.asarray(s.convert('RGB').resize(clean.size, Image.LANCZOS), dtype=np.float32)
    m = max(1, k // 4)
    diffs = []
    for by, bx in blocks:
        sl = (slice(by * k + m, (by + 1) * k - m), slice(bx * k + m, (bx + 1) * k - m))
        diffs.append(float(s[sl].mean() - a[sl].mean()))
    votes = np.array(diffs).reshape(REPEAT, BITS)
    bits = (votes.sum(axis=0) > 0).astype(int)
    code = sum(int(v) << i for i, v in enumerate(bits))
    agree = float(((votes > 0) == bits).mean())          # 三遍里各位一致的比例，1.0 最可信
    return code, agree, float(np.abs(votes).mean())


def selftest(clean_path, k, code=12345):
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'wm.png')
        embed(clean_path, out, code, k)
        im = Image.open(out).convert('RGB')
        w, h = im.size
        small = im.resize((1080, round(h * 1080 / w)), Image.LANCZOS)
        buf = io.BytesIO(); small.save(buf, 'JPEG', quality=75); buf.seek(0)
        got, agree, mag = read(clean_path, Image.open(buf), k)
        d = np.abs(np.asarray(im, np.int16) - np.asarray(Image.open(clean_path).convert('RGB'), np.int16))
        print(f'写入 {code} → 缩到 1080 宽＋JPEG75 后读出 {got}（{"对" if got == code else "错"}），'
              f'一致率 {agree:.2f}，平均升降 {mag:.1f} 级；改动像素最大差 {d.max()} 级')
        return got == code


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['embed', 'read', 'selftest'])
    ap.add_argument('args', nargs='+')
    ap.add_argument('--k', type=int, default=8)
    o = ap.parse_args()
    if o.cmd == 'embed':
        embed(o.args[0], o.args[1], int(o.args[2]), o.k)
    elif o.cmd == 'read':
        code, agree, mag = read(o.args[0], o.args[1], o.k)
        print(f'读出编号 {code}，一致率 {agree:.2f}（1.0 最可信，低于 0.8 当读不出），平均升降 {mag:.1f} 级')
    else:
        sys.exit(0 if selftest(o.args[0], o.k) else 1)
