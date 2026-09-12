# SSH CA

自托管 **SSH 证书颁发机构** 与身份门户：飞书登录后签发短时用户证书，业务机只信任 CA，不再铺个人 `authorized_keys`。

控制面用 Docker Compose；业务机用 Ansible。不覆盖 Git / deploy key。

## 架构

```
员工浏览器 / scripts/ssh-ca-login.sh
        │  :8088 HTTP 或 :8443 HTTPS
        ▼
proxy (Nginx)
        ▼
portal (FastAPI) ── Redis（设备流）
        │  飞书 OAuth → policy.json（open_id / 部门 → sre|dev）
        │  step ssh certificate --sign
        ▼
step-ca          仅 127.0.0.1:9000 + 内网
        │
        ▼
业务机（Ansible: TrustedUserCAKeys + principals + 可选 host 证）
```

| 组件 | 容器名 | 端口 | 职责 |
|------|--------|------|------|
| proxy | `sshca-proxy` | `8088` / `8443` | 唯一 HTTP(S) 入口 |
| portal | `sshca-portal` | 不发布 | 登录并签发用户证书 |
| redis | `sshca-redis` | 不发布 | CLI 设备流 |
| step-ca | `sshca-step-ca` | `127.0.0.1:9000` | 用户 CA + host CA |

业务 SSH 主机 **不要** 放进这个 compose。用 `ansible/` 推送到金丝雀再扩到集群。

## 角色

`idp-portal/policy.json`：对不上的飞书账号拒绝签发（`unmapped_role` 为 `null`）。

| 角色 | Unix 用户 | principal | sudo |
|------|-----------|-----------|------|
| sre | `sre` | `sre` | 要该机 Unix 密码（不要全员共用一个口令） |
| dev | `dev` | `dev` | 无 |

定点授权（工号或 `open_id`）：

```json
"users": {
  "ou_xxxxxxxx": "sre"
}
```

改 `policy.json` 后必须重建门户镜像。

飞书 `user_info` **不含部门**。要按部门映射，须在开放平台开通 **获取用户组织架构信息**，且通讯录范围包含人所在 **部门**（只勾一个人会查到人、拿不到部门）。详见下文。

## 控制面

```bash
cp .env.example .env
cp credentials.example.txt credentials.txt
chmod 600 .env credentials.txt
# 填写 CA 口令、SESSION_SECRET（openssl rand -hex 32）、飞书应用
docker compose --env-file .env up -d --build
```

从旧实验室 volume 迁 PKI 时在 `.env` 写：

```
PKI_VOLUME=ssh-ca-lab_step-ca-data
REDIS_VOLUME=ssh-ca-lab_redis-data
```

HTTPS 仅飞书、关掉本地登录：

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d
```

健康检查：

```bash
curl -sf http://127.0.0.1:8088/healthz
curl -skf https://127.0.0.1:8443/healthz
```

导出 CA 公钥给 Ansible / 员工 `known_hosts`：

```bash
./scripts/export-ca-pubkeys.sh
```

可选：本机起一台演示 SSH 机（端口 2222，并打开本地门户账号，仅供 e2e）：

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.demo.yml up -d --build
./scripts/e2e-verify.sh
```

## 飞书

重定向 URL 必须与 `FEISHU_REDIRECT_URL` 完全一致。权限改完要 **创建版本并发布**：

| 权限 | 作用 |
|------|------|
| `contact:contact.base:readonly` | tenant token 读用户 |
| `contact:user.department:readonly` | 返回 `department_ids` |
| `contact:department.base:readonly` | 部门 ID → 中文名 |

通讯录范围必须包含部门或全部成员。

## 签发与登录

```bash
./scripts/ssh-ca-login.sh              # 打开/打印飞书 URL，登录后写证书
./scripts/ssh-ca-login.sh --ssh
```

```bash
ssh -i ~/.ssh/id_ed25519 \
  -o CertificateFile=~/.ssh/id_ed25519-cert.pub \
  -o IdentitiesOnly=yes \
  sre@app.example.com
```

客户端 `known_hosts`：

```
@cert-authority * <ssh_host_ca_key.pub 那一行>
```

## Ansible（业务机）

```bash
./scripts/export-ca-pubkeys.sh
cd ansible
# 编辑 inventory/hosts.yml
ansible-playbook playbooks/canary.yml    # 1 台，保留 authorized_keys
# 用门户签一张 sre 证登录成功后再：
ansible-playbook playbooks/fleet.yml
ansible-playbook playbooks/lockdown.yml  # AuthorizedKeysFile none
```

host 证书：在 CA 上签好后放到 `ansible/files/host-certs/<inventory_hostname>-cert.pub`。provisioner 密码不要下发到业务机。

金丝雀阶段保留云厂商 console。第一天不要全网关掉 `authorized_keys`。

## 生产注意

- JWK `admin` 只给门户服务账号，不要给员工。
- 用户证 TTL 默认 8 小时；过期重新跑 CLI，不用改服务器。
- 吊销：短 TTL +（必要时）`step ca revoke` / 主机 `RevokedKeys`。
- CA 私钥只在 `sshca-pki` volume，做好备份。`docker compose down -v` 会毁掉 CA，已签证书全部作废。
- 生产 sudo 用每人密码或 LDAP/SSSD，不要全员同一个 `SRE_PASSWORD`。
- 代理自签证书仅过渡；正式环境换正规 TLS。

GitHub 仓库若仍叫 `ssh-ca-lab`，clone 路径可以暂时不变；产品名是 **SSH CA**。
