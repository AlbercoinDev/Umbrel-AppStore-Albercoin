const API={basePath:window.location.pathname.endsWith('/')?window.location.pathname:window.location.pathname+'/',url(path){return this.basePath+path.replace(/^\/+/, '')}};
let pollTimer=null;
const TEXT={
  en:{eyebrow:'Umbrel hardware diagnostics',subtitle:'CPU stress testing, live sensor readings and stability reports for your Umbrel machine.',noTestYet:'No test yet',cpuCheck:'CPU Check',detectingCpu:'Detecting CPU...',startTest:'Start CPU test',starting:'Starting...',cancel:'Cancel',status:'Status',timeRemaining:'Time remaining',totalCpu:'Total CPU',currentTemp:'Current temp',maxTemp:'Max temp',avgFrequency:'Avg frequency',finalResult:'Final Result',noReport:'No report generated yet.',perCoreView:'Per-Core Live View',perCoreHelp:'If per-core temperature is not exposed by the host, available sensors are listed below.',core:'Core',usage:'Usage',frequency:'Frequency',temperature:'Temperature',waitingSamples:'Waiting for samples...',availableSensors:'Available Sensors',noSensorData:'No sensor data loaded yet.',lastReport:'Last Report',viewLatestReport:'View latest report',notExposed:'Not exposed',cpuSensor:'CPU sensor',coreSensor:'core sensor',unknownCpu:'Unknown CPU',cores:'cores',running:'Running',cpuRunning:'CPU test is running. Keep this page open or return later; the backend keeps collecting samples.',noTempSensors:'No temperature sensors exposed by this host. Machine Doctor can still test CPU load, but thermal diagnostics will be incomplete.',result:'Result',report:'Report',savedReports:'saved in /data/reports',warning:'WARNING',error:'ERROR',noReportAvailable:'No report is available yet.',expectedJson:'Expected JSON from',got:'got'},
  es:{eyebrow:'Diagnóstico hardware para Umbrel',subtitle:'Pruebas de estrés CPU, sensores en directo e informes de estabilidad para tu máquina Umbrel.',noTestYet:'Sin prueba todavía',cpuCheck:'Chequeo CPU',detectingCpu:'Detectando CPU...',startTest:'Iniciar test CPU',starting:'Iniciando...',cancel:'Cancelar',status:'Estado',timeRemaining:'Tiempo restante',totalCpu:'CPU total',currentTemp:'Temp. actual',maxTemp:'Temp. máxima',avgFrequency:'Frecuencia media',finalResult:'Resultado final',noReport:'Aún no se ha generado ningún informe.',perCoreView:'Vista en directo por núcleo',perCoreHelp:'Si el host no expone temperatura por núcleo, los sensores disponibles aparecen debajo.',core:'Núcleo',usage:'Uso',frequency:'Frecuencia',temperature:'Temperatura',waitingSamples:'Esperando muestras...',availableSensors:'Sensores disponibles',noSensorData:'Aún no se han cargado datos de sensores.',lastReport:'Último informe',viewLatestReport:'Ver último informe',notExposed:'No expuesto',cpuSensor:'sensor CPU',coreSensor:'sensor de núcleo',unknownCpu:'CPU desconocida',cores:'núcleos',running:'Ejecutando',cpuRunning:'El test CPU se está ejecutando. Puedes dejar esta página abierta o volver después; el backend sigue recogiendo muestras.',noTempSensors:'Este host no expone sensores de temperatura al contenedor. Machine Doctor puede comprobar la carga CPU, pero el diagnóstico térmico será incompleto.',result:'Resultado',report:'Informe',savedReports:'guardado en /data/reports',warning:'AVISO',error:'ERROR',noReportAvailable:'Todavía no hay ningún informe disponible.',expectedJson:'Se esperaba JSON de',got:'recibido',stopped:'parado',finished:'finalizado'}
};
let lang=(localStorage.getItem('machine-doctor-lang')||((navigator.language||'').toLowerCase().startsWith('es')?'es':'en'));

function t(key){return (TEXT[lang]&&TEXT[lang][key])||TEXT.en[key]||key}
function setLang(nextLang){lang=nextLang==='es'?'es':'en';localStorage.setItem('machine-doctor-lang',lang);applyTranslations();refresh()}
function applyTranslations(){document.documentElement.lang=lang;document.querySelectorAll('[data-i18n]').forEach(el=>{el.textContent=t(el.dataset.i18n)});document.getElementById('en-btn').classList.toggle('active',lang==='en');document.getElementById('es-btn').classList.toggle('active',lang==='es')}
function fmtState(state){return lang==='es'&&TEXT.es[state]?TEXT.es[state]:state}

