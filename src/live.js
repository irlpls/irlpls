/* irlpls live: a free provider's reading, fetched straight from the visitor's browser (there is no hosted irlpls API).
   Only providers that are free, keyless and answer a browser (CORS) are wired here; each was checked by a real browser
   request (2026-10-28, rechecked 2026-10-29). Not wired: OpenAQ (key required, and its API refuses browser requests);
   NOAA NDBC buoys and NASA FIRMS files (no CORS header); WSDOT and ALERTCalifornia cameras (their camera lists allow a
   browser, but the stills don't, so a browser can show a still but can't read when it was taken); Caltrans CCTV (one
   2.5 MB file per district). The landing labels those "through your agent" instead of offering Get it now.
   No free, keyless, browser-readable source covers air in India (Sensor.Community has no sensors reporting around Delhi;
   OpenAQ, which carries the CPCB stations, needs a key): the answer says so and names that plan.
   Units are the reference library's (irlpls/adapters): metres above MLLW for tides, °C for weather, ft and ft³/s for USGS.

   Etiquette, so a page full of visitors never hammers a public service:
   - at most 1 request per 2 s per provider (calls inside one lookup are spaced too), at most 20 per visitor per 10 min;
   - a short cache: a reading is reused while the provider can't have a newer one yet (younger than its update interval);
   - 429 (or 503) honours Retry-After (60 s when the header is absent or unreadable) and blocks that provider until then;
   - never a retry loop: one tap, one lookup; a failure is said plainly and waits for the person.
   What leaves the browser: the map point (rounded to ~100 m) or a station id. Never the typed text.

   IRLLive.route(cardId) -> the route, or null       IRLLive.place(text, parsed) -> {lat, lon, cc, name} or {missing}
   IRLLive.get(cardId, place, onStep, {ask}) -> Promise of {ok:true, r, cached, via?} or {ok:false, kind:'busy'|'budget'|'down'|'none', wait, plan?: [headline, sentence]}
     via: the card that actually answered, when the regional catalogue (GeoNet for New Zealand, EMSC for Europe) or a fallback
     (AirNow when Sensor.Community has no trustworthy US sensor) answered instead of the one tapped.
   IRLLive.blocked(cardId) -> seconds until this provider may be asked again (0 when it may)
   The ask itself never leaves the page: it only picks which of the provider's values to show (USGS: an ask for the
   river's level, height or stage shows gage height, 00065, when that gauge has it). */
