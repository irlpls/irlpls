/* irlpls places: the browser's world gazetteer. A typed place is put on the map HERE, from places.json (GeoNames
   cities15000, CC BY 4.0: cities of 50,000+ people and every national capital; built by tools/make_places.py). The typed
   text never leaves the browser: places.json is one static file, fetched whole, the same for everyone.

   IRLPlaces.load(base?) -> Promise (fetches base + 'places.json' once; resolves even when it fails, with ready false)
   IRLPlaces.ready       -> true once the table is in
   IRLPlaces.find(text)  -> {name, cc, lat, lon, admin, pop} or null
       name: "City, ST" in the US (the postal code), else the city's English name ("Munich", "Cologne")
   IRLPlaces.country(text) -> the ISO code of a country named in the text, or null

   How find() reads an ask:
   - names match case- and accent-blind ("sao paulo" = "São Paulo"), as runs of 1-4 words;
   - a run counts when its words are capitalised as written ("Munich air quality"), or when it follows a preposition
     (in, at, near, around, of, by, for, from, to: "air quality in munich");
   - common English words that are also city names (reading, nice, mobile, bath …) count only capitalised AND after a
     preposition ("river level in Reading"), never as plain words ("Reading list", "is the water nice in Paris");
   - a country, a US state or another first-level division named too picks among same-named cities
     ("Springfield, Illinois", "Portland, Maine", "Cambridge, MA", "Munich, Germany");
   - otherwise the longer name wins ("New York" over "York"), then the bigger city. */
(function(){
var P={ready:false},D=null,IDX=null,CTRY=null,ADM=null,loading=null;
var PREP={'in':1,'at':1,'near':1,'around':1,'of':1,'by':1,'for':1,'from':1,'to':1,'outside':1};
var STOP=('air weather river rivers tide tides traffic quake quakes camera cam ships ship level rain snow wind water today now '+
  'bath bend brick clay date delta enterprise forest highland homestead hub independence male man mango mission mobile most nice '+
  'normal orange paradise plantation reading sale sandy spring split sunrise surprise temple union van vista wedding woodland '+
  'centennial commonwealth confederation meridian midland reservoir riverside uptown san sur ho bo wa can may march hope '+
  'buckeye goodyear grapevine bellflower cypress mesquite paramount sparks revere sig gare marl mascara turbo tyre').split(' ');
var STOPS={};STOP.forEach(function(w){STOPS[w]=1});
function norm(s){s=String(s||'').toLowerCase().replace(/[’`]/g,"'");try{s=s.normalize('NFD').replace(/[̀-ͯ]/g,'')}catch(e){}
  return s.replace(/ß/g,'ss').replace(/\s+/g,' ').trim()}
function index(d){IDX={};CTRY={};ADM={};
  d.cities.forEach(function(r,i){[r[0]].concat(r[1]?String(r[1]).split('|'):[]).forEach(function(n){if(!n)return;var k=norm(n);(IDX[k]=IDX[k]||[]);if(IDX[k].indexOf(i)<0)IDX[k].push(i)})});
  Object.keys(d.countries||{}).forEach(function(k){CTRY[norm(k)]=d.countries[k]});
  Object.keys(d.admin1||{}).forEach(function(k){var n=norm(d.admin1[k]);(ADM[n]=ADM[n]||[]).push(k)})}
P.load=function(base){if(loading)return loading;
  loading=(typeof fetch==='function'?fetch((base||'')+'places.json').then(function(r){if(!r.ok)throw 0;return r.json()}):Promise.reject(0))
    .then(function(d){D=d;index(d);P.ready=true;return P},function(){P.ready=false;loading=null;return P});
  return loading};
/* for tests and pages that embed the table */
P.use=function(d){D=d;index(d);P.ready=true;return P};
/* the words of the text, as written and normalised */
function words(text){var out=[],re=/[^\s,;:!?()"“”\[\]{}\/]+/g,m;
  while((m=re.exec(String(text||'')))){var w=m[0].replace(/^['’.\-]+|['’.\-]+$/g,'');if(!w)continue;
    out.push({raw:w,n:norm(w),cap:/^[A-ZÀ-ÞĀ-Ž]/.test(w)&&w!==w.toLowerCase()})}
  return out}
/* what else the text names: countries and first-level divisions (full names, or a US postal code written in capitals) */
function context(ws){var cc={},adm={},i,n,k;
  for(n=4;n>=1;n--)for(i=0;i+n<=ws.length;i++){k=ws.slice(i,i+n).map(function(w){return w.n}).join(' ');
    if(CTRY.hasOwnProperty(k)&&!(n===1&&/^(us|in)$/.test(k)&&ws[i].raw!==ws[i].raw.toUpperCase()))cc[CTRY[k]]=1;
    if(ADM.hasOwnProperty(k))ADM[k].forEach(function(a){adm[a]=1})}
  ws.forEach(function(w){if(/^[A-Z]{2}$/.test(w.raw)&&D.admin1['us.'+w.raw])adm['us.'+w.raw]=1});
  return{cc:cc,adm:adm}}
P.country=function(text){if(!P.ready)return null;var c=context(words(text)).cc,k=Object.keys(c);return k.length?k[0]:null};
P.find=function(text){if(!P.ready)return null;var ws=words(text),ctx=context(ws),best=null,i,n,k,rows,j;
  for(n=4;n>=1;n--)for(i=0;i+n<=ws.length;i++){k=ws.slice(i,i+n).map(function(w){return w.n}).join(' ');rows=IDX[k];if(!rows)continue;
    var allCap=ws.slice(i,i+n).every(function(w){return w.cap}),prep=i>0&&PREP[ws[i-1].n];
    if(!allCap&&!prep)continue;
    /* among same-named cities: the one in a named division, else in a named country, else the biggest (rows are biggest first) */
    var inAdm=rows.filter(function(r){var c=D.cities[r];return c[3]&&ctx.adm[c[2]+'.'+c[3]]}),
      inCc=rows.filter(function(r){return ctx.cc[D.cities[r][2]]}),pick=(inAdm[0]!=null?inAdm:inCc[0]!=null?inCc:rows)[0],
      hit=inAdm.length?2:inCc.length?1:0,c=D.cities[pick];
    if(n===1&&STOPS[k]&&!(allCap&&(prep||hit)))continue;  /* a common word: only as a name (capitalised, after a preposition or with its state/country) */
    var s=[hit,n,c[6]];
    if(!best||s[0]>best.s[0]||s[0]===best.s[0]&&(s[1]>best.s[1]||s[1]===best.s[1]&&s[2]>best.s[2]))best={s:s,r:c}}
  if(!best)return null;var r=best.r;
  return{name:r[2]==='us'&&r[3]?r[0]+', '+r[3]:r[0],cc:r[2],lat:r[4]/100,lon:r[5]/100,admin:r[3]&&D.admin1[r[2]+'.'+r[3]]||'',pop:r[6]*1000}};
window.IRLPlaces=P;
})();
