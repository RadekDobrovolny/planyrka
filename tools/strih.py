"""
Prostříhání nahrávky podle scénáře: vystřihne přeřeky (zopakované úseky) a „ehm“,
ztiší nádechy v pauzách a zkrátí ticho na začátku a na konci.

    uv run --no-project --python 3.11 --with faster-whisper --with torch==2.5.1 \\
        --with torchaudio==2.5.1 --with "numpy<2" --with scipy --with soundfile --with certifi \\
        python tools/strih.py assets/puvodni/02-parkoviste.m4a assets/02-parkoviste.txt assets/strih/02-parkoviste.flac

Jak to funguje:
  1. Whisper nahrávku přepíše doslovně (i s „ehm“ a opakováním).
  2. Vystřihne se jen to, co je jasně navíc: „ehm“ a úsek, který se hned potom zopakoval
     (platí POSLEDNÍ verze). Je-li fráze opakovaná i ve scénáři, je to záměr a zůstane.
  3. Slova navíc (improvizace) a odchylky od scénáře se jen označí ke kontrole.
  4. Přesné hranice slov dá zarovnání (model MMS) – střih padne do pauzy, ne do slova.
  5. Nádechy (tichý zvuk bez tónu hlasu v pauze) se ztiší (výchozí −15 dB), ne vystřihnou.
     Ticho na začátku a na konci se zkrátí podle skutečného zvuku.

Vedle výstupu se uloží .json se seznamem zásahů (pro kontrolu v kontrola-strihu.html).

    --prepis soubor.json   použít hotový přepis (seznam slov s časy) místo spuštění Whisperu

Ruční střihy (co automatika nenajde – třeba když Whisper opakování „vyhladí“) se zapisují
do rucni-strihy.json ve složce výstupu: {"02-parkoviste": [[od, do, "poznámka"]]} – časy
v sekundách v původní nahrávce; hranice se doladí na nejtišší místo v okolí ±50 ms.
    --nadechy 15           o kolik dB ztišit nádechy (0 = nechat, 99 = úplně vyhodit)
    --overit               výslednou nahrávku znovu přepsat (text po střihu podle skutečného zvuku)
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import tempfile
import unicodedata

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000
FILLERS = {"ehm", "eh", "hm", "hmm", "em", "ehh", "eee", "ee", "mhm", "öhm", "ehmm", "hmmm"}
PROMPT = "Ehm, no... tak. Já, já jsem tady. Ehm, počkej, ještě jednou. Tady je, ehm, tady je to."


# ---------- načtení ----------

def load(path):
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEF32@{SR}", path, wav], check=True)
        x, sr = sf.read(wav, dtype="float32")
    assert sr == SR
    return x.mean(1) if x.ndim == 2 else x


def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(path, language="cs", word_timestamps=True, beam_size=5,
                               condition_on_previous_text=False, initial_prompt=PROMPT)
    return [{"w": w.word.strip(), "start": w.start, "end": w.end} for s in segs for w in s.words if w.word.strip()]


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def romanize(w):
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z']", "", "".join(c for c in w if not unicodedata.combining(c)))


# ---------- porovnání se scénářem ----------

def sub_cost(a, b):
    if a == b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return 0.3 if r >= 0.8 else 0.7 if r >= 0.6 else 1.5


def align_to_script(T, S):
    """Pro každé slovo přepisu index slova scénáře (nebo None). Při opakování vyhraje pozdější verze."""
    n, m = len(T), len(S)
    D = np.zeros((n + 1, m + 1))
    D[:, 0] = np.arange(n + 1)
    D[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j - 1] + sub_cost(T[i - 1], S[j - 1]), D[i - 1, j] + 1, D[i, j - 1] + 1)
    match = [None] * n
    i, j = n, m
    while i > 0 and j > 0:                       # zpětný průchod od konce → shoda s pozdější verzí
        if np.isclose(D[i, j], D[i - 1, j - 1] + sub_cost(T[i - 1], S[j - 1])):
            match[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif np.isclose(D[i, j], D[i - 1, j] + 1):
            i -= 1
        else:
            j -= 1
    return match


# ---------- přesné časy slov (MMS) ----------

def precise_times(x, words):
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    import torch
    from torchaudio.pipelines import MMS_FA as bundle

    x16 = signal.resample_poly(x, 1, SR // 16000).astype(np.float32)
    idx = [i for i, w in enumerate(words) if romanize(w["w"])]
    model, tok, aligner = bundle.get_model(), bundle.get_tokenizer(), bundle.get_aligner()
    with torch.inference_mode():
        em, _ = model(torch.from_numpy(x16).unsqueeze(0))
        spans = aligner(em[0], tok([romanize(words[i]["w"]) for i in idx]))
    sec = len(x16) / em.size(1) / 16000
    out = [dict(w) for w in words]
    for i, sp in zip(idx, spans):
        out[i]["start"], out[i]["end"] = sp[0].start * sec, sp[-1].end * sec
    return out


# ---------- zvuk ----------

def envelope_db(x, hop=0.01):
    h = int(SR * hop)
    n = len(x) // h
    return 20 * np.log10(np.sqrt((x[: n * h].reshape(n, h) ** 2).mean(1)) + 1e-9)


def quietest(env, t0, t1, hop=0.01):
    """Nejtišší místo v intervalu (pro střih)."""
    a, b = int(t0 / hop), max(int(t1 / hop), int(t0 / hop) + 1)
    seg = env[a:b]
    return (a + int(np.argmin(seg))) * hop if len(seg) else t0


# ---------- přeřeky ----------

def same_word(a, b):
    """Stejné slovo i přes drobnou chybu přepisu (páru / pádu)."""
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def count_phrase(seq, phrase):
    L = len(phrase)
    return sum(all(same_word(seq[p + k], phrase[k]) for k in range(L)) for p in range(len(seq) - L + 1))


def find_retakes(T, S):
    """Úseky (i, j): od slova i začíná pokus, který se od slova j zopakoval znovu.
    Vystřihne se T[i:j] – tedy dřívější pokus i to, co bylo mezi ním a opakováním."""
    cuts = []
    n = len(T)
    i = 0
    while i < n:
        found = None
        if T[i] in FILLERS:
            i += 1
            continue
        for j in range(i + 1, min(n, i + 40)):
            L = 0
            while j + L < n and i + L < j and same_word(T[i + L], T[j + L]):
                L += 1
            if not L:
                continue
            gap = j - (i + L)                       # slova mezi pokusem a opakováním
            phrase = T[j:j + L]
            if gap > 4:
                continue
            if L >= 3:
                ok = any(len(w) >= 4 for w in phrase)
            elif L == 2:
                ok = any(len(w) >= 5 for w in phrase) and gap <= 2
            else:
                ok = gap == 0 and len(phrase[0]) >= 2      # koktnutí „já já“
            # pojistka: když je fráze opakovaná i ve scénáři, je to záměr
            if ok and count_phrase(T, phrase) > count_phrase(S, phrase):
                # nový pokus často začne slovem, které v prvním chybělo („…ehm, když někdo…“):
                # slova mezi pokusy (kromě „ehm“) patří k novému pokusu
                while j - 1 >= i + L and T[j - 1] not in FILLERS:
                    j -= 1
                found = (i, j)
                break
        if found:
            cuts.append(found)
            i = found[1]                          # další hledání až od nového pokusu
        else:
            i += 1
    merged = []
    for i, j in sorted(cuts):
        if merged and i <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], j))
        else:
            merged.append((i, j))
    return merged


def merge_flags(flags):
    """Sousední slova navíc spojit do jedné položky."""
    flags = sorted(flags, key=lambda f: f["start"])
    out = []
    for f in flags:
        if out and f["typ"] == out[-1]["typ"] == "navic" and f["start"] - out[-1]["end"] < 0.6:
            out[-1]["text"] += " " + f["text"]
            out[-1]["end"] = f["end"]
        elif out and f["typ"] == out[-1]["typ"] == "chybi" and abs(f["start"] - out[-1]["start"]) < 0.01:
            out[-1]["text"] += " " + f["text"]
        else:
            out.append(dict(f))
    return out


# ---------- řeč a pauzy ze zvuku ----------

def speech_mask(env, speech, margin=0.06):
    """Rámce s řečí (do 20 dB pod typickou úrovní), rozšířené o rezervu kolem slov."""
    m = env > speech - 20
    k = int(margin / 0.01)
    return np.convolve(m.astype(float), np.ones(2 * k + 1), mode="same") > 0


def pauses(mask, hop=0.01):
    out, i = [], 0
    while i < len(mask):
        if not mask[i]:
            j = i
            while j < len(mask) and not mask[j]:
                j += 1
            out.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    return out


def voiced_fraction(x, t0, t1):
    """Podíl rámců s tónem hlasu (autokorelace v rozsahu 70–400 Hz)."""
    seg = x[int(t0 * SR): int(t1 * SR)]
    frame, hop = int(0.04 * SR), int(0.02 * SR)
    lo, hi = SR // 400, SR // 70
    v = []
    for s0 in range(0, len(seg) - frame, hop):
        f = seg[s0:s0 + frame] - seg[s0:s0 + frame].mean()
        if np.sqrt((f ** 2).mean()) < 1e-4:
            continue
        ac = np.fft.irfft(np.abs(np.fft.rfft(f, 2 * frame)) ** 2)[:frame]
        v.append(ac[lo:hi].max() / (ac[0] + 1e-12))
    return float(np.mean(np.array(v) > 0.6)) if v else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("--prepis")
    ap.add_argument("--nadechy", type=float, default=15)
    ap.add_argument("--overit", action="store_true")
    a = ap.parse_args()

    x = load(a.audio)
    dur = len(x) / SR
    words = json.load(open(a.prepis, encoding="utf-8"))["words"] if a.prepis else transcribe(a.audio)
    # Whisper si v tichu občas vymyslí adresu webu apod. – takové „slovo“ v nahrávce není
    hallucinated = [w for w in words if re.search(r"^\.\w|www|https?:|\.(org|com|cz|net)\b", w["w"].lower())]
    words = [w for w in words if norm(w["w"]) and w not in hallucinated]
    script = open(a.text, encoding="utf-8").read().split()
    S = [norm(w) for w in script if norm(w)]
    T = [norm(w["w"]) for w in words]
    words = precise_times(x, words)
    env = envelope_db(x)
    floor = float(np.percentile(env, 5))
    speech = float(np.median(env[env > env.max() - 30]))
    is_speech = speech_mask(env, speech)
    edits, flags, cut_ranges = [], [], []

    def add_cut(i0, i1, kind):
        """Vystřihnout slova i0..i1-1 a nechat místo nich přirozenou pauzu."""
        prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
        next_start = words[i1]["start"] if i1 < len(words) else dur
        j0, j1 = words[i0]["start"], words[i1 - 1]["end"]
        gap1, gap2 = max(j0 - prev_end, 0), max(next_start - j1, 0)
        pause = float(np.clip(max(gap1, gap2), 0.3, 0.8))
        c0 = prev_end + min(gap1, pause / 2)
        c1 = next_start - min(gap2, pause / 2)
        c0 = quietest(env, max(prev_end + 0.02, c0 - 0.05), min(c0 + 0.05, j0))
        c1 = quietest(env, max(j1, c1 - 0.05), min(c1 + 0.05, next_start - 0.02))
        if c1 > c0:
            cut_ranges.append((c0, c1))
            edits.append({"typ": kind, "text": " ".join(words[k]["w"] for k in range(i0, i1)), "start": c0, "end": c1})

    # --- přeřeky: úsek, který se hned potom zopakoval → platí poslední verze ---
    cut_words = set()
    for i0, i1 in find_retakes(T, S):
        add_cut(i0, i1, "opakovani")
        cut_words.update(range(i0, i1))
    # --- ruční střihy ---
    manual_file = os.path.join(os.path.dirname(a.out) or ".", "rucni-strihy.json")
    scene_key = os.path.splitext(os.path.basename(a.out))[0]
    manual = json.load(open(manual_file, encoding="utf-8")).get(scene_key, []) if os.path.exists(manual_file) else []
    for m0, m1, *note in manual:
        # střih až k začátku / konci nahrávky jde přesně ke kraji
        c0 = 0.0 if m0 <= 0.06 else quietest(env, max(m0 - 0.05, 0), m0 + 0.05)
        c1 = dur if m1 >= dur - 0.06 else quietest(env, m1 - 0.05, min(m1 + 0.05, dur))
        cut_ranges.append((c0, c1))
        edits.append({"typ": "rucne", "text": note[0] if note else "ruční střih", "start": c0, "end": c1})
        cut_words.update(k for k, w in enumerate(words) if c0 <= (w["start"] + w["end"]) / 2 <= c1)
    # --- samostatné „ehm“ ---
    for k, w in enumerate(T):
        if w in FILLERS and k not in cut_words:
            add_cut(k, k + 1, "ehm")
            cut_words.add(k)

    # odchylky od scénáře – jen ke kontrole (často je to jen chyba přepisu); počítá se z výsledné nahrávky
    kept = [k for k in range(len(words)) if k not in cut_words]
    match = [None] * len(words)
    for k, mm in zip(kept, align_to_script([T[k] for k in kept], S)):
        match[k] = mm
    for k, mm in enumerate(match):
        if k in cut_words:
            continue
        if mm is None:
            flags.append({"typ": "navic", "text": words[k]["w"], "start": words[k]["start"], "end": words[k]["end"]})
        elif T[k] != S[mm]:
            flags.append({"typ": "jinak", "text": f"{words[k]['w']}  (scénář: {script_word(script, mm)})",
                          "start": words[k]["start"], "end": words[k]["end"]})
    matched = {mm for k, mm in enumerate(match) if mm is not None and k not in cut_words}
    for j in range(len(S)):
        if j not in matched:
            near = [words[k]["end"] for k, mm in enumerate(match) if mm is not None and mm < j]
            t = near[-1] if near else 0.0
            flags.append({"typ": "chybi", "text": script_word(script, j), "start": t, "end": t + 0.5})
    flags = merge_flags(flags)

    # --- ticho na začátku a na konci (podle skutečného zvuku, ne podle přepisu) ---
    sp = np.flatnonzero(is_speech)
    first = min(sp[0] * 0.01, words[0]["start"]) if len(sp) else words[0]["start"]
    last = max((sp[-1] + 1) * 0.01, words[-1]["end"]) if len(sp) else words[-1]["end"]
    head, tail = max(first - 0.4, 0), min(last + 0.8, dur)
    if head > 0.05:
        cut_ranges.append((0.0, head))
        edits.append({"typ": "ticho", "text": "ticho na začátku", "start": 0.0, "end": head})
    if dur - tail > 0.05:
        cut_ranges.append((tail, dur))
        edits.append({"typ": "ticho", "text": "ticho na konci", "start": tail, "end": dur})
    cut_ranges.sort()

    # --- nádechy: tichý neznělý zvuk v pauze mezi řečí → ztišit ---
    # chráněné je vše, co je řeč podle zvuku, a každé slovo podle zarovnání (+50 ms)
    protect = is_speech.copy()
    for k in kept:
        protect[max(int((words[k]["start"] - 0.05) / 0.01), 0): int((words[k]["end"] + 0.05) / 0.01) + 1] = True
    gain = np.ones(len(x), dtype=np.float32)
    if a.nadechy > 0:
        for p0, p1 in pauses(protect):
            if p1 - p0 < 0.12 or p0 < head or p1 > tail or any(c0 <= p0 and p1 <= c1 for c0, c1 in cut_ranges):
                continue
            seg = env[int(p0 / 0.01): int(p1 / 0.01)]
            if not len(seg) or seg.max() < max(floor + 15, speech - 55):
                continue                                     # v pauze nic slyšitelného
            if voiced_fraction(x, p0, p1) > 0.3:
                continue                                     # má tón hlasu – nesahat
            g = 10 ** (-min(a.nadechy, 80) / 20)
            s0, s1 = int(p0 * SR), int(p1 * SR)
            ramp = min(int(0.02 * SR), (s1 - s0) // 3)
            curve = np.full(s1 - s0, g, dtype=np.float32)
            fade = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ramp))
            curve[:ramp] = 1 - (1 - g) * fade
            curve[-ramp:] = g + (1 - g) * fade
            gain[s0:s1] = np.minimum(gain[s0:s1], curve)
            edits.append({"typ": "nadech", "text": f"nádech ({seg.max() - speech:.0f} dB proti řeči) ztišen o {a.nadechy:.0f} dB",
                          "start": p0, "end": p1})
    y = x * gain

    # --- střih s prolnutím ---
    keep, t = [], 0.0
    for c0, c1 in cut_ranges:
        if c0 > t:
            keep.append((t, c0))
        t = max(t, c1)
    if t < dur:
        keep.append((t, dur))
    xf = int(0.025 * SR)
    out = np.zeros(0, dtype=np.float32)
    new_at = []
    for s, e in keep:
        piece = y[int(s * SR): int(e * SR)].copy()
        if len(out) and len(piece) > xf and len(out) > xf:
            ph = np.linspace(0, np.pi / 2, xf, dtype=np.float32)
            out[-xf:] = out[-xf:] * np.cos(ph) + piece[:xf] * np.sin(ph)
            piece = piece[xf:]
        new_at.append((s, e, len(out) / SR))
        out = np.concatenate([out, piece])

    def to_new(t):
        for s, e, n0 in new_at:
            if t <= e:
                return n0 + max(t - s, 0)
        return len(out) / SR

    for ed in edits + flags:
        ed["new_at"] = round(to_new(ed["start"] if ed["typ"] in ("nadech", "jinak", "navic", "chybi") else ed["end"]), 3)
        ed["start"], ed["end"] = round(ed["start"], 3), round(ed["end"], 3)

    # krátké zeslabení na začátku a konci, aby po oříznutí nic necvaklo
    fi, fo = min(int(0.02 * SR), len(out) // 4), min(int(0.08 * SR), len(out) // 4)
    out[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    out[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    sf.write(a.out, out, SR, subtype="PCM_24")
    report = {
        "zdroj": a.audio, "vystup": a.out,
        "delka_pred": round(dur, 2), "delka_po": round(len(out) / SR, 2),
        "zasahy": sorted(edits, key=lambda e: e["start"]),
        "ke_kontrole": sorted(flags, key=lambda e: e["start"]),
        "prepis": " ".join(w["w"] for w in words),
        "vyrazeno_z_prepisu": [w["w"] for w in hallucinated],
        "text_po_strihu": (" ".join(w["w"] for w in transcribe(a.out)) if a.overit
                           else " ".join(w["w"] for k, w in enumerate(words) if k not in cut_words)),
    }
    json.dump(report, open(os.path.splitext(a.out)[0] + ".json", "w"), ensure_ascii=False, indent=1)
    cuts = [e for e in edits if e["typ"] in ("ehm", "opakovani", "rucne")]
    print(f"{os.path.basename(a.out)}: {dur:.1f} s → {len(out)/SR:.1f} s | vystřiženo {len(cuts)} | "
          f"nádechů {sum(e['typ'] == 'nadech' for e in edits)} | ke kontrole {len(flags)}")


def script_word(script, j):
    return [w for w in script if norm(w)][j]


if __name__ == "__main__":
    main()
