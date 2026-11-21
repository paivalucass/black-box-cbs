import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source=await readFile(new URL('../src/analysisCharts.ts',import.meta.url),'utf8')
const { outputText }=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}})
const { histogram,histogramOption,occupancyOption,segmentStyle }=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'))

test('histogram uses observed milliseconds and percentages adding to 100',()=>{
 const bins=histogram([.001,.001,.002,.003],2)
 assert.deepEqual(bins.map(b=>b.count),[2,2])
 assert.deepEqual(bins.map(b=>b.percentage),[50,50])
 assert.equal(bins[0].lower,1)
 assert.equal(bins[1].upper,3)
 const option=histogramOption(bins,'light')
 assert.equal(option.yAxis.min,0);assert.equal(option.yAxis.max,100)
 assert.equal(option.xAxis.name,'End-to-end latency (ms)')
 assert.equal(option.series[0].type,'bar')
})
test('empty and constant latency samples are not fabricated or divided by zero',()=>{
 assert.deepEqual(histogram([]),[])
 const bins=histogram([.0001,.0001,.0001,NaN])
 assert.equal(bins.length,1);assert.equal(bins[0].count,3);assert.equal(bins[0].percentage,100)
 assert.ok(bins[0].upper>bins[0].lower)
})
test('gantt keeps actual intervals, separates phases and converts seconds to ms',()=>{
 const segments=[
  {flow_id:'flow1',packet:'flow1-0',kind:'queueing',start:.001,end:.002,censored:false},
  {flow_id:'flow1',packet:'flow1-0',kind:'transmission',start:.002,end:.004,censored:false},
  {flow_id:'flow1',packet:'flow1-0',kind:'propagation',start:.004,end:.0045,censored:false},
 ]
 const option=occupancyOption(segments,['flow1'],'dark',0,.01)
 assert.equal(option.xAxis.max,10)
 assert.equal(option.yAxis.data[0],'flow1')
 assert.deepEqual(option.series[1].data[0].value.slice(1,3),[2,4])
 assert.equal(option.series[0].type,'custom')
 assert.equal(option.dataZoom[0].filterMode,'weakFilter')
 assert.ok(segmentStyle('queueing','flow1','dark').decal)
 assert.notDeepEqual(segmentStyle('queueing','flow1','dark').decal,segmentStyle('propagation','flow1','dark').decal)
 assert.equal(segmentStyle('transmission','flow1','dark').decal,undefined)
 const packets=occupancyOption(segments,['flow1'],'light',0,.01,true)
 assert.deepEqual(packets.yAxis.data,['flow1-0'])
})
