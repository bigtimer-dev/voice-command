-- Voice commands (Superwhisper transcription -> ~/.config/voice-commands/commands.conf)
-- Ctrl+Shift+Space, not anything with Alt+Shift: Omarchy setups with two keyboard
-- layouts often use grp:alt_shift_toggle, which eats Alt+Shift as the layout switch.
o.bind_hold("CTRL + SHIFT + SPACE", "Voice command (hold to talk)", "voice-command --start", "Voice command: run", "voice-command --stop")

-- Voice command on the mouse: hold both side buttons (Back +
-- Forward, mouse:275 + mouse:276) to talk, release either to run. The binds
-- consume the buttons, so apps no longer get Back / Forward from them.
do
  local down = { [275] = false, [276] = false }
  local active = false
  local function press(code)
    return function()
      down[code] = true
      if not active and down[275] and down[276] then
        active = true
        hl.exec_cmd("voice-command --start")
      end
    end
  end
  local function release(code)
    return function()
      down[code] = false
      if active then
        active = false
        hl.exec_cmd("voice-command --stop")
      end
    end
  end
  for _, code in ipairs({ 275, 276 }) do
    local key = "mouse:" .. code
    o.bind(key, nil, press(code), { transparent = true, ignore_mods = true })
    o.bind(key, nil, release(code), { release = true, transparent = true, ignore_mods = true })
  end
end
