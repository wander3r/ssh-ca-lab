"""SSH CA identity portal: Feishu (or optional local login) → short-lived user certs."""

from __future__ import annotations

import json
import logging
import time
import os
import re
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("idp-portal")

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

PORTAL_USER = os.environ.get("PORTAL_USER", "").strip()
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "")
PROVISIONER = os.environ.get("PROVISIONER_NAME", "admin")
PROVISIONER_PASSWORD = os.environ.get("PROVISIONER_PASSWORD", "")
STEP_CA_URL = os.environ.get("STEP_CA_URL", "https://step-ca:9000")
ROOT_CA_PATH = os.environ.get("ROOT_CA_PATH", "/home/step/certs/root_ca.crt")
CERT_TTL = os.environ.get("CERT_TTL", "8h")
# Fallback principals only if a session has no role (should be rare).
DEFAULT_PRINCIPALS = [
    p.strip()
    for p in os.environ.get("SSH_PRINCIPALS", "").split(",")
    if p.strip()
]
SSH_HINT_PORT = os.environ.get("SSH_HINT_PORT", "2222")

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
FEISHU_REDIRECT_URL = os.environ.get(
    "FEISHU_REDIRECT_URL", "http://127.0.0.1:8088/auth/feishu/callback"
).strip()
FEISHU_AUTH_URL = os.environ.get(
    "FEISHU_AUTH_URL", "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
).strip()
FEISHU_TOKEN_URL = os.environ.get(
    "FEISHU_TOKEN_URL", "https://accounts.feishu.cn/oauth/v3/token"
).strip()
FEISHU_USERINFO_URL = os.environ.get(
    "FEISHU_USERINFO_URL", "https://open.feishu.cn/open-apis/authen/v1/user_info"
).strip()
FEISHU_SCOPES = os.environ.get(
    "FEISHU_SCOPES",
    "contact:user.email:readonly contact:user.employee_id:readonly contact:contact.base:readonly",
).strip()
PORTAL_PUBLIC_URL = os.environ.get("PORTAL_PUBLIC_URL", "http://127.0.0.1:8088").rstrip("/")
CLIENT_TTL_SEC = int(os.environ.get("CLIENT_SESSION_TTL", "600"))
REDIS_URL = os.environ.get("REDIS_URL", "").strip()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


ALLOW_LOCAL_LOGIN = _env_flag("ALLOW_LOCAL_LOGIN", False)
SESSION_HTTPS_ONLY = _env_flag("SESSION_HTTPS_ONLY", False)


class ClientSessionStore:
    """Device-flow sessions: Redis when REDIS_URL is set, else process memory."""

    PREFIX = "sshca:cs:"

    def __init__(self) -> None:
        self._mem: dict[str, dict] = {}
        self._r = None
        if REDIS_URL:
            import redis as redis_lib

            self._r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)

    def _key(self, sid: str) -> str:
        return self.PREFIX + sid

    def put(self, sid: str, data: dict) -> None:
        payload = json.dumps(data)
        if self._r is not None:
            self._r.setex(self._key(sid), CLIENT_TTL_SEC, payload)
        else:
            self._mem[sid] = data

    def get(self, sid: str) -> dict | None:
        if self._r is not None:
            raw = self._r.get(self._key(sid))
            return json.loads(raw) if raw else None
        item = self._mem.get(sid)
        if not item:
            return None
        if time.time() - item.get("created", 0) > CLIENT_TTL_SEC:
            self._mem.pop(sid, None)
            return None
        return item

    def delete(self, sid: str) -> None:
        if self._r is not None:
            self._r.delete(self._key(sid))
        else:
            self._mem.pop(sid, None)

    def purge(self) -> None:
        if self._r is not None:
            return
        now = time.time()
        for key in [k for k, v in self._mem.items() if now - v.get("created", 0) > CLIENT_TTL_SEC]:
            self._mem.pop(key, None)

    def ping(self) -> None:
        if self._r is not None:
            self._r.ping()


CLIENT_STORE = ClientSessionStore()



POLICY_PATH = Path(os.environ.get("POLICY_PATH", str(APP_DIR / "policy.json")))
_POLICY_CACHE: dict | None = None


