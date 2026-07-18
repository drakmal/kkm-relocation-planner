// Rental-listing helpers. Malaysia has no free public rental-price API and
// scraping the listing portals violates their terms, so instead of fabricating
// price figures we deep-link to LIVE rental searches for each recommended area.
// The real, current price range is shown on the portal itself.

export type RentalLinks = {
  area: string;
  mudah: string;
  iproperty: string;
};

/** Mudah.my "properties for rent" search for an area (+ optional state). */
export function mudahRentalUrl(area: string, state?: string): string {
  const query = [area, state].filter(Boolean).join(' ');
  return `https://www.mudah.my/malaysia/properties-for-rent?q=${encodeURIComponent(query)}`;
}

/** iProperty rental search for an area. */
export function ipropertyRentalUrl(area: string, state?: string): string {
  const query = [area, state].filter(Boolean).join(' ');
  return `https://www.iproperty.com.my/rent/?q=${encodeURIComponent(query)}`;
}

export function rentalLinksFor(area: string, state?: string): RentalLinks {
  return {
    area,
    mudah: mudahRentalUrl(area, state),
    iproperty: ipropertyRentalUrl(area, state),
  };
}
