"""Turn a folder of voice clips into a Piper training dataset.

Usage:
    uv run python tools/prep_voice_dataset.py <clips folder> <output folder> [--glitch <glitch folder>] [--model medium.en]

Writes:
    <output>/wavs/*.wav    22050 Hz mono, silence trimmed, volume normalized
    <output>/metadata.csv  one "file.wav|transcript" line per clip
    <output>/review.txt    clips worth a listen, most suspicious first

Clips longer than 15 seconds get cut at pauses instead of being thrown away.

With --glitch, it learns what your glitch pile sounds like and flags the clips that sound
most like it, to catch glitchy ones that slipped into the calm pile while sorting.

Always read through metadata.csv afterwards. Whisper misspells names like Tenno, Lotus
or Orbiter, and every transcript has to match the audio exactly for training to work.
"""

import argparse
import wave
from pathlib import Path

import av
import numpy as np
from numpy.typing import NDArray
from faster_whisper import WhisperModel # pyright: ignore[reportMissingTypeStubs]

RATE = 22050
WHISPER_RATE = 16000
MIN_SECONDS = 1.0
MAX_SECONDS = 15.0
UNSURE_LOGPROB = -0.8
AUDIO_EXTS = {".wav", ".ogg", ".mp3", ".flac", ".m4a", ".opus", ".webm"}

FRAME_SECONDS = 0.02
MIN_PAUSE = 0.3
MIN_PIECE = 0.4

FFT_SIZE = 1024
BANDS = 24
GLITCH_FLAGS = 20

def decode_audio(path: Path, sampling_rate: int) -> NDArray[np.float32]:
    """Decode any audio file to mono float32. faster-whisper's own decode_audio breaks on PyAV 19+."""

    resampler = av.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks: list[NDArray[np.int16]] = []

    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().flatten())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().flatten())

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0

def resample(audio: NDArray[np.float32], rate: int) -> NDArray[np.float32]:
    """Convert RATE audio to another sample rate. Linear interpolation is plenty for Whisper."""

    size = int(audio.size * rate / RATE)
    return np.interp(np.linspace(0, audio.size - 1, size), np.arange(audio.size), audio).astype(np.float32)

def trim(audio: NDArray[np.float32]) -> NDArray[np.float32]:
    """Cut leading and trailing silence, keeping a little padding."""

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak == 0.0:
        return audio

    loud = np.nonzero(np.abs(audio) > peak * 0.02)[0]
    pad = int(RATE * 0.1)
    return audio[max(loud[0] - pad, 0):loud[-1] + pad]

def pause_spans(audio: NDArray[np.float32]) -> list[tuple[int, int]]:
    """Find the stretches of speech between pauses of MIN_PAUSE seconds or longer, as sample ranges."""

    frame = int(RATE * FRAME_SECONDS)
    count = audio.size // frame
    if count == 0:
        return []

    rms = np.sqrt(np.mean(audio[:count * frame].reshape(count, frame) ** 2, axis=1))
    quiet = rms < max(float(rms.max()) * 0.05, 1e-4)
    pause_frames = int(MIN_PAUSE / FRAME_SECONDS)

    spans: list[tuple[int, int]] = []
    start: int | None = None
    run = 0
    for i, is_quiet in enumerate(quiet):
        if not is_quiet:
            if start is None:
                start = i
            run = 0
        elif start is not None:
            run += 1
            if run >= pause_frames:
                spans.append((start, i - run + 1))
                start = None
    if start is not None:
        spans.append((start, count))

    pad = int(0.05 / FRAME_SECONDS)
    return [
        (max(a - pad, 0) * frame, min(b + pad, count) * frame)
        for a, b in spans
        if (b - a) * FRAME_SECONDS >= MIN_PIECE
    ]

def split_long(audio: NDArray[np.float32]) -> list[NDArray[np.float32]]:
    """Cut a clip that's too long at its pauses, keeping each piece as long as possible."""

    if audio.size <= MAX_SECONDS * RATE:
        return [audio]

    groups: list[tuple[int, int]] = []
    for a, b in pause_spans(audio):
        if groups and b - groups[-1][0] <= MAX_SECONDS * RATE:
            groups[-1] = (groups[-1][0], b)
        else:
            groups.append((a, b))
    return [audio[a:b] for a, b in groups]

def sound_features(audio: NDArray[np.float32]) -> NDArray[np.float64]:
    """Describe how a clip sounds: tone per frequency band, how much it shifts over time, and how noisy it is."""

    count = audio.size // FFT_SIZE
    frames = audio[:count * FFT_SIZE].reshape(count, FFT_SIZE)
    power = np.abs(np.fft.rfft(frames * np.hanning(FFT_SIZE), axis=1)) ** 2 + 1e-12

    # Only look at frames where he's actually talking
    energy = power.sum(axis=1)
    voiced = energy > energy.max() * 0.01
    power, frames, energy = power[voiced], frames[voiced], energy[voiced]

    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
    edges = np.geomspace(100, RATE / 2, BANDS + 1)
    bands = np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
    tone = 10 * np.log10(bands)
    tone -= tone.mean(axis=1, keepdims=True)

    flatness = np.log(power).mean(axis=1) - np.log(power.mean(axis=1))
    flux = np.sqrt((np.diff(tone, axis=0) ** 2).mean(axis=1)) if len(tone) > 1 else np.zeros(1)
    crossings = (np.abs(np.diff(np.sign(frames), axis=1)) > 0).mean(axis=1)
    centroid = np.log((power * freqs).sum(axis=1) / energy)

    return np.concatenate([
        tone.mean(axis=0), tone.std(axis=0),
        [flatness.mean(), flatness.std(), flux.mean(), flux.std(), crossings.mean(), crossings.std(),
         (10 * np.log10(energy)).std(), centroid.mean(), centroid.std()],
    ])

