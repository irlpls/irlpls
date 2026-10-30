/* irlpls call pane, one renderer for Find data and the landing's screen 3, in two layers (the owner, 2026-10-30):
   1. "Your agent → irlpls": the one uniform irlpls call, the same for every provider (MCP irlpls_request / the Python
      SDK's irlpls.request), and the plan irlpls returns (provider, readiness, price, what the policy allows);
   2. "What irlpls sends for you": the wrapper's translation, the provider's own call, folded to one line until opened.
   Layer 2 is
   the reference library's prepared call for a provider (catalog.json providers[id].ready_call, built by
   build.py from the adapter's prepare()), filled with this ask: {NEED} the ask's words, {STREET_ADDRESS} a street address
   when the ask has one, {PLACE} the place, {LAT}/{LON} the point when this page can map it (a call shown for a named
   example place keeps its own box). Credentials are only ever named: the HTTP view shows the placeholder ({KEY_NAME}),
   curl and Python read it from the environment. Nothing is sent from this page.

   IRLCall.html(opts) -> string     opts: {provider, readiness: {level|state, line}, price, rc, text, req, place, street,
                                           showLine (put the readiness line in the pane), docs (when rc is missing)}
   IRLCall.bind(root)               wires the tabs (HTTP / curl / Python) and Copy in every pane under root
   IRLCall.variants(opts)           {http, curl, python} as plain text (tests, copy)
   Every value is escaped; the highlighter is a few regexes, no dependency. */
