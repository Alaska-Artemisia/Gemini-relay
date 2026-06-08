const http = require('http');
const crypto = require('crypto');
const SECRET = process.env.RELAY_SECRET || 'meandlia-gemini-2026';
const PORT = process.env.PORT || 3000;
const jobs = [];
const completed = [];
function auth(req) { return req.headers['x-relay-secret'] === SECRET; }
function body(req) { return new Promise((resolve) => { let d=''; req.on('data',c=>d+=c); req.on('end',()=>{ try{resolve(JSON.parse(d))}catch(e){resolve(d)} }); }); }
const server = http.createServer(async (req, res) => {
  const p = new URL(req.url,'http://x').pathname;
  if (req.method==='GET' && p==='/') { res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({status:'ok',pending:jobs.length})); return; }
  if (!auth(req)) { res.writeHead(401); res.end('Unauthorized'); return; }
  if (req.method==='POST' && p==='/job') { const job=await body(req); const id=crypto.randomUUID(); jobs.push({id,job,createdAt:new Date().toISOString()}); console.log('Job queued:',job.filename); res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({id,status:'queued'})); return; }
  if (req.method==='GET' && p==='/poll') { const pending=jobs.splice(0); res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify(pending)); return; }
  if (req.method==='POST' && p==='/done') { const r=await body(req); completed.unshift(r); if(completed.length>50)completed.pop(); res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({status:'ok'})); return; }
  if (req.method==='GET' && p==='/status') { res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({pending:jobs.length,completed:completed.slice(0,5)})); return; }
  res.writeHead(404); res.end('Not found');
});
server.listen(PORT, () => console.log('Gemini Relay on port', PORT));
