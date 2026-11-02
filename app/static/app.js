'use strict';

const $ = s => document.querySelector(s);

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const usd = v => v == null ? 'Unavailable' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v);

const fmt = v => v ? new Date(v).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) + ' ET' : 'Not available';

let csrf = '', routeVersion = 0, config = {}, currentReport, memoryRows = [];

async function api(path, method='GET', data) {

  const r = await fetch('/api/'+path,{method,headers:{'Content-Type':'application/json','X-Tony-Request':'1','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});

  let value; try { value=await r.json(); } catch { throw new Error('The server returned an unreadable response.'); }

  if(!r.ok){if(r.status===401 && path!=='login') await session();throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail||'Request failed'));}

  return value;

}

function notify(text){$('#toast').textContent=text;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,6500);}

async function action(button, task){button.disabled=true;try{await task();}catch(e){notify(e.message);}finally{button.disabled=false;}}

function heading(eyebrow,title,subtitle,actions=''){return `<div class="page-heading"><div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;}

function blank(text){return `<div class="empty">${esc(text)}</div>`;}

function button(text,id,primary=false){return `<button id="${id}" class="${primary?'primary':'secondary'}">${text}</button>`;}

function settingsLink(){return '<a class="secondary" href="#settings">Settings ↗</a>';}

function chart(report){

 const vals=report.today.hourly,prev=report.pace.previous,max=Math.max(...vals,...prev,1),w=680,h=180;

 const path=v=>v.map((n,i)=>`${i?'L':'M'}${36+i*(w-45)/23},${h-10-n/max*(h-35)}`).join(' ');

 return `<svg class="chart" viewBox="0 0 690 185" role="img" aria-label="Hourly sales for selected business day compared with the same weekday last week"><title>Hourly sales, dollars</title>${[0,.5,1].map(v=>`<line x1="36" x2="680" y1="${170-v*145}" y2="${170-v*145}" stroke="#e8ebe2" stroke-dasharray="3 5"/><text x="0" y="${174-v*145}" font-size="9" fill="#84907e">${Math.round(v*max)}</text>`).join('')}<path d="${path(prev)}" fill="none" stroke="#c2c8b6" stroke-width="2" stroke-dasharray="5 5"/><path d="${path(vals)} L671,170 L36,170 Z" fill="#edf2e8" opacity=".6"/><path d="${path(vals)}" fill="none" stroke="#527354" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/></svg><div class="chart-labels">${[0,4,8,12,16,20,23].map(i=>`<span>${report.today.hour_labels[i]}</span>`).join('')}</div><div class="legend"><span>Selected day</span><span>Same weekday last week</span></div>`;

}

async function session(){const s=await api('session');csrf=s.csrf||'';$('#shell').hidden=!s.authenticated;$('#login').hidden=s.authenticated;$('#setup-label').hidden=!s.setup_required;$('#login-description').textContent=s.setup_required?'Enter your launch terminal setup code and choose a password of at least 12 characters.':'Sign in to your private operations assistant.';if(s.authenticated) await route();}

$('#login-form').addEventListener('submit',async e=>{e.preventDefault();await action(e.submitter,async()=>{try{const r=await api('login','POST',{password:$('#password').value,setup_code:$('#setup-code').value});csrf=r.csrf;$('#password').value='';$('#setup-code').value='';await session();}catch(err){$('#login-error').textContent=err.message;}});});

$('#logout').onclick=e=>action(e.currentTarget,async()=>{await api('logout','POST',{});await session();});

window.addEventListener('hashchange',route);



async function route(){

 if($('#shell').hidden)return;

 const version=++routeVersion, page=location.hash.slice(1)||'overview';

 document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.dataset.page===page));

 const labels={overview:'Overview',shift:'Shift Desk',exceptions:'Exceptions',chat:'Ask your agent',briefings:'Daily briefings',memory:'Memory & tasks',corner:'Tony’s Corner',integrations:'Integrations',settings:'Settings & diagnostics'};

 $('#page-label').textContent=labels[page]||'Overview';

 $('#main').innerHTML='<div class="loading">Getting your operations desk ready…</div>';

 try{

   config=await api('settings');if(version!==routeVersion)return;

   $('#mode-pill').textContent=config.demo?'DEMO WORKSPACE':'LIVE · READ ONLY';$('#mode-pill').className='pill'+(config.demo?' demo':'');

   const html=await ({overview:overview,shift:shiftPage,exceptions:exceptionsPage,chat:chatPage,briefings:briefings,memory:memory,corner:cornerPage,integrations:integrations,settings:settings}[page]||overview)();

   if(version!==routeVersion)return;$('#main').innerHTML=html;bind(page);

 }catch(e){if(version===routeVersion)$('#main').innerHTML=heading('CONNECTION NEEDS ATTENTION','Let’s get connected.',esc(e.message),settingsLink())+'<div class="card"><h2>Your data is safe.</h2><p class="muted">A report could not be retrieved. No estimated or demo values have been substituted. Check integration settings, or explicitly enable demo mode to explore the application.</p><button class="secondary" id="retry">Try again</button></div>';$('#retry')?.addEventListener('click',route);}

}

async function overview(){

 const [r,archive]=await Promise.all([api('overview'),api('briefings')]);currentReport=r;

 const t=r.today,l=r.labor,b=archive.find(x=>x.mode===r.mode);

 const trend=r.seven_day_change==null?'No valid comparison':`${r.seven_day_change>=0?'+':''}${r.seven_day_change.toFixed(1)}% over previous 7 days`;

 const laborValue=l?.percentage==null?'Unavailable':l.percentage.toFixed(1)+'%';

 return heading('THE DAILY PICTURE','Your shift, in focus.',"A little clarity for everything ahead.",`<span class="date-chip">◷ &nbsp; ${esc(r.business_date)}</span>${button('Refresh ↻','refresh')}`)+

 (r.mode==='demo'?'<div class="banner">DEMO MODE · Every figure below is synthetic. Switch to live Toast in Settings when you’re ready.</div>':'')+

 `<div class="kpi-grid"><div class="card kpi featured"><div class="kpi-label">Net sales today <span>↗</span></div><div class="kpi-value">${usd(t.net_sales)}</div><small>Business date ${esc(r.business_date)}</small></div><div class="card kpi"><div class="kpi-label">Week to date <span>▥</span></div><div class="kpi-value">${usd(r.wtd)}</div><small class="positive">${esc(trend)}</small></div><div class="card kpi"><div class="kpi-label">Month to date <span>▤</span></div><div class="kpi-value">${usd(r.mtd)}</div><small>${t.orders} orders today · ${t.checks} checks</small></div><div class="card kpi"><div class="kpi-label">Labor / sales <span>♙</span></div><div class="kpi-value">${laborValue}</div><small>${l?(l.estimated?'Estimate · ':'Recorded · ')+usd(l.cost):'Check Toast labor permissions'}</small></div></div>`+

 `<div class="dashboard-grid"><div class="stack"><section class="card"><div class="card-head"><h3>The pace of the day</h3><span class="pill">HOURLY SALES</span></div><small>${esc(r.pace.label)}${r.pace.change==null?'':' · '+r.pace.change.toFixed(1)+'% through '+r.pace.through_hour}</small>${chart(r)}</section><section class="card"><div class="card-head"><h3>On the horizon</h3><small>NEXT ${config.future_days} DAYS</small></div>${r.upcoming.length?r.upcoming.slice(0,8).map(o=>`<div class="list-row"><div class="row-title"><span class="icon-square">▣</span><div><strong>${o.amount>=config.large_order_threshold?'Large order':'Scheduled order'}</strong><p>${esc(fmt(o.when))} · ${esc(o.source)}</p></div></div><strong>${usd(o.amount)}</strong></div>`).join(''):blank('No upcoming orders returned in the scanned window.')}</section><section class="card"><div class="card-head"><h3>Recent sales</h3><small>BUSINESS DAYS</small></div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Orders</th><th>Net sales</th></tr></thead><tbody>${r.daily.slice(-7).reverse().map(d=>`<tr><td>${esc(d.date)}</td><td>${d.orders}</td><td>${usd(d.net_sales)}</td></tr>`).join('')}</tbody></table></div></section></div>`+

 `<div class="stack"><section class="card briefing-card"><span class="eyebrow">YOUR MORNING BRIEF</span><span class="orb">✦</span><h2>Start with the<br>whole picture.</h2>${b?`<div class="briefing-preview">${esc(b.text.slice(0,420))}</div><a class="secondary" href="#briefings">Read briefing ↗</a>`:'<p>Yesterday’s performance, today’s priorities.<br>Ready every morning at 9 AM Eastern.</p>'+button('Create today’s briefing ↗','make-briefing')}</section><section class="card"><div class="card-head"><h3>On the floor</h3><span class="status-dot">${l?l.clocked_in.length+' CLOCKED IN':'UNAVAILABLE'}</span></div>${l?l.clocked_in.map(x=>`<div class="list-row"><div class="row-title"><span class="icon-square">♙</span><div><strong>${esc(x.employee)}</strong><p>Since ${esc(fmt(x.since))}</p></div></div><small>${x.hours.toFixed(1)}h</small></div>`).join('')||blank('No active shifts returned.'):blank('Labor access is unavailable.')}${r.overtime?.length?'<div class="alert-item"><strong>Hours to watch</strong><p>'+r.overtime.map(x=>esc(x.employee)+' · '+x.hours+'h').join('<br>')+'</p></div>':''}</section><section class="card"><div class="card-head"><h3>Needs a look</h3><small>OPERATIONAL SIGNALS</small></div><div class="alert-item"><strong>${r.stock===null?'Stock unavailable':r.stock.length+' items out of stock'}</strong><p>${r.stock?.map(x=>esc(x.name)).join(', ')||'No stock exceptions returned.'}</p></div><div class="alert-item"><strong>${usd(t.discounts)} in discounts · ${t.voids} voids</strong><p>Refunds ${usd(t.refunds)}. Review context before drawing conclusions.</p></div></section><section class="card ask-card"><h3>A second set of eyes.</h3><p>Ask your agent about the day.</p>${['Anything I missed today?','How are sales looking today?','Who’s clocked in?'].map(q=>`<button class="prompt-chip" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</section></div></div>`+

 `<details class="source-note"><summary>${esc(r.source)} · Retrieved ${esc(fmt(r.fresh_at))} · View reporting notes</summary><p>Source cache up to ${r.cache_max_age_seconds} seconds. Period: ${esc(r.period_start)} to ${esc(r.period_end)}.</p><ul>${r.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`;

}

async function chatPage(){const [messages,status]=await Promise.all([api('chat'),api('status')]);return heading('YOUR OPERATIONS PARTNER','Ask away.','Real information. Useful perspective. A little less on your plate.',settingsLink())+`<div class="chat-layout"><section class="card chat-card"><div class="chat-history" id="messages">${messages.length?messages.map(messageHTML).join(''):'<div class="empty"><h2>What’s on your mind?</h2>Ask about sales, labor, upcoming orders, or changes since your last check-in.</div>'}</div><form class="chat-compose" id="chat-form"><textarea id="question" placeholder="Anything I missed today?" aria-label="Message your operations agent" required maxlength="4000" rows="2"></textarea><button class="primary" type="submit">Send ↗</button></form><div class="chat-hint">${status.chatgpt.connected?'ChatGPT plan · '+esc(status.chatgpt.model||'Account model selected on first request'):'ChatGPT not connected · Factual reports remain available'} · ${config.demo?'DEMO DATA':'Read-only tools'}</div></section><aside class="stack chat-aside"><section class="card ask-card"><h3>Start here.</h3>${['How are sales looking today?','What happened yesterday?','How does today compare to last week?','Anything I missed today?','Find all orders from Sample Guest yesterday','/writeup'].map(q=>`<button class="prompt-chip" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</section><section class="card"><h3>Grounded in your business</h3><p class="muted">Your agent reads normalized Toast reports and your approved notes. It cannot change orders, payroll, or restaurant records.</p><small>Tony uses AI to interpret natural requests, including multiple tasks. Order and coaching exchanges are saved in encrypted local chat history; retrieved guest records are not supplied to AI or web search. Try &ldquo;find all orders from Sample Guest yesterday&rdquo;, or /guest name, email, or phone. Review important decisions against source records.</small></section></aside></div>`;}



function cornerJournalText(j){

 try{

  const value=JSON.parse(j.text);

  if(j.kind==='gaps' && Array.isArray(value)){

   const reasons=[...new Set(value.map(x=>x.reason).filter(x=>typeof x==='string'))];

   return `${value.length} series are pending. ${reasons.join(' ')}`;

  }

  if(j.kind==='data_import' && value && typeof value==='object'){

   const notes=[];

   if(value.clover?.covered_days!=null)notes.push(`Clover: ${value.clover.covered_days} historical days imported`);

   if(value.toast_snapshot?.rows!=null)notes.push(`Toast: ${value.toast_snapshot.rows} saved observations imported`);

   if(value.events)notes.push(`Event snapshots: ${value.events.business??0} business events and ${value.events.football??0} football events`);

   if(notes.length)return notes.join('. ')+'.';

  }

 }catch{}

 return j.text;

}

async function cornerPage(){

 const [r,h,m]=await Promise.all([api('corner'),api('corner?view=hypotheses'),api('corner?view=mistakes')]);

 if(r.available===false)return heading('RESEARCH WORKSPACE','Tony’s Corner','Research is unavailable. Your operations desk remains available.')+'<section class="card">'+blank(r.reason)+'</section>';

 const s=r.status, cover=s.coverage||[], predictions=r.predictions||[];

 const tw=await api('tasks').catch(()=>null);

 return heading('RESEARCH, WITH RECEIPTS','Tony’s Corner','Observations, forecasts and the evidence behind them.',button('Refresh ↻','refresh'))+

 `<div class="banner">${s.enabled?'Scheduled research enabled · '+esc(s.schedule_et)+' Eastern':'Scheduled research is off'} · ${s.isolation.ready?'OS isolation verified':'Analysis locked pending isolation setup'} · AI requests: up to ${s.limits.model_calls_per_day}/day${s.limits.model_calls_per_day?' · 40/month · Web search requests up to '+esc(s.limits.web_search_requests_per_day)+'/day · 90-second timeouts · No provider token/spend cap':''}</div>`+

 `<section class="card"><h2>Research allowance</h2><p>${esc(s.research_usage?.requests_today??0)} / ${esc(s.limits.model_calls_per_day)} requests today · ${esc(s.research_usage?.requests_this_month??0)} / 40 this month</p><p class="source-note">Search and interpretation share this allowance. Failed attempts count. At the monthly limit, local numerical research continues.</p>${(s.research_usage?.recent_searches||[]).map(x=>`<p class="source-note">Public search: ${esc(x.status)} · ${esc(fmt(x.started_at))}</p>`).join('')}</section>`+

 `<div class="page-grid">${approvedTaskPanel(tw)}${improvementInbox(r.improvements||[])}<section class="card"><h2>Research journal</h2>${(r.journal||[]).map(j=>`<article class="list-row"><div><strong>${esc(j.day)} · ${esc(j.kind)}</strong><p>${esc(cornerJournalText(j))}</p><small>${esc(fmt(j.created_at))}</small></div></article>`).join('')||blank('No completed research journal yet. Imported facts and future findings will appear here.')}</section>`+

 `<section class="card"><h2>Issued forecasts</h2><p class="source-note">Intervals are empirical planning ranges. Clover gross sales and Toast net sales use separate series.</p>${predictions.map(p=>`<div class="list-row"><div><strong>${esc(p.target_day)} · ${esc(p.metric)}</strong><p>${esc(p.dimension)}: ${esc(p.value)} · range ${esc(p.low)}–${esc(p.high)}</p><small>Issued ${esc(fmt(p.created_at))} · ${esc(p.model_version)}</small></div></div>`).join('')||blank('No forecasts have been issued. Missing forecasts are not replaced with invented values.')}</section>`+

 `<section class="card full"><h2>Source coverage</h2><div class="table-wrap"><table><thead><tr><th>Source / metric</th><th>Dimension</th><th>Dates</th><th>Days</th><th>Retrieved</th></tr></thead><tbody>${cover.map(c=>`<tr><td>${esc(c.source)} · ${esc(c.metric)}</td><td>${esc(c.dimension)}</td><td>${esc(c.first_day)}–${esc(c.last_day)}</td><td>${esc(c.days)}</td><td>${esc(fmt(c.retrieved_at))}</td></tr>`).join('')}</tbody></table></div>${cover.length?'':blank('No sanitized research data imported yet.')}</section>`+

 `<section class="card full"><h2>External feature evidence</h2><p class="source-note">These are dated schedule or forecast snapshots. Historical dates do not prove Tony knew them at the time. Tests exclude later knowledge, unknown exposures and realized weather; promising associations never automatically change forecasts.</p><div class="table-wrap"><table><thead><tr><th>Feature</th><th>Kind</th><th>Dates</th><th>Days</th><th>Earliest known</th></tr></thead><tbody>${(s.feature_coverage||[]).map(f=>`<tr><td>${esc(f.feature)}</td><td>${esc(f.kind)}</td><td>${esc(f.first_day)}–${esc(f.last_day)}</td><td>${esc(f.days)}</td><td>${esc(fmt(f.earliest_known))}</td></tr>`).join('')}</tbody></table></div>${s.feature_coverage?.length?'':blank('No verified feature vintages imported yet.')}</section>`+

 `<section class="card"><h2>Hypotheses</h2>${(h.hypotheses||[]).map(x=>`<div class="list-row"><div><strong>${esc(x.title)}</strong><p>${esc(x.status)} · confidence ${esc(x.confidence)}</p></div></div>`).join('')||blank('No hypotheses registered yet. Proposed explanations will be distinguished from supported findings.')}</section>`+

 `<section class="card"><h2>Hypothesis experiments</h2><p class="source-note">Two fixed numeric tests per day at most. Results never automatically change issued forecasts.</p>${(h.plans||[]).map(x=>`<div class="list-row"><div><strong>${esc(x.template)} · ${esc(x.metric)}</strong><p>${esc(x.dimension)} · ${esc(x.status)}</p></div></div>`).join('')||blank('No numeric experiments proposed yet.')}</section>`+

 `<section class="card"><h2>Forecast mistakes</h2>${(m.scorecards||[]).map(x=>`<div class="list-row"><div><strong>${esc(x.target_day)} · ${esc(x.metric)}</strong><p>Predicted ${esc(x.predicted)} · actual ${esc(x.actual)} · error ${esc(x.error)}</p></div></div>`).join('')||blank('No issued forecasts have matured into scorecards yet.')}</section>`+

 `<section class="card"><h2>School calendar coverage</h2>${(s.school_scope||[]).map(x=>`<div class="list-row"><div><strong>${esc(x.name)}</strong><p>${esc(x.priority)} · ${esc(x.status)}</p></div></div>`).join('')||blank('School scope has not been configured.')}</section>`+

 `<section class="card"><h2>Pending data and limits</h2>${s.pending.map(x=>`<p class="source-note">${esc(x)}</p>`).join('')}<p class="source-note">HTTP cap ${s.limits.http_requests_per_utc_day}/UTC day. Numerical analysis cap ${s.limits.run_seconds} seconds/run. Read-only restaurant data.</p></section>`+

 `<section class="card ask-card"><h2>Ask about the research</h2>${['What did you learn yesterday in Tony’s Corner?','What do you predict for Friday night in Tony’s Corner?','What events or school closures might affect sales?','Which forecasts did you get wrong?','What hypotheses are you testing?','What surprised you this week?'].map(q=>`<button class="prompt-chip" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</section></div>`;

}

async function exceptionsPage(){
 const [ledger,health]=await Promise.all([api('exceptions?include_closed=true'),api('source-health')]);
 return heading('MANAGER REVIEW','Exceptions & source health','Track issues locally, acknowledge ownership, and retain the evidence. No automatic messages.')+`<div class="page-grid">
 <section class="card full"><h2>Source freshness</h2><p>Checks use saved records. A fresh timestamp does not prove complete data.</p>${health.sources.map(r=>`<p><strong>${esc(r.title)}</strong>: ${esc(r.status)} · ${esc(fmt(r.observed_at))}</p>`).join('')}<button class="secondary" id="exceptions-refresh">Run local checks</button></section>
 <section class="card"><h2>Record an issue</h2><form id="exception-add"><label>Issue<input name="title" required maxlength="300"></label><label>Evidence / note<textarea name="note" required maxlength="1500"></textarea></label><label>Severity<select name="severity"><option value="warning">Warning</option><option value="critical">Critical</option><option value="info">Information</option></select></label><button class="primary" type="submit">Record locally</button></form></section>
 <section class="card"><h2>Review queue</h2><p>${esc(JSON.stringify(ledger.counts))}</p><p>Acknowledgment, assignment and due flags stay local. Source systems are unchanged.</p></section>
 ${ledger.items.map(r=>`<section class="card"><span class="pill">${esc(r.severity)} · ${esc(r.status)}</span><h2>${esc(r.title)}</h2><p>${esc(r.source)} · Seen ${r.occurrences} times · ${esc(fmt(r.last_seen))}</p><pre class="pre">${esc(JSON.stringify(r.evidence,null,2))}</pre><form data-exception="${esc(r.id)}" data-revision="${r.revision}"><label>Status<select name="status">${['open','acknowledged','resolved','false_positive'].map(v=>`<option value="${v}" ${v===r.status?'selected':''}>${v.replace('_',' ')}</option>`).join('')}</select></label><label>Assigned to (local label)<input name="assigned_to" maxlength="120" value="${esc(r.assigned_to)}"></label><label>Reason<textarea name="note" required maxlength="1500"></textarea></label><button class="secondary" type="submit">Save review</button></form><details><summary>Recent history</summary>${r.events.map(e=>`<p>${esc(e.action)} · ${esc(fmt(e.created_at))} · ${esc(e.note)}</p>`).join('')}</details></section>`).join('')||blank('No exceptions recorded.')}</div>`;
}

async function shiftPage(){
 const [s,menu]=await Promise.all([api('shift'),api('seasonal-menu')]);
 return heading('YOUR SHIFT, IN ONE PLACE','Shift Desk','Owner follow-ups, planned events and the latest imported board.',button('Refresh','refresh'))+
 `<div class="page-grid"><section class="card"><h2>Your follow-ups</h2><p class="source-note">Deadlines stay visible when overdue. These tasks do not modify monday.com.</p>${s.followups.map(t=>`<article class="list-row"><div><strong>${esc(t.title)}</strong><p>${esc(t.timing)} / ${esc(t.due_day||'No due date')} / P${esc(t.priority)}</p>${t.note?`<p>${esc(t.note)}</p>`:''}<button class="secondary" data-followup-done="${esc(t.id)}">Mark done</button></div></article>`).join('')||blank('No open follow-ups recorded. Add one below or use /followup in Telegram.')}<p class="source-note">${esc(s.memory_tasks?.length||0)} other open tasks remain in <a href="#memory">Memory &amp; tasks</a>.</p></section>
 <form class="card" id="followup-form"><h2>Add a follow-up</h2><label>What needs doing?<input name="title" required maxlength="300"></label><label>Due date<input name="due_day" type="date"></label><label>Priority<select name="priority"><option value="2">Normal</option><option value="1">High</option><option value="3">Later</option></select></label><label>Note<textarea name="note" maxlength="1500"></textarea></label><button type="submit" class="primary">Save follow-up</button></form>
 <section class="card"><h2>Today's events</h2><p class="source-note">Calendar checked ${esc(fmt(s.freshness.calendar_fetched_at))}${s.freshness.calendar_error?' / Refresh unavailable; showing saved data':''}</p>${s.today_events.map(e=>`<p>${esc(e.text)}</p>`).join('')||blank('No events found in the saved calendar for today. This does not prove nothing is planned.')}</section>
 <section class="card"><h2>Next seven days</h2>${s.upcoming_events.map(e=>`<div class="list-row"><div><strong>${esc(e.date)}</strong><p>${esc(e.text)}</p></div></div>`).join('')||blank('No upcoming notes in the saved calendar.')}</section>
 <section class="card full"><h2>Open monday.com items</h2><p class="source-note">${esc(s.board_open_count)} open items in the snapshot imported ${esc(fmt(s.freshness.board_imported_at))}. Changes since that export are not included. Showing up to 12; ask Tony for a narrower board search. ${esc(s.board_ambiguous_count)} completed-group items lack a Done status and remain ambiguous.</p>${s.board_items.map(i=>`<div class="list-row"><div><strong>${esc(i.name)}</strong><p>${esc(i.group)} / ${esc(i.fields?.Status||'Status not supplied')}</p></div></div>`).join('')||blank('No open board items in the available snapshot.')}</section>
 <section class="card full"><h2>Halloween menu from Toast</h2>${menu.available?`<p class="source-note">${esc(menu.catalog_item_count)} catalog items / owner-planned service: ${esc(menu.owner_confirmed_service?.dates?.join(', ')||'Unconfirmed')} / retrieved ${esc(fmt(menu.retrieved_at))}. Catalog presence does not establish activation or stock; prices below are source base prices.</p><div class="table-wrap"><table><thead><tr><th>Item</th><th>Base price</th><th>Modifier groups</th></tr></thead><tbody>${menu.items.map(i=>`<tr><td>${esc(i.name)}</td><td>${usd(i.price)}</td><td>${i.modifier_group_names.map(esc).join(', ')}</td></tr>`).join('')}</tbody></table></div><button class="prompt-chip" data-ask="What is on our Halloween menu in Toast?">Ask Tony about the menu</button>`:blank(menu.reason)}</section></div>`;
}
function approvedTaskPanel(data){

 if(!data)return '';

 return `<section class="card full"><h2>Approved task worker</h2><p>${esc(data.worker.status)} · Up to 5 task starts/UTC day · 12 HTTP requests/task within Corner’s shared daily limit · 120 seconds/task · No model calls</p><p class="source-note">A queued Improvement Inbox item is not task approval. Review a task’s exact instructions, scope and limits before approving. Audits produce diagnostics; unresolved counts remain partial.</p><form id="task-propose-form"><label>Fixed skill<select name="skill">${data.skills.map(x=>`<option value="${esc(x.id)}">${esc(x.id)} — ${esc(x.description)}</option>`).join('')}</select></label><label>Scope<input name="scope" maxlength="1500" placeholder="Blank for coverage; up to 3 comma-separated dates for pizza audit; URL for public page"></label><button type="submit" class="secondary">Prepare proposal</button></form><p class="source-note">Public reader: HTTPS text and links only; no login, JavaScript, forms or automatic link following. Approved sites: ${data.public_hosts.map(esc).join(', ')}.</p>${data.tasks.map(t=>`<article class="list-row"><div><strong>${esc(t.id)} · ${esc(t.manifest.skill)} · ${esc(t.state)}</strong><div class="pre">${esc(JSON.stringify(t.manifest,null,2))}</div><small>Exact scope hash: ${esc(t.sha256)}</small>${t.state==='proposed'?`<p><button class="primary" data-task-approve="${esc(t.id)}" data-hash="${esc(t.sha256.slice(0,16))}">Approve this exact task</button></p>`:''}${['proposed','approved'].includes(t.state)?`<button class="secondary" data-task-cancel="${esc(t.id)}">Cancel before start</button>`:''}<button class="secondary" data-task-result="${esc(t.id)}">Read status / result</button><div id="task-result-${esc(t.id)}" class="pre"></div>${t.error?`<p>${esc(t.error)}</p>`:''}</div></article>`).join('')||blank('No approved tasks. Prepare a proposal above or use /task coverage_review in Telegram.')}</section>`;

}

function improvementInbox(tasks){

 return `<section class="card full"><h2>Improvement Inbox</h2><p class="source-note">Priorities: P1 accuracy and reliability, P2 evidence, P3 Tony’s proposals. Existing approved jobs report progress here. Queuing a task does not execute code or approve changes.</p>${tasks.map(t=>`<article class="list-row"><div><strong>#${esc(t.id)} · P${esc(t.priority)} · ${esc(t.title)}</strong><p>${esc(t.status)} · ${esc(t.source)} · updated ${esc(fmt(t.updated_at))}</p><p>${esc(t.progress)}</p><p>Acceptance: ${t.acceptance.map(esc).join('; ')}</p><details><summary>Evidence</summary><div class="pre">${esc(JSON.stringify(t.evidence,null,2))}</div></details><form class="improvement-review" data-task="${esc(t.id)}"><label>Review status<select name="status">${['proposed','queued','blocked','done','dismissed'].map(v=>`<option value="${v}" ${t.status===v?'selected':''}>${v}</option>`).join('')}</select></label><label>Review note<input name="note" required maxlength="1600" placeholder="Evidence, next step, or reason for this decision"></label><button type="submit" class="secondary">Save review</button></form></div></article>`).join('')||blank('No tasks recorded yet.')}<p class="source-note">For code work, send /code with the specific request in Telegram, review the diff, and approve that exact patch separately.</p></section>`;

}

function messageHTML(m){const body=esc(m.text).replace(/https?:\/\/[^\s<>]+/g,u=>`<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);return `<div class="message ${m.role==='user'?'user':'assistant'}"><small>${m.role==='user'?'You':'the restaurant agent'}</small>${body}</div>`;}

async function briefings(){const rows=await api('briefings');return heading('THE MORNING EDITION','A good place to start.','Your daily briefing archive · 9:00 AM America/New_York',button('Create today’s briefing','make-briefing',true))+rows.map((b,i)=>`<details class="card archive-item" ${i===0?'open':''}><summary><span>${esc(b.day)} · Morning briefing</span><span class="pill ${b.mode==='demo'?'demo':''}">${b.mode.toUpperCase()} · ${b.ai?'AI + FACTS':'FACTUAL'}</span></summary><p class="source-note">Saved ${esc(fmt(b.created))}</p><div class="pre">${esc(b.text)}</div></details>`).join('')+(rows.length?'':`<div class="card">${blank('Your first briefing is ready to be created. Reports are saved locally, even when the AI narrative is unavailable.')}</div>`);}

async function memory(){memoryRows=await api('memory');return heading('THE THINGS THAT MATTER','Keep it in mind.','Only notes you explicitly save become persistent memory.')+`<div class="page-grid"><form class="card" id="memory-form"><h2>Add a note or follow-up.</h2><label>What should the agent remember?<textarea id="memory-text" rows="5" maxlength="4000" required placeholder="A procedure, supplier note, preference, or task…"></textarea></label><div class="form-grid"><label>Keep as<select id="memory-kind"><option value="permanent">Permanent information</option><option value="temporary">Temporary note</option><option value="task">Follow-up task</option></select></label><label>Expires (temporary notes)<input type="datetime-local" id="memory-expires"></label></div><div class="form-actions">${button('Save approved note','save-memory',true)}</div><p class="source-note">Avoid customer details and sensitive employee information. Notes are stored on this computer and may be included in AI requests.</p></form><div class="stack">${memoryRows.map(m=>`<article class="card memory-item ${m.done?'done':''}" data-memory="${m.id}"><div class="card-head"><span class="pill">${esc(m.kind.toUpperCase())}${m.done?' · DONE':''}</span><small>${m.expires?'Expires '+esc(fmt(m.expires)):esc(fmt(m.created))}</small></div><p>${esc(m.text)}</p><div class="inline-actions"><button class="quiet" data-edit="${m.id}">Edit</button>${m.kind==='task'?`<button class="quiet" data-done="${m.id}">${m.done?'Reopen':'Mark complete'}</button>`:''}<button class="quiet danger" data-delete="${m.id}">Delete</button></div></article>`).join('')||'<div class="card">'+blank('No saved memory yet. Add the context that helps your team.')+'</div>'}</div></div>`;}

async function integrations(){const s=await api('status');const cards=[['Toast POS',s.toast.status,'Sales, check totals, trends, labor, stock, and upcoming orders. Endpoint availability depends on your granted permissions.'],['Clover history',s.clover?.history?.days?(s.clover.history.days+' days imported'):(s.clover?.configured?'Token saved · import pending':'Not connected'),'Historical daily sales trends and top-level item counts from the former POS. Gross sales are labeled separately from Toast net sales.'],['the restaurant events Sheet',s.calendar?.configured?(s.calendar.events+' events imported · '+s.calendar.year):'Not connected','Read-only monthly event tabs. Staff plans are separate from official sports schedules and POS sales.'],['ChatGPT plan',s.chatgpt.connected?'Signed in'+(s.chatgpt.plan_enabled?' · plan enabled':' · plan permission missing'):'Not connected','Official browser OAuth. No separately billed API key or automatic billing fallback.'],['Telegram',s.telegram,'Private bot conversations and optional morning delivery. Allowlisted user and chat only; outbound polling requires no public port.'],['Restaurant365',s.r365,'Typed provider interfaces for invoices, inventory, recipe costs, purchasing, vendors and accounting. No endpoints are called.'],['ezCater',s.ezcater,'HMAC-SHA256 signature validation and local test fixture. Receipt-only adapter; order retrieval and hosting remain pending.'],['Microsoft 365 Outlook',s.outlook.status,'Read-only Inbox previews. Authorize locally with connect-outlook; see OUTLOOK-SETUP.md.'],['monday.com',s.monday.status,s.monday.mode==='snapshot'?'Manual Excel export; save fresh files in the weekly import folder shown below, or import in Settings.':'Read-only board items; connect in Settings.']];return heading('CONNECTED, WITH CARE','Your business ecosystem.','Know exactly what is connected and what is still to come.',settingsLink())+`<div class="page-grid">${cards.map(([name,status,description])=>`<section class="card status-card"><span class="eyebrow">INTEGRATION</span><h2>${name}</h2><span class="pill">${esc(status)}</span><p class="source-note">${description}</p></section>`).join('')}<section class="card full"><h3>Weekly calendar and board refresh</h3><p>${s.weekly_sources?.enabled?'Every Saturday at 4 AM Eastern':'Weekly refresh is off'} · Next: ${esc(fmt(s.weekly_sources?.next_scheduled_at))}</p><p>Google Sheet: automatic download. monday.com: export the board to Excel and save it here:</p><div class="pre">${esc(s.weekly_sources?.import_directory||'Not configured')}</div><p class="source-note">After Saturday 4 AM, Tony checks for the new export every five minutes until one is imported. If the computer is asleep, he catches up when it resumes. Missing or invalid exports preserve the previous snapshot.</p><details><summary>Latest weekly refresh status</summary><div class="pre">${esc(JSON.stringify(s.weekly_sources?.current_cycle||{},null,2))}</div></details></section><section class="card full"><h3>Toast endpoint checks</h3>${Object.keys(s.toast.endpoints).length?`<div class="table-wrap"><table><thead><tr><th>Endpoint</th><th>Result</th><th>Checked</th></tr></thead><tbody>${Object.entries(s.toast.endpoints).map(([path,v])=>`<tr><td>${esc(path)}</td><td>${esc(v.status)} ${esc(v.detail||'')}</td><td>${esc(fmt(v.checked_at))}</td></tr>`).join('')}</tbody></table></div>`:blank('Endpoint checks appear after the first live report. Configured credentials alone do not confirm access.')}</section></div>`;}

async function settings(){const s=await api('status'),a=s.chatgpt;return heading('MAKE IT YOURS','Settings & connections.','Private by default. Explicit about every connection.')+`<div class="page-grid"><section class="card"><span class="eyebrow">AI CONNECTION</span><h2>Continue with ChatGPT</h2><p class="muted">Sign in on this computer at <a class="link" href="http://127.0.0.1:8790/#settings">127.0.0.1:8787</a>. Approve ChatGPT plan usage. To use available ChatGPT credits, open ChatGPT Settings &gt; Usage. Under App limits, select the restaurant agent &gt; Manage limits and set 100%; also enable app credit use. Review automatic credit purchases separately.</p><label>Saved account<select id="account"><option value="">Add a ChatGPT account</option>${a.accounts.map(x=>`<option value="${esc(x.id)}" ${x.id===a.active?'selected':''}>${esc(x.label)}</option>`).join('')}</select></label><div class="form-actions">${button('Continue with ChatGPT','connect-ai',true)}${button('Use saved account','select-account')}${button('Enable plan permission','reconsent')}</div><div class="form-actions">${button('Load available models','load-models')}${button('Sign out of ChatGPT','disconnect-ai')}</div><div id="model-picker"></div><p class="source-note">Selected model: ${esc(a.model||'None yet')} · ${a.connected?'Signed in':'Not connected'}<br><a class="link" href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage ChatGPT usage and app credit permissions ↗</a></p>${a.error?`<div class="banner error">${esc(a.error.code)}${a.error.request_id?' · Request '+esc(a.error.request_id):''}</div>`:''}${a.paused?button('Retry ChatGPT requests','resume-ai'):''}</section><section class="card"><span class="eyebrow">LOCAL CREDENTIALS</span><h2>Toast & Telegram</h2><p class="muted">Enter secrets using the local terminal wizard. They are encrypted with your Windows account and never returned to this page.</p><div class="code">./.venv/Scripts/python.exe -m app.manage configure</div><p class="source-note">Choose <b>toast</b> or <b>telegram</b>. For Telegram, create a bot with BotFather and enter its token, your numeric user ID, and your private chat ID.</p><h3>Telegram commands</h3><p class="code">/sales · /briefing · /catchup · /status<br>Or ask a question in a private message.</p><small>Bot chats are handled by Telegram’s servers and are not end-to-end encrypted. Morning delivery is an explicit option below.</small></section><form class="card" id="settings-form"><span class="eyebrow">REPORTING PREFERENCES</span><h2>Your operating rhythm.</h2><label class="checkbox"><input id="demo" type="checkbox" ${config.demo?'checked':''}>Use clearly labeled synthetic demo data</label><div class="form-grid"><label>Fallback closeout hour (Eastern)<input type="number" id="closeout" min="0" max="23" value="${config.closeout_hour}"></label><label>Large order threshold ($)<input id="threshold" type="number" min="1" value="${config.large_order_threshold}"></label><label>Sales and hours week starts<select id="workweek"><option value="">Not configured</option>${['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].map((d,i)=>`<option value="${i}" ${config.workweek_start===i?'selected':''}>${d}</option>`).join('')}</select></label><label>Confirmed overtime multiplier<input id="overtime" type="number" min="1" max="3" step=".01" placeholder="Leave blank if unknown" value="${config.overtime_multiplier??''}"></label><label>Future order scan (days)<input id="future" type="number" min="1" max="30" value="${config.future_days}"></label></div><label class="checkbox"><input id="briefing-ai" type="checkbox" ${config.briefing_ai?'checked':''}>Use ChatGPT plan for automatic briefing narratives</label><label class="checkbox"><input id="telegram-briefings" type="checkbox" ${config.telegram_briefings?'checked':''}>Send live morning briefings to my allowed Telegram chat</label><div class="form-actions">${button('Save preferences','save-settings',true)}</div></form><section class="card"><span class="eyebrow">SYSTEM HEALTH</span><h2>Running locally.</h2><div class="list-row"><span>Business timezone</span><small>${esc(s.timezone)}</small></div><div class="list-row"><span>Briefing scheduler</span><small>${esc(s.scheduler.status)}</small></div><div class="list-row"><span>Dashboard origin</span><small>${esc(s.transport)}</small></div><p class="source-note">${esc(s.briefing_error||'The scheduler checks every five minutes after 9 AM Eastern and catches up after a restart.')}</p><div class="form-actions">${button('Back up database','backup')}${button('Clear conversation history','clear-chat')}</div><h3 class="source-note">PRIVATE REMOTE ACCESS</h3><p class="muted">Use Tailscale Serve with HTTPS and your private tailnet. Keep the app bound to 127.0.0.1. See README for setup and secure-cookie configuration.</p><small>Version ${esc(s.version)} · Backups retained locally (latest 14). Chat history retains the most recent 2,000 messages.</small></section></div>`;}

function on(id,fn){$(id)?.addEventListener('click',e=>action(e.currentTarget,fn));}

async function bind(page){

 on('#refresh',route);on('#make-briefing',async()=>{await api('briefings','POST',{});notify('Morning briefing saved.');location.hash='briefings';if(page==='briefings')await route();});

 document.querySelectorAll('[data-ask]').forEach(b=>b.onclick=async()=>{const q=b.dataset.ask;if(page!=='chat'){location.hash='chat';sessionStorage.setItem('tonyops_question',q);}else{$('#question').value=q;$('#question').focus();}});

 if(page==='exceptions'){
  $('#exceptions-refresh').onclick=e=>action(e.currentTarget,async()=>{await api('exceptions/refresh','POST',{});notify('Local source checks completed.');await route();});
  $('#exception-add').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const d=new FormData(e.target);await api('exceptions','POST',Object.fromEntries(d));await route();});};
  document.querySelectorAll('[data-exception]').forEach(form=>form.onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const d=Object.fromEntries(new FormData(form));d.expected_revision=Number(form.dataset.revision);await api('exceptions/'+form.dataset.exception,'POST',d);notify('Review saved.');await route();});});
 }
 if(page==='shift'){
  const form=$('#followup-form');form.onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const fields=new FormData(form);await api('followups','POST',{title:fields.get('title'),due_day:fields.get('due_day'),note:fields.get('note'),priority:Number(fields.get('priority'))});notify('Follow-up saved.');await route();});};
  document.querySelectorAll('[data-followup-done]').forEach(b=>b.onclick=()=>action(b,async()=>{await api('followups/'+b.dataset.followupDone+'/done','POST',{});notify('Follow-up completed.');await route();}));
 }
 if(page==='corner'){

  const proposal=$('#task-propose-form');if(proposal)proposal.onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const fields=new FormData(proposal),skill=fields.get('skill'),scope=fields.get('scope').trim();const args=skill==='coverage_review'?{}:skill==='pizza_audit'?{dates:scope.split(',').map(x=>x.trim()).filter(Boolean)}:{url:scope};await api('tasks/propose','POST',{skill,arguments:args});notify('Proposal prepared. Review the scope before approval.');await route();});};

  document.querySelectorAll('[data-task-approve]').forEach(b=>b.onclick=()=>action(b,async()=>{await api('tasks/'+b.dataset.taskApprove+'/approve','POST',{hash:b.dataset.hash});notify('Exact task approved; worker will pick it up.');await route();}));

  document.querySelectorAll('[data-task-cancel]').forEach(b=>b.onclick=()=>action(b,async()=>{await api('tasks/'+b.dataset.taskCancel+'/cancel','POST',{});await route();}));

  document.querySelectorAll('[data-task-result]').forEach(b=>b.onclick=()=>action(b,async()=>{const t=await api('tasks/'+b.dataset.taskResult);$('#task-result-'+b.dataset.taskResult).textContent=JSON.stringify({state:t.state,result:t.result,checkpoint:t.checkpoint,error:t.error},null,2);}));

  document.querySelectorAll('.improvement-review').forEach(form=>form.onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const values=new FormData(form);await api('corner/improvements/'+form.dataset.task,'POST',{status:values.get('status'),note:values.get('note')});notify('Review saved. No execution started.');await route();});});

 }

 if(page==='chat'){

  const pending=sessionStorage.getItem('tonyops_question');if(pending){$('#question').value=pending;sessionStorage.removeItem('tonyops_question');}

  $('#messages').scrollTop=$('#messages').scrollHeight;

  $('#chat-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const q=$('#question').value.trim();if(!q)return;$('#question').value='';$('#messages').insertAdjacentHTML('beforeend',messageHTML({role:'user',text:q})+'<div class="loading" id="thinking">Checking your business data…</div>');$('#messages').scrollTop=$('#messages').scrollHeight;try{const r=await api('chat','POST',{text:q});$('#thinking')?.remove();$('#messages')?.insertAdjacentHTML('beforeend',messageHTML({role:'assistant',text:r.text}));if(r.writeup_pdf&&/^[a-f0-9]{32}$/.test(r.writeup_pdf))$('#messages')?.insertAdjacentHTML('beforeend',`<p><a href="/api/writeups/${r.writeup_pdf}/pdf">Download unsigned coaching draft PDF</a></p>`);}finally{$('#thinking')?.remove();if($('#messages'))$('#messages').scrollTop=$('#messages').scrollHeight;}});};

 }

 if(page==='memory'){

  $('#memory-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{await api('memory','POST',{text:$('#memory-text').value,kind:$('#memory-kind').value,expires:$('#memory-expires').value?new Date($('#memory-expires').value).toISOString():null});await route();});};

  document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>action(b,async()=>{await api('memory/'+b.dataset.delete,'DELETE');await route();}));

  document.querySelectorAll('[data-done]').forEach(b=>b.onclick=()=>action(b,async()=>{const m=memoryRows.find(x=>x.id===Number(b.dataset.done));await api('memory/'+m.id,'PUT',{...m,done:!m.done});await route();}));

  document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const m=memoryRows.find(x=>x.id===Number(b.dataset.edit)),card=b.closest('article');card.innerHTML=`<label>Edit approved note<textarea rows="5">${esc(m.text)}</textarea></label><div class="form-actions"><button class="primary">Save correction</button><button class="secondary">Cancel</button></div>`;card.querySelector('.secondary').onclick=route;card.querySelector('.primary').onclick=e=>action(e.currentTarget,async()=>{await api('memory/'+m.id,'PUT',{...m,text:card.querySelector('textarea').value});await route();});});

 }

 if(page==='settings'){

  const s=await api('status');if(location.hash!=='#settings')return;

  $('#settings-form').insertAdjacentHTML('beforeend',`<label class="checkbox"><input id="web-search" type="checkbox" ${config.web_search_enabled?'checked':''}>Search the public web for current questions</label><p class="source-note">Off until you enable it. Tony uses your ChatGPT plan or available credits, with no separate API-key billing. The API list price ($0.01 per search action plus tokens) is a reference, not a confirmed extra charge to you. One qualifying question sends at most one search request; a request may contain multiple billable actions. Local cap: 25 search requests per Eastern day. Today: ${s.web_search.daily_requests}/25. ${s.web_search.last_error?'Last search issue: '+esc(s.web_search.last_error):''} Search uses only a public query; Toast, Clover, notes and chat history stay out of the search request.</p>`);

  const credentials=$('#settings-form')?.parentElement;

  credentials?.insertAdjacentHTML('afterbegin',`<form class="card" id="monday-import-form"><span class="eyebrow">TEAM BOARD · NO API TOKEN</span><h2>Import monday.com export</h2><p class="muted">Export the board to Excel in monday.com, then select that .xlsx file here. Tony will read the local snapshot; re-export and import again to refresh it.</p><label>Board URL<input id="monday-import-url" type="url" required value="${esc(s.monday?.board_url||'')}"></label><label>Excel export<input id="monday-file" type="file" accept=".xlsx" required></label><div class="form-actions"><button class="primary" type="submit">Import board snapshot</button></div><p class="source-note">${s.monday?.mode==='snapshot'?esc(s.monday.items+' items imported '+fmt(s.monday.imported_at)):'No snapshot imported yet.'} Export via board menu → More actions → Export board to Excel.</p></form>`);

  $('#monday-import-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const file=$('#monday-file').files[0];if(!file||!file.name.toLowerCase().endsWith('.xlsx'))throw new Error('Choose a monday.com .xlsx export.');if(file.size>8000000)throw new Error('Export exceeds the 8 MB limit.');const response=await fetch('/api/monday/import',{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','X-Tony-Request':'1','X-CSRF-Token':csrf,'X-Monday-Board-Url':$('#monday-import-url').value.trim()},body:file});const value=await response.json();if(!response.ok)throw new Error(value.detail||'Board import failed.');notify(value.items+' monday.com items imported.');await route();});};

  $('#monday-import-form').insertAdjacentHTML('afterend',`<form class="card" id="monday-form" autocomplete="off"><span class="eyebrow">TEAM BOARD</span><h2>Optional live API connection</h2><p class="muted">Tony reads and summarizes items on one board. Enter your personal API token here on this computer; it is stored in the Windows-encrypted vault and never shown in chat or status. Tony makes read-only queries.</p><label>Board URL<input id="monday-url" type="url" required value="${esc(s.monday?.board_url||'')}"></label><label>Personal API token<input id="monday-token" type="password" required autocomplete="off"></label><div class="form-actions"><button class="primary" type="submit">Check and save board</button></div><p class="source-note">${s.monday?.mode==='api'?'Live API connected: '+esc(s.monday.board_name)+'.':'Not connected.'} Find your token in monday.com profile → Developers → API token → Show. Do not paste it into chat.</p></form>`);

  $('#monday-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const token=$('#monday-token').value.trim();try{const r=await api('monday/connect','POST',{url:$('#monday-url').value.trim(),token});notify('Connected to '+r.board_name+'.');await route();}finally{const field=$('#monday-token');if(field)field.value='';}});};

  credentials?.insertAdjacentHTML('afterbegin',`<form class="card" id="calendar-form" autocomplete="off"><span class="eyebrow">PLANNED EVENTS</span><h2>the restaurant Google Sheet calendar</h2><p class="muted">Paste the shared Sheet link. Tony reads the month tabs and keeps a local copy for offline use. No Google API key is needed.</p><label>Google Sheets sharing link<input id="calendar-url" type="url" required placeholder="https://docs.google.com/spreadsheets/d/…/edit" autocomplete="off"></label><div class="form-actions"><button class="primary" type="submit">Connect Sheet</button><button class="secondary" type="button" id="calendar-refresh" ${s.calendar?.configured?'':'disabled'}>Refresh events</button></div><p class="source-note">${s.calendar?.configured?esc(s.calendar.events+' events from '+s.calendar.year+'; last checked '+fmt(s.calendar.fetched_at)):'Not connected.'}${s.calendar?.last_error?' Refresh issue: '+esc(s.calendar.last_error):''}</p></form>`);

  $('#calendar-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{await api('calendar/connect','POST',{url:$('#calendar-url').value.trim()});notify('Google Sheet events connected.');await route();});};

  on('#calendar-refresh',async()=>{const r=await api('calendar/refresh','POST',{});notify(r.last_error?'Refresh unavailable; saved events remain available.':'Calendar events refreshed.');await route();});

  credentials?.insertAdjacentHTML('afterbegin',`<form class="card" id="clover-form" autocomplete="off"><span class="eyebrow">HISTORICAL SALES</span><h2>Connect Clover</h2><p class="muted">Enter your Clover Platform API token here on this computer. Tony checks Read Orders access before saving it in the Windows-encrypted vault. The token never appears in status or chat.</p><label>Clover merchant ID<input id="clover-merchant" required pattern="[A-Za-z0-9]{8,32}" value="${esc(s.clover?.merchant_id||'3NMZM0YN49QQ1')}" autocomplete="off"></label><label>API token<input id="clover-token" type="password" required autocomplete="off"></label><div class="form-actions"><button class="primary" type="submit">Check and save token</button></div><p class="source-note">${s.clover?.configured?'Token saved for merchant '+esc(s.clover.merchant_id)+'.':'Not connected yet.'} Imported history: ${s.clover?.history?.days||0} days from ${esc(s.clover?.history?.first_day||'pending')} through ${esc(s.clover?.history?.last_day||'pending')}. Tony uses these aggregates for historical questions.</p></form>`);

  $('#clover-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const token=$('#clover-token').value.trim();try{await api('clover/connect','POST',{merchant_id:$('#clover-merchant').value.trim(),token});notify('Clover token saved. I can now check historical export access.');await route();}finally{const field=$('#clover-token');if(field)field.value='';}});};

  api('status').then(s=>{if(location.hash!=='#settings')return;const grid=$('#settings-form')?.parentElement;if(!grid)return;grid.insertAdjacentHTML('beforeend',`<section class="card full"><h2>Live diagnostics</h2><p>Model: ${esc(s.chatgpt.model||'Not selected')} · Token: ${esc(s.chatgpt.token_state)} · Fallback: ${s.chatgpt.fallback_active?'active':'inactive'}</p><p>Last successful AI request: ${esc(fmt(s.chatgpt.last_success))}<br>Next briefing: ${esc(s.scheduler.next_briefing)}<br>Morning delivery: ${s.scheduler.enabled?'enabled':'disabled'}<br>Outlook: ${esc(s.outlook.status)} ${esc(s.outlook.account||'')}</p><details><summary>Provider, worker and delivery details</summary><div class="pre">${esc(JSON.stringify({ai:s.chatgpt,runtime:s.runtime,telegram:s.telegram,scheduler:s.scheduler,outlook:s.outlook},null,2))}</div></details></section>`);}).catch(e=>notify(e.message));

  $('#settings-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{await api('settings','PUT',{demo:$('#demo').checked,closeout_hour:Number($('#closeout').value),workweek_start:$('#workweek').value===''?null:Number($('#workweek').value),overtime_multiplier:$('#overtime').value===''?null:Number($('#overtime').value),future_days:Number($('#future').value),large_order_threshold:Number($('#threshold').value),briefing_ai:$('#briefing-ai').checked,telegram_briefings:$('#telegram-briefings').checked,web_search_enabled:$('#web-search').checked});notify('Preferences saved.');await route();});};

  const signIn=async reconsent=>{const r=await api('ai/start','POST',{account:$('#account').value||null,reconsent});location.assign(r.url);};

  on('#connect-ai',()=>signIn(false));on('#reconsent',()=>signIn(true));

  on('#select-account',async()=>{await api('ai/account','POST',{account:$('#account').value});await route();});

  on('#disconnect-ai',async()=>{const r=await api('ai/signout','POST',{});notify(r.revocation_confirmed?'Signed out; renewable session revoked.':'Signed out locally. Remote revocation was not confirmed; disconnect this app in ChatGPT Settings.');await route();});

  on('#resume-ai',async()=>{await api('ai/resume','POST',{});await route();});

  on('#load-models',async()=>{const models=await api('ai/models');$('#model-picker').innerHTML=`<label>Account models<select id="models">${models.map(x=>`<option value="${esc(x.slug)}">${esc(x.display_name)}</option>`).join('')}</select></label><div class="form-actions">${button('Use this model','save-model')}</div>`;on('#save-model',async()=>{await api('ai/model','POST',{model:$('#models').value});notify('Model selected.');});});

  on('#backup',async()=>{const r=await api('backup','POST',{});notify('Database backup saved: '+r.file);});

  on('#clear-chat',async()=>{if(confirm('Delete local web and Telegram conversation history?')){await api('chat','DELETE');notify('Conversation history cleared.');}});

 }

}

session().catch(e=>{document.body.innerHTML='<main><h1>Unable to reach the local server.</h1><p>'+esc(e.message)+'</p></main>';});