def _load_policy() -> dict:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    default = {
        "unmapped_role": None,
        "roles": {
            "sre": {"unix_user": "sre", "principals": ["sre"], "sudo": "all", "departments": []},
            "dev": {"unix_user": "dev", "principals": ["dev"], "sudo": False, "departments": []},
        },
        "users": {},
    }
    if POLICY_PATH.is_file():
        try:
            data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            default.update({k: data[k] for k in data})
        except Exception:
            pass
    _POLICY_CACHE = default
    return default


def _role_spec(name: str) -> dict | None:
    return (_load_policy().get("roles") or {}).get(name)


def _resolve_role(*, department_names: list[str] | None = None, employee_no: str = "", open_id: str = "", local_user: str = "") -> str | None:
    pol = _load_policy()
    if local_user in ("sre", "dev"):
        return local_user
    if local_user and local_user == PORTAL_USER:
        return "sre"
    users = pol.get("users") or {}
    for key in (employee_no, open_id):
        if key and key in users:
            return users[key]
    names = [n.strip().casefold() for n in (department_names or []) if n and str(n).strip()]
    for role, spec in (pol.get("roles") or {}).items():
        aliases = [str(x).strip().casefold() for x in (spec.get("departments") or []) if x]
        for n in names:
            if n == role or n in aliases:
                return role
            if any(a and (a in n or n in a) for a in aliases):
                return role
    return pol.get("unmapped_role") or None


def _bind_role(request: Request, role: str, *, display_name: str, identity: str) -> None:
    spec = _role_spec(role) or {}
    request.session["role"] = role
    request.session["user"] = spec.get("unix_user") or role
    request.session["display_name"] = display_name
    request.session["cert_identity"] = identity
    request.session["principals"] = spec.get("principals") or [role]
    request.session["sudo"] = spec.get("sudo") or False


def _session_principals(request: Request) -> list[str]:
    raw = request.session.get("principals")
    if isinstance(raw, list) and raw:
        return [str(p) for p in raw]
    spec = _role_spec(str(request.session.get("role") or ""))
    if spec and spec.get("principals"):
        return list(spec["principals"])
    return [p.strip() for p in DEFAULT_PRINCIPALS if p.strip()]



def _ensure_role(request: Request) -> str | None:
    """Stale pre-RBAC cookies may have user but no role; fill from policy."""
    role = request.session.get("role")
    if role:
        return str(role)
    user = str(request.session.get("user") or "")
    role = _resolve_role(
        local_user=user,
        employee_no=str(request.session.get("employee_no") or ""),
        open_id=str(request.session.get("feishu_open_id") or ""),
        department_names=list(request.session.get("feishu_departments") or []),
    )
    if not role:
        return None
    _bind_role(
        request,
        role,
        display_name=str(request.session.get("display_name") or user or role),
        identity=str(request.session.get("cert_identity") or f"{user or role}@sshca"),
    )
    return role


def feishu_enabled() -> bool:
    return bool(FEISHU_APP_ID and FEISHU_APP_SECRET)


def _ssh_hint_host(request: Request) -> str:
    return (os.environ.get("SSH_HINT_HOST") or request.url.hostname or "127.0.0.1").strip()

app = FastAPI(title="SSH CA")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
    session_cookie="sshca_session",
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)


def _login_page_vars(request: Request, error: str | None = None) -> dict:
    return {
        "request": request,
        "error": error,
        "portal_user_hint": PORTAL_USER,
        "feishu_enabled": feishu_enabled(),
        "allow_local_login": ALLOW_LOCAL_LOGIN,
    }


