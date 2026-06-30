#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CURVA DOL - robo diario
Busca os dados que o site nao consegue pegar sozinho (pre da ANBIMA + cupom da B3)
e grava em curve.json. Roda todo dia via GitHub Actions, depois do fechamento.

O site (index.html) le esse curve.json + o dolar ao vivo (AwesomeAPI) e desenha a curva.
Cada perna grava seu proprio "status" pra gente saber, pelo log, o que funcionou.
"""
import re, json, base64, sys
from datetime import datetime, date, timedelta
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "pt-BR,pt;q=0.9"}
requests.packages.urllib3.disable_warnings()  # B3 usa SSL legado

# ---------- calendario de dias uteis B3 ----------
FERIADOS = {
    "2026-01-01","2026-02-16","2026-02-17","2026-04-03","2026-04-21","2026-05-01",
    "2026-06-04","2026-09-07","2026-10-12","2026-11-02","2026-11-15","2026-11-20",
    "2026-12-24","2026-12-25","2026-12-31",
    "2027-01-01","2027-02-08","2027-02-09","2027-03-26","2027-04-21","2027-05-01",
    "2027-05-27","2027-09-07","2027-10-12","2027-11-02","2027-11-15","2027-12-24",
    "2027-12-25","2027-12-31",
}
def eh_util(d): return d.weekday() < 5 and d.isoformat() not in FERIADOS
def primeiro_util_do_mes(ano, mes):
    d = date(ano, mes, 1)
    while not eh_util(d): d += timedelta(days=1)
    return d
def ultimo_pregao(ref=None):
    d = ref or datetime.utcnow().date()
    # processamento noturno da B3: so confia no dia anterior se ainda cedo (UTC)
    if datetime.utcnow().hour < 22:
        d -= timedelta(days=1)
    while not eh_util(d): d -= timedelta(days=1)
    return d
def dias_uteis(d0, d1):
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if eh_util(d): n += 1
    return n

MESES_COD = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}

def vencimentos_dol(refdate, n=12):
    """12 vencimentos do DOL a partir do mes que vem (1o dia util de cada mes)."""
    out = []
    ano, mes = refdate.year, refdate.month
    for _ in range(n):
        mes += 1
        if mes > 12: mes = 1; ano += 1
        venc = primeiro_util_do_mes(ano, mes)
        cod = "DOL" + MESES_COD[venc.month] + f"{venc.year % 100:02d}"
        out.append({
            "code": cod, "venc": venc.isoformat(),
            "du": dias_uteis(refdate, venc),
            "dc": (venc - refdate).days,
        })
    return out

# ---------- PRE: ANBIMA (Svensson) ----------
def get_pre(refdate):
    url = "https://www.anbima.com.br/informacoes/est-termo/CZ.asp"
    r = requests.get(url, headers=UA, timeout=40, verify=False)
    r.encoding = "latin1"
    txt = re.sub(r"<[^>]+>", " ", r.text).replace("&nbsp;", " ")
    i = txt.upper().find("PREFIXADOS")
    if i < 0: raise RuntimeError("bloco PREFIXADOS nao encontrado")
    nums = re.findall(r"-?\d+,\d{3,}", txt[i:i+600])[:6]
    if len(nums) < 6: raise RuntimeError(f"so achei {len(nums)} parametros")
    b = [float(x.replace(",", ".")) for x in nums]
    m = re.search(r"(\d{2}/\d{2}/\d{4})", txt[max(0,i-400):i])
    return {"b1":b[0],"b2":b[1],"b3":b[2],"b4":b[3],"l1":b[4],"l2":b[5],
            "date": m.group(1) if m else refdate.strftime("%d/%m/%Y"), "status":"ok"}

def svensson(tau, p):
    l1,l2 = p["l1"],p["l2"]
    t1 = (1-pow(2.718281828,-l1*tau))/(l1*tau)
    t2 = t1 - pow(2.718281828,-l1*tau)
    t3 = (1-pow(2.718281828,-l2*tau))/(l2*tau) - pow(2.718281828,-l2*tau)
    return p["b1"] + p["b2"]*t1 + p["b3"]*t2 + p["b4"]*t3

# ---------- CUPOM: cascata de fontes da B3 ----------
def _parse_curva_b3_html(html):
    """Pagina lum-taxas-referenciais: extrai pares (dias_corridos, taxa)."""
    txt = re.sub(r"<[^>]+>", " ", html)
    pares = re.findall(r"(\d{1,5})\s+([\d.]+,\d+)\s+([\d.]+,\d+)?", txt)
    out = []
    for dc, c1, c2 in pares:
        try:
            dcn = int(dc)
            taxa = float((c2 or c1).replace(".", "").replace(",", "."))
            if 1 <= dcn <= 4000 and 0 < taxa < 60: out.append((dcn, taxa))
        except: pass
    return out

def cupom_via_taxas_doc(refdate):
    """Curva DOC (cupom cambial LIMPO) - a fonte ideal, se o endpoint legado viver."""
    url = "https://www2.bmf.com.br/pages/portal/bmfbovespa/lumis/lum-taxas-referenciais-bmf-ptBR.asp"
    params = {"Data": refdate.strftime("%d/%m/%Y"),
              "Data1": refdate.strftime("%Y%m%d"), "slcTaxa": "DOC"}
    r = requests.get(url, params=params, headers=UA, timeout=40, verify=False,
                     allow_redirects=False)
    if r.status_code in (301,302,303,307,308):
        raise RuntimeError("endpoint migrou (redirect)")
    pts = _parse_curva_b3_html(r.text)
    if len(pts) < 3: raise RuntimeError("curva DOC vazia/curta")
    return {"vertices": pts, "source": "B3 taxas-referenciais DOC (cupom limpo)"}

def cupom_via_bdi_scs(refdate):
    """Fallback: tabela mark-to-market do BDI (instrumento SCS). Reachable em qualquer IP."""
    import pdfplumber, io
    iso, ymd = refdate.isoformat(), refdate.strftime("%Y%m%d")
    url = f"https://arquivos.b3.com.br/bdi/download/bdi/{iso}/BDI_03-1_{ymd}.pdf"
    r = requests.get(url, headers=UA, timeout=60, verify=False)
    r.raise_for_status()
    texto = ""
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for pg in pdf.pages[:3]:
            texto += (pg.extract_text() or "") + "\n"
    # linhas tipo: SCSU601 01/09/2026 64 +5,541000 1,0098507 49.512,27
    pts = []
    for m in re.finditer(r"SCS\w+\s+\d{2}/\d{2}/\d{4}\s+(\d+)\s+\+?([\d.]+,\d+)", texto):
        dc = int(m.group(1)); taxa = float(m.group(2).replace(".","").replace(",","."))
        if dc >= 20 and 0 < taxa < 30:  # corta o vertice ultra-curto (ruido do cupom sujo)
            pts.append((dc, taxa))
    if len(pts) < 2: raise RuntimeError("SCS nao encontrado no BDI")
    return {"vertices": pts, "source": "B3 BDI / SCS (aproximacao)"}

def get_cupom(refdate):
    erros = []
    for fn in (cupom_via_taxas_doc, cupom_via_bdi_scs):
        try:
            res = fn(refdate); res["status"] = "ok"; return res
        except Exception as e:
            erros.append(f"{fn.__name__}: {e}")
            print("  cupom -", erros[-1])
    return {"vertices": [], "status": "fail", "erros": erros,
            "source": "nenhuma fonte respondeu"}

def interp(vertices, dc):
    """Interpola/extrapola (flat nas pontas) a taxa para um prazo dc."""
    v = sorted(vertices)
    if not v: return None
    if dc <= v[0][0]: return v[0][1]
    if dc >= v[-1][0]: return v[-1][1]
    for (x0,y0),(x1,y1) in zip(v, v[1:]):
        if x0 <= dc <= x1:
            return y0 + (y1-y0)*(dc-x0)/(x1-x0)
    return v[-1][1]

# ---------- monta o curve.json ----------
def main():
    refdate = ultimo_pregao()
    print(f"Pregao de referencia: {refdate}")
    out = {"updatedAt": datetime.utcnow().isoformat()+"Z",
           "refdate": refdate.strftime("%d/%m/%Y"),
           "vencimentos": vencimentos_dol(refdate)}

    try:
        out["pre"] = get_pre(refdate); print("PRE: ok", out["pre"]["date"])
    except Exception as e:
        out["pre"] = {"status": "fail", "erro": str(e)}; print("PRE: FALHOU -", e)

    cup = get_cupom(refdate); out["cupom"] = cup
    print(f"CUPOM: {cup['status']} ({cup.get('source')})")

    # cupom por codigo de vencimento (interpolado pra cada DOL)
    byCode = {}
    if cup.get("vertices"):
        for v in out["vencimentos"]:
            t = interp(cup["vertices"], v["dc"])
            if t is not None: byCode[v["code"]] = round(t, 4)
    out["cupom"]["byCode"] = byCode
    out["cupom"]["date"] = refdate.strftime("%d/%m/%Y")

    with open("curve.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("curve.json gravado.")
    # se a pre falhou, devolve erro pro Action (pra gente ver no log)
    if out["pre"].get("status") != "ok":
        sys.exit(1)

if __name__ == "__main__":
    main()
