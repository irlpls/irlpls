/* the pinned header glasses over once the page moves */
(function(){var b=document.querySelector('.top');if(!b)return;function f(){b.classList.toggle('stuck',scrollY>4)}addEventListener('scroll',f,{passive:true});f()})();

/* a page's shared title (.hl): as it scrolls away under the header, its colours flow into the wordmark and stay (the landing does the same for its headline) */
(function(){var b=document.querySelector('.top .brand'),h=document.querySelector('h1.hl');if(!b||!h||document.getElementById('s1'))return;var raf=0,top=document.querySelector('.top');
function set(){raf=0;var r=h.getBoundingClientRect(),t=top.getBoundingClientRect().bottom;b.style.setProperty('--bc',Math.min(1,Math.max(0,(t+16-r.top)/(r.height+16))).toFixed(3))}
addEventListener('scroll',function(){if(!raf)raf=requestAnimationFrame(set)},{passive:true});set()})();

/* a generated document's contents (build.py toc_html): open on a desktop, folded on a phone (closes after a tap), the section in view marked */
(function(){var toc=document.querySelector('details.toc[data-toc]');if(!toc)return;var mq=matchMedia('(min-width:960px)');
function sync(){toc.open=mq.matches}sync();(mq.addEventListener?mq.addEventListener('change',sync):mq.addListener(sync));
toc.addEventListener('click',function(e){if(e.target.closest('a')&&!mq.matches)toc.open=false});
var links=[].slice.call(toc.querySelectorAll('a'));if(!('IntersectionObserver' in window))return;
var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting)links.forEach(function(a){a.classList.toggle('on',a.hash==='#'+e.target.id)})})},{rootMargin:'0px 0px -70% 0px'});
links.forEach(function(a){var h=document.getElementById(decodeURIComponent(a.hash.slice(1)));if(h)io.observe(h)})})();
