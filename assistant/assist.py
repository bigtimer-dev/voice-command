#!/usr/bin/env python3
"""Fallback brain for voice-command: map a free-form sentence to one of the
catalog commands, or answer a short question. Local Ollama model, offline.
Prints JSON: {"action": "command", "phrase": "..."} | {"action": "answer", "text": "...", "lang": "en|es"} | {"action": "none"}
"""
import json, os, re, subprocess, sys, time, urllib.request

MODEL = os.environ.get("VOICE_LLM", "qwen3:4b")
CATALOG = os.path.expanduser("~/.config/voice-commands/catalog.txt")


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
TIME_Q = re.compile(r"\b(what's the time|whats the time|time is it|what time it is|tell me the time|current time|"
                    r"que hora|hora es|hora tenemos|dime la hora)\b")
DATE_Q = re.compile(r"\b(what day|what's the date|whats the date|what date|today's date|todays date|date today|"
                    r"que dia|que fecha|fecha de hoy|fecha es|dia es hoy)\b")
SPANISH = re.compile(r"\b(que|hora|dia|fecha|hoy|es|dime|tenemos)\b")


def clock_answer(sentence, lang):
    """Time and date questions are answered here, not by the model (it misreads numbers)."""
    import unicodedata
    q = unicodedata.normalize("NFKD", sentence.lower()).encode("ascii", "ignore").decode()
    q = re.sub(r"[^a-z0-9' ]+", " ", q)
    t, d = TIME_Q.search(q), DATE_Q.search(q)
    if not (t or d):
        return None
    es = lang == "es" or (lang != "en" and bool(SPANISH.search(q)))
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    if es:
        if t:
            h12 = h % 12 or 12
            part = ("de la madrugada" if h < 6 else "de la mañana" if h < 12
                    else "de la tarde" if h < 20 else "de la noche")
            text = f"{'Es la' if h12 == 1 else 'Son las'} {h12}:{m:02d} {part}."
        else:
            text = f"Hoy es {DIAS[now.tm_wday]} {now.tm_mday} de {MESES[now.tm_mon - 1]} de {now.tm_year}."
    else:
        if t:
            text = time.strftime("It's %-I:%M %p.", now)
        else:
            text = time.strftime("Today is %A, %B %-d, %Y.", now)
    return {"action": "answer", "text": text, "lang": "es" if es else "en"}


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def media():
    """What the MPRIS players are doing; a playing one first."""
    names = re.findall(r"org\.mpris\.MediaPlayer2\.[\w.-]+", sh("busctl", "--user", "list", "--no-pager"))
    found = []
    for name in dict.fromkeys(names):
        prop = lambda p: sh("busctl", "--user", "-j", "get-property", name, "/org/mpris/MediaPlayer2",
                            "org.mpris.MediaPlayer2.Player", p)
        try:
            status = json.loads(prop("PlaybackStatus"))["data"]
            meta = json.loads(prop("Metadata"))["data"]
        except (ValueError, KeyError, TypeError):
            continue
        artist = meta.get("xesam:artist", {}).get("data") or [""]
        found.append({"player": name[len("org.mpris.MediaPlayer2."):].split(".instance")[0], "status": status,
                      "title": meta.get("xesam:title", {}).get("data", ""), "artist": ", ".join(artist)})
    found.sort(key=lambda m: m["status"] != "Playing")
    return found[0] if found else {}


def desktop_state():
    """Volume, media, focused window and workspace, so the model knows what
    "it", "this song" or "no sound" refer to. VC_STATE (JSON) overrides it for tests."""
    if os.environ.get("VC_STATE"):
        return json.loads(os.environ["VC_STATE"])
    st = {}
    m = re.search(r"Volume: ([\d.]+)( \[MUTED\])?", sh("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"))
    if m:
        st["volume"], st["muted"] = round(float(m.group(1)) * 100), bool(m.group(2))
    st.update(media())
    try:
        w = json.loads(sh("hyprctl", "activewindow", "-j") or "{}")
        st["window"], st["window_title"] = w.get("class", ""), (w.get("title") or "")[:80]
        st["workspace"] = json.loads(sh("hyprctl", "activeworkspace", "-j") or "{}").get("id")
    except ValueError:
        pass
    return st


def facts(st, clock=True):
    lines = [time.strftime("Now: %A %d %B %Y, %H:%M.")] if clock else []
    if "volume" in st:
        lines.append(f"Sound: volume {st['volume']}%" + (", MUTED." if st.get("muted") else ", not muted."))
    if st.get("title"):
        what = f'"{st["title"]}"' + (f' by {st["artist"]}' if st.get("artist") else "")
        verb = {"Playing": "is playing", "Paused": "is paused on"}.get(st.get("status"), "has")
        lines.append(f"Media: {st.get('player', 'a player')} {verb} {what}.")
    else:
        lines.append("Media: nothing is playing.")
    if st.get("window"):
        lines.append(f'Focused window: {st["window"]}' + (f' ("{st["window_title"]}")' if st.get("window_title") else "") + ".")
    if st.get("workspace"):
        lines.append(f"Current workspace: {st['workspace']}.")
    return "Current desktop state:\n" + "\n".join("- " + l for l in lines)