def require_login(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


@app.get("/", response_class=HTMLResponse)
def home(request: Request, client_session: str = ""):
    if client_session:
        request.session["client_session"] = client_session
    if _ensure_role(request) or request.session.get("user"):
        if request.session.get("client_session") and _ensure_role(request):
            _complete_client_session(request.session["client_session"], request)
            return RedirectResponse("/client/done", status_code=302)
        if request.session.get("role"):
            return RedirectResponse("/issue", status_code=302)
    return templates.TemplateResponse("login.html", _login_page_vars(request))


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not ALLOW_LOCAL_LOGIN:
        return templates.TemplateResponse(
            "login.html",
            _login_page_vars(request, "本地账号登录已关闭"),
            status_code=403,
        )
    role = _resolve_role(local_user=username)
    local_ok = (
        (username in ("sre", "dev") and password == username)
        or (username == PORTAL_USER and password == PORTAL_PASSWORD)
    )
    if local_ok and role:
        _bind_role(request, role, display_name=username, identity=f"{username}@local")
        cs = request.session.get("client_session")
        if cs:
            _complete_client_session(cs, request)
            return RedirectResponse("/client/done", status_code=302)
        return RedirectResponse("/issue", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        _login_page_vars(request, "用户名或密码错误（试用账号见 credentials.txt）"),
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/issue", response_class=HTMLResponse)
def issue_page(request: Request, user: str = Depends(require_login)):
    return templates.TemplateResponse(
        "issue.html",
        {
            "request": request,
            "user": user,
            "display_name": request.session.get("display_name"),
            "principals": ",".join(_session_principals(request)),
            "ttl": CERT_TTL,
            "error": None,
            "result": None,
            "sudo": request.session.get("sudo") or False,
            "ssh_host": _ssh_hint_host(request),
            "ssh_port": SSH_HINT_PORT,
        },
    )



def _purge_client_sessions() -> None:
    CLIENT_STORE.purge()


def _complete_client_session(session_id: str, request: Request) -> bool:
    sess = CLIENT_STORE.get(session_id)
    if not sess or sess.get("status") != "pending" or not sess.get("pubkey"):
        return False
    try:
        if not _ensure_role(request):
            sess["status"] = "error"
            sess["error"] = "department not allowed"
            CLIENT_STORE.put(session_id, sess)
            return False
        identity = request.session.get("cert_identity") or f"{request.session.get('user')}@sshca"
        principals = _session_principals(request)
        cert_text, inspect_out = _sign_ssh_cert(sess["pubkey"], identity, principals)
        sess["status"] = "ready"
        sess["certificate"] = cert_text.strip()
        sess["inspect"] = inspect_out
        sess["identity"] = identity
        sess["unix_user"] = request.session.get("user")
        sess["role"] = request.session.get("role")
        sess["principals"] = principals
        sess["sudo"] = request.session.get("sudo")
        CLIENT_STORE.put(session_id, sess)
        return True
    except Exception as exc:  # noqa: BLE001
        sess["status"] = "error"
        sess["error"] = str(exc)
        CLIENT_STORE.put(session_id, sess)
        return False


def _validate_pubkey(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("公钥为空")
    # single-line OpenSSH public key
    line = text.splitlines()[0].strip()
    if not re.match(r"^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521))\s+\S+", line):
        raise ValueError("需要 OpenSSH 公钥（ssh-ed25519 / ssh-rsa / ecdsa-...）")
    return line + "\n"


def _sign_ssh_cert(pubkey_text: str, identity: str, principals: list[str]) -> tuple[str, str]:
    """Sign pubkey via step CLI; returns (cert_text, ssh_keygen_l_output)."""
    if not PROVISIONER_PASSWORD:
        raise RuntimeError("PROVISIONER_PASSWORD not set")

    with tempfile.TemporaryDirectory(prefix="sshcert-") as td:
        td_path = Path(td)
        pub_path = td_path / "id_lab.pub"
        pub_path.write_text(pubkey_text, encoding="utf-8")
        # step writes <basename>-cert.pub next to the .pub when given a .pub path
        # For `step ssh certificate <id> id_lab.pub`, output is id_lab-cert.pub
        cert_path = td_path / "id_lab-cert.pub"
        prov_pass = td_path / "prov.pass"
        prov_pass.write_text(PROVISIONER_PASSWORD, encoding="utf-8")
        prov_pass.chmod(0o600)

        cmd = [
            "step",
            "ssh",
            "certificate",
            identity,
            str(pub_path),
            "--sign",
            "--provisioner",
            PROVISIONER,
            "--provisioner-password-file",
            str(prov_pass),
            "--not-after",
            CERT_TTL,
            "--ca-url",
            STEP_CA_URL,
            "--root",
            ROOT_CA_PATH,
        ]
        for p in principals:
            p = p.strip()
            if p:
                cmd.extend(["--principal", p])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"step ssh certificate failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
            )
        if not cert_path.is_file():
            # fallback: search for *-cert.pub
            found = list(td_path.glob("*-cert.pub"))
            if not found:
                raise RuntimeError(
                    f"certificate file missing after sign.\nstdout={proc.stdout}\nstderr={proc.stderr}"
                )
            cert_path = found[0]
        cert_text = cert_path.read_text(encoding="utf-8")

        inspect = subprocess.run(
            ["ssh-keygen", "-L", "-f", str(cert_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        inspect_out = inspect.stdout if inspect.returncode == 0 else inspect.stderr
        return cert_text, inspect_out


@app.post("/issue", response_class=HTMLResponse)
async def issue_cert(
    request: Request,
    user: str = Depends(require_login),
    pubkey: Optional[str] = Form(None),
    pubkey_file: Optional[UploadFile] = File(None),
):
    error = None
    result = None
    try:
        raw = ""
        if pubkey_file is not None and pubkey_file.filename:
            raw = (await pubkey_file.read()).decode("utf-8", errors="replace")
        elif pubkey:
            raw = pubkey
        else:
            raise ValueError("请粘贴或上传 SSH 公钥")

        pubkey_line = _validate_pubkey(raw)
        principals = _session_principals(request)
        # identity / key-id shown in ssh-keygen -L
        identity = request.session.get("cert_identity") or f"{user}@sshca"
        cert_text, inspect_out = _sign_ssh_cert(pubkey_line, identity, principals)

        # stash cert in session-scoped temp for download
        out_dir = Path("/work") / secrets.token_hex(8)
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_file = out_dir / "id_lab-cert.pub"
        cert_file.write_text(cert_text, encoding="utf-8")
        request.session["last_cert_path"] = str(cert_file)

        result = {
            "cert_text": cert_text.strip(),
            "inspect": inspect_out,
            "download": "/download-cert",
            "ttl": CERT_TTL,
            "principals": principals,
        }
    except Exception as exc:  # noqa: BLE001 — show to demo user
        error = str(exc)

    return templates.TemplateResponse(
        "issue.html",
        {
            "request": request,
            "user": user,
            "display_name": request.session.get("display_name"),
            "principals": ",".join(_session_principals(request)),
            "ttl": CERT_TTL,
            "error": error,
            "result": result,
            "sudo": request.session.get("sudo") or False,
            "ssh_host": _ssh_hint_host(request),
            "ssh_port": SSH_HINT_PORT,
        },
        status_code=400 if error else 200,
    )


@app.get("/download-cert")
def download_cert(request: Request, user: str = Depends(require_login)):
    path = request.session.get("last_cert_path")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "no certificate; please issue again")
    return FileResponse(
        path,
        filename="id_lab-cert.pub",
        media_type="application/octet-stream",
    )


@app.post("/api/issue")
async def api_issue(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    pubkey: str = Form(...),
):
    """Non-browser helper for e2e scripts."""
    if not ALLOW_LOCAL_LOGIN:
        raise HTTPException(403, "local login disabled")
    role = _resolve_role(local_user=username)
    local_ok = (
        (username in ("sre", "dev") and password == username)
        or (username == PORTAL_USER and password == PORTAL_PASSWORD)
    )
    if not local_ok or not role:
        raise HTTPException(401, "invalid credentials")
    try:
        pubkey_line = _validate_pubkey(pubkey)
        spec = _role_spec(role) or {}
        principals = list(spec.get("principals") or [role])
        unix_user = spec.get("unix_user") or role
        cert_text, inspect_out = _sign_ssh_cert(
            pubkey_line, f"{username}@sshca", principals
        )
        return {
            "ok": True,
            "certificate": cert_text.strip(),
            "inspect": inspect_out,
            "ttl": CERT_TTL,
            "principals": principals,
            "role": role,
            "unix_user": unix_user,
            "sudo": spec.get("sudo") or False,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc




def _http_json(method: str, url: str, *, data: dict | None = None, token: str | None = None) -> dict:
    body = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"feishu http {exc.code}: {payload[:300]}") from exc
    return json.loads(payload) if payload else {}


def _feishu_tenant_token() -> str:
    r = _http_json(
        "POST",
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
    )
    token = r.get("tenant_access_token")
    if r.get("code") not in (None, 0) or not token:
        raise RuntimeError(r.get("msg") or "tenant_access_token failed")
    return token


def _feishu_get_user(token: str, open_id: str) -> dict:
    url = (
        "https://open.feishu.cn/open-apis/contact/v3/users/"
        + urllib.parse.quote(open_id)
        + "?user_id_type=open_id&department_id_type=open_department_id"
    )
    r = _http_json("GET", url, token=token)
    if r.get("code") not in (None, 0):
        raise RuntimeError(r.get("msg") or "contact user get failed")
    return (r.get("data") or {}).get("user") or {}


def _feishu_department_name(token: str, department_id: str) -> str:
    url = (
        "https://open.feishu.cn/open-apis/contact/v3/departments/"
        + urllib.parse.quote(department_id)
        + "?department_id_type=open_department_id"
    )
    r = _http_json("GET", url, token=token)
    dept = (r.get("data") or {}).get("department") or {}
    name = (dept.get("name") or "").strip()
    zh = ((dept.get("i18n_name") or {}).get("zh_cn") or "").strip()
    return zh or name


def _feishu_parent_names(token: str, department_id: str) -> list[str]:
    url = (
        "https://open.feishu.cn/open-apis/contact/v3/departments/parent"
        "?department_id_type=open_department_id&department_id="
        + urllib.parse.quote(department_id)
    )
    r = _http_json("GET", url, token=token)
    out: list[str] = []
    for item in (r.get("data") or {}).get("items") or []:
        name = (item.get("name") or "").strip()
        zh = ((item.get("i18n_name") or {}).get("zh_cn") or "").strip()
        label = zh or name
        if label:
            out.append(label)
    return out


def _collect_feishu_departments(user_token: str, open_id: str, info: dict) -> tuple[list[str], str]:
    """Return (labels, debug note). Labels include names and raw ids."""
    labels: list[str] = []
    notes: list[str] = []
    for key in ("department_name", "department", "employee_type"):
        val = info.get(key)
        if val:
            labels.append(str(val))
    for val in info.get("department_ids") or []:
        labels.append(str(val))

    user: dict = {}
    for token, kind in ((user_token, "user_token"), (None, "tenant_token")):
        try:
            tok = token or _feishu_tenant_token()
            user = _feishu_get_user(tok, open_id)
            notes.append(f"contact user ok via {kind}")
            break
        except Exception as exc:
            notes.append(f"contact user {kind}: {exc}")

    dept_ids = [str(x) for x in (user.get("department_ids") or []) if x]
    for val in dept_ids:
        labels.append(val)

    tokens: list[str] = []
    if user_token:
        tokens.append(user_token)
    try:
        tokens.append(_feishu_tenant_token())
    except Exception as exc:
        notes.append(f"tenant_token: {exc}")

    for did in dept_ids:
        resolved = False
        for tok in tokens:
            try:
                name = _feishu_department_name(tok, did)
                if name:
                    labels.append(name)
                    resolved = True
                for parent in _feishu_parent_names(tok, did):
                    labels.append(parent)
                if resolved:
                    break
            except Exception as exc:
                notes.append(f"dept {did}: {exc}")
        if not resolved:
            notes.append(f"dept {did}: name unresolved")

    # de-dupe, keep order
    seen: set[str] = set()
    uniq: list[str] = []
    for item in labels:
        item = str(item).strip()
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq, "; ".join(notes)


@app.get("/auth/feishu")
def feishu_start(request: Request, client_session: str = ""):
    if not feishu_enabled():
        raise HTTPException(503, "飞书未配置")
    if client_session:
        _purge_client_sessions()
        if CLIENT_STORE.get(client_session) is None:
            raise HTTPException(404, "client session expired")
        request.session["client_session"] = client_session
        if _ensure_role(request):
            _complete_client_session(client_session, request)
            return RedirectResponse("/client/done", status_code=302)
    state = secrets.token_urlsafe(24)
    request.session["feishu_oauth_state"] = state
    q = {
        "client_id": FEISHU_APP_ID,
        "response_type": "code",
        "redirect_uri": FEISHU_REDIRECT_URL,
        "state": state,
    }
    if FEISHU_SCOPES:
        q["scope"] = FEISHU_SCOPES
    return RedirectResponse(FEISHU_AUTH_URL + "?" + urllib.parse.urlencode(q), status_code=302)


@app.get("/auth/feishu/callback")
def feishu_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return templates.TemplateResponse(
            "login.html",
            _login_page_vars(request, f"飞书拒绝授权：{error}"),
            status_code=401,
        )
    expect = request.session.pop("feishu_oauth_state", None)
    if not code or not state or state != expect:
        return templates.TemplateResponse(
            "login.html",
            _login_page_vars(request, "飞书回调无效（state 不匹配，请重试）"),
            status_code=401,
        )
    try:
        token_body = {
            "grant_type": "authorization_code",
            "client_id": FEISHU_APP_ID,
            "client_secret": FEISHU_APP_SECRET,
            "code": code,
            "redirect_uri": FEISHU_REDIRECT_URL,
        }
        tr = _http_json("POST", FEISHU_TOKEN_URL, data=token_body)
        access = tr.get("access_token") or (tr.get("data") or {}).get("access_token")
        if tr.get("code") not in (None, 0) or not access:
            raise RuntimeError(tr.get("error_description") or tr.get("msg") or str(tr)[:200])
        ur = _http_json("GET", FEISHU_USERINFO_URL, token=access)
        info = ur.get("data") or ur
        if ur.get("code") not in (None, 0):
            raise RuntimeError(ur.get("msg") or "user_info failed")
        open_id = info.get("open_id") or ""
        name = (info.get("name") or info.get("en_name") or "").strip()
        employee_no = (info.get("employee_no") or "").strip()
        user_id = (info.get("user_id") or "").strip()
        if not open_id:
            raise RuntimeError("飞书未返回 open_id")
    except Exception as exc:
        return templates.TemplateResponse(
            "login.html",
            _login_page_vars(request, f"飞书登录失败：{exc}"),
            status_code=401,
        )

    who = employee_no or user_id or name or open_id
    dept_names, dept_note = _collect_feishu_departments(access, open_id, info)
    log.warning(
        "feishu login name=%s employee_no=%s open_id=%s depts=%s note=%s",
        name,
        employee_no,
        open_id,
        dept_names,
        dept_note,
    )
    role = _resolve_role(
        department_names=dept_names, employee_no=employee_no, open_id=open_id
    )
    request.session["feishu_open_id"] = open_id
    request.session["feishu_departments"] = dept_names
    if not role:
        shown = "、".join(dept_names) if dept_names else "无（飞书 user_info 不含部门，通讯录接口也没拿到）"
        request.session.clear()
        return templates.TemplateResponse(
            "login.html",
            _login_page_vars(
                request,
                f"你的飞书部门不在 SSH 白名单里。当前拿到：{shown}。说明：{dept_note}",
            ),
            status_code=403,
        )
    _bind_role(request, role, display_name=name or who, identity=f"{who}@feishu")
    cs = request.session.get("client_session")
    if cs:
        _complete_client_session(cs, request)
        return RedirectResponse("/client/done", status_code=302)
    return RedirectResponse("/issue", status_code=302)


@app.post("/api/client/begin")
async def client_begin(request: Request):
    """SSH helper starts a login+sign session and opens the returned login_url."""
    _purge_client_sessions()
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "invalid json") from exc
    try:
        pubkey_line = _validate_pubkey(str(body.get("pubkey") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sid = secrets.token_urlsafe(24)
    CLIENT_STORE.put(sid, {"pubkey": pubkey_line, "status": "pending", "created": time.time()})
    qsid = urllib.parse.quote(sid)
    out = {
        "ok": True,
        "session_id": sid,
        "login_url": f"{PORTAL_PUBLIC_URL}/?client_session={qsid}",
        "poll_url": f"{PORTAL_PUBLIC_URL}/api/client/poll?session_id={qsid}",
        "ttl_seconds": CLIENT_TTL_SEC,
    }
    if feishu_enabled():
        out["feishu_login_url"] = f"{PORTAL_PUBLIC_URL}/auth/feishu?client_session={qsid}"
    return out


@app.get("/api/client/poll")
def client_poll(session_id: str = ""):
    sess = CLIENT_STORE.get(session_id)
    if not sess:
        raise HTTPException(404, "unknown or expired session")
    out = {"ok": True, "status": sess["status"]}
    if sess["status"] == "ready":
        out.update(
            {
                "certificate": sess.get("certificate"),
                "inspect": sess.get("inspect"),
                "identity": sess.get("identity"),
                "ttl": CERT_TTL,
                "principals": sess.get("principals") or [p.strip() for p in DEFAULT_PRINCIPALS if p.strip()],
                "unix_user": sess.get("unix_user"),
                "role": sess.get("role"),
                "sudo": sess.get("sudo"),
            }
        )
    if sess["status"] == "error":
        out["error"] = sess.get("error")
    return out


@app.get("/client/done", response_class=HTMLResponse)
def client_done(request: Request):
    return templates.TemplateResponse("done.html", {"request": request, "user": request.session.get("user"), "role": request.session.get("role"), "sudo": request.session.get("sudo")})

@app.get("/healthz")
def healthz():
    try:
        CLIENT_STORE.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis: {exc}") from exc
    return {"status": "ok"}
