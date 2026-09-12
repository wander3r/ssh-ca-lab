# SSH CA + IdP 试用实验室（Smallstep 风格）

本目录是一套 **自托管 SSH 证书颁发机构（CA）+ 身份门户** 的 Docker 实验环境。

目标：登录门户（本地账号或飞书）→ 用本人 SSH 公钥换一张 **短时用户证书** → 用证书 SSH 进信任该 CA 的目标机。  
**不覆盖** Git / deploy key；生产里那些仍走独立密钥或单独的 host/user 策略。

## 架构

```
浏览器 / scripts/ssh-ca-login.sh
    │  :8088 HTTP  或  :8443 HTTPS（Nginx 自签）
    ▼
proxy (edge, Nginx)
    ▼
idp-portal (FastAPI, 不直接对外)
    │  Redis 存 CLI 设备流
    │  本地 sre/dev（可关），或飞书 OAuth → policy.json
    │  step ssh certificate --sign  (JWK provisioner)
    ▼
step-ca  仅 ca 网 + 127.0.0.1:9000
    │  用户证 TTL=1h；host 证给 ssh-target
    ▼
客户端 ssh -i key -o CertificateFile=key-cert.pub
    │  :2222  （@cert-authority 校验 host 证书）
    ▼
ssh-target
    TrustedUserCAKeys + HostCertificate + AuthorizedPrincipalsFile
```

| 服务 | 容器 | 宿主机端口 | 作用 |
|------|------|------------|------|
| proxy | `ssh-ca-lab-proxy` | `8088` HTTP、`8443` HTTPS | 唯一 HTTP(S) 入口 |
| idp-portal | `ssh-ca-lab-idp-portal` | 不发布 | 登录 + 按角色签发用户证书 |
| redis | `ssh-ca-lab-redis` | 不发布 | CLI 设备流会话 |
| step-ca | `ssh-ca-lab-step-ca` | `127.0.0.1:9000` | 用户 CA + host CA |
| ssh-target | `ssh-ca-lab-ssh-target` | `2222` | 证书登录演示机 |

账号与 CA 口令写在 **`credentials.txt`**（权限 600），不要把真实口令写进 README。

## 角色

`idp-portal/policy.json` 决定证书 principals 和目标机 Unix 用户。对不上的飞书账号 **拒绝签发**（`unmapped_role` 为 `null`）。

| 角色 | Unix 用户 | principal | sudo | 本地试用 | 飞书 |
|------|-----------|-----------|------|----------|------|
| sre | `sre` | `sre` | 要 Unix 密码（`SRE_PASSWORD`） | `sre` / `sre` | 部门名匹配「运维」等，或 `users` 白名单 |
| dev | `dev` | `dev` | 无 | `dev` / `dev` | 部门名匹配「研发」等，或 `users` 白名单 |

`credentials.txt` 里的 `PORTAL_USER`（默认 `demo`）是实验室别名，签发时映射为 **sre**。目标机 `demo` 用户仍在，但新证书不会带 `demo` principal。

飞书登录优先看 `users` 里的工号 / `open_id`，再按部门名匹配；都对不上就 **拒绝**（`unmapped_role` 为 `null`）。  
`user_info` **不含部门**。若通讯录权限不够，页面会显示「当前拿到：无」——这是没读到部门，不是部门名写错。可先用 `open_id` 定点授权（门户日志 `feishu login ... open_id=`）：

```json
"users": {
  "ou_xxxxxxxx": "sre"
}
```

改 `policy.json` 后必须 `--build` 门户。真实 `open_id` 不要写进公开文档。

## 从 GitHub 使用

```bash
git clone https://github.com/wander3r/ssh-ca-lab.git
cd ssh-ca-lab
cp .env.example .env
cp credentials.example.txt credentials.txt
chmod 600 .env credentials.txt
# 把 change-me-ca-password 改成自己的口令（两处保持一致）
```

局域网或飞书回调不要用 127.0.0.1：把 `.env` 里的 `PORTAL_PUBLIC_URL` / `FEISHU_REDIRECT_URL` 改成浏览器实际访问的 URL（须与飞书应用里登记的重定向地址一致）。

`.env` / `credentials.txt` 以及签发出来的私钥 **不要提交**。

## 飞书应用（可选）

