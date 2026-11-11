"""Submit evidence of a confirmed CMS article using short-lived GitHub OIDC.

Requires authoritative CMS GET-by-sourceKey after the publisher's write/lookup.
No extra CMS POST, no WeChat call, no stored GitHub access secret.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from .channels import CMS_INGEST_URL,lookup_cms_delivery

AUDIENCE="qjai-ops-delivery-v1"
OPS_URL="https://ingest.ops.qjai.org/v1/github-events"
JOB_BY_PREFIX={
    "producthunt":"ph_cms",
    "x-daily":"x_cms",
    "overseas-stories":"overseas_cms",
    "trustmrr-weekly":"trustmrr_cms_weekly",
}
ALLOWED_OUTCOMES={"created","already_exists","duplicate","confirmed_after_error"}

def _oidc_token()->str:
    if(os.getenv("GITHUB_REPOSITORY")!="gamehack/cms-publisher"
       or os.getenv("GITHUB_REF")!="refs/heads/main"
       or os.getenv("GITHUB_EVENT_NAME") not in ("schedule","workflow_dispatch")):
        raise RuntimeError("Untrusted CMS Ops evidence environment")
    endpoint=os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL","")
    auth=os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN","")
    p=urlparse(endpoint)
    if(p.scheme!="https" or not p.hostname or
       not p.hostname.endswith(".actions.githubusercontent.com") or
       p.username or p.password or p.fragment or not auth):
        raise RuntimeError("GitHub OIDC environment unavailable")
    request=Request(endpoint+("&" if p.query else "?")+"audience="+AUDIENCE,
        headers={"Authorization":"Bearer "+auth,"Accept":"application/json"},method="GET")
    try:
        with urlopen(request,timeout=15) as response:
            result=json.loads(response.read(32769))
    except (HTTPError,URLError,TimeoutError,ValueError):
        raise RuntimeError("GitHub identity request failed") from None
    token=result.get("value") if isinstance(result,dict) else None
    if not isinstance(token,str) or len(token)>20000 or token.count(".")!=2:
        raise RuntimeError("Invalid GitHub OIDC response")
    return token

def proof_items(result:dict,now:datetime)->list[dict]:
    if not isinstance(result,dict) or result.get("status")!="COMPLETED":
        return []
    reports=result.get("reports")
    if not isinstance(reports,list):raise RuntimeError("CMS reports are malformed")
    items=[]
    for item in reports:
        if not isinstance(item,dict) or item.get("status")!="COMPLETED":continue
        key=item.get("source_key")
        if not isinstance(key,str) or key.count(":")!=1:raise RuntimeError("CMS source key invalid")
        source,period=key.split(":",1)
        job=JOB_BY_PREFIX.get(source)
        if not job:
            # Monthly CMS reports are intentionally a separate project stage.
            if source=="trustmrr-monthly":continue
            raise RuntimeError("Unexpected CMS source key")
        if item.get("cms") not in ALLOWED_OUTCOMES:
            raise RuntimeError("CMS delivery receipt is unrecognized")
        if source=="overseas-stories":
            if not re.fullmatch(r"0[1-9]|[1-9]\d{1,4}",period):
                raise RuntimeError("Overseas issue id invalid")
            target=now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        else:
            if not re.fullmatch(r"\d{4}-\d\d-\d\d",period):
                raise RuntimeError("Calendar period invalid")
            if datetime.strptime(period,"%Y-%m-%d").strftime("%Y-%m-%d")!=period:
                raise RuntimeError("Calendar period invalid")
            target=period
        items.append({"job_id":job,"period_key":target,
            "stage":"cms_draft_confirmed","status":"success",
            "occurred_at":int(now.timestamp()),
            "metadata":{"source_key":key}})
    return items

def report(output_file:Path)->dict:
    # A completed GitHub step is not delivery proof. Check the live CMS DB.
    result=json.loads(output_file.read_text(encoding="utf-8"))
    now=datetime.now(timezone.utc)
    items=proof_items(result,now)
    if not items:
        return {"status":"OPS_NO_CMS_DRAFT_TO_REPORT","count":0}
    token=os.getenv("CMS_INGEST_TOKEN","")
    if len(token)<32:raise RuntimeError("CMS token missing")
    for item in items:
        actual=lookup_cms_delivery(item["metadata"]["source_key"],token=token,
                                   url=os.getenv("CMS_INGEST_URL",CMS_INGEST_URL))
        if not actual or actual.get("status") not in ("draft","published"):
            raise RuntimeError("CMS sourceKey not confirmed by authoritative lookup")
        item["metadata"]["external_status"]=actual["status"]
    jwt=_oidc_token()
    for item in items:
        req=Request(OPS_URL,method="POST",
            data=json.dumps(item,separators=(",",":"),ensure_ascii=False).encode(),
            headers={"Authorization":"Bearer "+jwt,
                     "Content-Type":"application/json",
                     "User-Agent":"QJAI-CMS-Ops-Proof/1.0"})
        try:
            with urlopen(req,timeout=12) as response:
                if response.status!=202:raise RuntimeError("Ops evidence unexpected status")
                body=json.loads(response.read(2048))
        except (HTTPError,URLError,TimeoutError,ValueError):
            raise RuntimeError("Ops CMS proof not acknowledged") from None
        if body.get("accepted") is not True:
            raise RuntimeError("Ops CMS proof rejected")
    return {"status":"OPS_CMS_DRAFT_CONFIRMED","count":len(items)}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--result-file",type=Path,required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(report(args.result_file)))
    except (ValueError,RuntimeError,OSError) as exc:
        parser.exit(2,"Ops CMS evidence failed: "+str(exc)+"\n")

if __name__=="__main__":
    main()
