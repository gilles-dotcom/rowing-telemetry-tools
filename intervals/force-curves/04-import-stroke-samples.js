// Replace PeachCurves field script. FIT processing ON. Re-process once.
// Timestamped samples allow chart averages to follow edited/new saved intervals.
{
  const MAX_MESSAGES = 16; // Proven budget. Try 128 after this works.
  const N = 21, fit = icu.fit;
  if (!fit || !fit.length) throw new Error("FIT data unavailable");
  const start = Date.parse(icu.activity.start_date)/1000-631065600;
  if (!Number.isFinite(start)) throw new Error("Missing UTC start_date");
  const names=["ForceCurvePort","ForceCurveStarboard","HandleForceCurve"];
  const strokes=[], count=Math.min(MAX_MESSAGES,fit.length);
  for(let q=0;q<count;q++) {
    const m=fit[count===1?0:Math.floor(q*(fit.length-1)/(count-1))];
    if(m._num!==20)continue;
    const nf=m.InstrokePointCount,tf=m.timestamp;
    const n=nf?nf.value:null,t=tf?tf.value:null;
    if(!Number.isInteger(n)||n<2||n>127||!Number.isFinite(t))continue;
    const curves=[];let valid=false;
    for(let k=0;k<3;k++) {
      const f=m[names[k]],v=f?f.value:null,y=[];
      for(let i=0;i<N;i++) {
        const pos=i*(n-1)/(N-1),a=Math.floor(pos),b=Math.min(a+1,n-1);
        const va=v&&v.length>=n?v[a]:null,vb=v&&v.length>=n?v[b]:null;
        if(Number.isFinite(va)&&Number.isFinite(vb)&&va!==65535&&vb!==65535){
          y.push(Math.round((va+(vb-va)*(pos-a))*10)/10);valid=true;
        }else y.push(null);
      }
      curves.push(y);
    }
    if(valid)strokes.push({t:t-start,curves});
  }
  if(!strokes.length)throw new Error("No curves found in sampled FIT messages");
  JSON.stringify({version:2,points:N,sampled:true,messageBudget:MAX_MESSAGES,strokes});
}
