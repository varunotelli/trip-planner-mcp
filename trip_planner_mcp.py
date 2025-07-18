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

def chunk_list(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

@mcp.tool()
async def search_hotels_chunked(
    city_code: str,
    check_in: str,
    check_out: str,
    adults: int = 1,
    room_quantity: int = 1,
    min_rating: Optional[int] = None
) -> dict:
    """
    Search hotels in a city by splitting hotel ID requests into manageable batches.

    This function:
    - Fetches a list of hotel IDs based on the city and date criteria.
    - Requests hotel offers in batches to avoid URL length limits or API errors.
    - Handles partial failures gracefully by skipping failed batches.
    - Always returns the collected data from successful batches.
    - Ensures the MCP server never fails completely due to partial API errors.

    Args:
        city_code: IATA city code (e.g., 'NYC').
        check_in: Check-in date in YYYY-MM-DD format.
        check_out: Check-out date in YYYY-MM-DD format.
        adults: Number of adults staying (default is 1).
        room_quantity: Number of rooms required (default is 1).
        min_rating: Minimum hotel star rating (optional).

    Returns:
        A dictionary containing:
        - 'hotels': A list of hotel offers successfully fetched.
        - 'search_params': The parameters used for the search.
        - 'total_results': Number of hotel offers returned.
        - 'source': The data source (Amadeus API).
        - Partial results are returned even if some requests fail; errors are logged but do not cause failure.
    """

    token = await service.get_amadeus_token()
    headers = {"Authorization": f"Bearer {token}"}

    hotel_list_url = "https://test.api.amadeus.com/v1/reference-data/locations/hotels/by-city"
    hotel_list_params = {
        "cityCode": city_code,
        "radius": 20,
        "radiusUnit": "KM",
        "hotelSource": "ALL",
    }
    if min_rating:
        hotel_list_params["ratings"] = str(min_rating)

    async with httpx.AsyncClient() as client:
        try:
            hotel_list_resp = await client.get(hotel_list_url, headers=headers, params=hotel_list_params)
            hotel_list_resp.raise_for_status()
            hotel_list_data = hotel_list_resp.json()
        except Exception as e:
            logger.warning(f"Amadeus hotel list fetch failed: {e}")
            return {"hotels": [], "error": str(e)}

        hotel_ids = [hotel["hotelId"] for hotel in hotel_list_data.get("data", [])]
        if not hotel_ids:
            return {"hotels": [], "error": "No hotels found for given city and rating"}

        offers_url = "https://test.api.amadeus.com/v3/shopping/hotel-offers"
        batch_size = 10
        all_offers = []

        for batch in chunk_list(hotel_ids[:50], batch_size):
            params = {
                "hotelIds": ",".join(batch),
                "checkInDate": check_in,
                "checkOutDate": check_out,
                "adults": adults,
                "roomQuantity": room_quantity,
                "currency": "USD"
            }
            try:
                offers_resp = await client.get(offers_url, headers=headers, params=params)
                #offers_resp.raise_for_status()
                offers_data = offers_resp.json()
                all_offers.extend(offers_data.get("data", []))
            except Exception as e:
                logger.warning(f"Amadeus hotel offers fetch failed for batch {batch}: {e}")
                # continue without stopping, keep data gathered so far
                continue

        hotels = []
        check_in_date_obj = datetime.strptime(check_in, "%Y-%m-%d")
        check_out_date_obj = datetime.strptime(check_out, "%Y-%m-%d")
        nights = (check_out_date_obj - check_in_date_obj).days or 1

        for offer_entry in all_offers:
            hotel_info = offer_entry.get("hotel", {})
            offers = offer_entry.get("offers", [])
            if not offers:
                continue

            best_offer = min(offers, key=lambda o: float(o["price"]["total"]))
            total_price = float(best_offer["price"]["total"])
            price_per_night = total_price / nights

            rating = hotel_info.get("rating")
            try:
                rating = float(rating)
            except (TypeError, ValueError):
                rating = None

            hotels.append({
                "hotel_id": hotel_info.get("hotelId"),
                "name": hotel_info.get("name"),
                "rating": rating,
                "price_per_night": round(price_per_night, 2),
                "total_price": total_price,
                "currency": best_offer["price"]["currency"],
                "room_type": best_offer["room"]["type"],
                "bed_type": best_offer["room"].get("typeEstimated", {}).get("bedType"),
                "guests": best_offer["guests"]["adults"],
                "amenities": hotel_info.get("amenities", []),
                "address": hotel_info.get("address", {}),
                "contact": hotel_info.get("contact", {}),
                "description": best_offer["room"].get("description", {}).get("text", "No description available")
            })

        hotels.sort(key=lambda h: h["price_per_night"])

        return {
            "hotels": hotels,
            "search_params": hotel_list_params,
            "total_results": len(hotels),
            "source": "Amadeus API"
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
        # Await async calls
        flight_data = await search_flights(origin, destination, departure_date, return_date)
        hotel_data = await search_hotels_chunked(destination, departure_date, return_date)

        
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
        # Await async calls
        person1_flights = await search_flights(person1_origin, destination, departure_date, return_date, 1)
        person2_flights = await search_flights(person2_origin, destination, departure_date, return_date, 1)
        hotel_data = await search_hotels_chunked(destination, departure_date, return_date, guests=2, rooms=1 if room_sharing else 2)
        
        # Select hotels similarly as in estimate_trip_cost
        selected_hotel = None
        if hotel_data["hotels"]:
            if travel_style == "budget":
                selected_hotel = min(hotel_data["hotels"], key=lambda x: x["price_per_night"])
            elif travel_style == "luxury":
                selected_hotel = max(hotel_data["hotels"], key=lambda x: x["price_per_night"])
            else:
                sorted_hotels = sorted(hotel_data["hotels"], key=lambda x: x["price_per_night"])
                selected_hotel = sorted_hotels[len(sorted_hotels) // 2]
        
        dep_date = datetime.strptime(departure_date, "%Y-%m-%d")
        ret_date = datetime.strptime(return_date, "%Y-%m-%d")
        nights = (ret_date - dep_date).days
        
        # Estimate daily expenses
        daily_costs = {"budget": 50, "mid-range": 100, "luxury": 200}
        daily_expense = daily_costs.get(travel_style, 100)
        
        # Calculate total hotel cost based on sharing
        hotel_cost = selected_hotel["price_per_night"] * nights if selected_hotel else 0
        
        # Flight costs for each person
        flight_cost_person1 = float(person1_flights["flights"][0]["price"]) if person1_flights["flights"] else 0
        flight_cost_person2 = float(person2_flights["flights"][0]["price"]) if person2_flights["flights"] else 0
        
        food_activities_cost = daily_expense * nights * 2  # for two persons
        
        total_cost = flight_cost_person1 + flight_cost_person2 + hotel_cost + food_activities_cost
        
        return {
            "trip_details": {
                "person1_origin": person1_origin,
                "person2_origin": person2_origin,
                "destination": destination,
                "departure": departure_date,
                "return": return_date,
                "nights": nights,
                "travel_style": travel_style,
                "room_sharing": room_sharing
            },
            "cost_breakdown": {
                "person1_flight": flight_cost_person1,
                "person2_flight": flight_cost_person2,
                "hotels": hotel_cost,
                "food_and_activities": food_activities_cost,
                "total": total_cost
            },
            "selected_hotel": selected_hotel,
            "hotel_options": hotel_data["hotels"],
            "currency": "USD"
        }
        
    except Exception as e:
        logger.error(f"Dual origin trip planning failed: {e}")
        return {"error": str(e)}


# Serve the MCP server on localhost:8010
if __name__ == "__main__":
    mcp.run()