function fmtPercent(value){return typeof value==='number'?`${value.toFixed(1)}%`:'-'}
function fmtTemp(value){return typeof value==='number'?`${value.toFixed(1)} ºC`:'-'}
function fmtFreq(value){return typeof value==='number'?`${value.toFixed(0)} MHz`:'-'}
function esc(value){const div=document.createElement('div');div.textContent=value==null?'':String(value);return div.innerHTML}
function fmtCoreTemp(core){
  if(typeof core.temperature!=='number')return t('notExposed');
  const suffix=core.temperature_source==='cpu_global'?` <small>(${t('cpuSensor')})</small>`:core.temperature_source==='physical_core'?` <small>(${t('coreSensor')})</small>`:'';
  return `${core.temperature.toFixed(1)} ºC${suffix}`;
}

async function json(path, options={}){
  const res=await fetch(API.url(path),options);
  const text=await res.text();
  let data={};
  try{data=text?JSON.parse(text):{};}catch(e){throw new Error(`${t('expectedJson')} ${path}, ${t('got')}: ${text.slice(0,120)}`)}
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

  document.getElementById('cpu-model').textContent=`${state.cpu_info?.model||t('unknownCpu')} · ${state.cpu_info?.cores||0} ${t('cores')}`;
  document.getElementById('state').textContent=fmtState(state.state||'stopped');
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
  pill.textContent=t('noTestYet');
  if(state.result){
    const cls=state.result==='PASS'?'pass':state.result==='WARNING'?'warning':'fail';
    pill.className=`pill ${cls}`;
    pill.textContent=state.result;
  }else if(state.state==='running'){
    pill.textContent=t('running');
  }

  renderCores(sample);
  renderSensors(sample?.temperatures||[]);
  renderFinal(state);
}

function renderCores(sample){
  const body=document.getElementById('core-body');
  const cores=sample?.per_core||[];
  if(!cores.length){body.innerHTML=`<tr><td colspan="4">${t('waitingSamples')}</td></tr>`;return}
  body.innerHTML=cores.map(core=>{
    const usage=typeof core.usage==='number'?core.usage:0;
    return `<tr><td>CPU ${esc(core.core)}</td><td><div class="bar-cell"><div class="mini-bar"><span style="width:${usage}%"></span></div>${fmtPercent(core.usage)}</div></td><td>${fmtFreq(core.frequency_mhz)}</td><td>${fmtCoreTemp(core)}</td></tr>`;
  }).join('');
}

function renderSensors(sensors){
  const root=document.getElementById('sensors');
  if(!sensors.length){root.innerHTML=`<p class="warn">${t('noTempSensors')}</p>`;return}
  root.innerHTML=sensors.map(sensor=>`<div class="sensor"><div><strong>${esc(sensor.name)}</strong><br><small>${esc(sensor.source)} · ${esc(sensor.id)}</small></div><strong>${fmtTemp(sensor.temperature)}</strong></div>`).join('');
}

function renderFinal(state){
  const box=document.getElementById('final-result');
  if(!state.result){box.textContent=state.state==='running'?t('cpuRunning'):t('noReport');return}
  const warnings=(state.warnings||[]).map(w=>`${t('warning')}: ${w}`).join('\n');
  const errors=(state.errors||[]).map(e=>`${t('error')}: ${e}`).join('\n');
  box.textContent=[`${t('result')}: ${state.result}`,`${t('report')}: ${state.report_path||t('savedReports')}`,warnings,errors].filter(Boolean).join('\n');
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
  const button=document.getElementById('start-btn');
  button.disabled=true;
  button.textContent=t('starting');
  try{await json('api/cpu/start',{method:'POST'});await refresh();if(!pollTimer)pollTimer=setInterval(refresh,1000);}catch(e){document.getElementById('final-result').textContent=e.message;alert(e.message)}finally{button.textContent=t('startTest');refresh()}
}

async function cancelTest(){
  try{await json('api/cpu/cancel',{method:'POST'});await refresh();}catch(e){alert(e.message)}
}

async function loadLatestReport(){
  try{const data=await json('api/reports/latest');document.getElementById('report').textContent=JSON.stringify(data.report,null,2);}catch(e){document.getElementById('report').textContent=t('noReportAvailable');}
}

document.getElementById('en-btn').addEventListener('click',()=>setLang('en'));
document.getElementById('es-btn').addEventListener('click',()=>setLang('es'));
document.getElementById('start-btn').addEventListener('click',startTest);
document.getElementById('cancel-btn').addEventListener('click',cancelTest);
document.getElementById('load-report').addEventListener('click',loadLatestReport);
applyTranslations();
refresh();
