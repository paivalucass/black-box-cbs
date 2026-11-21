<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { VueFlow } from '@vue-flow/core'
import type { Node as GraphNode, Edge } from '@vue-flow/core'
import LatencyAnalysis from './LatencyAnalysis.vue'
import LinkOccupancyAnalysis from './LinkOccupancyAnalysis.vue'
import ReceiverSelect from './ReceiverSelect.vue'
import RouteSelect from './RouteSelect.vue'
import { queue, normalizeScenario, normalizeTopology } from './types'
import type { Scenario, Device, Flow, Port } from './types'
const scenario=ref<Scenario>({schema_version:4,ned_name:'Scenario',name:'New scenario',nodes:[],links:[],flows:[],experiment:{duration:1.1,warmup:0,seed:1}})
const templates=ref<Scenario[]>([]), revisions=ref<any[]>([]), jobs=ref<any[]>([]), capabilities=ref<any>({features:{}})
const mainTabs=[
 {id:'experiment',label:'Experiment'},
 {id:'build',label:'Build Simulation'},
 {id:'run',label:'Run Simulation'},
 {id:'analysis',label:'Analysis'}
] as const
const buildTabs=[
 {id:'topology',label:'Topology'},
 {id:'traffic',label:'Traffic'},
 {id:'review',label:'Review'}
] as const
const analysisTabs=[{id:'latency',label:'Latency'},{id:'occupancy',label:'Per link flow occupancy'}] as const
const activeAnalysisTab=ref<'latency'|'occupancy'>('latency')
type MainTabId=typeof mainTabs[number]['id']
type BuildTabId=typeof buildTabs[number]['id']
const activeTab=ref<MainTabId>('experiment')
const activeBuildTab=ref<BuildTabId>('topology')
const currentTitle=computed(()=>mainTabs.find(t=>t.id===activeTab.value)?.label)
const theme=ref<'dark'|'light'>(localStorage.getItem('tsn-theme')==='light'?'light':'dark')
watch(theme,value=>{
 document.documentElement.dataset.theme=value
 localStorage.setItem('tsn-theme',value)
},{immediate:true})
function tabKey(event:KeyboardEvent,index:number,scope:'main'|'build'|'analysis'){
 const tabs=scope==='main'?mainTabs:scope==='build'?buildTabs:analysisTabs
 let next=index
 if(event.key==='ArrowDown')next=(index+1)%tabs.length
 else if(event.key==='ArrowUp')next=(index+tabs.length-1)%tabs.length
 else if(event.key==='Home')next=0
 else if(event.key==='End')next=tabs.length-1
 else return
 event.preventDefault()
 if(scope==='main')activeTab.value=mainTabs[next].id
 else if(scope==='build')activeBuildTab.value=buildTabs[next].id
 else activeAnalysisTab.value=analysisTabs[next].id
 document.getElementById(scope+'-tab-'+tabs[next].id)?.focus()
}
const backendHealth=ref<any>(null)
const backendReady=computed(()=>backendHealth.value?.results_pipeline==='json-v1'&&backendHealth.value?.compiler_version==='3.3')
const selected=ref(''), result=ref<any>(null), job=ref<any>(null), message=ref(''), busy=ref(false), file=ref('omnetpp.ini')
const graphNodes=ref<GraphNode[]>([]), history=ref<string[]>([]), future=ref<string[]>([])
const flowId=ref(''), chosenRevision=ref(''), chosenTemplate=ref('0'), compareId=ref(''), compared=ref<any>(null)
const libraryId=ref('')
const runModelId=ref('draft'), analysisRunId=ref(''), analysisJob=ref<any>(null), analysisLoading=ref(false), analysisError=ref('')
let analysisRequest=0
const runScenario=computed(()=>runModelId.value==='draft'?scenario.value:revisions.value.find(r=>r.id===runModelId.value)?.scenario)
const completedJobs=computed(()=>jobs.value.filter(j=>j.status==='completed'))
const displayedScenario=computed(()=>activeTab.value==='analysis'?analysisJob.value?.body.scenario:activeTab.value==='run'?runScenario.value:scenario.value)
const linkDraft=ref({source:'',target:'',length_m:10,bitrate:100000000})
const openNodes=ref(new Set<string>()), openFlows=ref(new Set<Flow>())
const connectionProblem=computed(()=>linkProblem(linkDraft.value.source,linkDraft.value.target))
const devices=computed(()=>scenario.value.nodes.filter(n=>n.kind==='device'))
const chosen=computed(()=>scenario.value.nodes.find(n=>n.id===selected.value))

