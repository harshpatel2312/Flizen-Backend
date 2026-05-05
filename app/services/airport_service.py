from app.core.logging import get_logger
from app.models.airport import AirportResponse
from typing import List
import json
from pathlib import Path

logger = get_logger(__name__)

class AirportService:
    """Service for airport operations — reads from the bundled JSON file into memory."""

    def __init__(self):
        self.collection = "airports"  # kept for reference; not used at runtime
        self._airports_dict: dict = {}
        self._airports_list: list = []
        self._cache_loaded = False
        self._load_airports()
    
    def _load_airports(self):
        """Load airports from comprehensive JSON file"""
        if self._cache_loaded:
            return
        
        try:
            # Try loading from airports.json (comprehensive list with all airports)
            complete_file = Path(__file__).parent.parent / "data" / "airports.json"
            
            if complete_file.exists():
                with open(complete_file, 'r', encoding='utf-8') as f:
                    airports_data = json.load(f)
                
                # Handle both dict and list formats
                if isinstance(airports_data, dict):
                    airports_data = list(airports_data.values())
                
                for airport in airports_data:
                    iata = airport.get('iata', '').upper()
                    self._airports_dict[iata] = airport
                    self._airports_list.append(airport)
                
                self._cache_loaded = True
                logger.info("Loaded %d airports from JSON cache", len(self._airports_list))
                return
            
            # Fallback to major airports
            major_file = Path(__file__).parent.parent / "data" / "major_airports.json"
            if major_file.exists():
                with open(major_file, 'r', encoding='utf-8') as f:
                    airports_data = json.load(f)
                
                for airport in airports_data:
                    iata = airport.get('iata', '').upper()
                    self._airports_dict[iata] = airport
                    self._airports_list.append(airport)
                
                self._cache_loaded = True
                logger.info("Loaded %d major airports from JSON", len(self._airports_list))
                return

            logger.warning("No airport data files found")
            self._cache_loaded = True

        except Exception as e:
            logger.exception("Error loading airports: %s", e)
            self._cache_loaded = True
    
    def search_airports(self, query: str, limit: int = 10) -> List[AirportResponse]:
        """
        Search airports from loaded cache of all 9,060+ airports.
        No Firestore quota issues - searches in-memory.
        
        Args:
            query: Search query string
            limit: Maximum number of results to return
            
        Returns:
            List of matching airports
        """
        if not query or len(query) < 1:
            return []
        
        # Ensure cache is loaded
        if not self._cache_loaded:
            self._load_airports()

        query_upper = query.upper()
        query_lower = query.lower()
        results = []
        
        try:
            # Search through all cached airports
            for airport in self._airports_list:
                iata = airport.get('iata', '').upper()
                name = airport.get('name', '').lower()
                city = airport.get('city', '').lower()
                country = airport.get('country', '').lower()
                
                # Match on: IATA code, name, city, or country
                if (query_upper == iata or
                    query_upper in iata or
                    query_lower in name or
                    query_lower in city or
                    query_lower in country):
                    
                    results.append(AirportResponse(
                        id=iata,
                        iata=airport.get('iata', ''),
                        name=airport.get('name', ''),
                        city=airport.get('city', ''),
                        country=airport.get('country', ''),
                        latitude=airport.get('latitude', 0),
                        longitude=airport.get('longitude', 0),
                        icao=airport.get('icao'),
                        timezone=airport.get('timezone')
                    ))
                
                if len(results) >= limit * 2:  # Collect extra for better sorting
                    break
            
            # Sort results: exact IATA matches first, then IATA prefix, then by name length
            results.sort(key=lambda a: (
                not (a.iata == query_upper),  # Exact matches first
                not (query_lower in a.iata.lower()),  # IATA contains next
                len(a.name)  # Then by name length
            ))
            
            return results[:limit]
        
        except Exception as e:
            logger.error("Error searching airports for query '%s': %s", query, e)
            return []
    
    def get_airport_by_iata(self, iata: str) -> AirportResponse:
        """Get airport by IATA code from cache."""
        if not self._cache_loaded:
            self._load_airports()
        
        try:
            iata_upper = iata.upper()
            if iata_upper in self._airports_dict:
                airport = self._airports_dict[iata_upper]
                return AirportResponse(
                    id=iata_upper,
                    iata=airport.get('iata', ''),
                    name=airport.get('name', ''),
                    city=airport.get('city', ''),
                    country=airport.get('country', ''),
                    latitude=airport.get('latitude', 0),
                    longitude=airport.get('longitude', 0),
                    icao=airport.get('icao'),
                    timezone=airport.get('timezone')
                )
            return None
        except Exception as e:
            logger.error("Error fetching airport '%s': %s", iata, e)
            return None
    
    def get_all_airports(self, limit: int = None) -> List[AirportResponse]:
        """Get all airports from cache."""
        if not self._cache_loaded:
            self._load_airports()
        
        try:
            results = []
            count = 0
            for airport in self._airports_list:
                if limit and count >= limit:
                    break
                
                results.append(AirportResponse(
                    id=airport.get('iata', ''),
                    iata=airport.get('iata', ''),
                    name=airport.get('name', ''),
                    city=airport.get('city', ''),
                    country=airport.get('country', ''),
                    latitude=airport.get('latitude', 0),
                    longitude=airport.get('longitude', 0),
                    icao=airport.get('icao'),
                    timezone=airport.get('timezone')
                ))
                count += 1
            
            return results
        except Exception as e:
            logger.error("Error fetching all airports: %s", e)
            return []

