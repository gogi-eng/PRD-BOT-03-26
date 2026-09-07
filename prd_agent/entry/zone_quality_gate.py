# -*- coding: utf-8 -*-
"""zone_quality_gate - гейт силы S/R зоны для LONG/SHORT."""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Tuple, Mapping
logger = logging.getLogger("prd_agent.zone_quality")
TF_WEIGHTS = {"15":1.0,"60":1.6,"240":2.4,"D":3.2}

def _zq_cfg(cfg):
    d = cfg.get("zone_quality_gate") if isinstance(cfg, dict) else None
    return dict(d) if isinstance(d, dict) else {}

def zone_quality_enabled(cfg):
    return bool(_zq_cfg(cfg).get("enabled", False))

def _simple_zones(klines, kind):
    """Упрощённые S/R зоны из свингов -> list[(low,high,strength_norm)]"""
    if not klines or len(klines) < 14:
        return []
    lows=[float(k.get("low") or 0) for k in klines]
    highs=[float(k.get("high") or 0) for k in klines]
    closes=[float(k.get("close") or 0) for k in klines]
    pl, ph = [], []
    for i in range(2, len(klines)-2):
        if lows[i]<lows[i-1] and lows[i]<=lows[i+1] and lows[i]<lows[i-2] and lows[i]<=lows[i+2]:
            pl.append(lows[i])
        if highs[i]>highs[i-1] and highs[i]>=highs[i+1] and highs[i]>highs[i-2] and highs[i]>=highs[i+2]:
            ph.append(highs[i])
    pivots = pl if kind=="support" else ph
    if not pivots:
        return []
    pivots.sort()
    tol=(max(closes) or 1.0)*0.0035
    groups=[[pivots[0]]]
    for p in pivots[1:]:
        if abs(p-groups[-1][-1])<=tol: groups[-1].append(p)
        else: groups.append([p])
    out=[]
    for g in groups:
        if not g: continue
        lo,hi=min(g),max(g)
        st=min(1.0, 0.25+0.15*len(g))
        out.append((lo,hi,st))
    return out