RULES = """How to decide:
- Too loud -> volume down. Too quiet, can't hear, louder -> volume up; but if the sound is MUTED, use mute (it turns sound back on).
- Dislikes the current song or wants a different one -> next song. Wants the one before -> previous song. Pause, stop, resume -> play pause music.
- Names music to hear (artist, song, genre, or a mood like "to study" or "para dormir") -> play on spotify X, X = that music in the user's words. open spotify only when no music is named.
- Wants to change wifi, bluetooth, display or sound devices -> open settings.
- Wants videos about a topic -> search youtube for X. open youtube only when no topic is named.
- Wants to be told something later, with or without the word remind ("wake me", "tell me in", "avisame", "dime en") -> remind me in N minutes to X. A countdown with no message -> set a timer for N minutes. N is in minutes, as said: "twenty minutes" -> 20, "an hour" -> 60, "media hora" -> 30.
- Questions you can answer from knowledge (facts, books, history, math, units, definitions) or from the desktop state given with the sentence (the song playing, the volume, the time) -> answer. search the web only for things that change: news, weather, prices, scores, schedules.
- Speech meant for another person (often starts with their name or "mom", "mamá"), thanks or small talk, or unclear -> none, even if it mentions music or places.
- Closing, quitting, shutting down or restarting anything is never allowed -> none."""


def main():
    sentence = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else ""
    rows = [l.strip() for l in open(CATALOG) if l.strip() and not l.startswith("#")]
    cmds = [r.split("|")[0].strip() for r in rows]
    listing = "\n".join(f"- {r.split('|')[0].strip()}  ({r.split('|')[1].strip()})" if "|" in r else f"- {r}" for r in rows)
    system = (
        "You are the voice assistant of a Linux desktop. The user said a sentence "
        "(English or Spanish, from speech recognition, may contain errors).\n"
        "Choose action:\n"
        "- command: the user wants the computer to do something one of these commands does "
        "(use the descriptions; the wording can be very different):\n" + listing + "\n"
        "  Put the command in 'command', any number in 'number' and the free text X in 'query' "
        "(keep the user's words and language for X).\n"
        "- answer: a question you can answer in words (facts, math, definitions, quick advice). "
        "Reply in the user's language in 'text', max 2 short sentences, plain text for speaking aloud.\n"
        "- none: talking to someone else, unclear, or an action that is not in the list.\n\n" + RULES
    )
    examples = [
        ("I want to listen to some music", {"action": "command", "command": "open spotify", "lang": "en"}),
        ("put on something to watch", {"action": "command", "command": "open youtube", "lang": "en"}),
        ("llévame al escritorio cuatro", {"action": "command", "command": "go to workspace N", "number": 4, "lang": "es"}),
        ("me voy, bloquea esto", {"action": "command", "command": "lock screen", "lang": "es"}),
        ("necesito acordarme de sacar la basura en una hora", {"action": "command", "command": "remind me in N minutes to X", "number": 60, "query": "sacar la basura", "lang": "es"}),
        ("what's the weather like in Madrid", {"action": "command", "command": "search the web for X", "query": "weather in Madrid", "lang": "en"}),
        ("cuánto es 6 por 7", {"action": "answer", "text": "6 por 7 es 42.", "lang": "es"}),
        ("did you call your mom yesterday", {"action": "none", "lang": "en"}),
        ("turn off the computer", {"action": "none", "lang": "en"}),
        # with a desktop state, like real requests: answer from it
        ("what am I listening to", {"action": "answer", "text": "You're listening to Yellow by Coldplay.", "lang": "en"},
         {"volume": 70, "muted": False, "player": "spotify", "status": "Playing", "title": "Yellow", "artist": "Coldplay"}),
    ]
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["command", "answer", "none"]},
            "command": {"type": "string", "enum": cmds},
            "number": {"type": "integer"},
            "query": {"type": "string"},
            "text": {"type": "string"},
            "lang": {"type": "string", "enum": ["en", "es"]},
        },
        "required": ["action", "lang"],
    }
    quick = clock_answer(sentence, lang)
    if quick:
        print(json.dumps(quick, ensure_ascii=False)); return
    # The desktop state goes with the sentence, not in the system prompt: the
    # system prompt and examples then stay identical between requests, so
    # Ollama reuses their cached prefix and only reads the new part.
    def turn(text, lang="", st=None, clock=False):
        return text + (f"  [spoken language: {lang}]" if lang in ("en", "es") else "") + \
            ("\n\n" + facts(st, clock) if st is not None else "")
    messages = [{"role": "system", "content": system}]
    for q, a, *st in examples:
        messages += [{"role": "user", "content": turn(q, a.get("lang", ""), st[0] if st else None)},
                     {"role": "assistant", "content": json.dumps(a, ensure_ascii=False)}]
    messages.append({"role": "user", "content": turn(sentence, lang, desktop_state(), clock=True)})
    body = {
        "model": MODEL, "stream": False, "format": schema, "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": messages,
    }
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            out = json.loads(json.loads(r.read())["message"]["content"])
    except Exception as e:  # Ollama down, timeout, bad JSON
        print(json.dumps({"action": "none", "error": str(e)}))
        return
    if out.get("action") == "command":
        c = out.get("command") or ""
        words = c.split()
        q = (out.get("query") or "").strip()
        if (c not in cmds or ("N" in words and not isinstance(out.get("number"), int))
                or ("X" in words and not q)):
            out = {"action": "none", "lang": out.get("lang", "en")}
        else:
            out["phrase"] = " ".join(str(out["number"]) if w == "N" else q if w == "X" else w for w in words)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