(function(){
var SPACING=2000,BUDGET=20,WINDOW=600000,TIMEOUT=15000,MIN=60000;

/* places this page can put on a map without asking anyone: [lat, lon, country, label]. A US state stands for a city in it
   (the answer names the city, so the point used is never hidden); a country for its usual city. */
var PL={
 // US states, as a city in each
 'alabama':[33.52,-86.80,'us','Birmingham, AL'],'alaska':[61.22,-149.90,'us','Anchorage, AK'],'arizona':[33.45,-112.07,'us','Phoenix, AZ'],
 'arkansas':[34.75,-92.29,'us','Little Rock, AR'],'california':[38.58,-121.49,'us','Sacramento, CA'],'colorado':[39.74,-104.99,'us','Denver, CO'],
 'connecticut':[41.76,-72.67,'us','Hartford, CT'],'delaware':[39.74,-75.55,'us','Wilmington, DE'],'florida':[28.54,-81.38,'us','Orlando, FL'],
 'georgia':[33.75,-84.39,'us','Atlanta, GA'],'hawaii':[21.31,-157.86,'us','Honolulu, HI'],'idaho':[43.62,-116.20,'us','Boise, ID'],
 'illinois':[41.88,-87.63,'us','Chicago, IL'],'indiana':[39.77,-86.16,'us','Indianapolis, IN'],'iowa':[41.59,-93.62,'us','Des Moines, IA'],
 'kansas':[37.69,-97.34,'us','Wichita, KS'],'kentucky':[38.25,-85.76,'us','Louisville, KY'],'louisiana':[30.45,-91.15,'us','Baton Rouge, LA'],
 'maine':[43.66,-70.26,'us','Portland, ME'],'maryland':[39.29,-76.61,'us','Baltimore, MD'],'massachusetts':[42.36,-71.06,'us','Boston, MA'],
 'michigan':[42.33,-83.05,'us','Detroit, MI'],'minnesota':[44.98,-93.27,'us','Minneapolis, MN'],'mississippi':[32.30,-90.18,'us','Jackson, MS'],
 'missouri':[38.63,-90.20,'us','St. Louis, MO'],'montana':[46.59,-112.04,'us','Helena, MT'],'nebraska':[41.26,-95.93,'us','Omaha, NE'],
 'nevada':[39.53,-119.81,'us','Reno, NV'],'new hampshire':[43.21,-71.54,'us','Concord, NH'],'new jersey':[40.22,-74.76,'us','Trenton, NJ'],
 'new mexico':[35.08,-106.65,'us','Albuquerque, NM'],'new york':[40.71,-74.01,'us','New York, NY'],'north carolina':[35.78,-78.64,'us','Raleigh, NC'],
 'north dakota':[46.81,-100.78,'us','Bismarck, ND'],'ohio':[39.96,-83.00,'us','Columbus, OH'],'oklahoma':[35.47,-97.52,'us','Oklahoma City, OK'],
 'oregon':[45.52,-122.68,'us','Portland, OR'],'pennsylvania':[40.27,-76.88,'us','Harrisburg, PA'],'rhode island':[41.82,-71.41,'us','Providence, RI'],
 'south carolina':[34.00,-81.03,'us','Columbia, SC'],'south dakota':[43.55,-96.73,'us','Sioux Falls, SD'],'tennessee':[36.16,-86.78,'us','Nashville, TN'],
 'texas':[30.27,-97.74,'us','Austin, TX'],'utah':[40.76,-111.89,'us','Salt Lake City, UT'],'vermont':[44.26,-72.58,'us','Montpelier, VT'],
 'virginia':[37.54,-77.44,'us','Richmond, VA'],'washington':[47.61,-122.33,'us','Seattle, WA'],'west virginia':[38.35,-81.63,'us','Charleston, WV'],
 'wisconsin':[43.04,-87.91,'us','Milwaukee, WI'],'wyoming':[41.14,-104.82,'us','Cheyenne, WY'],
 // US cities
 'austin':[30.27,-97.74,'us','Austin, TX'],'houston':[29.76,-95.37,'us','Houston, TX'],'dallas':[32.78,-96.80,'us','Dallas, TX'],
 'san antonio':[29.42,-98.49,'us','San Antonio, TX'],'comfort':[29.97,-98.91,'us','Comfort, TX'],'new braunfels':[29.70,-98.12,'us','New Braunfels, TX'],
 'el paso':[31.76,-106.49,'us','El Paso, TX'],'los angeles':[34.05,-118.24,'us','Los Angeles, CA'],'san francisco':[37.77,-122.42,'us','San Francisco, CA'],
 'san diego':[32.72,-117.16,'us','San Diego, CA'],'sacramento':[38.58,-121.49,'us','Sacramento, CA'],'seattle':[47.61,-122.33,'us','Seattle, WA'],
 'bellevue':[47.61,-122.20,'us','Bellevue, WA'],'spokane':[47.66,-117.43,'us','Spokane, WA'],'portland':[45.52,-122.68,'us','Portland, OR'],'portland, maine':[43.66,-70.26,'us','Portland, ME'],'portland, me':[43.66,-70.26,'us','Portland, ME'],
 'denver':[39.74,-104.99,'us','Denver, CO'],'boulder':[40.01,-105.27,'us','Boulder, CO'],'chicago':[41.88,-87.63,'us','Chicago, IL'],
 'new york city':[40.71,-74.01,'us','New York, NY'],'nyc':[40.71,-74.01,'us','New York, NY'],'manhattan':[40.78,-73.97,'us','Manhattan, NY'],
 'brooklyn':[40.68,-73.94,'us','Brooklyn, NY'],'boston':[42.36,-71.06,'us','Boston, MA'],'philadelphia':[39.95,-75.17,'us','Philadelphia, PA'],
 'pittsburgh':[40.44,-80.00,'us','Pittsburgh, PA'],'baltimore':[39.29,-76.61,'us','Baltimore, MD'],'miami':[25.76,-80.19,'us','Miami, FL'],
 'tampa':[27.95,-82.46,'us','Tampa, FL'],'orlando':[28.54,-81.38,'us','Orlando, FL'],'atlanta':[33.75,-84.39,'us','Atlanta, GA'],
 'phoenix':[33.45,-112.07,'us','Phoenix, AZ'],'tucson':[32.22,-110.97,'us','Tucson, AZ'],'las vegas':[36.17,-115.14,'us','Las Vegas, NV'],
 'salt lake city':[40.76,-111.89,'us','Salt Lake City, UT'],'minneapolis':[44.98,-93.27,'us','Minneapolis, MN'],'st. louis':[38.63,-90.20,'us','St. Louis, MO'],
 'st louis':[38.63,-90.20,'us','St. Louis, MO'],'kansas city':[39.10,-94.58,'us','Kansas City, MO'],'nashville':[36.16,-86.78,'us','Nashville, TN'],
 'memphis':[35.15,-90.05,'us','Memphis, TN'],'new orleans':[29.95,-90.07,'us','New Orleans, LA'],'detroit':[42.33,-83.05,'us','Detroit, MI'],
 'cleveland':[41.50,-81.69,'us','Cleveland, OH'],'columbus':[39.96,-83.00,'us','Columbus, OH'],'cincinnati':[39.10,-84.51,'us','Cincinnati, OH'],
 'boise':[43.62,-116.20,'us','Boise, ID'],'albuquerque':[35.08,-106.65,'us','Albuquerque, NM'],'oklahoma city':[35.47,-97.52,'us','Oklahoma City, OK'],
 'omaha':[41.26,-95.93,'us','Omaha, NE'],'milwaukee':[43.04,-87.91,'us','Milwaukee, WI'],'raleigh':[35.78,-78.64,'us','Raleigh, NC'],
 'charlotte':[35.23,-80.84,'us','Charlotte, NC'],'richmond':[37.54,-77.44,'us','Richmond, VA'],'anchorage':[61.22,-149.90,'us','Anchorage, AK'],
 'honolulu':[21.31,-157.86,'us','Honolulu, HI'],
 // US coast (NOAA tide stations)
 'santa monica':[34.02,-118.49,'us','Santa Monica, CA'],'long beach':[33.77,-118.19,'us','Long Beach, CA'],'monterey':[36.60,-121.89,'us','Monterey, CA'],
 'santa barbara':[34.42,-119.70,'us','Santa Barbara, CA'],'galveston':[29.30,-94.80,'us','Galveston, TX'],'corpus christi':[27.80,-97.40,'us','Corpus Christi, TX'],
 'charleston':[32.78,-79.93,'us','Charleston, SC'],'savannah':[32.08,-81.09,'us','Savannah, GA'],'key west':[24.56,-81.78,'us','Key West, FL'],
 'norfolk':[36.85,-76.29,'us','Norfolk, VA'],'annapolis':[38.98,-76.49,'us','Annapolis, MD'],'atlantic city':[39.36,-74.42,'us','Atlantic City, NJ'],
 'juneau':[58.30,-134.42,'us','Juneau, AK'],'san juan':[18.47,-66.11,'us','San Juan, PR'],'puerto rico':[18.47,-66.11,'us','San Juan, PR'],
 // Austria (viadonau gauges carried by PEGELONLINE)
 'austria':[48.21,16.37,'at','Vienna'],'österreich':[48.21,16.37,'at','Vienna'],'vienna':[48.21,16.37,'at','Vienna'],'wien':[48.21,16.37,'at','Vienna'],
 'linz':[48.31,14.29,'at','Linz'],'krems':[48.41,15.60,'at','Krems'],
 // France (Hub'Eau)
 'france':[48.86,2.35,'fr','Paris'],'paris':[48.86,2.35,'fr','Paris'],'lyon':[45.76,4.84,'fr','Lyon'],'marseille':[43.30,5.37,'fr','Marseille'],
 'bordeaux':[44.84,-0.58,'fr','Bordeaux'],'toulouse':[43.60,1.44,'fr','Toulouse'],'nantes':[47.22,-1.55,'fr','Nantes'],'strasbourg':[48.57,7.75,'fr','Strasbourg'],
 'lille':[50.63,3.06,'fr','Lille'],'orléans':[47.90,1.90,'fr','Orléans'],'orleans':[47.90,1.90,'fr','Orléans'],'rouen':[49.44,1.10,'fr','Rouen'],'grenoble':[45.19,5.72,'fr','Grenoble'],
 // New Zealand (GeoNet)
 'new zealand':[-41.29,174.78,'nz','Wellington'],'nz':[-41.29,174.78,'nz','Wellington'],'wellington':[-41.29,174.78,'nz','Wellington'],
 'auckland':[-36.85,174.76,'nz','Auckland'],'christchurch':[-43.53,172.64,'nz','Christchurch'],'dunedin':[-45.87,170.50,'nz','Dunedin'],'queenstown':[-45.03,168.66,'nz','Queenstown'],
 // elsewhere (earthquakes and citizen air sensors cover the world)
 'netherlands':[52.37,4.90,'nl','Amsterdam'],'amsterdam':[52.37,4.90,'nl','Amsterdam'],'rotterdam':[51.92,4.48,'nl','Rotterdam'],
 'switzerland':[47.37,8.54,'ch','Zürich'],'zurich':[47.37,8.54,'ch','Zürich'],'zürich':[47.37,8.54,'ch','Zürich'],'czechia':[50.08,14.44,'cz','Prague'],'czech republic':[50.08,14.44,'cz','Prague'],'prague':[50.08,14.44,'cz','Prague'],
 'japan':[35.68,139.69,'jp','Tokyo'],'tokyo':[35.68,139.69,'jp','Tokyo'],'osaka':[34.69,135.50,'jp','Osaka'],'mexico':[19.43,-99.13,'mx','Mexico City'],'mexico city':[19.43,-99.13,'mx','Mexico City'],
 'turkey':[41.01,28.98,'tr','Istanbul'],'istanbul':[41.01,28.98,'tr','Istanbul'],'chile':[-33.45,-70.67,'cl','Santiago'],'santiago':[-33.45,-70.67,'cl','Santiago'],
 'peru':[-12.05,-77.04,'pe','Lima'],'lima':[-12.05,-77.04,'pe','Lima'],'taiwan':[25.03,121.57,'tw','Taipei'],'taipei':[25.03,121.57,'tw','Taipei'],
 'indonesia':[-6.21,106.85,'id','Jakarta'],'jakarta':[-6.21,106.85,'id','Jakarta'],'philippines':[14.60,120.98,'ph','Manila'],'manila':[14.60,120.98,'ph','Manila'],
 'italy':[41.90,12.50,'it','Rome'],'rome':[41.90,12.50,'it','Rome'],'greece':[37.98,23.73,'gr','Athens'],'athens':[37.98,23.73,'gr','Athens'],
 'iceland':[64.15,-21.94,'is','Reykjavík'],'reykjavik':[64.15,-21.94,'is','Reykjavík'],'reykjavík':[64.15,-21.94,'is','Reykjavík'],
 // England (Environment Agency)
 'england':[51.51,-0.13,'gb','London'],'uk':[51.51,-0.13,'gb','London'],'united kingdom':[51.51,-0.13,'gb','London'],'britain':[51.51,-0.13,'gb','London'],
 'london':[51.51,-0.13,'gb','London'],'manchester':[53.48,-2.24,'gb','Manchester'],'leeds':[53.80,-1.55,'gb','Leeds'],'york':[53.96,-1.08,'gb','York'],
 'bristol':[51.45,-2.59,'gb','Bristol'],'oxford':[51.75,-1.26,'gb','Oxford'],'liverpool':[53.41,-2.98,'gb','Liverpool'],
 'newcastle':[54.98,-1.61,'gb','Newcastle'],'sheffield':[53.38,-1.47,'gb','Sheffield'],'nottingham':[52.95,-1.15,'gb','Nottingham'],
 'exeter':[50.72,-3.53,'gb','Exeter'],'norwich':[52.63,1.30,'gb','Norwich'],'carlisle':[54.89,-2.93,'gb','Carlisle'],
 // Germany (PEGELONLINE: federal waterways)
 'germany':[50.94,6.96,'de','Köln'],'deutschland':[50.94,6.96,'de','Köln'],'köln':[50.94,6.96,'de','Köln'],'koln':[50.94,6.96,'de','Köln'],
 'cologne':[50.94,6.96,'de','Köln'],'bonn':[50.74,7.10,'de','Bonn'],'düsseldorf':[51.23,6.77,'de','Düsseldorf'],'dusseldorf':[51.23,6.77,'de','Düsseldorf'],
 'duisburg':[51.43,6.76,'de','Duisburg'],'koblenz':[50.36,7.59,'de','Koblenz'],'mainz':[50.00,8.27,'de','Mainz'],'frankfurt':[50.11,8.68,'de','Frankfurt'],
 'mannheim':[49.49,8.47,'de','Mannheim'],'berlin':[52.52,13.40,'de','Berlin'],'hamburg':[53.55,9.99,'de','Hamburg'],'bremen':[53.08,8.80,'de','Bremen'],
 'dresden':[51.05,13.74,'de','Dresden'],'magdeburg':[52.13,11.63,'de','Magdeburg'],'passau':[48.57,13.43,'de','Passau'],'regensburg':[49.01,12.10,'de','Regensburg'],
 // Finland (Digitraffic)
 'finland':[60.17,24.94,'fi','Helsinki'],'helsinki':[60.17,24.94,'fi','Helsinki'],'espoo':[60.21,24.66,'fi','Espoo'],'turku':[60.45,22.27,'fi','Turku'],
 'tampere':[61.50,23.76,'fi','Tampere'],'oulu':[65.01,25.47,'fi','Oulu'],'vaasa':[63.10,21.62,'fi','Vaasa'],'kotka':[60.47,26.95,'fi','Kotka'],
 'porvoo':[60.39,25.66,'fi','Porvoo'],'lahti':[60.98,25.66,'fi','Lahti'],
 // a sea, as the middle of it (a fifth value: the radius, km, a route may use instead of its own)
 'gulf of finland':[59.95,25.3,'fi','the Gulf of Finland',60],
 // India (no free browser source for its air; quakes are worldwide)
 'india':[28.61,77.21,'in','Delhi'],'delhi':[28.61,77.21,'in','Delhi'],'new delhi':[28.61,77.21,'in','New Delhi'],'mumbai':[19.08,72.88,'in','Mumbai'],
 'bengaluru':[12.97,77.59,'in','Bengaluru'],'bangalore':[12.97,77.59,'in','Bengaluru'],'kolkata':[22.57,88.36,'in','Kolkata'],'chennai':[13.08,80.27,'in','Chennai']};
var KEYS=Object.keys(PL).sort(function(a,b){return b.length-a.length});
/* areas: a US state or a country, standing for a city in it. Checked after the cities and after any unknown place name */
var AREA=/^(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|england|uk|united kingdom|britain|germany|deutschland|finland|puerto rico|austria|österreich|france|new zealand|nz|netherlands|switzerland|czechia|czech republic|japan|mexico|turkey|chile|peru|taiwan|indonesia|philippines|italy|greece|iceland|india)$/;
function esc(s){return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}
function hit(t,k){return t.indexOf(k)>-1&&new RegExp('(^|[^a-zà-ÿ])'+esc(k)+'(?![a-zà-ÿ])').test(t)}
function at(k){var p=PL[k],o={lat:p[0],lon:p[1],cc:p[2],name:p[3]};if(p[4])o.r=p[4];return o}
/* the place in an ask: a known city named in it (the longest name wins); else, if it names a place this table doesn't know
   ("Springfield, Illinois"), that place is asked about, never swapped for its state's city; else its state or country */
function place(text,r){text=String(text||'');var t=' '+text.toLowerCase()+' ',i,k,areas=[];r=r||{};
  for(i=0;i<KEYS.length;i++){k=KEYS[i];if(hit(t,k)){if(!AREA.test(k))return at(k);areas.push(k)}}
  /* then the world table (src/places.js: GeoNames cities of 50k+ and capitals, loaded in this browser; the text never leaves it) */
  var W=window.IRLPlaces&&window.IRLPlaces.ready?window.IRLPlaces.find(text):null;
  if(W)return{lat:W.lat,lon:W.lon,cc:W.cc,name:W.name};
  var cap=text.match(/(?:^|[^\wà-ÿ])[A-ZÀ-Þ][a-zà-ÿ'’.-]+(?:\s+[A-ZÀ-Þ][a-zà-ÿ'’.-]+)*/g)||[];
  for(i=0;i<cap.length;i++){var w=cap[i].replace(/^[^A-ZÀ-Þ]+/,''),lw=w.toLowerCase();
    if(i===0&&text.trim().indexOf(w)===0)w=w.split(/\s+/).slice(1).join(' '),lw=w.toLowerCase();  /* the first word is just a capital */
    if(!w||areas.some(function(a){return lw===a||(' '+lw+' ').indexOf(' '+a+' ')>-1}))continue;
    return{missing:'known',heard:w}}
  if(areas.length)return at(areas[0]);
  if(r.place)return{missing:'known',heard:r.place.split(',')[0].trim()};
  return{missing:r.country_code==='us'?'state':'place'}}

/* ---------- etiquette: spacing, budget, Retry-After, cache ---------- */
var next={},block={},mem={},ST=null;
try{ST=window.sessionStorage}catch(e){}
function load(k,d){try{var v=ST&&ST.getItem('irlpls.live.'+k);return v?JSON.parse(v):d}catch(e){return d}}
function save(k,v){try{ST&&ST.setItem('irlpls.live.'+k,JSON.stringify(v))}catch(e){}}
function now(){return Date.now()}
function sleep(ms){return new Promise(function(f){setTimeout(f,ms)})}
function spent(){var t=now();return load('calls',[]).filter(function(x){return t-x<WINDOW})}
function Busy(kind,wait){this.kind=kind;this.wait=wait}
/* "Retry-After: 30" or an HTTP date; unreadable (CORS hides it unless the provider exposes it) -> 60 s */
function retryAfter(h){var v=h&&h.get('Retry-After');if(!v)return 60;if(/^\d+$/.test(v.trim()))return Math.max(1,Math.min(3600,+v));
  var d=Date.parse(v);return isNaN(d)?60:Math.max(1,Math.min(3600,Math.ceil((d-now())/1000)))}
function call(pid,url,step,how){
  var b=block[pid]||load('block.'+pid,0);if(b>now())return Promise.reject(new Busy('busy',Math.ceil((b-now())/1000)));
  var w=Math.max(0,(next[pid]||0)-now());next[pid]=now()+w+SPACING;
  return sleep(w).then(function(){
    var c=spent();if(c.length>=BUDGET)throw new Busy('budget',Math.ceil((c[0]+WINDOW-now())/1000));
    c.push(now());save('calls',c);if(step)step();
    var o={headers:{Accept:how?'*/*':'application/json'}},ab='AbortController' in window?new AbortController():null,tm;
    if(ab){o.signal=ab.signal;tm=setTimeout(function(){ab.abort()},TIMEOUT)}
    return fetch(url,o).then(function(res){clearTimeout(tm);
      if(res.status===429||res.status===503){var s=retryAfter(res.headers);block[pid]=now()+s*1000;save('block.'+pid,block[pid]);throw new Busy('busy',s)}
      if(!res.ok)throw new Busy('down');return how?how(res):res.json()},function(e){clearTimeout(tm);throw e instanceof Busy?e:new Busy('down')})})}
function text(res){return res.text()}
function blocked(id){var R=ROUTES[id];if(!R)return 0;var b=Math.max(block[R.pid]||0,load('block.'+R.pid,0));return b>now()?Math.ceil((b-now())/1000):0}

/* ---------- geometry ---------- */
function km(a,b,c,d){var p=Math.PI/180,x=.5-Math.cos((c-a)*p)/2+Math.cos(a*p)*Math.cos(c*p)*(1-Math.cos((d-b)*p))/2;return 12742*Math.asin(Math.sqrt(x))}
function box(p,r){var dl=r/111,dn=r/(111*Math.max(Math.cos(p.lat*Math.PI/180),.01));return[p.lon-dn,p.lat-dl,p.lon+dn,p.lat+dl].map(function(v){return v.toFixed(4)}).join(',')}
function near(list,p,lat,lon){return list.map(function(x){return{x:x,d:km(p.lat,p.lon,lat(x),lon(x))}}).sort(function(a,b){return a.d-b.d})}
function r2(v){return Math.round(v*1000)/1000}
function title(s){return String(s||'').toLowerCase().replace(/(^|[\s(\/-])([a-zà-ÿ])/g,function(m,a,b){return a+b.toUpperCase()})}
function num(v,d){v=+v;return Math.abs(v)>=100?String(Math.round(v)):String(Math.round(v*Math.pow(10,d==null?1:d))/Math.pow(10,d==null?1:d))}

/* USGS station names are abbreviated ("Colorado Rv at Austin, TX"): spell the common ones out */
var ABBR={Rv:'River',R:'River',Ck:'Creek',Cr:'Creek',Nr:'near',Abv:'above',Blw:'below',Bl:'below',Lk:'Lake',Res:'Reservoir',Br:'Branch',Fk:'Fork',Trib:'Tributary',Brk:'Brook',Bk:'Brook',Spgs:'Springs',Mtn:'Mountain',Ft:'Fort',St:'St'};
function usgsName(s){return String(s||'').replace(/\b([A-Z][a-z]{0,3})\b\.?/g,function(m,w){return ABBR.hasOwnProperty(w)?ABBR[w]:m})}

/* Austrian Danube gauges run by viadonau, carried by PEGELONLINE without coordinates: the one for each city this page knows
   (uuid, where it is), checked against PEGELONLINE's station list on 2026-10-28 (number 42013 Korneuburg, 420061 Wilhering, 42012 Dürnstein) */
var AT={'Vienna':['ff44be4a-f934-446c-afb0-a1cf7702c48c','upstream of'],'Linz':['aec23fd6-9146-46c8-9004-02952388f2e3','upstream of'],'Krems':['ce4e3050-2d16-459a-8101-61535132398c','upstream of']};
/* an ask for the water's level, not its flow */
var LEVEL=/\b(levels?|heights?|stages?)\b/i;
/* ---------- the routes: one per card. Each returns a reading {value, what, station, km, t, photo?, alt?, link} ---------- */
var ROUTES={
 'usgs-water':{pid:'usgs',name:'USGS',every:15,radius:40,cc:['us'],noun:'gauge',area:'the US',eg:'river, Austin, Texas',
   /* USGS Water Data OGC API (api.waterdata.usgs.gov; the legacy waterservices.usgs.gov is slowed from 2026-11-16 and shut on
      2027-02-22). Keyless use is rate limited per hour by api.data.gov (429 when spent; https://api.waterdata.usgs.gov/docs/ogcapi/keys/),
      so: one bbox request for both parameters, and the gauge's name from monitoring-locations only once per browser. */
   get:function(p,s,o){var lv=!!(o&&o.level),O='https://api.waterdata.usgs.gov/ogcapi/v1/collections/';
     return call('usgs',O+'latest-continuous/items?f=json&parameter_code=00060,00065&properties=monitoring_location_id,parameter_code,value,time&limit=1000&bbox='+box(p,40),s).then(function(d){
     /* latest-continuous keeps a series' last value even when the gauge stopped years ago: only readings from the past 3 days
        count (the legacy siteStatus=active), and of two series for one gauge and parameter, the newer */
     var old=now()-3*864e5,by={};
     ((d||{}).features||[]).forEach(function(f){var q=f.properties||{},g=(f.geometry||{}).coordinates,t=Date.parse(q.time),v=+q.value,k;
       if(!g||!q.monitoring_location_id||q.value==null||q.value===''||!isFinite(v)||v<=-999||!(t>old))return;
       k=q.monitoring_location_id+'|'+q.parameter_code;
       if(!by[k]||t>Date.parse(by[k].t))by[k]={site:String(q.monitoring_location_id).replace(/^USGS-/,''),lat:+g[1],lon:+g[0],code:q.parameter_code,v:v,t:q.time}});
     var ts=Object.keys(by).map(function(k){return by[k]});
     /* the river, not a ditch: of the gauges reporting flow within 15 km, the one carrying the most water; else the nearest
        flow gauge; a stage-only gauge when none reports flow */
     var fl=ts.filter(function(x){return x.code==='00060'}),ns=near(fl.length?fl:ts,p,function(x){return x.lat},function(x){return x.lon}),
       big=ns.filter(function(n){return n.d<=15&&n.x.code==='00060'}).sort(function(a,b){return b.x.v-a.x.v})[0],pick=big||ns[0];
     if(!pick)return null;var x=pick.x;
     /* asked for the level: the same gauge's gage height (00065, ft) when it reports one; else its flow, saying so */
     if(lv&&x.code==='00060'){var gh=ts.filter(function(y){return y.site===x.site&&y.code==='00065'})[0];if(gh)x=gh}
     var nm=load('usgsname.'+x.site,null);
     return(nm?Promise.resolve(nm):call('usgs',O+'monitoring-locations/items/USGS-'+x.site+'?f=json',s).then(function(m){
         var n=(((m||{}).properties)||{}).monitoring_location_name;if(n)save('usgsname.'+x.site,n);return n},function(){return null}))
       .then(function(n){return{value:x.code==='00060'?num(x.v)+' ft³/s':num(x.v,2)+' ft',
         what:x.code==='00060'?(lv?'flowing (this gauge reports flow, not level)':'flowing'):'gage height',station:n?usgsName(n):'USGS gauge '+x.site,km:pick.d,t:x.t,v:x.v,code:x.code,lv:lv,
         link:'https://waterdata.usgs.gov/monitoring-location/'+x.site+'/'}})})}},
 'nws':{pid:'nws',name:'NWS',every:20,radius:60,cc:['us'],noun:'weather station',area:'the US',eg:'weather, Austin, Texas',
   get:function(p,s){var B='https://api.weather.gov';
     return call('nws',B+'/points/'+r2(p.lat).toFixed(3)+','+r2(p.lon).toFixed(3),s).then(function(pt){var u=((pt||{}).properties||{}).observationStations;if(!u)return null;
       return call('nws',u,s)}).then(function(st){var f=st&&((st.features||[])[0]);if(!f)return null;var sp=f.properties,g=f.geometry.coordinates;
       return call('nws',B+'/stations/'+encodeURIComponent(sp.stationIdentifier)+'/observations/latest',s).then(function(o){var q=(o||{}).properties||{},c=(q.temperature||{}).value;
         if(c==null&&!q.textDescription)return null;
         return{value:c==null?q.textDescription:num(c)+' °C'+(q.textDescription?', '+q.textDescription.toLowerCase():''),what:'',station:sp.name,km:km(p.lat,p.lon,g[1],g[0]),t:q.timestamp,c:c,desc:q.textDescription||'',
           link:'https://www.weather.gov/'}})})}},
 'uk-ea-flood':{pid:'ea',name:'Environment Agency',every:15,radius:15,cc:['gb'],noun:'gauge',area:'England',eg:'river, York, England',
   get:function(p,s){var B='https://environment.data.gov.uk/flood-monitoring';
     return call('ea',B+'/id/stations?parameter=level&lat='+r2(p.lat)+'&long='+r2(p.lon)+'&dist=15&_limit=20',s).then(function(d){
       var n=near((d||{}).items||[],p,function(x){return +x.lat},function(x){return +x.long}).filter(function(n){return isFinite(n.d)})[0];if(!n)return null;var st=n.x;
       return call('ea',B+'/id/stations/'+encodeURIComponent(st.notation||st.stationReference)+'/readings?latest',s).then(function(r){
         var it=((r||{}).items||[]).filter(function(x){return typeof x.value==='number'&&/level/.test(x.measure||'')})[0];if(!it)return null;
         return{value:num(it.value,2)+' m',what:'water level',station:st.label+(st.riverName?' ('+st.riverName+')':''),km:n.d,t:it.dateTime,v:it.value,
           link:'https://check-for-flooding.service.gov.uk/station/'+(st.RLOIid||'')}})})}},
 'pegelonline':{pid:'pegel',name:'PEGELONLINE',every:15,radius:25,cc:['de','at'],noun:'gauge',area:'German federal waterways and a few Danube gauges in Austria',eg:'river, Cologne, Germany',
   get:function(p,s){if(p.cc==='at'){var g=AT[p.name];if(!g)return Promise.resolve(null);
       return call('pegel','https://pegelonline.wsv.de/webservices/rest-api/v2/stations/'+g[0]+'.json?includeTimeseries=true&includeCurrentMeasurement=true',s).then(function(x){
         var w=((x||{}).timeseries||[]).filter(function(t){return t.shortname==='W'&&t.currentMeasurement})[0];if(!w)return null;
         return{value:num(w.currentMeasurement.value)+' '+(w.unit||'cm'),what:'water level',station:title(x.longname)+' ('+title((x.water||{}).longname||'Donau')+'), '+g[1]+' '+p.name,km:null,sep:true,v:+w.currentMeasurement.value,unit:w.unit||'cm',
           t:w.currentMeasurement.timestamp,link:'https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr='+encodeURIComponent(x.number||'')}})}
     return call('pegel','https://pegelonline.wsv.de/webservices/rest-api/v2/stations.json?includeTimeseries=true&includeCurrentMeasurement=true&radius=25&latitude='+r2(p.lat)+'&longitude='+r2(p.lon),s).then(function(d){
     var n=near((d||[]).filter(function(x){return x.latitude!=null&&(x.timeseries||[]).some(function(t){return t.shortname==='W'&&t.currentMeasurement})}),p,function(x){return x.latitude},function(x){return x.longitude})[0];
     if(!n)return null;var w=n.x.timeseries.filter(function(t){return t.shortname==='W'})[0],cm=w.currentMeasurement;
     return{value:num(cm.value)+' '+(w.unit||'cm'),what:'water level',station:title(n.x.longname)+((n.x.water||{}).longname?' ('+title(n.x.water.longname)+')':''),km:n.d,t:cm.timestamp,v:+cm.value,unit:w.unit||'cm',
       link:'https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr='+encodeURIComponent(n.x.number||'')}})}},
 'digitraffic-marine-ais':{pid:'digitraffic',name:'Digitraffic',every:1,radius:20,cc:['fi'],noun:'ships',area:'Finland',eg:'ships, Helsinki, Finland',
   get:function(p,s){var rad=p.r||20;return call('digitraffic','https://meri.digitraffic.fi/api/ais/v1/locations?radius='+rad+'&latitude='+r2(p.lat)+'&longitude='+r2(p.lon),s).then(function(d){
     var f=(d||{}).features||[];if(!f.length)return null;var t=Math.max.apply(null,f.map(function(x){return +(x.properties||{}).timestampExternal||0}));
     return{value:f.length+(f.length===1?' ship':' ships'),what:'reporting',station:p.r?'on '+p.name+', within '+rad+' km of its middle':'within '+rad+' km of '+p.name,km:null,t:new Date(t).toISOString(),n:f.length,rad:rad,
       link:'https://www.digitraffic.fi/en/marine-traffic/'}})}},
 'fintraffic-weathercams':{pid:'fintraffic',name:'Fintraffic',every:10,radius:40,cc:['fi','ax'],noun:'road camera',area:'Finland',eg:'road camera, Helsinki, Finland',
   get:function(p,s){var B='https://tie.digitraffic.fi/api/weathercam/v1';
     var list=mem.cams?Promise.resolve(mem.cams):call('fintraffic',B+'/stations',s).then(function(d){
       return mem.cams=((d||{}).features||[]).filter(function(f){return(f.properties||{}).collectionStatus==='GATHERING'}).map(function(f){return{id:f.id,name:f.properties.name,lon:f.geometry.coordinates[0],lat:f.geometry.coordinates[1]}})});
     return list.then(function(cs){var n=near(cs,p,function(x){return x.lat},function(x){return x.lon})[0];if(!n||n.d>40)return null;
       return call('fintraffic',B+'/stations/'+encodeURIComponent(n.x.id)+'/data',s).then(function(d){var ps=((d||{}).presets||[]).filter(function(x){return x.measuredTime})[0];if(!ps)return null;
         var nm=String(n.x.name||'').replace(/^[a-z]+\d+_/i,'').replace(/_/g,' ');
         return{value:'Road camera',what:'',station:nm,km:n.d,t:ps.measuredTime,photo:'https://weathercam.digitraffic.fi/'+encodeURIComponent(ps.id)+'.jpg',
           alt:'Road weather camera at '+nm+', Finland',link:'https://www.digitraffic.fi/en/road-traffic/'}})})}},
 /* NOAA CO-OPS: the nearest water-level station (one station list, kept for the visit), then its latest 6-minute reading
    above MLLW (Great Lakes stations: IGLD). application= names the caller, as CO-OPS asks; it is not required. */
 'noaa-tides':{pid:'coops',name:'NOAA Tides & Currents',every:6,radius:60,cc:['us'],noun:'tide station',area:'US coasts and the Great Lakes',eg:'tide, Santa Monica, California',
   get:function(p,s){var B='https://api.tidesandcurrents.noaa.gov';
     var list=mem.tides?Promise.resolve(mem.tides):call('coops',B+'/mdapi/prod/webapi/stations.json?type=waterlevels',s).then(function(d){
       return mem.tides=((d||{}).stations||[]).map(function(x){return{id:x.id,name:x.name,state:x.state,lat:+x.lat,lon:+x.lng,gl:!!x.greatlakes}})});
     return list.then(function(cs){var n=near(cs,p,function(x){return x.lat},function(x){return x.lon})[0];if(!n||n.d>60)return null;var st=n.x;
       return call('coops',B+'/api/prod/datagetter?station='+encodeURIComponent(st.id)+'&product=water_level&date=latest&datum='+(st.gl?'IGLD':'MLLW')+'&units=metric&time_zone=gmt&format=json&application=irlpls',s).then(function(d){
         var x=((d||{}).data||[])[0];if(!x||x.v===''||x.v==null||!isFinite(+x.v))return null;
         return{value:num(x.v,2)+' m',what:st.gl?'water level (IGLD datum)':'water level above mean lower low water',station:st.name+(st.state?', '+st.state:''),km:n.d,v:+x.v,gl:st.gl,
           t:String(x.t).replace(' ','T')+':00Z',link:'https://tidesandcurrents.noaa.gov/stationhome.html?id='+encodeURIComponent(st.id)}})})}},
 /* earthquakes: the catalogue's events of the past 24 h within 250 km, counted, with the largest named. None is an answer. */
 'usgs-earthquakes':{pid:'usgsq',name:'USGS Earthquakes',every:1,radius:250,cc:null,noun:'catalogue',area:'the world',eg:'earthquakes near Anchorage, Alaska',
   get:function(p,s){return call('usgsq','https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&latitude='+r2(p.lat)+'&longitude='+r2(p.lon)+'&maxradiuskm=250&starttime='+since(24)+'&orderby=time&limit=500',s).then(function(d){
     var f=((d||{}).features||[]).map(function(x){var q=x.properties||{};return{mag:q.mag,place:q.place,t:q.time}});
     /* outside the US the USGS catalogue lists mostly larger quakes (about M4.5 and up): an empty answer there says so */
     return quakes(f,p,new Date((((d||{}).metadata||{}).generated)||now()).toISOString(),'https://earthquake.usgs.gov/earthquakes/map/',null,p.cc&&p.cc!=='us'?'outside the US, USGS lists mostly M4.5 and up':'')})}},
 'emsc-seismicportal':{pid:'emsc',name:'EMSC',every:1,radius:250,cc:null,noun:'catalogue',area:'the world',eg:'earthquakes near Athens, Greece',
   get:function(p,s){return call('emsc','https://www.seismicportal.eu/fdsnws/event/1/query?format=json&lat='+r2(p.lat)+'&lon='+r2(p.lon)+'&maxradius=2.25&starttime='+since(24)+'&orderby=time&limit=500',s).then(function(d){
     var f=((d||{}).features||[]).map(function(x){var q=x.properties||{};return{mag:q.mag,place:title(q.flynn_region),t:q.time}});
     return quakes(f,p,new Date().toISOString(),'https://www.seismicportal.eu/')})}},
 /* GeoNet lists its latest quakes (about two days' worth): the past 24 h within 250 km, when the list reaches back that far */
 'geonet-nz':{pid:'geonet',name:'GeoNet',every:1,radius:250,cc:['nz'],noun:'catalogue',area:'New Zealand',eg:'earthquakes near Wellington, New Zealand',
   get:function(p,s){return call('geonet','https://api.geonet.org.nz/quake?MMI=-1',s).then(function(d){
     var all=((d||{}).features||[]).map(function(x){var q=x.properties||{},g=(x.geometry||{}).coordinates||[];return{mag:q.magnitude,place:q.locality,t:q.time,lat:g[1],lon:g[0]}}),
       old=Math.min.apply(null,all.map(function(x){return Date.parse(x.t)}).concat([now()])),h=Math.max(1,Math.min(24,Math.floor((now()-old)/3600000)));
     return quakes(all.filter(function(x){return Date.parse(x.t)>=now()-h*3600000&&km(p.lat,p.lon,x.lat,x.lon)<=250}),p,new Date().toISOString(),'https://www.geonet.org.nz/earthquake',h)})}},
 /* Hub'Eau: stations in service within 15 km, then their water heights of the last 2 h; the nearest one reporting.
    Heights come in mm (Hub'Eau: "mm pour les hauteurs d'eau"), shown in m. */
 'hubeau-hydrometrie':{pid:'hubeau',name:'Hub’Eau',every:10,radius:15,cc:['fr'],noun:'gauge',area:'France',eg:'river level, Paris, France',
   get:function(p,s){var B='https://hubeau.eaufrance.fr/api/v2/hydrometrie';
     return call('hubeau',B+'/referentiel/stations?latitude='+r2(p.lat)+'&longitude='+r2(p.lon)+'&distance=15&en_service=true&size=50&format=json&fields=code_station,libelle_station,latitude_station,longitude_station',s).then(function(d){
       var ns=near((d||{}).data||[],p,function(x){return x.latitude_station},function(x){return x.longitude_station}).filter(function(n){return isFinite(n.d)}).slice(0,20);if(!ns.length)return null;
       return call('hubeau',B+'/observations_tr?code_entite='+ns.map(function(n){return encodeURIComponent(n.x.code_station)}).join(',')+'&grandeur_hydro=H&date_debut_obs='+since(2)+'Z&size=1000&sort=desc&fields=code_station,date_obs,resultat_obs',s).then(function(o){
         var last={};((o||{}).data||[]).forEach(function(x){if(!last[x.code_station]&&typeof x.resultat_obs==='number')last[x.code_station]=x});
         var n=ns.filter(function(n){return last[n.x.code_station]})[0];if(!n)return null;var x=last[n.x.code_station];
         return{v:x.resultat_obs/1000,value:num(x.resultat_obs/1000,2)+' m',what:'water height',station:String(n.x.libelle_station||'').split(' - ').slice(0,2).join(' - '),km:n.d,t:x.date_obs,link:'https://hubeau.eaufrance.fr/page/api-hydrometrie'}})})}},
 /* Sensor.Community: citizen sensors' PM2.5 (value_type P2) of the last 5 minutes within 5 km, as the median of the sensors */
 'sensor-community':{pid:'sc',name:'Sensor.Community',every:5,radius:5,cc:null,noun:'sensor',area:'the world',eg:'air quality, Berlin, Germany',
   fallback:{cc:['us'],id:'airnow'},plan:noAir,
   get:function(p,s){return call('sc','https://data.sensor.community/airrohr/v1/filter/area='+r2(p.lat)+','+r2(p.lon)+',5',s).then(function(d){
     var by={};(d||[]).forEach(function(m){var v=(m.sensordatavalues||[]).filter(function(x){return x.value_type==='P2'})[0],id=(m.sensor||{}).id,t=String(m.timestamp||'').replace(' ','T')+'Z';
       if(v&&isFinite(+v.value)&&+v.value>=0&&id!=null&&(!by[id]||by[id].t<t))by[id]={v:+v.value,t:t}});
     return airMedian(Object.keys(by).map(function(k){return by[k]}),p)})}},
 /* AirNow's reporting-area file (no key; refreshed at :25 and :55, about 2 MB, read once per half hour): the nearest area's
    current observations, its AQI being the highest of its pollutants */
 'airnow':{pid:'airnow',name:'AirNow',every:30,radius:80,cc:['us'],noun:'reporting area',area:'the US',eg:'air quality, Denver, Colorado',
   get:function(p,s){var f=mem.airnow&&now()-mem.airnow.at<1800000?Promise.resolve(mem.airnow.rows):call('airnow','https://files.airnowtech.org/airnow/today/reportingarea.dat',s,text).then(function(t){
       var rows=String(t||'').split(/\r?\n/).map(function(l){return l.split('|')}).filter(function(c){return c.length>=14&&c[5]==='O'&&c[12]!==''&&isFinite(+c[12])&&isFinite(+c[9])});
       mem.airnow={at:now(),rows:rows};return rows});
     return f.then(function(rows){var n=near(rows,p,function(c){return +c[9]},function(c){return +c[10]})[0];if(!n||n.d>80)return null;var a=n.x[7],st=n.x[8];
       var top=rows.filter(function(c){return c[7]===a&&c[8]===st}).sort(function(x,y){return +y[12]-+x[12]})[0],t=anTime(top);if(!t)return null;
       return{aqi:Math.round(+top[12]),cat:top[13],pol:top[11]==='OZONE'?'ozone':top[11],value:'AQI '+Math.round(+top[12]),what:'('+top[13].toLowerCase()+', '+(top[11]==='OZONE'?'ozone':top[11])+')',station:a+', '+st,km:n.d,t:t,link:'https://www.airnow.gov/'}})}},
 /* TfL JamCams: the nearest camera reporting available, and its still; the still's own Last-Modified is the capture time */
 'tfl-jamcams':{pid:'tfl',name:'TfL',every:3,radius:2,cc:['gb'],noun:'camera',area:'London',eg:'traffic camera, London',
   get:function(p,s){return call('tfl','https://api.tfl.gov.uk/Place?lat='+r2(p.lat)+'&lon='+r2(p.lon)+'&radius=2000&type=JamCam',s).then(function(d){
     var prop=function(c,k){return ((c.additionalProperties||[]).filter(function(a){return a.key===k})[0]||{}).value};
     var n=near(((d||{}).places||[]).filter(function(c){return prop(c,'available')==='true'&&/^https:\/\//.test(prop(c,'imageUrl')||'')}),p,function(c){return c.lat},function(c){return c.lon})[0];if(!n)return null;
     var img=prop(n.x,'imageUrl'),view=prop(n.x,'view');
     return call('tfl',img,s,function(res){return res.headers.get('Last-Modified')}).then(function(lm){var t=Date.parse(lm||'');if(isNaN(t))return null;
       return{value:'Traffic camera',what:'',station:n.x.commonName,km:n.d,t:new Date(t).toISOString(),photo:img,alt:'TfL traffic camera at '+n.x.commonName+', London'+(view?' (view: '+view+')':''),link:'https://tfl.gov.uk/traffic/status/'}})})}},
 /* FMI: weather stations' 2 m air temperature (t2m, °C) and 10-minute wind (ws_10min, m/s) of the last 40 minutes in a box around the point */
 'fmi-open-data':{pid:'fmi',name:'FMI',every:10,radius:40,cc:['fi','ax'],noun:'weather station',area:'Finland',eg:'weather, Helsinki, Finland',
   get:function(p,s){var dl=.3,dn=.6;
     return call('fmi','https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature&storedquery_id=fmi::observations::weather::simple&parameters=t2m,ws_10min&starttime='+since(40/60)+'Z&bbox='+[p.lon-dn,p.lat-dl,p.lon+dn,p.lat+dl].map(function(v){return v.toFixed(2)}).join(','),s,text).then(function(t){
       var doc=new DOMParser().parseFromString(String(t||''),'application/xml'),st={};
       [].forEach.call(doc.getElementsByTagNameNS('*','BsWfsElement'),function(e){var g=function(n){var x=e.getElementsByTagNameNS('*',n)[0];return x?x.textContent.trim():''};
         var pos=g('pos'),v=+g('ParameterValue'),tm=g('Time'),k=g('ParameterName');if(!pos||!isFinite(v)||g('ParameterValue')==='NaN')return;
         var o=st[pos]=st[pos]||{lat:+pos.split(/\s+/)[0],lon:+pos.split(/\s+/)[1]};if(!o[k]||o[k].t<tm)o[k]={v:v,t:tm}});
       var n=near(Object.keys(st).map(function(k){return st[k]}).filter(function(o){return o.t2m}),p,function(o){return o.lat},function(o){return o.lon})[0];if(!n||n.d>40)return null;var o=n.x;
       return{c:o.t2m.v,ws:o.ws_10min?o.ws_10min.v:null,value:num(o.t2m.v)+' °C'+(o.ws_10min?', wind '+num(o.ws_10min.v)+' m/s':''),what:'',station:'FMI weather station',km:n.d,t:o.t2m.t,link:'https://en.ilmatieteenlaitos.fi/'}})}}};

/* Sensor.Community PM2.5 as one answer, robust to a broken sensor:
   - a reading at the sensor's ceiling (999.9 on an SDS011, 1000 on others) is a saturated or failed sensor, never air: dropped;
   - with 3 or more sensors, the median, after dropping a sensor far from the rest (more than 8 median absolute deviations
     and more than 50 µg/m³ from the median): one broken sensor among working ones;
   - one or two sensors: their median, and when it is extreme (over 250 µg/m³, "hazardous" territory) it is flagged as
     unconfirmed by any nearby sensor, never stated as fact. No sensor left -> null (the route's fallback, or "none"). */
var PM_CEIL=999,PM_EXTREME=250;
function median(vs){vs=vs.slice().sort(function(a,b){return a-b});var h=vs.length>>1;return vs.length%2?vs[h]:(vs[h-1]+vs[h])/2}
function airMedian(xs,p){xs=xs.filter(function(x){return x.v<PM_CEIL});if(!xs.length)return null;
  var vs=xs.map(function(x){return x.v}),m=median(vs),dropped=0;
  if(vs.length>=3){var mad=median(vs.map(function(v){return Math.abs(v-m)}))||1;
    var keep=xs.filter(function(x){return Math.abs(x.v-m)<=Math.max(8*mad,50)});dropped=xs.length-keep.length;xs=keep;vs=xs.map(function(x){return x.v});m=median(vs)}
  var n=vs.length,t=xs.map(function(x){return x.t}).sort().pop(),one=n===1?' citizen sensor':' citizen sensors';
  var what=n>=3?'PM2.5, the median of '+n+one+(dropped?' ('+dropped+' outlier'+(dropped===1?'':'s')+' left out)':''):
    'PM2.5 from '+n+one+(m>PM_EXTREME?', unconfirmed: no other sensor nearby agrees':'');
  return{v:m,n:n,dropped:dropped,value:num(m)+' µg/m³',what:what,station:'within 5 km of '+p.name,km:null,t:t,link:'https://maps.sensor.community/',unconfirmed:n<3&&m>PM_EXTREME}}
/* no free browser source for air here: the plan, in words (the OpenAQ card carries official stations, free with a key) */
function noAir(p){return['No free live source for air near '+p.name+'.','None that a browser can read reports there. Your agent can ask OpenAQ: it carries official stations, free with a key.']}
/* ISO time h hours ago, to the minute (so a repeat ask within the minute is the same URL) */
function since(h){return new Date(Math.floor((now()-h*3600000)/60000)*60000).toISOString().slice(0,19)}
/* a catalogue's events as one answer: how many, and the largest; none is an answer too */
function quakes(f,p,t,link,h,scope){h=h||24;f=f.filter(function(x){return x.mag!=null&&isFinite(+x.mag)});
  var w='in the past '+h+' h within 250 km of '+p.name;
  /* none is an answer, but its time is when the catalogue was checked (checked:true), never a quake's */
  if(!f.length)return{value:'No earthquakes',what:'recorded '+w+(scope?' ('+scope+')':''),station:'',km:null,t:t,link:link,checked:true,n:0,h:h,scope:scope||''};
  var big=f.slice().sort(function(a,b){return b.mag-a.mag})[0];
  return{value:f.length+(f.length===1?' earthquake':' earthquakes'),what:w,station:(f.length===1?'':'largest ')+'M\u00a0'+(+big.mag).toFixed(1)+(big.place?', '+big.place:''),km:null,sep:true,t:t,link:link,
    n:f.length,h:h,scope:scope||'',big:{mag:+big.mag,place:big.place||'',t:big.t==null?null:new Date(typeof big.t==='number'||/^\d+$/.test(big.t)?+big.t:big.t).toISOString()}}}
/* AirNow's local valid date (mm/dd/yy), hour and time-zone name as UTC; an unknown zone name gives no time (and no answer) */
var TZ={EST:-5,EDT:-4,CST:-6,CDT:-5,MST:-7,MDT:-6,PST:-8,PDT:-7,AKST:-9,AKDT:-8,HST:-10,AST:-4};
function anTime(c){if(!c)return null;var d=c[1].split('/'),hm=(c[2]||'').split(':'),z=c[3]==='ADT'?(c[8]==='AK'?-8:-3):TZ[c[3]];
  if(z==null||d.length!==3||hm.length<2)return null;var t=Date.UTC(2000+ +d[2],+d[0]-1,+d[1],+hm[0]-z,+hm[1]);return isNaN(t)?null:new Date(t).toISOString()}

/* ---------- the free services, as the landing's screen 3 offers them: a short label, one everyday question and a topic each ----------
   (every one is a route above: free, keyless and readable by a browser) */
var SERVICES=[
 ['usgs-water','USGS rivers','River level in Austin, Texas?','river'],['nws','NWS weather','Weather in Denver right now?','weather'],
 ['noaa-tides','NOAA tides','Tide at Santa Monica?','tide'],['usgs-earthquakes','USGS quakes','Earthquakes near Anchorage, Alaska?','quake'],
 ['airnow','AirNow air','Air quality in Denver?','air'],['tfl-jamcams','London cams','Traffic camera in London?','cam'],
 ['fintraffic-weathercams','Finnish road cams','Road camera near Tampere?','cam'],['digitraffic-marine-ais','Finnish ships','Ships near Helsinki?','ship'],
 ['uk-ea-flood','England rivers','River level in York?','river'],['pegelonline','German rivers','Rhine level at Cologne?','river'],
 ['hubeau-hydrometrie','French rivers','River level in Paris?','river'],['sensor-community','Citizen air sensors','Air quality in Berlin?','air'],
 ['emsc-seismicportal','EMSC quakes','Earthquakes near Athens, Greece?','quake'],['geonet-nz','GeoNet quakes','Earthquakes near Wellington?','quake'],
 ['fmi-open-data','FMI weather','Weather in Helsinki?','weather']].filter(function(s){return ROUTES[s[0]]}).map(function(s){return{id:s[0],label:s[1],q:s[2],t:s[3]}});

/* ---------- one ask, start to finish, in the words both pages show (plain text; the page escapes it) ----------
   ask(cardId, text, parsed, onStart) -> Promise of
     {kind:'answer', id (the card that answered), asked (the tapped route's name), name, r, p, why}
     {kind:'msg', head, rest, again?, sug?, wait?}   wait: seconds before Try again may be tapped (busy, budget)
   onStart(status line, place) is called just before the first request, so the page can say who it is asking. */
var VIA={'geonet-nz':'New Zealand’s own catalogue lists smaller quakes','emsc-seismicportal':'Europe’s catalogue lists smaller quakes there',
  'airnow':'no Sensor.Community sensor nearby gave a trustworthy reading','usgs-earthquakes':'GeoNet covers New Zealand only'};
/* the world table, loaded once, before the first place is read (a failed load still answers, from the small table above) */
function ready(){return window.IRLPlaces?window.IRLPlaces.load().then(function(){},function(){}):Promise.resolve()}
function ask(id,q,parsed,onStart){return ready().then(function(){return ask1(id,q,parsed,onStart)})}
function ask1(id,q,parsed,onStart){var R=ROUTES[id];if(!R)return Promise.resolve({kind:'msg',head:'No live route for this provider.'});
  var p=place(q,parsed||{}),n=R.name;
  /* a place this page can't put on the map: offer a big city it can, in the same state when the ask names one */
  if(p.missing){var sc=parsed&&parsed.state&&place(parsed.state),sug=R.eg;
    if(p.missing==='known'&&sc&&!sc.missing)sug=q.replace(p.heard,sc.name.split(',')[0]);
    return Promise.resolve(p.missing==='known'?{kind:'msg',head:'Couldn’t place '+p.heard+'.',rest:'Add the country, e.g. “Munich, Germany”. Or try:',sug:sug}:{kind:'msg',head:'Add a place.',rest:'Try:',sug:sug})}
  if(R.cc&&R.cc.indexOf(p.cc)<0)return Promise.resolve({kind:'msg',head:n+' covers '+R.area+' only.',rest:'Add a place there, e.g. “'+R.eg+'”.'});
  if(onStart)onStart('Asking '+n+' near '+p.name+'…',p);
  return get(id,p,null,{ask:q}).then(function(x){
    if(!x.ok){
      if(x.kind==='busy'||x.kind==='budget')return{kind:'msg',head:x.kind==='busy'?n+' is busy.':'Paused, to go easy on providers.',rest:'',again:true,wait:x.wait||60};
      if(x.kind==='none'){if(x.plan)return{kind:'msg',head:x.plan[0],rest:x.plan[1]};
        return{kind:'msg',head:R.noun==='ships'?'No ships reporting within '+(p.r||20)+' km of '+p.name+'.':'No '+n+' '+R.noun+' reporting near '+p.name+'.',rest:'Try a nearby city.'}}
      return{kind:'msg',head:n+' didn’t answer.',rest:'It may be down.',again:true}}
    var by=x.via&&ROUTES[x.via]?x.via:id;
    return{kind:'answer',id:by,asked:n,name:ROUTES[by].name,r:x.r,p:p,why:by!==id?(VIA[by]||''):'',cached:x.cached}})}
/* the answer as words: "<value>" and the rest ("flowing · Colorado River at Austin, TX"); the place once */
function where(r,p){var city=p.name.split(',')[0];return r.km==null||(r.km<15&&r.station.toLowerCase().indexOf(city.toLowerCase())>-1)?r.station:r.station+', '+(r.km<1?'under 1':Math.round(r.km))+' km from '+p.name}
function sentence(r,p){var w=where(r,p);return{value:r.value,rest:(r.what?' '+r.what:'')+(w?(r.km!=null||r.sep?' · ':' ')+w:'')}}
/* the answer as a reply (the owner, 2026-10-30): a sentence an assistant would write, built only from the reading. One
   template per service. A short bold verdict only where the data itself gives one (a count of quakes: yes or no; AirNow's own
   category); otherwise the figure is the bold part. Then the units, the place, how fresh, and context the provider gives.
   Never a verdict the data doesn't support (no "high" or "normal" without a reference the provider states).
   reply(answer) -> [segments]: a string is plain text, {b:text} is bold. Plain text; the page escapes it. */
function the(s){return /^(USGS|the |[a-z])/.test(s)||!/\b(River|Rv|Creek|Brook|Bayou|Canal|Fork|Branch|Lake|Reservoir|Slough|Run)\b/.test(String(s).split(/ (at|near|above|below) /)[0])?s:'the '+s}
function fresh(t,verb){return (verb||'measured')+' '+ago(t)+' ago'}
function far(r,p){return r.km!=null&&r.km>=15?' The station is '+Math.round(r.km)+' km from '+p.name+'.':''}
function cap(s){s=String(s||'');return s.charAt(0).toUpperCase()+s.slice(1)}
function plural(n,one,many){return n+' '+(n===1?one:many)}
var REPLY={
 'usgs-water':function(r,p){var st=the(r.station);
   if(r.code==='00065')return['The gauge on '+st+' reads ',{b:num(r.v,2)+' ft'},' (gage height), '+fresh(r.t)+'.'+far(r,p)];
   return['Right now '+st+' is flowing at ',{b:num(r.v)+' ft³/s'},(r.lv?' (this gauge reports flow, not level)':'')+', '+fresh(r.t)+'.'+far(r,p)]},
 'nws':function(r,p){var d=r.desc?r.desc.toLowerCase():'';
   if(r.c==null)return[{b:cap(d)},' at '+r.station+', '+fresh(r.t,'observed')+'.'+far(r,p)];
   return['It’s ',{b:num(r.c)+' °C'},(d?' and '+d:'')+' at '+r.station+', '+fresh(r.t,'observed')+'.'+far(r,p)]},
 'fmi-open-data':function(r,p){return['It’s ',{b:num(r.c)+' °C'},(r.ws!=null?' with wind at '+num(r.ws)+' m/s':'')+' at the FMI weather station '+(r.km<1?'in':'near')+' '+p.name+', '+fresh(r.t,'observed')+'.'+far(r,p)]},
 'uk-ea-flood':function(r,p){return['The water level at '+r.station+' is ',{b:num(r.v,2)+' m'},', '+fresh(r.t)+'.'+far(r,p)]},
 'pegelonline':function(r,p){return['The water level at '+r.station+' reads ',{b:num(r.v)+' '+r.unit},' on the gauge, '+fresh(r.t)+'.'+far(r,p)]},
 'hubeau-hydrometrie':function(r,p){return['The water height at '+r.station+' is ',{b:num(r.v,2)+' m'},', '+fresh(r.t)+'.'+far(r,p)]},
 'noaa-tides':function(r,p){return['The water at '+r.station+' is ',{b:num(r.v,2)+' m'},' '+(r.gl?'on the IGLD datum':'above mean lower low water')+', '+fresh(r.t)+'.'+far(r,p)]},
 'quake':function(r,p){var w='within 250 km of '+p.name+' in the past '+r.h+' h';
   if(!r.n)return[{b:'No earthquakes'},' recorded '+w+'.'+(r.scope?' ('+cap(r.scope)+'.)':'')];
   var b=r.big||{},m='M'+(+b.mag).toFixed(1)+(b.place?', '+b.place:'')+(b.t?', '+ago(b.t)+' ago':'');
   return[{b:'Yes, '+plural(r.n,'earthquake','earthquakes')},' '+w+(r.n===1?': ':'; the largest was ')+m+'.']},
 'airnow':function(r,p){var c=String(r.cat||'').toLowerCase();return c?['Air quality in '+r.station+' is ',{b:c},': AQI '+r.aqi+' ('+r.pol+'), '+fresh(r.t,'observed')+'.'+far(r,p)]:['Air quality in '+r.station+': ',{b:'AQI '+r.aqi},' ('+r.pol+'), '+fresh(r.t,'observed')+'.'+far(r,p)]},
 'sensor-community':function(r,p){var h=r.n>=3?'the median of '+r.n+' citizen sensors'+(r.dropped?' ('+plural(r.dropped,'outlier','outliers')+' left out)':''):'from '+plural(r.n,'citizen sensor','citizen sensors');
   return['PM2.5 within 5 km of '+p.name+' is ',{b:num(r.v)+' µg/m³'},', '+h+', '+fresh(r.t)+'.'+(r.unconfirmed?' Unconfirmed: no other sensor nearby agrees.':'')]},
 'cam':function(r,p){return[{b:'The latest still'},' from the '+r.station+' camera, taken '+ago(r.t)+' ago.'+far(r,p)]},
 'digitraffic-marine-ais':function(r,p){return[{b:plural(r.n,'ship is','ships are')},' reporting '+r.station+'; the latest position came in '+ago(r.t)+' ago.']}};
REPLY['usgs-earthquakes']=REPLY['emsc-seismicportal']=REPLY['geonet-nz']=REPLY.quake;REPLY['tfl-jamcams']=REPLY['fintraffic-weathercams']=REPLY.cam;
function reply(x){var f=REPLY[x.id],s=sentence(x.r,x.p);return f?f(x.r,x.p):[{b:s.value},s.rest]}
function ago(t){var m=Math.max(1,Math.round((now()-Date.parse(t))/60000));return m<90?m+' min':m<2880?Math.round(m/60)+' h':Math.round(m/1440)+' d'}
function clock(t){var d=new Date(t),s;try{s=d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}catch(e){s=''}
  return new Date().toDateString()===d.toDateString()?s:s+', '+d.toLocaleDateString([],{month:'short',day:'numeric'})}

function route(id){return ROUTES[id]||null}
/* the regional catalogue answers a quake ask where it sees more than the global one: GeoNet for New Zealand, EMSC for Europe
   and the Mediterranean (the same rule the reference library's broker documents) */
var EUROPE=['al','at','ba','be','bg','ch','cy','cz','de','dk','ee','es','fi','fr','gb','gr','hr','hu','ie','is','it','lt','lu','lv','me','mk','mt','nl','no','pl','pt','ro','rs','se','si','sk','tr','ua'];
function regional(id,p){if(!/^(usgs-earthquakes|emsc-seismicportal|geonet-nz)$/.test(id))return id;
  if(p.cc==='nz')return 'geonet-nz';if(EUROPE.indexOf(p.cc)>-1)return 'emsc-seismicportal';return id==='geonet-nz'?'usgs-earthquakes':id}
/* one lookup: the cached reading while it is still the newest the provider can have, else one fetch */
function get(id,p,step,opt){var to=regional(id,p);
  if(to!==id)return one(to,p,step,opt).then(function(x){if(x.ok)x.via=to;return x});
  var R=ROUTES[id];if(!R)return Promise.resolve({ok:false,kind:'none'});
  var fb=R.fallback&&(!R.fallback.cc||R.fallback.cc.indexOf(p.cc)>-1)&&ROUTES[R.fallback.id]?R.fallback.id:null;
  return one(id,p,step,opt).then(function(x){
    if(!x.ok&&x.kind==='none'&&fb)return one(fb,p,step,opt).then(function(y){if(y.ok)y.via=fb;else if(R.plan)y.plan=R.plan(p);return y});
    if(!x.ok&&x.kind==='none'&&R.plan)x.plan=R.plan(p);return x})}
function one(id,p,step,opt){var R=ROUTES[id];if(!R)return Promise.resolve({ok:false,kind:'none'});
  var o={level:LEVEL.test(String((opt||{}).ask||''))};
  var key=id+'|'+p.lat.toFixed(3)+','+p.lon.toFixed(3)+(o.level&&id==='usgs-water'?'|level':''),c=mem[key]||load('r.'+key,null);
  if(c&&c.r&&now()<Math.max(Date.parse(c.r.t)+R.every*60000,c.at+MIN))return Promise.resolve({ok:true,r:c.r,cached:true});
  var w=blocked(id);if(w)return Promise.resolve({ok:false,kind:'busy',wait:w});
  return R.get(p,step,o).then(function(r){if(!r)return{ok:false,kind:'none'};var v={r:r,at:now()};mem[key]=v;save('r.'+key,v);return{ok:true,r:r,cached:false}},
    function(e){return e instanceof Busy?{ok:false,kind:e.kind,wait:e.wait}:{ok:false,kind:'down'}})}
window.IRLLive={route:route,place:place,get:get,blocked:blocked,regional:regional,airMedian:airMedian,ids:Object.keys(ROUTES),
  services:SERVICES,ask:ask,ready:ready,sentence:sentence,reply:reply,ago:ago,clock:clock,
  reset:function(){next={};block={};mem={};try{Object.keys(ST||{}).forEach(function(k){if(k.indexOf('irlpls.live.')===0)ST.removeItem(k)})}catch(e){}}};
})();