(function(){
function E(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
var CSS='.ircp{margin:10px 0 0;padding:12px 12px 6px;border-radius:20px;background:rgba(255,255,255,.72);border:1px solid rgba(255,255,255,.9);box-shadow:0 8px 30px rgba(20,30,50,.08),inset 0 1px 0 #fff;min-width:0;max-width:100%}'+
 '.ircp-h{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;min-width:0}'+
 '.ircp-n{font:600 15px/1.3 var(--sans,Inter,system-ui,sans-serif);color:var(--fg,#101418);min-width:0;overflow-wrap:anywhere}'+
 '.ircp-r{display:inline-flex;align-items:center;padding:3px 9px;border-radius:999px;border:1px solid var(--line2,rgba(20,30,50,.16));font:600 12px/1.2 var(--sans,Inter,system-ui,sans-serif);color:var(--fg2,#3d4654);white-space:nowrap}'+
 '.ircp-r.ok{border-color:transparent;background:rgba(10,110,52,.1);color:var(--live,#0a6e34)}'+
 '.ircp-p{margin-left:auto;font:500 13px/1.3 var(--sans,Inter,system-ui,sans-serif);color:var(--fg2,#3d4654);white-space:nowrap}'+
 '.ircp-rl{margin:4px 0 0;font:500 13px/1.4 var(--sans,Inter,system-ui,sans-serif);color:var(--fg2,#3d4654)}'+
 '.ircp-k{margin:8px 0 0;font:600 12px/1.3 var(--sans,Inter,system-ui,sans-serif);letter-spacing:.06em;text-transform:uppercase;color:var(--fg3,#5b6574)}'+
 '.ircp-st{margin-left:6px;padding:2px 7px;border-radius:999px;background:rgba(20,30,50,.06);letter-spacing:0;text-transform:none;font-weight:600;color:var(--fg2,#3d4654)}.ircp-st.ok{background:rgba(10,110,52,.1);color:var(--live,#0a6e34)}'+
 '.ircp-bar{display:flex;align-items:center;justify-content:space-between;gap:6px;margin:2px 0 0}'+
 '.ircp-tabs{display:flex;align-items:center}'+
 '.ircp-bar button{min-height:44px;min-width:44px;margin:0;padding:0 2px;border:0;background:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font:600 13px/1 var(--sans,Inter,system-ui,sans-serif);color:var(--fg3,#5b6574)}'+
 '.ircp-bar button>span{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border-radius:999px;transition:background .15s,color .15s}'+
 '.ircp-tabs button[aria-selected="true"]>span{background:var(--fg,#101418);color:#fff}'+
 '@media(hover:hover){.ircp-bar button:hover>span{color:var(--fg,#101418)}.ircp-tabs button[aria-selected="true"]:hover>span{color:#fff}.ircp-cp:hover>span{background:rgba(20,30,50,.05)}}'+
 '.ircp-bar button:focus-visible{outline:none}.ircp-bar button:focus-visible>span{outline:2px solid var(--pol,#1b5fd6);outline-offset:1px}'+
 '.ircp-cp>span,.ircp-cp1>span{border:1px solid var(--line2,rgba(20,30,50,.16))}.ircp-cp svg{flex:none}'+
 '.ircp pre.ircp-code{display:block;margin:2px 0 0;padding:14px 16px;border:0;border-radius:14px;background:#0f1720;color:#e6edf3;font:500 12.5px/1.65 ui-monospace,"SF Mono",Menlo,Consolas,monospace;white-space:pre;overflow-wrap:normal;word-break:normal;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain;max-width:100%;tab-size:2;box-shadow:inset 0 0 0 1px rgba(255,255,255,.06)}'+
 '.ircp pre.ircp-code[hidden]{display:none}'+
 '.ircp pre.ircp-code:focus-visible{outline:2px solid var(--pol,#1b5fd6);outline-offset:2px}'+
 '.ircp .m{display:inline-block;padding:0 7px;border-radius:6px;font-weight:700;letter-spacing:.02em}'+
 '.ircp .m-get{background:rgba(34,197,94,.16);color:#86efac}.ircp .m-post{background:rgba(59,130,246,.2);color:#93c5fd}.ircp .m-x{background:rgba(245,158,11,.18);color:#fcd34d}'+
 '.ircp .t-host,.ircp .t-q,.ircp .t-flag,.ircp .t-p{color:#8b98a9}.ircp .t-path{color:#f0f6fc}.ircp .t-qk{color:#79c0ff}.ircp .t-qv{color:#a5d6ff}'+
 '.ircp .t-hk{color:#d2a8ff}.ircp .t-hv{color:#c9d1d9}.ircp .t-key{color:#79c0ff}.ircp .t-str{color:#a5d6ff}.ircp .t-num{color:#f2cc60}.ircp .t-lit,.ircp .t-kw{color:#ff7b72}.ircp .t-fn{color:#d2a8ff}'+
 '.ircp .t-ph{color:#fde68a;background:rgba(250,204,21,.13);border-radius:4px;box-shadow:inset 0 0 0 1px rgba(250,204,21,.32)}'+
 '.ircp-l{margin:10px 0 0;padding:0 0 0 18px;font:500 13px/1.45 var(--sans,Inter,system-ui,sans-serif);color:var(--fg2,#3d4654)}'+
 '.ircp-l b{font-weight:600;color:var(--fg,#101418);overflow-wrap:anywhere}.ircp-l a{display:inline-block;padding:13px 0;margin:-13px 0;color:var(--pol,#1b5fd6);overflow-wrap:anywhere}'+
 '.ircp-f{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:0 12px;margin:2px 0 0}'+
 '.ircp-gk{display:inline-flex;align-items:center;gap:4px;min-height:44px;font:600 14px var(--sans,Inter,system-ui,sans-serif);color:var(--pol,#1b5fd6);text-decoration:none}'+
 '@media(hover:hover){.ircp-gk:hover{text-decoration:underline;text-underline-offset:3px}}'+
 '.ircp-a{margin:8px 0 0}.ircp-plan{margin:8px 0 0;font:500 13px/1.45 var(--sans,Inter,system-ui,sans-serif);color:var(--fg2,#3d4654)}.ircp-plan b{color:var(--fg,#101418)}'+
 '.ircp-kl{margin:4px 0 0;font:500 12.5px/1.4 var(--sans,Inter,system-ui,sans-serif);color:var(--fg3,#5b6574)}'+
 '.ircp-b{margin:8px 0 0;border-top:1px solid var(--line,rgba(20,30,50,.09))}'+
 '.ircp-x{display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;width:100%;min-height:44px;margin:0;padding:4px 0 0;border:0;background:none;cursor:pointer;text-align:left;font:600 12px/1.3 var(--sans,Inter,system-ui,sans-serif);letter-spacing:.06em;text-transform:uppercase;color:var(--fg3,#5b6574)}'+
 '.ircp-x .xl{flex:1 1 auto;min-width:0}.ircp-x .ircp-st{white-space:nowrap}.ircp-x .xu{order:3;flex:1 0 100%;min-width:0;padding-bottom:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:500 12px/1.3 ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:0;text-transform:none;color:var(--fg2,#3d4654)}'+
 '.ircp-x .m{background:rgba(10,110,52,.1);color:var(--live,#0a6e34);font:700 11px/1.5 ui-monospace,"SF Mono",Menlo,monospace;margin-right:6px}'+
 '.ircp-x svg{flex:none;transition:transform .2s ease}.ircp-x[aria-expanded="true"] svg{transform:rotate(90deg)}'+
 '.ircp-x:focus-visible{outline:2px solid var(--pol,#1b5fd6);outline-offset:2px;border-radius:8px}'+
 '.ircp-bb[hidden]{display:none}'+
 '.ircp-c{margin:-2px 0 6px;font:500 12px/1.35 var(--sans,Inter,system-ui,sans-serif);color:var(--fg3,#5b6574)}'+
 '.ircp-q{margin:0;padding:12px 0;font:500 13px/1.4 var(--sans,Inter,system-ui,sans-serif);color:var(--fg3,#5b6574)}';
function css(){if(document.getElementById('ircp-css'))return;var s=document.createElement('style');s.id='ircp-css';s.textContent=CSS;(document.head||document.documentElement).appendChild(s)}

/* ---------- segments: [text, class] pairs, so one pass gives both the plain text (copy) and the highlighted HTML ---------- */
function Seg(){this.s=[]}
Seg.prototype.p=function(t,c){if(t!=='')this.s.push([String(t),c||''])};
Seg.prototype.text=function(){return this.s.map(function(x){return x[0]}).join('')};
Seg.prototype.html=function(){return this.s.map(function(x){return x[1]?'<span class="'+x[1]+'">'+E(x[0])+'</span>':E(x[0])}).join('')};
/* a string with {PLACEHOLDER} tokens (and $ENV / ${ENV} in shell) picked out */
var PH=/(\{[A-Z][A-Z0-9_]*\}|\$\{[A-Z][A-Z0-9_]*\}|\$[A-Z][A-Z0-9_]*)/;
function ph(S,t,c){String(t).split(PH).forEach(function(x,i){if(i%2)S.p(x,'t-ph');else S.p(x,c)})}
/* a URL: scheme and host dim, path bright, query keys and values apart */
function url(S,u){var m=/^(https?:\/\/[^\/?#]+)([^?#]*)(\?[^#]*)?(#.*)?$/.exec(u);
  if(!m){var n=/^(\{[A-Z0-9_]+\})([^?#]*)(\?[^#]*)?$/.exec(u);if(!n){ph(S,u,'t-path');return}m=[u,n[1],n[2],n[3],''];}
  ph(S,m[1],'t-host');ph(S,m[2],'t-path');
  if(m[3]){S.p('?','t-q');m[3].slice(1).split('&').forEach(function(kv,i){if(i)S.p('&','t-q');var j=kv.indexOf('=');
    if(j<0){ph(S,kv,'t-qk');return}ph(S,kv.slice(0,j),'t-qk');S.p('=','t-q');ph(S,kv.slice(j+1),'t-qv')})}
  if(m[4])S.p(m[4],'t-q')}
/* JSON text (already pretty-printed): keys, strings, numbers, true/false/null */
var JT=/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
function json(S,t,lit){var i=0,m;JT.lastIndex=0;
  while((m=JT.exec(t))){S.p(t.slice(i,m.index),'t-p');
    if(m[1]){if(m[2]){S.p(m[1],'t-key');S.p(m[2],'t-p')}else ph(S,m[1],'t-str')}
    else if(m[3])S.p(lit?lit[m[3]]:m[3],'t-lit');else S.p(m[4],'t-num');i=JT.lastIndex}
  S.p(t.slice(i),'t-p')}
function method(S,m){m=String(m||'GET').toUpperCase();S.p(m,'m '+(m==='GET'?'m-get':m==='POST'?'m-post':'m-x'))}

/* ---------- filling the call with the ask ---------- */
function J(v){return JSON.stringify(String(v)).slice(1,-1)}  /* the ask goes inside JSON strings */
function filled(o){var rc=o.rc,req=o.req||{},pl=o.place||{missing:true},text=o.text||'',st=o.street;
  var ll=!pl.missing&&pl.lat!=null&&!rc.example,where=req.place||(!pl.missing&&pl.name)||'',sa=st?String(st[0]||st).replace(/[\s,.]+$/,''):'';
  function f(s,enc){s=String(s);var q=enc?encodeURIComponent:J;s=s.replace(/\{NEED\}/g,q(text));if(sa)s=s.replace(/\{STREET_ADDRESS\}/g,q(sa));if(where)s=s.replace(/\{PLACE\}/g,q(where));
    return ll?s.replace(/"\{LAT\}"|\{LAT\}/g,pl.lat.toFixed(4)).replace(/"\{LON\}"|\{LON\}/g,pl.lon.toFixed(4)):s}
  var h=rc.headers||{},hd=Object.keys(h).map(function(k){return[k,f(h[k])]}),b=rc.body,bt='',bo;
  if(b!=null){bt=typeof b==='string'?f(b):f(JSON.stringify(b,null,2));try{bo=JSON.parse(bt)}catch(e){bo=undefined}}
  return{method:String(rc.method||'GET').toUpperCase(),url:f(rc.url||'',true),headers:hd,body:bt,obj:bo,ll:ll,key:((rc.auth||{}).key_name)||''}}

/* ---------- the three views ---------- */
function vHttp(c){var S=new Seg();method(S,c.method);S.p(' ');url(S,c.url);
  c.headers.forEach(function(h){S.p('\n');S.p(h[0],'t-hk');S.p(': ','t-p');ph(S,h[1],'t-hv')});
  if(c.body){S.p('\n\n');if(c.obj!==undefined)json(S,c.body);else ph(S,c.body,'t-str')}return S}
/* curl: the key from the environment ("$KEY"), everything else single-quoted */
function sq(t){return "'"+String(t).replace(/'/g,"'\\''")+"'"}
function dq(t,key){var x=String(t).replace(/[\\"$`]/g,'\\$&');if(key)x=x.split('{'+key+'}').join('${'+key+'}');return '"'+x+'"'}
function vCurl(c){var S=new Seg(),k=c.key,u=k&&c.url.indexOf('{'+k+'}')>-1;S.p('curl','t-fn');
  if(c.method!=='GET'){S.p(' ');S.p('-X','t-flag');S.p(' ');method(S,c.method)}
  S.p(' ');if(u)ph(S,dq(c.url,k),'t-str');else{S.p("'",'t-str');url(S,c.url);S.p("'",'t-str')}
  c.headers.forEach(function(h){S.p(' \\\n  ');S.p('-H','t-flag');S.p(' ');var v=h[0]+': '+h[1];
    if(k&&v.indexOf('{'+k+'}')>-1)ph(S,dq(v,k),'t-str');else ph(S,sq(v),'t-str')});
  if(c.body){S.p(' \\\n  ');S.p('--data','t-flag');S.p(' ');
    if(c.obj!==undefined&&c.body.indexOf("'")<0){S.p("'",'t-str');json(S,c.body);S.p("'",'t-str')}else ph(S,sq(c.body),'t-str')}
  return S}
/* Python (requests): the key from os.environ, the body as a Python literal */
function vPy(c){var S=new Seg(),k=c.key,I='    ';
  function pstr(t){t=String(t);if(!(k&&t.indexOf('{'+k+'}')>-1)){ph(S,JSON.stringify(t),'t-str');return}
    var any=false,join=function(){if(any)S.p(' + ','t-p');any=true};
    t.split('{'+k+'}').forEach(function(x,i){if(i){join();S.p('os','t-fn');S.p('.environ[','t-p');S.p(JSON.stringify(k),'t-ph');S.p(']','t-p')}
      if(x){join();ph(S,JSON.stringify(x),'t-str')}})}
  function lit(v,ind){if(v===null){S.p('None','t-lit');return}if(v===true||v===false){S.p(v?'True':'False','t-lit');return}
    if(typeof v==='number'){S.p(String(v),'t-num');return}if(typeof v==='string'){pstr(v);return}
    var arr=Array.isArray(v),ks=arr?v:Object.keys(v);if(!ks.length){S.p(arr?'[]':'{}','t-p');return}
    S.p(arr?'[':'{','t-p');ks.forEach(function(x,i){S.p('\n'+ind+I);if(arr)lit(x,ind+I);else{S.p(JSON.stringify(x),'t-key');S.p(': ','t-p');lit(v[x],ind+I)}S.p(',','t-p')});
    S.p('\n'+ind+(arr?']':'}'),'t-p')}
  S.p('import','t-kw');S.p(' os\n');S.p('import','t-kw');S.p(' requests\n\nr = requests.');S.p(c.method.toLowerCase(),'t-fn');S.p('(\n'+I);pstr(c.url);S.p(',');
  if(c.headers.length){S.p('\n'+I+'headers=');var ho={};c.headers.forEach(function(h){ho[h[0]]=h[1]});lit(ho,I);S.p(',')}
  if(c.body){S.p('\n'+I+(c.obj!==undefined?'json':'data')+'=');if(c.obj!==undefined)lit(c.obj,I);else pstr(c.body);S.p(',')}
  S.p('\n'+I+'timeout=');S.p('30','t-num');S.p(',\n)\nr.');S.p('raise_for_status','t-fn');S.p('()\n');S.p('print','t-fn');S.p('(r.');S.p('json','t-fn');S.p('())');return S}

/* layer 1: the uniform irlpls call (the same shape for every provider; the policy spends nothing on its own) */
function vAsk(o,kind){var S=new Seg(),need=JSON.stringify(String(o.text||''));
  if(kind==='mcp'){S.p('irlpls_request','t-fn');S.p('(\n  ','t-p');S.p('need','t-key');S.p('=','t-p');S.p(need,'t-str');S.p(',\n  ','t-p');S.p('policy','t-key');S.p('={','t-p');S.p('"max_order_usd"','t-key');S.p(': ','t-p');S.p('0','t-num');S.p('}\n)','t-p');return S}
  S.p('import','t-kw');S.p(' irlpls\n\njob = irlpls.');S.p('request','t-fn');S.p('(\n    ');S.p(need,'t-str');S.p(',\n    policy={');S.p('"max_order_usd"','t-key');S.p(': ');S.p('0','t-num');S.p('},\n)\n');
  S.p('print','t-fn');S.p('(job.plan[0])');return S}
function variants(o){var c=filled(o);return{http:vHttp(c).text(),curl:vCurl(c).text(),python:vPy(c).text(),c:c}}
var PILL={ready_now:['Ready now',1],one_consent:['One sign-up'],account_first:['Account first'],contract:['Contract']};
var CHEV='<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3l5 5-5 5"/></svg>';
var COPY='<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8"/><path d="M10.5 3.5v-.3A1.7 1.7 0 0 0 8.8 1.5H3.2A1.7 1.7 0 0 0 1.5 3.2v5.6a1.7 1.7 0 0 0 1.7 1.7h.3"/></svg>';
var n=0;
function host(u){return String(u).replace(/^https?:\/\//,'').replace(/\?.*$/,'')}
function tabs(id,list,cpLabel,cls){return '<div class="ircp-bar"><div class="ircp-tabs" role="tablist" aria-label="Show the call as">'+
    list.map(function(t,i){return '<button type="button" role="tab" id="'+id+'-t'+t[0]+'" aria-controls="'+id+'-'+t[0]+'" aria-selected="'+(i?'false':'true')+'" tabindex="'+(i?'-1':'0')+'" data-v="'+t[0]+'"><span>'+t[1]+'</span></button>'}).join('')+
    '</div><button type="button" class="'+cls+'" aria-label="'+E(cpLabel)+'"><span>'+COPY+'<b style="font-weight:600">Copy</b></span></button></div>'}
function html(o){css();o=o||{};var rd=o.readiness||{},lv=rd.level||rd.state,pill=PILL[lv],rc=o.rc,a=(rc&&rc.auth)||{};
  var h='<div class="ircp" data-ircp>';
  if(o.provider||pill||o.price)h+='<div class="ircp-h">'+(o.provider?'<span class="ircp-n">'+E(o.provider)+'</span>':'')+
    (pill?'<span class="ircp-r'+(pill[1]?' ok':'')+'"'+(rd.line?' title="'+E(rd.line)+'"':'')+'>'+E(pill[0])+'</span>':'')+(o.price?'<span class="ircp-p">'+E(o.price)+'</span>':'')+'</div>';
  if(o.showLine&&rd.line)h+='<p class="ircp-rl">'+E(rd.line)+'</p>';
  /* layer 1: your agent asks irlpls, the same way for every provider */
  var id1='ircp'+(++n),A={mcp:vAsk(o,'mcp'),sdk:vAsk(o,'sdk')};
  h+='<div class="ircp-a" data-grp><p class="ircp-k">Your agent → irlpls</p>'+tabs(id1,[['mcp','MCP'],['sdk','SDK']],'Copy the irlpls call','ircp-cp1')+
    ['mcp','sdk'].map(function(v,i){return '<pre class="ircp-code ircp-ask" id="'+id1+'-'+v+'" role="tabpanel" aria-labelledby="'+id1+'-t'+v+'" tabindex="0" data-copy="'+E(A[v].text())+'"'+(i?' hidden':'')+' aria-label="The irlpls call">'+A[v].html()+'</pre>'}).join('')+
    (o.provider?'<p class="ircp-plan">irlpls plans: <b>'+E(o.provider)+'</b>'+(pill?' · '+E(pill[0]):'')+(o.free?'. Free, so it runs within the policy.':(o.price?' · '+E(o.price):'')+'. Policy max_order_usd 0: nothing paid runs without your approval.')+'</p>':'')+
    (a.key_name&&o.provider?'<p class="ircp-kl">Runs in your agent’s irlpls with your '+E(o.provider)+' key.</p>':'')+'</div>';
  if(!rc){var d=o.docs;return h+'<p class="ircp-q">No ready call for this provider yet.'+(d?' <a class="ircp-gk" href="'+E(d)+'" rel="noopener">Its API docs</a>':'')+'</p></div>'}
  /* layer 2: what irlpls sends the provider for you (the wrapper's translation), folded to one line until opened */
  var c=filled(o),V={http:vHttp(c),curl:vCurl(c),python:vPy(c)},id='ircp'+(++n),code=V.http.text(),ok=o.ran&&o.ran.status>=200&&o.ran.status<300;
  h+='<div class="ircp-b"><button type="button" class="ircp-x" aria-expanded="'+(o.open?'true':'false')+'" aria-controls="'+id+'-b"><span class="xl">'+E(o.kicker||'What irlpls sends for you')+'</span>'+
    '<span class="xu"><span class="m">'+E(c.method)+'</span>'+E(host(c.url))+'</span>'+(o.ran?'<span class="ircp-st'+(ok?' ok':'')+'">'+E(o.ran.status>0?o.ran.status+' · '+o.ran.ms+' ms':o.ran.status<0?'failed':'running…')+'</span>':'')+CHEV+'</button>'+(o.cache?'<p class="ircp-c">'+E(o.cache)+'</p>':'')+
    '<div class="ircp-bb" id="'+id+'-b" data-grp'+(o.open?'':' hidden')+'>'+tabs(id,[['http','HTTP'],['curl','curl'],['python','Python']],'Copy the HTTP call','ircp-cp');
  ['http','curl','python'].forEach(function(v,i){h+='<pre class="ircp-code'+(i?'':' call')+'" id="'+id+'-'+v+'" role="tabpanel" aria-labelledby="'+id+'-t'+v+'" tabindex="0" data-copy="'+E(V[v].text())+'"'+(i?' hidden':'')+' aria-label="The provider call">'+V[v].html()+'</pre>'});
  var lines=[];
  if(a.key_name)lines.push('<b>'+E(a.key_name)+'</b>: '+(a.get_key_url?'get it at '+E(a.get_key_url.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,'')):'your key')+'.');
  (rc.placeholders||[]).filter(function(x){return code.indexOf('{'+x.name+'}')>-1}).slice(0,3).forEach(function(x){lines.push('<b>'+E(x.name)+'</b>: '+E(String(x.what||'').replace(/\s*\(\{\w+\}[^)]*\)/g,''))+'.')});
  if(!c.ll&&/\{LAT\}|\{LON\}/.test(code))lines.push('<b>LAT, LON</b>: your place, as numbers.');
  if(rc.example)lines.push('Shown for '+E(rc.example)+'; your agent’s call uses your place.');
  if(!rc.safe_to_run_without_approval)lines.push('Needs your approval: it may cost money or send someone.');
  if(lines.length)h+='<ul class="ircp-l cl">'+lines.map(function(l){return '<li>'+l+'</li>'}).join('')+'</ul>';
  h+='</div></div>';
  var note=o.note!=null?o.note:'Nothing is sent from this page.';
  if(a.get_key_url||note)h+='<div class="ircp-f">'+(a.get_key_url?'<a class="ircp-gk" href="'+E(a.get_key_url)+'" rel="noopener">Get a key'+CHEV+'</a>':'')+(note?'<p class="ircp-q bq">'+E(note)+'</p>':'')+'</div>';
  return h+'</div>'}

function copyText(t){if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(t).catch(function(){return fallback(t)});return Promise.resolve(fallback(t))}
function fallback(t){var x=document.createElement('textarea');x.value=t;x.setAttribute('readonly','');x.style.cssText='position:fixed;top:0;left:0;opacity:0;pointer-events:none';document.body.appendChild(x);x.select();
  var ok=false;try{ok=document.execCommand('copy')}catch(e){}x.remove();if(!ok)throw new Error('copy failed');return true}
function bind(root){(root||document).querySelectorAll('[data-ircp]').forEach(function(p){if(p.__ircp)return;p.__ircp=1;
  /* layer 2 folds and unfolds */
  var x=p.querySelector('.ircp-x');if(x)x.onclick=function(){var o=x.getAttribute('aria-expanded')!=='true';x.setAttribute('aria-expanded',o?'true':'false');document.getElementById(x.getAttribute('aria-controls')).hidden=!o};
  /* each layer: its own tabs and Copy */
  [].forEach.call(p.querySelectorAll('[data-grp]'),function(g){var tabs=[].slice.call(g.querySelectorAll('[role=tab]')),cp=g.querySelector('.ircp-cp, .ircp-cp1'),lab=cp&&cp.querySelector('b'),tm=0;
    function cur(){return g.querySelector('pre.ircp-code:not([hidden])')}
    function sel(t,focus){tabs.forEach(function(b){var on=b===t;b.setAttribute('aria-selected',on?'true':'false');b.tabIndex=on?0:-1;document.getElementById(b.getAttribute('aria-controls')).hidden=!on});
      if(cp)cp.setAttribute('aria-label','Copy the '+t.textContent.trim()+' call');if(focus)t.focus()}
    tabs.forEach(function(b,i){b.onclick=function(){sel(b)};b.onkeydown=function(e){var k=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;if(!k)return;e.preventDefault();sel(tabs[(i+k+tabs.length)%tabs.length],true)}});
    if(cp)cp.onclick=function(){var pre=cur();if(!pre)return;clearTimeout(tm);
      copyText(pre.getAttribute('data-copy')).then(function(){lab.textContent='Copied'},function(){lab.textContent='Select and copy'}).then(function(){tm=setTimeout(function(){lab.textContent='Copy'},1600)})}})})}
window.IRLCall={html:html,bind:bind,variants:variants,fill:filled};
})();
