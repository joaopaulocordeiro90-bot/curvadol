#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MESA BRL - robo diario (macro & cambio)
Busca tudo que o dashboard nao consegue pegar sozinho e grava em curve.json:
  - FX (varios pares)            -> AwesomeAPI
  - Juros/inflacao (Selic, IPCA, PTAX) -> Banco Central (SGS, API aberta)
  - Expectativas (Focus)         -> Banco Central (Olinda)
  - Pre (Svensson)               -> ANBIMA
  - Cupom cambial                -> B3 (cascata)
  - Curva DOL (vencimentos)
Roda todo dia via GitHub Actions. Cada bloco grava seu 'status' pra diagnostico.
"""
import re, json, sys
from datetime import datetime, date, timedelta
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "pt-BR,pt;q=0.9", "Accept": "application/json,*/*"}
requests.packages.urllib3.disable_warnings()

# ===================== CALENDARIO B3 =====================
FERIADOS = {
    "2026-01-01","2026-02-16","2026-02-17","2026-04-03","2026-04-21","2026-05-01",
    "2026-06-04","2026-09-07","2026-10-12","2026-11-02","2026-11-15","2026-11-20",
    "2026-12-24","2026-12-25","2026-12-31",
    "2027-01-01","2027-02-08","2027-02-09","2027-03-26","2027-04-21","2027-05-01",
    "2027-05-27","2027-09-07","2027-10-12","2027-11-02","2027-11-15","2027-12-24",
    "2027-12-25","2027-12-31",
}
def eh_util(d): return d.weekday() < 5 and d.isoformat() not in FERIADOS
def primeiro_util_do_mes(a, m):
    d = date(a, m, 1)
    while not eh_util(d): d += timedelta(days=1)
    return d
def ultimo_pregao(ref=None):
    d = ref or datetime.utcnow().date()
    if datetime.utcnow().hour < 22: d -= timedelta(days=1)
    while not eh_util(d): d -= timedelta(days=1)
    return d
def dias_uteis(d0, d1):
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if eh_util(d): n += 1
    return n
MC = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}
def vencimentos_dol(ref, n=12):
    out=[]; a,m=ref.year,ref.month
    for _ in range(n):
        m+=1
        if m>12: m=1; a+=1
        v=primeiro_util_do_mes(a,m)
        out.append({"code":"DOL"+MC[v.month]+f"{v.year%100:02d}","venc":v.isoformat(),
                    "du":dias_uteis(ref,v),"dc":(v-ref).days})
    return out

# ===================== FX (AwesomeAPI) =====================
def get_fx():
    pares = "USD-BRL,EUR-BRL,GBP-BRL,EUR-USD,GBP-USD,USD-JPY,USD-MXN,USD-CLP,USD-ZAR,USD-CNY,USD-CAD,USD-SEK,USD-CHF"
    r = requests.get(f"https://economia.awesomeapi.com.br/json/last/{pares}",
                     headers=UA, timeout=30)
    r.raise_for_status()
    out={}
    for k,v in r.json().items():
        out[k]={"bid":float(v["bid"]),"ask":float(v.get("ask",v["bid"])),
                "pct":float(v.get("pctChange",0)),"name":v.get("name","")}
    return out

# ===================== BANCO CENTRAL (SGS) =====================
def sgs(code, n=1):
    url=f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/{n}?formato=json"
    r=requests.get(url, headers=UA, timeout=30); r.raise_for_status()
    return r.json()
def _num(s): return float(str(s).replace(".","").replace(",",".")) if "," in str(s) else float(s)
def get_rates():
    out={}
    fontes={"selic":432,"ipca12m":13522,"ptax":1,"igpm12m":13521}
    for k,code in fontes.items():
        try:
            d=sgs(code); out[k]={"valor":_num(d[-1]["valor"]),"data":d[-1]["data"]}
        except Exception as e:
            out[k]={"erro":str(e)}
    # juro real ex-post = (1+selic)/(1+ipca) - 1
    try:
        s=out["selic"]["valor"]/100; i=out["ipca12m"]["valor"]/100
        out["juroreal"]={"valor":round(((1+s)/(1+i)-1)*100,2)}
    except: pass
    return out

# ===================== FOCUS (Olinda) =====================
def get_focus(year):
    base=("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
          "ExpectativasMercadoAnuais")
    out={"ano":year}
    alvo=[("IPCA","ipca"),("Selic","selic"),("Câmbio","cambio"),("PIB Total","pib")]
    def busca(filtro):
        p={"$top":"1","$format":"json","$orderby":"Data desc",
           "$select":"Indicador,Data,DataReferencia,Mediana","$filter":filtro}
        r=requests.get(base,params=p,headers=UA,timeout=30,verify=False); r.raise_for_status()
        return r.json().get("value",[])
    for ind,key in alvo:
        try:
            vals=busca(f"Indicador eq '{ind}' and DataReferencia eq '{year}'")
            if not vals:  # fallback: pega a expectativa mais recente, sem travar o ano
                vals=busca(f"Indicador eq '{ind}'")
            if vals:
                out[key]={"mediana":vals[0].get("Mediana"),"data":vals[0].get("Data"),
                          "ref":vals[0].get("DataReferencia")}
            else:
                out[key]={"erro":"sem dados"}; print(f"  focus {ind}: vazio")
        except Exception as e:
            out[key]={"erro":str(e)}; print(f"  focus {ind}: {e}")
    return out

# ===================== PRE (ANBIMA) =====================
def get_pre(ref):
    r=requests.get("https://www.anbima.com.br/informacoes/est-termo/CZ.asp",
                   headers=UA, timeout=40, verify=False); r.encoding="latin1"
    txt=re.sub(r"<[^>]+>"," ",r.text).replace("&nbsp;"," ")
    i=txt.upper().find("PREFIXADOS")
    if i<0: raise RuntimeError("PREFIXADOS ausente")
    nums=re.findall(r"-?\d+,\d{3,}",txt[i:i+600])[:6]
    if len(nums)<6: raise RuntimeError(f"{len(nums)}/6 params")
    b=[float(x.replace(",",".")) for x in nums]
    m=re.search(r"(\d{2}/\d{2}/\d{4})",txt[max(0,i-400):i])
    return {"b1":b[0],"b2":b[1],"b3":b[2],"b4":b[3],"l1":b[4],"l2":b[5],
            "date":m.group(1) if m else ref.strftime("%d/%m/%Y"),"status":"ok"}
def svensson(tau,p):
    import math
    l1,l2=p["l1"],p["l2"]
    t1=(1-math.exp(-l1*tau))/(l1*tau); t2=t1-math.exp(-l1*tau)
    t3=(1-math.exp(-l2*tau))/(l2*tau)-math.exp(-l2*tau)
    return p["b1"]+p["b2"]*t1+p["b3"]*t2+p["b4"]*t3

# ===================== CUPOM (B3 cascata) =====================
def _curva_html(html):
    txt=re.sub(r"<[^>]+>"," ",html)
    out=[]
    for dc,c1,c2 in re.findall(r"(\d{1,5})\s+([\d.]+,\d+)\s+([\d.]+,\d+)?",txt):
        try:
            n=int(dc); t=float((c2 or c1).replace(".","").replace(",","."))
            if 1<=n<=4000 and 0<t<60: out.append((n,t))
        except: pass
    return out
def cupom_doc(ref):
    url="https://www2.bmf.com.br/pages/portal/bmfbovespa/lumis/lum-taxas-referenciais-bmf-ptBR.asp"
    p={"Data":ref.strftime("%d/%m/%Y"),"Data1":ref.strftime("%Y%m%d"),"slcTaxa":"DOC"}
    r=requests.get(url,params=p,headers=UA,timeout=40,verify=False,allow_redirects=False)
    if r.status_code in (301,302,303,307,308): raise RuntimeError("migrou (redirect)")
    pts=_curva_html(r.text)
    if len(pts)<3: raise RuntimeError("DOC vazia")
    return {"vertices":pts,"source":"B3 taxas-referenciais DOC (cupom limpo)"}
def cupom_bdi(ref):
    import pdfplumber, io
    iso,ymd=ref.isoformat(),ref.strftime("%Y%m%d")
    url=f"https://arquivos.b3.com.br/bdi/download/bdi/{iso}/BDI_03-1_{ymd}.pdf"
    r=requests.get(url,headers=UA,timeout=60,verify=False); r.raise_for_status()
    tx=""
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for pg in pdf.pages[:3]: tx+=(pg.extract_text() or "")+"\n"
    pts=[]
    for m in re.finditer(r"SCS\w+\s+\d{2}/\d{2}/\d{4}\s+(\d+)\s+\+?([\d.]+,\d+)",tx):
        dc=int(m.group(1)); t=float(m.group(2).replace(".","").replace(",","."))
        if dc>=20 and 0<t<30: pts.append((dc,t))
    if len(pts)<2: raise RuntimeError("SCS ausente")
    return {"vertices":pts,"source":"B3 BDI / SCS (aproximacao)"}
def get_cupom(ref):
    err=[]
    for fn in (cupom_doc,cupom_bdi):
        try:
            res=fn(ref); res["status"]="ok"; return res
        except Exception as e:
            err.append(f"{fn.__name__}: {e}"); print("  cupom -",err[-1])
    return {"vertices":[],"status":"fail","erros":err,"source":"nenhuma fonte"}
def interp(vs,dc):
    v=sorted(vs)
    if not v: return None
    if dc<=v[0][0]: return v[0][1]
    if dc>=v[-1][0]: return v[-1][1]
    for (x0,y0),(x1,y1) in zip(v,v[1:]):
        if x0<=dc<=x1: return y0+(y1-y0)*(dc-x0)/(x1-x0)
    return v[-1][1]

# ===================== MONTA curve.json =====================
def bloco(nome, fn, *a):
    try:
        r=fn(*a); print(f"{nome}: ok"); return r
    except Exception as e:
        print(f"{nome}: FALHOU - {e}"); return {"status":"fail","erro":str(e)}

def main():
    ref=ultimo_pregao()
    print("Pregao:",ref)
    out={"updatedAt":datetime.utcnow().isoformat()+"Z","refdate":ref.strftime("%d/%m/%Y"),
         "vencimentos":vencimentos_dol(ref)}
    out["fx"]    = bloco("FX",    get_fx)
    out["rates"] = bloco("RATES", get_rates)
    out["focus"] = bloco("FOCUS", get_focus, ref.year)
    out["pre"]   = bloco("PRE",   get_pre, ref)

    cup=get_cupom(ref); out["cupom"]=cup
    byCode={}
    if cup.get("vertices"):
        for v in out["vencimentos"]:
            t=interp(cup["vertices"],v["dc"])
            if t is not None: byCode[v["code"]]=round(t,4)
    out["cupom"]["byCode"]=byCode; out["cupom"]["date"]=ref.strftime("%d/%m/%Y")
    print(f"CUPOM: {cup['status']} ({cup.get('source')})")

    json.dump(out, open("curve.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)
    print("curve.json gravado.")
    # so quebra o Action se a PRE (essencial pra curva) falhar
    if isinstance(out["pre"],dict) and out["pre"].get("status")=="fail":
        sys.exit(1)

if __name__=="__main__":
    main()
