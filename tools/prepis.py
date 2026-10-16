"""
Titulky s časy jednotlivých slov pro data/scenes.js – ve dvou krocích.

1) Přepis řeči na text (Whisper). Výsledek si přečti a oprav překlepy:

    uv run --no-project --with faster-whisper \
        python tools/prepis.py text assets/02-neco.m4a > assets/02-neco.txt

2) Časy slov podle opraveného textu (forced alignment, model MMS):

    uv run --no-project --with torch==2.5.1 --with torchaudio==2.5.1 \
        --with "numpy<2" --with soundfile --with certifi \
        python tools/prepis.py casy assets/02-neco.m4a assets/02-neco.txt

Krok 2 vypíše pole `cues`, které se vloží do scény v data/scenes.js.
Každý titulek = 1–2 věty, každé slovo = [text, začátek, konec] v sekundách.
Modely se při prvním spuštění stáhnou (Whisper ~3 GB, MMS ~1,2 GB).
Převod zvuku používá `afconvert`, který je součástí macOS.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

MAX_WORDS = 18       # delší věta se rozdělí (u čárky nebo u nejdelší pauzy)
MERGE_MAX = 12       # dvě krátké věty se ukážou spolu, pokud mají dohromady nejvýš tolik slov
MERGE_GAP = 1.0      # …a není mezi nimi delší pauza (s)

SENTENCE_END = re.compile(r"[.!?…]['\"»“”)]*$")


# ---------- 1) přepis ----------

def transcribe(audio, model_name="large-v3"):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, language="cs", beam_size=5)
    return "\n".join(s.text.strip() for s in segments)


# ---------- 2) zarovnání textu na zvuk ----------

def load_wav_16k(audio):
    """Načte zvuk jako mono 16 kHz (přes afconvert z macOS)."""
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "a.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEF32@16000", "-c", "1", audio, wav_path], check=True)
        x, _ = sf.read(wav_path, dtype="float32")
    return x.mean(1) if x.ndim == 2 else x


def romanize(word):
    """MMS zná jen a–z: z češtiny stačí odstranit diakritiku."""
    word = unicodedata.normalize("NFKD", word.lower())
    return re.sub(r"[^a-z']", "", "".join(c for c in word if not unicodedata.combining(c)))


def align(audio, text):
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    import torch
    from torchaudio.pipelines import MMS_FA as bundle

    words = [w for w in text.split() if romanize(w)]
    wav = torch.from_numpy(load_wav_16k(audio)).unsqueeze(0)

    model = bundle.get_model()
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()
    with torch.inference_mode():
        emission, _ = model(wav)
        spans = aligner(emission[0], tokenizer([romanize(w) for w in words]))

    sec = wav.size(1) / emission.size(1) / bundle.sample_rate
    return [
        {"w": w, "start": sp[0].start * sec, "end": sp[-1].end * sec}
        for w, sp in zip(words, spans)
    ]


# ---------- seskupení do titulků ----------

def split_long(sentence):
    if len(sentence) <= MAX_WORDS:
        return [sentence]
    mid = len(sentence) / 2
    commas = [i for i, w in enumerate(sentence[:-1]) if w["w"].endswith(",")]
    if commas:
        cut = min(commas, key=lambda i: abs(i + 1 - mid)) + 1
    else:
        cut = max(range(2, len(sentence) - 2), key=lambda i: sentence[i]["start"] - sentence[i - 1]["end"])
    return split_long(sentence[:cut]) + split_long(sentence[cut:])


def group(words):
    sentences, cur = [], []
    for w in words:
        cur.append(w)
        if SENTENCE_END.search(w["w"]):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)

    parts = [p for s in sentences for p in split_long(s)]

    cues, i = [], 0
    while i < len(parts):
        cue = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if (
            nxt
            and SENTENCE_END.search(cue[-1]["w"])
            and len(cue) + len(nxt) <= MERGE_MAX
            and nxt[0]["start"] - cue[-1]["end"] < MERGE_GAP
        ):
            cue = cue + nxt
            i += 1
        cues.append(cue)
        i += 1
    return cues


def to_js(cues):
    out = ["cues: ["]
    for cue in cues:
        out.append("  [")
        for w in cue:
            out.append(f'    [{json.dumps(w["w"], ensure_ascii=False)}, {w["start"]:.2f}, {w["end"]:.2f}],')
        out.append("  ],")
    out.append("],")
    return "\n".join(out)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "text":
        print(transcribe(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "casy":
        text = open(sys.argv[3], encoding="utf-8").read()
        print(to_js(group(align(sys.argv[2], text))))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
