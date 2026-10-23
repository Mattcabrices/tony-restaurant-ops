'use strict';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>v==null?'Unavailable':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(v);
const row=(label,value)=>`<div class="row"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
async function load(){
 const response=await fetch('/api/demo');if(!response.ok)throw new Error('The synthetic report is unavailable.');const d=await response.json();
 document.querySelector('#date').textContent=`EXAMPLE BUSINESS DATE ${d.business_date} · ${d.provenance.source} · ALL VALUES INVENTED`;
 const wings=d.product_mix.categories.wings;
 document.querySelector('#stats').innerHTML=[['Sample net sales',money(d.sales.net_sales)],['Example orders',d.sales.orders],['Whole pies',d.whole_pizzas.total],['Known wing pieces',wings.known_pieces]].map(([label,value])=>`<div class="stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join('');
 document.querySelector('#pmix').innerHTML=Object.entries(d.whole_pizzas.by_channel).map(([key,value])=>row(key.replace('_',' ')+' pies',value)).join('')+row('Wing portions',wings.observed_portion_orders)+row('Individual wing pieces',wings.total_pieces??'Unknown sizes prevent a complete total');
 const f=d.forecast;
 document.querySelector('#forecast').innerHTML=f.available?`<div class="value">${money(f.value)}</div><div class="range">${money(f.low)} – ${money(f.high)}</div>`+row('Synthetic target',f.target_day)+row('Prior residuals',f.calibration_n)+row('Retrospective MAE',money(d.backtest.mae)):`<p>${esc(f.reason)}</p>`;
 document.querySelector('#integrations').innerHTML=d.integrations.map(i=>`<div class="integration"><strong>${esc(i.name)}</strong><span>${esc(i.public_status)}</span><small>${esc(i.production_status)}</small></div>`).join('');
}
load().catch(error=>{const box=document.querySelector('#error');box.hidden=false;box.textContent=error.message;});