在 [飞书开放平台](https://open.feishu.cn/app) 建自建应用，重定向 URL 与 `FEISHU_REDIRECT_URL` 完全一致（实验室常用 `http://<局域网IP>:8088/auth/feishu/callback`）。

权限（应用身份，改完要 **创建版本并发布**）：

| 权限 | 作用 |
|------|------|
| 获取通讯录基本信息 `contact:contact.base:readonly` | 用 tenant token 读用户 |
| 获取用户组织架构信息 `contact:user.department:readonly` | 返回 `department_ids`（没有它部门永远是空） |
| 获取部门基础信息 `contact:department.base:readonly` | 把部门 ID 译成「运维 / 研发」等中文名 |

**通讯录权限范围** 必须包含人所在 **部门**（或全部成员）。只勾选某一个用户时，接口能查到人，但 **不会返回部门**，门户就会 403「不在 SSH 白名单里」。

OAuth 用户授权范围见 `.env` 的 `FEISHU_SCOPES`。用户身份缺 `contact:contact.base:readonly` 时日志会有 `99991679`，门户会改走 tenant token。

## 启动

`.env` 必须有 `SESSION_SECRET`（`openssl rand -hex 32`）。

```bash
# 若 Docker daemon 走 TCP：export DOCKER_HOST=tcp://127.0.0.1:2375
cd ssh-ca-lab
docker compose --env-file .env up -d --build
docker compose ps
```

更严的叠加（关掉门户本地账号，cookie 仅 HTTPS；飞书回调请改成 `https://<host>:8443/...`）：

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d
```

首次 `step-ca` 会把 PKI 写进 named volume `step-ca-data`（**不要用 bind mount**，远程 Docker 看不到本机路径）。配置（含 `policy.json`、Nginx）COPY 进镜像，改策略后需要 `--build`。

健康检查：

```bash
curl -sf http://127.0.0.1:8088/healthz
curl -skf https://127.0.0.1:8443/healthz
nc -zv 127.0.0.1 2222
```

HTTPS 用的是 Nginx 自签证书，浏览器会告警，实验室可忽略；生产换正规证书。

取出 **host CA** 公钥给客户端 `known_hosts`（避免 TOFU）：

```bash
echo "@cert-authority * $(docker exec ssh-ca-lab-step-ca cat /home/step/certs/ssh_host_ca_key.pub)"
```

## 登录与签发流程

1. 浏览器打开 http://127.0.0.1:8088 （或你的 `PORTAL_PUBLIC_URL`）。
2. 用 `sre` / `dev` 本地账号，或点飞书登录。
3. 本机生成密钥（**私钥不要上传**）：

```bash
ssh-keygen -t ed25519 -f ~/id_lab -N '' -C 'you@lab'
```

4. 把 `~/id_lab.pub` 粘贴或上传到门户，点签发。TTL 默认 **1 小时**，principals 为当前角色（`sre` 或 `dev`）。
5. 下载 `id_lab-cert.pub`，放到私钥旁边。

命令行（本地账号，不经过浏览器）：

```bash
./scripts/ssh-ca-login.sh --local sre
# 或：
./scripts/issue-cert.sh ~/id_lab.pub ~/id_lab-cert.pub
# ISSUE_USER=dev ISSUE_PASSWORD=dev ./scripts/issue-cert.sh ~/id_lab.pub ~/id_lab-cert.pub
```

浏览器 / 飞书设备流（终端打印登录 URL，登录后自动写证书）：

```bash
./scripts/ssh-ca-login.sh
./scripts/ssh-ca-login.sh --ssh          # 拿到证书后直接连目标机
./scripts/ssh-ca-login.sh --no-open      # 只打印 URL，适合无图形环境
```

## SSH 进目标机

```bash
# 推荐：用 host CA，避免 TOFU
echo "@cert-authority * $(docker exec ssh-ca-lab-step-ca cat /home/step/certs/ssh_host_ca_key.pub)" \
  >> ~/.ssh/known_hosts

ssh -i ~/id_lab \
  -o CertificateFile=~/id_lab-cert.pub \
  -o IdentitiesOnly=yes \
  -p 2222 \
  sre@127.0.0.1
# 开发角色则用 dev@127.0.0.1
```

实验室也可以 `-o StrictHostKeyChecking=no`。`sre` 登录后 `sudo` 要输入 `SRE_PASSWORD`（默认 `sre`），不是飞书密码。

一键端到端验证（sre 的 sudo 必须输入 Unix 密码，dev 不能 sudo）：

```bash
./scripts/e2e-verify.sh
```

成功时应打印 `SUCCESS_SSH_CA_LAB`。目标机 **没有** `authorized_keys`（`AuthorizedKeysFile none`），只有 CA 签的证书能登录。sshd 关闭了密码登录。

`sre` 的 Unix 密码由 `.env` / `credentials.txt` 的 **`SRE_PASSWORD`** 在容器启动时写入（默认 `sre`）。这只给 `sudo` 用，**不是** 飞书/门户密码。`dev` / `demo` 仍无 Unix 密码。不要用 `passwd -l`，那会让 sshd 直接拒绝证书登录。

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
# 或保证证书 principals 与 Unix 用户名一致（本实验用 sre / dev）
systemctl reload sshd
```

3. **不必**把个人公钥同步到每台机的 `authorized_keys`。人换设备时重新向门户/IdP 申请短时证书即可；吊销靠 TTL +（生产）`RevokedKeys` / step-ca revoke。

可选：用 **host CA** 给每台机签 host 证书，客户端 `known_hosts` 写一行 `@cert-authority * <host_ca.pub>`，避免 TOFU。

```bash
docker exec ssh-ca-lab-step-ca cat /home/step/certs/ssh_host_ca_key.pub
```

## 生产：用真 IdP / OIDC 替换假登录

本门户的本地表单只是试通证书链路。飞书 OAuth 已接在门户上，CA 侧仍用 JWK `admin` provisioner 代签。生产对应 Smallstep 的做法：

1. 在 IdP（Keycloak / Dex / Okta / Google Workspace / 飞书）建 OIDC client。
2. 在 `step-ca` 增加 **OIDC provisioner**（`step ca provisioner add`）。
3. 用户执行 `step ssh login` 或自己的门户：浏览器走 OIDC → 用 ID token 向 CA 换 SSH 用户证书。
4. 把 IdP 的 `email` / groups / 部门映射到 SSH **principals**（本实验用 `policy.json` 的 `sre` / `dev`）。
5. JWK `admin` provisioner **只留给自动化门户服务账号**，不要发给终端用户。

## 注意

- 配置已 COPY 进镜像；PKI 用 named volume。
- 证书 TTL 过期后重新签发即可，无需改目标机。
- 重置实验：`docker compose down -v`（会丢掉 CA 密钥，所有已签证书立即作废）。
