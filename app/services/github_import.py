from __future__ import annotations

import hashlib, json, re, shutil, zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
import httpx
from app.config import settings
from app.db import connect, utc_now

_PACKAGE_RE=re.compile(r"^[A-Za-z0-9._-]{1,128}$"); _ALLOWED_TYPES={"book","comics","audiobook"}; _TYPE_DIRS={"book":"books","comics":"comics","audiobook":"audiobooks"}; _BULK_TYPES={"book","comics"}
class GitHubImportError(RuntimeError): pass
class GitHubImportForbidden(GitHubImportError): pass
@dataclass(slots=True)
class GitHubPackage:
    package_id:str; content_type:str; title:str; language:str; version:str; created_at:str; files:tuple[str,...]; checksums:dict[str,str]; path:str; commit_sha:str; status:str="new"; current_version:str=""

def require_system_owner(identity_id:int)->None:
    if not settings.is_system_owner(int(identity_id)): raise GitHubImportForbidden("Недостаточно прав")
def _repo()->tuple[str,str]:
    value=str(settings.GITHUB_IMPORT_REPOSITORY or "").strip().strip("/")
    if value.count("/")!=1: raise GitHubImportError("Репозиторий импорта не настроен")
    a,b=value.split("/",1)
    if not a or not b: raise GitHubImportError("Репозиторий импорта не настроен")
    return a,b
def _headers()->dict[str,str]:
    h={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}; token=str(settings.GITHUB_IMPORT_TOKEN or "").strip()
    if token: h["Authorization"]=f"Bearer {token}"
    return h
def _root_path(*parts:str)->str:
    root=str(settings.GITHUB_IMPORT_ROOT or "").strip().strip("/"); clean=[str(PurePosixPath(p)).strip("/") for p in parts if str(p).strip("/")]; return "/".join(([root] if root else [])+clean)
def validate_manifest(data:dict[str,Any],*,package_path:str,commit_sha:str)->GitHubPackage:
    req=("package_id","content_type","title","language","version","files","checksums","created_at"); missing=[x for x in req if x not in data]
    if missing: raise GitHubImportError("Повреждён manifest: отсутствует "+", ".join(missing))
    pid,kind=str(data["package_id"]).strip(),str(data["content_type"]).strip().lower()
    if not _PACKAGE_RE.fullmatch(pid): raise GitHubImportError("Некорректный package_id")
    if kind not in _ALLOWED_TYPES: raise GitHubImportError("Неподдерживаемый content_type")
    files=tuple(str(PurePosixPath(str(x))) for x in data["files"])
    if not files or len(files)!=len(set(files)): raise GitHubImportError("Manifest должен содержать уникальный непустой список files")
    for name in files:
        p=PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or str(p) in {".",""}: raise GitHubImportError("Небезопасный путь в manifest")
    sums={str(PurePosixPath(str(k))):str(v).lower().strip() for k,v in dict(data["checksums"]).items()}
    for name in files:
        if not re.fullmatch(r"[0-9a-f]{64}",sums.get(name,"")): raise GitHubImportError(f"Нет корректного SHA-256 для {name}")
    return GitHubPackage(pid,kind,str(data["title"]).strip(),str(data["language"]).strip(),str(data["version"]).strip(),str(data["created_at"]).strip(),files,sums,package_path,commit_sha)
