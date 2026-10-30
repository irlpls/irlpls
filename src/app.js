/* irlpls front end: the provider finder, Capture Card pages and compare.
   Card pages are rendered at build time (build.py card_article); the finder's sheet shows that same page, so a card
   has one renderer. This file adds what needs a request (fit and its gaps), the finder rows and compare.
   Every value shown comes from the card JSON; anything a card leaves out reads "Not stated". The helpers below
   mirror build.py, and tools/qa_pages.py checks both against cards/*.json. */
(function(){
var root=document.getElementById('root'),base=root.dataset.base||'';
function E(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
var L=window.IRLLabels,money=L.money,priceText=L.priceText;  /* the landing's words too (src/labels.js) */
var SHORT={human:'Person',application:'App',camera:'Camera',time:'Time',location:'Place',content:'Content',corroboration:'Cross-check'};
var DEFAULT=['time','location','content'];
function title(s){return String(s).replace(/\b\w/g,function(m){return m.toUpperCase()})}
function plainName(c){return c.name.replace(/\s*\(.*\)/,'')}
function modeText(c){var m=(c.delivery||{}).modes||[];return m.length>1?'Live, made to order':m[0]==='live'?'Live':m.length?'Made to order':'Not stated'}
function speed(c){var s=(c.delivery||{}).speed;return s&&s!=='unknown'?s:'Speed not stated'}
function where(c){var r=((c.coverage||{}).regions||[]).map(function(x){return x.split('(')[0].split(',')[0].trim()}).filter(Boolean);return (r.length>2?r.slice(0,2).join(', ')+' +'+(r.length-2):r.join(', '))||'Not stated'}
function right(v){return v==='allowed'?'Allowed':v==='forbidden'?'Not allowed':'Not stated'}
function keyText(c){var k=(c.access||{}).key_required,l=((c.api||{}).auth||[]).some(function(m){return m.type==='oauth2'&&(m.cimd||m.dynamic_registration)});return k==null?'Not stated':k?(l?'Required, or link an account':'Required'):'Not needed'}
/* the landing's eight stages (IRLMatch.stageLevels, mirrored by build.py stage_dots/stage_tally): dots + the short tally (IRLMatch.stageTally) */
var stageSays=L.stageSays;
function dots(c){var l=IRLMatch.stageLevels(c),T=IRLMatch.stageTitles;
  return '<span class="dots" role="img" aria-label="'+E(l.map(function(x,i){return T[i]+': '+stageSays(i,x)}).join('; '))+'">'+l.map(function(x){return '<i class="'+IRLMatch.stageMark(x)+'"></i>'}).join('')+'</span>'}
function tally(c){return IRLMatch.stageTally(c,true)}  /* a row, beside its eight dots: "n claimed · n evidenced" */
/* a fit in words, as on the landing; the number (out of 100) sits in the title */
function fitWord(s){return s.verdict==='strong'?'Good fit':'Partial fit'}
function fitTitle(s){return 'match '+s.total+'/100 for this request'}
function fmt(m){return m<60?m+' min':m<1440?Math.floor(m/60)+' h':Math.floor(m/1440)+' d'}
function regionName(cc){try{return new Intl.DisplayNames(['en'],{type:'region'}).of(cc.toUpperCase())}catch(e){return cc.toUpperCase()}}

/* the fit for one request, and what this provider can't do for it, in plain words */
/* proof gaps are not repeated here: the card's proof marks switch to the guarantees this request needs */
function gaps(s){var out=[];
  (s.gaps||[]).forEach(function(g){if(!/^(no proof|self-reported)/.test(g))out.push(g.charAt(0).toUpperCase()+g.slice(1))});
  return out}
function fitBlock(s,text){var g=gaps(s);
  return '<section class="fitb" aria-label="Fit for your request"><div class="fitn"><b title="'+fitTitle(s)+'">'+fitWord(s)+'</b><span>for “'+E(text)+'”</span></div>'+(g.length?'<ul>'+g.map(function(x){return '<li>'+E(x)+'</li>'}).join('')+'</ul>':'')+'</section>'}
function addFit(article,c,s,text){var at=article.querySelector('.spec');if(at)at.insertAdjacentHTML('beforebegin',fitBlock(s,text))}

var label=document.getElementById('label'),qx=new URLSearchParams(location.search).get('q')||'';
var needData=document.getElementById('grid')||document.getElementById('cmp')||(root.dataset.card&&qx);
if(!needData)return;  /* a card page with no request is complete as built */

fetch(base+'cards.json').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}).then(function(D){
  var C=D.cards.filter(function(c){return IRLMatch.inScope(c)});
  function byId(i){return C.filter(function(c){return c.id===i})[0]}
  function scored(c,text){if(!text)return null;var r=IRLMatch.parse(text,C),s=IRLMatch.score(c,r);s.req=r;return s}

  if(root.dataset.card&&label){var cc=byId(root.dataset.card),s=cc&&scored(cc,qx);if(s&&s.total!=null)addFit(label.querySelector('.cardv'),cc,s,qx)}
  if(document.getElementById('grid'))finder();
  if(document.getElementById('cmp'))compare();

  /* ---------- the finder ---------- */
  function finder(){
    var q=document.getElementById('q'),grid=document.getElementById('grid'),parsed=document.getElementById('parsed'),meta=document.getElementById('meta'),clr=document.getElementById('clr'),
        ex=document.getElementById('ex'),more=document.getElementById('more'),tm=null,lastFocus=null,pushed=false,showWeak=false,lastText=null,fb=[].slice.call(document.querySelectorAll('#filters button'));
    var P=new URLSearchParams(location.search),flt={free:P.get('free')==='1',live:P.get('live')==='1',commission:P.get('made')==='1'};
    function syncF(){fb.forEach(function(b){b.setAttribute('aria-pressed',String(!!flt[b.dataset.f]))})}syncF();
    var sheet=document.createElement('div');sheet.id='sheet';sheet.hidden=true;sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-labelledby','card-h');document.body.appendChild(sheet);
    function row(c,s,on){
      return '<li><a class="row" data-id="'+c.id+'" href="'+base+'c/'+c.id+'.html'+(on?'?q='+encodeURIComponent(q.value.trim()):'')+'">'+
        '<span class="main"><span class="nmr"><span class="nm">'+E(plainName(c))+(c.status==='provider-maintained'?'<span class="pmk"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>Published</span>':'')+'</span>'+(on?'<span class="fit'+(s.verdict==='strong'?'':' w')+'" title="'+fitTitle(s)+'">'+fitWord(s)+'</span>':'')+'</span>'+
        '<span class="sub">'+E(modeText(c))+' · '+E(speed(c))+' · '+E(priceText(c))+'</span></span>'+
        '<span class="proof">'+dots(c)+'<span class="tl">'+tally(c)+'</span></span></a></li>'}
    /* how the request was read: only the constraints (place, timing, budget, extra proof), in plain words.
       Topic words stay in the box, where the user typed them. */
    function readAs(req,any){var out=[],pn=req.state?title(req.state):req.country_code?regionName(req.country_code):req.regions.length?req.regions.map(title).join(', '):null;
      if(req.place)out.push(pn?(req.place.toLowerCase()===pn.toLowerCase()?pn:req.place+' → '+pn):req.place+': place not recognised, add a country');else if(pn)out.push(pn);
      if(any)out.push(req.mode==='live'?'live now':'made to order'+(req.within_minutes!=null?' within '+fmt(req.within_minutes):''));  /* no mode for an ask nothing matches (it is only the default) */
      if(req.max_price_usd!=null)out.push('under '+money(req.max_price_usd));
      var ex=req.require.filter(function(k){return DEFAULT.indexOf(k)<0});if(ex.length)out.push('needs evidence of: '+ex.map(function(k){return SHORT[k].toLowerCase()}).join(', '));
      return out}
    function render(){
      var text=q.value.trim(),req=IRLMatch.parse(text,C),on=text.length>0;clr.hidden=!on;ex.hidden=on;
      if(text!==lastText){showWeak=false;lastText=text}
      var all=C.map(function(c){return{c:c,s:on?IRLMatch.score(c,req):null}});
      var matched=all.filter(function(x){return !on||x.s.total>0});
      var ra=on?readAs(req,matched.length>0):[];parsed.innerHTML=ra.length?'<span class="sr">Read as: </span>'+ra.map(function(b){return '<span>'+E(b)+'</span>'}).join(''):'';parsed.hidden=!ra.length;
      var list=matched.filter(function(x){var m=x.c.delivery.modes;
        if(flt.free&&!IRLMatch.isFree(x.c))return false;if(flt.live&&m.indexOf('live')<0)return false;if(flt.commission&&m.indexOf('commission')<0)return false;return true});
      if(on)list.sort(IRLMatch.compare);else list.sort(function(a,b){return plainName(a.c).localeCompare(plainName(b.c))});
      /* weak matches (under 40% of the best fit) wait behind a button */
      var top=on&&list.length?list[0].s.total:0,shown=on&&!showWeak?list.filter(function(x){return x.s.total>=.4*top}):list,weak=list.length-shown.length;
      var fl=flt.free||flt.live||flt.commission;
      /* 'Recently published by providers' sits after the list, and only while browsing: a query or a filter shows the user's results alone */
      var pubs=document.querySelector('section.pubs');if(pubs)pubs.hidden=on||fl;
      bestBlock(on&&list.length?list[0]:null,text);
      grid.classList.toggle('scored',on);
      meta.textContent=shown.length+(on?(shown.length===1?' provider matches':' providers match'):(shown.length===1?' provider':' providers'));
      grid.innerHTML=shown.map(function(x){return row(x.c,x.s,on)}).join('');var ky=document.querySelector('.lhead .key');if(ky)ky.hidden=!list.length;
      more.hidden=!weak;more.textContent='+'+weak+(weak===1?' weaker match':' weaker matches');
      if(!list.length){meta.innerHTML=fl&&matched.length?'None with these filters. <button type="button" class="linkish" id="clrf">Clear filters</button>':'No provider covers this. Try a broader ask.';
        var cf=document.getElementById('clrf');if(cf)cf.onclick=function(){flt={free:false,live:false,commission:false};syncF();render()}}
      try{var u=new URL(location.href);if(on)u.searchParams.set('q',text);else u.searchParams.delete('q');[['free','free'],['live','live'],['made','commission']].forEach(function(p){if(flt[p[1]])u.searchParams.set(p[0],'1');else u.searchParams.delete(p[0])});history.replaceState(history.state,'',u)}catch(e){}}
    /* the best match, read like the landing's screen 3 (the owner, 2026-10-29): what you get from it in one line, and the one
       next step. A free live source this browser can read: Get it now (the landing fetches it). Otherwise its plan: mode,
       price, speed and how close it is to "just say yes" (readiness, from catalog.json, fetched once). */
    var best=document.getElementById('best'),RDY=null,rdyAsked=false;
    function bestBlock(x,text){if(!best)return;if(!x){best.hidden=true;best.innerHTML='';return}
      var c=x.c,LV=window.IRLLive,now=(c.access||{}).cost==='free'&&LV&&LV.route(c.id),card='<a href="'+base+'c/'+c.id+'.html" data-open="'+c.id+'">';
      var h='<p class="bk">Best match</p><p class="bn">'+E(plainName(c))+'</p>';
      h+=now?'<p class="bp">Live · Free · it answers now, straight to your browser.</p><div class="ba"><a class="go" href="'+base+'index.html?q='+encodeURIComponent(text)+'&amp;go=1#try">Get it now</a>'+card+'Its card</a></div>':
        '<p class="bp">'+E(modeText(c)+' · '+priceText(c)+' · '+speed(c))+'</p><p class="bp" id="brdy" hidden></p><div class="ba">'+card.replace('<a ','<a class="go" ')+'See the plan</a></div>';
      best.innerHTML=h;best.hidden=false;if(!now)readiness(c.id)}
    function readiness(id){var put=function(){var r=document.getElementById('brdy'),x=RDY&&RDY[id];if(r&&x&&x.line){r.textContent=x.line;r.hidden=false}};
      if(RDY||rdyAsked){put();return}rdyAsked=true;
      fetch(base+'catalog.json').then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(d){RDY={};var ps=d.providers||{};Object.keys(ps).forEach(function(k){RDY[k]=ps[k].readiness||null});put()}).catch(function(){})}
    if(best)best.addEventListener('click',function(e){var a=e.target.closest('a[data-open]');if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.button)return;e.preventDefault();open(a.dataset.open,true,e.detail>0)});
    function later(){clearTimeout(tm);tm=setTimeout(render,90)}
    q.addEventListener('input',later);clr.onclick=function(){q.value='';render();q.focus()};
    more.addEventListener('click',function(){var n=grid.querySelectorAll('.row').length;showWeak=true;render();var a=grid.querySelectorAll('.row')[n];if(a)a.focus()});
    document.getElementById('filters').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;var k=b.dataset.f;flt[k]=!flt[k];
      if(flt[k]&&k==='live')flt.commission=false;if(flt[k]&&k==='commission')flt.live=false;syncF();render()});
    ex.addEventListener('click',function(e){var b=e.target.closest('button');if(b){q.value=b.dataset.q||b.textContent;render();q.focus()}});
    /* the sample asks stay on one line: an edge fade while more are off to the right */
    function fade(){ex.classList.toggle('fade',ex.scrollLeft+ex.clientWidth<ex.scrollWidth-2)}ex.addEventListener('scroll',fade,{passive:true});addEventListener('resize',fade);fade();
    document.addEventListener('keydown',function(e){if(e.key==='/'&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)&&sheet.hidden){e.preventDefault();q.focus()}});
    /* the sheet replaces the page while open (opaque, full screen): nothing sits over anything.
       It shows the card's own built page (c/<id>.html), so the sheet and the page can't disagree. */
    var pages={};
    function load(id){if(pages[id])return Promise.resolve(pages[id]);
      return fetch(base+'c/'+id+'.html').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.text()}).then(function(t){
        var a=new DOMParser().parseFromString(t,'text/html').querySelector('article.cardv');if(!a)throw new Error('no card in page');
        [].forEach.call(a.querySelectorAll('a[href^="../"]'),function(l){l.setAttribute('href',base+l.getAttribute('href').slice(3))});
        var h=a.querySelector('h1');if(h){var h2=document.createElement('h2');h2.className=h.className;h2.id=h.id;h2.innerHTML=h.innerHTML;h.replaceWith(h2)}
        return (pages[id]=a.outerHTML)})}
    function others(on){[].forEach.call(document.body.children,function(n){if(n!==sheet&&n.tagName!=='SCRIPT'){if(on)n.setAttribute('inert','');else n.removeAttribute('inert')}})}
    function open(id,push,quiet){var c=byId(id);if(!c)return;lastFocus=document.activeElement;var text=q.value.trim();
      sheet.dataset.id=id;
      sheet.innerHTML='<div class="sheet-bar"><button class="sheet-x" type="button" aria-label="Close">'+
        '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5"/></svg>'+(text?'Results':'Find data')+'</button></div><div class="sheet-in"><p class="empty" role="status">Loading '+E(plainName(c))+'…</p></div>';
      sheet.hidden=false;sheet.scrollTop=0;document.body.classList.add('lock');others(true);var sx=sheet.querySelector('.sheet-x');sx.classList.toggle('quiet',!!quiet);sx.focus();
      if(push){history.pushState({sheet:id},'','#'+id);pushed=true}
      var inner=sheet.querySelector('.sheet-in');
      load(id).then(function(h){if(sheet.hidden||sheet.dataset.id!==id)return;inner.innerHTML=h;var s=scored(c,text);if(s&&s.total!=null)addFit(inner.querySelector('.cardv'),c,s,text)},
        function(){if(sheet.hidden||sheet.dataset.id!==id)return;inner.innerHTML='<p class="empty" role="alert">Couldn’t load this card. <a href="'+base+'c/'+id+'.html">Open its page</a></p>'})}
    function close(fromPop){if(sheet.hidden)return;sheet.hidden=true;document.body.classList.remove('lock');others(false);
      if(!fromPop){if(pushed)history.back();else try{history.replaceState(null,'',location.pathname+location.search)}catch(e){}}pushed=false;
      if(lastFocus&&lastFocus.focus&&document.contains(lastFocus))lastFocus.focus();else{var a=grid.querySelector('.row');if(a)a.focus()}}
    grid.addEventListener('click',function(e){var a=e.target.closest('a.row');if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.button)return;e.preventDefault();open(a.dataset.id,true,e.detail>0)});  /* a tap or click (detail>0) opens without a focus ring; Enter shows it */
    sheet.addEventListener('click',function(e){if(e.target.closest('.sheet-x'))close()});
    document.addEventListener('keydown',function(e){if(sheet.hidden)return;var sx=sheet.querySelector('.sheet-x');if(sx&&e.key==='Tab')sx.classList.remove('quiet');if(e.key==='Escape'){e.preventDefault();close()}
      if(e.key==='Tab'){var f=[].slice.call(sheet.querySelectorAll('button,a[href],summary')).filter(function(n){return n.offsetParent});if(!f.length)return;var a=f[0],z=f[f.length-1];if(e.shiftKey&&document.activeElement===a){e.preventDefault();z.focus()}else if(!e.shiftKey&&document.activeElement===z){e.preventDefault();a.focus()}}});
    window.addEventListener('popstate',function(){var id=history.state&&history.state.sheet;if(id&&byId(id)){pushed=true;open(id,false)}else close(true)});
    var hash=location.hash.slice(1),q0=P.get('q');if(q0)q.value=q0;
    render();if(hash&&byId(hash))open(hash,false)}

  /* ---------- compare: up to four, side by side; the eight stages as marks so a phone holds three columns ---------- */
  function compare(){
    var MAX=innerWidth<600?3:4,qs=new URLSearchParams(location.search).get('ids'),asked=(qs?qs.split(','):[]).map(function(x){return x.trim()}).filter(Boolean),
        bad=asked.filter(function(x){return !byId(x)}),known=asked.filter(byId),ids=known.slice(0,MAX),cut=known.slice(MAX),
        pk=document.getElementById('picker'),el=document.getElementById('cmp'),msg=document.getElementById('cmpmsg'),note=[];
    if(bad.length)note.push('No provider with the id '+bad.map(function(x){return '“'+x+'”'}).join(', ')+'.');
    if(cut.length)note.push('This screen fits '+MAX+'; left out '+cut.map(function(x){return plainName(byId(x))}).join(', ')+'.');
    var sorted=C.slice().sort(function(a,b){return plainName(a).localeCompare(plainName(b))});
    /* one-tap starting points when nothing is chosen; names come from the cards */
    var PRESETS=[['usgs-water','nws'],['proxypics','wegolook'],['skyfi','capella','umbra']].map(function(p){return p.filter(byId).slice(0,MAX)}).filter(function(p){return p.length>1});
    function picker(){
      pk.innerHTML=ids.length<MAX?'<label class="add"><span class="sr">Add a provider</span><select id="addp"><option value="">+ Add</option>'+sorted.filter(function(c){return ids.indexOf(c.id)<0}).map(function(c){return '<option value="'+c.id+'">'+E(plainName(c))+'</option>'}).join('')+'</select></label>':''}
    function set(){note=[];try{history.replaceState(null,'',ids.length?'?ids='+ids.join(','):location.pathname)}catch(e){}picker();table()}
    el.addEventListener('click',function(e){var b=e.target.closest('button[data-rm]');if(b){ids.splice(ids.indexOf(b.dataset.rm),1);set();var s=document.getElementById('addp');if(s)s.focus();return}
      var p=e.target.closest('a[data-ids]');if(p&&!(e.metaKey||e.ctrlKey||e.shiftKey)){e.preventDefault();ids=p.dataset.ids.split(',');set()}});
    pk.addEventListener('change',function(e){if(e.target.id==='addp'&&e.target.value){ids.push(e.target.value);set();var s=document.getElementById('addp');if(s)s.focus()}});
    function table(){
      var key=document.querySelector('.cmpkey');msg.textContent=note.join(' ');
      if(!ids.length){el.className='presets';el.innerHTML='<ul aria-label="Starting points">'+PRESETS.map(function(p){return '<li><a href="?ids='+p.join(',')+'" data-ids="'+p.join(',')+'">'+p.map(function(i){return E(plainName(byId(i)))}).join(' vs ')+'</a></li>'}).join('')+'</ul>';if(key)key.hidden=true;return}
      el.className='scroll';if(key)key.hidden=false;
      var cs=ids.map(byId);
      function row(l,f){return '<tr><th scope="row">'+l+'</th>'+cs.map(function(c){return '<td>'+f(c)+'</td>'}).join('')+'</tr>'}
      var h='<table class="n'+cs.length+'"><caption class="sr">Providers side by side</caption><thead><tr><th scope="col"><span class="sr">Field</span></th>'+cs.map(function(c){return '<th scope="col"><div class="ch"><a href="'+base+'c/'+c.id+'.html">'+E(plainName(c))+'</a><button type="button" class="rm" data-rm="'+c.id+'" aria-label="Remove '+E(plainName(c))+'"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m4.5 4.5 7 7m0-7-7 7"/></svg></button></div></th>'}).join('')+'</tr></thead><tbody>'+
        row('Mode',function(c){return E(modeText(c))})+row('Speed',function(c){return E(speed(c))})+row('Price',function(c){return E(priceText(c))})+row('Coverage',function(c){return E(where(c))})+
        row('Key',function(c){return E(keyText(c))})+row('Resale',function(c){return right((c.licensing||{}).resale)})+row('AI training',function(c){return right((c.licensing||{}).ai_training)})+
        '</tbody><tbody class="g"><tr class="sec"><th scope="rowgroup" colspan="'+(cs.length+1)+'">Eight links</th></tr>';
      IRLMatch.stageTitles.forEach(function(t,i){h+=row(E(t),function(c){var l=IRLMatch.stageLevels(c)[i];return '<span class="mk1" role="img" aria-label="'+E(stageSays(i,l))+'" title="'+E(stageSays(i,l))+'"><i class="'+IRLMatch.stageMark(l)+'" aria-hidden="true"></i><span class="w">'+L.LEVEL[l]+'</span></span>'})});
      el.innerHTML=h+'</tbody></table>'}
    picker();table()}
}).catch(function(err){
  var why=' ('+E(err&&err.message||'error')+')',msg='<p class="empty" role="alert">Couldn’t load the provider list'+why+'. Read <a href="'+base+'cards.json">cards.json</a> directly.</p>',
      meta=document.getElementById('meta'),cmp=document.getElementById('cmp');
  if(meta)meta.innerHTML='Search is unavailable'+why+'. All providers are listed below.';  /* the built list stays */
  else if(cmp)cmp.outerHTML=msg;
  else if(label){var a=label.querySelector('.cardv .spec');if(a)a.insertAdjacentHTML('beforebegin',msg.replace('the provider list','the fit for this request'))}
});
})();
