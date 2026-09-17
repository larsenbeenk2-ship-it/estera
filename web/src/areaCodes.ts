import { geographicAreaCodes, nanpaFileDate } from "./data/nanpaGeographic";

/** NANPA snapshot; refresh with data/update-area-codes.py. No network requests. */
export const areaCodeSource = {
  title: "NANPA geographic area codes",
  url: "https://www.nanpa.com/reports/npa-reports",
  dataUrl: "https://reports.nanpa.com/public/npa_report.csv",
  date: nanpaFileDate,
};

const regions = new Map<string, { query: string; region: string }>();
for (const [codes, region, query] of geographicAreaCodes) {
  for (const code of codes.split(" ")) regions.set(code, { query, region });
}

/**
 * Resolve explicit North American telephone area-code searches to a region.
 * Bare three-digit input remains a postal/place search. The source does not
 * provide precise area-code boundaries; labels deliberately disclose scope.
 * Toll-free, other nongeographic, unassigned, and future codes return null.
 */
export function areaCodeQuery(
  input: string,
): { query: string; label: string } | null {
  const text = input.trim();
  const match =
    text.match(
      /^(?:telephone\s+|phone\s+)?area[\s-]*code\s*[:#-]?\s*\(?([2-9]\d{2})\)?$/i,
    ) ?? text.match(/^\(?([2-9]\d{2})\)?\s+area[\s-]*code$/i);
  if (!match) return null;
  const code = match[1];
  const found = regions.get(code);
  if (!found) return null;

  // CPUC confirms 415/628 cover San Francisco, most of Marin, and a small
  // part of San Mateo County. San Francisco is a representative place only.
  // Source: https://www.cpuc.ca.gov/415areacode/ (accessed 2026-09-13).
  if (code === "415" || code === "628")
    return {
      query: "San Francisco, California, United States",
      label: `+1 area code ${code} · San Francisco / Marin / northern San Mateo area · representative place`,
    };

  return {
    query: found.query,
    label: `+1 area code ${code} · ${found.region} · region-level lookup`,
  };
}
