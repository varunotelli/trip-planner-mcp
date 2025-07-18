#!/usr/bin/env python3
"""
Trip Planning MCP Server using FastMCP
Helps plan trips by fetching real-time flight and hotel prices
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import httpx
from mcp.server.fastmcp import FastMCP
import os
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trip-planner")

# Initialize FastMCP
mcp = FastMCP("Trip Planner")



class TripPlannerService:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        
        # API Keys - set these as environment variables
        self.amadeus_api_key = os.getenv("AMADEUS_API_KEY")
        self.amadeus_api_secret = os.getenv("AMADEUS_API_SECRET")
        self.amadeus_token = None

    async def get_amadeus_token(self) -> str:
        """Get Amadeus API access token"""
        if not self.amadeus_api_key or not self.amadeus_api_secret:
            raise ValueError("Amadeus API credentials not configured")
        
        if self.amadeus_token:
            return self.amadeus_token
        
        try:
            response = await self.client.post(
                "https://test.api.amadeus.com/v1/security/oauth2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.amadeus_api_key,
                    "client_secret": self.amadeus_api_secret
                }
            )
            response.raise_for_status()
            token_data = response.json()
            self.amadeus_token = token_data["access_token"]
            return self.amadeus_token
        except Exception as e:
            logger.error(f"Error getting Amadeus token: {e}")
            raise

    async def get_city_code(self, city_name: str) -> str:
        """Get IATA city code for hotel search"""
        try:
            token = await self.get_amadeus_token()
            
            response = await self.client.get(
                "https://test.api.amadeus.com/v1/reference-data/locations/cities",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "keyword": city_name,
                    "max": 1
                }
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("data"):
                return data["data"][0]["iataCode"]
            else:
                # Fallback to first 3 letters of city name
                return city_name[:3].upper()
                
        except Exception as e:
            logger.warning(f"City code lookup failed: {e}")
            return city_name[:3].upper()

# Initialize service
service = TripPlannerService()

@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    adults: int = 2
) -> Dict[str, Any]:
    """Search for flights between two locations
    
    Args:
        origin: Origin city or airport code
        destination: Destination city or airport code
        departure_date: Departure date (YYYY-MM-DD)
        return_date: Return date (YYYY-MM-DD), optional
        adults: Number of adults
    """
    try:
        token = await service.get_amadeus_token()
        
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure_date,
            "adults": adults,
            "max": 10
        }
        
        if return_date:
            params["returnDate"] = return_date
        
        response = await service.client.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params
        )
        response.raise_for_status()
        
        data = response.json()
        flights = []
        
        for offer in data.get("data", []):
            flight_info = {
                "price": offer["price"]["total"],
                "currency": offer["price"]["currency"],
                "segments": []
            }
            
            for itinerary in offer["itineraries"]:
                for segment in itinerary["segments"]:
                    flight_info["segments"].append({
                        "departure": {
                            "airport": segment["departure"]["iataCode"],
                            "time": segment["departure"]["at"]
                        },
                        "arrival": {
                            "airport": segment["arrival"]["iataCode"],
                            "time": segment["arrival"]["at"]
                        },
                        "carrier": segment["carrierCode"],
                        "flight_number": segment["number"]
                    })
            
            flights.append(flight_info)
        
        return {
            "flights": flights,
            "search_params": params,
            "total_results": len(flights)
        }
        
    except Exception as e:
        # Fallback with mock data for demonstration
        logger.warning(f"Flight search failed, using mock data: {e}")
        return {
            "flights": [
                {
                    "price": "850.00",
                    "currency": "USD",
                    "segments": [
                        {
                            "departure": {"airport": origin, "time": f"{departure_date}T08:00:00"},
                            "arrival": {"airport": destination, "time": f"{departure_date}T14:30:00"},
                            "carrier": "AA",
                            "flight_number": "1234"
                        }
                    ]
                }
            ],
            "search_params": {"origin": origin, "destination": destination},
            "total_results": 1,
            "note": "Mock data - configure APIs for real results"
        }

@mcp.tool()
async def search_hotels(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int = 2,
    rooms: int = 1
) -> Dict[str, Any]:
    """Search for hotels in a destination
    
    Args:
        destination: Destination city
        check_in: Check-in date (YYYY-MM-DD)
        check_out: Check-out date (YYYY-MM-DD)
        guests: Number of guests
        rooms: Number of rooms
    """
    try:
        token = await service.get_amadeus_token()
        
        # First, get city code for the destination
        city_code = await service.get_city_code(destination)
        
        # Search for hotels
        params = {
            "cityCode": city_code,
            "checkInDate": check_in,
            "checkOutDate": check_out,
            "adults": guests,
            "rooms": rooms,
            "radius": 20,
            "radiusUnit": "KM",
            "hotelSource": "ALL"
        }
        
        response = await service.client.get(
            "https://test.api.amadeus.com/v1/reference-data/locations/hotels/by-city",
            headers={"Authorization": f"Bearer {token}"},
            params=params
        )
        response.raise_for_status()
        
        hotels_data = response.json()
        
        # Get hotel offers for the first few hotels
        hotels = []
        hotel_ids = [hotel["hotelId"] for hotel in hotels_data.get("data", [])[:10]]
        
        if hotel_ids:
            offers_response = await service.client.get(
                "https://test.api.amadeus.com/v3/shopping/hotel-offers",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "hotelIds": ",".join(hotel_ids),
                    "checkInDate": check_in,
                    "checkOutDate": check_out,
                    "adults": guests,
                    "rooms": rooms
                }
            )
            offers_response.raise_for_status()
            offers_data = offers_response.json()
            
            # Process hotel offers
            for hotel_offer in offers_data.get("data", []):
                hotel_info = hotel_offer.get("hotel", {})
                offers = hotel_offer.get("offers", [])
                
                if offers:
                    best_offer = min(offers, key=lambda x: float(x["price"]["total"]))
                    
                    # Calculate price per night
                    total_price = float(best_offer["price"]["total"])
                    check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                    check_out_date = datetime.strptime(check_out, "%Y-%m-%d")
                    nights = (check_out_date - check_in_date).days
                    price_per_night = total_price / nights if nights > 0 else total_price
                    
                    hotels.append({
                        "hotel_id": hotel_info.get("hotelId"),
                        "name": hotel_info.get("name", "Unknown Hotel"),
                        "rating": hotel_info.get("rating", "N/A"),
                        "price_per_night": round(price_per_night, 2),
                        "total_price": total_price,
                        "currency": best_offer["price"]["currency"],
                        "room_type": best_offer["room"]["type"],
                        "bed_type": best_offer["room"]["typeEstimated"]["bedType"],
                        "guests": best_offer["guests"]["adults"],
                        "amenities": hotel_info.get("amenities", []),
                        "address": hotel_info.get("address", {}),
                        "contact": hotel_info.get("contact", {}),
                        "description": best_offer["room"]["description"]["text"] if best_offer.get("room", {}).get("description") else "No description available"
                    })
        
        # Sort hotels by price
        hotels.sort(key=lambda x: x["price_per_night"])
        
        return {
            "hotels": hotels,
            "search_params": {
                "destination": destination,
                "city_code": city_code,
                "check_in": check_in,
                "check_out": check_out,
                "guests": guests,
                "rooms": rooms
            },
            "total_results": len(hotels),
            "source": "Amadeus API"
        }
        
    except Exception as e:
        logger.warning(f"Amadeus hotel search failed, using fallback: {e}")
        # Fallback with realistic mock data
        nights = (datetime.strptime(check_out, "%Y-%m-%d") - datetime.strptime(check_in, "%Y-%m-%d")).days
        
        return {
            "hotels": [
                {
                    "hotel_id": "BUDGET001",
                    "name": f"Budget Hotel {destination}",
                    "rating": "3",
                    "price_per_night": 85.00,
                    "total_price": 85.00 * nights,
                    "currency": "USD",
                    "room_type": "Standard Room",
                    "bed_type": "DOUBLE",
                    "guests": guests,
                    "amenities": ["FREE_WIFI", "AIR_CONDITIONING"],
                    "address": {"cityName": destination},
                    "contact": {},
                    "description": "Comfortable budget accommodation"
                },
                {
                    "hotel_id": "MID001",
                    "name": f"Central Hotel {destination}",
                    "rating": "4",
                    "price_per_night": 145.00,
                    "total_price": 145.00 * nights,
                    "currency": "USD",
                    "room_type": "Superior Room",
                    "bed_type": "QUEEN",
                    "guests": guests,
                    "amenities": ["FREE_WIFI", "AIR_CONDITIONING", "RESTAURANT", "FITNESS_CENTER"],
                    "address": {"cityName": destination},
                    "contact": {},
                    "description": "Modern hotel in city center"
                },
                {
                    "hotel_id": "LUX001",
                    "name": f"Luxury Resort {destination}",
                    "rating": "5",
                    "price_per_night": 320.00,
                    "total_price": 320.00 * nights,
                    "currency": "USD",
                    "room_type": "Deluxe Suite",
                    "bed_type": "KING",
                    "guests": guests,
                    "amenities": ["FREE_WIFI", "AIR_CONDITIONING", "RESTAURANT", "FITNESS_CENTER", "SPA", "POOL", "ROOM_SERVICE"],
                    "address": {"cityName": destination},
                    "contact": {},
                    "description": "Luxury accommodation with premium amenities"
                }
            ],
            "search_params": {
                "destination": destination,
                "check_in": check_in,
                "check_out": check_out,
                "guests": guests,
                "rooms": rooms
            },
            "total_results": 3,
            "source": "Fallback Data",
            "note": "Configure Amadeus API for real hotel data"
        }

@mcp.tool()
async def get_travel_suggestions(
    budget: float,
    origin: str,
    travel_style: str = "mid-range",
    interests: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Get travel destination suggestions based on budget and preferences
    
    Args:
        budget: Total budget for the trip
        origin: Origin city
        travel_style: Travel style (budget, mid-range, luxury)
        interests: List of interests (beach, culture, adventure, food, etc.)
    """
    if interests is None:
        interests = []
    
    # Mock suggestions based on budget ranges
    suggestions = []
    
    if budget < 1000:
        suggestions = [
            {"destination": "Mexico City", "estimated_cost": 800, "highlights": ["culture", "food"]},
            {"destination": "Costa Rica", "estimated_cost": 950, "highlights": ["adventure", "nature"]},
            {"destination": "Prague", "estimated_cost": 900, "highlights": ["culture", "architecture"]}
        ]
    elif budget < 2000:
        suggestions = [
            {"destination": "Barcelona", "estimated_cost": 1400, "highlights": ["culture", "beach", "food"]},
            {"destination": "Tokyo", "estimated_cost": 1800, "highlights": ["culture", "food", "technology"]},
            {"destination": "Iceland", "estimated_cost": 1600, "highlights": ["nature", "adventure"]}
        ]
    else:
        suggestions = [
            {"destination": "Maldives", "estimated_cost": 2800, "highlights": ["beach", "luxury"]},
            {"destination": "New Zealand", "estimated_cost": 2400, "highlights": ["adventure", "nature"]},
            {"destination": "Switzerland", "estimated_cost": 2600, "highlights": ["nature", "luxury"]}
        ]
    
    return {
        "budget": budget,
        "travel_style": travel_style,
        "interests": interests,
        "suggestions": suggestions
    }

