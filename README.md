# voice-command

Offline voice commands for [Omarchy](https://omarchy.org) (Hyprland) using
[Superwhisper](https://superwhisper.com) for speech-to-text. Say
**"hey jarvis, open the terminal"** or hold a key and talk. Works in English and Spanish.

## What it does

- **Apps:** open agent (Claude Code), browser, terminal, files, Spotify, YouTube, settings
- **Windows and system:** close or fullscreen the window, lock the screen, workspaces
  ("go to workspace 3", "move window to workspace 2"), turn off or restart the PC
  (20 s countdown you can cancel)
- **Media and volume:** play/pause, next, previous, "volume 65", "volume up", mute
- **Commands with a value:**
  - "search YouTube for lofi", "search Google for …"
  - "remind me in 10 minutes to check the oven", "set a timer for 5 minutes"
  - "play Rammstein on Spotify" (plays it automatically)
- **Free-form sentences:** when no phrase matches, a small local model (Ollama, `qwen3:4b`)
  works out what you meant ("it's too loud" → volume down) or answers a short question out
  loud with an offline Piper voice. Time and date are answered from the clock. Shutdown,
  restart and close window are never reachable this way.
- **Matching inside longer sentences:** "I want to open the terminal" works too.

## Ways to talk to it

| Trigger | How |
|---|---|
| Wake word | say "hey jarvis", then the command (or both in one breath) |
| Keyboard | hold **Ctrl+Shift+Alt+Space**, talk, release |
| Mouse | hold both side buttons, talk, release either one |

When it's listening, playing audio is ducked to 20% and comes back afterwards. A sound
tells you whether the command worked.

## Install

Needs Omarchy (Hyprland), Superwhisper with an offline model, PipeWire, `jq`, and `uv`.
Optional: Ollama for the smart fallback, and Spotify for music.

```bash
git clone https://github.com/bigtimer-dev/voice-command && cd voice-command
./install.sh
```

Then:

- **Bindings:** copy `hypr/voice-bindings.lua` into `~/.config/hypr/bindings.lua`.
- **Smart fallback:** `ollama pull qwen3:4b`
  (any Ollama model works: set `VOICE_LLM=<model>` in your session environment)
- **Spotify auto-play:** create a free app at <https://developer.spotify.com/dashboard>
  (Web API) and put its Client ID and secret in `~/.config/voice-commands/spotify.env`.
  Without it, "play X on Spotify" opens the search page instead.

## Adding your own commands

Edit `~/.config/voice-commands/commands.conf`. Each line is `regex<TAB>shell command`.
The file is read on every command, and the first match wins.

- `$VC_NUM`: the last number you said ("volume sixty four" → 64)
- `$VC_ARG`, `$VC_ARG2`: the text captured by each `(.+)` in the pattern
- `$VC_LANG`: `en` or `es`, from Superwhisper

To let the fallback model use a command, add it to `catalog.txt` too.

Test without speaking:

```bash
voice-command --match "play despacito on youtube"   # dry run: shows what would run
voice-command --text "it's too loud"                # runs it, fallback included
```

## Files

| Path | What |
|---|---|
| `bin/voice-command` | main script: record, transcribe, match, run |
| `bin/voice-say` | speak text with Piper (en/es) |
| `bin/voice-remind` | spoken durations → `omarchy reminder` |
| `bin/voice-spotify` | Spotify search + play via MPRIS |
| `bin/voice-duck` | lower and restore playing audio |
| `bin/voice-shutdown` | cancellable shutdown/restart countdown |
| `bin/voice-llm-warm` | preload the Ollama model on wake |
| `wake-word/wake_word.py` | always-on listener (`wake-word.service`) |
| `wake-word/matcher.py`, `bin/wake-word-enroll` | optional personal wake phrase (MFCC + DTW on your own recordings, `WAKE_JOHAN=1`) |
| `assistant/assist.py` | local LLM fallback |

## Privacy

Everything runs on your computer. The only network calls are the Spotify search (when
configured) and the web/YouTube searches you ask for.

## License

MIT
