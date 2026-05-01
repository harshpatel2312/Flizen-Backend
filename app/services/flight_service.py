from app.models.flight import FlightSearchRequest, Flight, FlightSearchResponse
from app.core.logging import get_logger
from typing import List
from datetime import datetime, timedelta
import random

logger = get_logger(__name__)

class FlightService:
    """Service for flight operations"""
    
    def __init__(self):
        # Airline data for realistic mock data generation
        self.airlines = [
            {"name": "British Airways", "code": "BA"},
            {"name": "Virgin Atlantic", "code": "VS"},
            {"name": "United Airlines", "code": "UA"},
            {"name": "American Airlines", "code": "AA"},
            {"name": "Delta Air Lines", "code": "DL"},
            {"name": "Air Canada", "code": "AC"},
            {"name": "Lufthansa", "code": "LH"},
            {"name": "Air France", "code": "AF"},
        ]
        
        self.aircraft = [
            "Boeing 787 Dreamliner",
            "Airbus A350",
            "Boeing 777",
            "Airbus A380",
            "Boeing 737 MAX",
            "Airbus A321",
        ]
        
        # Base prices by cabin class
        self.price_base = {
            "Economy": 250,
            "Premium Economy": 600,
            "Business": 2200,
            "First": 5500
        }
    
    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResponse:
        logger.info(
            "Flight search | %s → %s | %s | %s cabin | pax=%s",
            request.from_airport,
            request.to_airport,
            request.departure_date,
            request.cabin_class,
            request.passengers,
        )
        # Generate mock outbound flights
        outbound_flights = self._generate_mock_flights(
            request.from_airport,
            request.to_airport,
            request.departure_date,
            request.cabin_class,
            count=5  # Number of mock flights
        )
        
        # Generate mock return flights if return date provided
        return_flights = None
        if request.return_date:
            return_flights = self._generate_mock_flights(
                request.to_airport,
                request.from_airport,
                request.return_date,
                request.cabin_class,
                count=5
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
        count: int = 5
    ) -> List[Flight]:
        """Generate realistic mock flight data for testing"""
        
        flights = []
        departure_hours = [6, 8, 10, 14, 16, 18, 20]  # Common departure times
        duration_hours = 7  # Typical transatlantic flight
        
        for i in range(count):
            airline = random.choice(self.airlines)
            stops = random.choices([0, 1], weights=[70, 30])[0]  # 70% direct, 30% 1 stop
            departure_hour = random.choice(departure_hours)
            
            # Add variation to prices
            base_price = self.price_base.get(cabin_class, self.price_base["Economy"])
            price_variation = random.randint(-150, 400)
            price = base_price + price_variation
            
            # Format times
            departure_time = f"{date}T{departure_hour:02d}:00:00"
            arrival_time_obj = datetime.fromisoformat(date) + timedelta(hours=duration_hours)
            arrival_time = f"{arrival_time_obj.strftime('%Y-%m-%d')}T{(departure_hour + duration_hours) % 24:02d}:30:00"
            
            # Format duration
            if stops == 0:
                duration_minutes = random.randint(390, 450)  # 6h 30m to 7h 30m
            else:
                duration_minutes = random.randint(510, 600)  # 8h 30m to 10h (with stop)
            
            hours = duration_minutes // 60
            minutes = duration_minutes % 60
            duration_str = f"{hours}h {minutes}m"
            
            flight = Flight(
                id=f"FL{from_airport.upper()}{to_airport.upper()}{i+1:03d}",
                airline=airline["name"],
                airline_code=airline["code"],
                flight_number=f"{airline['code']}{random.randint(1000, 9999)}",
                from_airport=from_airport.upper(),
                to_airport=to_airport.upper(),
                departure_time=departure_time,
                arrival_time=arrival_time,
                duration=duration_str,
                stops=stops,
                price=max(100, int(price)),  # Ensure positive price
                cabin_class=cabin_class,
                aircraft=random.choice(self.aircraft)
            )
            flights.append(flight)
        
        return flights