const metrics=computed(()=>analysisJob.value?.metrics)
const finished=new Set(['completed','failed','cancelled','interrupted'])
let timer:ReturnType<typeof setInterval>|undefined
async function api(path:string, body?:unknown, method='POST'){
 const response=await fetch('/api/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const data=await response.json(); if(!response.ok)throw new Error(data.diagnostics?JSON.stringify(data.diagnostics,null,2):data.error||response.statusText);return data
}
function snapshot(){history.value.push(JSON.stringify(scenario.value));history.value=history.value.slice(-40);future.value=[]}
function syncGraph(){graphNodes.value=scenario.value.nodes.map(n=>({id:n.id,label:n.id+' · '+(n.kind==='switch'?'TSN switch':'End device'),position:{x:n.x,y:n.y},class:n.kind}))}
function restore(s:Scenario){chosenRevision.value='';localStorage.removeItem('tsn-saved-id');history.value=[];future.value=[];scenario.value=normalizeScenario(s);openNodes.value.clear();openFlows.value.clear();selected.value='';result.value=null;syncGraph()}
function undo(){if(!history.value.length)return;future.value.push(JSON.stringify(scenario.value));scenario.value=normalizeScenario(JSON.parse(history.value.pop()!));openFlows.value.clear();syncGraph()}
function redo(){if(!future.value.length)return;history.value.push(JSON.stringify(scenario.value));scenario.value=normalizeScenario(JSON.parse(future.value.pop()!));openFlows.value.clear();syncGraph()}
const edges=computed<Edge[]>(()=>scenario.value.links.map(l=>({id:l.id,source:l.a.node,target:l.b.node,label:`${l.bitrate/1e6} Mbps · ${l.length_m} m`,animated:!!Object.values(result.value?.paths?.[flowId.value]||{}).flat().some((h:any)=>h.node===l.a.node&&h.port===l.a.port||h.node===l.b.node&&h.port===l.b.port)})))
function unique(prefix:string,items:{id:string}[]){let i=0;while(items.some(n=>n.id===prefix+i))i++;return prefix+i}
function addNode(kind:'device'|'switch') {snapshot();const n:Device={id:unique(kind==='device'?'host':'sw',scenario.value.nodes),kind,ports:[],clock_drift_ppm:0,x:100+(scenario.value.nodes.length%4)*210,y:100+Math.floor(scenario.value.nodes.length/4)*120};scenario.value.nodes.push(n);selected.value=n.id;syncGraph()}
function removeNode(){snapshot();const id=selected.value;scenario.value.nodes=scenario.value.nodes.filter(n=>n.id!==id);scenario.value.links=scenario.value.links.filter(l=>l.a.node!==id&&l.b.node!==id);scenario.value.flows=scenario.value.flows.filter(f=>f.source!==id&&!f.receivers.includes(id));normalizeTopology(scenario.value);openNodes.value.delete(id);selected.value='';syncGraph()}
function duplicateNode(){if(!chosen.value)return;snapshot();const n=JSON.parse(JSON.stringify(chosen.value));n.id=unique(n.kind==='device'?'host':'sw',scenario.value.nodes);n.x+=40;n.y+=60;n.ports=[];scenario.value.nodes.push(n);syncGraph()}
function linkProblem(source:string,target:string){
 if(!source||!target)return ''
 if(source===target)return 'Choose two different nodes.'
 if(!Number.isFinite(linkDraft.value.length_m)||linkDraft.value.length_m<0)return 'Enter a cable length of zero metres or more.'
 if(!Number.isFinite(linkDraft.value.bitrate)||linkDraft.value.bitrate<=0||linkDraft.value.bitrate>100000000000)return 'Enter a bitrate greater than zero and at most 100 Gbps.'
 if(scenario.value.links.some(l=>l.a.node===source&&l.b.node===target||l.a.node===target&&l.b.node===source))return 'These nodes are already connected.'
 for(const id of [source,target]){
  const node=scenario.value.nodes.find(n=>n.id===id)
  const count=scenario.value.links.filter(l=>l.a.node===id||l.b.node===id).length
  if(node?.kind==='device'&&count)return id+' already has its Ethernet connection. End devices support one connection; switches can have multiple.'
  if(count>=64)return id+' already has the maximum 64 connections.'
 }
 return ''
}
function addLink(){
 if(!linkDraft.value.source||!linkDraft.value.target||connectionProblem.value)return
 snapshot();scenario.value.links.push({id:unique('link',scenario.value.links),a:{node:linkDraft.value.source},b:{node:linkDraft.value.target},bitrate:linkDraft.value.bitrate,length_m:linkDraft.value.length_m,delay:linkDraft.value.length_m/200000000})
 normalizeTopology(scenario.value)
}
function removeLink(index:number){snapshot();scenario.value.links.splice(index,1);normalizeTopology(scenario.value)}
function toggleNode(id:string,event:Event){if((event.currentTarget as HTMLDetailsElement).open)openNodes.value.add(id);else openNodes.value.delete(id)}
function toggleFlow(flow:Flow,event:Event){if((event.currentTarget as HTMLDetailsElement).open)openFlows.value.add(flow);else openFlows.value.delete(flow)}
function removeFlow(index:number){snapshot();openFlows.value.delete(scenario.value.flows[index]);scenario.value.flows.splice(index,1)}
function peer(node:string,index:number){const link=scenario.value.links.find(l=>l.a.node===node&&l.a.port===index||l.b.node===node&&l.b.port===index);return link?(link.a.node===node?link.b.node:link.a.node):''}
function resizeQueues(p:Port,count:number){
 if(!Number.isInteger(count)||count<1||count>8)return
 snapshot();while(p.queues.length<count)p.queues.push(queue());p.queues.splice(count)
 p.class_to_queue=p.class_to_queue.map(q=>Math.min(q,count-1))
}
function resizeClasses(p:Port,count:number){
 if(!Number.isInteger(count)||count<1||count>8)return
 snapshot();while(p.class_to_queue.length<count)p.class_to_queue.push(Math.min(p.class_to_queue.length,p.queues.length-1));p.class_to_queue.splice(count)
 p.pcp_map=p.pcp_map.map(c=>Math.min(c,count-1))
}
function addFlow(){snapshot();const f:Flow={id:unique('flow',scenario.value.flows),source:devices.value[0]?.id||'',destination:'',receivers:devices.value.length>1?[devices.value.at(-1)!.id]:[],multicast_mac:null,routes:{},payload:1000,interval:.001,sending_mode:'periodic',burst_size:10,burst_period:.01,clock_drift_ppm:0,start:0,vlan:10,pcp:4,deadline:.01};scenario.value.flows.push(f);flowId.value=f.id}
function pruneRoutes(f:Flow){for(const receiver of Object.keys(f.routes))if(!f.receivers.includes(receiver))delete f.routes[receiver]}
function changeSender(f:Flow){f.receivers=f.receivers.filter(n=>n!==f.source);f.routes={}}
function setRoute(f:Flow,receiver:string,path:string[]|null){if(path)f.routes[receiver]=path;else delete f.routes[receiver]}
function move(event:any){snapshot();const n=scenario.value.nodes.find(n=>n.id===event.node.id);if(n){n.x=event.node.position.x;n.y=event.node.position.y}}
async function action(fn:()=>Promise<void>){busy.value=true;message.value='';try{await fn()}catch(e){message.value=String(e)}finally{busy.value=false}}
async function compile(){result.value=await api('compile',scenario.value);message.value='Validated and generated. '+result.value.diagnostics.length+' warnings.'}
async function refresh(){jobs.value=await api('jobs');if(job.value&&!finished.has(job.value.status)){job.value=await api('jobs/'+job.value.id)}}
async function run(){
 if(!backendReady.value)throw new Error('Restart the Python backend to load Ethernet compiler 3.3 and results engine json-v1 before running simulations.')
 const id=runModelId.value
 const input=id==='draft'?normalizeScenario(scenario.value):normalizeScenario((await api('scenarios/'+id)).scenario)
 const response=await api('jobs',{scenario:input})
 job.value=await api('jobs/'+response.id)
 message.value='Queued simulation “'+input.name+'” · run '+response.id.slice(0,8)
 await refresh();activeTab.value='run';await nextTick();document.getElementById('runs')?.scrollIntoView({behavior:'smooth'})
}
async function refreshModels(){revisions.value=await api('scenarios')}
async function loadAnalysisRun(){
 const request=++analysisRequest,id=analysisRunId.value
 analysisJob.value=null;compared.value=null;compareId.value='';analysisError.value='';analysisLoading.value=!!id
 if(!id)return
 try{
  const selectedRun=await api('jobs/'+id)
  if(request===analysisRequest)analysisJob.value=selectedRun
 }catch(e){if(request===analysisRequest)analysisError.value=String(e)}
 finally{if(request===analysisRequest)analysisLoading.value=false}
}
watch(analysisRunId,()=>loadAnalysisRun())
async function compareRun(){
 const id=compareId.value,base=analysisRunId.value
 compared.value=null
 const comparison=await api('jobs/'+id)
 if(id===compareId.value&&base===analysisRunId.value)compared.value=comparison
}
async function copyCommand(){
 if(!job.value)return
 await navigator.clipboard.writeText(job.value.command)
 message.value='Command copied'
}
async function save(asCopy=false){
 const saved=await api('scenarios'+(!asCopy&&chosenRevision.value?'/'+chosenRevision.value:''),scenario.value,!asCopy&&chosenRevision.value?'PUT':'POST')
 revisions.value=await api('scenarios');chosenRevision.value=saved.id
 localStorage.setItem('tsn-saved-id',saved.id)
 message.value='Saved simulation “'+saved.name+'”.'
}
function newSimulation(){restore({schema_version:4,ned_name:'Scenario',name:'',nodes:[],links:[],flows:[],experiment:{duration:1.1,warmup:0,seed:1}});localStorage.removeItem('tsn-saved-id')}
function loadTemplate(){
 const name=scenario.value.name.trim()
 restore(templates.value[Number(chosenTemplate.value)])
 if(name)scenario.value.name=name
 else scenario.value.name=''
 localStorage.removeItem('tsn-saved-id');message.value='Template loaded. Enter a simulation name and save it.'
}
function openSimulation(){
 const saved=revisions.value.find(r=>r.id===libraryId.value);if(!saved)return
 restore(saved.scenario);chosenRevision.value=saved.id
 localStorage.setItem('tsn-saved-id',saved.id);message.value='Opened “'+saved.name+'”.'
}
async function recoverResults(){await api('jobs/'+job.value.id+'/extract',{});job.value=await api('jobs/'+job.value.id);await refresh()}
async function selectNode(id:string){selected.value=id;openNodes.value.add(id);activeTab.value='build';activeBuildTab.value='topology';await nextTick();document.getElementById('node-'+id)?.scrollIntoView({behavior:'smooth',block:'start'})}
async function showResults(id:string=job.value?.id){
 if(!id)return
 activeTab.value='analysis'
 if(analysisRunId.value===id)await loadAnalysisRun()
 else analysisRunId.value=id
 await nextTick();document.getElementById('analysis')?.scrollIntoView({behavior:'smooth',block:'start'})
}
async function exportBundle(){const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scenario.value)});if(!r.ok)throw new Error(await r.text());download(await r.blob(),'tsn-scenario.zip')}
function download(blob:Blob,name:string){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function jsonExport(){download(new Blob([JSON.stringify(scenario.value,null,2)],{type:'application/json'}),'scenario.json')}
async function importFile(e:Event){const input=e.target as HTMLInputElement;const f=input.files?.[0];if(!f)return;await action(async()=>{const s=JSON.parse(await f.text());const checked=await api('scenarios/validate',s);restore(checked);message.value='Imported simulation configuration'});input.value=''}
function csvExport(){if(!metrics.value||!analysisJob.value)return;const rows=[['flow','generated','received','missing_at_horizon','deadline_misses','min_latency_s','max_latency_s','mean_latency_s','p99_latency_s','mean_absolute_jitter_s','goodput_bps'],...Object.entries(metrics.value.flows).map(([id,m]:[string,any])=>[id,m.generated,m.received,m.missing_at_horizon,m.deadline_misses,m.latency_seconds.min,m.latency_seconds.max,m.latency_seconds.mean,m.latency_seconds.p99,m.mean_absolute_ipdv_seconds,m.goodput_bps])];download(new Blob([rows.map(row=>row.map(v=>JSON.stringify(v??'')).join(',')).join('\n')],{type:'text/csv'}),'metrics-'+analysisJob.value.id.slice(0,8)+'.csv')}
const format=(value:number|null,scale=1)=>value==null?'—':(value*scale).toFixed(3)

watch(scenario,()=>{result.value=null;localStorage.setItem('tsn-draft',JSON.stringify(scenario.value))},{deep:true})
onMounted(()=>action(async()=>{
 try{backendHealth.value=await api('health')}catch{backendHealth.value={results_pipeline:'unknown'}}
 templates.value=await api('templates');capabilities.value=await api('capabilities');revisions.value=await api('scenarios')
 const draft=localStorage.getItem('tsn-draft')
 if(draft){try{scenario.value=normalizeScenario(JSON.parse(draft))}catch{newSimulation()}}else newSimulation()
 chosenRevision.value=localStorage.getItem('tsn-saved-id')||''
 if(!revisions.value.some(r=>r.id===chosenRevision.value))chosenRevision.value=''
 syncGraph();await refresh();timer=setInterval(()=>refresh().catch(()=>{}),1500)
}))
onUnmounted(()=>clearInterval(timer))
</script>

<template>
 <div class="app-shell">
  <aside class="sidebar" aria-label="Research workspace navigation">
   <div class="sidebar-heading"><p class="app-name">TSN research</p><p class="muted">OMNeT++ / INET</p></div>
   <div class="sidebar-navigation">
   <div class="sidebar-tabs main-tabs" role="tablist" aria-label="Workspace sections" aria-orientation="vertical">
    <button v-for="(tab,index) in mainTabs" :key="tab.id" :id="'main-tab-'+tab.id" role="tab" :aria-controls="tab.id" :aria-selected="activeTab===tab.id" :tabindex="activeTab===tab.id?0:-1" @click="activeTab=tab.id" @keydown="tabKey($event,index,'main')">{{tab.label}}</button>
   </div>
   <div v-if="activeTab==='build'" class="build-navigation">
    <div class="sidebar-tabs build-tabs" role="tablist" aria-label="Build simulation sections" aria-orientation="vertical">
     <button v-for="(tab,index) in buildTabs" :key="tab.id" :id="'build-tab-'+tab.id" role="tab" :aria-controls="'build-'+tab.id" :aria-selected="activeBuildTab===tab.id" :tabindex="activeBuildTab===tab.id?0:-1" @click="activeBuildTab=tab.id" @keydown="tabKey($event,index,'build')">{{tab.label}}</button>
    </div>
   </div>
   </div>
   <div v-if="activeTab==='analysis'" class="analysis-navigation">
    <div class="sidebar-tabs" role="tablist" aria-label="Analysis sections" aria-orientation="vertical"><button v-for="(tab,index) in analysisTabs" :key="tab.id" :id="'analysis-tab-'+tab.id" role="tab" :aria-controls="'analysis-'+tab.id" :aria-selected="activeAnalysisTab===tab.id" :tabindex="activeAnalysisTab===tab.id?0:-1" @click="activeAnalysisTab=tab.id" @keydown="tabKey($event,index,'analysis')">{{tab.label}}</button></div>
   </div>
   <div class="sidebar-footer">
    <fieldset class="theme-control"><legend>Appearance</legend><div class="theme-options"><button :aria-pressed="theme==='dark'" @click="theme='dark'">Dark</button><button :aria-pressed="theme==='light'" @click="theme='light'">White</button></div></fieldset>
    <p class="muted">Cmdenv · terminal execution</p>
   </div>
  </aside>

  <main class="workspace">
   <header class="page-header"><div><h1>{{currentTitle}}</h1><p class="current-simulation">{{displayedScenario?.name?.trim()||(activeTab==='analysis'?'Choose a simulation run':'Unsaved simulation')}}</p></div><p v-if="displayedScenario" class="scenario-summary">{{displayedScenario.nodes.length}} nodes · {{displayedScenario.links.length}} links · {{displayedScenario.flows.length}} flows</p></header>
   <p v-if="message" role="status" class="notice">{{message}}</p>
   <p v-if="backendHealth&&!backendReady" role="alert" class="notice">Backend restart required: stop the Python server with Ctrl+C, then restart scripts/serve.sh. This frontend requires Ethernet compiler 3.3 and results engine json-v1. Rebuilding Vue does not reload Python code.</p>

   <div v-if="activeTab==='experiment'" id="experiment" role="tabpanel" aria-labelledby="main-tab-experiment" tabindex="0">
  <section class="panel" aria-labelledby="simulation-heading">
   <h2 id="simulation-heading">Simulation</h2>
   <div class="save-row">
    <label class="simulation-name">Simulation name<input v-model="scenario.name" placeholder="e.g. Automotive CBS baseline" autocomplete="off"></label>
    <button class="primary" :disabled="busy || !scenario.name.trim()" @click="action(()=>save())">Save simulation</button>
    <button :disabled="busy || !chosenRevision || !scenario.name.trim()" @click="action(()=>save(true))">Save as copy</button>
    <button :disabled="busy" @click="newSimulation">New simulation</button>
   </div>
   <p class="muted">{{chosenRevision ? 'Editing a saved simulation. Save updates this simulation; change the name and use Save as copy to create another.' : 'New simulation. Choose a name and save it independently of any template.'}} Draft configurations can be saved before they are ready to run.</p>
   <div class="library-grid">
    <div><label>Start from a template<select v-model="chosenTemplate"><option v-for="(t,i) in templates" :key="i" :value="String(i)">{{t.name}}</option></select></label><button :disabled="!templates.length" @click="loadTemplate">Load template</button></div>
    <div><label>Saved simulations<select v-model="libraryId"><option value="">Choose a simulation</option><option v-for="r in revisions" :key="r.id" :value="r.id">{{r.name}}</option></select></label><button :disabled="!libraryId" @click="openSimulation">Open simulation</button></div>
   </div>
   <div class="actions"><button @click="jsonExport">Export configuration JSON</button><label class="button file-button">Import configuration JSON<input type="file" accept=".json" hidden @change="importFile"></label></div>
  </section>
   <section id="experiment-settings" class="panel">
   <h2>Experiment settings</h2>
   <div class="form-grid"><label>NED network name<input v-model="scenario.ned_name"></label><label>Simulation end (seconds)<input type="number" min="0" step="0.1" v-model.number="scenario.experiment.duration"></label><label>Warm-up (seconds)<input type="number" min="0" step="0.1" v-model.number="scenario.experiment.warmup"></label><label>Random seed<input type="number" min="0" v-model.number="scenario.experiment.seed"></label></div>
   <label class="check"><input type="checkbox" v-model="scenario.experiment.record_link_occupancy">Record per-link packet timelines (additional trace storage)</label>
   <p class="muted">Traffic runs until simulation end. Packets still queued or in transit then are reported as missing at the horizon; only deadlines reached before the end are assessed. Each run retains a snapshot of its configuration.</p>
   <div class="actions"><button :disabled="busy" @click="action(compile)">Validate configuration</button><button @click="activeTab='build'">Build simulation</button><span>{{scenario.nodes.length}} nodes · {{scenario.links.length}} links · {{scenario.flows.length}} flows</span></div>
  </section>
   </div>

   <div v-if="activeTab==='build'" id="build" role="tabpanel" aria-labelledby="main-tab-build">
    <div v-if="activeBuildTab==='topology'" id="build-topology" role="tabpanel" aria-labelledby="build-tab-topology" tabindex="0">
   <section id="topology" class="panel">
   <h2>Topology</h2>
   <div class="actions"><button @click="addNode('device')">+ End device</button><button @click="addNode('switch')">+ Switch</button><button :disabled="!history.length" @click="undo">Undo topology edit</button><button :disabled="!future.length" @click="redo">Redo</button><label>Highlight validated flow<select v-model="flowId"><option value="">Choose flow</option><option v-for="f in scenario.flows" :key="f.id" :value="f.id">{{f.id}}</option></select></label></div>
   <div class="canvas-panel"><VueFlow v-model:nodes="graphNodes" :edges="edges" :nodes-connectable="false" :delete-key-code="null" fit-view-on-init @node-click="({node})=>selectNode(node.id)" @node-drag-stop="move"/><p v-if="!scenario.nodes.length" class="canvas-empty">Add end devices and switches, or load a template in Experiment.</p></div>
   <p class="muted">Drag nodes to arrange them. Click a node to jump to its settings below. Connect nodes below; the system creates and assigns their ports automatically.</p>
  </section>
   <section id="links" class="panel">
   <h2>Ethernet links</h2>
   <div class="form-grid">
    <label>First node<select v-model="linkDraft.source"><option value="">Choose node</option><option v-for="n in scenario.nodes" :key="n.id">{{n.id}}</option></select></label>
    <label>Second node<select v-model="linkDraft.target"><option value="">Choose node</option><option v-for="n in scenario.nodes.filter(n=>n.id!==linkDraft.source)" :key="n.id">{{n.id}}</option></select></label>
    <label>Bitrate (bits/second)<input type="number" min="1" v-model.number="linkDraft.bitrate"></label>
    <label>Cable length (metres)<input type="number" min="0" step="0.1" v-model.number="linkDraft.length_m"></label>
   </div>
   <p class="muted">Ports are allocated automatically, one per connection. Propagation delay = cable length / 200,000,000 m/s (INET's Ethernet cable model).</p>
   <p v-if="connectionProblem" class="notice">{{connectionProblem}}</p>
   <button :disabled="!linkDraft.source||!linkDraft.target||!!connectionProblem" @click="addLink">Connect nodes</button>
   <div class="table-scroll"><table><thead><tr><th>Link</th><th>Connected nodes</th><th>Bitrate (bits/second)</th><th>Cable length (m)</th><th>Calculated delay (ns)</th><th></th></tr></thead><tbody>
    <tr v-for="(l,i) in scenario.links" :key="l.id"><td>{{l.id}}</td><td>{{l.a.node}} ↔ {{l.b.node}}</td><td><input :aria-label="l.id+' bitrate'" type="number" min="1" v-model.number="l.bitrate"></td><td><input :aria-label="l.id+' cable length in metres'" type="number" min="0" step="0.1" v-model.number="l.length_m" @input="l.delay=l.length_m/200000000"></td><td>{{format(l.length_m/200000000,1e9)}}</td><td><button @click="removeLink(i)">Remove</button></td></tr>
   </tbody></table></div>
  </section>
   <section id="nodes" class="panel">
   <h2>Nodes, VLAN admission & egress queues</h2>
   <p class="muted">Click a device or switch to open its configuration. Higher queue indices always have higher priority: queue 7 is highest and queue 0 lowest when using eight queues. PCP selects a traffic class, which selects a physical queue; frames are FIFO within each queue.</p>
   <details v-for="n in scenario.nodes" :id="'node-'+n.id" :key="n.id" class="node-card" :class="{highlighted:selected===n.id}" :open="openNodes.has(n.id)" @toggle="toggleNode(n.id,$event)">
    <summary>{{n.id}} · {{n.kind==='switch'?'TSN switch':'End device'}} <span class="summary-meta">{{n.ports.length}} connected {{n.ports.length===1?'port':'ports'}}</span></summary>
    <div v-if="openNodes.has(n.id)" class="config-body">
    <div class="section-title"><h3>{{n.id}} · {{n.kind==='switch'?'TSN switch':'End device'}}</h3><div class="actions"><button @click="selected=n.id;duplicateNode()">Duplicate node</button><button class="danger" @click="selected=n.id;removeNode()">Delete node</button></div></div>
    <label>Node clock drift (ppm)<input type="number" min="-100000" max="100000" v-model.number="n.clock_drift_ppm"></label><p v-if="n.clock_drift_ppm==null" class="notice">Legacy flows used conflicting clocks. Choose one drift for this node.</p>
    <div v-for="p in n.ports" :key="p.index" class="port-card">
     <h4>Connection to {{peer(n.id,p.index)}}</h4><p class="muted">System-assigned interface: eth{{p.index}}. Highest configured queue: {{p.queues.length-1}}; lowest: 0.</p>
     <label>Permitted ingress / egress VLANs (comma separated)<input :value="p.vlans.join(', ')" @change="p.vlans=($event.target as HTMLInputElement).value.split(',').map(Number)"></label>
     <p class="muted">Tagged VLAN IDs 1–4094. This is admission filtering; CBS shapes the egress queues.</p>
     <div class="form-grid"><label>Number of physical queues<input type="number" min="1" max="8" :value="p.queues.length" @change="resizeQueues(p,Number(($event.target as HTMLInputElement).value))"></label><label>Number of traffic classes<input type="number" min="1" max="8" :value="p.class_to_queue.length" @change="resizeClasses(p,Number(($event.target as HTMLInputElement).value))"></label></div>
     <h4>PCP → traffic class</h4><div class="pcp"><label v-for="(_,i) in p.pcp_map" :key="i">PCP {{i}}<select v-model.number="p.pcp_map[i]"><option v-for="(_,c) in p.class_to_queue" :key="c" :value="c">Class {{c}}</option></select></label></div>
     <h4>Traffic class → physical queue</h4><div class="pcp"><label v-for="(_,c) in p.class_to_queue" :key="c">Class {{c}}<select v-model.number="p.class_to_queue[c]"><option v-for="(_,q) in p.queues" :key="q" :value="q">Queue {{q}}</option></select></label></div>
     <div class="queue-grid"><div v-for="(q,i) in p.queues" :key="i" class="queue-card"><h4>Queue {{i}}</h4><label>Shaper<select v-model="q.shaper"><option value="sp">Strict priority / FIFO</option><option value="cbs">CBS / FIFO</option></select></label><label>Queue buffer capacity (packets)<input type="number" min="1" max="1000000" v-model.number="q.capacity"><span class="field-help">Maximum packets waiting in this queue. New arrivals are dropped when the buffer is full (drop-tail).</span></label><template v-if="q.shaper==='cbs'"><label>idleSlope (bits/second)<input type="number" min="1" v-model.number="q.idle_slope"></label><label class="check"><input type="checkbox" v-model="q.optimize">Expose to future optimizer</label></template></div></div>
     <div class="actions"><button :disabled="p.queues.length>=8" @click="resizeQueues(p,p.queues.length+1)">+ Queue</button><button :disabled="p.queues.length<=1" @click="resizeQueues(p,p.queues.length-1)">Remove last queue</button></div>
    </div>
    <p v-if="!n.ports.length" class="muted">Connect this node to another node to create its Ethernet port automatically.</p>
    </div>
   </details>
   <p v-if="!scenario.nodes.length" class="muted">No nodes yet. Add them using the topology controls.</p>
  </section>
    </div>
    <div v-if="activeBuildTab==='traffic'" id="build-traffic" role="tabpanel" aria-labelledby="build-tab-traffic" tabindex="0">
   <section id="traffic" class="panel">
   <div class="section-title"><h2>Traffic flows</h2><button @click="addFlow">+ Flow</button></div><p class="muted">Raw EthernetSourceApp → EthernetSinkApp. Start offset controls generation; queues determine wire transmission time. Offset and sending periods use the sender node’s shared local clock. Periodic messages and bursts continue until simulation end. Deadlines use simulation time. Burst packets are generated together; Ethernet serializes them on the wire.</p>
   <details v-for="(f,i) in scenario.flows" :key="i" class="flow-card" :open="openFlows.has(f)" @toggle="toggleFlow(f,$event)"><summary>{{f.id||'Unnamed flow'}} <span class="summary-meta">{{f.source||'Choose sender'}} → {{f.receivers.length?f.receivers.join(', '):'Choose receivers'}}</span></summary><div v-if="openFlows.has(f)" class="config-body"><div class="section-title"><label>Flow identifier<input v-model="f.id"></label><button @click="removeFlow(i)">Remove flow</button></div><div class="form-grid"><label>Sender<select v-model="f.source" @change="changeSender(f)"><option value="">Choose device</option><option v-for="n in devices" :key="n.id">{{n.id}}</option></select></label><ReceiverSelect :id="'receivers-'+i" v-model="f.receivers" :options="devices.filter(n=>n.id!==f.source).map(n=>n.id)" @update:model-value="pruneRoutes(f)"/><label>Multicast MAC (optional; automatic for multiple receivers)<input :value="f.multicast_mac||''" placeholder="01:00:00:00:00:01" @change="f.multicast_mac=($event.target as HTMLInputElement).value.trim()||null"></label><label>Payload (bytes)<input type="number" min="1" max="1500" v-model.number="f.payload"></label><label>Sending mode<select v-model="f.sending_mode"><option value="periodic">Periodic message</option><option value="burst">Periodic burst</option><option value="once">One message</option></select></label><label v-if="f.sending_mode==='periodic'">Sending period (seconds)<input type="number" min="0.000000001" step="0.0001" v-model.number="f.interval"></label><template v-if="f.sending_mode==='burst'"><label>Messages per burst<input type="number" min="1" max="100000" v-model.number="f.burst_size"></label><label>Period between bursts (seconds)<input type="number" min="0.000000001" step="0.0001" v-model.number="f.burst_period"></label></template><label>VLAN ID<input type="number" min="1" max="4094" v-model.number="f.vlan"></label><label>PCP priority<input type="number" min="0" max="7" v-model.number="f.pcp"></label><label>Start offset (local seconds)<input type="number" min="0" v-model.number="f.start"></label><label>Deadline (seconds)<input type="number" min="0" step="0.001" v-model.number="f.deadline"></label></div><RouteSelect v-for="receiver in f.receivers" :key="receiver" :id="'route-'+i+'-'+receiver" :scenario="scenario" :flow="f" :receiver="receiver" @change="path=>setRoute(f,receiver,path)"/></div></details>
   <p class="muted">Click a flow to open its settings. Routes default to the shortest path by number of links; the dropdown lists available loop-free routes, shortest first. Physical cycles are allowed. Static forwarding uses loop-free paths; multicast paths must form one source-rooted tree. Only configured receivers get copies.</p>
   <p v-if="!scenario.flows.length" class="muted">Add a flow between two end devices.</p>
  </section>
    </div>
    <div v-if="activeBuildTab==='review'" id="build-review" role="tabpanel" aria-labelledby="build-tab-review" tabindex="0">
   <section id="files" class="panel">
   <div class="section-title"><h2>Review simulation</h2><div class="actions"><button :disabled="busy" @click="action(compile)">Generate / refresh</button><button :disabled="busy" @click="action(exportBundle)">Download bundle ZIP</button></div></div>
   <template v-if="result"><label>Preview file<select v-model="file"><option v-for="(_,name) in result.files" :key="name">{{name}}</option></select></label><ul v-if="result.diagnostics.length" class="diagnostics"><li v-for="(d,i) in result.diagnostics" :key="i">{{d.object}}: {{d.message}}</li></ul><pre>{{result.files[file]}}</pre><h3>Validated flow paths</h3><div v-for="(hops,id) in result.paths" :key="id"><b>{{id}}</b><p v-for="(path,receiver) in hops" :key="receiver">{{receiver}}: {{path.map((h:any)=>`${h.node}:eth${h.port} → class ${h.traffic_class} → queue ${h.queue}`).join(' / ')}}</p></div></template><p v-else class="muted">Generate to validate the configuration and inspect NED, INI and the resolved flow paths.</p>
  </section>
   <details id="features" class="panel"><summary>TSN feature support</summary><div class="feature-grid"><div v-for="(supported,name) in capabilities.features" :key="name"><b>{{String(name).replaceAll('_',' ')}}</b><span :class="supported?'enabled':'muted'">{{supported?'Supported':'Planned · unavailable'}}</span></div></div></details>
    </div>
   </div>

   <div v-if="activeTab==='run'" id="run" role="tabpanel" aria-labelledby="main-tab-run" tabindex="0">
   <section class="panel" aria-labelledby="launch-heading">
    <h2 id="launch-heading">Select simulation to run</h2>
    <label>Simulation<select v-model="runModelId" :disabled="busy"><option value="draft">Current editor · {{scenario.name.trim()||'Unsaved simulation'}}</option><optgroup label="Saved simulations"><option v-for="model in revisions" :key="model.id" :value="model.id">{{model.name}}</option></optgroup></select></label>
    <p class="muted">{{runModelId==='draft'?'Runs the current editor configuration, including unsaved changes.':'Runs the saved version of the selected simulation. Save editor changes first if you want to include them.'}}</p>
    <p v-if="runScenario">{{runScenario.nodes.length}} nodes · {{runScenario.links.length}} links · {{runScenario.flows.length}} flows · Simulation end {{runScenario.experiment.duration}} s · Seed {{runScenario.experiment.seed}}</p>
    <p v-else class="notice">This simulation is no longer available. Refresh the list and choose another.</p>
    <div class="actions"><button class="primary" :disabled="busy||!backendReady||!runScenario" @click="action(run)">Run selected simulation</button><button :disabled="busy" @click="action(refreshModels)">Refresh simulations</button></div>
   </section>
   <section id="runs" class="panel">
   <p v-if="backendReady" class="muted">Ethernet compiler {{backendHealth.compiler_version}} · Results engine: {{backendHealth.results_pipeline}} · backend started {{new Date(backendHealth.started_at*1000).toLocaleString()}}</p>
   <h2>Simulation runs</h2>
   <div class="table-scroll"><table><thead><tr><th>Simulation</th><th>Run</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody><tr v-for="r in jobs" :key="r.id"><td>{{r.name}}</td><td>{{r.id.slice(0,8)}}</td><td>{{r.status}}</td><td>{{new Date(r.created*1000).toLocaleString()}}</td><td><div class="actions"><button @click="action(async()=>{job=await api('jobs/'+r.id)})">Inspect</button><button v-if="r.status==='completed'" @click="showResults(r.id)">Analyse</button></div></td></tr></tbody></table></div>
   <template v-if="job"><h3>{{job.body.scenario.name}} · {{job.status}}</h3><p v-if="job.error" class="notice">{{job.error}}</p><div class="actions"><button v-if="!finished.has(job.status)" @click="action(async()=>{await api('jobs/'+job.id+'/cancel',{});await refresh()})">Cancel run</button><button v-if="job.can_extract" :disabled="busy" @click="action(recoverResults)">Recover results without rerunning</button><button @click="action(copyCommand)">Copy terminal command</button><a :href="'/api/jobs/'+job.id+'/artifacts'">Download run artifacts</a><button v-if="job.metrics" @click="showResults()">Open in Analysis</button></div><ol v-if="job.pipeline" class="processing-stages"><li v-for="stage in ['simulation','export','metrics']" :key="stage"><strong>{{({simulation:'Run Cmdenv',export:'Export native results to JSON',metrics:'Calculate metrics'} as Record<string,string>)[stage]}}</strong>: {{job.pipeline.stages[stage]?.status||'pending'}}</li></ol><h4>Simulation command</h4><pre>{{job.command}}</pre><h4>Cmdenv log</h4><pre class="log">{{job.log||'Waiting for worker…'}}</pre><template v-if="job.export_log"><h4>Result export log</h4><pre>{{job.export_log}}</pre></template><template v-if="job.processing_log"><h4>Metric processing errors</h4><pre>{{job.processing_log}}</pre></template></template>
   <p v-else class="muted">Inspect a run to see its logs. Simulations execute in Cmdenv without opening a window.</p>
  </section>
   </div>

   <div v-if="activeTab==='analysis'" id="analysis" role="tabpanel" aria-labelledby="main-tab-analysis" tabindex="0">
    <section class="panel" aria-labelledby="analysis-selector-heading">
     <h2 id="analysis-selector-heading">Select simulation run</h2>
     <label>Simulation run to analyse<select v-model="analysisRunId"><option value="">Choose a completed run</option><option v-for="r in completedJobs" :key="r.id" :value="r.id">{{r.name}} · {{new Date(r.created*1000).toLocaleString()}} · {{r.id.slice(0,8)}}</option></select></label>
     <p v-if="!completedJobs.length" class="muted">No completed runs with statistics yet. Launch a simulation in Run Simulation.</p>
     <p class="muted">Statistics use the selected run's saved configuration and results.</p>
     <p v-if="analysisLoading" role="status">Loading run statistics…</p>
     <p v-if="analysisError" role="alert" class="notice">{{analysisError}}</p>
     <button v-if="analysisError" :disabled="analysisLoading" @click="loadAnalysisRun">Retry loading statistics</button>
     <div v-if="analysisJob" class="analysis-run-info"><h3>{{analysisJob.body.scenario.name}} · {{analysisJob.id.slice(0,8)}}</h3><p>Created {{new Date(analysisJob.created*1000).toLocaleString()}} · Simulation end {{analysisJob.body.scenario.experiment.duration}} s · Seed {{analysisJob.body.scenario.experiment.seed}} · Status {{analysisJob.status}}</p></div>
    </section>
   <template v-if="metrics">
    <div class="actions"><button @click="csvExport">Export metrics CSV</button><a :href="'/api/jobs/'+analysisJob.id+'/artifacts'">Download full run artifacts</a></div>
    <div v-if="activeAnalysisTab==='latency'" id="analysis-latency" role="tabpanel" aria-labelledby="analysis-tab-latency">
     <LatencyAnalysis :metrics="metrics" :theme="theme"/>
     <details class="panel"><summary>Compare latency with another run</summary><div class="actions"><label>Comparison run<select v-model="compareId"><option value="">Choose a completed run</option><option v-for="r in completedJobs.filter(j=>j.id!==analysisRunId)" :key="r.id" :value="r.id">{{r.name}} · {{r.id.slice(0,8)}}</option></select></label><button :disabled="!compareId||busy" @click="action(compareRun)">Compare</button></div>
      <div v-if="compared?.metrics" class="table-scroll"><table><thead><tr><th>Flow / receiver</th><th>Selected run mean (ms)</th><th>Comparison run mean (ms)</th></tr></thead><tbody><tr v-for="(m,id) in metrics.flows" :key="id"><td>{{id}}</td><td>{{format(m.latency_seconds.mean,1000)}}</td><td>{{format(compared.metrics.flows[id]?.latency_seconds.mean??null,1000)}}</td></tr></tbody></table></div>
     </details>
    </div>
    <div v-if="activeAnalysisTab==='occupancy'" id="analysis-occupancy" role="tabpanel" aria-labelledby="analysis-tab-occupancy"><LinkOccupancyAnalysis :run-id="analysisJob.id" :metrics="metrics" :theme="theme"/></div>
   </template>
   <p v-else-if="!analysisLoading&&!analysisError" class="muted">{{analysisRunId?'Statistics are unavailable for this run. Inspect its logs in Run Simulation.':'Choose a completed simulation run above to view its statistics.'}}</p>
   </div>
  </main>
 </div>
</template>
