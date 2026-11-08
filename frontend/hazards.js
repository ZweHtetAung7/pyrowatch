// Hazard-specific presentation logic. Kept separate from map, replay and satellite controls.
export const HAZARDS = {
  wildfire: {
    label: 'Wildfire',
    mapLabel: 'Wildfire risk',
    shortLabel: 'Wildfire',
    caveat: 'Relative risk index, not a probability.',
  },
  thunderstorm: {
    label: 'Thunderstorm',
    mapLabel: 'Thunderstorm potential',
    shortLabel: 'Thunderstorm',
    caveat: 'CAPE indicates atmospheric instability, not that lightning or a storm will occur.',
  },
  blizzard: {
    label: 'Blizzard',
    mapLabel: 'Blizzard conditions',
    shortLabel: 'Blizzard',
    caveat: 'Screening index, not an official blizzard warning.',
  },
};

export function hazardFor(cell, hazard) {
  if (hazard === 'wildfire') {
    return cell?.hazards?.wildfire || {
      score: cell?.risk ?? null,
      category: cell?.category ?? null,
      factors: cell?.factors || [],
    };
  }
  return cell?.hazards?.[hazard] || { score: null, category: null, factors: [] };
}

export function hazardActions(hazard, score) {
  if (score == null) return ['Forecast measurements are unavailable for this area.', 'Check official local alerts and forecasts.'];
  if (hazard === 'thunderstorm') {
    return ['Monitor official weather alerts and radar for developing storms.',
      'If thunder is heard, move indoors and avoid exposed high ground.'];
  }
  if (hazard === 'blizzard') {
    return ['Check official winter-weather and road advisories before travel.',
      'Prepare warm layers, food, water and a charged phone if travel is necessary.'];
  }
  if (score >= 60) return ['Pre-position initial-attack crews and check evacuation routes.',
    'Restrict equipment use and open burning.'];
  if (score >= 40) return ['Increase patrols and lookout coverage during the afternoon peak.',
    'Remind residents about defensible space.'];
  return ['Normal staffing.', 'Monitor the forecast for wind or humidity changes.'];
}
