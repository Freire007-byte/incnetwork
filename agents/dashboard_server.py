#!/usr/bin/env python3
# Dashboard mobile-first -- porta 8099 -- atualiza a cada 10s
import json, time, os, sys
sys.path.insert(0, "/root/caca-pump/agents")
import db as DB
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT       = 8099
SIM_LOG    = "/tmp/inc_study/sim_results.txt"
SIM_TRADES = "/tmp/inc_study/sim_trades.jsonl"

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Caca Pump</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#060610;color:#e0e0e0;font-family:'Courier New',monospace;font-size:14px}
.hdr{background:linear-gradient(135deg,#0d0d1a,#1a0d00);padding:12px 16px;border-bottom:2px solid #ff6600;position:sticky;top:0;z-index:100}
.hdr h1{color:#ff6600;font-size:1.1rem}.hdr .sub{color:#555;font-size:0.65rem;margin-top:2px}
.tabs{display:flex;background:#0d0d1a;border-bottom:1px solid #222;overflow-x:auto}
.tab{padding:10px 16px;color:#555;cursor:pointer;white-space:nowrap;font-size:0.75rem;border-bottom:2px solid transparent}
.tab.active{color:#ff6600;border-bottom-color:#ff6600}
.pg{display:none;padding:12px}.pg.active{display:block}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.card{background:#13131f;border:1px solid #222244;border-radius:8px;padding:12px}
.card .lbl{color:#666;font-size:0.6rem;text-transform:uppercase;margin-bottom:4px}
.card .val{font-size:1.4rem;font-weight:bold}
.orange{color:#ff6600}.green{color:#00ff88}.blue{color:#00d4ff}.red{color:#ff4466}.yellow{color:#ffcc00}
.sec{background:#13131f;border:1px solid #1a1a2e;border-radius:8px;padding:12px;margin-bottom:12px}
.sec-t{color:#ff6600;font-size:0.75rem;font-weight:bold;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #1a1a2e}
.log{height:280px;overflow-y:auto;font-size:0.65rem;line-height:1.7}
.ll{padding:1px 2px;border-bottom:1px solid #0d0d1a}
.ls{color:#00ff88;background:#00ff8808}.le{color:#00d4ff;background:#00d4ff08}
.lp{color:#00ff88;font-weight:bold}.lx{color:#ff4466;font-weight:bold}.lsk{color:#333}
table{width:100%;border-collapse:collapse;font-size:0.65rem}
th{color:#ff6600;padding:5px 6px;text-align:left;border-bottom:1px solid #1a1a2e;white-space:nowrap}
td{padding:4px 6px;border-bottom:1px solid #0d0d1a;white-space:nowrap;overflow:hidden;max-width:120px;text-overflow:ellipsis}
.prog{background:#1a1a2e;border-radius:4px;height:6px;margin-top:4px;overflow:hidden}
.progb{height:6px;border-radius:4px;background:linear-gradient(90deg,#ff6600,#ffcc00);transition:width .5s}
.big{font-size:2rem;font-weight:bold;text-align:center;padding:16px 0}
.hint{color:#444;font-size:0.6rem;margin-top:6px}
.ftr{text-align:center;padding:12px;color:#333;font-size:0.6rem;border-top:1px solid #111}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>
<div class="hdr"><h1>&#127919; CACA PUMP</h1><div class="sub" id="upd">...</div></div>
<div class="tabs">
  <div class="tab active" onclick="show('sim',this)">&#127921; Simulacao</div>
  <div class="tab" onclick="show('col',this)">&#128200; Coleta 7D</div>
  <div class="tab" onclick="show('pat',this)">&#129504; Padroes</div>
  <div class="tab" onclick="show('net',this)">&#128279; Rede</div>
</div>
<div id="pg-sim" class="pg active">
  <div class="big" id="pnl">+0.00000 SOL</div>
  <div class="cards">
    <div class="card"><div class="lbl">Trades</div><div class="val blue" id="s-tot">0</div></div>
    <div class="card"><div class="lbl">Win Rate</div><div class="val green" id="s-wr">0%</div></div>
    <div class="card"><div class="lbl">Vitorias</div><div class="val green" id="s-win">0</div></div>
    <div class="card"><div class="lbl">Perdas</div><div class="val red" id="s-los">0</div></div>
  </div>
  <div class="sec"><div class="sec-t">&#128202; Trades</div>
    <table><thead><tr><th>Token</th><th>PnL%</th><th>SOL</th><th>Saida</th><th>Min</th></tr></thead>
    <tbody id="tb"></tbody></table>
    <div class="hint" id="hint"></div>
  </div>
  <div class="sec"><div class="sec-t">&#128196; Log</div><div class="log" id="lg"></div></div>
</div>
<div id="pg-col" class="pg">
  <div class="cards">
    <div class="card"><div class="lbl">Tokens</div><div class="val orange" id="c-tok">0</div></div>
    <div class="card"><div class="lbl">Analisados</div><div class="val blue" id="c-ana">0</div></div>
    <div class="card"><div class="lbl">Transacoes</div><div class="val green" id="c-txs">0</div></div>
    <div class="card"><div class="lbl">Carteiras</div><div class="val yellow" id="c-wal">0</div></div>
  </div>
  <div class="sec"><div class="sec-t">Progresso</div>
    <div style="font-size:.65rem;color:#888" id="c-pct">0%</div>
    <div class="prog"><div class="progb" id="c-bar" style="width:0%"></div></div>
  </div>
  <div class="sec"><div class="sec-t">Tokens Recentes</div>
    <table><thead><tr><th>Token</th><th>Padrao</th><th>Liq</th><th>h1%</th><th>Whal</th></tr></thead>
    <tbody id="rtb"></tbody></table>
  </div>
  <div class="sec"><div class="sec-t">Fontes</div><div id="srcs" style="font-size:.65rem;line-height:2"></div></div>
</div>
<div id="pg-pat" class="pg">
  <div class="sec"><div class="sec-t">Padroes Descobertos</div><div id="patlist"></div></div>
  <div class="sec"><div class="sec-t">Estrategia</div><div id="stratlist" style="font-size:.7rem;line-height:2.2"></div></div>
</div>
<div id="pg-net" class="pg">
  <div class="sec"><div class="sec-t">Grupos de Carteiras</div>
    <table><thead><tr><th>G</th><th>Role</th><th>Carts</th><th>Tokens</th><th>SOL</th></tr></thead>
    <tbody id="gtb"></tbody></table>
  </div>
  <div class="sec"><div class="sec-t">Por Tipo</div><div id="wtypes" style="font-size:.7rem;line-height:2.2"></div></div>
</div>
<div class="ftr">Caca Pump v2 | http://153.75.224.178:8099 | <span id="ft">--</span></div>
<script>
const PC={'0':'#00ff88','1':'#ff4466','2':'#00d4ff','3':'#ff9900','4':'#888','5':'#ff3333','6':'#8888ff','-1':'#555'};
const PN={'0':'BALEIA','1':'BOT_SW','2':'LENTO','3':'EXPLOS','4':'ORGANIC','5':'RUG','6':'MISTO','-1':'PEND'};
const STR={'0':['BALEIA_FORTE','Entra 0-3min, sai 10-15min','#00ff88'],'1':['BOT_SWARM','NAO ENTRAR','#ff4466'],
  '2':['LENTO_WHALE','Ate 5min, 20-25min saida','#00d4ff'],'3':['EXPLOSIVO','Imediato, 5-8min','#ff9900'],
  '4':['ORGANIC','Observar','#888'],'5':['RUG_CAND','BLOQUEADO','#ff3333'],'6':['MISTO','Ate 3min cautela','#8888ff']};
function show(id,el){
  document.querySelectorAll('.pg').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pg-'+id).classList.add('active');
  el.classList.add('active');
}
function pc(v){return parseFloat(v)>=0?'#00ff88':'#ff4466'}
function fmt(n,d){return (parseFloat(n)||0).toFixed(d||0)}
async function refresh(){
  try{
    const d=await(await fetch('/api/all')).json();
    const now=new Date().toLocaleTimeString('pt-BR');
    document.getElementById('upd').textContent='att: '+now;
    document.getElementById('ft').textContent=now;
    const s=d.sim||{};
    const pnl=parseFloat(s.total_pnl||0);
    const pe=document.getElementById('pnl');
    pe.textContent=(pnl>=0?'+':'')+pnl.toFixed(5)+' SOL';
    pe.style.color=pnl>=0?'#00ff88':'#ff4466';
    document.getElementById('s-tot').textContent=s.total||0;
    document.getElementById('s-wr').textContent=fmt(s.win_rate,1)+'%';
    document.getElementById('s-win').textContent=s.wins||0;
    document.getElementById('s-los').textContent=s.losses||0;
    document.getElementById('hint').textContent=s.hint||'';
    const rc={'TP':'#00ff88','SL':'#ff4466','TEMPO':'#ffcc00','FIM_SIM':'#8888ff'};
    document.getElementById('tb').innerHTML=[...(d.sim_trades||[])].reverse().slice(0,15).map(t=>{
      const p=parseFloat(t.pnl_pct||0),sl=parseFloat(t.pnl_sol||0);
      return '<tr><td style="color:#fff;font-weight:bold">'+(t.symbol||'?')+'</td>'+
        '<td style="color:'+pc(p)+'">'+(p>=0?'+':'')+p.toFixed(1)+'%</td>'+
        '<td style="color:'+pc(sl)+'">'+(sl>=0?'+':'')+sl.toFixed(4)+'</td>'+
        '<td style="color:'+(rc[t.exit_reason]||'#888')+'">'+(t.exit_reason||'-')+'</td>'+
        '<td>'+fmt(t.hold_min,1)+'m</td></tr>';
    }).join('');
    const lg=document.getElementById('lg');
    const atBot=lg.scrollHeight-lg.clientHeight<=lg.scrollTop+20;
    lg.innerHTML=(d.sim_log||[]).map(l=>{
      let c='';
      if(l.includes('>>> SINAL <<<')||l.includes('[SINAL]'))c='ls';
      else if(l.includes('SIM ENTRADA'))c='le';
      else if(l.includes('LUCRO'))c='lp';
      else if(l.includes('PERDA'))c='lx';
      else if(l.includes('[skip]'))c='lsk';
      return '<div class="ll '+c+'">'+l.replace(/</g,'&lt;')+'</div>';
    }).join('');
    if(atBot)lg.scrollTop=lg.scrollHeight;
    const c=d.coleta||{};
    document.getElementById('c-tok').textContent=(c.tokens||0).toLocaleString();
    document.getElementById('c-ana').textContent=(c.analyzed||0).toLocaleString();
    document.getElementById('c-txs').textContent=(c.txs||0).toLocaleString();
    document.getElementById('c-wal').textContent=(c.wallets||0).toLocaleString();
    const pct=c.tokens>0?Math.round(c.analyzed/c.tokens*100):0;
    document.getElementById('c-pct').textContent=pct+'% ('+c.analyzed+'/'+c.tokens+')';
    document.getElementById('c-bar').style.width=pct+'%';
    document.getElementById('rtb').innerHTML=(d.recent_tokens||[]).map(t=>{
      const col=PC[String(t.pid)]||'#888';
      return '<tr><td style="color:#fff;font-weight:bold">'+(t.symbol||'?')+'</td>'+
        '<td style="color:'+col+';font-size:.6rem">'+(PN[String(t.pid)]||'?')+'</td>'+
        '<td>$'+Math.round(t.liq||0).toLocaleString()+'</td>'+
        '<td style="color:'+(t.h1>=0?'#00ff88':'#ff4466')+'">'+(t.h1>=0?'+':'')+fmt(t.h1,0)+'%</td>'+
        '<td>'+(t.wc||0)+'</td></tr>';
    }).join('');
    document.getElementById('srcs').innerHTML=Object.entries(d.sources||{}).map(([k,v])=>
      '<div><span style="color:#ff6600">'+k+'</span>: <span style="color:#00ff88">'+v+'</span></div>').join('');
    const pats=d.patterns||{};
    document.getElementById('patlist').innerHTML=Object.keys(pats).length>0?
      Object.entries(pats).sort((a,b)=>b[1].token_count-a[1].token_count).map(([pid,p])=>{
        const col=PC[pid]||'#888';
        return '<div style="margin-bottom:12px">'+
          '<div style="display:flex;justify-content:space-between">'+
          '<span style="color:'+col+';font-weight:bold;font-size:.8rem">'+p.name+'</span>'+
          '<span style="color:#888;font-size:.65rem">'+p.token_count+' ('+p.pct_of_total+'%)</span></div>'+
          '<div class="prog" style="margin-top:4px"><div class="progb" style="width:'+Math.min(100,p.pct_of_total*3)+'%;background:'+col+'55"></div></div>'+
          '<div style="font-size:.6rem;color:#555;margin-top:3px">SOL/5m:'+p.avg_sol_5min+' Whal:'+p.avg_whales+' Dur:'+p.avg_duration+'m</div></div>';
      }).join(''):'<div style="color:#333;padding:20px;text-align:center">Aguardando dados...</div>';
    document.getElementById('stratlist').innerHTML=Object.entries(STR).map(([pid,[n,s,c]])=>
      '<div><span style="color:'+c+';font-weight:bold">'+n+':</span> '+s+'</div>').join('');
    document.getElementById('gtb').innerHTML=(d.groups||[]).map(g=>
      '<tr><td>G'+g.gid+'</td><td style="color:#ff9900">'+g.role+'</td>'+
      '<td>'+g.wallets+'</td><td>'+fmt(g.avg_tokens,1)+'</td><td>'+fmt(g.avg_sol,2)+'</td></tr>').join('');
    document.getElementById('wtypes').innerHTML=Object.entries(d.wallet_types||{}).map(([r,c])=>{
      const col=r==='whale'?'#00ff88':r==='bot'?'#ff4466':'#888';
      return '<div><span style="color:'+col+'">'+r.toUpperCase()+'</span>: '+c.toLocaleString()+'</div>';
    }).join('');
  }catch(e){document.getElementById('upd').textContent='Erro: '+e.message;}
  setTimeout(refresh,10000);
}
refresh();
</script>
</body>
</html>"""

def get_sim():
    trades = []
    if os.path.exists(SIM_TRADES):
        for line in open(SIM_TRADES):
            try: trades.append(json.loads(line))
            except: pass
    wins   = [t for t in trades if (t.get("pnl_sol") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_sol") or 0) <= 0]
    pnl    = sum(t.get("pnl_sol", 0) for t in trades)
    wr     = round(len(wins)/max(1, len(trades))*100, 1)
    aw     = round(sum(t.get("pnl_pct",0) for t in wins)/max(1,len(wins)),2) if wins else 0
    al     = round(sum(t.get("pnl_pct",0) for t in losses)/max(1,len(losses)),2) if losses else 0
    hint   = f"Avg vitoria: {aw:+.2f}% | Avg perda: {al:+.2f}%" if trades else ""
    return {"total":len(trades),"wins":len(wins),"losses":len(losses),
            "total_pnl":round(pnl,5),"win_rate":wr,"hint":hint}, trades

def get_log():
    if not os.path.exists(SIM_LOG): return []
    return [l for l in open(SIM_LOG).read().strip().split("\n") if l.strip()][-60:]

def get_db():
    try:
        conn = DB.get_conn()
        nt = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        na = conn.execute("SELECT COUNT(*) FROM token_patterns WHERE pattern_id>=0").fetchone()[0]
        nx = conn.execute("SELECT COUNT(*) FROM token_txs").fetchone()[0]
        nw = conn.execute("SELECT COUNT(DISTINCT wallet) FROM wallet_appearances").fetchone()[0]
        src = {}
        for r in conn.execute("SELECT source,COUNT(*) FROM tokens GROUP BY source").fetchall():
            src[r[0]] = r[1]
        recent = []
        for r in conn.execute("""SELECT t.symbol,t.liq_usd,t.peak_h1,p.pattern_id,p.whale_count,p.sol_5min
            FROM tokens t JOIN token_patterns p ON p.mint=t.mint
            WHERE p.pattern_id>=0 ORDER BY t.collected_at DESC LIMIT 20""").fetchall():
            recent.append({"symbol":r[0],"liq":r[1],"h1":r[2],"pid":r[3],"wc":r[4],"s5":r[5]})
        conn.close()
        return {"tokens":nt,"analyzed":na,"txs":nx,"wallets":nw}, src, recent
    except:
        return {"tokens":0,"analyzed":0,"txs":0,"wallets":0}, {}, []

def get_patterns():
    try:
        if os.path.exists("/root/caca-pump/data/patterns_report.json"):
            return json.load(open("/root/caca-pump/data/patterns_report.json")).get("patterns", {})
    except: pass
    return {}

def get_groups():
    try:
        conn = DB.get_conn()
        rows = conn.execute("""SELECT group_id,role,COUNT(*),AVG(token_count),AVG(total_sol)
            FROM wallet_groups GROUP BY group_id,role ORDER BY 3 DESC LIMIT 10""").fetchall()
        wt = {}
        for r in conn.execute("SELECT role,COUNT(*) FROM wallet_groups GROUP BY role").fetchall():
            wt[r[0]] = r[1]
        conn.close()
        return [{"gid":r[0],"role":r[1],"wallets":r[2],"avg_tokens":r[3],"avg_sol":r[4]} for r in rows], wt
    except:
        return [], {}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/all":
            sim, trades = get_sim()
            coleta, src, recent = get_db()
            groups, wt = get_groups()
            data = json.dumps({
                "sim": sim, "sim_trades": trades[-30:],
                "sim_log": get_log(), "coleta": coleta,
                "sources": src, "recent_tokens": recent,
                "patterns": get_patterns(), "groups": groups,
                "wallet_types": wt, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
    def log_message(self, *a): pass

if __name__ == "__main__":
    print(f"[DASHBOARD] http://153.75.224.178:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