async def ensure_github_import_schema()->None:
    async with connect() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS github_import_history(id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL, content_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', version TEXT NOT NULL, commit_sha TEXT NOT NULL, status TEXT NOT NULL, file_count INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0, book_id INTEGER, error TEXT, created_at TEXT NOT NULL, UNIQUE(package_id, version, commit_sha, status))"""); await db.execute("CREATE INDEX IF NOT EXISTS idx_github_import_package ON github_import_history(package_id,id DESC)"); await db.commit()
async def _last_success(pid:str):
    await ensure_github_import_schema()
    async with connect() as db:
        cur=await db.execute("SELECT * FROM github_import_history WHERE package_id=? AND status='success' ORDER BY id DESC LIMIT 1",(pid,)); return await cur.fetchone()
async def import_history(identity_id:int,*,status:str="",limit:int=30)->list[dict[str,Any]]:
    require_system_owner(identity_id); await ensure_github_import_schema(); sql="SELECT * FROM github_import_history"; args=[]
    if status: sql+=" WHERE status=?"; args.append(status)
    sql+=" ORDER BY id DESC LIMIT ?"; args.append(max(1,min(100,int(limit))))
    async with connect() as db: cur=await db.execute(sql,tuple(args)); return [dict(x) for x in await cur.fetchall()]
async def _get_json(client,url,params=None):
    r=await client.get(url,headers=_headers(),params=params)
    if r.status_code==404:return None
    r.raise_for_status(); return r.json()
async def repository_status(identity_id:int)->dict[str,Any]:
    require_system_owner(identity_id); owner,repo=_repo(); branch=str(settings.GITHUB_IMPORT_BRANCH or "main").strip()
    async with httpx.AsyncClient(timeout=20.0,follow_redirects=False) as c: commit=await _get_json(c,f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/commits/{quote(branch)}")
    if not commit: raise GitHubImportError("Ветка GitHub не найдена")
    return {"repository":f"{owner}/{repo}","branch":branch,"root":str(settings.GITHUB_IMPORT_ROOT or ""),"commit_sha":commit["sha"]}
async def discover_packages(identity_id:int,*,page:int=1,page_size:int|None=None)->dict[str,Any]:
    require_system_owner(identity_id); st=await repository_status(identity_id); owner,repo=_repo(); branch,sha=st["branch"],st["commit_sha"]; size=max(1,min(100,int(page_size or settings.GITHUB_IMPORT_PAGE_SIZE or 50))); found=[]
    async with httpx.AsyncClient(timeout=20.0,follow_redirects=False) as c:
        for kind,folder in _TYPE_DIRS.items():
            entries=await _get_json(c,f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(_root_path(folder),safe='/')}",{"ref":branch})
            if not entries: continue
            for e in entries:
                if e.get("type")!="dir": continue
                pp=str(e["path"]); m=await _get_json(c,f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(pp+'/manifest.json',safe='/')}",{"ref":sha})
                if not m or m.get("type")!="file": continue
                raw=await c.get(m["download_url"],headers=_headers()); raw.raise_for_status(); p=validate_manifest(raw.json(),package_path=pp,commit_sha=sha)
                if p.content_type!=kind: raise GitHubImportError(f"Тип пакета {p.package_id} не соответствует каталогу")
                prev=await _last_success(p.package_id)
                if prev: p.current_version=str(prev["version"]); p.status="imported" if p.current_version==p.version else "update"
                found.append(p)
    found.sort(key=lambda x:(x.content_type,x.package_id)); start=max(0,(max(1,int(page))-1)*size); return {"items":found[start:start+size],"page":max(1,int(page)),"page_size":size,"total":len(found),"commit_sha":sha}
async def find_package(identity_id:int,package_id:str)->GitHubPackage:
    require_system_owner(identity_id); wanted=str(package_id).strip()
    if not _PACKAGE_RE.fullmatch(wanted): raise GitHubImportError("Некорректный package_id")
    page=1
    while True:
        r=await discover_packages(identity_id,page=page,page_size=100)
        for p in r["items"]:
            if p.package_id==wanted:return p
        if page*r["page_size"]>=r["total"]:break
        page+=1
    raise GitHubImportError("Пакет не найден")
async def download_package(identity_id:int,p:GitHubPackage)->Path:
    require_system_owner(identity_id); owner,repo=_repo(); root=Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import")); free=shutil.disk_usage(root.parent if root.parent.exists() else Path(".")).free
    if free<int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB)*1024*1024: raise GitHubImportError("Недостаточно свободного места для временного импорта")
    target=root/f"{p.package_id}-{p.commit_sha[:12]}"; shutil.rmtree(target,ignore_errors=True); target.mkdir(parents=True,exist_ok=False); total=0; limit=int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB)*1024*1024
    try:
        async with httpx.AsyncClient(timeout=None,follow_redirects=False) as c:
            for name in p.files:
                meta=await _get_json(c,f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(p.path+'/'+name,safe='/')}",{"ref":p.commit_sha})
                if not meta or meta.get("type")!="file" or not meta.get("download_url"): raise GitHubImportError(f"Отсутствует файл: {name}")
                dest=target.joinpath(*PurePosixPath(name).parts); dest.parent.mkdir(parents=True,exist_ok=True); digest=hashlib.sha256()
                async with c.stream("GET",meta["download_url"],headers=_headers()) as response:
                    response.raise_for_status()
                    with dest.open("wb") as out:
                        async for chunk in response.aiter_bytes(1024*1024):
                            total+=len(chunk)
                            if total>limit: raise GitHubImportError("Пакет превышает лимит временного импорта")
                            digest.update(chunk); out.write(chunk)
                if digest.hexdigest()!=p.checksums[name]: raise GitHubImportError(f"SHA-256 не совпадает: {name}")
        return target
    except Exception: shutil.rmtree(target,ignore_errors=True); raise
