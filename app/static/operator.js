'use strict';

window.TonyOperator = (() => {
  const money = n => n == null ? 'Unavailable' : new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(n);
  const quantity = n => n == null ? 'Unavailable' : Number(n).toLocaleString('en-US',{maximumFractionDigits:2});
  const values = (rows, fn) => rows.map(fn).join('');
  const metricOptions = `<option value="sales">Net sales</option><option value="orders">Orders</option><option value="wing_orders">Wing orders</option><option value="wing_pieces">Wing pieces</option><option value="finger_orders">Finger orders</option><option value="finger_pieces">Finger pieces</option><option value="labor_hours">Recorded labor hours</option>`;
  let capabilities;

  async function page() {
    capabilities = await api('operator/capabilities');
    const day = esc(capabilities.business_date);
    return heading('OPERATOR WORKSPACE','Find the answer. Check the evidence.',
      'Sales, receipts and forecasts with clear dates, source coverage and uncertainty.') +
      `<div class="operator-layout"><section class="card"><form id="operator-form" class="operator-form">
      <label>What do you need?<select id="op-kind"><option value="sales">Sales & comparisons</option><option value="orders">Find orders by amount</option><option value="receipt">Find a specific receipt</option><option value="channels">Sales by ordering channel</option><option value="forecast">Forecast demand</option></select></label>
      <div data-op-kinds="sales orders"><label>Period<select id="op-period"><option value="yesterday">Yesterday</option><option value="today">Today so far</option><option value="this_week">This reporting week</option><option value="last_week">Last reporting week</option><option value="last_14_days">Last 14 completed business days</option><option value="custom">Custom dates</option></select></label>
      <div id="op-custom" class="operator-fields" hidden><label>From<input id="op-start" type="date" value="${day}"></label><label>Through<input id="op-end" type="date" value="${day}"></label></div></div>
      <div data-op-kinds="sales"><label>Metric<select id="op-sales-metric">${metricOptions}<option value="whole_pizzas">Whole pizzas</option></select></label><label>Compare with<select id="op-compare"><option value="none">No comparison</option><option value="previous_period">Previous equal-length period</option><option value="previous_week">Same dates one week earlier</option></select></label></div>
      <div data-op-kinds="orders" hidden><div class="operator-fields"><label>Minimum $<input id="op-min" type="number" min="0" max="10000000" step="0.01" value="500"></label><label>Maximum $<input id="op-max" type="number" min="0" max="10000000" step="0.01" value="900"></label></div><label>Match on<select id="op-basis"><option value="order_total">Combined order total, including tax/tip</option><option value="check_total">Individual check total, including tax/tip</option><option value="net_sales">Net sales, after refunds</option></select></label></div>
      <div data-op-kinds="receipt" hidden><label>Business date<input id="op-receipt-day" type="date" value="${day}"></label><label>Receipt/order/check number or order UUID<input id="op-reference" maxlength="36" autocomplete="off" placeholder="For example: 101"></label><p class="muted">Receipt numbers can repeat. Guest lookup remains in private chat.</p></div>
      <div data-op-kinds="channels" hidden><label>Business date<input id="op-channel-day" type="date" value="${day}"></label><div class="operator-fields"><label>From hour<input id="op-hour-start" type="number" min="0" max="23" value="16"></label><label>Until hour (exclusive)<input id="op-hour-end" type="number" min="1" max="24" value="21"></label></div><p class="muted">24-hour Eastern time. 16–21 means 4–9 PM, based on order-open time.</p></div>
      <div data-op-kinds="forecast" hidden><label>Target day<input id="op-target" type="date" value="${day}"></label><label>Forecast metric<select id="op-forecast-metric">${metricOptions}<option value="evening_sales">Evening net sales (5 PM–midnight)</option></select></label><p class="muted">Uses stored history and the restricted numerical worker. No AI or paid search calls.</p></div>
      <button class="primary" type="submit">Run read-only query</button><p id="op-error" class="error" role="alert"></p>
      <small>Business date ${day} · America/New_York · week starts ${['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][capabilities.workweek_start]}</small>
      </form></section><section id="operator-result" class="card" aria-live="polite">${blank('Choose a query. Results will show the source, dates and any coverage gaps.')}</section></div>`;
  }

  function notes(items) { return items?.length ? `<ul class="operator-notes">${values(items,x=>`<li>${esc(x)}</li>`)}</ul>` : ''; }
  function table(headers, rows) { return `<div class="table-wrap"><table><thead><tr>${values(headers,h=>`<th scope="col">${esc(h)}</th>`)}</tr></thead><tbody>${rows}</tbody></table></div>`; }
  function receipts(data) {
    const rows = data.matches || [];
    return `<h2>${rows.length} matching receipt${rows.length===1?'':'s'}</h2><p class="source-note">${esc(data.start||data.day)}${data.end?' through '+esc(data.end):''} · ${esc(data.amount_basis||'Receipt total')}</p>` +
      (rows.length ? table(['Business date','Order','Checks','Combined total'],values(rows,r=>`<tr><td>${esc(r.business_date)}</td><td>${esc(r.order_number||'Not numbered')}</td><td>${values(r.checks,c=>`<div>Check ${esc(c.check_number||'—')}: ${money(c.total)}</div>`)}</td><td>${money(r.combined_order_total)}${r.net_sales==null?'':`<br><small>Net sales ${money(r.net_sales)}</small>`}</td></tr>`)) : blank(data.partial?'No matches in the completed portion. Unsearched data may contain matches.':'No matching active receipts returned in the searched coverage.')) +
      notes([data.notice,...(data.limitations||[]),...(data.incomplete_dates?.length?['Unsearched dates: '+data.incomplete_dates.join(', ')]:[])].filter(Boolean));
  }

  function sales(data) {
    const p=data.primary;
    return `<h2>${esc(p.metric.replaceAll('_',' '))}</h2><p>${esc(p.start)} through ${esc(p.end)}</p>`+
      values(p.sources,s=>{const format=s.unit.startsWith('USD')?money:quantity;const n=s.full_period_total??s.observed_total??s.covered_total;
        return `<article class="operator-source"><h3>${esc(s.source)}</h3><strong class="operator-value">${format(n)}</strong><span class="pill">${s.complete?'Complete':'Partial coverage'}</span><p>${esc(s.unit)} · ${s.covered_days}/${s.requested_days} completed days</p>${s.complete?'':notes(['Observed/covered subtotal only. A current day is still in progress.', 'Missing or unconfirmed: '+s.missing_or_unconfirmed_dates.join(', ')])}</article>`;})+
      (data.comparison ? `<h3>Comparison: ${esc(data.comparison_period.start)} through ${esc(data.comparison_period.end)}</h3>`+table(['Source','Change','Percent','Coverage'],values(data.comparison,c=>`<tr><td>${esc(c.source)}</td><td>${c.unit.startsWith('USD')?money(c.change):quantity(c.change)}</td><td>${c.percent_change==null?'Unavailable':quantity(c.percent_change)+'%'}</td><td>${c.complete?'Complete comparable periods':'Incomplete; no change inferred'}</td></tr>`)) : '')+
      notes(p.recovery_notes)+notes(p.limitations);
  }

  function forecast(data) {
    const format=data.unit?.startsWith('USD')?money:quantity;
    return `<h2>Forecast for ${esc(data.target)}</h2><p>${esc(data.unit)} · ${esc(data.forecast_type)}</p>`+
      (data.available?`<strong class="operator-value">${format(data.predicted)}</strong><p>Range ${format(data.low)}–${format(data.high)}</p><p class="source-note">${esc(data.interval_label)}</p>`:blank(data.reason||'Forecast unavailable.'))+
      (data.history?`<p>${data.history.usable_days} usable dates · ${esc(data.history.first||'—')} through ${esc(data.history.last||'—')}</p>`:'')+
      (data.backtest?`<h3>Rolling backtest</h3>${table(['Scored dates','MAE','WAPE','Bias'],`<tr><td>${data.backtest.n}</td><td>${format(data.backtest.mae)}</td><td>${data.backtest.wape==null?'Unavailable':quantity(data.backtest.wape)+'%'}</td><td>${format(data.backtest.bias)}</td></tr>`)}<p class="muted">Positive bias means overprediction. These are retrospective error estimates.</p>`:'')+
      (data.recent_backtest_dates?.length?table(['Past date','Predicted','Actual'],values(data.recent_backtest_dates,r=>`<tr><td>${esc(r.day)}</td><td>${format(r.predicted)}</td><td>${format(r.actual)}</td></tr>`)):'')+
      notes(data.limitations);
  }

  function render(result) {
    const d=result.data;
    let content=result.kind==='sales'&&d.primary?sales(d):result.kind==='forecast'?forecast(d):
      ['orders','receipt'].includes(result.kind)?receipts(d):result.kind==='channels'&&d.channels?
      `<h2>Sales by ordering channel</h2><p>${esc(d.business_date)} · ${d.start_hour}:00–${d.end_hour}:00 Eastern</p>`+
      table(['Channel','Net sales','Orders'],values(d.channels,r=>`<tr><td>${esc(r.label)}</td><td>${money(r.net_sales)}${r.exact?'':' (partial)'}</td><td>${quantity(r.orders)}</td></tr>`))+notes(d.limitations):'';
    return `<div class="card-head"><span class="pill">${esc(result.status)}</span><small>${esc(result.generated_at)}</small></div>`+
      notes(result.warnings)+content+`<p class="source-note">${esc(d.source||'Operator service')} · Reference ${esc(result.request_id)} · ${quantity(result.elapsed_ms||0)} ms</p>`;
  }

  function bind() {
    const form=$('#operator-form'); if(!form)return;
    const update=()=>{const kind=$('#op-kind').value;form.querySelectorAll('[data-op-kinds]').forEach(e=>e.hidden=!e.dataset.opKinds.split(' ').includes(kind));$('#op-custom').hidden=$('#op-period').value!=='custom';};
    $('#op-kind').onchange=update;$('#op-period').onchange=update;update();
    form.onsubmit=async event=>{event.preventDefault();await action(event.submitter,async()=>{
      $('#op-error').textContent='';const kind=$('#op-kind').value;let body={kind};
      if(['sales','orders'].includes(kind)){body.period=$('#op-period').value;if(body.period==='custom'){body.start=$('#op-start').value;body.end=$('#op-end').value;}}
      if(kind==='sales')Object.assign(body,{metric:$('#op-sales-metric').value,compare:$('#op-compare').value});
      if(kind==='orders')Object.assign(body,{minimum:Number($('#op-min').value),maximum:Number($('#op-max').value),basis:$('#op-basis').value});
      if(kind==='receipt')Object.assign(body,{day:$('#op-receipt-day').value,reference:$('#op-reference').value.trim()});
      if(kind==='channels')Object.assign(body,{day:$('#op-channel-day').value,start_hour:Number($('#op-hour-start').value),end_hour:Number($('#op-hour-end').value)});
      if(kind==='forecast')Object.assign(body,{target:$('#op-target').value,metric:$('#op-forecast-metric').value});
      const target=$('#operator-result');target.innerHTML=blank('Reading verified sources. This may take up to two minutes.');
      try{const result=await api('operator/query','POST',body);if(target.isConnected)target.innerHTML=render(result);}
      catch(error){if(target.isConnected)target.innerHTML=blank(error.message);$('#op-error')?.replaceChildren(document.createTextNode(error.message));}
    });};
  }
  return {page,bind};
})();
