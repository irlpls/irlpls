/* the pinned header glasses over once the page moves */
(function(){var b=document.querySelector('.top');if(!b)return;function f(){b.classList.toggle('stuck',scrollY>4)}addEventListener('scroll',f,{passive:true});f()})();
