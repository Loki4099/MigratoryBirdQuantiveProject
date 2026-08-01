const SR={
 async get(url){const response=await fetch(url);if(!response.ok){let message=`请求失败 (${response.status})`;try{message=(await response.json()).detail||message}catch{}throw new Error(message)}return response.json()},
 num(value,digits=2){return value==null?'—':Number(value).toFixed(digits)},
 pct(value,digits=1){return value==null?'—':`${(Number(value)*100).toFixed(digits)}%`},
 metric(metrics,key){return metrics?.[key]?.value??null},
 tone(value){return value==null?'':Number(value)>=0?'positive':'negative'},
 context(ctx){return `${ctx.metric_version} · ${ctx.period_start} → ${ctx.period_end}`},
 header(){return `<header class="topbar"><a class="brand" href="/"><span class="mark">SR</span><span><span class="eyebrow">Deterministic research</span><h1>US Style Factor Lab</h1></span></a><nav class="nav"><a href="/">排行榜</a><a href="/compare">回测对比</a><a href="/docs">API</a></nav></header>`},
 draw(canvas,series){const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);const all=series.flatMap(s=>s.points.map(p=>Number(p.value))).filter(Number.isFinite);if(!all.length)return;let min=Math.min(...all),max=Math.max(...all);if(min===max){min-=1;max+=1}const pad={l:48,r:14,t:14,b:30},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b;c.strokeStyle='#213a55';c.fillStyle='#8fa4b8';c.font='11px system-ui';for(let i=0;i<5;i++){const y=pad.t+ph*i/4;c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();const value=max-(max-min)*i/4;c.fillText(value.toFixed(2),4,y+4)}series.forEach((s,si)=>{c.strokeStyle=s.color||['#59a8ff','#49d6c6','#f2bd5a','#ff7185'][si%4];c.lineWidth=2;c.beginPath();s.points.forEach((p,i)=>{const x=pad.l+pw*i/Math.max(1,s.points.length-1),y=pad.t+ph*(max-Number(p.value))/(max-min);i?c.lineTo(x,y):c.moveTo(x,y)});c.stroke()});}
};
document.addEventListener('DOMContentLoaded',()=>{document.querySelector('[data-header]').innerHTML=SR.header()});
