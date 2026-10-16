"""
Úprava mluveného slova, aby znělo „studiově“ – beze změny časování (titulky dál sedí).

    uv run --no-project --with numpy --with scipy --with soundfile --with pyloudnorm \
        python tools/zvuk.py assets/puvodni/02-neco.m4a assets/02-neco.m4a

Řetězec:
  1. horní propust 70 Hz (dunění, otřesy)
  2. korekční EQ – spektrum hlasu se srovná podle průměrného spektra řeči
     (ubere „huhlavé“ basy, vrátí srozumitelnost 1–4 kHz a vzduch ve výškách)
  3. de-esser – ztlumí jen ostré sykavky, které by po EQ vynikly
  4. jemná komprese (2,5:1) – vyrovná hlasitost slov
  5. expander – potichu ztlumí šum a ruchy v pauzách
  6. hlasitost −16 LUFS (standard pro web/podcasty), špičky max. −1,5 dBTP

    --sila 1.0    jak moc EQ koriguje (0.6 = jemně, 1.0 = naplno)
    --hlas auto   cílové spektrum pro mužský / ženský hlas (auto = podle výšky hlasu)
    --telo 8.5    o kolik dB víc „těla“ kolem základního tónu hlasu, než má průměrná řeč
                  (všechny nahrávky se srovnají na stejnou úroveň – scény pak zní stejně)

Všechny filtry jsou s nulovou fází / centrované, takže se zvuk v čase neposune.
Převod formátů používá `afconvert` z macOS.
"""
import argparse
import os
import subprocess
import tempfile

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

# třetinooktávová pásma
FC = np.array([50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
               2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000], float)
# průměrné dlouhodobé spektrum řeči (úrovně v pásmech, dB; zhruba podle Byrne et al. 1994)
LTASS = {
    # mužský hlas: základní tón kolem 100–150 Hz, proto víc basů
    "muz":  np.array([36, 40, 45, 54, 57.5, 57, 58, 59.5, 60, 60, 59, 57, 55, 53, 51, 49.5,
                      48.5, 47.5, 46.5, 45.5, 44, 42.5, 41, 39.5, 37.5, 34], float),
    "zena": np.array([34, 38, 43, 48, 53, 55.5, 57, 59, 60, 60, 59, 57, 55, 53, 51, 49.5,
                      48.5, 47.5, 46.5, 45.5, 44, 42.5, 41, 39.5, 37.5, 34], float),
}
# + mírné rozhlasové zvýraznění prezence a „vzduchu“
PRESENCE = np.interp(np.log2(FC), np.log2([1500, 3000, 5000, 8000, 11000, 16000]), [0, 2, 2.5, 1, 2, 1])
# „tělo“ hlasu kolem základního tónu (mužský hlas ~110 Hz) – tvar, který se násobí volbou --telo;
# výchozích 8,5 dB odpovídá schválené studiové verzi první nahrávky
BODY_SHAPE = {"muz": np.interp(np.log2(FC), np.log2([80, 100, 125, 160, 200]), [0, 1, 1, 0.5, 0], left=0, right=0),
              "zena": np.interp(np.log2(FC), np.log2([160, 200, 250, 315, 400]), [0, 1, 1, 0.5, 0], left=0, right=0)}


def target(voice, body_db=0.0):
    goal = LTASS[voice] + PRESENCE + body_db * BODY_SHAPE[voice]
    return goal - 10 * np.log10(0.2316 * FC)                       # úroveň v pásmu → hustota na Hz


def median_f0(y, sr):
    """Hrubý odhad výšky hlasu (autokorelace na znělých úsecích)."""
    y16 = signal.resample_poly(y, 16000, sr)
    n, hop = 1024, 256
    frames = np.lib.stride_tricks.sliding_window_view(y16, n)[::hop]
    energy = (frames ** 2).mean(1)
    lo, hi = 16000 // 400, 16000 // 60
    f0s = []
    for fr in frames[energy > energy.max() * 10 ** (-2.5)]:
        fr = (fr - fr.mean()) * np.hanning(n)
        ac = np.fft.irfft(np.abs(np.fft.rfft(fr, 2 * n)) ** 2)[:n]
        ac /= ac[0] + 1e-12
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.5:
            f0s.append(16000 / k)
    return float(np.median(f0s)) if f0s else 150.0


def speech_mask(y, sr):
    hop = int(sr * 0.02)
    frames = np.lib.stride_tricks.sliding_window_view(y, hop)[::hop]
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    return np.repeat(db > db.max() - 30, hop)[: len(y)]


def band_density(y, sr):
    mask = speech_mask(y, sr)
    f, P = signal.welch(y[: len(mask)][mask], sr, nperseg=8192)
    return np.array([
        10 * np.log10(P[(f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))].mean() + 1e-20)
        for fc in FC
    ])


def highpass(y, sr):
    return signal.sosfiltfilt(signal.butter(4, 70, "highpass", fs=sr, output="sos"), y)


