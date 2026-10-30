/* irlpls labels: the words every page uses for a card's price and its stage levels, in one place.
   The landing (src/index.html) and the finder, card sheets and compare (src/app.js) both read these; build.py mirrors them
   (price_text, LEVEL, stage_says) for the built pages, and tests pin the two to the same words for every card.
   Levels are always: Evidenced / Claimed / Reported (a time only) / Not stated. */
window.IRLLabels=(function(){
function money(n){return n<1?'$'+n:n%1?'$'+n.toFixed(2):'$'+n}
function unit(c){return String((c.pricing||{}).unit||'').replace(/^per /,'').replace(/\bkm2\b/g,'km²').split(/\s*\(|;|,\s/)[0].trim()}
/* price in a few words: "Free", "Free with a key", "Free tier", "$5 / km²", "On request", "Price not stated" */
function priceText(c){var p=c.pricing||{},k=(c.access||{}).cost;
  if(k==='free')return 'Free';if(k==='free with key')return 'Free with a key';if(k==='freemium')return 'Free tier';
  if(p.from_usd)return money(p.from_usd)+(unit(c)?' / '+unit(c):'');
  return k==='quote'?'On request':k==='paid'?'Price not stated':'Not stated'}
var LEVEL={evidenced:'Evidenced',claimed:'Claimed',reported:'Reported',none:'Not stated'};
var SAYS={evidenced:'Evidenced',claimed:'Claimed',reported:'Reported (a time is stated, not attested)',none:'Not stated'};
/* a stage's level with its reason where the word alone would mislead (stage 7, Independently verified, when only claimed) */
function stageSays(i,l){return l==='claimed'&&i===6?'Claimed (the provider says it was checked; no public evidence)':SAYS[l]}
return{money:money,unit:unit,priceText:priceText,LEVEL:LEVEL,stageSays:stageSays}})();
