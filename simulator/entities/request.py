"""Request entity."""

from dataclasses import dataclass


@dataclass
class Request:
    request_id: str
    created_at: int
    origin: tuple[int, int]
    destination: tuple[int, int]
    max_wait_time: int
    status: str = "waiting"
    assigned_vehicle_id: str | None = None
    pickup_time: int | None = None
    dropoff_time: int | None = None
    cancelled_at: int | None = None
    logged_complete: bool = False

    def assign(self, vehicle_id: str, current_time: int) -> None:
        self.assigned_vehicle_id = vehicle_id
        self.status = "assigned"

    def mark_onboard(self, current_time: int) -> None:
        self.pickup_time = current_time
        self.status = "onboard"

    def mark_completed(self, current_time: int) -> None:
        self.dropoff_time = current_time
        self.status = "completed"

    def mark_cancelled(self, current_time: int) -> None:
        self.cancelled_at = current_time
        self.status = "cancelled"

    def waiting_time(self, current_time: int) -> int:
        return current_time - self.created_at

    def to_dict(self, current_time: int) -> dict:
        return {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "origin": self.origin,
            "destination": self.destination,
            "max_wait_time": self.max_wait_time,
            "status": self.status,
            "assigned_vehicle_id": self.assigned_vehicle_id,
            "pickup_time": self.pickup_time,
            "dropoff_time": self.dropoff_time,
            "cancelled_at": self.cancelled_at,
            "waiting_time": self.waiting_time(current_time),
        }
