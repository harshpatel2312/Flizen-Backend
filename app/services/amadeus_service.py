"""
Amadeus API Service
Handles authentication and flight data retrieval from Amadeus API
"""
import httpx
import time
from typing import Optional, Dict, List, Any
from datetime import datetime
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.flight_schema import FlightOffer, FlightSegment

logger = get_logger(__name__)


class AmadeusTokenCache:
    """
    In-memory token cache to avoid requesting new tokens on every API call
    """
    def __init__(self, ttl: int = 1800):
        self.token: Optional[str] = None
        self.expires_at: float = 0
        self.ttl = ttl
    
    def is_valid(self) -> bool:
        """Check if cached token is still valid"""
        return self.token is not None and time.time() < self.expires_at
    
    def set_token(self, token: str) -> None:
        """Store token with expiration time"""
        self.token = token
        self.expires_at = time.time() + self.ttl
    
    def get_token(self) -> Optional[str]:
        """Get token if valid, otherwise return None"""
        return self.token if self.is_valid() else None


class AmadeusService:
    """
    Service for interacting with Amadeus Flight Offers Search API
    Handles OAuth2 authentication and flight search
    """
    
    def __init__(self):
        self.api_key = settings.AMADEUS_API_KEY
        self.api_secret = settings.AMADEUS_API_SECRET
        self.base_url = settings.AMADEUS_API_BASE_URL
        self.token_cache = AmadeusTokenCache(ttl=settings.TOKEN_CACHE_TTL)
        
        # Validate credentials
        if not self.api_key or not self.api_secret:
            logger.warning("Amadeus API credentials not configured. Using mock data.")
    
    async def get_access_token(self) -> Optional[str]:
        """
        Get OAuth2 access token from Amadeus API
        Uses cached token if available and valid
        
        Returns:
            Access token string, or None if authentication fails
        """
        # Check if we have a valid cached token
        cached_token = self.token_cache.get_token()
        if cached_token:
            logger.debug("Using cached Amadeus access token")
            return cached_token
        
        # Check if credentials are configured
        if not self.api_key or not self.api_secret:
            logger.error("Amadeus API credentials not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                # Request new token
                token_url = f"{self.base_url}/v1/security/oauth2/token"
                
                response = await client.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.api_key,
                        "client_secret": self.api_secret,
                    },
                    timeout=10.0,
                )
                
                if response.status_code != 200:
                    logger.error(f"Token request failed: {response.status_code} - {response.text}")
                    return None
                
                data = response.json()
                token = data.get("access_token")
                
                if token:
                    # Cache the token
                    self.token_cache.set_token(token)
                    logger.info("Successfully retrieved new Amadeus access token")
                    return token
                else:
                    logger.error("No access token in response")
                    return None
        
        except httpx.TimeoutException:
            logger.error("Token request timeout")
            return None
        except Exception as e:
            logger.error(f"Token request error: {str(e)}")
            return None
    
    async def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
    ) -> List[FlightOffer]:
        """
        Search for flights using Amadeus Flight Offers Search API
        
        Args:
            origin: Origin airport IATA code (e.g., YYZ)
            destination: Destination airport IATA code (e.g., JFK)
            departure_date: Departure date in YYYY-MM-DD format
            adults: Number of adult passengers
            children: Number of child passengers
            infants: Number of infant passengers
        
        Returns:
            List of FlightOffer objects
        """
        # Validate inputs
        if not self._validate_inputs(origin, destination, departure_date, adults):
            return []
        
        # Get access token
        token = await self.get_access_token()
        if not token:
            logger.warning("No valid token, using mock data")
            return self._get_mock_flights(origin, destination, departure_date)
        
        try:
            async with httpx.AsyncClient() as client:
                # Prepare request parameters
                params = {
                    "originLocationCode": origin.upper(),
                    "destinationLocationCode": destination.upper(),
                    "departureDate": departure_date,
                    "adults": str(adults),
                    "max": "10",  # Return max 10 offers
                }
                
                # Add children and infants if present
                if children > 0:
                    params["children"] = str(children)
                if infants > 0:
                    params["infants"] = str(infants)
                
                # Make API request
                search_url = f"{self.base_url}/v2/shopping/flight-offers"
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Flizen/1.0"
                }
                
                response = await client.get(
                    search_url,
                    params=params,
                    headers=headers,
                    timeout=15.0,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    flights = self._parse_amadeus_response(data)
                    logger.info(f"Retrieved {len(flights)} flights from Amadeus")
                    return flights
                else:
                    logger.error(f"Flight search failed: {response.status_code} - {response.text}")
                    # Fall back to mock data
                    return self._get_mock_flights(origin, destination, departure_date)
        
        except httpx.TimeoutException:
            logger.error("Flight search request timeout")
            return self._get_mock_flights(origin, destination, departure_date)
        except Exception as e:
            logger.error(f"Flight search error: {str(e)}")
            return self._get_mock_flights(origin, destination, departure_date)
    
    def _validate_inputs(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int
    ) -> bool:
        """Validate search input parameters"""
        # Check origin and destination are IATA codes
        if not origin or len(origin) != 3 or not origin.isalpha():
            logger.warning(f"Invalid origin airport code: {origin}")
            return False
        
        if not destination or len(destination) != 3 or not destination.isalpha():
            logger.warning(f"Invalid destination airport code: {destination}")
            return False
        
        # Check they're different
        if origin.upper() == destination.upper():
            logger.warning("Origin and destination cannot be the same")
            return False
        
        # Validate date format
        try:
            departure = datetime.strptime(departure_date, "%Y-%m-%d")
            if departure < datetime.now():
                logger.warning(f"Departure date is in the past: {departure_date}")
                return False
        except ValueError:
            logger.warning(f"Invalid date format: {departure_date}")
            return False
        
        # Check adults
        if adults < 1:
            logger.warning("At least 1 adult passenger is required")
            return False
        
        return True
    
    def _parse_amadeus_response(self, data: Dict[str, Any]) -> List[FlightOffer]:
        """
        Transform Amadeus API response to FlightOffer objects
        
        Args:
            data: Raw response from Amadeus API
        
        Returns:
            List of FlightOffer objects
        """
        flights = []
        
        try:
            # Extract dictionaries
            amadeus_flights = data.get("data", [])
            dictionaries = data.get("dictionaries", {})
            
            for idx, flight in enumerate(amadeus_flights):
                try:
                    offer = self._parse_flight_offer(flight, dictionaries, idx)
                    if offer:
                        flights.append(offer)
                except Exception as e:
                    logger.warning(f"Error parsing flight offer: {str(e)}")
                    continue
        
        except Exception as e:
            logger.error(f"Error parsing Amadeus response: {str(e)}")
        
        return flights
    
    def _parse_flight_offer(
        self,
        flight: Dict[str, Any],
        dictionaries: Dict[str, Any],
        index: int
    ) -> Optional[FlightOffer]:
        """
        Parse a single flight offer from Amadeus response
        
        Args:
            flight: Single flight offer from Amadeus
            dictionaries: Reference data for airlines, aircraft, etc.
            index: Index of the flight
        
        Returns:
            FlightOffer object or None if parsing fails
        """
        try:
            # Extract itineraries (outbound flight)
            itineraries = flight.get("itineraries", [])
            if not itineraries:
                return None
            
            outbound = itineraries[0]
            segments = outbound.get("segments", [])
            
            if not segments:
                return None
            
            # Parse first segment for basic flight info
            first_segment = segments[0]
            last_segment = segments[-1]
            
            # Extract airline info
            airline_code = first_segment.get("operating", {}).get("carrierCode") or first_segment.get("carrierCode")
            airline_name = dictionaries.get("carriers", {}).get(airline_code, airline_code)
            
            # Extract flight number
            flight_number = f"{airline_code}{first_segment.get('number', '')}"
            
            # Parse times
            departure_time = self._parse_time(first_segment.get("departure", {}).get("at"))
            arrival_time = self._parse_time(last_segment.get("arrival", {}).get("at"))
            
            # Calculate duration
            duration = self._format_duration(outbound.get("duration", ""))
            
            # Get price info
            price_detail = flight.get("price", {})
            price = float(price_detail.get("total", 0))
            currency = price_detail.get("currency", "USD")
            
            # Number of stops
            stops = max(0, len(segments) - 1)
            
            # Parse segments
            parsed_segments = []
            for segment in segments:
                parsed_seg = self._parse_segment(segment, dictionaries)
                if parsed_seg:
                    parsed_segments.append(parsed_seg)
            
            # Create FlightOffer
            flight_offer = FlightOffer(
                id=f"AM{index}_{int(time.time())}",
                airline=airline_name,
                flight_number=flight_number,
                departure_time=departure_time,
                arrival_time=arrival_time,
                duration=duration,
                stops=stops,
                price=price,
                currency=currency,
                segments=parsed_segments,
            )
            
            return flight_offer
        
        except Exception as e:
            logger.warning(f"Error parsing flight offer: {str(e)}")
            return None
    
    def _parse_segment(
        self,
        segment: Dict[str, Any],
        dictionaries: Dict[str, Any]
    ) -> Optional[FlightSegment]:
        """Parse a single flight segment"""
        try:
            airline_code = segment.get("operating", {}).get("carrierCode") or segment.get("carrierCode")
            airline_name = dictionaries.get("carriers", {}).get(airline_code, airline_code)
            
            departure_time = self._parse_time(segment.get("departure", {}).get("at"))
            arrival_time = self._parse_time(segment.get("arrival", {}).get("at"))
            
            aircraft_code = segment.get("aircraft", {}).get("code")
            aircraft_name = dictionaries.get("aircraft", {}).get(aircraft_code, aircraft_code)
            
            segment_obj = FlightSegment(
                airline=airline_name,
                airline_code=airline_code,
                flight_number=f"{airline_code}{segment.get('number', '')}",
                departure_time=departure_time,
                arrival_time=arrival_time,
                departure_airport=segment.get("departure", {}).get("iataCode"),
                arrival_airport=segment.get("arrival", {}).get("iataCode"),
                duration=self._format_duration(segment.get("duration", "")),
                aircraft=aircraft_name,
            )
            
            return segment_obj
        except Exception as e:
            logger.warning(f"Error parsing segment: {str(e)}")
            return None
    
    def _parse_time(self, iso_time: Optional[str]) -> str:
        """
        Convert ISO 8601 datetime to HH:MM format
        Input: 2026-05-20T10:30:00
        Output: 10:30
        """
        if not iso_time:
            return "N/A"
        
        try:
            # Extract time portion
            time_part = iso_time.split("T")[1]
            return time_part[:5]  # Return HH:MM
        except:
            return iso_time
    
    def _format_duration(self, duration: str) -> str:
        """
        Convert ISO 8601 duration to readable format
        Input: PT1H35M
        Output: 1h 35m
        """
        if not duration:
            return "N/A"
        
        try:
            # Remove PT prefix
            duration = duration.replace("PT", "")
            
            # Parse hours and minutes
            hours = minutes = 0
            if "H" in duration:
                h_part = duration.split("H")[0]
                hours = int(h_part)
                duration = duration.split("H")[1]
            
            if "M" in duration:
                m_part = duration.split("M")[0]
                minutes = int(m_part)
            
            # Format
            if hours > 0 and minutes > 0:
                return f"{hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h"
            elif minutes > 0:
                return f"{minutes}m"
            else:
                return "N/A"
        except:
            return duration
    
    def _get_mock_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str
    ) -> List[FlightOffer]:
        """
        Generate mock flights for testing
        Used when API credentials are not configured or API fails
        """
        import random
        
        airlines = [
            {"name": "Air Canada", "code": "AC"},
            {"name": "United Airlines", "code": "UA"},
            {"name": "American Airlines", "code": "AA"},
            {"name": "Delta Airlines", "code": "DL"},
            {"name": "Southwest Airlines", "code": "WN"},
        ]
        
        mock_flights = []
        
        for i in range(5):
            airline = random.choice(airlines)
            
            flight = FlightOffer(
                id=f"MOCK_{i}_{int(time.time())}",
                airline=airline["name"],
                flight_number=f"{airline['code']}{random.randint(100, 999)}",
                departure_time=f"{8 + i*3:02d}:00",
                arrival_time=f"{14 + i*3:02d}:30",
                duration=f"{random.randint(4, 8)}h {random.randint(0, 55)}m",
                stops=random.choices([0, 1], weights=[70, 30])[0],
                price=round(random.uniform(150, 600), 2),
                currency="USD",
                segments=[],
            )
            
            mock_flights.append(flight)
        
        logger.info(f"Generated {len(mock_flights)} mock flights")
        return mock_flights
