# Flizen Backend

A FastAPI backend for the Flizen premium flight booking platform.

## 📋 Project Structure

```
app/
  main.py                 # FastAPI application entry point
  core/
    config.py             # Configuration management
    firebase.py           # Firebase initialization and client
  models/
    airport.py           # Airport data models
    flight.py            # Flight search models
    health.py            # Health check models
  routes/
    airports.py          # Airport search endpoints
    flights.py           # Flight search endpoints
    health.py            # Health check endpoint
  services/
    airport_service.py   # Airport business logic
    flight_service.py    # Flight search logic
  data/
    airports_seed.json   # Airport seed data

seed_airports.py         # Script to populate Firestore with airports
requirements.txt         # Python dependencies
.env.example            # Environment variables template
```

## 🚀 Setup

### Prerequisites

- Python 3.9+
- pip
- Firebase project with credentials JSON

### Installation

1. **Clone and navigate to backend:**
   ```bash
   cd Flizen-Backend
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv venv
   
   # Windows
   venv\Scripts\activate
   
   # macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup environment variables:**
   ```bash
   cp .env.example .env
   ```
   
   Then edit `.env` and add:
   - `FIREBASE_CREDENTIALS_PATH`: Path to your Firebase admin key JSON file
   - `FIREBASE_PROJECT_ID`: Your Firebase project ID
   - `CORS_ORIGINS`: Frontend URLs (comma-separated)

5. **Add Firebase credentials:**
   - Place your `firebase-admin-key.json` in the `Flizen-Backend` root directory
   - Or set `FIREBASE_CREDENTIALS_PATH` environment variable to point to your credentials file

6. **Seed airport data:**
   ```bash
   python seed_airports.py
   ```
   
   This will populate Firestore with sample airports from `app/data/airports_seed.json`.

## 📡 API Endpoints

### Health Check
- **GET** `/health`
  - Returns backend status and environment

### Airport Search
- **GET** `/airports/search?q=<query>`
  - Search airports by city, name, IATA code, ICAO code, or country
  - Returns up to 10 matching airports
  - Example: `/airports/search?q=new+york`

- **GET** `/airports/{iata}`
  - Get airport details by IATA code
  - Example: `/airports/JFK`

### Flight Search
- **POST** `/flights/search`
  - Search for flights based on route, dates, passengers, and cabin class
  - Currently returns mock flight results
  
  **Request Body:**
  ```json
  {
    "from_airport": "JFK",
    "to_airport": "LHR",
    "departure_date": "2024-12-20",
    "return_date": "2024-12-27",
    "passengers": {
      "adults": 1,
      "children": 0,
      "infants": 0
    },
    "cabin_class": "Economy"
  }
  ```
  
  **Response:**
  ```json
  {
    "outbound_flights": [...],
    "return_flights": [...],
    "search_date": "2024-01-15T10:30:00",
    "currency": "USD"
  }
  ```

## 🏃 Running the Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then visit:
- API: http://localhost:8000
- Interactive Docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🗄️ Database Schema (Firestore)

### Airports Collection
```
airports/
  JFK/
    {
      "name": "John F. Kennedy International Airport",
      "city": "New York",
      "country": "United States",
      "iata": "JFK",
      "icao": "KJFK",
      "latitude": 40.6413,
      "longitude": -73.7781,
      "timezone": "America/New_York"
    }
  LHR/
    {...}
  ...
```

## 🔄 Future Enhancements

1. **Real Flight APIs**: Integrate with Amadeus, Duffel, Skyscanner, or other flight data providers
2. **User Authentication**: Add JWT-based authentication
3. **Booking Management**: Store and manage flight bookings
4. **Payment Integration**: Connect payment processors (Stripe, PayPal)
5. **Flight Analytics**: Track search trends and popular routes
6. **Email Notifications**: Send confirmations and updates

## 📝 Environment Variables

See `.env.example` for all required variables:

- `FIREBASE_CREDENTIALS_PATH`: Path to Firebase admin key (default: `firebase-admin-key.json`)
- `FIREBASE_PROJECT_ID`: Firebase project ID (default: `flizen-flight-booking`)
- `CORS_ORIGINS`: Allowed CORS origins (default: `http://localhost:5173,http://localhost:3000`)
- `ENVIRONMENT`: Development or production mode (default: `development`)

## 🛠️ Development

### Add New Endpoints

1. Create models in `app/models/`
2. Create service in `app/services/`
3. Create route in `app/routes/`
4. Include router in `app/main.py`

### Connect to Real Flight APIs

Update `FlightService.search_flights()` in `app/services/flight_service.py` to integrate with actual flight data providers.

## 📄 License

MIT
