"""Random endpoints must stay on directed, mutually accessible OSM roads."""
import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from simulator.osm_routing import OSMRoutingService, OSMRoutingError
from simulator.scenarios.generator import (
    DEFAULT_LATLON_START, approx_point_distance, ensure_vehicle_positions,
    generate_random_requests,
)


class RoadGenerationTests(unittest.TestCase):
    def test_real_graph_seeds_and_generated_vehicles_are_on_accessible_roads(self):
        config = {'simulation': {'space_mode':'latlon','max_time':300,'time_step':30},
                  'vehicles': {'initial_positions':[list(DEFAULT_LATLON_START)]}}
        vehicles = ensure_vehicle_positions(config, vehicle_count=4)
        service = OSMRoutingService({})
        for point in vehicles[1:]:
            self.assertLess(service.nearest_node(point)[2], 1e-6)
            service.plan_route(vehicles[0], point, 1, 1)
        for seed in range(5):
            requests = generate_random_requests(vehicles, 12, 'latlon', config, seed, 500, 100)
            self.assertEqual(requests, generate_random_requests(vehicles, 12, 'latlon', config, seed, 500, 100))
            for request in requests:
                origin, destination = request['origin'], request['destination']
                self.assertNotEqual(origin, destination)
                self.assertEqual(request['max_wait_time'], 100)
                for point in [origin, destination]:
                    self.assertLess(service.nearest_node(point)[2], 1e-6)
                    self.assertLessEqual(min(approx_point_distance(point, v, 'latlon') for v in vehicles), 500)
                for vehicle in vehicles:
                    service.plan_route(vehicle, origin, 1, 1)
                    service.plan_route(destination, vehicle, 1, 1)
                service.plan_route(origin, destination, 1, 1)
                self.assertTrue(request['metadata']['route_accessibility_validated'])

    def test_disconnected_oneway_and_crowding_never_produce_offroad_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'graph.b64'
            nodes = [[37.5,127],[37.5,127.001],[37.501,127.001],
                     [37.501,127],[37.5001,127],[37.5001,127.001],[37.5002,127]]
            graph = {'bounds':[37,126,38,128], 'snapshot':'test', 'nodes':nodes,
                     'edges':[[0,1],[1,2],[2,3],[3,0],[4,5],[5,4],[0,6],[6,4]]}
            path.write_bytes(base64.b64encode(gzip.compress(json.dumps(graph).encode())))
            config = {'simulation':{'osm_graph_path':str(path),'max_time':30,
                                    'snap_generated_requests_to_road_network':False}}
            requests = generate_random_requests([nodes[0]], 25, 'latlon', config, 5, 500)
            for request in requests:
                self.assertIn(request['origin'], nodes[:4])
                self.assertIn(request['destination'], nodes[:4])
            self.assertTrue(any(r['metadata']['spacing_relaxed'] for r in requests))
            # Default snap distance also must not bypass disconnected roads.
            service = OSMRoutingService(config['simulation'])
            with self.assertRaises(OSMRoutingError):
                service.plan_route(nodes[4], nodes[0], 1, 1)
            with self.assertRaisesRegex(OSMRoutingError,'왕복'):
                generate_random_requests([nodes[0],nodes[4]], 1, 'latlon', config, 1, 500)
            with self.assertRaisesRegex(OSMRoutingError,'부족'):
                generate_random_requests([nodes[0]], 1, 'latlon', config, 1, 0)
            with self.assertRaises(OSMRoutingError):
                generate_random_requests([[35,129]], 1, 'latlon', config, 1, 500)

    def test_empty_generation_and_grid_remain_supported(self):
        self.assertEqual(generate_random_requests([],0,'latlon',{},42,500), [])
        config = {'simulation':{'grid_width':10,'grid_height':10,'max_time':100}}
        for request in generate_random_requests([[0,0]],5,'grid',config,42,3):
            for point in [request['origin'],request['destination']]:
                self.assertTrue(all(isinstance(x,int) and 0 <= x < 10 for x in point))


if __name__ == '__main__':
    unittest.main()
