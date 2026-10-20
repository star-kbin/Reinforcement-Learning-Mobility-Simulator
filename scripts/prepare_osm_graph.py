"""일회성 OSM 추출 파일을 재현 가능한 도로망으로 변환합니다."""
import argparse,base64,gzip,json
from pathlib import Path

def build(source, target):
 data=json.loads(Path(source).read_text(encoding='utf-8'))
 nodes={e['id']:[e['lat'],e['lon']] for e in data['elements'] if e['type']=='node'}
 edges=[];used=set()
 for way in data['elements']:
  if way['type']!='way': continue
  tags=way.get('tags',{})
  if tags.get('access') in ('no','private') or tags.get('motor_vehicle') in ('no','private') or tags.get('motorcar')=='no': continue
  oneway=tags.get('oneway','yes' if tags.get('junction')=='roundabout' or tags.get('highway') in ('motorway','motorway_link') else 'no')
  ids=way['nodes']
  for a,b in zip(ids,ids[1:]):
   if a not in nodes or b not in nodes: continue
   if oneway=='-1': a,b=b,a
   edges.append([a,b]);used.update((a,b))
   if oneway not in ('yes','1','true','-1'): edges.append([b,a])
 ids=sorted(used);index={n:i for i,n in enumerate(ids)}
 graph={'bounds':[37.475,126.915,37.535,127.055], 'source':'OpenStreetMap contributors / Overpass API', 'snapshot':'2026-10-20', 'nodes':[nodes[n] for n in ids], 'edges':[[index[a],index[b]] for a,b in edges]}
 raw=json.dumps(graph,separators=(',',':')).encode()
 Path(target).parent.mkdir(parents=True,exist_ok=True)
 Path(target).write_text(base64.b64encode(gzip.compress(raw,mtime=0)).decode()+'\n',encoding='ascii')
 print(len(ids),'nodes',len(edges),'directed edges',Path(target).stat().st_size,'bytes')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('target');a=p.parse_args();build(a.source,a.target)
