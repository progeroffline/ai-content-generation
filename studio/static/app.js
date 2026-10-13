const $ = id => document.getElementById(id);
let sourceId = null, sourceBusy = false, requestKey = null, requestBody = null;
const cards = new Map();
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-Studio-Request':'1',...options.headers}});
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try { const body = await response.json(); message = body.error || message; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
function el(tag, cls, text) { const n = document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; }
function modeChanged() {
  const isImage = $('mode').value === 'image';
  $('source-wrap').hidden = $('mode').value !== 'image_video';
  $('duration-wrap').hidden = isImage;
  $('model-name').textContent = isImage ? 'Z-Image-Turbo' : 'Wan 2.2 · 5B';
  $('submit').replaceChildren(document.createTextNode(isImage ? 'Создать изображение' : 'Создать видео'),el('span','','↗'));
  $('size').replaceChildren();
  const sizes = isImage ? [['square','Квадрат · 1024 × 1024'],['landscape','Альбом · 1216 × 832'],['portrait','Портрет · 832 × 1216']] : [['landscape','Альбом · 1280 × 704'],['portrait','Портрет · 704 × 1280']];
  for(const [v,t] of sizes){const o=el('option','',t);o.value=v;$('size').append(o);}
}
$('mode').addEventListener('change',modeChanged);
$('prompt').addEventListener('input',()=>{$('counter').textContent=`${$('prompt').value.length} / 4000`;});
$('example').onclick=()=>{
  $('prompt').value=$('mode').value==='image' ? 'Candid photographic portrait of an adult woman near a large window, natural skin texture, soft overcast daylight, subtle expression, 85mm lens, shallow depth of field, neutral colors.' : 'A person gently turns toward the camera and smiles. Soft natural daylight, subtle hair movement, steady camera, realistic motion, continuous shot.';
  $('prompt').dispatchEvent(new Event('input'));
};
function setSource(id,url){sourceId=id;$('source-preview').src=url;$('source-preview').hidden=false;$('source-status').textContent='Кадр сохранён на сервере';}
$('source').onchange=async()=>{
  const file=$('source').files[0];if(!file)return;
  error('');sourceId=null;sourceBusy=true;$('submit').disabled=true;
  try{if(file.size>20*1024*1024)throw new Error('Максимальный размер — 20 МБ');$('source-status').textContent='Загружаем кадр…';const r=await api('/api/uploads',{method:'POST',headers:{'Content-Type':file.type},body:file});setSource(r.id,r.url);}
  catch(e){error(e.message);$('source-status').textContent='Не удалось загрузить';}
  finally{sourceBusy=false;$('submit').disabled=false;}
};
$('form').onsubmit=async e=>{
  e.preventDefault();if(sourceBusy)return;error('');
  const data={prompt:$('prompt').value,mode:$('mode').value,size:$('size').value,seconds:Number($('seconds').value),seed:$('seed').value===''?null:Number($('seed').value),source_id:sourceId};
  if(data.mode==='image_video'&&!sourceId){error('Загрузите или выберите исходный кадр');return;}
  const body=JSON.stringify(data);
  if(body!==requestBody){requestKey=crypto.randomUUID();requestBody=body;}
  $('submit').disabled=true;
  try{await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':requestKey},body});requestBody=null;requestKey=null;await refresh();}
  catch(e){error(e.message+' Повторная отправка тех же параметров защищена от дублей.');}
  finally{$('submit').disabled=false;}
};
function renderCard(job){
  const card=el('article','card');
  if(job.files.length){
    for(const f of job.files){const media=el(f.mime.startsWith('video/')?'video':'img');media.src='/api/files/'+f.id;if(media.tagName==='VIDEO'){media.controls=true;media.preload='metadata';}else{media.alt=job.params.prompt;media.loading='lazy';}card.append(media);}
  } else {
    const stage=el('div','stage'+(job.status==='failed'?' failed':''));stage.append(el('span','badge',job.status==='failed'?'НЕ ЗАВЕРШЕНО':'В РАБОТЕ'),el('p','',job.stage));
    if(job.status!=='failed'){const progress=el('progress');progress.max=100;if(job.progress!==null)progress.value=job.progress;progress.setAttribute('aria-label','Прогресс текущего этапа');stage.append(progress);}
    if(job.error)stage.append(el('p','',job.error));card.append(stage);
  }
  const b=el('div','card-body');b.append(el('span','badge',job.params.mode==='image'?'ИЗОБРАЖЕНИЕ · Z-IMAGE':'ВИДЕО · WAN 2.2'),el('h3','',job.params.prompt),el('div','card-meta',`${new Date(job.created*1000).toLocaleString('ru-RU')} · ${job.params.width} × ${job.params.height} · Seed ${job.params.seed}`));
  const actions=el('div','card-actions');
  for(const f of job.files){const a=el('a','','Скачать ↓');a.href='/api/files/'+f.id+'?download=1';a.download=f.name;actions.append(a);if(f.mime.startsWith('image/')){const use=el('button','secondary','Оживить ↗');use.onclick=()=>{$('mode').value='image_video';modeChanged();setSource(f.id,'/api/files/'+f.id);$('form').scrollIntoView({behavior:'smooth'});};actions.append(use);}}
  if(job.status==='completed'){const a=el('a','','Workflow');a.href='/api/jobs/'+job.id+'/workflow';actions.append(a);}
  const reuse=el('button','secondary','Повторить параметры');reuse.onclick=()=>{$('mode').value=job.params.mode;modeChanged();$('prompt').value=job.params.prompt;$('seed').value=job.params.seed;$('size').value=job.params.size;$('seconds').value=job.params.seconds;if(job.params.source_id)setSource(job.params.source_id,'/api/files/'+job.params.source_id);$('prompt').dispatchEvent(new Event('input'));$('form').scrollIntoView({behavior:'smooth'});};actions.append(reuse);b.append(actions);card.append(b);return card;
}
async function refresh(){
  try{
    const {jobs}=await api('/api/jobs');$('count').textContent=`${jobs.filter(j=>j.status==='completed').length} готово · ${jobs.filter(j=>['queued','running','submitting'].includes(j.status)).length} в очереди / работе`;
    if(jobs.length){const empty=$('jobs').querySelector('.empty');if(empty)empty.remove();}
    const ids=new Set(jobs.map(j=>j.id));for(const [id,c]of cards)if(!ids.has(id)){c.node.remove();cards.delete(id);}
    jobs.forEach((j,index)=>{const signature=JSON.stringify(j);let c=cards.get(j.id);if(!c||c.signature!==signature){const node=renderCard(j);if(c)c.node.replaceWith(node);c={node,signature};cards.set(j.id,c);}const current=$('jobs').children[index];if(current!==c.node)$('jobs').insertBefore(c.node,current||null);});
  }catch(e){$('count').textContent='Не удалось загрузить историю. Повторяем подключение…';}
}
async function connection(){try{const s=await api('/api/status');$('connection').textContent=s.online?'● GPU подключён':'○ ComfyUI недоступен';$('connection').classList.toggle('online',s.online);}catch{$('connection').textContent='○ Нет связи с сервером';}}
$('refresh').onclick=()=>{refresh();connection();};
function money(value){const n=Number(value);return Number.isFinite(n)?`$${n.toFixed(3)}/ч`:'Цена не указана';}
function ram(value){const n=Number(value);if(!Number.isFinite(n))return '—';return n>1024?`${Math.round(n/1024)} ГБ`:`${Math.round(n)} ГБ`;}
function serverCard(item, offer=false){
  const card=el('article','server-card');
  const head=el('div','server-head');head.append(el('span','badge',offer?'ПРЕДЛОЖЕНИЕ':'ИНСТАНС'),el('span','server-price',money(item.dph_total)));card.append(head);
  card.append(el('h3','',`${item.gpu_name||'GPU'} × ${item.num_gpus||1}`));
  card.append(el('p','server-meta',`VRAM ${ram(item.gpu_ram)} · RAM ${ram(item.cpu_ram)} · диск ${item.disk_space||'—'} ГБ`));
  card.append(el('p','server-meta',`Надёжность ${item.reliability2?Math.round(Number(item.reliability2)*100)+'%':'—'} · ${item.geolocation||'локация не указана'}`));
  const actions=el('div','server-actions');
  if(offer){
    const rent=el('button','primary compact','Арендовать');
    rent.onclick=async()=>{
      card.querySelector('[role="alert"]')?.remove();
      const confirmation=window.prompt(`Будет создан платный сервер ${money(item.dph_total)}. Для подтверждения введите RENT ${item.id}`);
      if(confirmation===null)return;
      if(confirmation!==`RENT ${item.id}`){const notice=el('p','notice','Подтверждение не совпало. Аренда не запущена.');notice.setAttribute('role','alert');card.append(notice);return;}
      rent.disabled=true;
      let unavailable=false;
      try{
        await api('/api/vast/rent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offer_id:item.id,disk:Number($('disk').value),confirm:confirmation})});
        $('vast-message').textContent='Сервер заказан. Обновляем список…';
        await loadInstances();
      }catch(e){
        unavailable=/no_such_ask|not available/i.test(e.message);
        const message=unavailable?'Предложение уже недоступно. Нажмите «Найти предложения» ещё раз и выберите другое.':e.message;
        $('vast-message').textContent=message;
        const notice=el('p','notice',message);notice.setAttribute('role','alert');card.append(notice);
      }finally{rent.disabled=unavailable;}
    };actions.append(rent);
  }else{
    const running=(item.actual_status||item.intended_status)==='running';
    const state=el('button','secondary',running?'Остановить':'Запустить');
    state.onclick=async()=>{state.disabled=true;try{await api(`/api/vast/instances/${item.id}/state`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:running?'stopped':'running'})});await loadInstances();}catch(e){$('vast-message').textContent=e.message;}finally{state.disabled=false;}};
    const destroy=el('button','danger','Удалить');
    destroy.onclick=async()=>{const confirmation=window.prompt(`Это удалит сервер и локальные данные. Введите DESTROY ${item.id}`);if(confirmation!==`DESTROY ${item.id}`)return;destroy.disabled=true;try{await api(`/api/vast/instances/${item.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:confirmation})});await loadInstances();}catch(e){$('vast-message').textContent=e.message;}finally{destroy.disabled=false;}};
    actions.append(state,destroy);card.append(el('p','status-line',`Статус: ${item.actual_status||item.intended_status||'неизвестен'} · ID ${item.id}`));
  }
  card.append(actions);return card;
}
async function loadInstances(){
  const wrap=$('instances');wrap.replaceChildren();
  try{const config=await api('/api/vast/config');if(!config.enabled){$('vast-message').textContent='Управление отключено: задайте VAST_API_KEY в окружении сервера.';$('rent-panel').hidden=true;return;}
    $('rent-panel').hidden=false;const {instances}=await api('/api/vast/instances');$('vast-message').textContent=instances.length?`Активных и сохранённых инстансов: ${instances.length}`:'Серверов пока нет.';instances.forEach(x=>wrap.append(serverCard(x)));
  }catch(e){$('vast-message').textContent=e.message;}
}
$('offer-form').onsubmit=async e=>{e.preventDefault();const names=[...document.querySelectorAll('input[name="gpu"]:checked')].map(x=>x.value);const out=$('offers');out.replaceChildren(el('p','muted','Ищем подходящие серверы…'));try{const {offers}=await api('/api/vast/offers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gpu_names:names,max_price:Number($('max-price').value),disk:Number($('disk').value)})});out.replaceChildren();offers.forEach(x=>out.append(serverCard(x,true)));if(!offers.length)out.append(el('p','muted','Подходящих предложений сейчас нет.'));}catch(err){out.replaceChildren(el('p','notice',err.message));}};
$('vast-refresh').onclick=loadInstances;
async function poll(){await refresh();setTimeout(poll,2000);}async function pollStatus(){await connection();setTimeout(pollStatus,10000);}poll();pollStatus();loadInstances();