@mcp.tool()
async def estimate_trip_cost(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    travel_style: str = "mid-range"
) -> Dict[str, Any]:
    """Estimate total trip cost including flights, hotels, and daily expenses
    
    Args:
        origin: Origin city
        destination: Destination city
        departure_date: Departure date (YYYY-MM-DD)
        return_date: Return date (YYYY-MM-DD)
        travel_style: Travel style (budget, mid-range, luxury)
    """
    try:
        # Get flight prices
        flight_data = search_flights(origin, destination, departure_date, return_date)
        
        # Get hotel prices
        hotel_data = search_hotels(destination, departure_date, return_date)
        
        # Calculate trip duration
        dep_date = datetime.strptime(departure_date, "%Y-%m-%d")
        ret_date = datetime.strptime(return_date, "%Y-%m-%d")
        nights = (ret_date - dep_date).days
        
        # Estimate daily expenses based on travel style
        daily_costs = {
            "budget": 50,
            "mid-range": 100,
            "luxury": 200
        }
        
        daily_expense = daily_costs.get(travel_style, 100)
        
        # Calculate costs
        flight_cost = float(flight_data["flights"][0]["price"]) if flight_data["flights"] else 0
        
        # Select hotel based on travel style
        selected_hotel = None
        if hotel_data["hotels"]:
            if travel_style == "budget":
                selected_hotel = min(hotel_data["hotels"], key=lambda x: x["price_per_night"])
            elif travel_style == "luxury":
                selected_hotel = max(hotel_data["hotels"], key=lambda x: x["price_per_night"])
            else:  # mid-range
                sorted_hotels = sorted(hotel_data["hotels"], key=lambda x: x["price_per_night"])
                selected_hotel = sorted_hotels[len(sorted_hotels) // 2]
            
            hotel_cost = selected_hotel["price_per_night"] * nights
        else:
            # Fallback pricing
            fallback_prices = {"budget": 70, "mid-range": 130, "luxury": 280}
            hotel_cost = fallback_prices.get(travel_style, 130) * nights
        
        food_activities = daily_expense * nights
        total_cost = flight_cost + hotel_cost + food_activities
        
        return {
            "trip_details": {
                "origin": origin,
                "destination": destination,
                "departure": departure_date,
                "return": return_date,
                "nights": nights,
                "travel_style": travel_style
            },
            "cost_breakdown": {
                "flights": flight_cost,
                "hotels": hotel_cost,
                "food_and_activities": food_activities,
                "total": total_cost
            },
            "selected_hotel": selected_hotel,
            "hotel_options": hotel_data["hotels"],
            "currency": "USD"
        }
        
    except Exception as e:
        logger.error(f"Cost estimation failed: {e}")
        return {"error": str(e)}

@mcp.tool()
async def plan_dual_origin_trip(
    person1_origin: str,
    person2_origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    travel_style: str = "mid-range",
    room_sharing: bool = True
) -> Dict[str, Any]:
    """Plan a trip where two people are coming from different cities
    
    Args:
        person1_origin: First person's origin city/airport
        person2_origin: Second person's origin city/airport
        destination: Meeting destination city
        departure_date: Departure date (YYYY-MM-DD)
        return_date: Return date (YYYY-MM-DD)
        travel_style: Travel style (budget, mid-range, luxury)
        room_sharing: Whether sharing a room
    """
    try:
        # Get flights for both people
        person1_flights = search_flights(person1_origin, destination, departure_date, return_date, 1)
        person2_flights = search_flights(person2_origin, destination, departure_date, return_date, 1)
        
        # Get hotel prices
        hotel_data = search_hotels(destination, departure_date, return_date, 2, 1 if room_sharing else 2)
        
        # Calculate trip duration
        dep_date = datetime.strptime(departure_date, "%Y-%m-%d")
        ret_date = datetime.strptime(return_date, "%Y-%m-%d")
        nights = (ret_date - dep_date).days
        
        # Get best flight prices
        person1_flight_cost = float(person1_flights["flights"][0]["price"]) if person1_flights["flights"] else 0
        person2_flight_cost = float(person2_flights["flights"][0]["price"]) if person2_flights["flights"] else 0
        
        # Hotel costs
        hotel_options = hotel_data["hotels"]
        selected_hotel = None
        if hotel_options:
            # Select hotel based on travel style
            if travel_style == "budget":
                selected_hotel = min(hotel_options, key=lambda x: x["price_per_night"])
            elif travel_style == "luxury":
                selected_hotel = max(hotel_options, key=lambda x: x["price_per_night"])
            else:  # mid-range
                sorted_hotels = sorted(hotel_options, key=lambda x: x["price_per_night"])
                selected_hotel = sorted_hotels[len(sorted_hotels) // 2]
            
            hotel_cost_per_night = selected_hotel["price_per_night"]
        else:
            # Fallback pricing
            hotel_cost_per_night = {"budget": 70, "mid-range": 130, "luxury": 280}.get(travel_style, 130)
        
        total_hotel_cost = hotel_cost_per_night * nights
        
        # Split hotel cost if sharing
        hotel_cost_per_person = total_hotel_cost / 2 if room_sharing else total_hotel_cost
        
        # Daily expenses per person
        daily_costs = {"budget": 40, "mid-range": 75, "luxury": 150}
        daily_expense_per_person = daily_costs.get(travel_style, 75)
        total_daily_expenses_per_person = daily_expense_per_person * nights
        
        # Calculate total costs
        person1_total = person1_flight_cost + hotel_cost_per_person + total_daily_expenses_per_person
        person2_total = person2_flight_cost + hotel_cost_per_person + total_daily_expenses_per_person
        grand_total = person1_total + person2_total
        
        return {
            "trip_summary": {
                "person1_origin": person1_origin,
                "person2_origin": person2_origin,
                "destination": destination,
                "departure_date": departure_date,
                "return_date": return_date,
                "nights": nights,
                "travel_style": travel_style,
                "room_sharing": room_sharing
            },
            "cost_breakdown": {
                "person1": {
                    "flight": person1_flight_cost,
                    "hotel_share": hotel_cost_per_person,
                    "daily_expenses": total_daily_expenses_per_person,
                    "total": person1_total
                },
                "person2": {
                    "flight": person2_flight_cost,
                    "hotel_share": hotel_cost_per_person,
                    "daily_expenses": total_daily_expenses_per_person,
                    "total": person2_total
                },
                "combined_total": grand_total
            },
            "flight_details": {
                "person1_flights": person1_flights["flights"][:3],
                "person2_flights": person2_flights["flights"][:3]
            },
            "hotel_selection": {
                "selected_hotel": selected_hotel,
                "all_options": hotel_data["hotels"]
            },
            "recommendations": {
                "best_arrival_time": "Try to arrive within 2-3 hours of each other",
                "meeting_point": f"Meet at {destination} airport or your hotel",
                "savings_tip": f"You could save ${abs(person1_flight_cost - person2_flight_cost):.0f} if both flew from the cheaper origin"
            }
        }
        
    except Exception as e:
        logger.error(f"Dual origin trip planning failed: {e}")
        return {"error": str(e)}

@mcp.tool()
async def create_trip_itinerary(
    destination: str,
    arrival_date: str,
    departure_date: str,
    interests: Optional[List[str]] = None,
    budget_per_day: float = 150,
    travel_style: str = "mid-range"
) -> Dict[str, Any]:
    """Create a detailed itinerary with activities and costs
    
    Args:
        destination: Destination city
        arrival_date: Arrival date (YYYY-MM-DD)
        departure_date: Departure date (YYYY-MM-DD)
        interests: List of interests (culture, food, romance, adventure, etc.)
        budget_per_day: Daily budget for activities and meals
        travel_style: Travel style (budget, mid-range, luxury)
    """
    if interests is None:
        interests = ["culture", "food", "romance"]
    
    try:
        # Calculate trip duration
        arrival = datetime.strptime(arrival_date, "%Y-%m-%d")
        departure = datetime.strptime(departure_date, "%Y-%m-%d")
        days = (departure - arrival).days
        
        # Activity database (in a real implementation, this would be from APIs)
        activities_db = {
            "culture": [
                {"name": "Museum Visit", "cost": 25, "duration": "2-3 hours"},
                {"name": "Historical Walking Tour", "cost": 35, "duration": "3 hours"},
                {"name": "Local Art Gallery", "cost": 15, "duration": "1-2 hours"},
                {"name": "Architecture Tour", "cost": 40, "duration": "2 hours"}
            ],
            "food": [
                {"name": "Food Market Tour", "cost": 60, "duration": "3 hours"},
                {"name": "Cooking Class", "cost": 80, "duration": "4 hours"},
                {"name": "Local Restaurant (dinner)", "cost": 70, "duration": "2 hours"},
                {"name": "Street Food Tour", "cost": 45, "duration": "2 hours"}
            ],
            "romance": [
                {"name": "Sunset Cruise", "cost": 85, "duration": "2 hours"},
                {"name": "Couples Spa Treatment", "cost": 200, "duration": "3 hours"},
                {"name": "Wine Tasting", "cost": 55, "duration": "2 hours"},
                {"name": "Rooftop Dinner", "cost": 120, "duration": "3 hours"}
            ],
            "adventure": [
                {"name": "City Bike Tour", "cost": 45, "duration": "4 hours"},
                {"name": "Hiking Excursion", "cost": 35, "duration": "6 hours"},
                {"name": "Kayaking/Water Sports", "cost": 65, "duration": "3 hours"},
                {"name": "Zip Line Adventure", "cost": 75, "duration": "2 hours"}
            ],
            "nature": [
                {"name": "Botanical Garden Visit", "cost": 20, "duration": "2 hours"},
                {"name": "National Park Day Trip", "cost": 50, "duration": "8 hours"},
                {"name": "Beach Day", "cost": 30, "duration": "4 hours"},
                {"name": "Wildlife Watching", "cost": 70, "duration": "4 hours"}
            ]
        }
        
        # Create daily itinerary
        itinerary = []
        total_activity_cost = 0
        
        for day in range(days):
            current_date = arrival + timedelta(days=day)
            day_activities = []
            day_cost = 0
            
            # Select activities based on interests
            for interest in interests[:2]:  # Max 2 interests per day
                if interest in activities_db:
                    activity = activities_db[interest][day % len(activities_db[interest])]
                    day_activities.append(activity)
                    day_cost += activity["cost"]
            
            # Add meals
            meal_costs = {
                "budget": {"breakfast": 15, "lunch": 25, "dinner": 35},
                "mid-range": {"breakfast": 25, "lunch": 40, "dinner": 60},
                "luxury": {"breakfast": 40, "lunch": 65, "dinner": 100}
            }
            
            meals = meal_costs.get(travel_style, meal_costs["mid-range"])
            day_cost += sum(meals.values())
            
            itinerary.append({
                "day": day + 1,
                "date": current_date.strftime("%Y-%m-%d"),
                "activities": day_activities,
                "meals": meals,
                "daily_cost": day_cost,
                "remaining_budget": budget_per_day - day_cost
            })
            
            total_activity_cost += day_cost
        
        # Transportation estimates
        local_transport_per_day = {"budget": 15, "mid-range": 25, "luxury": 40}
        transport_cost = local_transport_per_day.get(travel_style, 25) * days
        
        return {
            "destination": destination,
            "trip_dates": {
                "arrival": arrival_date,
                "departure": departure_date,
                "duration_days": days
            },
            "itinerary": itinerary,
            "cost_summary": {
                "total_activities_and_meals": total_activity_cost,
                "local_transportation": transport_cost,
                "daily_average": total_activity_cost / days if days > 0 else 0,
                "total_per_person": total_activity_cost + transport_cost
            },
            "travel_style": travel_style,
            "interests": interests,
            "tips": [
                "Book popular attractions in advance",
                "Keep some budget flexible for spontaneous activities",
                "Consider city tourist passes for multiple attractions",
                "Check if your hotel includes breakfast"
            ]
        }
        
    except Exception as e:
        logger.error(f"Itinerary creation failed: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    mcp.run()