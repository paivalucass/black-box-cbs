import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source=await readFile(new URL('../src/types.ts',import.meta.url),'utf8')
const { outputText }=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}})
const { normalizeScenario,normalizeTopology }=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'))
const legacy=JSON.parse(await readFile(new URL('../../tests/fixtures/legacy-scenarios/line-1.json',import.meta.url),'utf8'))

test('loading old models derives cable lengths and connected port counts',()=>{
 const s=normalizeScenario(legacy)
 assert.equal(s.schema_version,4)
 for(const n of s.nodes){
  const degree=s.links.filter(l=>l.a.node===n.id||l.b.node===n.id).length
  assert.deepEqual(n.ports.map(p=>p.index),Array.from({length:degree},(_,i)=>i))
 }
 for(const link of s.links)assert.equal(link.length_m,link.delay*200000000)
 assert.deepEqual(normalizeScenario(s),s)
})

test('deleting a connection keeps surviving queue settings with that connection',()=>{
 const s=normalizeScenario(legacy)
 const sw=s.nodes.find(n=>n.kind==='switch')
 const cable=s.links.find(l=>l.a.node==='listener'||l.b.node==='listener')
 const endpoint=cable.a.node===sw.id?cable.a:cable.b
 sw.ports[endpoint.port].queues[1].idle_slope=12345678
 const before=endpoint.port
 s.links=s.links.filter(l=>l.a.node!=='talker'&&l.b.node!=='talker')
 normalizeTopology(s)
 assert.equal(endpoint.port,before-1)
 assert.equal(sw.ports[endpoint.port].queues[1].idle_slope,12345678)
 assert.equal(s.nodes.find(n=>n.id==='talker').ports.length,0)
})

test('new connections need only node IDs and automatically create switch ports',()=>{
 const s=normalizeScenario(legacy), sw=s.nodes.find(n=>n.kind==='switch')
 const count=sw.ports.length
 s.nodes.push({id:'newHost',kind:'device',ports:[],clock_drift_ppm:0,x:0,y:0})
 s.links.push({id:'newCable',a:{node:sw.id},b:{node:'newHost'},bitrate:1e8,length_m:20})
 normalizeTopology(s)
 const link=s.links.at(-1)
 assert.equal(link.a.port,count)
 assert.equal(link.b.port,0)
 assert.equal(link.delay,100e-9)
 assert.equal(sw.ports.length,count+1)
 const snapshot=structuredClone(s)
 normalizeTopology(s)
 assert.deepEqual(s,snapshot)
})

test('legacy lower-index-first configurations preserve their priority through migration',()=>{
 const data=structuredClone(legacy), p=data.nodes[0].ports[0]
 p.higher_queue_index_first=false
 p.class_to_queue=[0,1]
 p.queues[0].capacity=111;p.queues[1].capacity=222
 const s=normalizeScenario(data), next=s.nodes[0].ports[0]
 assert.equal(next.higher_queue_index_first,true)
 assert.deepEqual(next.class_to_queue,[1,0])
 assert.deepEqual(next.queues.map(q=>q.capacity),[222,111])
 assert.deepEqual(normalizeScenario(s),s)
})


test('old traffic stops disappear from editable and exported models',()=>{
 const data=structuredClone(legacy)
 for(const f of data.flows)f.stop=.0001
 const s=normalizeScenario(data)
 assert.equal(s.experiment.duration,data.experiment.duration)
 assert.ok(s.flows.every(f=>!Object.hasOwn(f,'stop')))
})
