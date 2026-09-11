"""Minimal IdP simulation + SSH cert issuance portal (trial lab).

Production would replace local username/password with real OIDC (Keycloak / Dex / Okta)
and map IdP groups → SSH principals / Unix accounts.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

PORTAL_USER = os.environ.get("PORTAL_USER", "demo")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "demo")
PROVISIONER = os.environ.get("PROVISIONER_NAME", "admin")
PROVISIONER_PASSWORD = os.environ.get("PROVISIONER_PASSWORD", "")
STEP_CA_URL = os.environ.get("STEP_CA_URL", "https://step-ca:9000")
ROOT_CA_PATH = os.environ.get("ROOT_CA_PATH", "/home/step/certs/root_ca.crt")
CERT_TTL = os.environ.get("CERT_TTL", "1h")
# Principals embedded in the SSH user certificate (must include local Unix user on target)
DEFAULT_PRINCIPALS = os.environ.get("SSH_PRINCIPALS", "demo").split(",")

app = FastAPI(title="SSH CA Lab IdP Portal")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
    session_cookie="ssh_ca_lab_session",
    same_site="lax",
    https_only=False,
)


def require_login(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/issue", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "portal_user_hint": PORTAL_USER},
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == PORTAL_USER and password == PORTAL_PASSWORD:
        request.session["user"] = username
        return RedirectResponse("/issue", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "用户名或密码错误（试用账号见 credentials.txt）",
            "portal_user_hint": PORTAL_USER,
        },
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
            "principals": ",".join(DEFAULT_PRINCIPALS),
            "ttl": CERT_TTL,
            "error": None,
            "result": None,
        },
    )


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
        principals = [p.strip() for p in DEFAULT_PRINCIPALS if p.strip()]
        identity = f"{user}@ssh-ca-lab"
        cert_text, inspect_out = _sign_ssh_cert(pubkey_line, identity, principals)

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
            "principals": ",".join(DEFAULT_PRINCIPALS),
            "ttl": CERT_TTL,
            "error": error,
            "result": result,
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
    if username != PORTAL_USER or password != PORTAL_PASSWORD:
        raise HTTPException(401, "invalid credentials")
    try:
        pubkey_line = _validate_pubkey(pubkey)
        principals = [p.strip() for p in DEFAULT_PRINCIPALS if p.strip()]
        cert_text, inspect_out = _sign_ssh_cert(
            pubkey_line, f"{username}@ssh-ca-lab", principals
        )
        return {
            "ok": True,
            "certificate": cert_text.strip(),
            "inspect": inspect_out,
            "ttl": CERT_TTL,
            "principals": principals,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
