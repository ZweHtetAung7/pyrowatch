import { clamp, readPref, savePref } from './util.js';

// Default palette rises steadily in lightness, so order reads with any color vision and in grayscale.
const PALETTES = {
  safe: { label: 'Colorblind-safe', stops: [[0, [62, 44, 112]], [20, [110, 58, 156]], [40, [168, 50, 121]], [60, [224, 83, 59]], [75, [249, 142, 9]], [100, [252, 222, 95]]] },
  classic: { label: 'Classic', stops: [[0, [26, 152, 80]], [20, [166, 217, 106]], [30, [254, 224, 139]], [45, [253, 174, 97]], [60, [244, 109, 67]], [75, [215, 48, 39]], [100, [142, 1, 82]]] },
};
let current = readPref('pw-palette', (v) => v in PALETTES, 'safe');
let lut = [];
let css = [];

function build() {
  const { stops } = PALETTES[current];
  lut = Array.from({ length: 101 }, (_, v) => {
    for (let i = 1; i < stops.length; i++) {
      const [b, cb] = stops[i];
      if (v <= b) {
        const [a, ca] = stops[i - 1];
        const t = (v - a) / (b - a);
        return ca.map((x, k) => Math.round(x + t * (cb[k] - x)));
      }
    }
    return stops.at(-1)[1];
  });
  css = lut.map((c) => `rgb(${c.join(',')})`);
}
build();

export const rgbAt = (v) => lut[Math.round(clamp(v, 0, 100))];
export const riskColor = (v) => css[Math.round(clamp(v, 0, 100))];

const CAT_MID = { Low: 10, Moderate: 30, High: 50, 'Very high': 67, Extreme: 88 };
export const catColor = (cat) => riskColor(CAT_MID[cat] ?? 50);
export function catTextColor(cat) {
  const [r, g, b] = rgbAt(CAT_MID[cat] ?? 50).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.18 ? '#0b0f14' : '#ffffff';
}

export const palette = {
  label: () => PALETTES[current].label,
  gradient: () => `linear-gradient(90deg, ${PALETTES[current].stops.map(([v, c]) => `rgb(${c.join(',')}) ${v}%`).join(', ')})`,
  toggle() {
    current = current === 'safe' ? 'classic' : 'safe';
    savePref('pw-palette', current);
    build();
  },
};
