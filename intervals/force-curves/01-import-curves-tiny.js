// Bounded sampled importer. Text activity field PeachCurves; FIT processing ON.
// Approximate: visits at most MAX_MESSAGES messages, evenly across the FIT file.
{
  const MAX_MESSAGES = 16;
  const N = 21;
  const fit = icu.fit;
  if (!fit) throw new Error("Enable Processes fit file messages");
  const length = fit.length;
  if (!length) throw new Error("No FIT messages available");
  const start = Date.parse(icu.activity.start_date)/1000 - 631065600;
  if (!Number.isFinite(start)) throw new Error("Missing UTC activity start_date");
  const names = ["ForceCurvePort", "ForceCurveStarboard", "HandleForceCurve"];
  const pieces = [{label:"Whole session · sampled",lo:-Infinity,hi:Infinity}];
  const intervals = icu.activity.icu_intervals || [];
  if (intervals.length > 100) throw new Error("Sampled importer supports up to 100 saved pieces");
  for (let j=0;j<intervals.length;j++) {
    const iv=intervals[j];
    if (Number.isFinite(iv.start_time) && Number.isFinite(iv.end_time) && iv.end_time>iv.start_time)
      pieces.push({label:(iv.label || "Piece "+(j+1))+" · sampled",lo:iv.start_time,hi:iv.end_time});
  }
  for (const p of pieces) {
    p.strokes=0;p.sum=[];p.count=[];
    for(let k=0;k<3;k++){p.sum.push(Array(N).fill(0));p.count.push(Array(N).fill(0));}
  }
  const samples=Math.min(length,MAX_MESSAGES);
  const matching=Array(pieces.length).fill(false);
  let found=0;
  for(let q=0;q<samples;q++) {
    const index=samples===1?0:Math.floor(q*(length-1)/(samples-1));
    const m=fit[index];
    if(m._num!==20)continue;
    const nf=m.InstrokePointCount,tf=m.timestamp;
    const n=nf?nf.value:null,t=tf?tf.value:null;
    if(!Number.isInteger(n)||n<2||n>127||!Number.isFinite(t))continue;
    for(let j=0;j<pieces.length;j++)matching[j]=t-start>=pieces[j].lo && t-start<pieces[j].hi;
    let valid=false;
    for(let k=0;k<3;k++) {
      const field=m[names[k]];
      const v=field?field.value:null;
      if(!v||v.length<n)continue;
      let channelValid=false;
      for(let i=0;i<N;i++) {
        const pos=i*(n-1)/(N-1),a=Math.floor(pos),b=Math.min(a+1,n-1),va=v[a],vb=v[b];
        if(!Number.isFinite(va)||!Number.isFinite(vb)||va===65535||vb===65535)continue;
        channelValid=true;
        const y=va+(vb-va)*(pos-a);
        for(let j=0;j<pieces.length;j++)if(matching[j]){pieces[j].sum[k][i]+=y;pieces[j].count[k][i]++;}
      }
      if(channelValid)valid=true;
    }
    if(valid){found++;for(let j=0;j<pieces.length;j++)if(matching[j])pieces[j].strokes++;}
  }
  if(!found)throw new Error("No usable curves in sampled messages. Increase MAX_MESSAGES or check the CURVES FIT file.");
  const result=[];
  for(const p of pieces){
    const curves=[];
    for(let k=0;k<3;k++){
      const y=[];
      for(let i=0;i<N;i++)y.push(p.count[k][i]?Math.round(p.sum[k][i]/p.count[k][i]*10)/10:null);
      curves.push(y);
    }
    result.push({label:p.label,strokes:p.strokes,curves});
  }
  JSON.stringify({version:1,points:N,sampled:true,messageBudget:MAX_MESSAGES,pieces:result});
}
