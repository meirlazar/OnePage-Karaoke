#!/usr/bin/env bash
# ==============================================================================
# Dynamic Theme Generator for Web Applications
# Generates JSON theme templates with 2-part randomized names & cohesive HSL palettes.
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# 1. WORD SETS
# ------------------------------------------------------------------------------

NOUNS=(
"Aether" "Afterglow" "Alpine" "Andromeda" "Arcade" "Archipelago" "Aurora" "Avalanche"
"Bamboo" "Borealis" "Canyon" "Cascade" "Celestial" "Cinder" "Cirrus" "Comet"
"Constellation" "Coral" "Cosmos" "Crater" "Crescent" "Crystal" "Dawn" "Delta"
"Desert" "Dusk" "Eclipse" "Ember" "Equinox" "Estuary" "Evergreen" "Exoplanet"
"Firefly" "Fjord" "Flare" "Forest" "Frost" "Galaxy" "Garden" "Glacier" "Snow" "Rain"
"Halo" "Harbor" "Horizon" "Icefall" "Ion" "Island" "Jade" "Juniper"
"Lagoon" "Lantern" "Lattice" "Meadow" "Meteor" "Mirage" "Monsoon" "Moonlight"
"Mountain" "Nebula" "Neon" "Nova" "Oasis" "Ocean" "River" "Hurricane" "Tornado" "Orbit" "Orchid"
"Pine" "Prism" "Pulsar" "Quartz" "Quasar" "Rainforest" "Reef" "Ridge" "Grunge" "Prairie" "Sandy" "Mud" "Earth"
"River" "Satellite" "Savanna" "Skyline" "Solstice" "Spectrum" "Starlight" "Summit" "DarkMatter" "SuperNova" "Apocalytic"
"Sunrise" "Sundown" "Supernova" "Tempest" "Thunder" "Tide" "Timber" "Tundra"
"Twilight" "Valley" "Vortex" "Waterfall" "Wildflower" "Wildfire" "Zephyr" "Zenith"
)

VERBS_COLORS=(
"Amber" "Amethyst" "Arc" "Azure" "Beam" "Blaze" "Bloom" "Blush" "Swirl" "Implosion" "Compression"
"Breeze" "Burst" "Cascade" "Cerulean" "Chime" "Cobalt" "Copper" "Coral" "Bang"
"Crest" "Crimson" "Cyan" "Dance" "Drift" "Echo" "Emerald" "Fade" "Twinkle" "Pulsing"
"Flash" "Flow" "Flux" "Gleam" "Glimmer" "Glow" "Gold" "Haze" "Falling" "Rising" "Detonation"
"Indigo" "Ignite" "Iridescence" "Jade" "Lilt" "Lumen" "Mist" "Mosaic"
"Onyx" "Opal" "Orbit" "Pearl" "Pulse" "Quartz" "Radiance" "Ripple" "Shimmer" "Death"
"Rose" "Rustle" "Saffron" "Scarlet" "Shade" "Shimmer" "Shine" "Silver"
"Soar" "Spark" "Spectrum" "Spiral" "Surge" "Teal" "Thaw" "Twinkle"
"Violet" "Wave" "Whisper" "Zephyr"
)

# ------------------------------------------------------------------------------
# 2. HSL TO HEX COLOR CONVERTER (AWK ENGINE)
# ------------------------------------------------------------------------------
# 2. HSL TO HEX ENGINE
hsl_to_hex() {
  awk -v h="$1" -v s="$2" -v l="$3" '
  function abs(v) { return v < 0 ? -v : v }
  BEGIN {
    h = h % 360; if (h < 0) h += 360;
    s = s / 100; l = l / 100;
    c = (1 - abs(2 * l - 1)) * s; hp = h / 60; x = c * (1 - abs((hp % 2) - 1));
    if (hp >= 0 && hp < 1) { r = c; g = x; b = 0; } else if (hp >= 1 && hp < 2) { r = x; g = c; b = 0; }
    else if (hp >= 2 && hp < 3) { r = 0; g = c; b = x; } else if (hp >= 3 && hp < 4) { r = 0; g = x; b = c; }
    else if (hp >= 4 && hp < 5) { r = x; g = 0; b = c; } else { r = c; g = 0; b = x; }
    m = l - c / 2;
    printf "#%02x%02x%02x\n", int((r + m) * 255 + 0.5), int((g + m) * 255 + 0.5), int((b + m) * 255 + 0.5);
  }'
}

