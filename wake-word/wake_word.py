#!/usr/bin/env python3
"""Always-on wake word for voice-command: "hey jarvis" (set WAKE_JOHAN=1 to also use the personal "hey johan").

Listens to the default microphone, cuts it into speech segments, and compares
each one (MFCC + DTW, see matcher.py) with recordings of *your* "hey johan" in
templates/. On a match it chimes, records the command until you stop talking,
and hands it to `voice-command --file` (Superwhisper transcription + matching).

"hey jarvis" (openWakeWord's pre-trained model) works too, for any voice.

You can pause after "hey johan" or say it all in one breath
("hey johan open terminal"): long segments are checked on their first second.

Pause without stopping the service:  touch $XDG_RUNTIME_DIR/wake-word.paused
Add more samples of your voice:       wake-word-enroll
"""
import collections, os, subprocess, sys, time, warnings, wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matcher as M  # noqa: E402

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".local/share/wake-word")
TEMPLATES = os.path.join(BASE, "templates")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PAUSED = os.path.join(RUNTIME, "wake-word.paused")
CMD_WAV = os.path.join(RUNTIME, "voice-command", "wake.wav")
PTT_PID = os.path.join(RUNTIME, "voice-command", "rec.pid")   # push-to-talk busy
CLIPS = os.path.join(RUNTIME, "wake-word", "clips")           # tmpfs, for tuning
KEEP_CLIPS = 40
AUTO_ADD = float(os.environ.get("WAKE_AUTO_ADD", "1.50"))   # learn from matches at least this clear
MAX_AUTO = 12                                               # auto-learned samples kept (oldest dropped)

USE_JOHAN = os.environ.get("WAKE_JOHAN", "0") == "1"   # personal "hey johan" (off: hey jarvis only)
THRESHOLD = float(os.environ.get("WAKE_THRESHOLD", "1.65"))   # lower = stricter
PREFIX_THRESHOLD = float(os.environ.get("WAKE_PREFIX_THRESHOLD", "1.85"))  # one-breath
RATE = M.RATE
STEP = 0.05                                  # 50 ms chunks
CHUNK = int(RATE * STEP) * 2
CHIME = "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
CHIME_VOLUME = 1.2                           # 1.0 = file as is

# pre-trained "hey jarvis" (openWakeWord), works alongside your own "hey johan"
JARVIS_THRESHOLD = float(os.environ.get("WAKE_JARVIS_THRESHOLD", "0.6"))
JARVIS_FRAME = 1280                          # 80 ms, what the model expects

PREROLL = 0.15                               # audio kept before speech onset
SEG_END_SILENCE = 0.35                       # pause that ends a segment
MIN_SEG, MAX_SHORT_SEG = 0.35, 1.3           # whole-segment check range
PREFIX_WINDOW = 1.3                          # one-breath check on long segments
IGNORE_AFTER_WAKE = 0.3
NO_SPEECH_TIMEOUT = 4.0
END_SILENCE = 0.9
MAX_COMMAND = 10.0


def log(*a):
    print(*a, flush=True)


