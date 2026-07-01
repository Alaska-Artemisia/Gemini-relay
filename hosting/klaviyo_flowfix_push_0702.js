(async () => {
  const RAW = 'https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/';
  const items = [
    {id:'Ubr8dz', file:'flowfix_w5_0702.html',  name:'W5',  marker:'martina-harbour-slow-holiday'},
    {id:'TwUcdS', file:'flowfix_w4_0702.html',  name:'W4',  marker:'First Piece Promise'},
    {id:'XPqgcR', file:'flowfix_w2_0702.html',  name:'W2',  marker:'1X5A6256-2.jpg'},
    {id:'XdxjGr', file:'flowfix_ba1_0702.html', name:'BA1', marker:'font-size:13px;font-weight:400;color:#9E4C58'},
    {id:'QQgMyU', file:'flowfix_ba2_0702.html', name:'BA2', marker:'font-size:13px;font-weight:400;color:#9E4C58'},
  ];
  const gc = n => (document.cookie.match('(^|;)\\s*'+n+'\\s*=\\s*([^;]+)')||[])[2];
  const csrf = gc('kl_csrf')||gc('csrftoken')||gc('csrf')||((document.querySelector('meta[name=csrf-token]')||{}).content)||'';
  const out = [];
  for (const it of items) {
    const row = {email: it.name, id: it.id, fetched:'', POST:'', verify:''};
    try {
      const html = await (await fetch(RAW+it.file, {cache:'no-store'})).text();
      row.fetched = html.length + 'ch';
      const headers = {'Content-Type':'application/x-www-form-urlencoded'};
      if (csrf) headers['X-CSRFToken'] = csrf;
      const res = await fetch('/ajax/email-editor/'+it.id+'/html', {
        method:'POST', credentials:'include', headers,
        body:'body='+encodeURIComponent(html)
      });
      row.POST = res.status;
      const back = await (await fetch('/ajax/email-editor/'+it.id+'/html', {cache:'no-store', credentials:'include'})).text();
      row.verify = back.includes(it.marker) ? 'OK ✅' : 'MISSING ⚠️';
    } catch(e) { row.POST = 'ERR: '+e.message; }
    out.push(row);
  }
  window.__flowfix = JSON.stringify(out, null, 2);
  console.table(out);
  try { copy(window.__flowfix); console.log('Results copied to clipboard — paste to Claude.'); }
  catch(e) { console.log('Run: copy(window.__flowfix)'); }
})();
