'use strict';
const $ = id => document.getElementById(id);
const state = {user:null, equipment:[], mine:[], occupancy:new Map(), category:'Все', date:'', today:'', csrf:document.querySelector('meta[name="csrf-token"]').content, hours:{start:'09:00',end:'18:00'}, selected:null, detailReady:false, catalogEpoch:0, detailEpoch:0, submitting:false};
const roleNames = {student:'Студент',teacher:'Преподаватель',admin:'Администратор'};
const numberDate = date => new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long'}).format(new Date(date+'T12:00:00'));
const shortMonth = date => new Intl.DateTimeFormat('ru-RU',{month:'short'}).format(new Date(date+'T12:00:00')).replace('.','');
const mins = time => Number(time.slice(0,2))*60+Number(time.slice(3));
const timeOf = value => `${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;
const escape = value => String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function art(item){const words=`${item.category} ${item.name}`.toLowerCase();if(/камер|фото/.test(words))return 'camera';if(/проектор/.test(words))return 'projector';if(/микроскоп/.test(words))return 'microscope';if(/аудио|микрофон|звук/.test(words))return 'audio';return 'equipment';}
function showError(id,message){$(id).textContent=message||'';$(id).hidden=!message;}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf,...options.headers}});
  let data;try{data=await response.json();}catch{throw new Error('Сервер недоступен. Попробуйте обновить страницу.');}
  if(data.csrf_token)state.csrf=data.csrf_token;
  if(!response.ok){
    if(response.status===401 && state.user){state.user=null;showLogin();}
    const error=new Error(data.error?.message||'Не удалось выполнить запрос');error.code=data.error?.code;throw error;
  }
  return data;
}
function online(ok){$('connection').classList.toggle('offline',!ok);$('connection').replaceChildren();const dot=document.createElement('span');dot.className='dot';$('connection').append(dot,document.createTextNode(ok?'Данные актуальны':'Нет связи с сервером'));}
function showLogin(){
  $('boot').hidden=true;$('app-view').hidden=true;$('login-view').hidden=false;
  if($('booking-dialog').open)$('booking-dialog').close();
  if($('equipment-dialog').open)$('equipment-dialog').close();
  authTab('login');$('register-form').reset();$('add-equipment').hidden=true;$('toast').hidden=true;
  state.catalogEpoch++;state.detailEpoch++;state.equipment=[];state.mine=[];state.occupancy.clear();$('password').value='';
}
async function showApp(){
  $('boot').hidden=true;$('login-view').hidden=true;$('app-view').hidden=false;
  $('department-label').textContent=state.department;$('user-name').textContent=state.user.name;$('user-role').textContent=roleNames[state.user.role];
  $('avatar').textContent=state.user.name.split(' ').slice(0,2).map(n=>n[0]).join('');
  $('add-equipment').hidden=state.user.role!=='admin';
  $('today-label').textContent=numberDate(state.today);$('catalog-date').value=state.date;
  $('detail-date').min=state.today;
  $('hours-label').textContent=`Рабочие часы ${state.hours.start}–${state.hours.end}`;$('zone-label').textContent=state.timezone;
  $('detail-hours').textContent=`${state.hours.start}–${state.hours.end} · ${state.timezone}`;
  for(const id of ['start-time','end-time']){$(id).min=state.hours.start;$(id).max=state.hours.end;}
  navigate('catalog');await refresh(true);
}
async function initialize(){
  try{const data=await api('/api/session');Object.assign(state,{user:data.user,hours:data.work_hours,timezone:data.timezone,department:data.department,today:data.today,date:data.today});if(state.user)await showApp();else showLogin();}
  catch(error){showLogin();showError('login-error',error.message);}
}
function navigate(page){
  $('catalog-view').hidden=page!=='catalog';$('mine-view').hidden=page!=='mine';
  for(const id of ['catalog','mine']){const active=page===id;$('nav-'+id).classList.toggle('active',active);if(active)$('nav-'+id).setAttribute('aria-current','page');else $('nav-'+id).removeAttribute('aria-current');}
  $('breadcrumb').innerHTML=`Лаборатория <span>/</span> ${page==='catalog'?'Каталог':'Мои брони'}`;
}
function renderCategories(){
  const categories=['Все',...new Set(state.equipment.map(item=>item.category))];
  if(!categories.includes(state.category))state.category='Все';
  $('categories').replaceChildren(...categories.map(category=>{const button=document.createElement('button');button.className='category-chip'+(category===state.category?' active':'');button.textContent=category;button.setAttribute('aria-pressed',category===state.category);button.onclick=()=>{state.category=category;renderCategories();renderCatalog();};return button;}));
}
function renderCatalog(){
  const query=$('search').value.trim().toLowerCase();
  const items=state.equipment.filter(item=>(state.category==='Все'||item.category===state.category)&&`${item.name} ${item.description} ${item.location}`.toLowerCase().includes(query));
  $('catalog-count').textContent=items.length;$('stat-total').textContent=state.equipment.length;
  $('stat-free').textContent=state.equipment.filter(item=>state.occupancy.has(item.id)&&state.occupancy.get(item.id).length===0).length;
  $('catalog-empty').hidden=items.length>0;
  $('equipment-grid').replaceChildren(...items.map(item=>{
    const intervals=state.occupancy.get(item.id);const free=intervals?.length===0;const unknown=!intervals;const card=document.createElement('article');card.className='equipment-card';
    const remaining=intervals?Math.max(0,mins(state.hours.end)-mins(state.hours.start)-intervals.reduce((n,b)=>n+mins(b.end_time)-mins(b.start_time),0)):0;
    card.innerHTML=`<div class="card-art ${art(item)}"><span class="availability-badge ${free?'':'busy'}"><span class="dot"></span>${unknown?'Расписание недоступно':free?'Свободно весь день':remaining===0?'День занят':'Есть занятые интервалы'}</span><img src="/static/${art(item)}.svg" alt="" loading="lazy"></div><div class="card-content"><span class="tiny">${escape(item.category)}</span><h3>${escape(item.name)}</h3><p class="card-location">${escape(item.location)}</p><p class="card-description">${escape(item.description)}</p></div><div class="card-bottom"><span>${unknown?'Проверьте расписание':`${Math.floor(remaining/60)} ч ${remaining%60?remaining%60+' мин ':''}свободно`}</span><button type="button" aria-label="Забронировать ${escape(item.name)}">Забронировать ↗</button></div>`;
    card.querySelector('button').onclick=()=>openDetail(item);return card;
  }));
}
function renderMine(){
  const upcoming=state.mine.filter(item=>item.date>=state.today);$('stat-mine').textContent=upcoming.length;$('mine-count').textContent=upcoming.length;
  $('mine-empty').hidden=state.mine.length>0;
  const sorted=[...state.mine].sort((a,b)=>{const ap=a.date<state.today,bp=b.date<state.today;if(ap!==bp)return ap?1:-1;return ap?b.date.localeCompare(a.date)||b.start_time.localeCompare(a.start_time):a.date.localeCompare(b.date)||a.start_time.localeCompare(b.start_time);});
  $('my-bookings').replaceChildren(...sorted.map(item=>{const row=document.createElement('article');row.className='booking-row';row.innerHTML=`<div class="booking-date"><strong>${Number(item.date.slice(-2))}</strong><span>${escape(shortMonth(item.date))} ${item.date.slice(0,4)}</span></div><div><h3>${escape(item.name)}</h3><p>⌖ ${escape(item.location)}</p><p>Бронь ${escape(item.id.slice(0,12))}</p></div><div class="booking-status"><strong>${escape(item.start_time)}–${escape(item.end_time)}</strong><span class="${item.date<state.today?'past':''}">${item.date<state.today?'Прошедшая бронь':'✓ Подтверждено'}</span></div>`;return row;}));
}
async function refresh(initial=false){
  const epoch=++state.catalogEpoch;if(initial)showError('catalog-error','');
  $('refresh').disabled=true;
  try{
    const [catalog,mine,settings]=await Promise.all([api('/api/equipment'),api('/api/bookings'),api('/api/session')]);
    const date=state.date;
    const results=await Promise.all(catalog.equipment.map(async item=>[item.id,(await api(`/api/equipment/${encodeURIComponent(item.id)}/bookings?date=${date}`)).bookings]));
    if(epoch!==state.catalogEpoch||!state.user)return;
    state.today=settings.today;$('detail-date').min=state.today;$('today-label').textContent=numberDate(state.today);
    state.equipment=catalog.equipment;state.mine=mine.bookings;state.occupancy=new Map(results);
    renderCategories();renderCatalog();renderMine();online(true);showError('catalog-error','');
  }catch(error){if(epoch===state.catalogEpoch){online(false);showError('catalog-error',error.message);}}
  finally{if(epoch===state.catalogEpoch)$('refresh').disabled=false;}
}
function gaps(intervals){
  const result=[];let position=state.hours.start;
  for(const interval of intervals){if(position<interval.start_time)result.push([position,interval.start_time]);if(position<interval.end_time)position=interval.end_time;}
  if(position<state.hours.end)result.push([position,state.hours.end]);return result;
}
function setTimes(start,end){$('start-time').value=start;$('end-time').value=timeOf(Math.min(mins(start)+60,mins(end)));}
async function openDetail(item){
  state.selected=item;showError('booking-error','');$('detail-title').textContent=item.name;$('detail-category').textContent=item.category;
  $('detail-description').textContent=item.description;$('detail-location').textContent=item.location;$('detail-image').src=`/static/${art(item)}.svg`;
  $('detail-date').value=state.date<state.today?state.today:state.date;$('booking-dialog').showModal();document.body.classList.add('modal-open');await loadDetail(true);
}
async function loadDetail(resetTimes=false){
  const epoch=++state.detailEpoch;state.detailReady=false;$('book-submit').disabled=true;$('schedule-state').textContent='Обновляем…';
  $('occupancy').textContent='Проверяем расписание…';$('timeline').replaceChildren();$('free-slots').replaceChildren();
  if($('detail-date').value<state.today){showError('booking-error','Нельзя бронировать на прошедшую дату. Выберите сегодня или более поздний день');$('schedule-state').textContent='Прошедшая дата';$('occupancy').textContent='Выберите другую дату';return;}
  try{
    const data=await api(`/api/equipment/${encodeURIComponent(state.selected.id)}/bookings?date=${$('detail-date').value}`);
    if(epoch!==state.detailEpoch||!$('booking-dialog').open)return;
    const intervals=data.bookings;const free=gaps(intervals);
    $('schedule-state').textContent=numberDate($('detail-date').value);
    $('occupancy').replaceChildren(...intervals.map(b=>{const span=document.createElement('span');span.className='occupied-tag';span.textContent=`${b.start_time}–${b.end_time} · занято`;return span;}));
    if(!intervals.length)$('occupancy').textContent='Весь рабочий день свободен';
    const total=mins(state.hours.end)-mins(state.hours.start);
    $('timeline').replaceChildren(...intervals.map(b=>{const span=document.createElement('span');span.className='timeline-block';span.style.left=`${(mins(b.start_time)-mins(state.hours.start))/total*100}%`;span.style.width=`${(mins(b.end_time)-mins(b.start_time))/total*100}%`;return span;}));
    $('free-slots').replaceChildren(...free.map(([start,end])=>{const button=document.createElement('button');button.type='button';button.className='slot';button.textContent=`${start}–${end}`;button.onclick=()=>{setTimes(start,end);showError('booking-error','');};return button;}));
    if(!free.length)$('free-slots').textContent='Свободного времени нет. Выберите другую дату.';
    if(resetTimes){if(free.length)setTimes(...free[0]);else{$('start-time').value='';$('end-time').value='';}}
    state.detailReady=free.length>0;$('book-submit').disabled=!state.detailReady;
  }catch(error){if(epoch===state.detailEpoch){showError('booking-error',error.message);$('schedule-state').textContent='Недоступно';$('occupancy').textContent='Не удалось загрузить расписание';}}
}
let toastTimer;
function toast(message,title='Бронь подтверждена'){$('toast-title').textContent=title;$('toast-message').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,8000);}
function authTab(mode){
  $('login-form').hidden=mode!=='login';$('register-form').hidden=mode!=='register';
  for(const name of ['login','register']){$('tab-'+name).classList.toggle('active',name===mode);$('tab-'+name).setAttribute('aria-pressed',name===mode);}
  showError('login-error','');showError('register-error','');
}
$('tab-login').onclick=()=>authTab('login');$('tab-register').onclick=()=>authTab('register');
$('register-form').addEventListener('submit',async event=>{
  event.preventDefault();showError('register-error','');
  if($('register-password').value!==$('register-confirm').value){showError('register-error','Пароли не совпадают');return;}
  const email=$('register-email').value.trim().toLowerCase();
  if(!email.endsWith('@g.nsu.ru')){showError('register-error','Для регистрации используйте почту вида name@g.nsu.ru');return;}
  $('register-submit').disabled=true;
  try{const data=await api('/api/register',{method:'POST',body:JSON.stringify({name:$('register-name').value.trim(),email,password:$('register-password').value,password_confirm:$('register-confirm').value})});state.user=data.user;$('register-form').reset();const settings=await api('/api/session');Object.assign(state,{today:settings.today,date:settings.today,hours:settings.work_hours,timezone:settings.timezone,department:settings.department});await showApp();toast('Вы вошли как студент. Выберите предмет в каталоге.','Аккаунт создан');}
  catch(error){showError('register-error',error.message);}finally{$('register-submit').disabled=false;}
});
$('add-equipment').onclick=()=>{
  if(state.user?.role!=='admin')return;
  $('equipment-form').reset();showError('equipment-error','');
  $('equipment-categories').replaceChildren(...[...new Set(state.equipment.map(e=>e.category))].map(category=>{const option=document.createElement('option');option.value=category;return option;}));
  $('equipment-dialog').showModal();document.body.classList.add('modal-open');
};
$('close-equipment').onclick=()=>$('equipment-dialog').close();
$('equipment-dialog').addEventListener('close',()=>document.body.classList.remove('modal-open'));
$('equipment-form').addEventListener('submit',async event=>{
  event.preventDefault();showError('equipment-error','');$('equipment-submit').disabled=true;
  try{const result=await api('/api/equipment',{method:'POST',body:JSON.stringify({name:$('equipment-name').value.trim(),location:$('equipment-location').value.trim(),category:$('equipment-category').value.trim(),description:$('equipment-description').value.trim()})});$('equipment-dialog').close();state.category='Все';$('search').value='';await refresh(true);toast(`${result.equipment.name} · ${result.equipment.location}`,'Предмет добавлен');}
  catch(error){showError('equipment-error',error.message);}finally{$('equipment-submit').disabled=false;}
});
$('login-form').addEventListener('submit',async event=>{
  event.preventDefault();showError('login-error','');$('login-submit').disabled=true;
  try{const data=await api('/api/login',{method:'POST',body:JSON.stringify({user_id:$('user-id').value.trim(),password:$('password').value})});state.user=data.user;$('password').value='';const settings=await api('/api/session');Object.assign(state,{today:settings.today,date:settings.today,hours:settings.work_hours,timezone:settings.timezone,department:settings.department});await showApp();}
  catch(error){showError('login-error',error.message);}finally{$('login-submit').disabled=false;}
});
$('show-password').onclick=()=>{const visible=$('password').type==='password';$('password').type=visible?'text':'password';$('show-password').textContent=visible?'Скрыть':'Показать';$('show-password').setAttribute('aria-label',visible?'Скрыть пароль':'Показать пароль');};
$('logout').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});state.user=null;showLogin();}catch(error){showError('catalog-error',error.message);}};
$('nav-catalog').onclick=()=>navigate('catalog');$('to-catalog').onclick=()=>navigate('catalog');$('nav-mine').onclick=()=>navigate('mine');
$('search').addEventListener('input',renderCatalog);
$('catalog-date').addEventListener('change',()=>{if(!$('catalog-date').value)return;state.date=$('catalog-date').value;state.occupancy.clear();renderCatalog();refresh();});
$('refresh').onclick=()=>refresh(true);
$('detail-date').addEventListener('change',()=>{showError('booking-error','');if($('detail-date').value)loadDetail(true);else{state.detailReady=false;$('book-submit').disabled=true;}});
$('close-dialog').onclick=()=>$('booking-dialog').close();
$('booking-dialog').addEventListener('close',()=>{document.body.classList.remove('modal-open');state.detailEpoch++;state.selected=null;});
$('booking-dialog').addEventListener('click',event=>{if(event.target===$('booking-dialog')){const r=$('booking-dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('booking-dialog').close();}});
$('booking-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!state.detailReady||state.submitting)return;showError('booking-error','');
  if($('detail-date').value<state.today){showError('booking-error','Нельзя бронировать на прошедшую дату. Выберите сегодня или более поздний день');return;}
  if($('start-time').value>=$('end-time').value){showError('booking-error','Окончание должно быть позже начала.');return;}
  const payload={equipment_id:state.selected.id,date:$('detail-date').value,start_time:$('start-time').value,end_time:$('end-time').value};const name=state.selected.name;
  state.submitting=true;state.detailReady=false;$('book-submit').disabled=true;$('book-submit').textContent='Подтверждаем…';
  try{const result=await api('/api/bookings',{method:'POST',body:JSON.stringify(payload)});$('booking-dialog').close();toast(`${result.action==='SKIP'?'Уже в ваших бронях: ':''}${name} · ${numberDate(payload.date)} · ${payload.start_time}–${payload.end_time}`);await refresh();}
  catch(error){if($('booking-dialog').open){if(error.code==='PAST_DATE'){const settings=await api('/api/session');state.today=settings.today;$('detail-date').min=state.today;}await loadDetail(false);showError('booking-error',error.message);}}
  finally{state.submitting=false;$('book-submit').innerHTML='Подтвердить бронь <span aria-hidden="true">→</span>';}
});
$('close-toast').onclick=()=>$('toast').hidden=true;
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.user)refresh();});
setInterval(()=>{if(state.user&&!document.hidden&&!$('refresh').disabled){refresh();if($('booking-dialog').open&&state.detailReady&&!state.submitting)loadDetail(false);}},15000);
initialize();
