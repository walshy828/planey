import logging
import httpx
import json as json_module
from bs4 import BeautifulSoup
from typing import List, Optional
from datetime import datetime, timezone
import dateutil.parser
from dateutil import tz
from app.config import settings

logger = logging.getLogger(__name__)

TZ_ABBREVS = {
    "EST": tz.gettz("America/New_York"),
    "EDT": tz.gettz("America/New_York"),
    "CST": tz.gettz("America/Chicago"),
    "CDT": tz.gettz("America/Chicago"),
    "MST": tz.gettz("America/Denver"),
    "MDT": tz.gettz("America/Denver"),
    "PST": tz.gettz("America/Los_Angeles"),
    "PDT": tz.gettz("America/Los_Angeles"),
    "AKST": tz.gettz("America/Anchorage"),
    "AKDT": tz.gettz("America/Anchorage"),
    "HST": tz.gettz("Pacific/Honolulu"),
}


class FlightAwareScraper:
    """Scraper for FlightAware using FlareSolverr to bypass Cloudflare."""

    def __init__(self, flaresolverr_url: Optional[str] = None):
        self.flaresolverr_url = flaresolverr_url or settings.flaresolverr_url

    async def get_page_content(self, url: str) -> Optional[str]:
        """Fetch solved HTML from FlareSolverr."""
        try:
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 60000
            }
            async with httpx.AsyncClient(timeout=70.0) as client:
                resp = await client.post(self.flaresolverr_url + "/v1", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok":
                        return data.get("solution", {}).get("response")
                    else:
                        logger.error(f"FlareSolverr error: {data.get('message')}")
                else:
                    logger.error(f"FlareSolverr request failed with status {resp.status_code}")
        except Exception as e:
            logger.error(f"Failed to connect to FlareSolverr: {e}")
        return None

    def _extract_trackpoll_json(self, html: str) -> Optional[dict]:
        """
        Extract and parse the trackpollBootstrap JSON variable embedded in the FA page.
        Uses brace counting to handle arbitrarily nested JSON without regex limits.
        """
        marker = "var trackpollBootstrap = "
        idx = html.find(marker)
        if idx < 0:
            return None
        try:
            json_start = html.index("{", idx)
            depth = 0
            for i, c in enumerate(html[json_start:], json_start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return json_module.loads(html[json_start : i + 1])
        except Exception as e:
            logger.warning(f"Failed to parse trackpollBootstrap JSON: {e}")
        return None

    def _ts_to_utc(self, ts: Optional[int]) -> Optional[datetime]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def _entry_status(self, entry: dict) -> str:
        if entry.get("cancelled"):
            return "cancelled"
        takeoff = entry.get("takeoffTimes") or {}
        landing = entry.get("landingTimes") or {}
        if takeoff.get("actual"):
            return "landed" if landing.get("actual") else "active"
        return "scheduled"

    def _parse_from_trackpoll(self, data: dict) -> List[dict]:
        """Extract normalized flight list from trackpollBootstrap data."""
        flights = []
        seen_ids: set = set()

        for flight_blob in data.get("flights", {}).values():
            for entry in (flight_blob.get("activityLog") or {}).get("flights", []):
                fa_id = entry.get("flightId")
                if fa_id in seen_ids:
                    continue
                seen_ids.add(fa_id)

                status = self._entry_status(entry)

                origin = entry.get("origin") or {}
                dest = entry.get("destination") or {}
                takeoff = entry.get("takeoffTimes") or {}
                landing = entry.get("landingTimes") or {}

                # For active flights use actual times; for upcoming use estimated → scheduled
                dep_ts = takeoff.get("actual") or takeoff.get("estimated") or takeoff.get("scheduled")
                arr_ts = landing.get("actual") or landing.get("estimated") or landing.get("scheduled")

                # FA flight IDs for GA flights look like "N512WB-1779997593-sw-2859p";
                # use the ident field if present, otherwise extract just the tail prefix.
                ident = entry.get("ident") or (fa_id.split("-")[0] if fa_id else None)
                flights.append({
                    "fa_flight_id": fa_id,
                    "flight_number": ident,
                    "status": status,
                    "origin_code": origin.get("iata") or origin.get("icao"),
                    "origin_icao": origin.get("icao"),
                    "origin_name": origin.get("friendlyName"),
                    "destination_code": dest.get("iata") or dest.get("icao"),
                    "destination_icao": dest.get("icao"),
                    "destination_name": dest.get("friendlyName"),
                    "departure_time": self._ts_to_utc(dep_ts),
                    "arrival_time": self._ts_to_utc(arr_ts),
                })
                logger.info(
                    f"FlightAware (JSON): {status} flight {fa_id} "
                    f"{origin.get('iata')}→{dest.get('iata')}"
                )

        return flights

    async def scrape_upcoming_flights(self, tail_number: str) -> List[dict]:
        """Scrape upcoming and current flights for a given tail number."""
        url = f"https://www.flightaware.com/live/flight/{tail_number}"
        html = await self.get_page_content(url)
        if not html:
            return []

        # Primary: extract from embedded trackpollBootstrap JSON (Unix timestamps → UTC)
        trackpoll = self._extract_trackpoll_json(html)
        if trackpoll:
            flights = self._parse_from_trackpoll(trackpoll)
            if flights:
                flights.sort(key=lambda x: 0 if x["status"] == "active" else 1)
                return flights
            logger.info("FlightAware: trackpollBootstrap found but contained no flights")

        # Fallback: HTML table parsing for older/different page layouts
        logger.info("FlightAware: falling back to HTML table parsing")
        return self._parse_from_html(html)

    def _parse_from_html(self, html: str) -> List[dict]:
        """Legacy HTML table parser (fallback when trackpollBootstrap is absent)."""
        soup = BeautifulSoup(html, "lxml")
        flights = []

        # Check for live flight in page summary
        summary_containers = soup.find_all(class_=lambda x: x and "SummaryStatus" in x)
        has_enroute = any(
            "En Route" in c.text or "In Air" in c.text or "Departed" in c.text
            for c in summary_containers
        )
        if not has_enroute:
            page_text = soup.get_text()
            if "En Route" in page_text or "In Air" in page_text:
                has_enroute = True
                logger.info("FlightAware: Detected 'En Route' via global text search")

        if has_enroute:
            try:
                ident_tag = soup.find(class_=lambda x: x and "SummaryIdent" in x)
                ident = ident_tag.text.strip() if ident_tag else None

                codes = [
                    span.text.strip()
                    for span in soup.find_all("span", class_=lambda x: x and "AirportCode" in x)
                ]
                names = []
                for div in soup.find_all(
                    class_=lambda x: x and ("SummaryOrigin" in x or "SummaryDestination" in x)
                ):
                    city_tag = div.find(class_=lambda x: x and "City" in x)
                    names.append(
                        city_tag.text.strip() if city_tag else div.get_text(separator=" ", strip=True)
                    )

                dep_time, arr_time = None, None
                for container in soup.find_all(
                    class_=lambda x: x and "flightPageDataTimesChild" in x
                ):
                    heading_tag = container.find(class_=lambda x: x and "ActualTimeHeading" in x)
                    text_tag = container.find(class_=lambda x: x and "ActualTimeText" in x)
                    if heading_tag and text_tag:
                        heading = heading_tag.text.strip().lower()
                        txt = text_tag.text.strip().replace("\xa0", " ")
                        try:
                            parsed = dateutil.parser.parse(txt, tzinfos=TZ_ABBREVS)
                            if parsed.year == 1900:
                                parsed = parsed.replace(year=datetime.now(tz.UTC).year)
                            if parsed.tzinfo:
                                parsed = parsed.astimezone(tz.UTC)
                                if "takeoff" in heading or "depart" in heading:
                                    dep_time = parsed
                                elif "landing" in heading or "arriv" in heading:
                                    arr_time = parsed
                        except Exception:
                            pass

                flights.append({
                    "fa_flight_id": None,
                    "flight_number": ident,
                    "status": "active",
                    "origin_code": codes[0] if len(codes) >= 2 else None,
                    "origin_icao": None,
                    "origin_name": names[0] if len(names) >= 1 else None,
                    "destination_code": codes[1] if len(codes) >= 2 else None,
                    "destination_icao": None,
                    "destination_name": names[1] if len(names) >= 2 else None,
                    "departure_time": dep_time,
                    "arrival_time": arr_time,
                })
                logger.info(f"FlightAware (HTML): detected en-route flight {ident}")
            except Exception as e:
                logger.warning(f"Failed to parse FlightAware summary: {e}")

        tables = soup.find_all("table")
        logger.info(f"FlightAware (HTML): found {len(tables)} tables on page")

        for table in tables:
            header_els = table.find_all("th") or (table.find("tr") or {}).find_all("td") if table.find("tr") else []
            headers = [el.text.strip().lower() for el in header_els]
            if not any(h in headers for h in ["ident", "flight", "origin", "destination", "departure", "arrival"]):
                continue

            header_text = " ".join(el.text for el in header_els).upper()
            table_tz = next(
                (tzobj for abbrev, tzobj in TZ_ABBREVS.items() if abbrev in header_text), None
            )

            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue
                try:
                    status = "scheduled"
                    if "En Route" in row.text or "In Air" in row.text:
                        status = "active"
                    elif "Arrived" in row.text or "Landed" in row.text:
                        status = "landed"

                    origin_raw = cols[2].text.strip() if len(cols) > 2 else None
                    dest_raw = cols[3].text.strip() if len(cols) > 3 else None

                    def _split_airport(raw):
                        if raw and "(" in raw:
                            parts = raw.split("(")
                            return parts[0].strip(), parts[-1].replace(")", "").strip()
                        return raw, None

                    origin_name, origin_code = _split_airport(origin_raw)
                    dest_name, dest_code = _split_airport(dest_raw)

                    def _parse_time(text):
                        if not text:
                            return None
                        try:
                            parsed = dateutil.parser.parse(text, tzinfos=TZ_ABBREVS)
                            if parsed.year == 1900:
                                parsed = parsed.replace(year=datetime.now().year)
                            if parsed.tzinfo is None and table_tz is not None:
                                parsed = parsed.replace(tzinfo=table_tz)
                            if parsed.tzinfo is not None:
                                return parsed.astimezone(tz.UTC)
                        except Exception:
                            pass
                        return None

                    dep_time = _parse_time(cols[4].text.strip() if len(cols) > 4 else None)
                    arr_time = _parse_time(cols[5].text.strip() if len(cols) > 5 else None)

                    entry = {
                        "fa_flight_id": None,
                        "flight_number": cols[0].text.strip(),
                        "status": status,
                        "origin_code": origin_code,
                        "origin_icao": None,
                        "origin_name": origin_name,
                        "destination_code": dest_code,
                        "destination_icao": None,
                        "destination_name": dest_name,
                        "departure_time": dep_time,
                        "arrival_time": arr_time,
                    }
                    if not any(f["flight_number"] == entry["flight_number"] for f in flights):
                        flights.append(entry)
                except Exception as e:
                    logger.warning(f"Failed to parse FlightAware HTML row: {e}")

        flights.sort(key=lambda x: 0 if x["status"] == "active" else 1)
        return flights


# Singleton
fa_scraper = FlightAwareScraper()
