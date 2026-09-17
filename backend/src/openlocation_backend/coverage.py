"""Worldwide coverage; coordinates and route geometry retain strict validation."""
from .models import Coordinate, Route


def require_coordinate(coordinate: Coordinate):
    # Revalidate callers that constructed models without Pydantic validation.
    Coordinate.model_validate({'latitude': coordinate.latitude, 'longitude': coordinate.longitude})


def require_route(route: Route):
    Route.model_validate(route.model_dump())
