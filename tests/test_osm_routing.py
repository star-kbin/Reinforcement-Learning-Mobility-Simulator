"""실제 OSM 도로망, 방향성, 실패 처리, 오프라인 실행 검증."""
import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulator.osm_routing import OSMRoutingService, OSMRoutingError
from simulator.routing import build_routing_service
from simulator.scenarios.loader import load_config, load_scenario
from simulator.env import MobilityEnv
from simulator.evaluation.runner import run_episode
from simulator.policies.factory import build_policy

ROOT = Path(__file__).resolve().parents[1]

class OSMRoutingTests(unittest.TestCase):
    def test_real_road_shape_and_distance(self):
        service=OSMRoutingService({})
        plan=service.plan_route((37.4915254,127.0310762),(37.504,126.95),1,30)
        self.assertEqual(plan.source,'osm_offline')
        self.assertGreater(len(plan.path),20)
        self.assertGreater(plan.metadata['road_distance_meters'],1000)
        self.assertGreater(plan.total_time,30)
        self.assertLessEqual(plan.metadata['connector_distance_meters'],1000)
        cached=service.plan_route(plan.path[0],plan.path[-1],1,3000)
        self.assertEqual(cached.path,plan.path)
        self.assertGreaterEqual(cached.total_time,3000)
        cached.path.clear()
        self.assertTrue(service.plan_route(plan.path[0],plan.path[-1],1,30).path)

    def test_outside_coverage_never_becomes_straight_line(self):
        with self.assertRaisesRegex(OSMRoutingError,'범위 밖'):
            OSMRoutingService({}).plan_route((35.1,129.1),(37.5,127),1,30)

    def test_oneway_and_disconnected_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'graph.b64'
            graph={'bounds':[37,126,38,128], 'snapshot':'test',
                   'nodes':[[37.5,127],[37.5,127.001],[37.501,127.001],[37.501,127],[37.51,127.01],[37.51,127.011]],
                   'edges':[[0,1],[1,2],[2,3],[3,0],[4,5],[5,4]]}
            file.write_bytes(base64.b64encode(gzip.compress(json.dumps(graph).encode())))
            service=OSMRoutingService({'osm_graph_path':str(file),'osm_max_snap_meters':1})
            plan=service.plan_route(graph['nodes'][1],graph['nodes'][0],1,1)
            self.assertEqual(plan.path,[tuple(graph['nodes'][i]) for i in [1,2,3,0]])
            with self.assertRaisesRegex(OSMRoutingError,'경로가 없습니다'):
                service.plan_route(graph['nodes'][0],graph['nodes'][4],1,1)

    def test_default_episodes_work_without_network(self):
        config=load_config(ROOT/'configs/base_latlon.yaml')
        scenario=load_scenario(ROOT/'scenarios/scenario_dongjak_history.yaml')
        with patch('urllib.request.urlopen',side_effect=AssertionError('Network is forbidden')):
            for policy in ('nearest','eta','random'):
                env=MobilityEnv(config=config,scenario=scenario)
                result=run_episode(env,build_policy(policy)).to_dict()
                sources={v.get('active_route_source') for f in result['timeline'] for v in f['vehicles']}
                self.assertIn('osm_offline',sources,policy)
                self.assertFalse(any(s and 'fallback' in s for s in sources),policy)
                self.assertGreater(len(result['timeline']),1)

    def test_old_provider_config_uses_osm(self):
        self.assertIsInstance(build_routing_service({'simulation':{'space_mode':'latlon','routing_provider':'tmap'},'integrations':{'tmap_app_key':'ignored'}}),OSMRoutingService)
        self.assertIsNone(build_routing_service({'simulation':{'space_mode':'grid'}}))

if __name__=='__main__': unittest.main()
