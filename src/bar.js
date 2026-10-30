/* the pinned header glasses over once the page moves */
(function(){var b=document.querySelector('.top');if(!b)return;function f(){b.classList.toggle('stuck',scrollY>4)}addEventListener('scroll',f,{passive:true});f()})();

/* a page's shared title (.hl): as it scrolls away under the header, its colours flow into the wordmark and stay (the landing does the same for its headline) */
(function(){var b=document.querySelector('.top .brand'),h=document.querySelector('h1.hl');if(!b||!h||document.getElementById('s1'))return;var raf=0,top=document.querySelector('.top');
function set(){raf=0;var r=h.getBoundingClientRect(),t=top.getBoundingClientRect().bottom;b.style.setProperty('--bc',Math.min(1,Math.max(0,(t+16-r.top)/(r.height+16))).toFixed(3))}
addEventListener('scroll',function(){if(!raf)raf=requestAnimationFrame(set)},{passive:true});set()})();
