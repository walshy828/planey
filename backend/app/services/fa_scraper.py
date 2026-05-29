import logging
import httpx
from bs4 import BeautifulSoup
from typing import List, Optional
from datetime import datetime
import dateutil.parser
from dateutil import tz
from app.config import settings

logger = logging.getLogger(__name__)

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

    async def scrape_upcoming_flights(self, tail_number: str) -> List[dict]:
        """Scrape upcoming and current flights for a given tail number."""
        url = f"https://www.flightaware.com/live/flight/{tail_number}"
        html = await self.get_page_content(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        flights = []

        # 1. Look for 'Live' or 'En Route' flight in the summary header first
        # We try several possible class names or just look for the text
        summary_containers = soup.find_all(class_=lambda x: x and 'SummaryStatus' in x)
        has_enroute = any("En Route" in c.text or "In Air" in c.text or "Departed" in c.text for c in summary_containers)
        
        if not has_enroute:
            # Global check for "En Route" text if containers missed it
            page_text = soup.get_text()
            if "En Route" in page_text or "In Air" in page_text:
                has_enroute = True
                logger.info("FlightAware: Detected 'En Route' status via global text search")

        if has_enroute:
            try:
                # Identification
                ident = None
                ident_tag = soup.find(class_=lambda x: x and 'SummaryIdent' in x)
                if ident_tag: ident = ident_tag.text.strip()
                else: ident = tail_number
                
                origin_code, origin_name = None, None
                dest_code, dest_name = None, None
                dep_time, arr_time = None, None
                
                # Look for airport codes and names in typical summary locations
                codes = []
                for span in soup.find_all("span", class_=lambda x: x and 'AirportCode' in x):
                    codes.append(span.text.strip())
                
                names = []
                for div in soup.find_all(class_=lambda x: x and ('SummaryOrigin' in x or 'SummaryDestination' in x)):
                    city_tag = div.find(class_=lambda x: x and 'City' in x)
                    if city_tag:
                        names.append(city_tag.text.strip())
                    else:
                        txt = div.get_text(separator=' ', strip=True)
                        if codes:
                            for c in codes:
                                txt = txt.replace(c, "").replace("(", "").replace(")", "").strip()
                        names.append(txt)

                # Look for times in summary
                # Often in classes like 'flightPageDataTimesChild'
                time_containers = soup.find_all(class_=lambda x: x and 'flightPageDataTimesChild' in x)
                for container in time_containers:
                    heading_tag = container.find(class_=lambda x: x and 'ActualTimeHeading' in x)
                    text_tag = container.find(class_=lambda x: x and 'ActualTimeText' in x)
                    if heading_tag and text_tag:
                        heading = heading_tag.text.strip().lower()
                        txt = text_tag.text.strip()
                        # Clean timezone or extra text like "EDT" if needed, but dateutil usually handles it
                        # Try to parse the time string
                        try:
                            # E.g. "11:28AM EDT"
                            # We might need to split off the timezone if dateutil complains, but let's try direct first
                            # Remove non-breaking spaces
                            txt = txt.replace('\xa0', ' ')
                            
                            # Map standard US timezones to dateutil offsets
                            tz_mapping = {
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
                                "HDT": tz.gettz("Pacific/Honolulu"),
                            }
                            
                            parsed = dateutil.parser.parse(txt, tzinfos=tz_mapping)
                            if parsed.year == 1900: parsed = parsed.replace(year=datetime.now(tz.UTC).year)
                            
                            # Ensure it's converted to UTC for database storage
                            if parsed.tzinfo:
                                parsed = parsed.astimezone(tz.UTC)

                            if 'takeoff' in heading or 'depart' in heading:
                                dep_time = parsed
                            elif 'landing' in heading or 'arriv' in heading:
                                arr_time = parsed
                        except Exception as e:
                            logger.warning(f"Failed to parse time '{txt}': {e}")
                            pass

                if len(codes) >= 2:
                    origin_code, dest_code = codes[0], codes[1]
                if len(names) >= 2:
                    origin_name, dest_name = names[0], names[1]
                else:
                    origin_name, dest_name = origin_code, dest_code
                
                live_flight = {
                    "flight_number": ident,
                    "status": "active",
                    "origin_code": origin_code,
                    "origin_name": origin_name,
                    "destination_code": dest_code,
                    "destination_name": dest_name,
                    "departure_time": dep_time,
                    "arrival_time": arr_time,
                }
                flights.append(live_flight)
                logger.info(f"Detected en-route flight {ident} from {origin_code} ({origin_name}) to {dest_code} ({dest_name})")
            except Exception as e:
                logger.warning(f"Failed to parse FlightAware summary: {e}")

        # 2. Look for tables (Upcoming, Recent, etc.)
        tables = soup.find_all("table")
        logger.info(f"FlightAware: Found {len(tables)} tables on page")
        
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

        for table in tables:
            header_els = table.find_all("th")
            headers = [th.text.strip().lower() for th in header_els]
            logger.info(f"FlightAware: Table headers found: {headers}")
            if not any(h in headers for h in ["ident", "flight", "origin", "destination", "departure", "arrival"]):
                continue

            # Try to extract a timezone from the header row text (e.g. "Departure (EDT)")
            header_text = " ".join(th.text for th in header_els).upper()
            table_tz = None
            for abbrev, tzobj in TZ_ABBREVS.items():
                if abbrev in header_text:
                    table_tz = tzobj
                    logger.info(f"FlightAware: Detected table timezone {abbrev}")
                    break

            logger.info(f"FlightAware: Processing flight table with headers: {headers}")
            rows = table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                try:
                    row_text = row.get_text(strip=True)
                    logger.debug(f"FlightAware: Parsing row: {row_text[:100]}...")
                    status_text = cols[0].text.strip() # Usually contains status like 'En Route' or 'Scheduled'

                    flight_data = {
                        "flight_number": cols[0].text.strip(),
                        "aircraft_type": cols[1].text.strip() if len(cols) > 1 else None,
                        "origin_raw": cols[2].text.strip() if len(cols) > 2 else None,
                        "destination_raw": cols[3].text.strip() if len(cols) > 3 else None,
                        "departure_time": cols[4].text.strip() if len(cols) > 4 else None,
                        "arrival_time": cols[5].text.strip() if len(cols) > 5 else None,
                        "status": "scheduled"
                    }

                    # Check if this row is actually 'En Route' or 'Arrived'
                    if "En Route" in row.text or "In Air" in row.text:
                        flight_data["status"] = "active"
                    elif "Arrived" in row.text or "Landed" in row.text:
                        flight_data["status"] = "landed"

                    # Clean up origin/destination
                    for key in ["origin", "destination"]:
                        raw_key = f"{key}_raw"
                        if flight_data[raw_key] and "(" in flight_data[raw_key]:
                            parts = flight_data[raw_key].split("(")
                            flight_data[f"{key}_name"] = parts[0].strip()
                            flight_data[f"{key}_code"] = parts[-1].replace(")", "").strip()
                        else:
                            flight_data[f"{key}_name"] = flight_data[raw_key]
                            flight_data[f"{key}_code"] = None

                    # Parse times — FA tables show local airport time; preserve tz if found
                    for key in ["departure_time", "arrival_time"]:
                        if flight_data[key]:
                            try:
                                # FlightAware table format: 'Thu 02:56PM' (often no tz suffix)
                                parsed = dateutil.parser.parse(
                                    flight_data[key], tzinfos=TZ_ABBREVS
                                )
                                if parsed.year == 1900:
                                    parsed = parsed.replace(year=datetime.now().year)
                                # If still naive and we detected a table-level tz, apply it
                                if parsed.tzinfo is None and table_tz is not None:
                                    parsed = parsed.replace(tzinfo=table_tz)
                                if parsed.tzinfo is not None:
                                    parsed = parsed.astimezone(tz.UTC)
                                flight_data[key] = parsed
                            except:
                                pass
                    
                    # Avoid duplicates if we already found it in the summary
                    if not any(f["flight_number"] == flight_data["flight_number"] for f in flights):
                        flights.append(flight_data)
                        logger.info(f"FlightAware: Found {flight_data['status']} flight {flight_data['flight_number']} from {flight_data.get('origin_code')} to {flight_data.get('destination_code')}")
                except Exception as e:
                    logger.warning(f"Failed to parse FlightAware row: {e}")

        # Final prioritization: Active flights first
        flights.sort(key=lambda x: 0 if x["status"] == "active" else 1)
        return flights

# Singleton
fa_scraper = FlightAwareScraper()