def notify(msg, ms=4000):
    subprocess.Popen(["notify-send", "-a", "Voice command", "-t", str(ms), "Voice command", msg],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def rms(b):
    s = np.frombuffer(b, dtype=np.int16).astype(np.float64)
    return float(np.sqrt((s * s).mean())) if len(s) else 0.0


def save_wav(path, audio_bytes):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(audio_bytes)


def save_clip(audio_bytes, label):
    try:
        os.makedirs(CLIPS, exist_ok=True)
        save_wav(os.path.join(CLIPS, time.strftime("%H%M%S_") + label + ".wav"), audio_bytes)
        for f in sorted(os.listdir(CLIPS))[:-KEEP_CLIPS]:
            os.remove(os.path.join(CLIPS, f))
    except OSError:
        pass


class Detector:
    def __init__(self):
        self.load()

    def load(self):
        self.templates = M.load_templates(TEMPLATES)
        self.mtime = self._mtime()
        log(f"{len(self.templates)} voice samples loaded, threshold {THRESHOLD}")

    def _mtime(self):
        try:
            return os.stat(TEMPLATES).st_mtime
        except OSError:
            return 0

    def maybe_reload(self):
        if self._mtime() != self.mtime:
            self.load()

    def check(self, seg_bytes, prefix=False):
        """Score a segment; with prefix, also return where the best prefix ends."""
        a = np.frombuffer(seg_bytes, dtype=np.int16).astype(np.float64)
        if not prefix:
            t = M.trim(a)
            if len(t) < RATE * MIN_SEG:
                return None, None
            return M.score(M.mfcc(t), self.templates)[0], None
        # first ~1.3 s, without per-segment normalization drift: features of
        # the whole window, open-ended DTW so the command after it is ignored
        a = a[: int(PREFIX_WINDOW * RATE)]
        nf = int(np.median([len(t) for _, t in self.templates]))
        score, end_frame = M.score_prefix(M.mfcc(a, norm_frames=nf), self.templates)
        return score, end_frame * M.HOP + M.WIN


class Jarvis:
    """openWakeWord's pre-trained hey_jarvis model, fed continuously."""
    def __init__(self):
        warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")
        import openwakeword
        from openwakeword.model import Model
        d = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
        self.model = Model(wakeword_model_paths=[os.path.join(d, "hey_jarvis_v0.1.onnx")])
        self.buf = np.zeros(0, dtype=np.int16)
        self.mute = 0

    def feed(self, b):
        """Add a chunk; return the best score of the frames it completed."""
        self.buf = np.concatenate([self.buf, np.frombuffer(b, dtype=np.int16)])
        best = 0.0
        while len(self.buf) >= JARVIS_FRAME:
            frame, self.buf = self.buf[:JARVIS_FRAME], self.buf[JARVIS_FRAME:]
            score = max(self.model.predict(frame).values())
            if self.mute > 0:       # its audio buffer still holds the last "hey jarvis"
                self.mute -= 1
            else:
                best = max(best, score)
        return best

    def reset(self):
        self.model.reset()
        self.buf = np.zeros(0, dtype=np.int16)
        self.mute = int(2.0 * RATE / JARVIS_FRAME)       # 2 s cooldown


def learn(seg_bytes, score):
    """Keep a clear match as a new voice sample, so the wake word adapts to
    how you say it (different volume, distance, mood)."""
    try:
        t = M.trim(np.frombuffer(seg_bytes, dtype=np.int16).astype(np.float64))
        if not (0.35 * RATE <= len(t) <= 1.3 * RATE):
            return
        name = time.strftime("auto-%Y%m%d-%H%M%S") + f"-{score:.2f}.wav"
        save_wav(os.path.join(TEMPLATES, name), t.astype(np.int16).tobytes())
        autos = sorted(f for f in os.listdir(TEMPLATES) if f.startswith("auto-"))
        for f in autos[:-MAX_AUTO]:
            os.remove(os.path.join(TEMPLATES, f))
        log(f"learned new voice sample {name}")
    except OSError:
        pass


def main():
    det = Detector() if USE_JOHAN else None
    try:
        jar = Jarvis()
        log(f"hey jarvis enabled, threshold {JARVIS_THRESHOLD}")
    except Exception as e:                       # keep hey johan working regardless
        jar = None
        log("hey jarvis unavailable:", e)
    if det and not det.templates:
        log("no voice samples in", TEMPLATES, "- run wake-word-enroll"); sys.exit(1)
    if not det and not jar:
        log("no wake word available"); sys.exit(1)

    mic = subprocess.Popen(
        ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16",
         "--latency", "50ms", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read():
        return mic.stdout.read(CHUNK)

    log("listening")
    noise = 50.0
    pre = collections.deque(maxlen=int(PREROLL / STEP))
    seg, silent, in_seg, long_checked = [], 0, False, False
    pending = None

    while True:
        b = read()
        if not b:
            log("microphone stream ended"); sys.exit(1)
        level = rms(b)
        busy = os.path.exists(PAUSED) or os.path.exists(PTT_PID)
        thr = max(noise * 4.0, 120.0)
        voiced = level > thr
        if not voiced:
            noise = 0.95 * noise + 0.05 * level

        jscore = jar.feed(b) if jar else 0.0
        jarvis = jscore >= JARVIS_THRESHOLD and not busy
        if jarvis:
            log(f"hey jarvis score {jscore:.2f} MATCH")
            in_seg, seg, pending, rest = False, [], None, None
            pre.clear()
        else:
            if det is None:                   # hey johan turned off
                continue
            if not in_seg:
                pre.append(b)
                if voiced and not busy:
                    in_seg, seg, silent, long_checked = True, list(pre), 0, False
                continue

            seg.append(b)
            silent = 0 if voiced else silent + 1
            dur = len(seg) * STEP
            rest, hit = None, False

            if silent * STEP >= SEG_END_SILENCE:          # segment finished
                in_seg = False
                pre.clear()
                if not long_checked and MIN_SEG <= dur <= MAX_SHORT_SEG + SEG_END_SILENCE:
                    audio = b"".join(seg)
                    score, _ = det.check(audio)
                    if score is not None:
                        hit = score < THRESHOLD
                        log(f"segment {dur:.1f}s score {score:.2f}{' MATCH' if hit else ''}")
                        save_clip(audio, f"{score:.2f}")
                        pending = (audio, score) if hit and score <= AUTO_ADD else None
                if not hit:
                    det.maybe_reload()
                    continue
            elif not long_checked and dur >= PREROLL + PREFIX_WINDOW:
                # still talking: maybe "hey johan open terminal" in one breath
                long_checked = True
                audio = b"".join(seg)
                off = int(PREROLL * RATE) * 2
                score, end = det.check(audio[off:], prefix=True)
                if score is None:
                    continue
                hit = score < PREFIX_THRESHOLD
                log(f"one-breath prefix score {score:.2f}{' MATCH' if hit else ''}")
                if not hit:
                    continue
                save_clip(audio, f"{score:.2f}-prefix")
                pending = None
                rest = audio[off + end * 2:]
                in_seg = False
            else:
                continue

        # ---- wake word heard: record the command ----
        subprocess.run(["voice-duck", "duck"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["voice-llm-warm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["paplay", "--volume", str(int(65536 * CHIME_VOLUME)), CHIME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        notify("Listening…", 6000)
        frames = [rest] if rest else []
        started = bool(rest)
        last_voice, n = 0.0, 0
        while True:
            c = read()
            if not c:
                break
            n += 1
            now = n * STEP                     # audio time, not wall time
            if not rest and not jarvis and now < IGNORE_AFTER_WAKE:
                continue
            frames.append(c)
            if rms(c) > thr:
                started, last_voice = True, now
            if not started and now > NO_SPEECH_TIMEOUT:
                break
            if started and now - last_voice > END_SILENCE:
                break
            if now > MAX_COMMAND:
                break
        pre.clear()
        if jar:
            jar.reset()
        if not started:
            pending = None
            log("no command heard")
            subprocess.Popen(["voice-duck", "restore"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            notify("Didn't hear a command", 2000)
            continue
        os.makedirs(os.path.dirname(CMD_WAV), exist_ok=True)
        save_wav(CMD_WAV, b"".join(frames))
        log(f"command recorded: {len(frames) * STEP:.1f}s")
        if pending:                       # a real command followed: it was really you
            learn(*pending)
            pending = None
            if det:
                det.maybe_reload()
        subprocess.Popen(["voice-command", "--file", CMD_WAV])


if __name__ == "__main__":
    main()
