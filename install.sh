#!/bin/bash
# Install voice-command for the current user (Omarchy / Hyprland, Superwhisper).
# Safe to re-run: your own commands.conf and catalog.txt are kept.
#
#   ./install.sh                 push-to-talk only (keeps a wake word you installed before)
#   ./install.sh --wake-word     also install the always-on "hey jarvis" listener
#   ./install.sh --no-wake-word  remove the listener if it is installed
set -e
cd "$(dirname "$0")"
BIN=~/.local/bin; WW=~/.local/share/wake-word; VA=~/.local/share/voice-assistant
TTS=~/.local/share/voice-tts; CONF=~/.config/voice-commands
UNIT=~/.config/systemd/user/wake-word.service

case ${1:-} in
  --wake-word) WAKE=yes ;;
  --no-wake-word) WAKE=no ;;
  "") [[ -e $UNIT ]] && WAKE=yes || WAKE=no ;;   # re-run: keep what's there
  *) echo "usage: $0 [--wake-word | --no-wake-word]" >&2; exit 2 ;;
esac

missing=()
for c in superwhisper uv jq curl iconv pw-record pw-play paplay pactl wpctl notify-send hyprctl; do
  command -v "$c" >/dev/null || missing+=("$c")
done
if (( ${#missing[@]} )); then
  echo "Missing: ${missing[*]}" >&2
  echo "Install them first (Superwhisper, uv, jq, PipeWire tools, libnotify), then re-run." >&2
  exit 1
fi

mkdir -p "$BIN" "$VA" "$TTS/voices" "$CONF"
for f in bin/*; do
  [[ $f == bin/wake-word-enroll ]] && continue
  install -m 755 "$f" "$BIN"/
done
install -m 644 assistant/assist.py "$VA"/
for f in commands.conf catalog.txt; do [[ -e $CONF/$f ]] || install -m 644 "config/$f" "$CONF/$f"; done
[[ -e $CONF/spotify.env ]] || install -m 600 config/spotify.env.example "$CONF/spotify.env"

# offline voice for spoken answers (Piper)
[[ -x $TTS/venv/bin/piper ]] || { uv venv -q -p 3.12 "$TTS/venv"; uv pip install -q --python "$TTS/venv/bin/python" piper-tts; }
B=https://huggingface.co/rhasspy/piper-voices/resolve/main
for v in en/en_US/lessac/medium/en_US-lessac-medium es/es_MX/ald/medium/es_MX-ald-medium; do
  n=$(basename "$v")
  [[ -s $TTS/voices/$n.onnx ]] || { curl -sSfL -o "$TTS/voices/$n.onnx" "$B/$v.onnx"; curl -sSfL -o "$TTS/voices/$n.onnx.json" "$B/$v.onnx.json"; }
done

if [[ $WAKE == yes ]]; then
  # wake word: openWakeWord 0.4 bundles the pre-trained hey_jarvis model
  mkdir -p "$WW/templates" ~/.config/systemd/user
  install -m 644 wake-word/*.py "$WW"/
  install -m 755 bin/wake-word-enroll "$BIN"/
  install -m 644 systemd/wake-word.service "$UNIT"
  [[ -x $WW/venv/bin/python ]] || uv venv -q "$WW/venv"
  uv pip install -q --python "$WW/venv/bin/python" numpy "openwakeword==0.4.0" onnxruntime
  systemctl --user daemon-reload
  systemctl --user enable wake-word.service
  systemctl --user restart wake-word.service
elif [[ -e $UNIT || -d $WW ]]; then
  systemctl --user disable --now wake-word.service 2>/dev/null || true
  rm -f "$UNIT" "$BIN/wake-word-enroll"
  rm -rf "$WW"
  systemctl --user daemon-reload
  echo "Removed the wake word listener."
fi

echo
echo "Installed$([[ $WAKE == yes ]] && echo ', with the "hey jarvis" wake word'). Next steps:"
echo "  - key/mouse bindings: add hypr/voice-bindings.lua to ~/.config/hypr/bindings.lua"
[[ $WAKE == yes ]] || echo "  - wake word (optional): ./install.sh --wake-word"
echo "  - smart fallback: ollama pull qwen3:4b"
echo "  - Spotify auto-play: fill in $CONF/spotify.env"
