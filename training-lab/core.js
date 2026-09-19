/* Pure repeatability calculations; no model, reference targets, or training writes. */
(function(root) {
  const keys = ['shoulder', 'off_shoulder', 'elbow', 'wrist', 'ball'];
  function complete(points) { return keys.every(k => points[k] && Number.isFinite(points[k].x) && Number.isFinite(points[k].y)) && Math.hypot(points.ball.x-points.shoulder.x,points.ball.y-points.shoulder.y)>0; }
  function angle(p) { return Math.atan2(p.shoulder.y-p.ball.y, Math.abs(p.ball.x-p.shoulder.x))*180/Math.PI; }
  function median(values) { if (!values.length) return null; const a=[...values].sort((a,b)=>a-b), m=Math.floor(a.length/2); return a.length%2 ? a[m] : (a[m-1]+a[m])/2; }
  function compare(items, records) {
    return items.map(item => {
      const a=records.find(r=>r.id===item.id && r.pass===1), b=records.find(r=>r.id===item.id && r.pass===2);
      if (!a || !b || a.status!=='labeled' || b.status!=='labeled' || a.frameIndex!==b.frameIndex) return {id:item.id, comparable:false};
      const shifts=Object.fromEntries(keys.map(k=>[k, Math.hypot(a.points[k].x-b.points[k].x,a.points[k].y-b.points[k].y)]));
      return {id:item.id, comparable:true, angleDifference:Math.abs(angle(a.points)-angle(b.points)), pointShifts:shifts, meanPointShift:keys.reduce((s,k)=>s+shifts[k],0)/keys.length};
    });
  }
  const api={keys,complete,angle,median,compare};
  if (typeof module!=='undefined') module.exports=api; else root.Lab=api;
})(globalThis);
