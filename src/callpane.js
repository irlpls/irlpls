/* irlpls call pane, one renderer for Find data and the landing's screen 3 (the owner, 2026-10-30: "Two nested things is
   weird. Too much here. Simplify and beautify."). One flat, calm three-step flow on a thin connector, like a mini timeline:
   1. Your agent → irlpls   the one uniform irlpls call as irlpls understood the ask (opts.req from IRLMatch.parse, opts.place):
                            need, and only the fields present (place, lat, lon, quantity, within_minutes, policy), with Copy.
   2. irlpls → matched      one line: provider · readiness pill · price (a keyed provider: "needs your key" + Get a key),
                            then the policy check against opts.card (budget, AI-training rights) when the ask sets one.
   3. irlpls → provider     one line: method pill, host+path (truncated), and status · ms in green for a call this page ran
                            ("not sent from this page" otherwise; a cache note as a muted suffix). Tapping it opens the full
                            request as one curl block with one Copy.
   The provider call is the reference library's prepared call (catalog.json providers[id].ready_call, built by build.py from
   the adapter's prepare()), filled with this ask: {NEED} the ask's words, {STREET_ADDRESS} a street address when the ask has
   one, {PLACE} the place, {LAT}/{LON} the point when this page can map it (a call shown for a named example place keeps its
   own box). Credentials are only ever named: curl reads them from the environment. Nothing is sent from this page.

   IRLCall.html(opts) -> string     opts: {provider, readiness: {level|state, line}, price, rc, text, req, place, street,
                                           policy (extra policy fields), card (for the policy check), ran: {status, ms}, cache (note),
                                           open (step 3 starts open), docs (when rc is missing)}
   IRLCall.bind(root)               wires step 3's fold and both Copy buttons in every pane under root
   IRLCall.variants(opts)           {http, curl, python} as plain text (tests)
   Every value is escaped; the highlighter is a few regexes, no dependency. */
