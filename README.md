# Trip Planner MCP Server

A fast, async trip planning server built with [FastMCP](https://github.com/mcp-org/fastmcp) that leverages the [Amadeus Travel APIs](https://developers.amadeus.com/) to provide real-time flight and hotel search, travel suggestions, and itinerary planning.

---

## Features

- Search flights and hotel offers with live Amadeus API integration  
- Support for multi-origin trip planning (e.g., group trips from different cities)  
- Travel suggestions based on budget, style, and interests  
- Trip cost estimation combining flights, hotels, and daily expenses  
- Detailed daily itinerary creation with activities and meal budgeting  
- Async, event-driven architecture powered by FastMCP for scalable interaction  

---

## Getting Started

### Prerequisites

- Python 3.8+  
- [Amadeus API account](https://developers.amadeus.com/) with API key and secret  
- `python-dotenv` for environment variable management  

### Installation

1. Clone this repository:
    ```bash
    git clone https://github.com/your-username/trip-planner-mcp.git
    cd trip-planner-mcp
    ```

2. Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3. Create a `.env` file in the project root with your Amadeus credentials:
    ```
    AMADEUS_API_KEY=your_api_key_here
    AMADEUS_API_SECRET=your_api_secret_here
    ```

### Running the Server

```bash
python trip_planner.py
