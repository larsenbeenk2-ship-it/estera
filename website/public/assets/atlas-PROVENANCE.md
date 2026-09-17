# Estera cartographic globe

The land geometry is **Natural Earth, 1:110m land, v5.1.2**. Natural Earth declares its vector and raster map data public domain, with personal and commercial use and modification allowed.

- Project: https://www.naturalearthdata.com/
- License statement: https://www.naturalearthdata.com/about/terms-of-use/
- Versioned source: https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_land.geojson
- Exact downloaded file: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson
- Retrieved: 2026-09-17.
- Original source SHA-256: `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9`.

## Local assets

- `atlas-land-source.geojson`: the unmodified source, retained for editing and reproducibility. It is not requested by the running page.
- `atlas-land.json`: derived runtime asset. The `points` flat array contains 17,682 sampled longitude/latitude pairs in hundredths of a degree. Sampling uses latitude rows separated by 0.82°, with longitude spacing adjusted by the cosine of latitude and alternate rows staggered. Polygon scanline intersections retain land and exclude holes. The `coasts` arrays retain 5,143 source coastline vertices rounded to hundredths of a degree.
- `atlas-san-francisco.svg`, `atlas-tokyo.svg`, `atlas-paris.svg`: original static fallback illustrations projected from the same data, with the selected city on the visible hemisphere. These are also used for print. They are code-authored vectors, not screenshots or generated imagery.

The stippling, lighting, graticule, frame, globe projection, route geometry, markers and choreography are original code authored for this site. No satellite texture, commercial texture, rendered video, Blender output or third-party renderer is included.

## Runtime treatment and limits

`AtlasGlobe.tsx` projects actual unit-sphere positions to Canvas 2D using a shared 3D rotation matrix and orthographic projection. Native document scrolling through `#atlas-hero` rotates the land and elevated great-circle routes. Destination selection rotates the chosen city toward the viewer. Visible surface points are clipped to the near hemisphere; elevated paths are tested against the globe surface, including the visible portions outside its silhouette. The orbit and the two routes occupy distinct world positions. Route dots are decorative connection cues, not real location traffic.

Frames run only while input-driven motion settles or a redraw is requested. Rendering pauses outside the viewport and while the document is hidden. Pixel ratio is capped at 1.5. Reduced motion provides a static composed view for each selected destination. Failed land loads or unavailable/lost Canvas rendering retain the corresponding SVG.

This is a stylized small-scale atlas, not a navigation map: coastline generalization and the stippling omit small islands and inland detail. No browsers, screenshots, performance tests or visual/motion review were run by the globe builder, in accordance with the user's execution preferences. A parent compilation check, if performed, establishes compilation only.
