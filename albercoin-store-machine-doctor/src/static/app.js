const API={basePath:window.location.pathname.endsWith('/')?window.location.pathname:window.location.pathname+'/',url(path){return this.basePath+path.replace(/^\/+/, '')}};
let pollTimer=null;

function fmtPercent(value){return typeof value==='number'?`${value.toFixed(1)}%`:'-'}
function fmtTemp(value){return typeof value==='number'?`${value.toFixed(1)} ºC`:'-'}
function fmtFreq(value){return typeof value==='number'?`${value.toFixed(0)} MHz`:'-'}
function esc(value){const div=document.createElement('div');div.textContent=value==null?'':String(value);return div.innerHTML}

async function json(path, options={}){
  const res=await fetch(API.url(path),options);
  const text=await res.text();
  const data=text?JSON.parse(text):{};
  if(!res.ok)throw new Error(data.error||`HTTP ${res.status}`);
  return data;
}

function latestSample(state){return state.current_sample || (state.samples && state.samples[state.samples.length-1]) || null}

function renderStatus(state){
  const sample=latestSample(state);
  const samples=state.samples||[];
  const temps=samples.map(s=>s.temperature_cpu).filter(v=>typeof v==='number');
  if(sample && typeof sample.temperature_cpu==='number')temps.push(sample.temperature_cpu);
  const freqs=sample?(sample.per_core||[]).map(c=>c.frequency_mhz).filter(v=>typeof v==='number'):[];
  const avgFreq=freqs.length?freqs.reduce((a,b)=>a+b,0)/freqs.length:null;

  document.getElementById('cpu-model').textContent=`${state.cpu_info?.model||'Unknown CPU'} · ${state.cpu_info?.cores||0} cores`;
  document.getElementById('state').textContent=state.state||'stopped';
  document.getElementById('remaining').textContent=state.state==='running'?`${state.remaining_seconds||0}s`:'-';
  document.getElementById('total-usage').textContent=fmtPercent(sample?.cpu_total_usage);
  document.getElementById('current-temp').textContent=fmtTemp(sample?.temperature_cpu);
  document.getElementById('max-temp').textContent=temps.length?fmtTemp(Math.max(...temps)):'-';
  document.getElementById('avg-freq').textContent=fmtFreq(avgFreq);
  document.getElementById('progress-bar').style.width=`${state.progress_percent||0}%`;

  document.getElementById('start-btn').disabled=state.state==='running';
  document.getElementById('cancel-btn').disabled=state.state!=='running';

  const pill=document.getElementById('result-pill');
  pill.className='pill muted';
  pill.textContent='No test yet';
  if(state.result){
    const cls=state.result==='PASS'?'pass':state.result==='WARNING'?'warning':'fail';
    pill.className=`pill ${cls}`;
    pill.textContent=state.result;
  }else if(state.state==='running'){
    pill.textContent='Running';
  }

  renderCores(sample);
  renderSensors(sample?.temperatures||[]);
  renderFinal(state);
}

function renderCores(sample){
  const body=document.getElementById('core-body');
  const cores=sample?.per_core||[];
  if(!cores.length){body.innerHTML='<tr><td colspan="4">Waiting for samples...</td></tr>';return}
  body.innerHTML=cores.map(core=>{
    const usage=typeof core.usage==='number'?core.usage:0;
    return `<tr><td>CPU ${esc(core.core)}</td><td><div class="bar-cell"><div class="mini-bar"><span style="width:${usage}%"></span></div>${fmtPercent(core.usage)}</div></td><td>${fmtFreq(core.frequency_mhz)}</td><td>${fmtTemp(core.temperature)}</td></tr>`;
  }).join('');
}

function renderSensors(sensors){
  const root=document.getElementById('sensors');
  if(!sensors.length){root.innerHTML='<p class="warn">No temperature sensors exposed by this host. Machine Doctor can still test CPU load, but thermal diagnostics will be incomplete.</p>';return}
  root.innerHTML=sensors.map(sensor=>`<div class="sensor"><div><strong>${esc(sensor.name)}</strong><br><small>${esc(sensor.source)} · ${esc(sensor.id)}</small></div><strong>${fmtTemp(sensor.temperature)}</strong></div>`).join('');
}

function renderFinal(state){
  const box=document.getElementById('final-result');
  if(!state.result){box.textContent=state.state==='running'?'CPU test is running. Keep this page open or return later; the backend keeps collecting samples.':'No report generated yet.';return}
  const warnings=(state.warnings||[]).map(w=>`WARNING: ${w}`).join('\n');
  const errors=(state.errors||[]).map(e=>`ERROR: ${e}`).join('\n');
  box.textContent=[`Result: ${state.result}`,`Report: ${state.report_path||'saved in /data/reports'}`,warnings,errors].filter(Boolean).join('\n');
}

async function refresh(){
  try{
    const state=await json('api/cpu/status');
    renderStatus(state);
    if(state.state==='running' && !pollTimer)pollTimer=setInterval(refresh,1000);
    if(state.state!=='running' && pollTimer){clearInterval(pollTimer);pollTimer=null;}
  }catch(e){document.getElementById('final-result').textContent=e.message;}
}

async function startTest(){
  try{await json('api/cpu/start',{method:'POST'});await refresh();if(!pollTimer)pollTimer=setInterval(refresh,1000);}catch(e){alert(e.message)}
}

async function cancelTest(){
  try{await json('api/cpu/cancel',{method:'POST'});await refresh();}catch(e){alert(e.message)}
}

async function loadLatestReport(){
  try{const data=await json('api/reports/latest');document.getElementById('report').textContent=JSON.stringify(data.report,null,2);}catch(e){document.getElementById('report').textContent='No report is available yet.';}
}

document.getElementById('start-btn').addEventListener('click',startTest);
document.getElementById('cancel-btn').addEventListener('click',cancelTest);
document.getElementById('load-report').addEventListener('click',loadLatestReport);
refresh();
