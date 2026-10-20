"""Regression checks: OSM vehicles must not invent straight-line driving."""
import unittest

from simulator.entities.vehicle import Vehicle


class RoadMovementFallbackTests(unittest.TestCase):
    def make_vehicle(self, position, target):
        vehicle = Vehicle(
            vehicle_id="V1",
            position=position,
            capacity=1,
            speed=1.0,
            available_until=1000,
        )
        vehicle.target_position = target
        return vehicle

    def test_latlon_without_route_does_not_move_straight(self):
        start = (37.5001, 127.0301)
        target = (37.5011, 127.0311)
        vehicle = self.make_vehicle(start, target)
        distance = vehicle._advance_position(1)
        self.assertEqual(vehicle.position, start)
        self.assertEqual(distance, 0.0)
        self.assertEqual(vehicle.actual_route_paths, [])

    def test_grid_without_route_preserves_step_movement(self):
        vehicle = self.make_vehicle((1.0, 1.0), (3.0, 1.0))
        distance = vehicle._advance_position(1)
        self.assertEqual(vehicle.position, (2.0, 1.0))
        self.assertEqual(distance, 1.0)


if __name__ == "__main__":
    unittest.main()
