from app.models.flight import FlightSearchRequest, Flight, FlightSearchResponse
from typing import List
from datetime import datetime
import random

class FlightService:
    """Service for flight operations"""
    
    def __init__(self):
        pass
    
    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResponse:
        """
        Search flights based on criteria.
        Currently returns mock data.
        Can be extended to connect to Amadeus, Duffel, Skyscanner, etc.
        
        Args:
            request: Flight search request with from, to, dates, passengers, class
            
        Returns:
            Flight search response with outbound and optional return flights
        """
        # Generate mock outbound flights
        outbound_flights = self._generate_mock_flights(
            request.from_airport,
            request.to_airport,
            request.departure_date,
            request.cabin_class,
            3  # Number of mock flights
        )
        
        # Generate mock return flights if return date provided
        return_flights = None
        if request.return_date:
            return_flights = self._generate_mock_flights(
                request.to_airport,
                request.from_airport,
                request.return_date,
                request.cabin_class,
                3
            )
        
        return FlightSearchResponse(
            outbound_flights=outbound_flights,
            return_flights=return_flights,
            search_date=datetime.now().isoformat()
        )
    
    def _generate_mock_flights(
        self,
        from_airport: str,
        to_airport: str,
        date: str,
        cabin_class: str,
        count: int = 3
    ) -> List[Flight]:
        """Generate mock flight data for testing"""
        
        airlines = [
            {"name": "United Airlines", "code": "UA"},
            {"name": "American Airlines", "code": "AA"},
            {"name": "Delta Air Lines", "code": "DL"},
            {"name": "Southwest Airlines", "code": "SW"},
            {"name": "Air Canada", "code": "AC"},
        ]
        
        aircraft = [
            "Boeing 787 Dreamliner",
            "Airbus A350",
            "Boeing 777",
            "Airbus A380",
            "Boeing 737 MAX"
        ]
        
        # Base prices by cabin class
        price_base = {
            "Economy": 400,
            "Premium Economy": 800,
            "Business": 2500,
            "First": 5000
        }
        
        flights = []
        for i in range(count):
            airline = random.choice(airlines)
            stop_options = [0, 0, 0, 1, 1]  # 60% direct, 40% 1 stop
            
            flight = Flight(
                id=f"FL{from_airport}{to_airport}{i+1}",
                airline=airline["name"],
                airline_code=airline["code"],
                flight_number=f"{airline['code']}{random.randint(100, 9999)}",
                from_airport=from_airport,
                to_airport=to_airport,
                departure_time=f"{date}T{8 + i*3:02d}:00:00Z",
                arrival_time=f"{date}T{14 + i*3:02d}:30:00Z",
                duration="6h 30m",
                stops=random.choice(stop_options),
                price=price_base.get(cabin_class, price_base["Economy"]) + random.randint(-100, 400),
                cabin_class=cabin_class,
                aircraft=random.choice(aircraft)
            )
            flights.append(flight)
        
        return flights
