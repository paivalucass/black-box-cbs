// Optional local copy for offline work; all ECharts source/license headers are kept.
import { mkdir, writeFile } from 'node:fs/promises'
const url='https://cdn.jsdelivr.net/npm/echarts@6.0.0/dist/echarts.min.js'
const response=await fetch(url)
if(!response.ok)throw new Error(`ECharts download failed: ${response.status}`)
const source=await response.text()
if(!source.includes('echarts')||source.length<100000)throw new Error('Unexpected ECharts distribution')
await mkdir(new URL('../public/vendor/',import.meta.url),{recursive:true})
await writeFile(new URL('../public/vendor/echarts.min.js',import.meta.url),source)
console.log('Installed ECharts 6.0.0 locally. Run npm run build to include it in the offline frontend.')
