/** Pinned ECharts runtime: prefer a local research-workstation asset, then CDN. */
export type ChartOption = Record<string, any>
export interface ChartInstance {
 setOption(option:ChartOption,options?:{notMerge?:boolean}):void
 resize():void
 dispose():void
 getDataURL(options:{type:'svg';backgroundColor?:string}):string
}
export interface EChartsRuntime { init(element:HTMLElement,theme?:string,options?:{renderer:'svg'}):ChartInstance }
declare global { interface Window { echarts?:EChartsRuntime } }
let pending:Promise<EChartsRuntime>|undefined
export function loadECharts():Promise<EChartsRuntime>{
 if(window.echarts)return Promise.resolve(window.echarts)
 if(pending)return pending
 const script=(src:string)=>new Promise<EChartsRuntime>((resolve,reject)=>{
  const element=document.createElement('script')
  const timer=setTimeout(()=>{element.remove();reject(new Error('ECharts download timed out'))},12000)
  element.src=src;element.async=true
  element.onload=()=>{clearTimeout(timer);if(window.echarts)resolve(window.echarts);else reject(new Error('ECharts did not initialize'))}
  element.onerror=()=>{clearTimeout(timer);element.remove();reject(new Error('Could not load ECharts'))}
  document.head.appendChild(element)
 })
 pending=script('/vendor/echarts.min.js').catch(()=>script('https://cdn.jsdelivr.net/npm/echarts@6.0.0/dist/echarts.min.js')).catch(error=>{pending=undefined;throw error})
 return pending
}