def cleanup_package(path:str|Path)->None: shutil.rmtree(Path(path),ignore_errors=True)
async def record_import(p:GitHubPackage,*,status:str,book_id:int|None=None,bytes_total:int=0,error:str="")->None:
    await ensure_github_import_schema(); safe=str(error or "")[:2000]; token=str(settings.GITHUB_IMPORT_TOKEN or "")
    if token:safe=safe.replace(token,"[REDACTED]")
    async with connect() as db: await db.execute("""INSERT OR REPLACE INTO github_import_history(package_id,content_type,title,version,commit_sha,status,file_count,bytes_total,book_id,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(p.package_id,p.content_type,p.title,p.version,p.commit_sha,status,len(p.files),int(bytes_total),book_id,safe,utc_now())); await db.commit()

def _build_import_zip(package:GitHubPackage,source:Path)->Path:
    if package.content_type=="audiobook": raise GitHubImportError("Массовый импорт аудиокниг пока отключён")
    archive=source.parent/f"{source.name}.voxlyra.zip"; prefix="Comics" if package.content_type=="comics" else "Books"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in package.files:
            path=source.joinpath(*PurePosixPath(name).parts)
            if not path.is_file(): raise GitHubImportError(f"После загрузки отсутствует файл: {name}")
            z.write(path,f"{prefix}/{package.package_id}/{name}")
    return archive

async def import_package(identity_id:int,package_id:str,*,allow_update:bool=False)->dict[str,Any]:
    require_system_owner(identity_id); package=await find_package(identity_id,package_id)
    if package.content_type not in _BULK_TYPES: return {"status":"unsupported_bulk","package":package,"book_ids":[]}
    if package.status=="imported": return {"status":"already_imported","package":package,"book_ids":[]}
    if package.status=="update" and not allow_update: return {"status":"update_available","package":package,"book_ids":[]}
    work=None; archive=None
    try:
        work=await download_package(identity_id,package); bytes_total=sum(p.stat().st_size for p in work.rglob("*") if p.is_file()); archive=_build_import_zip(package,work)
        from app.services.library_manager import import_library_zip, restore_import_replacement_backups, finalize_import_replacement_backups
        result=await import_library_zip(archive,f"github-{package.package_id}-{package.version}.zip",identity_id)
        if result.errors and not result.book_ids:
            await restore_import_replacement_backups(result.batch_id); raise GitHubImportError("; ".join(" / ".join(e.reasons) for e in result.errors[:5]))
        await finalize_import_replacement_backups(result.batch_id)
        book_id=result.book_ids[0] if len(result.book_ids)==1 else None; await record_import(package,status="success",book_id=book_id,bytes_total=bytes_total)
        return {"status":"success","package":package,"batch_id":result.batch_id,"book_ids":list(result.book_ids),"added":result.added,"replaced":result.replaced,"duplicates":result.duplicates,"errors":len(result.errors)}
    except Exception as exc:
        await record_import(package,status="failed",error=str(exc)); raise
    finally:
        if archive: archive.unlink(missing_ok=True)
        if work: cleanup_package(work)

async def import_all_new(identity_id:int,*,max_packages:int=1000)->dict[str,Any]:
    """Import all new book/comic packages in bounded pages. Updates require explicit owner action."""
    require_system_owner(identity_id); page=1; selected=[]; updates=[]; audio=[]
    while len(selected)<max_packages:
        result=await discover_packages(identity_id,page=page,page_size=100)
        for p in result["items"]:
            if p.content_type not in _BULK_TYPES: audio.append(p.package_id)
            elif p.status=="new": selected.append(p.package_id)
            elif p.status=="update": updates.append(p.package_id)
        if page*result["page_size"]>=result["total"]: break
        page+=1
    summary={"total":len(selected),"success":0,"failed":0,"already":0,"updates":updates,"audio_skipped":audio,"errors":[]}
    for pid in selected[:max_packages]:
        try:
            outcome=await import_package(identity_id,pid)
            if outcome["status"]=="success": summary["success"]+=1
            else: summary["already"]+=1
        except Exception as exc:
            summary["failed"]+=1; summary["errors"].append({"package_id":pid,"error":str(exc)[:500]})
    return summary

async def retry_failed(identity_id:int,*,max_packages:int=100)->dict[str,Any]:
    """Retry only latest failed packages that have not subsequently succeeded."""
    require_system_owner(identity_id); rows=await import_history(identity_id,status="failed",limit=max_packages); seen=set(); ids=[]
    for row in rows:
        pid=str(row["package_id"])
        if pid in seen: continue
        seen.add(pid); latest=await _last_success(pid)
        if latest and str(latest["created_at"])>=str(row["created_at"]): continue
        ids.append(pid)
    summary={"total":len(ids),"success":0,"failed":0,"errors":[]}
    for pid in ids:
        try:
            outcome=await import_package(identity_id,pid)
            if outcome["status"] in {"success","already_imported"}: summary["success"]+=1
        except Exception as exc:
            summary["failed"]+=1; summary["errors"].append({"package_id":pid,"error":str(exc)[:500]})
    return summary
