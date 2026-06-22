"""재사용 미니 관계도(force-graph) 섹션 빌더: 토픽·사용자 메타 탭 공용.

콘텐츠 탭과 동일한 force-graph 엔진을 작은 카드 형태로 재사용한다.
정책·데이터는 그대로 두고 '관계도' 시각화만 보강하는 용도.
"""
import json

from .dashboard import _vendor_js


def vendor_script() -> str:
    """force-graph(MIT) UMD 를 페이지당 한 번 주입."""
    return "<script>%s</script>" % _vendor_js()


_JS = r"""(function(){
var D=__DATA__,COL=__COL__,GID="__GID__";
var el=document.getElementById(GID); if(!el||!window.ForceGraph) return;
var adj={}; D.links.forEach(function(l){(adj[l.s]=adj[l.s]||{})[l.t]=1;(adj[l.t]=adj[l.t]||{})[l.s]=1;});
var hi=null,pin=null;
function near(){ if(!hi) return null; var s={}; s[hi]=1; var a=adj[hi]||{}; for(var k in a) s[k]=1; return s; }
function hexA(h,a){h=(h||'#888').replace('#','');return 'rgba('+parseInt(h.slice(0,2),16)+','+parseInt(h.slice(2,4),16)+','+parseInt(h.slice(4,6),16)+','+a+')';}
function lid(x){return (x&&x.id!==undefined)?x.id:x;}
var G=ForceGraph()(el)
 .graphData({nodes:D.nodes.map(function(n){return Object.assign({},n);}),links:D.links.map(function(l){return {source:l.s,target:l.t,w:l.w||1};})})
 .backgroundColor('#010102').nodeRelSize(5).nodeVal(function(n){return n.val||3;})
 .nodeColor(function(n){var ns=near();var c=COL[n.kind]||'#8b93a7';return (ns&&!ns[n.id])?hexA(c,0.3):c;})
 .nodeCanvasObjectMode(function(){return 'after';})
 .nodeCanvasObject(function(n,ctx,scale){var ns=near();var show=hi?(ns&&ns[n.id]):(n.hub||(n.val||0)>=6); if(!show||scale<0.4)return;
   ctx.font=(12/scale)+'px -apple-system,sans-serif';ctx.textBaseline='middle';
   var x=n.x+(Math.cbrt(n.val||3)*4)/scale+2;
   ctx.lineWidth=3/scale;ctx.strokeStyle='rgba(1,1,2,0.9)';ctx.strokeText(n.label,x,n.y);
   ctx.fillStyle=(ns&&!ns[n.id])?'rgba(180,185,195,0.5)':'#e7eaf0';ctx.fillText(n.label,x,n.y);})
 .linkColor(function(l){var ns=near();if(ns)return (ns[lid(l.source)]&&ns[lid(l.target)])?'rgba(94,106,210,0.55)':'rgba(40,46,58,0.12)';return 'rgba(60,60,68,0.5)';})
 .linkWidth(function(l){var ns=near();var b=Math.min(3,l.w||1);return (ns&&ns[lid(l.source)]&&ns[lid(l.target)])?b+1:b*0.6;})
 .onNodeHover(function(n){el.style.cursor=n?'pointer':'grab';hi=n?n.id:pin;})
 .onNodeClick(function(n){pin=(pin===n.id?null:n.id);hi=pin;})
 .onBackgroundClick(function(){pin=null;hi=null;})
 .autoPauseRedraw(false).cooldownTicks(220);
// 오밀조밀 레이아웃: 반발력 완화 + 원거리 반발 차단(distanceMax) → 분리된 묶음(필터·사건)끼리 응집,
// 링크 거리 단축 + 중심 인력 강화로 콘텐츠 묶음이 허브 주변에 모이게.
try{
  G.d3Force('charge').strength(-120).distanceMax(130);
  var lf=G.d3Force('link'); if(lf) lf.distance(function(l){return 30;}).strength(0.9);
  var cf=G.d3Force('center'); if(cf&&cf.strength) cf.strength(1);
}catch(e){}
function sz(){G.width(el.clientWidth).height(el.clientHeight);}
sz(); if(window.ResizeObserver) new ResizeObserver(sz).observe(el);
setTimeout(function(){try{G.zoomToFit(500,30);}catch(e){}},700);
})();"""


def section(gid: str, nodes: list, links: list, col: dict,
            title: str, hint: str = "", legend=None, height: int = 440) -> str:
    """노드/링크 → 자체 완결 관계도 카드 HTML(force-graph 필요). host CSS 비의존."""
    if not nodes:
        return ""
    js = (_JS.replace("__DATA__", json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False))
          .replace("__COL__", json.dumps(col, ensure_ascii=False))
          .replace("__GID__", gid))
    leg = ""
    if legend:
        leg = ('<div style="display:flex;gap:15px;flex-wrap:wrap;margin-top:9px;font-size:12px;color:#8a8f98">'
               + "".join('<span><i style="display:inline-block;width:9px;height:9px;border-radius:50%%;'
                         'background:%s;margin-right:5px;vertical-align:middle"></i>%s</span>' % (c, n)
                         for n, c in legend) + "</div>")
    return (
        '<div style="margin:16px 0;background:#0f1011;border:1px solid #23252a;border-radius:12px;padding:16px">'
        '<h2 style="margin:0 0 10px;font-size:15px;color:#f7f8f8">%s '
        '<span style="color:#8a8f98;font-weight:400;font-size:12px">%s</span></h2>'
        '<div id="%s" style="width:100%%;height:%dpx;border:1px solid #23252a;border-radius:10px;'
        'background:#010102;cursor:grab"></div>%s</div><script>%s</script>'
        % (title, hint, gid, height, leg, js))
