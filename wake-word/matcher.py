"""Personal wake-word matching: MFCC features + DTW against your own recordings.

No speech-to-text: a spoken segment is compared to template recordings of the
wake phrase, so it learns your exact pronunciation and accent.
"""
import glob, os, wave

import numpy as np

RATE = 16000
WIN, HOP, NFFT, NMEL, NCEP = 400, 160, 512, 26, 13   # 25 ms / 10 ms frames


def _mel_bank():
    def hz2mel(h): return 2595 * np.log10(1 + h / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(80), hz2mel(7600), NMEL + 2))
    bins = np.floor((NFFT + 1) * pts / RATE).astype(int)
    fb = np.zeros((NMEL, NFFT // 2 + 1))
    for i in range(1, NMEL + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        fb[i - 1, l:c] = (np.arange(l, c) - l) / max(c - l, 1)
        fb[i - 1, c:r] = (r - np.arange(c, r)) / max(r - c, 1)
    return fb


_FB = _mel_bank()
_WINDOW = np.hamming(WIN)
_k = np.arange(NMEL)
_DCT = np.cos(np.pi / NMEL * (_k[None, :] + 0.5) * np.arange(NCEP)[:, None])


def mfcc(audio, norm_frames=None):
    """audio: int16/float array at 16 kHz -> (frames, 12) normalized features.
    norm_frames: compute the normalization from only the first N frames."""
    x = np.asarray(audio, dtype=np.float64)
    if len(x) < WIN:
        x = np.pad(x, (0, WIN - len(x)))
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * _WINDOW
    power = np.abs(np.fft.rfft(frames, NFFT)) ** 2 / NFFT
    mel = np.log(power @ _FB.T + 1e-6)
    cep = (mel @ _DCT.T)[:, 1:]                   # drop c0 (loudness)
    ref = cep[:norm_frames] if norm_frames else cep
    cep = cep - ref.mean(axis=0)                  # cepstral mean normalization
    return cep / (ref.std(axis=0) + 1e-6)


def dtw(a, b):
    """Length-normalized DTW distance between two feature sequences."""
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0.0
    for i in range(1, n + 1):
        row, prev = acc[i], acc[i - 1]
        c = cost[i - 1]
        for j in range(1, m + 1):
            row[j] = c[j - 1] + min(prev[j], prev[j - 1], row[j - 1])
    return acc[n, m] / (n + m)


def trim(audio, rel=0.08, floor=50.0, pad=0.08, gap=0.3):
    """Cut a clip down to its longest stretch of speech (energy based)."""
    a = np.asarray(audio, dtype=np.float64)
    fr = a[: len(a) // 160 * 160].reshape(-1, 160)
    r = np.sqrt((fr ** 2).mean(1))
    on = np.where(r > max(r.max() * rel, floor))[0]
    if not len(on):
        return a[:0]
    segs, s, prev = [], on[0], on[0]
    for i in on[1:]:
        if i - prev > gap * 100:
            segs.append((s, prev)); s = i
        prev = i
    segs.append((s, prev))
    s, e = max(segs, key=lambda x: x[1] - x[0])
    p = int(pad * 100)
    s, e = max(s - p, 0), min(e + p + 1, len(r))
    return a[s * 160:e * 160]


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def load_templates(directory):
    out = []
    for f in sorted(glob.glob(os.path.join(directory, "*.wav"))):
        t = trim(read_wav(f))
        if len(t) > RATE * 0.2:
            out.append((os.path.basename(f), mfcc(t)))
    return out


def score(segment_feats, templates, k=3):
    """Mean of the k best template distances (lower = more like the wake word)."""
    d = sorted(dtw(segment_feats, t) for _, t in templates)
    return float(np.mean(d[:k])), d


def dtw_open_end(t, s, min_ratio=0.6):
    """DTW of template t against the *start* of s: the end point in s is free,
    so words after the wake phrase don't count. Returns (distance, end_frame)."""
    cost = np.sqrt(((t[:, None, :] - s[None, :, :]) ** 2).sum(-1))
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0.0
    for i in range(1, n + 1):
        row, prev = acc[i], acc[i - 1]
        c = cost[i - 1]
        for j in range(1, m + 1):
            row[j] = c[j - 1] + min(prev[j], prev[j - 1], row[j - 1])
    j0 = max(int(n * min_ratio), 1)
    norm = acc[n, j0:] / (n + np.arange(j0, m + 1))
    j = int(np.argmin(norm))
    return float(norm[j]), j0 + j


def score_prefix(seg_feats, templates, k=3):
    """Like score(), for a segment that starts with the wake phrase and goes on.
    Returns (score, end_frame) where end_frame is where the phrase ends."""
    res = sorted(dtw_open_end(t, seg_feats) for _, t in templates)
    best = res[:k]
    ends = sorted(e for _, e in best)
    return float(np.mean([d for d, _ in best])), ends[len(ends) // 2]
