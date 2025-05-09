# Axiom Project Server

A FastAPI backend server that receives meme project data from the Axiom browser plugin and provides a WebSocket connection for real-time updates.

## Features

- REST API endpoint to receive meme project data
- WebSocket connection for real-time updates
- Redis storage (DB 6) for meme projects
- API endpoint to retrieve recent projects with pagination
- Time-based sorting (newest first)

## Setup

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your Redis configuration
   ```

4. Run the server:
   ```bash
   python main.py
   ```

   Alternatively, you can use uvicorn directly:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 5001
   ```

## API Endpoints

- `POST /api/meme-projects`: Receive meme project data from the browser plugin
- `GET /api/meme-projects?page=1&page_size=10`: Get recent meme projects with pagination
- `WebSocket /ws`: Connect to receive real-time updates

## Pagination

The API supports pagination with the following query parameters:
- `page`: Page number (default: 1)
- `page_size`: Number of items per page (default: 10, max: 100)

The response includes pagination metadata:
```json
{
  "status": "success",
  "projects": [...],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total_count": 50,
    "total_pages": 5
  }
}
```

## Requirements

- Python 3.8+
- Redis server (configured to use DB 6) 