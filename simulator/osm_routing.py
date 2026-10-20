"""Offline OSM shortest-path routing; no credentials or network requests."""
from __future__ import annotations
import base64
from collections import OrderedDict
from functools import lru_cache
import gzip
import heapq
import json
import math
from pathlib import Path

from simulator.routing import BaseRoutingService, RoutePlan, _geo_distance_meters
from simulator.entities.position import distance_between

DEFAULT_GRAPH = Path(__file__).resolve().parents[1] / 'data/osm/seoul_demo.json.gz.b64'

@lru_cache(maxsize=2)
def load_osm_graph(path: str = str(DEFAULT_GRAPH)) -> dict:
    graph = json.loads(gzip.decompress(base64.b64decode(Path(path).read_bytes())))
    adjacency = [[] for _ in graph['nodes']]
    for a, b in graph['edges']:
        adjacency[a].append((b, _geo_distance_meters(graph['nodes'][a], graph['nodes'][b])))
    graph['adjacency'] = adjacency
    # Only nodes that can depart AND arrive are candidates for endpoint snapping.
    incoming = {b for _, b in graph['edges']}
    graph['snap_nodes'] = [i for i, edges in enumerate(adjacency) if edges and i in incoming]
    return graph

class OSMRoutingError(ValueError):
    """Route cannot be supplied; never replace a road route with a straight line."""

class OSMRoutingService(BaseRoutingService):
    def __init__(self, config: dict):
        self.graph = load_osm_graph(str(config.get('osm_graph_path') or DEFAULT_GRAPH))
        self.average_speed_kph = float(config.get('osm_average_speed_kph', 25.0))
        if not math.isfinite(self.average_speed_kph) or self.average_speed_kph <= 0:
            raise OSMRoutingError('주행 평균 속도는 0보다 큰 유한한 수여야 합니다.')
        self.snap_limit = float(config.get('osm_max_snap_meters', 500.0))
        self.cache = OrderedDict()
        self.snap_cache = {}

    def _snap(self, point):
        point = tuple(point)
        if point in self.snap_cache:
            return self.snap_cache[point]
        south, west, north, east = self.graph['bounds']
        if not (south <= point[0] <= north and west <= point[1] <= east):
            raise OSMRoutingError(f'좌표 {point}가 내장 OSM 범위 밖입니다. 서울 동작·서초 실습 지역으로 수정하거나 별도 OSM 그래프를 지정해 주세요.')
        candidates = sorted((_geo_distance_meters(point, self.graph['nodes'][i]), i) for i in self.graph['snap_nodes'])[:6]
        candidates = [(d,i) for d,i in candidates if d <= self.snap_limit]
        if not candidates:
            raise OSMRoutingError(f'좌표 {point}에서 {self.snap_limit:g}m 이내 자동차 도로를 찾지 못했습니다.')
        self.snap_cache[point] = candidates
        return candidates

    def nearest_node(self, point):
        distance, index = self._snap(point)[0]
        return index, tuple(self.graph['nodes'][index]), distance

    def plan_route(self, start, end, speed, time_step, reference_time=None):
        start, end = tuple(start), tuple(end)
        key = (start, end)
        if key in self.cache:
            cached = self.cache[key]
            self.cache.move_to_end(key)
            return RoutePlan(list(cached.path), cached.total_distance, max(cached.total_time, float(time_step)) if start != end else 0., cached.source, dict(cached.metadata))
        starts, ends = self._snap(start), self._snap(end)
        if start == end:
            return RoutePlan([start], 0., 0., 'osm_offline', {'network_distance_meters':0.})
        # Multi-source Dijkstra includes short access connectors in endpoint choice.
        dist = {}; previous = {}; queue=[]; roots={}
        for d, i in starts:
            dist[i]=d; previous[i]=None; roots[i]=(i,d);heapq.heappush(queue,(d,i))
        targets={i:d for d,i in ends};best=float('inf');finish=None
        while queue:
            cost, node=heapq.heappop(queue)
            if cost != dist[node]: continue
            if cost >= best: break
            if node in targets and cost+targets[node] < best:
                best=cost+targets[node];finish=node
            for nxt, length in self.graph['adjacency'][node]:
                candidate=cost+length
                if candidate < dist.get(nxt,float('inf')):
                    dist[nxt]=candidate;previous[nxt]=node;roots[nxt]=roots[node]
                    heapq.heappush(queue,(candidate,nxt))
        if finish is None:
            raise OSMRoutingError('지정 좌표 사이에 일방통행을 준수하는 자동차 경로가 없습니다. 출발·도착 위치를 도로 가까이 수정해 주세요.')
        route=[];node=finish
        while node is not None:
            route.append(node);node=previous[node]
        route.reverse();path=[start]
        for i in route:
            point=tuple(self.graph['nodes'][i])
            if point != path[-1]:path.append(point)
        if path[-1]!=end:path.append(end)
        start_node,start_connector=roots[finish];end_connector=targets[finish]
        metadata={'network_distance_meters':round(best,2),'road_distance_meters':round(best-start_connector-end_connector,2),
                  'average_speed_kph':self.average_speed_kph,'coordinate_mode':'osm_network',
                  'connector_distance_meters':round(start_connector+end_connector,2),
                  'snap':{'start':{'distance_meters':round(start_connector,2),'snapped':self.graph['nodes'][start_node]},
                          'end':{'distance_meters':round(end_connector,2),'snapped':self.graph['nodes'][finish]}},
                  'path_point_count':len(path),'data_snapshot':self.graph['snapshot']}
        plan=RoutePlan(path,sum(distance_between(a,b) for a,b in zip(path,path[1:])),best/(self.average_speed_kph/3.6),'osm_offline',metadata)
        self.cache[key]=plan
        if len(self.cache)>512:self.cache.popitem(last=False)
        return RoutePlan(list(plan.path),plan.total_distance,max(plan.total_time,float(time_step)),plan.source,dict(plan.metadata))