def eq_curve(y, sr, goal, strength, max_boost, max_cut, smooth=True):
    measured = band_density(y, sr)
    anchor = (FC >= 400) & (FC <= 630)
    corr = (goal - goal[anchor].mean()) - (measured - measured[anchor].mean())
    if smooth:
        corr = np.convolve(np.pad(corr, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    corr = np.clip(strength * corr, -max_cut, max_boost)
    corr[FC < 70] = np.minimum(corr[FC < 70], 0)
    return corr


def apply_eq(y, sr, corr):
    grid = np.linspace(0, sr / 2, 4097)
    gain_db = np.interp(np.log2(np.maximum(grid, 20)), np.log2(FC), corr)
    fir = signal.firwin2(8191, grid, 10 ** (gain_db / 20), fs=sr)   # lineární fáze → žádný posun v čase
    return signal.fftconvolve(y, fir, mode="same")


def match_eq(y, sr, strength, voice, body_db=8.5, max_boost=18, max_cut=20):
    """Korekční EQ ve dvou průchodech: druhý změří, co po prvním zbylo, a doladí to."""
    goal = target(voice, body_db)
    corr = eq_curve(y, sr, goal, strength, max_boost, max_cut)
    y = apply_eq(y, sr, corr)
    rest = eq_curve(y, sr, goal, strength, 6, 6, smooth=False)   # jen malé zbytky, bez vyhlazení
    return apply_eq(y, sr, rest), corr + rest


def deess(y, sr):
    nper, hop = 1024, 256
    f, _, Z = signal.stft(y, sr, nperseg=nper, noverlap=nper - hop)
    band = (f >= 4500) & (f <= 11000)
    lvl = 10 * np.log10((np.abs(Z[band]) ** 2).sum(0) + 1e-12)
    tot = 10 * np.log10((np.abs(Z) ** 2).sum(0) + 1e-12)
    active = tot > tot.max() - 35
    sib = lvl - tot                                   # jak moc je rámec „sykavý“
    over = np.maximum(sib - np.percentile(sib[active], 90), 0) * active
    red_db = signal.filtfilt(np.ones(3) / 3, [1], -np.minimum(over * (1 - 1 / 3), 6))
    Z[band] *= 10 ** (red_db / 20)
    _, out = signal.istft(Z, sr, nperseg=nper, noverlap=nper - hop)
    return out[: len(y)]


def compress(y, sr, ratio=2.5, knee=6.0):
    blk = int(sr * 0.001)
    nb = len(y) // blk
    db = 20 * np.log10(np.sqrt((y[: nb * blk].reshape(nb, blk) ** 2).mean(1) + 1e-12))
    att, rel = np.exp(-1 / 8), np.exp(-1 / 120)       # 8 ms / 120 ms
    env = np.empty_like(db)
    s = db[0]
    for i, v in enumerate(db):
        c = att if v > s else rel
        s = c * s + (1 - c) * v
        env[i] = s
    thr = np.percentile(env[env > env.max() - 30], 55)
    d = env - thr
    gr = np.where(d <= -knee / 2, 0,
         np.where(d >= knee / 2, d * (1 / ratio - 1), (1 / ratio - 1) * (d + knee / 2) ** 2 / (2 * knee)))
    return y * np.interp(np.arange(len(y)), np.arange(nb) * blk + blk / 2, 10 ** (gr / 20))


def expand(y, sr, below_db=38, depth_db=12):
    blk = int(sr * 0.01)
    nb = -(-len(y) // blk)
    yp = np.pad(y, (0, nb * blk - len(y)))
    db = uniform_filter1d(20 * np.log10(np.sqrt((yp.reshape(nb, blk) ** 2).mean(1)) + 1e-9), 5)
    red = np.clip((np.percentile(db, 99) - below_db - db) * 1.5, 0, depth_db)
    red = uniform_filter1d(red, 9)                    # ~90 ms, ať to „nepumpuje“
    return y * np.interp(np.arange(len(y)), np.arange(nb) * blk + blk / 2, 10 ** (-red / 20))


def loudness_and_limit(y, sr, lufs=-16.0, ceiling_db=-1.5):
    meter = pyln.Meter(sr)
    y = pyln.normalize.loudness(y, meter.integrated_loudness(y), lufs)
    ceiling = 10 ** (ceiling_db / 20)
    peaks = np.abs(signal.resample_poly(y, 4, 1))[: len(y) * 4].reshape(len(y), 4).max(1)
    need = np.minimum(1, ceiling / np.maximum(peaks, 1e-9))
    w = int(sr * 0.004) | 1
    need = uniform_filter1d(minimum_filter1d(need, w), w)   # centrovaný „lookahead“
    return np.clip(y * need, -ceiling, ceiling)


def process(y, sr, strength=1.0, voice="auto", body_db=8.5):
    if voice == "auto":
        voice = "muz" if median_f0(y, sr) < 165 else "zena"
    y = highpass(y, sr)
    y, _ = match_eq(y, sr, strength, voice, body_db)
    y = deess(y, sr)
    y = compress(y, sr)
    y = expand(y, sr)
    return loudness_and_limit(y, sr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst", help=".m4a nebo .wav")
    ap.add_argument("--sila", type=float, default=1.0)
    ap.add_argument("--hlas", choices=["auto", "muz", "zena"], default="auto")
    ap.add_argument("--telo", type=float, default=8.5)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wav_in = os.path.join(tmp, "in.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEF32@48000", args.src, wav_in], check=True)
        x, sr = sf.read(wav_in)
        mono = x.mean(1) if x.ndim == 2 else x

        y = process(mono, sr, args.sila, args.hlas, args.telo).astype(np.float32)

        if args.dst.lower().endswith(".wav"):
            sf.write(args.dst, y, sr, subtype="FLOAT")
        else:
            wav_out = os.path.join(tmp, "out.wav")
            sf.write(wav_out, y, sr, subtype="FLOAT")
            subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "160000", "-q", "127",
                            wav_out, args.dst], check=True)
    print(f"{args.src} → {args.dst}")


if __name__ == "__main__":
    main()
