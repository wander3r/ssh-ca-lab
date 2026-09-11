# SSH CA + IdP 试用实验室（Smallstep 风格）

本目录是一套 **自托管 SSH 证书颁发机构（CA）+ 简易身份门户（IdP 模拟）** 的 Docker 实验环境。

目标：登录简易 IdP → 用本人 SSH 公钥换一张 **短时用户证书** → 用证书 SSH 进信任该 CA 的目标机。  
**不覆盖** Git / deploy key；生产里那些仍走独立密钥或单独的 host/user 策略。

## 架构

```
浏览器 / curl
    │  http://127.0.0.1:8080
    ▼
idp-portal (FastAPI)
    │  step ssh certificate --sign  (JWK provisioner)
    ▼
step-ca :9000
    │  签名短时 SSH user cert (TTL=1h, principal=demo)
    ▼
客户端 ssh -i key -o CertificateFile=key-cert.pub
    │  :2222
    ▼
ssh-target (OpenSSH, TrustedUserCAKeys = 用户 CA 公钥)
    本地用户 demo（无密码、无 authorized_keys）
```

| 服务 | 容器 | 宿主机端口 | 作用 |
|------|------|------------|------|
| step-ca | `ssh-ca-lab-step-ca` | `9000` | SSH 用户 CA（同时初始化了 host CA 密钥，本实验未强制用 host cert） |
| idp-portal | `ssh-ca-lab-idp-portal` | `8080` | 本地账号登录 + 粘贴公钥签发证书 |
| ssh-target | `ssh-ca-lab-ssh-target` | `2222` | 信任用户 CA 的 OpenSSH 演示机 |

账号与 CA 口令写在 **`credentials.txt`**（权限 600），不要把真实口令写进 README。

## 从 GitHub 使用

```bash
git clone https://github.com/wander3r/ssh-ca-lab.git
cd ssh-ca-lab
cp .env.example .env
cp credentials.example.txt credentials.txt
chmod 600 .env credentials.txt
# 把 change-me-ca-password 改成自己的口令（两处保持一致）
```

`.env` / `credentials.txt` 以及签发出来的私钥 **不要提交**。

## 启动

```bash
# 若 Docker daemon 走 TCP：export DOCKER_HOST=tcp://127.0.0.1:2375
cd ssh-ca-lab
docker compose --env-file .env up -d --build
docker compose ps
```

首次 `step-ca` 会把 PKI 写进 named volume `step-ca-data`（**不要用 bind mount**，远程 Docker 看不到本机路径）。

健康检查：

```bash
curl -sf http://127.0.0.1:8080/healthz
nc -zv 127.0.0.1 2222
```

## 登录与签发流程

1. 浏览器打开 http://127.0.0.1:8080 ，用 `credentials.txt` 里的 `PORTAL_USER` / `PORTAL_PASSWORD` 登录。
2. 本机生成密钥（**私钥不要上传**）：

```bash
ssh-keygen -t ed25519 -f ~/id_lab -N '' -C 'you@lab'
```

3. 把 `~/id_lab.pub` 粘贴或上传到门户，点签发。TTL 默认 **1 小时**，principals 含 Unix 用户 `demo`。
4. 下载 `id_lab-cert.pub`，放到私钥旁边。

命令行等价：

```bash
./scripts/issue-cert.sh ~/id_lab.pub ~/id_lab-cert.pub
```

## SSH 进目标机

```bash
ssh -i ~/id_lab \
  -o CertificateFile=~/id_lab-cert.pub \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=no \
  -p 2222 \
  demo@127.0.0.1
```

一键端到端验证：

```bash
./scripts/e2e-verify.sh
```

成功时应打印 `SUCCESS_SSH_CA_LAB`。目标机 **没有** `authorized_keys`（`AuthorizedKeysFile none`），只有 CA 签的证书能登录。
目标机 `demo` 使用 `usermod -p '*'`（无登录密码但账户**未锁定**）。`passwd -l` 会让 sshd 直接拒绝证书登录。

## 把几十台云主机指向同一 CA

1. 从 CA volume 取出 **用户 CA 公钥**（不是 TLS root）：

```bash
docker exec ssh-ca-lab-step-ca cat /home/step/certs/ssh_user_ca_key.pub
```

2. 每台主机：

```bash
# /etc/ssh/ssh_user_ca.pub  ← 上面那一行
# sshd_config:
TrustedUserCAKeys /etc/ssh/ssh_user_ca.pub
# 建议同时：
# AuthorizedPrincipalsFile /etc/ssh/auth_principals/%u
# 或保证证书 principals 与 Unix 用户名一致（本实验用 demo）
systemctl reload sshd
```

3. **不必**把个人公钥同步到每台机的 `authorized_keys`。人换设备时重新向门户/IdP 申请短时证书即可；吊销靠 TTL +（生产）`RevokedKeys` / step-ca revoke。

可选：用 **host CA** 给每台机签 host 证书，客户端 `known_hosts` 写一行 `@cert-authority * <host_ca.pub>`，避免 TOFU。

```bash
docker exec ssh-ca-lab-step-ca cat /home/step/certs/ssh_host_ca_key.pub
```

## 生产：用真 IdP / OIDC 替换假登录

本门户的本地表单只是试通证书链路。生产对应 Smallstep 的做法：

1. 在 IdP（Keycloak / Dex / Okta / Google Workspace）建 OIDC client。
2. 在 `step-ca` 增加 **OIDC provisioner**（`step ca provisioner add`）。
3. 用户执行 `step ssh login` 或自己的门户：浏览器走 OIDC → 用 ID token 向 CA 换 SSH 用户证书。
4. 把 IdP 的 `email` / groups 映射到 SSH **principals**（本实验写死 `demo`）。
5. JWK `admin` provisioner **只留给自动化门户服务账号**，不要发给终端用户。

## 注意

- 配置已 COPY 进镜像；PKI 用 named volume。
- 证书 TTL 过期后重新签发即可，无需改目标机。
- 重置实验：`docker compose down -v`（会丢掉 CA 密钥，所有已签证书立即作废）。
