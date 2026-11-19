#!/bin/bash
# Install voice-command for the current user (Omarchy / Hyprland, Superwhisper).
# Safe to re-run: your own commands.conf and catalog.txt are kept.
set -e
cd "$(dirname "$0")"
BIN=~/.local/bin; WW=~/.local/share/wake-word; VA=~/.local/share/voice-assistant
TTS=~/.local/share/voice-tts; CONF=~/.config/voice-commands
mkdir -p "$BIN" "$WW/templates" "$VA" "$TTS/voices" "$CONF" ~/.config/systemd/user

install -m 755 bin/* "$BIN"/
install -m 644 wake-word/*.py "$WW"/
install -m 644 assistant/assist.py "$VA"/
for f in commands.conf catalog.txt; do [[ -e $CONF/$f ]] || install -m 644 "config/$f" "$CONF/$f"; done
[[ -e $CONF/spotify.env ]] || install -m 600 config/spotify.env.example "$CONF/spotify.env"
install -m 644 systemd/wake-word.service ~/.config/systemd/user/

# wake word: openWakeWord 0.4 bundles the pre-trained hey_jarvis model
[[ -x $WW/venv/bin/python ]] || uv venv -q "$WW/venv"
uv pip install -q --python "$WW/venv/bin/python" numpy "openwakeword==0.4.0" onnxruntime

# offline voice for spoken answers (Piper)
[[ -x $TTS/venv/bin/piper ]] || { uv venv -q -p 3.12 "$TTS/venv"; uv pip install -q --python "$TTS/venv/bin/python" piper-tts; }
B=https://huggingface.co/rhasspy/piper-voices/resolve/main
for v in en/en_US/lessac/medium/en_US-lessac-medium es/es_MX/ald/medium/es_MX-ald-medium; do
  n=$(basename "$v")
  [[ -s $TTS/voices/$n.onnx ]] || { curl -sSfL -o "$TTS/voices/$n.onnx" "$B/$v.onnx"; curl -sSfL -o "$TTS/voices/$n.onnx.json" "$B/$v.onnx.json"; }
done

systemctl --user daemon-reload
systemctl --user enable --now wake-word.service
echo
echo "Installed. Optional next steps:"
echo "  - key/mouse bindings: add hypr/voice-bindings.lua to ~/.config/hypr/bindings.lua"
echo "  - smart fallback: ollama pull qwen3:4b"
echo "  - Spotify auto-play: fill in $CONF/spotify.env"
