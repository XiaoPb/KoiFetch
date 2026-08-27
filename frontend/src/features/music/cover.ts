// Tiny inline SVG placeholder for covers that fail to load or are absent
// (same data-URI approach as the parser ResultCard fallback).
export const COVER_FALLBACK =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">' +
      '<rect width="100%" height="100%" fill="#f0f0f0"/>' +
      '<text x="50%" y="50%" fill="#bfbfbf" font-size="16" text-anchor="middle" dominant-baseline="middle">♪</text>' +
      '</svg>',
  );
