const $ = id => document.getElementById(id);
const watching = new Map();
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-Studio-Request':'1','Content-Type':'application/json',...options.headers}});
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try { const body = await response.json(); message = body.error || message; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function el(tag, cls='', text='') { const e=document.createElement(tag); if(cls)e.className=cls; if(text)e.textContent=text; return e; }
function money(value){const n=Number(value);return Number.isFinite(n)?`$${n.toFixed(3)}/ч`:'—';}
function gb(value){const n=Number(value);return Number.isFinite(n)?`${Math.round(n>1024?n/1024:n)} ГБ`:'—';}

function instanceCard(item){
  const card=el('article','server-card instance');
  const head=el('div','server-head');head.append(el('span','badge',`СЕРВЕР ${item.id}`),el('span','server-price',money(item.dph_total)));
  card.append(head,el('h3','',`${item.gpu_name||'GPU'} · ${item.geolocation||''}`));
  const stage=el('p','status-line',`Статус: ${item.actual_status||item.intended_status||'создаётся'}`);
  const link=el('a','primary compact','Открыть SwarmUI ↗');link.target='_blank';link.rel='noopener noreferrer';link.hidden=true;
  const actions=el('div','server-actions');
  const running=(item.actual_status||item.intended_status)==='running';
  const state=el('button','secondary',running?'Остановить':'Запустить');
  state.onclick=()=>act(state,`/api/vast/instances/${item.id}/state`,{method:'POST',body:JSON.stringify({state:running?'stopped':'running'})});
  const destroy=el('button','danger','Удалить');
  destroy.onclick=()=>{if(confirm(`Удалить сервер ${item.id}? Модели на нём пропадут.`))act(destroy,`/api/vast/instances/${item.id}`,{method:'DELETE'});};
  actions.append(link,state,destroy);
  const logs=el('pre','log');logs.textContent='Загружаем лог…';
  const details=el('details','log-wrap');const summary=el('summary','','Лог запуска');details.append(summary,logs);
  card.append(stage,actions,details);
  watch(item.id,{stage,link,logs,details});
  return card;
}
async function act(button,path,options){
  button.disabled=true;
  try{await api(path,options);await loadInstances();}catch(e){$('vast-message').textContent=e.message;button.disabled=false;}
}
function watch(id,view){
  const previous=watching.get(id);if(previous)clearTimeout(previous.timer);
  const entry={view,timer:null};watching.set(id,entry);
  const tick=async()=>{
    if(watching.get(id)!==entry)return;
    try{
      const p=await api(`/api/vast/instances/${id}/progress`);
      view.stage.textContent=`${p.ready?'●':'○'} ${p.stage}`;view.stage.classList.toggle('ready',p.ready);
      view.link.hidden=!p.swarm_url;if(p.swarm_url)view.link.href=p.swarm_url;
      const atBottom=view.logs.scrollTop+view.logs.clientHeight>=view.logs.scrollHeight-20;
      view.logs.textContent=(p.status_msg&&!p.logs.length?p.status_msg+'\n':'')+(p.logs.join('\n')||'Лог пока пуст — контейнер ещё не запустился.');
      if(atBottom)view.logs.scrollTop=view.logs.scrollHeight;
      if(!p.ready&&p.status!=='exited')view.details.open=true;
      entry.timer=setTimeout(tick,p.ready||p.status==='exited'?30000:5000);
    }catch(e){view.stage.textContent=`○ ${e.message}`;entry.timer=setTimeout(tick,10000);}
  };
  tick();
}
async function loadInstances(){
  const wrap=$('instances');
  try{
    const config=await api('/api/vast/config');
    if(!config.enabled){$('vast-message').textContent='Задайте VAST_API_KEY в окружении сервера.';return;}
    const {instances}=await api('/api/vast/instances');
    for(const [, entry] of watching)clearTimeout(entry.timer);watching.clear();
    wrap.replaceChildren(...instances.map(instanceCard));
    $('vast-message').textContent=instances.length?`Серверов: ${instances.length}`:'Серверов нет — выберите новый ниже.';
  }catch(e){$('vast-message').textContent=e.message;}
}
function offerCard(item){
  const card=el('article','server-card');
  const head=el('div','server-head');head.append(el('span','badge','ПРЕДЛОЖЕНИЕ'),el('span','server-price',money(item.dph_total)));
  card.append(head,el('h3','',item.gpu_name||'GPU'));
  card.append(el('p','server-meta',`VRAM ${gb(item.gpu_ram)} · RAM ${gb(item.cpu_ram)} · ${item.geolocation||'локация не указана'}`));
  card.append(el('p','server-meta',`Трафик $${Number(item.inet_down_cost||0).toFixed(4)}/ГБ · надёжность ${Math.round(Number(item.reliability2||0)*100)}%`));
  const rent=el('button','primary compact','Арендовать');
  rent.onclick=async()=>{
    rent.disabled=true;rent.textContent='Арендуем…';
    try{await api('/api/vast/rent',{method:'POST',body:JSON.stringify({offer_id:item.id,disk:Number($('disk').value)})});$('offers').replaceChildren();await loadInstances();window.scrollTo({top:0,behavior:'smooth'});}
    catch(e){const gone=/no_such_ask|not available|no longer available/i.test(e.message);rent.textContent=gone?'Уже занят':'Арендовать';rent.disabled=gone;card.append(el('p','notice',gone?'Этот сервер уже заняли — выберите другой.':e.message));}
  };
  const actions=el('div','server-actions');actions.append(rent);card.append(actions);return card;
}
$('offer-form').onsubmit=async e=>{
  e.preventDefault();
  const out=$('offers');out.replaceChildren(el('p','muted','Ищем серверы…'));
  try{
    const names=[...document.querySelectorAll('input[name="gpu"]:checked')].map(x=>x.value);
    const {offers}=await api('/api/vast/offers',{method:'POST',body:JSON.stringify({gpu_names:names,max_price:Number($('max-price').value),disk:Number($('disk').value)})});
    out.replaceChildren(...(offers.length?offers.map(offerCard):[el('p','muted','Подходящих серверов нет — поднимите цену или выберите другие GPU.')]));
  }catch(err){out.replaceChildren(el('p','notice',err.message));}
};
$('vast-refresh').onclick=loadInstances;
loadInstances();