async def evaluate_zone_quality(sig, exchange, *, klines15=None, cfg=None):
    cfg=dict(cfg or {})
    zc=_zq_cfg(cfg)
    if not bool(zc.get("enabled", False)):
        return True, "zone_quality: off"
    if exchange is None or not hasattr(exchange,"get_klines"):
        return True, "zone_quality: no exchange"
    side_u=str(getattr(sig,"side","") or "").upper()
    if side_u not in ("BUY","LONG","SELL","SHORT"):
        return True,""
    is_buy=side_u in ("BUY","LONG")
    sym=getattr(sig,"symbol",None)
    if not sym:
        return True,""
    import asyncio
    tfs=[str(x) for x in zc.get("tfs",["15","60","240","D"]) if x]
    results=await asyncio.gather(*[exchange.get_klines(sym, interval=tf, limit=180) for tf in tfs], return_exceptions=True)
    zctx={}   # tf -> {"support":[...], "resistance":[...]}
    price=None
    base_k=None
    for tf,kres in zip(tfs, results):
        if isinstance(kres,Exception) or not kres:
            continue
        if price is None and kres:
            price=float(kres[-1].get("close") or 0)
        if tf=="15" and base_k is None:
            base_k=list(kres)
        zctx[tf]={"support":_simple_zones(list(kres),"support"), "resistance":_simple_zones(list(kres),"resistance")}
    if price is None or not zctx:
        return True,"zone_quality: нет данных"
    k15 = klines15 or base_k or []
    # ATR по 15m
    atr=0.0
    if len(k15)>=2:
        hs=[float(k.get("high") or 0) for k in k15[-14:]]
        ls=[float(k.get("low") or 0) for k in k15[-14:]]
        if hs:
            atr=sum(h-l for h,l in zip(hs,ls))/len(hs)
    if atr<=0: atr=price*0.005
    # 1) зона входа со стороны
    need="support" if is_buy else "resistance"
    cand=None; best=-1.0
    for tf,d in zctx.items():
        w=_zone_tf_weight(tf,cfg)
        for (lo,hi,st) in d.get(need,[]):
            if is_buy:
                if price < lo-price*0.015 or price>hi+price*0.02: continue
                pos=(price-lo)/(hi-lo) if hi>lo else 0.0
            else:
                if price > hi+price*0.015 or price<lo-price*0.02: continue
                pos=(price-lo)/(hi-lo) if hi>lo else 1.0
            eff=(0.3+0.7*st)*w
            # предпочтение: Buy ближе к низу (pos~0.2), Sell ближе к верху (pos~0.8)
            prefer_low=zc.get("entry_prefer_low_zone",True) if is_buy else not zc.get("entry_prefer_high_zone",True)
            target_pos=0.25 if is_buy else 0.75
            penalty=abs(pos-target_pos)
            score=eff - penalty*1.2 if zc.get("prefer_zone_edge",True) else eff
            if score>best:
                best=score; cand=(tf,lo,hi,eff,pos)
    if cand is None:
        return False, f"zone_quality: {side_u} нет зоны {need} рядом"
    tf_e,lo_e,hi_e,eff_e,pos_e=cand
    min_st=float(zc.get("min_strength",0.55) or 0.55)
    if eff_e < min_st:
        return False, f"zone_quality: зона {need} слаба eff={eff_e:.2f}<{min_st}"
    # 2) цель-структура (куда идти)
    need_tgt = "resistance" if is_buy else "support"
    has_tgt=False
    for tf,d in zctx.items():
        for (lo,hi,st) in d.get(need_tgt,[]):
            if is_buy and lo>hi_e+price*0.005:
                has_tgt=True
            elif (not is_buy) and hi<lo_e-price*0.005:
                has_tgt=True
    if zc.get("require_structure",True) and not has_tgt:
        return False, f"zone_quality: {side_u} нет цели ({need_tgt}) - некуда идти"
    # 3) предыдущее движение к зоне
    pm, pm_reason=_prev_move_ok(k15, atr, "Buy" if is_buy else "Sell", cfg)
    if not pm:
        return False, pm_reason
    # 4) стакан (если включён и доступен)
    if zc.get("check_orderbook",False) and hasattr(exchange,"get_orderbook"):
        try:
            ob=await exchange.get_orderbook(sym, limit=25)
            if isinstance(ob,dict) and ob.get("bids") and ob.get("asks"):
                b=sum(float(x[1]) for x in ob["bids"][:10])
                a=sum(float(x[1]) for x in ob["asks"][:10])
                ratio=(b/a) if is_buy else (a/b)
                minr=float(zc.get("ob_min_ratio",1.0) or 1.0)
                if b>0 and a>0 and ratio<minr:
                    return False, f"zone_quality: стакан против {side_u} ratio={ratio:.2f}<{minr}"
        except Exception as e:
            logger.warning("zone_quality ob %s: %s", sym, e)
    logger.info("zone_quality OK %s %s zone=%s eff=%.2f", sym, side_u, need, eff_e)
    return True, f"zone_quality: OK {side_u} у {need}({tf_e}) eff={eff_e:.2f}"

def _zone_tf_weight(tf, cfg):
    zc=_zq_cfg(cfg)
    wm=zc.get("tf_weights") if isinstance(zc.get("tf_weights"),dict) else TF_WEIGHTS
    return float(wm.get(str(tf), 1.0))

def _prev_move_ok(klines, atr, side, cfg):
    zc=_zq_cfg(cfg)
    max_atr=float(zc.get("prev_move_max_atr",3.0) or 3.0)
    if not klines or len(klines)<10:
        return True,""
    closes=[float(k.get("close") or 0) for k in klines[-10:]]
    move=abs(closes[-1]-closes[0])/(atr if atr>0 else 1)
    down=sum(1 for i in range(1,len(closes)) if closes[i]<closes[i-1])
    up=sum(1 for i in range(1,len(closes)) if closes[i]>closes[i-1])
    n=len(closes)-1
    if side=="Buy" and move>max_atr and down>=n-1:
        return False, f"prev_move: крутой спуск к поддержке ({move:.1f}A, {down}/{n})"
    if side=="Sell" and move>max_atr and up>=n-1:
        return False, f"prev_move: крутой подъём к сопротивлению ({move:.1f}A, {up}/{n})"
    return True,""
