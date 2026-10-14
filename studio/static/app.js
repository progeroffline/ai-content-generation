const $ = id => document.getElementById(id);
const watching = new Map();
const STEPS = ['Образ', 'Настройка', 'Сборка', 'Движок', 'Готово'];

async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-Studio-Request':'1','Content-Type':'application/json',...options.headers}});
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try { const body = await response.json(); message = body.error || message; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function el(tag, cls='', text='') { const e=document.createElement(tag); if(cls)e.className=cls; if(text!=='')e.textContent=text; return e; }
function price(value){const n=Number(value);const p=el('span','price',Number.isFinite(n)?`$${n.toFixed(2)}`:'—');p.append(el('small','',' /час'));return p;}
function gb(mb){const n=Number(mb);return Number.isFinite(n)&&n>0?`${Math.round(n/1024)} ГБ`:'—';}
function say(text, error=false){const m=$('message');m.textContent=text;m.hidden=!text;m.classList.toggle('error',error);}
function friendly(message){return /no_such_ask|not available|no longer available/i.test(message)?'Этот сервер уже заняли — выберите другой.':message;}

// Position of a backend stage on the step bar; -1 when the server is not starting up.
function stepOf(p){
  if(p.ready)return 4;
  const s=p.stage||'';
  if(s.startsWith('Загрузка образа'))return 0;
  if(s.startsWith('Контейнер')||s.startsWith('Автонастройка'))return 1;
  if(s.startsWith('Сборка'))return 2;
  if(s.startsWith('SwarmUI')||s.startsWith('Движок'))return 3;
  return -1;
}
function renderSteps(list, current){
  list.hidden=current<0;
  [...list.children].forEach((li,i)=>{li.className=i<current?'done':i===current?(current===4?'done':'current'):'';});
}

function instanceCard(item){
  const card=el('article','card');
  const top=el('div','server-top');const title=el('div');
  title.append(el('h3','',item.gpu_name||'GPU'),el('div','server-sub',`${item.geolocation||'Локация неизвестна'} · сервер ${item.id}`));
  top.append(title,price(item.dph_total));
  const status=el('p','status');status.setAttribute('aria-live','polite');const dot=el('span','status-dot');const text=el('span','','Проверяем состояние…');status.append(dot,text);
  const steps=el('ol','steps');STEPS.forEach(s=>steps.append(el('li','',s)));steps.hidden=true;steps.setAttribute('aria-hidden','true');
  const actions=el('div','actions');
  const link=el('a','btn btn-primary','Открыть ComfyUI ↗');link.target='_blank';link.rel='noopener noreferrer';link.hidden=true;
  const stopping=item.intended_status==='stopped';
  const toggle=el('button','btn',stopping?'Запустить':'Остановить');toggle.type='button';
  toggle.onclick=()=>act(toggle,`/api/vast/instances/${item.id}/state`,{method:'POST',body:JSON.stringify({state:stopping?'running':'stopped'})},stopping?'Запускаем…':'Останавливаем…');
  const destroy=el('button','btn btn-danger','Удалить');destroy.type='button';
  destroy.onclick=()=>{if(confirm(`Удалить сервер ${item.id}? Модели на нём пропадут, оплата прекратится.`))act(destroy,`/api/vast/instances/${item.id}`,{method:'DELETE'},'Удаляем…');};
  actions.append(link,el('span','spacer'),toggle,destroy);
  const box=el('details','log-box');const summary=el('summary');const updated=el('span','', '');summary.append(el('span','','Лог запуска'),updated);
  const log=el('pre','log','Загружаем лог…');log.tabIndex=0;box.append(summary,log);
  card.append(top,status,steps,actions,box);
  watch(item.id,{status,text,steps,link,log,box,updated});
  return card;
}
async function act(button,path,options,busy){
  const label=button.textContent;button.disabled=true;button.textContent=busy;
  try{await api(path,options);await loadInstances();}
  catch(e){say(friendly(e.message),true);button.disabled=false;button.textContent=label;}
}
function watch(id,view){
  const entry={timer:null};watching.set(id,entry);
  let opened=false;
  const tick=async()=>{
    if(watching.get(id)!==entry)return;
    let delay=5000;
    try{
      const p=await api(`/api/vast/instances/${id}/progress`);
      const stopped=p.status==='exited'||p.stage==='Остановлен';
      view.status.dataset.tone=p.ready?'ready':stopped?'stopped':'progress';
      view.text.textContent=p.stage;
      renderSteps(view.steps,stopped?-1:stepOf(p));
      view.link.hidden=!p.swarm_url;if(p.swarm_url)view.link.href=p.swarm_url;
      const atBottom=view.log.scrollTop+view.log.clientHeight>=view.log.scrollHeight-24;
      view.log.textContent=p.logs.length?p.logs.join('\n'):(p.status_msg||'Лог пока пуст — контейнер ещё не запустился.');
      if(atBottom)view.log.scrollTop=view.log.scrollHeight;
      view.updated.textContent=`обновлено ${new Date().toLocaleTimeString('ru-RU')}`;
      if(!p.ready&&!stopped&&!opened){view.box.open=true;opened=true;}
      if(p.ready||stopped)delay=30000;
    }catch(e){view.status.dataset.tone='error';view.text.textContent=e.message;delay=10000;}
    entry.timer=setTimeout(tick,delay);
  };
  tick();
}
function stopWatching(){for(const entry of watching.values())clearTimeout(entry.timer);watching.clear();}
async function loadInstances(){
  try{
    const config=await api('/api/vast/config');
    if(!config.enabled){say('Задайте VAST_API_KEY в .env на сервере панели.',true);return;}
    const {instances}=await api('/api/vast/instances');
    stopWatching();
    $('instances').replaceChildren(...(instances.length?instances.map(instanceCard):[el('p','empty','Серверов нет. Выберите новый ниже.')]));
    say('');
  }catch(e){say(e.message,true);}
}

function offerCard(item){
  const card=el('article','card offer');
  const head=el('div','server-top');head.append(el('h3','',item.gpu_name||'GPU'),price(item.dph_total));
  const meta=el('div','meta',`${gb(item.gpu_ram)} видеопамяти · ${gb(item.cpu_ram)} RAM · ${item.geolocation||'локация неизвестна'}`);
  const meta2=el('div','meta',`Интернет ↓ ${Math.round(Number(item.inet_down||0))} / ↑ ${Math.round(Number(item.inet_up||0))} Мбит/с · надёжность ${Math.round(Number(item.reliability2||0)*100)}%`);
  const meta3=el('div','meta',`Трафик ↓ $${Number(item.inet_down_cost||0).toFixed(4)}/ГБ · ↑ $${Number(item.inet_up_cost||0).toFixed(4)}/ГБ`);
  const rent=el('button','btn btn-primary','Арендовать');rent.type='button';
  rent.onclick=async()=>{
    rent.disabled=true;rent.textContent='Арендуем…';card.querySelector('.notice')?.remove();
    try{
      await api('/api/vast/rent',{method:'POST',body:JSON.stringify({offer_id:item.id,disk:Number($('disk').value)})});
      $('offers').replaceChildren();await loadInstances();window.scrollTo({top:0,behavior:'smooth'});
    }catch(e){
      const text=friendly(e.message),gone=text!==e.message;
      rent.textContent=gone?'Занят':'Арендовать';rent.disabled=gone;card.append(el('p','notice error',text));
    }
  };
  card.append(head,meta,meta2,meta3,rent);
  return card;
}
$('offer-form').onsubmit=async e=>{
  e.preventDefault();
  const names=[...document.querySelectorAll('input[name="gpu"]:checked')].map(x=>x.value);
  const out=$('offers');
  if(!names.length){out.replaceChildren(el('p','notice error','Выберите хотя бы одну видеокарту.'));return;}
  const button=$('search');button.disabled=true;button.textContent='Ищем…';
  out.replaceChildren(el('p','notice','Ищем свободные серверы…'));
  try{
    const {offers}=await api('/api/vast/offers',{method:'POST',body:JSON.stringify({gpu_names:names,max_price:Number($('max-price').value),disk:Number($('disk').value)})});
    offers.sort((a,b)=>a.dph_total-b.dph_total);
    out.replaceChildren(...(offers.length?offers.map(offerCard):[el('p','empty','Свободных серверов нет — поднимите цену или добавьте видеокарты.')]));
  }catch(err){out.replaceChildren(el('p','notice error',err.message));}
  finally{button.disabled=false;button.textContent='Найти серверы';}
};
$('refresh').onclick=loadInstances;
loadInstances();