contrast_text() {
  local hex="${1#\#}"
  local red=$((16#${hex:0:2}))
  local green=$((16#${hex:2:2}))
  local blue=$((16#${hex:4:2}))
  awk -v r="$red" -v g="$green" -v b="$blue" '
    function channel(v) {
      v /= 255
      return v <= 0.04045 ? v / 12.92 : exp(2.4 * log((v + 0.055) / 1.055))
    }
    BEGIN {
      luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
      dark_contrast = (luminance + 0.05) / 0.053
      light_contrast = 1.025 / (luminance + 0.05)
      print dark_contrast >= light_contrast ? "#000000" : "#ffffff"
    }
  '
}

contrast_ratio() {
  local first="${1#\#}" second="${2#\#}"
  local r1=$((16#${first:0:2})) g1=$((16#${first:2:2})) b1=$((16#${first:4:2}))
  local r2=$((16#${second:0:2})) g2=$((16#${second:2:2})) b2=$((16#${second:4:2}))
  awk -v r1="$r1" -v g1="$g1" -v b1="$b1" -v r2="$r2" -v g2="$g2" -v b2="$b2" '
    function channel(v) { v /= 255; return v <= 0.04045 ? v / 12.92 : exp(2.4 * log((v + 0.055) / 1.055)) }
    function luminance(r, g, b) { return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b) }
    BEGIN { a = luminance(r1, g1, b1); b = luminance(r2, g2, b2); printf "%.6f\n", (a > b ? a + 0.05 : b + 0.05) / (a > b ? b + 0.05 : a + 0.05) }
  '
}

ensure_contrast() {
  local foreground="$1" background="$2" minimum="$3"
  local ratio
  ratio=$(contrast_ratio "$foreground" "$background")
  if awk -v ratio="$ratio" -v minimum="$minimum" 'BEGIN { exit !(ratio >= minimum) }'; then
    printf '%s\n' "$foreground"
  else
    contrast_text "$background"
  fi
}

# 3. GENERATION LOGIC
generate_theme() {
  local noun="${NOUNS[$((RANDOM % ${#NOUNS[@]}))]}"
  local verb="${VERBS_COLORS[$((RANDOM % ${#VERBS_COLORS[@]}))]}"
  local theme_id="${noun}-${verb}"
  local theme_name="$(tr '[:lower:]' '[:upper:]' <<< "${noun:0:1}")${noun:1} $(tr '[:lower:]' '[:upper:]' <<< "${verb:0:1}")${verb:1}"

  local mode=$((RANDOM % 4))
  local scheme=$((RANDOM % 6))
  local base_h=$((RANDOM % 360))
  local surface_h panel_h control_h accent_h secondary_h scheme_name

  case "$scheme" in
    0) scheme_name="Analogous"; surface_h=$(((base_h + 24) % 360)); panel_h=$(((base_h + 336) % 360)); control_h=$(((base_h + 45) % 360)); accent_h=$(((base_h + 145) % 360)); secondary_h=$(((base_h + 205) % 360)) ;;
    1) scheme_name="Complementary"; surface_h=$(((base_h + 12) % 360)); panel_h=$(((base_h + 180) % 360)); control_h=$(((base_h + 192) % 360)); accent_h=$(((base_h + 180) % 360)); secondary_h=$(((base_h + 30) % 360)) ;;
    2) scheme_name="Split Complementary"; surface_h=$(((base_h + 20) % 360)); panel_h=$(((base_h + 150) % 360)); control_h=$(((base_h + 210) % 360)); accent_h=$(((base_h + 150) % 360)); secondary_h=$(((base_h + 210) % 360)) ;;
    3) scheme_name="Triadic"; surface_h=$(((base_h + 120) % 360)); panel_h=$(((base_h + 240) % 360)); control_h=$(((base_h + 120) % 360)); accent_h=$(((base_h + 240) % 360)); secondary_h=$(((base_h + 120) % 360)) ;;
    4) scheme_name="Tetradic"; surface_h=$(((base_h + 90) % 360)); panel_h=$(((base_h + 180) % 360)); control_h=$(((base_h + 270) % 360)); accent_h=$(((base_h + 90) % 360)); secondary_h=$(((base_h + 270) % 360)) ;;
    *) scheme_name="Near Neutral"; surface_h=$(((base_h + 10) % 360)); panel_h=$(((base_h + 350) % 360)); control_h=$(((base_h + 25) % 360)); accent_h=$(((base_h + 170) % 360)); secondary_h=$(((base_h + 215) % 360)) ;;
  esac

  local bg_l bg_s text_l text_m_l accent_s structural_s canvas_l border_l
  local surface_l panel_l control_l overlay_l accent_l waveform_l waveform_divider_l waveform_playhead_l

  if [[ $mode -eq 0 || $mode -eq 1 ]]; then
    bg_l=$((6 + RANDOM % 5)); surface_l=$((14 + RANDOM % 5)); panel_l=$((11 + RANDOM % 5))
    control_l=$((18 + RANDOM % 6)); overlay_l=$((4 + RANDOM % 4)); canvas_l=$((2 + RANDOM % 4))
    text_l=91; text_m_l=66; border_l=30; accent_l=64
    waveform_l=$((72 + RANDOM % 12)); waveform_divider_l=76; waveform_playhead_l=68
    [[ $mode -eq 0 ]] && { bg_s=10; structural_s=14; accent_s=58; } || { bg_s=22; structural_s=28; accent_s=84; }
  else
    bg_l=$((95 - RANDOM % 3)); surface_l=$((88 - RANDOM % 4)); panel_l=$((91 - RANDOM % 4))
    control_l=$((84 - RANDOM % 4)); overlay_l=$((97 - RANDOM % 2)); canvas_l=$((98 - RANDOM % 2))
    text_l=12; text_m_l=38; border_l=70; accent_l=42
    waveform_l=$((20 + RANDOM % 12)); waveform_divider_l=24; waveform_playhead_l=30
    [[ $mode -eq 2 ]] && { bg_s=9; structural_s=15; accent_s=58; } || { bg_s=18; structural_s=25; accent_s=82; }
  fi

  local bg_base=$(hsl_to_hex "$base_h" "$bg_s" "$bg_l")
  local bg_surface=$(hsl_to_hex "$surface_h" "$structural_s" "$surface_l")
  local bg_panel=$(hsl_to_hex "$panel_h" "$structural_s" "$panel_l")
  local control_bg=$(hsl_to_hex "$control_h" "$structural_s" "$control_l")
  local overlay_bg=$(hsl_to_hex "$secondary_h" "$((structural_s / 2))" "$overlay_l")
  local canvas_bg=$(hsl_to_hex "$panel_h" "$((structural_s / 2))" "$canvas_l")
  local border=$(hsl_to_hex "$accent_h" "$((accent_s / 2))" "$border_l")

  local text_main=$(hsl_to_hex "$base_h" "$bg_s" "$text_l")
  local text_muted=$(hsl_to_hex "$base_h" "$bg_s" "$text_m_l")
  local paper=$(hsl_to_hex "$surface_h" 8 97)
  local ink=$(hsl_to_hex "$panel_h" 12 7)

  local accent=$(hsl_to_hex "$accent_h" "$accent_s" "$accent_l")
  local accent_hover=$(hsl_to_hex "$secondary_h" "$accent_s" $((mode < 2 ? 72 : 34)))
  local button_fg=$(contrast_text "$accent")
  local waveform_accent=$(ensure_contrast "$(hsl_to_hex "$accent_h" 92 "$waveform_l")" "$control_bg" 3)
  local waveform_divider=$(ensure_contrast "$(hsl_to_hex "$control_h" 12 "$waveform_divider_l")" "$control_bg" 2)
  local waveform_playhead=$(ensure_contrast "$(hsl_to_hex 350 88 "$waveform_playhead_l")" "$control_bg" 3)

  # Status
  local success=$(hsl_to_hex 130 "$accent_s" $((mode < 2 ? 60 : 40)))
  local warning=$(hsl_to_hex 40 "$accent_s" $((mode < 2 ? 60 : 45)))
  local danger=$(hsl_to_hex 350 "$accent_s" $((mode < 2 ? 65 : 45)))

  local radii=("0px" "4px" "8px" "12px" "16px")
  local radius="${radii[$((RANDOM % ${#radii[@]}))]}"

  cat <<EOF
{
  "id": "${theme_id}",
  "name": "${theme_name}",
  "description": "Generated ${scheme_name} palette (${mode}).",
  "source_name": "Auto Theme Engine",
  "source_url": "https://localhost",
  "license": "MIT",
  "vars": {
    "bg-base": "${bg_base}",
    "bg-surface": "${bg_surface}",
    "bg-panel": "${bg_panel}",
    "border": "${border}",
    "text-main": "${text_main}",
    "text-muted": "${text_muted}",
    "accent": "${accent}",
    "accent-hover": "${accent_hover}",
    "success": "${success}",
    "warning": "${warning}",
    "danger": "${danger}",
    "label-color": "${accent}",
    "control-bg": "${control_bg}",
    "control-fg": "${text_main}",
    "control-border": "${border}",
    "button-bg": "${accent}",
    "button-fg": "${button_fg}",
    "button-hover-bg": "${accent_hover}",
    "button-small-fg": "${text_main}",
    "paper": "${paper}",
    "ink": "${ink}",
    "canvas-bg": "${canvas_bg}",
    "playhead": "${danger}",
    "waveform-accent": "${waveform_accent}",
    "waveform-divider": "${waveform_divider}",
    "waveform-playhead": "${waveform_playhead}",
    "shadow": "${ink}",
    "overlay-bg": "${overlay_bg}",
    "divider": "${border}",
    "active-glow": "${accent}",
    "radius": "${radius}"
  }
}
EOF
}

main() {
  local dir="${1:-./themes}"
  local count="${2:-1}"
  [[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "Count must be a positive integer." >&2; exit 2; }
  mkdir -p "$dir"

  local generated=0 attempts=0 json id path
  while ((generated < count)); do
    json=$(generate_theme)
    id=$(sed -n 's/^[[:space:]]*"id": "\([^"]*\)",$/\1/p' <<< "$json")
    path="${dir}/${id}.json"
    attempts=$((attempts + 1))
    if [[ -e "$path" ]]; then
      ((attempts < count * 100)) || { echo "Could not find a unique theme name." >&2; exit 1; }
      continue
    fi
    printf '%s\n' "$json" > "$path"
    echo "Saved ${path}"
    generated=$((generated + 1))
  done
}

main "$@"