def glitch_scores(calm: dict[str, NDArray[np.float64]], glitch: list[NDArray[np.float64]]) -> dict[str, float]:
    """Score every calm clip by how much it sounds like the glitch pile (linear discriminant analysis)."""

    names = list(calm)
    c, g = np.stack([calm[n] for n in names]), np.stack(glitch)

    both = np.vstack([c, g])
    mean, std = both.mean(axis=0), both.std(axis=0) + 1e-9
    c, g = (c - mean) / std, (g - mean) / std

    # The direction that best separates glitch from calm, regularized since there are few glitch clips
    within = (np.cov(c.T) * (len(c) - 1) + np.cov(g.T) * (len(g) - 1)) / (len(c) + len(g) - 2)
    direction = np.linalg.solve(within + np.eye(len(within)), g.mean(axis=0) - c.mean(axis=0))

    return dict(zip(names, (c @ direction).tolist()))

def save_wav(path: Path, audio: NDArray[np.float32]):
    audio = audio / max(float(np.max(np.abs(audio))), 1e-6) * 0.95
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

def audio_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS)

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clips", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--glitch", type=Path, help="Folder of glitchy clips, used to flag similar ones in the calm pile")
    parser.add_argument("--model", default="small.en", help="Whisper model, medium.en is more accurate but slower")
    parser.add_argument("--name", default="ordis", help="Prefix for the output file names")
    args = parser.parse_args()

    clips = audio_files(args.clips)
    if not clips:
        parser.error(f"No audio files found in {args.clips}")

    wavs = args.output / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)

    whisper = WhisperModel(args.model, device="cpu", compute_type="int8")

    rows: list[str] = []
    review: dict[str, str] = {}
    sources: dict[str, str] = {}
    features: dict[str, NDArray[np.float64]] = {}
    skipped = 0
    total = 0.0

    for clip in clips:
        try:
            pieces = split_long(trim(decode_audio(clip, RATE)))
        except Exception as err:
            print(f"skip {clip.name}: can't decode ({e})")
            skipped += 1
            continue

        for part, audio in enumerate(pieces, 1):
            source = clip.name if len(pieces) == 1 else f"{clip.name} part {part}"
            seconds = audio.size / RATE
            if not MIN_SECONDS <= seconds <= MAX_SECONDS:
                print(f"skip {source}: {seconds:.1f}s is outside {MIN_SECONDS}-{MAX_SECONDS}s")
                skipped += 1
                continue

            segments, _ = whisper.transcribe(resample(audio, WHISPER_RATE), language="en") # pyright: ignore[reportUnknownMemberType]
            segments = list(segments)
            text = " ".join(s.text.strip() for s in segments).replace("|", " ").strip()

            if not text:
                print(f"skip {source}: no speech found")
                skipped += 1
                continue

            name = f"{args.name}_{len(rows) + 1:04d}.wav"
            save_wav(wavs / name, audio)
            rows.append(f"{name}|{text}")
            sources[name] = source
            features[name] = sound_features(audio)
            total += seconds

            if min(s.avg_logprob for s in segments) < UNSURE_LOGPROB:
                review[name] = f"unclear speech: {text}"
                print(f"CHECK {name}: {text}")
            else:
                print(f"ok    {name}: {text}")

    glitchy: list[str] = []
    if args.glitch:
        glitch = [sound_features(a) for p in audio_files(args.glitch) if (a := trim(decode_audio(p, RATE))).size >= RATE // 2]
        if len(glitch) >= 5 and len(features) >= 20:
            scores = glitch_scores(features, glitch)
            glitchy = sorted(scores, key=lambda n: -scores[n])[:GLITCH_FLAGS]
            for name in glitchy:
                review[name] = "sounds glitchy" + (f", {review[name]}" if name in review else "")
        else:
            print("Not enough clips to run the glitch check, it needs 5+ glitch and 20+ calm clips.")

    # Glitch suspects first, most glitchy at the top, then the unclear-speech ones
    order = glitchy + [n for n in review if n not in glitchy]

    (args.output / "metadata.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (args.output / "review.txt").write_text("".join(f"{n} (from {sources[n]}): {review[n]}\n" for n in order), encoding="utf-8")

    print(f"\n{len(rows)} clips, {total / 60:.1f} minutes of audio, {skipped} skipped.")
    print(f"{len(review)} to review ({len(glitchy)} might be glitchy), see review.txt")
    if total < 20 * 60:
        print("That's under 20 minutes, fine-tuning will work but more audio sounds better.")

if __name__ == "__main__":
    main()