(function(){
function E(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
var SANS='var(--sans,Inter,system-ui,sans-serif)',MONO='var(--mono,ui-monospace,"SF Mono",Menlo,monospace)';
var CSS='.ircp{margin:0;padding:0;min-width:0;max-width:100%;border:0;background:none;box-shadow:none}'+
 '.ircp-tl{list-style:none;margin:0;padding:0}'+
 /* the timeline: a small dot per step on one thin line */
 '.ircp-s{position:relative;min-width:0;padding:0 0 14px 20px}.ircp-s:last-child{padding-bottom:0}'+
 '.ircp-s:before{content:"";position:absolute;left:0;top:5px;width:7px;height:7px;border-radius:50%;background:#fff;box-shadow:inset 0 0 0 1.5px var(--fg3,#5b6574)}'+
 '.ircp-s:after{content:"";position:absolute;left:3px;top:15px;bottom:1px;width:1px;background:var(--line2,rgba(20,30,50,.16))}.ircp-s:last-child:after{display:none}'+
 '.ircp-s.ok:before{background:var(--live,#0a6e34);box-shadow:none}.ircp-s.bad:before{background:var(--bad,#b3261e);box-shadow:none}'+
 '.ircp-k{margin:0 0 4px;font:500 12px/17px '+SANS+';color:var(--fg3,#5b6574)}'+
 /* step 1: the irlpls call, one light code line, and a copy icon */
 '.ircp-row{display:flex;align-items:center;gap:2px;min-width:0}'+
 '.ircp-ask{flex:1 1 auto;min-width:0;display:block;margin:0;padding:7px 10px;border-radius:10px;background:#f4f6f9;font:500 12.5px/1.5 '+MONO+';color:var(--fg,#101418);white-space:pre-wrap;overflow-wrap:anywhere}'+
 '.ircp-ask .l0{display:block}.ircp-ask .ln{display:block;padding-left:4ch;text-indent:-2ch}'+
 '.ircp-i{flex:none;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;margin:-6px -10px -6px 0;padding:0;border:0;background:none;cursor:pointer;color:var(--fg3,#5b6574);border-radius:12px}'+
 '.ircp-i svg{display:block}.ircp-i .ck{display:none}.ircp-i.done .cp{display:none}.ircp-i.done .ck{display:block;color:var(--live,#0a6e34)}'+
 '@media(hover:hover){.ircp-i:hover{color:var(--fg,#101418)}}.ircp-i:focus-visible{outline:2px solid var(--pol,#1b5fd6);outline-offset:-4px}'+
 /* step 2: provider · readiness · price */
 '.ircp-m{display:flex;flex-wrap:wrap;align-items:center;gap:2px 6px;margin:0;font:500 14px/1.4 '+SANS+';color:var(--fg2,#3d4654)}'+
 '.ircp-n{font-weight:600;color:var(--fg,#101418);overflow-wrap:anywhere}.ircp-m i{font-style:normal;color:var(--fg3,#5b6574)}'+
 '.ircp-r{display:inline-flex;align-items:center;padding:1px 8px;border-radius:999px;background:rgba(20,30,50,.06);font:600 12px/1.5 '+SANS+';color:var(--fg2,#3d4654);white-space:nowrap}'+
 '.ircp-r.ok{background:rgba(10,110,52,.1);color:var(--live,#0a6e34)}'+
 '.ircp-p{white-space:nowrap}.ircp-nk{color:var(--fg3,#5b6574)}'+
 '.ircp-gk{display:inline-flex;align-items:center;min-height:44px;margin:0;font:600 14px '+SANS+';color:var(--pol,#1b5fd6);text-decoration:none;white-space:nowrap}'+
 '.ircp-gk2{display:flex;width:max-content;margin:-6px 0 -12px;font-weight:500}'+
 '@media(hover:hover){.ircp-gk:hover{text-decoration:underline;text-underline-offset:3px}}'+
 /* step 3: the provider call, one line that opens to the full request */
 '.ircp-x{display:flex;align-items:flex-start;gap:8px;width:100%;min-height:44px;margin:0 0 -12px;padding:0;border:0;background:none;cursor:pointer;text-align:left;font:500 12.5px/1.3 '+MONO+';color:var(--fg2,#3d4654)}'+
 '.ircp-x .xt{flex:1 1 auto;min-width:0}.ircp-x .xu{overflow-wrap:anywhere;margin-right:8px}.ircp-x .ircp-st{display:inline-block}'+
 '.ircp-x .ircp-st{flex:none;white-space:nowrap;font:600 12.5px/1.3 '+SANS+';color:var(--fg3,#5b6574)}.ircp-st.ok{color:var(--live,#0a6e34)}.ircp-st.bad{color:var(--bad,#b3261e)}.ircp-st.q{font-weight:500}'+
 '.ircp-c{font-weight:500;color:var(--fg3,#5b6574)}'+
 '.ircp-x .m{margin-top:-1px}.ircp-x svg{margin-top:3px;flex:none;color:var(--fg3,#5b6574);transition:transform .2s ease}.ircp-x[aria-expanded="true"] svg{transform:rotate(90deg)}'+
 '.ircp-x:focus-visible{outline:2px solid var(--pol,#1b5fd6);outline-offset:2px;border-radius:8px}'+
 '.ircp .m{flex:none;display:inline-block;padding:1px 6px;border-radius:6px;font:700 11px/1.5 '+MONO+';letter-spacing:.02em;background:rgba(20,30,50,.07);color:var(--fg2,#3d4654)}'+
 '.ircp .m-get{background:rgba(10,110,52,.1);color:var(--live,#0a6e34)}.ircp .m-post{background:rgba(27,95,214,.1);color:var(--pol,#1b5fd6)}'+
 '.ircp-bb{margin:10px 0 0}.ircp-bb[hidden]{display:none}'+
 '.ircp-bb .ircp-row{align-items:flex-start}.ircp-bb .ircp-i{margin-top:-4px}'+
 '.ircp pre.ircp-code{flex:1 1 auto;display:block;min-width:0;margin:0;padding:10px 12px;border:0;border-radius:10px;background:#f4f6f9;color:var(--fg,#101418);font:500 12.5px/1.6 '+MONO+';white-space:pre;overflow-wrap:normal;word-break:normal;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain;max-width:100%;tab-size:2}'+
 '.ircp pre.ircp-code:focus-visible{outline:2px solid var(--pol,#1b5fd6);outline-offset:2px}'+
 /* quiet highlighting: ink for what matters, grey for punctuation, the key to fill in marked */
 '.ircp .t-host,.ircp .t-q,.ircp .t-flag,.ircp .t-p,.ircp .t-lit,.ircp .t-kw{color:var(--fg3,#5b6574)}.ircp .t-fn{font-weight:600}.ircp .t-key,.ircp .t-qk,.ircp .t-hk{color:var(--fg2,#3d4654)}'+
 '.ircp .t-ph{background:rgba(250,204,21,.22);border-radius:4px;color:#7a5200}'+
 '.ircp-cks{display:flex;flex-direction:column;gap:2px;margin:4px 0 0;font:500 13px/1.4 '+SANS+';color:var(--fg3,#5b6574)}'+
 '.ircp-ck{display:flex;align-items:baseline;gap:6px}.ircp-ck:before{content:"";flex:none;width:5px;height:5px;border-radius:50%;background:currentColor;transform:translateY(-2px)}'+
 '.ircp-ck.ok{color:var(--live,#0a6e34)}.ircp-ck.bad{color:var(--bad,#b3261e)}'+
 '.ircp-l{margin:6px 0 0;font:500 12.5px/1.4 '+SANS+';color:var(--fg3,#5b6574)}'+
 '.ircp-q{margin:0;font:500 13px/1.4 '+SANS+';color:var(--fg3,#5b6574)}.ircp-q .ircp-gk{margin:0 0 0 6px}';
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


/* step 1: the irlpls call, as irlpls understood the ask (the page's parser, IRLMatch.parse, and the place it mapped): only
   the fields present, each a real irlpls_request parameter (place, lat, lon, within_minutes, quantity, policy). A budget in
   the words becomes policy max_order_usd; AI-training rights become policy need_ai_training. No fields: one line. */
var AIT=/\b(?:ai[- ]?training|train(?:ing)?\s+(?:an?\s+)?(?:ai|models?)|training rights)\b/i;
function askFields(o){var r=o.req||{},pl=o.place||{},f=[],pol={};
  /* a street address stays as written (its city's point would mislead, so no lat/lon then); else the place as mapped */
  var addr=/^\s*\d/.test(r.place||''),where=addr?r.place:(!pl.missing&&pl.name?pl.name:(r.place||''));
  var loc=[];if(where)loc.push(['place',JSON.stringify(String(where)),'t-str']);
  if(!addr&&!pl.missing&&pl.lat!=null&&pl.lon!=null){loc.push(['lat',(+pl.lat).toFixed(2),'t-num']);loc.push(['lon',(+pl.lon).toFixed(2),'t-num'])}
  if(loc.length)f.push(loc);
  var tq=[];if(r.quantity>1)tq.push(['quantity',String(r.quantity),'t-num']);if(r.within_minutes!=null)tq.push(['within_minutes',String(r.within_minutes),'t-num']);
  if(tq.length)f.push(tq);
  if(o.policy&&typeof o.policy==='object')for(var k in o.policy)pol[k]=o.policy[k];
  if(r.max_price_usd!=null&&pol.max_order_usd==null)pol.max_order_usd=r.max_price_usd;
  if(AIT.test(o.text||'')&&pol.need_ai_training==null)pol.need_ai_training=true;
  return{groups:f,policy:pol}}
function vAsk(o){var S=new Seg(),F=askFields(o),pk=Object.keys(F.policy),need=JSON.stringify(String(o.text||''));
  S.p('irlpls_request','t-fn');S.p('(','t-p');
  if(!F.groups.length&&!pk.length){S.p('need','t-key');S.p('=','t-p');S.p(need,'t-str');S.p(')','t-p');return S}
  S.p('\n  ');S.p('need','t-key');S.p('=','t-p');S.p(need,'t-str');
  F.groups.forEach(function(g){S.p(',','t-p');S.p('\n  ');g.forEach(function(x,i){if(i)S.p(', ','t-p');S.p(x[0],'t-key');S.p('=','t-p');S.p(x[1],x[2])})});
  if(pk.length){S.p(',','t-p');S.p('\n  ');S.p('policy','t-key');S.p('={','t-p');
    pk.forEach(function(k,i){if(i)S.p(', ','t-p');S.p(JSON.stringify(k),'t-key');S.p(': ','t-p');var v=F.policy[k];S.p(JSON.stringify(v),typeof v==='number'?'t-num':typeof v==='string'?'t-str':'t-lit')});S.p('}','t-p')}
  S.p('\n');S.p(')','t-p');return S}
/* the multi-line call as HTML lines with a hanging indent, so a long field wraps under itself on a phone (line breaks are
   always their own unclassed segments, so splitting at them never cuts a tag) */
function askHtml(S){var L=S.html().split('\n');if(L.length<2)return L[0];
  return L.map(function(l,i){return i&&i<L.length-1?'<span class="ln">'+l.replace(/^  /,'')+'</span>':'<span class="l0">'+l+'</span>'}).join('')}
/* step 2's policy check, against the matched card: the budget, and AI-training rights when asked for */
function money(v){return '$'+(+v).toLocaleString('en-US',{maximumFractionDigits:2})}
function checks(o){var c=o.card,F=askFields(o),P=F.policy,out=[];if(!c)return out;
  var pr=(c.pricing||{}).from_usd,cost=(c.access||{}).cost,b=P.max_order_usd;
  if(b!=null){if(typeof pr==='number'&&pr>0)out.push(pr<=b?['Within your '+money(b)+' budget','ok']:['Over your '+money(b)+' budget: from '+money(pr),'bad']);
    else if(cost==='free'||cost==='free with key')out.push(['Free, within your '+money(b)+' budget','ok']);
    else out.push(['No published price: irlpls asks before ordering','']);}
  if(P.need_ai_training){var ai=(c.licensing||{}).ai_training;
    out.push(ai==='allowed'?['AI training allowed','ok']:ai==='forbidden'?['AI training forbidden','bad']:['AI-training rights not stated: irlpls asks first',''])}
  return out}
function variants(o){var c=filled(o);return{http:vHttp(c).text(),curl:vCurl(c).text(),python:vPy(c).text(),c:c}}
var PILL={ready_now:['Ready now',1],one_consent:['One sign-up'],account_first:['Account first'],contract:['Contract']};
var CHEV='<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3l5 5-5 5"/></svg>';
var COPY='<svg class="cp" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8"/><path d="M10.5 3.5v-.3A1.7 1.7 0 0 0 8.8 1.5H3.2A1.7 1.7 0 0 0 1.5 3.2v5.6a1.7 1.7 0 0 0 1.7 1.7h.3"/></svg>'+
 '<svg class="ck" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 8.5 3.2 3L13 4.5"/></svg>';
var n=0;
function hostPath(u){return String(u).replace(/^https?:\/\//,'').replace(/[?#].*$/,'')}
/* the one-line view: the host, and "/…" for the rest (written out, never cut by the box) */
function shortUrl(u){var h=hostPath(u),i=h.indexOf('/');return i<0||i===h.length-1?h.replace(/\/$/,''):h.slice(0,i)+'/…'}
function copyBtn(cls,label,text){return '<button type="button" class="ircp-i '+cls+'" aria-label="'+E(label)+'" title="Copy" data-copy="'+E(text)+'">'+COPY+'</button>'}
function step(k,body,cls){return '<li class="ircp-s'+(cls?' '+cls:'')+'"><p class="ircp-k">'+k+'</p>'+body+'</li>'}
function html(o){css();o=o||{};var rd=o.readiness||{},pill=PILL[rd.level||rd.state],rc=o.rc,a=(rc&&rc.auth)||{},A=vAsk(o);
  var h='<div class="ircp" data-ircp><ol class="ircp-tl">';
  /* 1. your agent asks irlpls, the same way for every provider */
  h+=step('Your agent → irlpls','<div class="ircp-row"><code class="ircp-ask" aria-label="The irlpls call">'+askHtml(A)+'</code>'+copyBtn('ircp-cp1','Copy the irlpls call',A.text())+'</div>');
  /* 2. who irlpls matched: provider · readiness · price, and a key when one is needed */
  var m=[];if(o.provider)m.push('<b class="ircp-n">'+E(o.provider)+'</b>');
  if(pill)m.push('<span class="ircp-r'+(pill[1]?' ok':'')+'"'+(rd.line?' title="'+E(rd.line)+'"':'')+'>'+E(pill[0])+'</span>');
  if(o.price&&!/not stated/i.test(o.price))m.push('<span class="ircp-p">'+E(o.price)+'</span>');  /* an unstated price says nothing here */
  if(a.key_name)m.push('<span class="ircp-nk">needs your key</span>');
  var ck=checks(o).map(function(x){return '<span class="ircp-ck'+(x[1]?' '+x[1]:'')+'">'+E(x[0])+'</span>'}).join('');
  if(m.length)h+=step('irlpls → matched','<p class="ircp-m">'+m.join('<i aria-hidden="true">·</i>')+'</p>'+(ck?'<p class="ircp-cks">'+ck+'</p>':'')+
    (a.get_key_url?'<a class="ircp-gk ircp-gk2" href="'+E(a.get_key_url)+'" rel="noopener">Get a key</a>':''));
  /* 3. what irlpls sends the provider: one line, the full request (curl) when tapped */
  if(!rc){var d=o.docs;return h+step('irlpls → provider','<p class="ircp-q">No ready call yet.'+(d?'<a class="ircp-gk" href="'+E(d)+'" rel="noopener">API docs</a>':'')+'</p>')+'</ol></div>'}
  var c=filled(o),cu=vCurl(c),id='ircp'+(++n),r=o.ran,ok=r&&r.status>=200&&r.status<300,bad=r&&(r.status<0||r.status>=300);
  var st=r?(r.status>0?r.status+' · '+r.ms+' ms':r.status<0?'failed':'running…'):'not sent from this page';
  var cm=o.cache?/(\d+ (?:s|min)) old/.exec(o.cache):null;
  var line='<button type="button" class="ircp-x" aria-expanded="'+(o.open?'true':'false')+'" aria-controls="'+id+'" aria-label="'+E(c.method+' '+hostPath(c.url)+', '+st+'. Show the full request')+'">'+
    '<span class="m m-'+(c.method==='GET'?'get':c.method==='POST'?'post':'x')+'">'+E(c.method)+'</span><span class="xt"><span class="xu">'+E(shortUrl(c.url))+'</span> '+
    '<span class="ircp-st'+(ok?' ok':bad?' bad':r?'':' q')+'">'+E(st)+(o.cache?'<span class="ircp-c" title="'+E(o.cache)+'"> · cached'+(cm?' '+E(cm[1]):'')+'</span>':'')+'</span></span>'+CHEV+'</button>';
  var notes=[];if(rc.example)notes.push('Shown for '+rc.example+'; your agent’s call uses your place.');
  if(!rc.safe_to_run_without_approval)notes.push('Needs your approval: it may cost money or send someone.');
  var body='<div class="ircp-bb" id="'+id+'"'+(o.open?'':' hidden')+'><div class="ircp-row"><pre class="ircp-code call" tabindex="0" aria-label="The provider call, as curl">'+cu.html()+'</pre>'+copyBtn('ircp-cp','Copy the curl call',cu.text())+'</div>'+
    (notes.length?'<p class="ircp-l">'+E(notes.join(' '))+'</p>':'')+'</div>';
  return h+step('irlpls → provider',line+body,ok?'ok':bad?'bad':'')+'</ol></div>'}

function copyText(t){if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(t).catch(function(){return fallback(t)});return Promise.resolve(fallback(t))}
function fallback(t){var x=document.createElement('textarea');x.value=t;x.setAttribute('readonly','');x.style.cssText='position:fixed;top:0;left:0;opacity:0;pointer-events:none';document.body.appendChild(x);x.select();
  var ok=false;try{ok=document.execCommand('copy')}catch(e){}x.remove();if(!ok)throw new Error('copy failed');return true}
function bind(root){(root||document).querySelectorAll('[data-ircp]').forEach(function(p){if(p.__ircp)return;p.__ircp=1;
  var x=p.querySelector('.ircp-x');if(x)x.onclick=function(){var o=x.getAttribute('aria-expanded')!=='true';x.setAttribute('aria-expanded',o?'true':'false');document.getElementById(x.getAttribute('aria-controls')).hidden=!o};
  [].forEach.call(p.querySelectorAll('.ircp-i'),function(b){var tm=0,lab=b.getAttribute('aria-label');
    b.onclick=function(){clearTimeout(tm);copyText(b.getAttribute('data-copy')).then(function(){b.classList.add('done');b.setAttribute('aria-label','Copied')},function(){b.setAttribute('aria-label','Select and copy')})
      .then(function(){tm=setTimeout(function(){b.classList.remove('done');b.setAttribute('aria-label',lab)},1600)})}})})}
window.IRLCall={html:html,bind:bind,variants:variants,fill:filled};
})();
